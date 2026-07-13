"""AIYES-118 — configure_keyboard failure-atomicity cleanup (RED).

Closes the AIYES-117 A10-AF-006 accepted LOW residual: the single
non-failure-atomic step in ``SessionStartUseCase._execute_linux`` —
``self._display_server.configure_keyboard(display)`` (session_start.py:287) —
is not wrapped in a cleanup-on-failure block. If it raises after a successful
marionette-profile provision + Xvfb start, the already-started Xvfb process is
leaked and (on a marionette launch) the aiyes-owned temp profile is leaked.

Pins the validated requirements (VALIDATED_INTENT_PKG.yaml):

  * R1 (failure_atomicity): after configure_keyboard raises, the started Xvfb
    pid is stopped — ``display_server.stop(xvfb_pid)`` is called, not leaked.
  * R2 (failure_atomicity): after configure_keyboard raises with an aiyes-owned
    temp profile provisioned, that profile is removed —
    ``MarionetteProfilePort.cleanup(session_id)`` is called for THIS session.
  * R3 (exception_fidelity): the ORIGINAL exception propagates unchanged — a
    BARE ``raise`` (asserted by object IDENTITY, and ``__cause__ is None`` to
    falsify a ``raise ... from`` wrap), not swallowed, not wrapped.
  * R5 (launch_shape_correctness): on a non-marionette Linux launch the profile
    cleanup is a guarded no-op while Xvfb is still stopped — the new block is
    correct for every launch shape. The Xvfb-leak half predates AIYES-117 and
    affects EVERY Linux launch (A2 bisect, CHANGE_IMPACT.yaml); it is pinned
    independently here, not only the marionette variant.
  * back-compat guard: on the success path the new try/except must NOT
    over-fire — no stop, no cleanup (protects the NFR-01 2693 baseline).

RED discipline: the module collects cleanly (all top-level imports resolve).
Against the current unpatched code, tests 1 and 2 fail on the cleanup / stop
POST-STATE assertions (not at import, not on ``pytest.raises`` — the bare
exception already propagates today because line 287 is unguarded). Test 3 is a
back-compat guard that already passes on the success path.

Fault injection: a LOCAL ``_FailingKeyboardDisplayServer`` subclass of the
shared ``FakeDisplayServer`` (see TESTS_CANONICAL.yaml for the blast-radius
justification vs a conftest ``fail_configure_keyboard`` flag).

Traceability:
  R1  -> test_configure_keyboard_failure_cleans_temp_profile_and_stops_xvfb (c)
       + test_configure_keyboard_failure_stops_xvfb_on_plain_linux_launch
  R2  -> test_configure_keyboard_failure_cleans_temp_profile_and_stops_xvfb (b)
  R3  -> both failure tests (identity + __cause__ assertions)
  R5  -> test_configure_keyboard_failure_stops_xvfb_on_plain_linux_launch
  back-compat -> test_configure_keyboard_success_path_leaves_resources_running
"""

from __future__ import annotations

from typing import Any

import pytest

from aiyes.domain.use_cases.session_start import SessionStartUseCase

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)

_MARIONETTE_BASE_PORT = 2828  # DEC-A7-02: marionette_port = 2828 + display_num


# ═══════════════════════════════════════════════════════════════════════
# Fault-injection fakes (kept LOCAL — zero edit to the shared conftest)
# ═══════════════════════════════════════════════════════════════════════


class _KbdBoom(RuntimeError):
    """Sentinel raised by the fault-injecting display server's
    configure_keyboard.

    A RuntimeError subclass so the domain's ``except Exception`` catches it, and
    a DISTINCT type so the test can assert the ORIGINAL propagates (R3, bare
    re-raise — not a wrapped surface / not ``raise ... from``).
    """


class _FailingKeyboardDisplayServer(FakeDisplayServer):
    """FakeDisplayServer whose ``configure_keyboard`` raises AFTER a successful
    ``start`` (Xvfb up), simulating a raising DisplayServerPort implementation.

    Local subclass rather than a conftest ``fail_configure_keyboard`` flag: the
    fault stays scoped to AIYES-118 with ZERO edit to the shared conftest
    fixture — the lowest-blast-radius option on the 2693-test baseline (task:
    "pick the lower-blast-radius option"). It inherits the parent's OBSERVABLE
    ``start`` / ``stop`` / ``.calls`` / ``.stopped`` recording unchanged, so the
    Xvfb-stop side effect is asserted through the real cleanup contract, not a
    bespoke hook.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # A concrete instance so the test can assert IDENTITY (R3 bare re-raise).
        self.boom = _KbdBoom("configure_keyboard failed (injected AIYES-118)")

    def configure_keyboard(self, display: str) -> None:
        # Record the attempt (observable), then raise the sentinel instance.
        self.calls.append(("configure_keyboard", display))
        raise self.boom


class _ObservableMarionetteProfile:
    """Observable fake for MarionetteProfilePort.

    Records provision(session_id, port, existing_profile) and cleanup(session_id)
    so the launch-generated session_id is recoverable and the cleanup(session_id)
    side effect is directly assertable. Mirrors the fake in
    tests/test_aiyes117_session_marionette.py.
    """

    def __init__(self) -> None:
        self.provision_calls: list = []  # (session_id, port, existing_profile)
        self.cleanup_calls: list = []

    def provision(self, session_id: str, port: int, existing_profile: Any) -> str:
        self.provision_calls.append((session_id, port, existing_profile))
        return existing_profile or f"/fake/aiyes-marionette-profile-{session_id}"

    def cleanup(self, session_id: str) -> None:
        self.cleanup_calls.append(session_id)


def _make_uc(
    display_server: FakeDisplayServer,
    repo: FakeSessionRepository,
    process: FakeProcess,
    profile: _ObservableMarionetteProfile,
    display_num: int = 99,
) -> SessionStartUseCase:
    return SessionStartUseCase(
        display_server=display_server,
        allocator=FakeDisplayAllocator(display_num=display_num),
        atspi_bus=FakeAccessibilityBus(),
        process=process,
        session_repo=repo,
        clock=FakeClock(),
        marionette_profile=profile,
    )


# ═══════════════════════════════════════════════════════════════════════
# Test 1 — AF-006 falsifiability test (marionette=True): temp profile + Xvfb
# ═══════════════════════════════════════════════════════════════════════


def test_configure_keyboard_failure_cleans_temp_profile_and_stops_xvfb() -> None:
    """R1 + R2 + R3: a firefox+marionette launch whose configure_keyboard raises
    AFTER a successful profile provision + Xvfb start must (a) propagate the
    ORIGINAL exception, (b) cleanup the aiyes-owned temp profile for THIS
    session, and (c) stop the started Xvfb pid.
    """
    repo = FakeSessionRepository()
    process = FakeProcess()
    profile = _ObservableMarionetteProfile()
    xvfb_pid = 4242
    display_server = _FailingKeyboardDisplayServer(pid=xvfb_pid)
    uc = _make_uc(display_server, repo, process, profile, display_num=99)

    # (a) R3 — the ORIGINAL sentinel propagates unchanged (bare re-raise).
    with pytest.raises(_KbdBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)
    assert excinfo.value is display_server.boom  # identity, not a wrapper
    assert excinfo.value.__cause__ is None  # not `raise ... from ...`

    # Bind the concrete session_id this launch generated (from the profile fake).
    assert profile.provision_calls, "marionette profile was never provisioned"
    provisioned_sid = profile.provision_calls[-1][0]

    # (b) R2 — the aiyes-owned temp profile is cleaned up for THIS session.
    assert profile.cleanup_calls == [provisioned_sid]

    # (c) R1 — the already-started Xvfb pid is stopped (not leaked).
    assert ("stop", xvfb_pid) in display_server.calls
    assert display_server.stopped is True

    # Post-state depth: the fault actually reached configure_keyboard, and no
    # partial session was persisted on this error path (failure-atomicity).
    assert ("configure_keyboard", ":99") in display_server.calls
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# Test 2 — pre-existing Xvfb-leak closure (marionette=False)
# ═══════════════════════════════════════════════════════════════════════


def test_configure_keyboard_failure_stops_xvfb_on_plain_linux_launch() -> None:
    """R1 + R5 (+ R3): a NON-marionette Linux launch whose configure_keyboard
    raises must still stop the started Xvfb pid (the pre-existing, initial-
    release-era leak that affects every Linux launch — A2 bisect), while the
    marionette-profile cleanup remains a guarded no-op (no profile on this path).
    """
    repo = FakeSessionRepository()
    process = FakeProcess()
    profile = _ObservableMarionetteProfile()  # wired but must stay untouched
    xvfb_pid = 7373
    display_server = _FailingKeyboardDisplayServer(pid=xvfb_pid)
    uc = _make_uc(display_server, repo, process, profile, display_num=99)

    # R3 — the ORIGINAL sentinel propagates unchanged.
    with pytest.raises(_KbdBoom) as excinfo:
        uc.execute(app_command="gedit", app_args=[], wait=0.0, marionette=False)
    assert excinfo.value is display_server.boom
    assert excinfo.value.__cause__ is None

    # R1 — Xvfb is stopped even on a non-marionette launch (leak closed).
    assert ("stop", xvfb_pid) in display_server.calls
    assert display_server.stopped is True

    # R5 — no marionette profile on this launch: neither provision nor cleanup
    # ran; the cleanup guard (`marionette and profile is not None`) is a no-op.
    assert profile.provision_calls == []
    assert profile.cleanup_calls == []

    # Post-state: fault reached configure_keyboard; nothing persisted.
    assert ("configure_keyboard", ":99") in display_server.calls
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# Test 3 — back-compat guard: success path unchanged (new try/except must not
# over-fire). Already GREEN today; pins that the fix does not regress success.
# ═══════════════════════════════════════════════════════════════════════


def test_configure_keyboard_success_path_leaves_resources_running() -> None:
    """back-compat: a normal successful marionette launch (configure_keyboard
    does NOT raise) must NOT stop Xvfb and must NOT call profile cleanup — the
    new failure-atomicity block only fires on the exception path.
    """
    repo = FakeSessionRepository()
    process = FakeProcess()
    profile = _ObservableMarionetteProfile()
    xvfb_pid = 5555
    display_server = FakeDisplayServer(pid=xvfb_pid)  # configure_keyboard succeeds
    uc = _make_uc(display_server, repo, process, profile, display_num=99)

    session = uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    # Success: session persisted and returned with the started Xvfb pid.
    assert session is not None
    assert session.xvfb_pid == xvfb_pid
    assert repo.load(session.session_id) is session

    # The new try/except must NOT over-fire on the success path.
    assert display_server.stopped is False
    assert ("stop", xvfb_pid) not in display_server.calls
    assert profile.cleanup_calls == []

    # configure_keyboard is still called exactly as before the fix.
    assert ("configure_keyboard", ":99") in display_server.calls
