"""AIYES-122 — post-start android device-side liveness parity (RED + guards).

Today ``SessionStartUseCase._execute_android`` returns a "successful" ``Session``
even when the launched android app never came up on the device: the only
post-start signal it could consult is the host ``app_pid`` from
``self._process.start`` — the transient adb launcher (``monkey``/``am start``),
already dead by the time ``wait`` elapses regardless of on-device outcome. The
fix consults the DEVICE-side probe
``AndroidAppLifecyclePort.is_app_running(serial, package_name)`` after the wait
and, if it reports not-running, raises ``RuntimeError`` so the AIYES-120
committed-flag ``finally`` cleans up atomically. The check is GATED
(``android_lifecycle`` wired AND ``device_serial`` AND ``package_name`` all
truthy) so every non-wired / identity-underivable path is byte-identical to
pre-AIYES-122.

RED-first (new-dependency contract): the tests that exercise the new behavior
construct ``SessionStartUseCase(..., android_lifecycle=<fake>)`` — a keyword the
CURRENT ``__init__`` does NOT accept — so they are RED today with
``TypeError: __init__() got an unexpected keyword argument 'android_lifecycle'``.
The wiring test asserts ``session_start_uc._android_lifecycle`` exists and IS the
composition-root singleton, which is RED today via ``AttributeError`` (the
attribute is not set yet and the construction site does not pass it). After A9
adds the trailing optional param + the gated check + the one wiring line, the
behavioral assertions become meaningful and GREEN.

Strata (per VALIDATED_INTENT_PKG.yaml test_taxonomy):
  RED today (new behavior; GREEN after A9):
    * DEAD-LAUNCH-raises-and-cleans   (is_app_running -> False -> raise + cleanup;
        stop app_pid once; NO save; NO stop_app; is_app_running called
        (serial, package); exception unwrapped)  — REQ-A/B/D, DEC-122-02/04, BEI-1
    * LIVE-LAUNCH-unchanged           (is_app_running -> True -> Session saved,
        app_pid NOT stopped, stop_app NOT called, probe consulted)  — REQ-A, BEI-1
    * GATE-SKIPPED-package-underivable (lifecycle wired, probe would say False,
        but package_name underivable -> triple-AND short-circuits ->
        is_app_running NEVER called; launch succeeds)  — REQ-C, BEI-3
    * WIRE-composition-root           (session_start_uc._android_lifecycle IS the
        AdbAppLifecycleAdapter singleton also passed to session_stop_uc)  — REQ-E, BEI-7

  EQUIVALENCE GUARDS (GREEN now AND after — they OMIT the new param, exercising
  the default-None gate-skipped paths; prove AIYES-122 does not regress):
    * GATE-SKIPPED-lifecycle-none     (param omitted -> default None -> no probe,
        launch succeeds exactly as pre-AIYES-122)  — REQ-C, BEI-4
    * GATE-SKIPPED-serial-absent      (device_serial missing -> pre-start
        ValueError BEFORE the try; process.start/stop never called)  — DEC-122-05/
        BEI-2
    * LINUX-untouched                 (backend='linux' success path; the new
        param never referenced; is_running(app_pid) check unchanged)  — BEI-6

Fault-injection / fake pattern follows the AIYES-120 precedent: LOCAL subclasses
of the shared conftest fakes + a LOCAL fake ``AndroidAppLifecyclePort`` — ZERO
edit to the shared conftest, lowest blast radius on the 2751-test baseline.
"""

from __future__ import annotations

import typing
from typing import Any, Dict, List, Optional, Tuple

import pytest

from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_start import SessionStartUseCase
from aiyes.ports.android_app_lifecycle import AndroidAppLifecyclePort

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)

_APP_PID = 78201  # the adb-launched app process the fake returns from start()
_DEVICE_SERIAL = "emulator-5554"
# A component that parses cleanly to package_name "com.example.app".
_APP_COMMAND = "com.example.app/.MainActivity"
_PACKAGE_NAME = "com.example.app"
# The exact android-specific message DEC-122-02 pins (names the package, keeps
# the linux "; session was not created" tail convention from _execute_linux:396).
_EXPECTED_MSG = (
    "Android app 'com.example.app' not running after startup wait; "
    "session was not created"
)

# Sentinel distinguishing "param omitted" (equivalence guard, GREEN today) from
# "param explicitly passed" (RED today via unexpected-kwarg TypeError).
_UNSET = object()


# ═══════════════════════════════════════════════════════════════════════
# LOCAL fake AndroidAppLifecyclePort — controllable is_app_running return +
# call recording for is_app_running AND stop_app (DEC-122-04 pin). Zero
# conftest edit (AIYES-120 local-fake precedent).
# ═══════════════════════════════════════════════════════════════════════


class _FakeAndroidLifecycle:
    """Fake AndroidAppLifecyclePort.

    ``is_app_running`` returns ``running[(serial, package)]`` (default
    ``_default``); every ``is_app_running`` and ``stop_app`` call is recorded so
    tests can assert call presence/absence and argument order.
    """

    def __init__(self, default: bool = True) -> None:
        self._default = default
        self.running: Dict[Tuple[str, str], bool] = {}
        self.calls: List[Tuple[str, Tuple[str, str]]] = []

    def is_app_running(self, serial: str, package_name: str) -> bool:
        self.calls.append(("is_app_running", (serial, package_name)))
        return self.running.get((serial, package_name), self._default)

    def stop_app(self, serial: str, package_name: str) -> None:
        self.calls.append(("stop_app", (serial, package_name)))


# ═══════════════════════════════════════════════════════════════════════
# Builder — passes android_lifecycle ONLY when supplied (a passed value, even
# None, is RED today via unexpected-kwarg; omitting it is GREEN today).
# ═══════════════════════════════════════════════════════════════════════


def _make_android_uc(
    *,
    process: Optional[FakeProcess] = None,
    repo: Optional[FakeSessionRepository] = None,
    clock: Optional[FakeClock] = None,
    lifecycle: Any = _UNSET,
) -> SessionStartUseCase:
    """Build a use case for the android path.

    The Linux-only ports (display_server, allocator, atspi_bus) are wired but
    never touched by ``_execute_android`` — plain fakes prove that by recording
    no calls.
    """
    kwargs: Dict[str, Any] = dict(
        display_server=FakeDisplayServer(),
        allocator=FakeDisplayAllocator(),
        atspi_bus=FakeAccessibilityBus(),
        process=process or FakeProcess(pid=_APP_PID),
        session_repo=repo or FakeSessionRepository(),
        clock=clock or FakeClock(),
    )
    if lifecycle is not _UNSET:
        kwargs["android_lifecycle"] = lifecycle
    return SessionStartUseCase(**kwargs)


def _stop_calls(process: FakeProcess) -> List[Any]:
    return [c for c in process.calls if c[0] == "stop"]


def _start_calls(process: FakeProcess) -> List[Any]:
    return [c for c in process.calls if c[0] == "start"]


def _save_calls(repo: FakeSessionRepository) -> List[Any]:
    return [c for c in repo.calls if c[0] == "save"]


def _probe_calls(lifecycle: _FakeAndroidLifecycle) -> List[Any]:
    return [c for c in lifecycle.calls if c[0] == "is_app_running"]


def _stop_app_calls(lifecycle: _FakeAndroidLifecycle) -> List[Any]:
    return [c for c in lifecycle.calls if c[0] == "stop_app"]


# ═══════════════════════════════════════════════════════════════════════
# RED — DEAD-LAUNCH-raises-and-cleans (HIGHEST; REQ-A/B/D, DEC-122-02/04, BEI-1)
#   lifecycle wired, serial present, package derivable, is_app_running -> False.
# ═══════════════════════════════════════════════════════════════════════


def test_dead_android_launch_raises_names_package_and_cleans_up() -> None:
    """A device-side not-running result after the wait raises ``RuntimeError``
    naming the package (with the linux '; session was not created' tail), stops
    the adb launcher ``app_pid`` exactly once via the AIYES-120 finally, persists
    NO session (the check precedes ``save`` — DEC-122-02 tightening), NEVER
    force-stops the package (DEC-122-04), and propagates unwrapped (bare raise).

    RED today: constructing with ``android_lifecycle=`` raises ``TypeError``
    (the param does not exist yet). GREEN after A9 adds the gated check.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    lifecycle = _FakeAndroidLifecycle(default=False)  # is_app_running -> False
    uc = _make_android_uc(process=process, repo=repo, lifecycle=lifecycle)

    with pytest.raises(RuntimeError) as excinfo:
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=0.0,
        )

    # DEC-122-02: exact android message — names the package AND keeps the tail.
    assert str(excinfo.value) == _EXPECTED_MSG
    # REQ-B: unwrapped (fresh raise, not chained) — mirrors the linux bare raise.
    assert excinfo.value.__cause__ is None
    # REQ-A: the DEVICE-side probe was consulted with (serial, package) — NOT the
    # host app_pid.
    assert _probe_calls(lifecycle) == [
        ("is_app_running", (_DEVICE_SERIAL, _PACKAGE_NAME))
    ]
    # REQ-B: the AIYES-120 finally best-effort stopped the (dead) launcher once.
    assert _stop_calls(process) == [("stop", _APP_PID)]
    # DEC-122-02 tightening: the check precedes save -> NO dead session persisted.
    assert _save_calls(repo) == []
    assert repo.load_all() == []
    # DEC-122-04: the failure path does NOT force-stop the on-device package.
    assert _stop_app_calls(lifecycle) == []


def test_dead_android_launch_does_not_force_stop_package() -> None:
    """DEC-122-04 (isolated pin): on the dead-launch path the fake lifecycle's
    ``stop_app`` is NEVER called — cleanup is limited to the AIYES-120 finally's
    best-effort ``process.stop(app_pid)`` (the already-dead launcher). Force-
    stopping would be a wasted call on a genuine dead launch and net-harmful on a
    false-negative (would kill a healthy app).

    RED today: ``android_lifecycle=`` is an unexpected kwarg (TypeError).
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    lifecycle = _FakeAndroidLifecycle(default=False)
    uc = _make_android_uc(process=process, repo=repo, lifecycle=lifecycle)

    with pytest.raises(RuntimeError):
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=0.0,
        )

    assert _stop_app_calls(lifecycle) == []


# ═══════════════════════════════════════════════════════════════════════
# RED — LIVE-LAUNCH-unchanged (REQ-A, BEI-1)
#   lifecycle wired, is_app_running -> True -> success path byte-for-byte intact.
# ═══════════════════════════════════════════════════════════════════════


def test_live_android_launch_succeeds_unchanged() -> None:
    """A True device-side result falls through to the unchanged success path: the
    same ``Session`` is constructed + saved exactly once, ``app_pid`` is NEVER
    stopped (committed suppresses the finally), ``stop_app`` is NEVER called, and
    the probe was consulted once with (serial, package).

    RED today: ``android_lifecycle=`` unexpected kwarg (TypeError). GREEN after A9.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    lifecycle = _FakeAndroidLifecycle(default=True)  # is_app_running -> True
    uc = _make_android_uc(process=process, repo=repo, lifecycle=lifecycle)

    session = uc.execute(
        app_command=_APP_COMMAND,
        app_args=[],
        backend="android",
        device_serial=_DEVICE_SERIAL,
        wait=0.0,
    )

    # A valid, persisted android Session identical to today's success output.
    assert isinstance(session, Session)
    assert session.backend == "android"
    assert session.device_serial == _DEVICE_SERIAL
    assert session.app_pid == _APP_PID
    assert session.package_name == _PACKAGE_NAME
    assert repo.load(session.session_id) is session
    # The probe was consulted (BEI-1) with (serial, package).
    assert _probe_calls(lifecycle) == [
        ("is_app_running", (_DEVICE_SERIAL, _PACKAGE_NAME))
    ]
    # save once; app_pid NEVER stopped; package NEVER force-stopped.
    assert len(_save_calls(repo)) == 1
    assert _stop_calls(process) == []
    assert _stop_app_calls(lifecycle) == []
    # start called exactly once (no wasted relaunch).
    assert len(_start_calls(process)) == 1


# ═══════════════════════════════════════════════════════════════════════
# RED — GATE-SKIPPED-package-underivable (REQ-C, BEI-3)
#   lifecycle wired + would say False, but package_name underivable -> the
#   triple-AND short-circuits at package_name -> is_app_running NEVER called.
# ═══════════════════════════════════════════════════════════════════════


def test_gate_skipped_package_underivable_probe_not_called() -> None:
    """When ``parse_android_package_identity`` yields an empty package_name (e.g.
    ``app_command='bash'`` with no android-shaped args), the gate short-circuits
    BEFORE ``is_app_running`` — the probe is NEVER called and the launch succeeds
    exactly as today, even though the wired probe would have returned False.
    Prevents a false-positive dead-launch raise when identity can't be derived.

    RED today: ``android_lifecycle=`` unexpected kwarg (TypeError). GREEN after A9.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    lifecycle = _FakeAndroidLifecycle(default=False)  # would raise IF consulted
    uc = _make_android_uc(process=process, repo=repo, lifecycle=lifecycle)

    session = uc.execute(
        app_command="bash",  # underivable: no "." package, no "/" component
        app_args=[],
        backend="android",
        device_serial=_DEVICE_SERIAL,
        wait=0.0,
    )

    # Launch succeeded (no raise) and the probe was short-circuited away.
    assert isinstance(session, Session)
    assert session.package_name == ""
    assert _probe_calls(lifecycle) == []
    assert _stop_app_calls(lifecycle) == []
    assert len(_save_calls(repo)) == 1
    assert _stop_calls(process) == []


# ═══════════════════════════════════════════════════════════════════════
# RED — WIRE-composition-root (REQ-E, BEI-7)
#   Guards against advertised-but-not-wired inert wiring (AIYES-109 class): the
#   production use case must actually receive the singleton adapter.
# ═══════════════════════════════════════════════════════════════════════


def test_composition_root_wires_android_lifecycle_into_session_start() -> None:
    """The shipped ``session_start_uc`` must carry the SAME
    ``AdbAppLifecycleAdapter`` singleton that composition_root already threads
    into ``session_stop_uc`` — proving the fix is LIVE in the app, not merely
    accepted by the class.

    RED today: ``session_start_uc`` has no ``_android_lifecycle`` attribute (the
    :359 construction does not pass it), so the ``is`` assertion raises
    ``AttributeError``. GREEN after A9 adds the param + the one wiring line.
    """
    from aiyes.cli import composition_root

    wired = composition_root.session_start_uc._android_lifecycle
    # Not merely non-None: it must be the SAME singleton instance.
    assert wired is composition_root._android_lifecycle
    # And the identical instance already passed to session_stop_uc (shared singleton).
    assert (
        composition_root.session_start_uc._android_lifecycle
        is composition_root.session_stop_uc._android_lifecycle
    )


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — GATE-SKIPPED-lifecycle-none (REQ-C, BEI-4) — GREEN now & after
#   Param OMITTED -> default None -> the gate's `is not None` is False -> no probe.
# ═══════════════════════════════════════════════════════════════════════


def test_gate_skipped_lifecycle_none_behaves_as_pre_aiyes122() -> None:
    """With ``android_lifecycle`` left at its default (param omitted, mirroring
    every pre-AIYES-122 construction site), ``_execute_android`` performs NO
    liveness check and the launch succeeds exactly as today: Session saved once,
    ``app_pid`` never stopped. Primary backward-compatibility guard — GREEN now
    AND after A9.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    uc = _make_android_uc(process=process, repo=repo)  # lifecycle omitted -> None

    session = uc.execute(
        app_command=_APP_COMMAND,
        app_args=[],
        backend="android",
        device_serial=_DEVICE_SERIAL,
        wait=0.0,
    )

    assert isinstance(session, Session)
    assert session.backend == "android"
    assert session.app_pid == _APP_PID
    assert repo.load(session.session_id) is session
    assert len(_save_calls(repo)) == 1
    assert _stop_calls(process) == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — GATE-SKIPPED-serial-absent (DEC-122-05, BEI-2) — GREEN now & after
#   Pre-start ValueError guard fires BEFORE the try -> nothing acquired, no probe.
# ═══════════════════════════════════════════════════════════════════════


def test_gate_skipped_serial_absent_raises_before_any_probe() -> None:
    """A missing ``device_serial`` raises ``ValueError`` BEFORE ``process.start``
    (outside the try, before the insertion point), so nothing is acquired and
    neither ``start`` NOR ``stop`` is ever called. Re-pins the AIYES-120
    device-serial guard and proves the liveness insertion cannot be reached when
    the pre-guard fires. GREEN now AND after.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    uc = _make_android_uc(process=process, repo=repo)  # lifecycle omitted -> None

    with pytest.raises(ValueError, match="device-serial"):
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=None,
            wait=0.0,
        )

    assert _start_calls(process) == []
    assert _stop_calls(process) == []
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — LINUX-untouched (BEI-6) — GREEN now & after
#   The android-only dependency never touches the linux path.
# ═══════════════════════════════════════════════════════════════════════


def test_linux_success_path_untouched_by_new_param() -> None:
    """A linux (backend='linux') success path is unaffected by AIYES-122: the new
    android-only dependency is never referenced, ``_execute_linux``'s own
    ``is_running(app_pid)`` check runs unchanged, and a valid linux Session is
    returned + saved. GREEN now AND after (constructed with the param omitted).
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    uc = _make_android_uc(process=process, repo=repo)  # lifecycle omitted -> None

    session = uc.execute(
        app_command="xterm",
        app_args=[],
        backend="linux",
        wait=0.0,
    )

    assert isinstance(session, Session)
    assert session.backend == "linux"
    assert session.app_pid == _APP_PID
    assert repo.load(session.session_id) is session
    assert len(_save_calls(repo)) == 1
    # The linux liveness check consulted the host app_pid (is_running), unchanged.
    assert any(c[0] == "is_running" for c in process.calls)
    # Success -> app_pid not stopped.
    assert _stop_calls(process) == []


# ═══════════════════════════════════════════════════════════════════════
# RED (rev-1) — TYPE-HINTS-RESOLVE (A10-AIYES122-001; MEDIUM)
#   __init__ annotates `android_lifecycle: Optional[AndroidAppLifecyclePort]`
#   (session_start.py:108) but AndroidAppLifecyclePort is NOT imported there.
#   `from __future__ import annotations` defers the annotation to a string so
#   runtime construction passes, masking the omission — but get_type_hints MUST
#   resolve the string to a real object and raises NameError today.
# ═══════════════════════════════════════════════════════════════════════


def test_session_start_init_type_hints_resolve() -> None:
    """``typing.get_type_hints(SessionStartUseCase.__init__)`` must resolve every
    annotation to a real object. The ``android_lifecycle`` parameter is annotated
    ``Optional[AndroidAppLifecyclePort]`` (session_start.py:108), yet
    ``AndroidAppLifecyclePort`` is NOT imported into that module — ``from
    __future__ import annotations`` defers the annotation to the string
    ``"Optional[AndroidAppLifecyclePort]"``, so plain construction never touches
    it and the omission stays latent.

    RED today: ``get_type_hints`` evaluates the deferred string in the module
    globals, where ``AndroidAppLifecyclePort`` is undefined, and raises
    ``NameError: name 'AndroidAppLifecyclePort' is not defined``. GREEN once A9
    adds the missing ``from aiyes.ports.android_app_lifecycle import
    AndroidAppLifecyclePort`` import. Guards the annotation-referenced-but-not-
    imported class (A10-AIYES122-001) from recurring — it silently breaks any
    ``get_type_hints`` / dataclass / typing-introspection consumer.
    """
    # RED today: raises NameError (AndroidAppLifecyclePort undefined in the module).
    hints = typing.get_type_hints(SessionStartUseCase.__init__)

    assert "android_lifecycle" in hints
    # Optional[X] == Union[X, None]; the resolved hint's args must include the
    # concrete port class from aiyes.ports.android_app_lifecycle AND NoneType.
    hint_args = typing.get_args(hints["android_lifecycle"])
    assert AndroidAppLifecyclePort in hint_args
    assert type(None) in hint_args
