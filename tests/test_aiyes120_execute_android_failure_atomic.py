"""AIYES-120 — failure-atomic cleanup for ``_execute_android`` (RED + guards).

The Android sibling of AIYES-119. Today ``SessionStartUseCase._execute_android``
acquires the adb-launched app process (``app_pid = self._process.start(...)``)
and then runs an UNGUARDED tail — ``clock.sleep`` -> ``clock.now`` ->
``parse_android_package_identity`` -> ``Session(...)`` — before a save-only
try/except that stops ``app_pid`` on a save failure. If any tail step BEFORE the
save raises, ``app_pid`` leaks (nothing stops it). This module authors the
behavioral-equivalence coverage the VALIDATED_INTENT_PKG.yaml required_test_set
demands, split into three strata:

  RED (fail today, GREEN after the fold into one committed-flag finally):
    * TAIL-GAP-android-parse-identity  (HIGHEST — the only post-start step with
        an EXTERNALLY-REACHABLE normal-Exception trigger: a non-str ``app_args``
        element reaches ``parse_android_package_identity`` and raises
        ``ValueError`` (AIYES-121 domain guard) after ``process.start`` returned
        ``app_pid``; REQ-A)
    * TAIL-GAP-android-sleep           (clock.sleep raises after start; REQ-A)
    * TAIL-GAP-android-now             (clock.now raises after sleep; REQ-A)
    * TAIL-GAP-android-session-construct (Session __post_init__ raises via a
        monkeypatched ``validate_session_id``; defense-in-depth, REQ-A)
        -> each currently UNGUARDED: nothing stops ``app_pid`` before the
           exception propagates, so every "app_pid was stopped" assertion fails
           today.
    * NON-MASKING-android (REQ-B/DEC-120-02): on an error path (clock.sleep
        raises) the finally's ``process.stop(app_pid)`` ITSELF raises; the
        ORIGINAL sleep failure must win identity (the stop failure is swallowed).
        Fails today because ``stop`` is never even attempted (no finally exists).

  EQUIVALENCE GUARDS (GREEN today AND must stay GREEN after the fold — they pin
  the CURRENT observable behavior of the paths the refactor must preserve; the
  save-fail path ships with ZERO test coverage on the android branch today, so
  the dominant refactor risk (silently regressing DEC-120-04's fold) is now
  falsifiable):
    * EQUIV-android-save-fail            (repo.save raises -> stop app_pid +
        original propagates; closes the zero-coverage gap; REQ-A/REQ-C/REQ-D)
    * EQUIV-android-device-serial-valueerror (pre-start ValueError -> neither
        process.start NOR process.stop ever called; DEC-120-05; REQ-D)
    * SUCCESS-android-unchanged          (success -> Session returned, app_pid
        NEVER stopped, save called once, start called once; DEC-120-03; REQ-D)

Fault injection mirrors the AIYES-119 precedent
(``test_aiyes119_execute_linux_failure_atomic.py``): LOCAL subclasses of the
shared conftest fakes carrying a concrete ``.boom`` sentinel instance — ZERO
edit to the shared conftest, lowest blast radius on the 2706-test baseline. The
parse-identity RED uniquely needs NO fault injection: a non-str ``app_args``
element is a real reachable input, so the PRODUCTION
``parse_android_package_identity`` genuinely raises.

Traceability (VALIDATED_INTENT_PKG.yaml required_test_set):
  ii_add_tail_gap_red    -> test_tail_gap_android_parse_identity_* / _sleep_* /
                            _now_* / _session_construct_*
  iii_add_non_masking    -> test_non_masking_android_original_error_wins_over_stop_error
  iv_add_equivalence...  -> test_equiv_android_save_fail_* /
                            test_equiv_android_device_serial_valueerror_* /
                            test_success_android_unchanged_*
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_start import SessionStartUseCase

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)

_APP_PID = 77001  # the adb-launched app process the fake returns from start()
_DEVICE_SERIAL = "emulator-5554"
# A well-formed component that parses cleanly — used by every test EXCEPT the
# parse-identity RED (which deliberately passes a malformed non-str app_args).
_APP_COMMAND = "com.example.app/.MainActivity"


# ═══════════════════════════════════════════════════════════════════════
# Sentinel exceptions — distinct RuntimeError subtypes so the tests can assert
# the ORIGINAL failure propagates unwrapped (REQ-C) and, for NON-MASKING, that
# the original wins over a DISTINCT cleanup-failure type (REQ-B).
# ═══════════════════════════════════════════════════════════════════════


class _SleepBoom(RuntimeError):
    """Raised by the fault-injecting clock's ``sleep`` (TAIL-GAP-android-sleep)."""


class _NowBoom(RuntimeError):
    """Raised by the fault-injecting clock's ``now`` (TAIL-GAP-android-now)."""


class _SessionConstructBoom(RuntimeError):
    """Raised by a monkeypatched ``validate_session_id`` — makes the REAL
    ``Session.__post_init__`` raise at the construct step
    (TAIL-GAP-android-session-construct)."""


class _StopBoom(RuntimeError):
    """A CLEANUP-time failure — the finally's ``process.stop(app_pid)`` raises.

    A DISTINCT type from ``_SleepBoom`` so a masking bug is detectable: if this
    type reaches the caller, the cleanup exception wrongly replaced the original
    launch failure (NON-MASKING-android).
    """


# ═══════════════════════════════════════════════════════════════════════
# Fault-injection fakes (LOCAL subclasses — zero edit to the shared conftest,
# per the AIYES-119 _Failing* precedent).
# ═══════════════════════════════════════════════════════════════════════


class _FailingSleepClock(FakeClock):
    """Clock whose ``sleep`` raises AFTER ``process.start`` acquired app_pid."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _SleepBoom("clock.sleep failed (injected AIYES-120 TAIL-GAP-sleep)")

    def sleep(self, seconds: float) -> None:
        self.calls.append(("sleep", seconds))
        raise self.boom


class _FailingNowClock(FakeClock):
    """Clock whose ``now`` raises — TAIL-GAP-android-now.

    ``_execute_android`` calls ``now()`` exactly once (the ``started_at`` line,
    session_start.py:195), so overriding it faults exactly that tail step.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _NowBoom("clock.now failed (injected AIYES-120 TAIL-GAP-now)")

    def now(self) -> float:
        self.calls.append(("now", None))
        raise self.boom


class _StopBoomProcess(FakeProcess):
    """Process whose ``start`` succeeds (app_pid acquired) but whose ``stop``
    records the attempt THEN raises — the CLEANUP-time failure for
    NON-MASKING-android.

    Recording before raising keeps the release attempt observable (proves the
    finally reached ``stop(app_pid)``) while still faulting the call (proves the
    ORIGINAL error, not this one, must win identity).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.boom = _StopBoom("process.stop failed (injected AIYES-120 NON-MASKING)")

    def stop(self, pid: int) -> None:
        self.calls.append(("stop", pid))
        raise self.boom


# ═══════════════════════════════════════════════════════════════════════
# Builder
# ═══════════════════════════════════════════════════════════════════════


def _make_android_uc(
    *,
    process: Optional[FakeProcess] = None,
    repo: Optional[FakeSessionRepository] = None,
    clock: Optional[FakeClock] = None,
) -> SessionStartUseCase:
    """Build a use case for the android path.

    The Linux-only ports (display_server, allocator, atspi_bus) are wired but
    NEVER touched by ``_execute_android`` — supplying plain fakes proves that by
    construction (they record no calls).
    """
    return SessionStartUseCase(
        display_server=FakeDisplayServer(),
        allocator=FakeDisplayAllocator(),
        atspi_bus=FakeAccessibilityBus(),
        process=process or FakeProcess(pid=_APP_PID),
        session_repo=repo or FakeSessionRepository(),
        clock=clock or FakeClock(),
    )


def _android_stop_calls(process: FakeProcess) -> List[Any]:
    return [c for c in process.calls if c[0] == "stop"]


# ═══════════════════════════════════════════════════════════════════════
# RED — TAIL-GAP-android-parse-identity  (HIGHEST realism; REQ-A)
#   A non-str app_args element reaches parse_android_package_identity and the
#   PRODUCTION function raises ValueError (AIYES-121 domain guard) AFTER
#   process.start acquired app_pid. No fault injection — a real,
#   externally-reachable malformed input.
# ═══════════════════════════════════════════════════════════════════════


def test_tail_gap_android_parse_identity_raises_stops_app_pid() -> None:
    """REQ-A (HIGHEST): a non-str ``app_args`` element (``["-n", 42]``) makes the
    real ``parse_android_package_identity`` raise ``ValueError`` (AIYES-121's
    domain type guard — was ``AttributeError`` pre-AIYES-121) after
    ``process.start`` returned ``app_pid``. The adb-launched ``app_pid`` must be
    stopped before the exception propagates unwrapped.

    AIYES-121 note: the exception TYPE changed (AttributeError -> ValueError); the
    failure-atomic cleanup this test pins is exception-type-agnostic, so the
    app_pid-stopped, unwrapped-``__cause__``, and empty-repo assertions are
    unchanged.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    uc = _make_android_uc(process=process, repo=repo)

    with pytest.raises(ValueError, match="42") as excinfo:
        # candidates = ["am", "-n", 42] -> the "-n"/42 pair reaches the AIYES-121
        # domain type guard, which raises ValueError naming the element.
        uc.execute(
            app_command="am",
            app_args=["-n", 42],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=0.0,
        )

    # REQ-C: the ValueError propagates unwrapped (fresh raise, not chained/wrapped).
    assert excinfo.value.__cause__ is None
    # REQ-A: the acquired app_pid was stopped exactly once before propagation.
    assert _android_stop_calls(process) == [("stop", _APP_PID)]
    # Failure-atomic: no partial session persisted.
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# RED — TAIL-GAP-android-sleep  (clock.sleep raises after start; REQ-A)
# ═══════════════════════════════════════════════════════════════════════


def test_tail_gap_android_sleep_raises_stops_app_pid() -> None:
    """REQ-A: if ``clock.sleep(wait)`` raises (wait > 0) after ``process.start``,
    the acquired ``app_pid`` is stopped before the ORIGINAL sentinel propagates
    unwrapped.

    RED today: the sleep step (session_start.py:192-193) is unguarded.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    clock = _FailingSleepClock()
    uc = _make_android_uc(process=process, repo=repo, clock=clock)

    with pytest.raises(_SleepBoom) as excinfo:
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=2.0,
        )

    assert excinfo.value is clock.boom
    assert excinfo.value.__cause__ is None
    # Post-state depth: the app was started, then the wait faulted.
    assert ("sleep", 2.0) in clock.calls
    assert any(c[0] == "start" for c in process.calls)
    # REQ-A: app_pid stopped exactly once; nothing persisted.
    assert _android_stop_calls(process) == [("stop", _APP_PID)]
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# RED — TAIL-GAP-android-now  (clock.now raises after sleep; REQ-A)
# ═══════════════════════════════════════════════════════════════════════


def test_tail_gap_android_now_raises_stops_app_pid() -> None:
    """REQ-A: if ``clock.now()`` raises after ``process.start`` (wait=0 so sleep
    is skipped), the acquired ``app_pid`` is stopped before the ORIGINAL
    sentinel propagates unwrapped.

    RED today: the now step (session_start.py:195) is unguarded.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    clock = _FailingNowClock()
    uc = _make_android_uc(process=process, repo=repo, clock=clock)

    with pytest.raises(_NowBoom) as excinfo:
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=0.0,
        )

    assert excinfo.value is clock.boom
    assert excinfo.value.__cause__ is None
    # Post-state depth: now() was reached (the faulting tail step).
    assert ("now", None) in clock.calls
    assert _android_stop_calls(process) == [("stop", _APP_PID)]
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# RED — TAIL-GAP-android-session-construct  (Session __post_init__ raises; REQ-A)
#   Defense-in-depth: not reachable via the current call site (session_id is
#   always a valid uuid4()[:8], backend always the literal "android"), so we
#   monkeypatch validate_session_id to fault the REAL __post_init__.
# ═══════════════════════════════════════════════════════════════════════


def test_tail_gap_android_session_construct_raises_stops_app_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-A: if ``Session(...)`` construction raises in ``__post_init__`` after
    ``process.start``, the acquired ``app_pid`` is stopped before the ORIGINAL
    sentinel propagates unwrapped.

    RED today: the Session-construct step (session_start.py:199-210) is
    unguarded. ``validate_session_id`` is the first call in ``__post_init__``, so
    faulting it faults exactly the construct step of the REAL Session.
    """
    boom = _SessionConstructBoom(
        "session __post_init__ failed (injected AIYES-120 TAIL-GAP-session)"
    )

    def _raise_construct(_session_id: str) -> None:
        raise boom

    monkeypatch.setattr("aiyes.domain.session.validate_session_id", _raise_construct)

    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    uc = _make_android_uc(process=process, repo=repo)

    with pytest.raises(_SessionConstructBoom) as excinfo:
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=0.0,
        )

    assert excinfo.value is boom
    assert excinfo.value.__cause__ is None
    assert _android_stop_calls(process) == [("stop", _APP_PID)]
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# RED — NON-MASKING-android: the ORIGINAL error wins over a raising stop()
#   (REQ-B; DEC-120-02 best-effort per-call swallow)
# ═══════════════════════════════════════════════════════════════════════


def test_non_masking_android_original_error_wins_over_stop_error() -> None:
    """REQ-B: on an error path (clock.sleep raises) where the finally's
    ``process.stop(app_pid)`` ITSELF raises, the ORIGINAL sleep failure must
    propagate — NOT the cleanup failure.

    RED today: no finally exists, so ``stop`` is never attempted — the "stop was
    attempted" assertion fails. After the fix the finally stops ``app_pid``, the
    ``_StopBoom`` is swallowed (DEC-120-02), and the ``_SleepBoom`` wins.
    """
    process = _StopBoomProcess(pid=_APP_PID)  # start ok; stop() raises _StopBoom
    repo = FakeSessionRepository()
    clock = _FailingSleepClock()  # the ORIGINAL failure
    uc = _make_android_uc(process=process, repo=repo, clock=clock)

    with pytest.raises(_SleepBoom) as excinfo:
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=2.0,
        )

    # REQ-B/REQ-C: the ORIGINAL sleep failure wins identity, unwrapped — the
    # cleanup _StopBoom did NOT replace it.
    assert excinfo.value is clock.boom
    assert not isinstance(excinfo.value, _StopBoom)
    assert excinfo.value.__cause__ is None
    # Best-effort: the failing release (stop) was reached and swallowed.
    assert ("stop", _APP_PID) in process.calls
    # Failure-atomic: nothing persisted.
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — EQUIV-android-save-fail  (GREEN now AND after)
#   Closes the ZERO-coverage gap on the android save-fail path; pins the
#   CURRENT behavior BEFORE DEC-120-04 folds the dedicated try/except away.
# ═══════════════════════════════════════════════════════════════════════


def test_equiv_android_save_fail_stops_app_pid_and_reraises() -> None:
    """Equivalence guard (GREEN now): when ``session_repo.save`` raises on the
    android path, TODAY ``app_pid`` is stopped and the original exception
    re-raised unwrapped. The fold (DEC-120-04) must reproduce exactly this.
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository(fail_on_save=True)  # raises RuntimeError("disk full")
    uc = _make_android_uc(process=process, repo=repo)

    with pytest.raises(RuntimeError, match="disk full") as excinfo:
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=_DEVICE_SERIAL,
            wait=0.0,
        )

    # REQ-C: unwrapped (bare re-raise semantics preserved).
    assert excinfo.value.__cause__ is None
    # REQ-A: app_pid stopped exactly once; save was attempted; nothing persisted.
    assert _android_stop_calls(process) == [("stop", _APP_PID)]
    assert any(c[0] == "save" for c in repo.calls)
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — EQUIV-android-device-serial-valueerror  (GREEN now AND after)
#   Pre-start ValueError guard (DEC-120-05) stays OUTSIDE the guarded region:
#   nothing is acquired, so neither process.start NOR process.stop is ever called.
# ═══════════════════════════════════════════════════════════════════════


def test_equiv_android_device_serial_valueerror_never_touches_process() -> None:
    """Equivalence guard (GREEN now): a missing ``device_serial`` raises
    ``ValueError`` BEFORE ``process.start`` — nothing is acquired, so neither
    ``start`` NOR ``stop`` is ever called (hardens DEC-120-05's pre-guard
    boundary; the existing test asserts the raise but NOT this call-absence).
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    uc = _make_android_uc(process=process, repo=repo)

    with pytest.raises(ValueError, match="device-serial"):
        uc.execute(
            app_command=_APP_COMMAND,
            app_args=[],
            backend="android",
            device_serial=None,
            wait=0.0,
        )

    # Nothing acquired -> nothing to release: no start AND no stop.
    assert not any(c[0] == "start" for c in process.calls)
    assert not any(c[0] == "stop" for c in process.calls)
    assert repo.load_all() == []


# ═══════════════════════════════════════════════════════════════════════
# EQUIVALENCE GUARD — SUCCESS-android-unchanged  (GREEN now AND after)
#   DEC-120-03 committed-flag non-firing: the success path returns a valid
#   Session, leaves the adb app running (stop NEVER called), saves exactly once.
# ═══════════════════════════════════════════════════════════════════════


def test_success_android_unchanged_leaves_app_running() -> None:
    """DEC-120-03 back-compat (GREEN now AND after): a normal android launch
    returns a valid Session, calls ``process.start`` exactly once, ``save``
    exactly once, and NEVER stops ``app_pid`` (the committed flag suppresses the
    finally on success — stopping would kill the live adb app process).
    """
    process = FakeProcess(pid=_APP_PID)
    repo = FakeSessionRepository()
    uc = _make_android_uc(process=process, repo=repo)

    session = uc.execute(
        app_command=_APP_COMMAND,
        app_args=[],
        backend="android",
        device_serial=_DEVICE_SERIAL,
        wait=0.0,
    )

    # A valid, persisted android Session is returned.
    assert isinstance(session, Session)
    assert session.backend == "android"
    assert session.device_serial == _DEVICE_SERIAL
    assert session.app_pid == _APP_PID
    assert repo.load(session.session_id) is session

    # process.start called exactly once; app_pid NEVER stopped (committed).
    start_calls = [c for c in process.calls if c[0] == "start"]
    assert len(start_calls) == 1
    assert _android_stop_calls(process) == []
    # save called exactly once.
    save_calls = [c for c in repo.calls if c[0] == "save"]
    assert len(save_calls) == 1
