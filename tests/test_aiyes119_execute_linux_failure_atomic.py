"""AIYES-119 — unified failure-atomic cleanup for ``_execute_linux`` (RED + guards).

Restructures ``SessionStartUseCase._execute_linux`` from 7 sprinkled per-step
cleanup call-sites into ONE guarded try/finally region gated by a ``committed``
flag. This module authors the behavioral-equivalence coverage the
VALIDATED_INTENT_PKG.yaml required_test_set demands, split into three strata:

  RED (fail today, GREEN after the refactor):
    * TAIL-GAP-002 process.is_running raises  (A5-BLOCK-002, HIGHEST realism; REQ-A)
    * TAIL-GAP-001 clock.sleep raises         (A5-BLOCK-001; REQ-A)
    * TAIL-GAP-003 clock.now raises           (A5-BLOCK-003; REQ-A)
        -> each currently UNGUARDED: nothing is released before the sentinel
           propagates, so every "resource released" assertion fails today.
    * NON-MASKING-001 (A10-AF-001; REQ-B): on an error path a cleanup call
        itself raises; today the cleanup exception MASKS the original and
        blocks the later releases. Fails today (wrong exception propagates).
    * IS-RUNNING-DESIGN-PIN (DEC-119-01, option (b); REQ-D): on the
        is_running==False path option (b) requires an UNCONDITIONAL
        process.stop(app_pid). Today that call is deliberately absent, so the
        "process.stop called exactly once" assertion fails.

  EQUIVALENCE GUARDS (GREEN today AND must stay GREEN after the refactor —
  these pin the CURRENT cleanup effect of the three error paths that ship with
  NO test coverage, so the restructure's dominant risk (silently regressing an
  existing error path) is falsifiable path-by-path):
    * EQUIV-xvfb-start-fail   (matrix row: xvfb-start-fail; REQ-A/REQ-D)
    * EQUIV-bus-start-fail    (matrix row: bus-start-fail;  REQ-A/REQ-D)
    * EQUIV-empty-bus-address (matrix row: empty-bus-address; REQ-A/REQ-C/REQ-D)
    * success-path guard      (DEC-119-03 committed-flag non-firing; REQ-D)

Fault injection mirrors the AIYES-118 precedent: LOCAL subclasses of the shared
conftest fakes — zero edit to the shared conftest, lowest blast radius on the
2696-test baseline. Each fault carries a concrete ``.boom`` sentinel instance so
identity (REQ-C bare re-raise: ``is`` + ``__cause__ is None``) is directly
assertable.

Traceability (VALIDATED_INTENT_PKG.yaml required_test_set):
  iv_add_tail_gap_red   -> test_tail_gap_002_* / _001_* / _003_*
  iii_add_non_masking   -> test_non_masking_001_original_error_wins_over_cleanup_error
  v_add_design_pin      -> test_is_running_false_design_pin_process_stop_called
  ii_add_equivalence... -> test_equiv_xvfb_start_fail / _bus_start_fail / _empty_bus_address
  DEC-119-03 back-compat -> test_success_path_marionette_leaves_resources_running_profile_present
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from aiyes.domain.session import Session
from aiyes.domain.types import BusStartResult
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
_DISPLAY_NUM = 99
_XVFB_PID = 4242
_APP_PID = 54321
_BUS_PID = 8888  # FakeAccessibilityBus default pid


# ═══════════════════════════════════════════════════════════════════════
# Sentinel exceptions — distinct RuntimeError subtypes so the test can assert
# the ORIGINAL failure propagates (REQ-C) and, for NON-MASKING, that the
# original wins over a DISTINCT cleanup-failure type (REQ-B).
# ═══════════════════════════════════════════════════════════════════════


class _SleepBoom(RuntimeError):
    """Raised by the fault-injecting clock's ``sleep`` (TAIL-GAP-001)."""


class _NowBoom(RuntimeError):
    """Raised by the fault-injecting clock's ``now`` (TAIL-GAP-003)."""


class _IsRunningBoom(RuntimeError):
    """Raised by the fault-injecting process's ``is_running`` (TAIL-GAP-002)."""


class _AppStartBoom(RuntimeError):
    """The ORIGINAL launch failure for NON-MASKING-001 (process.start raises)."""


class _CleanupBoom(RuntimeError):
    """A CLEANUP-time failure (display_server.stop raises) for NON-MASKING-001.

    A DISTINCT type from ``_AppStartBoom`` so a masking bug is detectable: if
    this type reaches the caller, the cleanup exception wrongly replaced the
    original launch failure.
    """


class _XvfbStartBoom(RuntimeError):
    """Raised by the fault-injecting display server's ``start`` (EQUIV-xvfb)."""


class _BusStartBoom(RuntimeError):
    """Raised by the fault-injecting bus's ``start_bus`` (EQUIV-bus)."""


# ═══════════════════════════════════════════════════════════════════════
# Fault-injection fakes (LOCAL subclasses — zero edit to the shared conftest,
# per the _FailingKeyboardDisplayServer precedent in test_aiyes118_*).
# ═══════════════════════════════════════════════════════════════════════


class _FailingSleepClock(FakeClock):
    """Clock whose ``sleep`` raises AFTER full resource acquisition."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _SleepBoom("clock.sleep failed (injected AIYES-119 TAIL-GAP-001)")

    def sleep(self, seconds: float) -> None:
        self.calls.append(("sleep", seconds))
        raise self.boom


class _FailingNowClock(FakeClock):
    """Clock whose ``now`` raises (sleep inherited/normal) — TAIL-GAP-003.

    ``_execute_linux`` calls ``now()`` only once, at the Session-construct step
    (line ~404), so overriding it faults exactly that tail step.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _NowBoom("clock.now failed (injected AIYES-119 TAIL-GAP-003)")

    def now(self) -> float:
        self.calls.append(("now", None))
        raise self.boom


class _FailingIsRunningProcess(FakeProcess):
    """Process whose ``is_running`` RAISES (distinct from returning False).

    ``start`` still succeeds (app_pid acquired), ``stop`` inherited/safe — so the
    cleanup path can call ``stop(app_pid)`` as a real, observable release.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _IsRunningBoom(
            "process.is_running failed (injected AIYES-119 TAIL-GAP-002)"
        )

    def is_running(self, pid: int) -> bool:
        self.calls.append(("is_running", pid))
        raise self.boom


class _FailingStartProcess(FakeProcess):
    """Process whose ``start`` raises — the ORIGINAL app-launch failure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _AppStartBoom("app failed to launch (injected AIYES-119)")

    def start(self, command: str, args: List[str], env: Optional[dict] = None) -> int:
        self.calls.append(("start", (command, args, env)))
        raise self.boom


class _FailingStartDisplayServer(FakeDisplayServer):
    """Display server whose ``start`` (Xvfb launch) raises — EQUIV-xvfb-start-fail."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _XvfbStartBoom("Xvfb failed to start (injected AIYES-119)")

    def start(self, display_num: int, resolution: str, color_depth: int) -> int:
        self.calls.append(("start", (display_num, resolution, color_depth)))
        raise self.boom


class _FailingStopDisplayServer(FakeDisplayServer):
    """Display server whose ``stop`` records the attempt THEN raises — the
    CLEANUP-time failure for NON-MASKING-001.

    Recording before raising keeps the attempt observable (proves the release
    was reached) while still faulting the call (proves the original error, not
    this one, must win and that the remaining releases still fire).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _CleanupBoom("display_server.stop failed (injected AIYES-119)")

    def stop(self, pid: int) -> None:
        self.calls.append(("stop", pid))
        self.stopped = True
        raise self.boom


class _FailingStartBus(FakeAccessibilityBus):
    """Bus whose ``start_bus`` raises — EQUIV-bus-start-fail."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _BusStartBoom("AT-SPI2 bus failed to start (injected AIYES-119)")

    def start_bus(self, display: str) -> BusStartResult:
        self.calls.append(("start_bus", display))
        raise self.boom


class _ObservableMarionetteProfile:
    """Observable fake for MarionetteProfilePort (mirrors test_aiyes118_*).

    Records ``provision`` / ``cleanup`` so the launch-generated session_id is
    recoverable and the ``cleanup(session_id)`` release is directly assertable.
    """

    def __init__(self) -> None:
        self.provision_calls: list = []  # (session_id, port, existing_profile)
        self.cleanup_calls: list = []

    def provision(self, session_id: str, port: int, existing_profile: Any) -> str:
        self.provision_calls.append((session_id, port, existing_profile))
        return existing_profile or f"/fake/aiyes-marionette-profile-{session_id}"

    def cleanup(self, session_id: str) -> None:
        self.cleanup_calls.append(session_id)


# ═══════════════════════════════════════════════════════════════════════
# Builders / shared assertions
# ═══════════════════════════════════════════════════════════════════════


def _make_uc(
    *,
    display_server: FakeDisplayServer,
    atspi_bus: Optional[FakeAccessibilityBus] = None,
    process: Optional[FakeProcess] = None,
    repo: Optional[FakeSessionRepository] = None,
    clock: Optional[FakeClock] = None,
    profile: Optional[_ObservableMarionetteProfile] = None,
    display_num: int = _DISPLAY_NUM,
) -> SessionStartUseCase:
    return SessionStartUseCase(
        display_server=display_server,
        allocator=FakeDisplayAllocator(display_num=display_num),
        atspi_bus=atspi_bus or FakeAccessibilityBus(),
        process=process or FakeProcess(pid=_APP_PID),
        session_repo=repo or FakeSessionRepository(),
        clock=clock or FakeClock(),
        marionette_profile=profile,
    )


def _provisioned_sid(profile: _ObservableMarionetteProfile) -> str:
    """Recover the launch-generated session_id from the profile fake."""
    assert profile.provision_calls, "marionette profile was never provisioned"
    return profile.provision_calls[-1][0]


def _assert_full_release(
    *,
    process: FakeProcess,
    atspi_bus: FakeAccessibilityBus,
    display_server: FakeDisplayServer,
    profile: _ObservableMarionetteProfile,
    repo: FakeSessionRepository,
    provisioned_sid: str,
    app_pid: int = _APP_PID,
    xvfb_pid: int = _XVFB_PID,
    bus_pid: int = _BUS_PID,
) -> None:
    """Assert every acquired resource was released (REQ-A full-failure-atomicity).

    Observable post-state, not surface: which release calls fired, on which pid.
    process.stop is asserted per DEC-119-01 option (b) (unconditional stop of an
    acquired app_pid). Release completeness is order-agnostic here; ordering
    itself is pinned by the retained save-fail guard and the EQUIV rows.
    """
    # process.stop(app_pid) — option (b): the app process is released.
    assert ("stop", app_pid) in process.calls, "app process was not stopped"
    # AT-SPI2 bus released.
    assert atspi_bus.stopped is True
    assert ("stop_bus", bus_pid) in atspi_bus.calls, "bus was not stopped"
    # Xvfb released.
    assert display_server.stopped is True
    assert ("stop", xvfb_pid) in display_server.calls, "xvfb was not stopped"
    # aiyes-owned temp Marionette profile released for THIS session.
    assert profile.cleanup_calls == [provisioned_sid], "temp profile not cleaned"
    # Failure-atomic: no partial session persisted.
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# RED — TAIL-GAP-002: process.is_running RAISES  (HIGHEST realism; A5-BLOCK-002)
# ═══════════════════════════════════════════════════════════════════════


def test_tail_gap_002_is_running_raises_releases_all_resources() -> None:
    """REQ-A: if ``process.is_running(app_pid)`` RAISES after full acquisition
    (marionette profile + Xvfb + bus + app process), every resource is released
    before the ORIGINAL sentinel propagates unwrapped.

    RED today: the line ~395 ``is_running`` call is unguarded — the exception
    propagates with NO cleanup, so app/bus/xvfb/profile all leak.
    """
    repo = FakeSessionRepository()
    process = _FailingIsRunningProcess(pid=_APP_PID)
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus()
    display_server = FakeDisplayServer(pid=_XVFB_PID)
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    with pytest.raises(_IsRunningBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    # REQ-C: the ORIGINAL sentinel propagates unwrapped.
    assert excinfo.value is process.boom
    assert excinfo.value.__cause__ is None
    # The fault actually reached the tail is_running step (post-state depth).
    assert ("is_running", _APP_PID) in process.calls

    _assert_full_release(
        process=process,
        atspi_bus=atspi_bus,
        display_server=display_server,
        profile=profile,
        repo=repo,
        provisioned_sid=_provisioned_sid(profile),
    )


# ═══════════════════════════════════════════════════════════════════════
# RED — TAIL-GAP-001: clock.sleep RAISES  (A5-BLOCK-001)
# ═══════════════════════════════════════════════════════════════════════


def test_tail_gap_001_clock_sleep_raises_releases_all_resources() -> None:
    """REQ-A: if ``clock.sleep(wait)`` RAISES after full acquisition (wait > 0),
    every resource is released before the ORIGINAL sentinel propagates unwrapped.

    RED today: the line ~393 ``sleep`` call is unguarded — no cleanup fires.
    """
    repo = FakeSessionRepository()
    process = FakeProcess(pid=_APP_PID)
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus()
    display_server = FakeDisplayServer(pid=_XVFB_PID)
    clock = _FailingSleepClock()
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        clock=clock,
        profile=profile,
    )

    with pytest.raises(_SleepBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=2.0, marionette=True)

    assert excinfo.value is clock.boom
    assert excinfo.value.__cause__ is None
    # Post-state depth: the app process was started before the wait faulted.
    assert ("sleep", 2.0) in clock.calls
    assert any(c[0] == "start" for c in process.calls)

    _assert_full_release(
        process=process,
        atspi_bus=atspi_bus,
        display_server=display_server,
        profile=profile,
        repo=repo,
        provisioned_sid=_provisioned_sid(profile),
    )


# ═══════════════════════════════════════════════════════════════════════
# RED — TAIL-GAP-003: clock.now RAISES  (A5-BLOCK-003)
# ═══════════════════════════════════════════════════════════════════════


def test_tail_gap_003_clock_now_raises_releases_all_resources() -> None:
    """REQ-A: if ``clock.now()`` RAISES after ``is_running`` returned True, every
    resource is released before the ORIGINAL sentinel propagates unwrapped.

    RED today: the line ~404 ``now`` call is unguarded — no cleanup fires.
    """
    repo = FakeSessionRepository()
    process = FakeProcess(pid=_APP_PID)  # is_running() -> True (started)
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus()
    display_server = FakeDisplayServer(pid=_XVFB_PID)
    clock = _FailingNowClock()
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        clock=clock,
        profile=profile,
    )

    with pytest.raises(_NowBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    assert excinfo.value is clock.boom
    assert excinfo.value.__cause__ is None
    # Post-state depth: liveness was confirmed True before now() faulted.
    assert ("is_running", _APP_PID) in process.calls
    assert ("now", None) in clock.calls

    _assert_full_release(
        process=process,
        atspi_bus=atspi_bus,
        display_server=display_server,
        profile=profile,
        repo=repo,
        provisioned_sid=_provisioned_sid(profile),
    )


# ═══════════════════════════════════════════════════════════════════════
# RED — NON-MASKING-001: original error wins over a raising cleanup call
#   (A10-AF-001; REQ-B best-effort per-call cleanup)
# ═══════════════════════════════════════════════════════════════════════


def test_non_masking_001_original_error_wins_over_cleanup_error() -> None:
    """REQ-B: on an error path (app-start-fail) where a cleanup call itself
    RAISES (display_server.stop), the ORIGINAL launch failure must propagate —
    NOT the cleanup failure — and the OTHER releases must still fire.

    RED today: the app-start-fail block calls stop_bus, then display_server.stop
    (raises) — the cleanup ``_CleanupBoom`` MASKS the original ``_AppStartBoom``
    and the subsequent profile cleanup never runs.
    """
    repo = FakeSessionRepository()
    process = _FailingStartProcess(pid=_APP_PID)  # process.start raises the ORIGINAL
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus()
    display_server = _FailingStopDisplayServer(pid=_XVFB_PID)  # stop() raises cleanup
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    with pytest.raises(_AppStartBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    # REQ-B / REQ-C: the ORIGINAL app-start failure wins identity, unwrapped —
    # the cleanup _CleanupBoom did NOT replace it.
    assert excinfo.value is process.boom
    assert not isinstance(excinfo.value, _CleanupBoom)
    assert excinfo.value.__cause__ is None

    # Best-effort: the failing release (xvfb.stop) was reached...
    assert ("stop", _XVFB_PID) in display_server.calls
    # ...and did NOT block the other releases — bus stop + profile cleanup fired.
    assert atspi_bus.stopped is True
    assert ("stop_bus", _BUS_PID) in atspi_bus.calls
    assert profile.cleanup_calls == [_provisioned_sid(profile)]
    # Failure-atomic: nothing persisted.
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# RED — IS-RUNNING-DESIGN-PIN: option (b) unconditional process.stop
#   (DEC-119-01; REQ-D)
# ═══════════════════════════════════════════════════════════════════════


def test_is_running_false_design_pin_process_stop_called() -> None:
    """DEC-119-01 option (b): when the app exits during the wait
    (``is_running`` RETURNS False), the unified finally calls
    ``process.stop(app_pid)`` exactly once (a harmless no-op on the shipped
    adapter) IN ADDITION to bus stop, xvfb stop, and profile cleanup — the
    RuntimeError('...exited during startup...') still propagates.

    RED today: the is_running==False block deliberately does NOT call
    process.stop, so the "stop called exactly once" assertion fails.
    """

    class _EarlyExitProcess(FakeProcess):
        """start() succeeds then marks the pid already-exited (is_running False)."""

        def start(
            self, command: str, args: List[str], env: Optional[dict] = None
        ) -> int:
            pid = super().start(command, args, env)
            self._running[pid] = False
            return pid

    repo = FakeSessionRepository()
    process = _EarlyExitProcess(pid=_APP_PID)
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus()
    display_server = FakeDisplayServer(pid=_XVFB_PID)
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    with pytest.raises(RuntimeError, match="exited during startup"):
        uc.execute(app_command="firefox", app_args=[], wait=2.0, marionette=True)

    # Option (b): process.stop(app_pid) fired EXACTLY once (the pinned decision).
    process_stop_calls = [c for c in process.calls if c[0] == "stop"]
    assert process_stop_calls == [("stop", _APP_PID)]
    # ...alongside the releases that already fired on this path today.
    bus_stop_calls = [c for c in atspi_bus.calls if c[0] == "stop_bus"]
    assert len(bus_stop_calls) == 1
    xvfb_stop_calls = [c for c in display_server.calls if c[0] == "stop"]
    assert len(xvfb_stop_calls) == 1
    assert profile.cleanup_calls == [_provisioned_sid(profile)]
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — EQUIV-xvfb-start-fail  (GREEN now AND after; REQ-A/REQ-D)
# ═══════════════════════════════════════════════════════════════════════


def test_equiv_xvfb_start_fail_cleans_profile_only() -> None:
    """Equivalence guard (GREEN now): when ``display_server.start`` raises on a
    marionette launch, TODAY only the temp profile is cleaned (nothing else was
    acquired). The refactor must reproduce exactly this — no bus/xvfb/process
    release for resources never acquired.
    """
    repo = FakeSessionRepository()
    process = FakeProcess(pid=_APP_PID)
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus()
    display_server = _FailingStartDisplayServer(pid=_XVFB_PID)
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    with pytest.raises(_XvfbStartBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    assert excinfo.value is display_server.boom
    assert excinfo.value.__cause__ is None

    # Only the profile is released — Xvfb start raised, so nothing else was up.
    assert profile.cleanup_calls == [_provisioned_sid(profile)]
    assert display_server.stopped is False
    assert ("stop", _XVFB_PID) not in display_server.calls
    assert atspi_bus.stopped is False
    assert not any(c[0] == "stop_bus" for c in atspi_bus.calls)
    assert not any(c[0] == "start_bus" for c in atspi_bus.calls)  # bus never started
    assert not any(c[0] == "stop" for c in process.calls)
    assert not any(c[0] == "start" for c in process.calls)  # app never started
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — EQUIV-bus-start-fail  (GREEN now AND after; REQ-A/REQ-D)
# ═══════════════════════════════════════════════════════════════════════


def test_equiv_bus_start_fail_stops_xvfb_and_cleans_profile() -> None:
    """Equivalence guard (GREEN now): when ``atspi_bus.start_bus`` raises on a
    marionette launch, TODAY Xvfb is stopped and the temp profile is cleaned;
    the bus itself was never acquired (no stop_bus). The refactor must reproduce
    exactly this.
    """
    repo = FakeSessionRepository()
    process = FakeProcess(pid=_APP_PID)
    profile = _ObservableMarionetteProfile()
    atspi_bus = _FailingStartBus()
    display_server = FakeDisplayServer(pid=_XVFB_PID)
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    with pytest.raises(_BusStartBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    assert excinfo.value is atspi_bus.boom
    assert excinfo.value.__cause__ is None

    # Xvfb stopped, profile cleaned; bus never acquired -> no stop_bus.
    assert display_server.stopped is True
    assert ("stop", _XVFB_PID) in display_server.calls
    assert profile.cleanup_calls == [_provisioned_sid(profile)]
    assert atspi_bus.stopped is False
    assert not any(c[0] == "stop_bus" for c in atspi_bus.calls)
    assert not any(c[0] == "start" for c in process.calls)  # app never started
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — EQUIV-empty-bus-address
#   (GREEN now AND after; REQ-A/REQ-C/REQ-D)
# ═══════════════════════════════════════════════════════════════════════


def test_equiv_empty_bus_address_stops_bus_xvfb_and_cleans_profile() -> None:
    """Equivalence guard (GREEN now): when ``start_bus`` SUCCEEDS but returns an
    empty bus address on a marionette launch, TODAY the bus is stopped, Xvfb is
    stopped, and the temp profile is cleaned, and the RuntimeError message is
    intact. The refactor must reproduce exactly this (REQ-C: the new_raise site's
    message reaches the caller unmasked).
    """
    repo = FakeSessionRepository()
    process = FakeProcess(pid=_APP_PID)
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus(bus_address="")  # empty -> triggers validation
    display_server = FakeDisplayServer(pid=_XVFB_PID)
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    with pytest.raises(RuntimeError, match="empty bus address") as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    # REQ-C: the constructed RuntimeError reaches the caller unwrapped.
    assert excinfo.value.__cause__ is None

    # Bus stopped, Xvfb stopped, profile cleaned.
    assert atspi_bus.stopped is True
    assert ("stop_bus", _BUS_PID) in atspi_bus.calls
    assert display_server.stopped is True
    assert ("stop", _XVFB_PID) in display_server.calls
    assert profile.cleanup_calls == [_provisioned_sid(profile)]
    assert not any(c[0] == "start" for c in process.calls)  # app never started
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — success path unchanged
#   (DEC-119-03 committed-flag non-firing; REQ-D)
# ═══════════════════════════════════════════════════════════════════════


def test_success_path_marionette_leaves_resources_running_profile_present() -> None:
    """DEC-119-03 back-compat (GREEN now AND after): a normal marionette launch
    returns a valid Session, leaves process/bus/xvfb running, and PRESERVES the
    temp profile (the finally must NOT fire on the success path — cleaning the
    profile would delete a live Firefox's profile).
    """
    repo = FakeSessionRepository()
    process = FakeProcess(pid=_APP_PID)
    profile = _ObservableMarionetteProfile()
    atspi_bus = FakeAccessibilityBus()
    display_server = FakeDisplayServer(pid=_XVFB_PID)
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    session = uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    # A valid Session is returned and persisted.
    assert isinstance(session, Session)
    assert session.xvfb_pid == _XVFB_PID
    assert session.app_pid == _APP_PID
    assert session.marionette_port == _MARIONETTE_BASE_PORT + _DISPLAY_NUM
    assert repo.load(session.session_id) is session

    # No cleanup fired on the success path — resources left running.
    assert display_server.stopped is False
    assert not any(c[0] == "stop" for c in display_server.calls)
    assert atspi_bus.stopped is False
    assert not any(c[0] == "stop_bus" for c in atspi_bus.calls)
    assert not any(c[0] == "stop" for c in process.calls)
    # The aiyes-owned temp profile is PRESERVED (provisioned, never cleaned).
    assert profile.provision_calls, "profile should have been provisioned"
    assert profile.cleanup_calls == []


# ═══════════════════════════════════════════════════════════════════════
# ORDER PIN — reverse-acquisition cross-resource release order
#   (A10-AIYES119-MED-001; DEC-119-05; REQ-A)
# ═══════════════════════════════════════════════════════════════════════


def test_cleanup_release_order_is_reverse_acquisition() -> None:
    """DEC-119-05 order pin (addresses A10-AIYES119-MED-001): on a FULL-RELEASE
    path the unified ``finally`` releases the four cross-resource holds in EXACT
    reverse-acquisition order —
    ``process.stop(app_pid)`` -> ``bus.stop_bus(bus_pid)`` ->
    ``display_server.stop(xvfb_pid)`` -> ``profile.cleanup(session_id)``.

    A single SHARED ordered recorder is threaded through all four resource
    fakes; each appends a labeled event as its release fires, into ONE list. The
    order-agnostic completeness guards (``_assert_full_release`` and the EQUIV
    rows) assert only WHICH releases fired, so a regression that reordered the
    same four releases (same set, wrong order) would survive them. This
    exact-sequence assertion is the pin that such a permutation must fail.

    Full-release trigger: ``process.is_running`` raises after full marionette
    acquisition (reuses the TAIL-GAP-002 injection) so all four releases fire.
    """
    events: List[Any] = []

    class _RecordingIsRunningProcess(_FailingIsRunningProcess):
        """process.stop records ('process.stop', pid) THEN releases normally."""

        def stop(self, pid: int) -> None:
            events.append(("process.stop", pid))
            super().stop(pid)

    class _RecordingBus(FakeAccessibilityBus):
        """bus.stop_bus records ('bus.stop_bus', pid) THEN releases normally."""

        def stop_bus(self, pid: int) -> None:
            events.append(("bus.stop_bus", pid))
            super().stop_bus(pid)

    class _RecordingDisplayServer(FakeDisplayServer):
        """display_server.stop records ('display.stop', pid) THEN releases."""

        def stop(self, pid: int) -> None:
            events.append(("display.stop", pid))
            super().stop(pid)

    class _RecordingProfile(_ObservableMarionetteProfile):
        """profile.cleanup records ('profile.cleanup', sid) THEN releases."""

        def cleanup(self, session_id: str) -> None:
            events.append(("profile.cleanup", session_id))
            super().cleanup(session_id)

    repo = FakeSessionRepository()
    process = _RecordingIsRunningProcess(pid=_APP_PID)
    profile = _RecordingProfile()
    atspi_bus = _RecordingBus()
    display_server = _RecordingDisplayServer(pid=_XVFB_PID)
    uc = _make_uc(
        display_server=display_server,
        atspi_bus=atspi_bus,
        process=process,
        repo=repo,
        profile=profile,
    )

    with pytest.raises(_IsRunningBoom) as excinfo:
        uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    # The ORIGINAL sentinel still wins unwrapped — the recorder must not perturb
    # the non-masking / bare-raise contract the sibling tests pin (REQ-C).
    assert excinfo.value is process.boom
    assert excinfo.value.__cause__ is None

    # THE PIN: the shared recorder captured EXACTLY the reverse-acquisition
    # sequence (list equality is order-sensitive — a permuted release order
    # yields a different list and fails here).
    provisioned_sid = _provisioned_sid(profile)
    assert events == [
        ("process.stop", _APP_PID),
        ("bus.stop_bus", _BUS_PID),
        ("display.stop", _XVFB_PID),
        ("profile.cleanup", provisioned_sid),
    ]

    # Completeness still holds — the order pin is ADDITIVE to the release set,
    # it does not weaken the order-agnostic full-release guarantee.
    _assert_full_release(
        process=process,
        atspi_bus=atspi_bus,
        display_server=display_server,
        profile=profile,
        repo=repo,
        provisioned_sid=provisioned_sid,
    )
