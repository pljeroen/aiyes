"""AIYES-104: scenario wait-family timeout status policy (RED).

These tests pin the AIYES-104 behavior the implementation (A9) must deliver:

  R1  A wait / wait_stable / wait_reactive step whose semantic output is a
      timeout (or, for wait_reactive, an unmatched terminal failure) is a
      FAILED scenario step by default — not the legacy unconditional "passed".
      The matched/satisfied success path is NOT regressed (FC-WAIT-07), and the
      top-level ScenarioRunResult.failure_code stays "executor_error"
      (FC-FAILCODE-12).
  R2  allow_timeout: true (the SINGLE boolean opt-in, OD-02) keeps a
      timeout/unmatched-terminal output a passed observation; absent or
      allow_timeout: false => default-fail (FC-POLICY-04/05). Load-time
      validation rejects a non-bool allow_timeout with exactly one
      ScenarioValidationIssue code "wait_allow_timeout_invalid" at path
      steps[i].allow_timeout, across all three wait kinds, and accepts
      boolean/absent with zero such issues (FC-VALID-06). Product-facing help
      describes the policy truthfully (FC-DOC-10).
  R3  The original jsonable wait-family output mapping is preserved verbatim,
      key-for-key and value-for-value (None-drop aware, ASM-06), when the step
      is reclassified to failed (FC-PRESERVE-08).
  LE-01  Each default-fail classification emits exactly one
      scenario.diagnostic.failure_classified event through the PRODUCTION
      DiagnosticEventPort adapter (src/aiyes/adapters/diagnostic_log.py::
      InMemoryDiagnosticLog) with contract_id="AIYES-104", step_id,
      failure_code="step_timeout", and a bounded single-line diagnostic_summary
      (<=200). Emission is fail-open: an internal adapter store failure is
      swallowed and increments the ADAPTER-owned emission_failure_count(); a
      passed classification emits none; no injected logger => no emission and no
      crash (FC-LOG-11).

Every test asserts the OBSERVABLE post-state (the result status + output
mapping, the captured event, or the documentation surface), not merely a
return value. The observability tests exercise the PRODUCTION adapter (not a
hand-rolled double) so the concrete sink wired by composition_root is proven.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest

from aiyes.adapters.diagnostic_log import InMemoryDiagnosticLog
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.domain.diagnostic_event import DiagnosticEvent
from aiyes.domain.reactive_wait import ReactiveWaitResult
from aiyes.domain.scenario import (
    ScenarioStep,
    validate_scenario_document,
)
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.domain.use_cases.wait import WaitResult
from aiyes.domain.use_cases.wait_stable import WaitStableResult

# OD-03: diagnostic_summary is a single line truncated at 200 chars.
SUMMARY_MAX_LEN = 200

# AIYES-104 stable step-level failure code carried in the LE-01 event.
_STEP_TIMEOUT_CODE = "step_timeout"

# FC-VALID-06: the single canonical load-time validation code.
_ALLOW_TIMEOUT_INVALID_CODE = "wait_allow_timeout_invalid"


# ─── Fixed-result wait-family doubles ─────────────────────────────────────
#
# Each double ignores the executor's kwargs and returns a fixed domain result
# object, so the jsonable output is exactly the real WaitResult /
# WaitStableResult / ReactiveWaitResult timeout (or success) shape.


@dataclasses.dataclass
class FixedWait:
    """A wait use case returning a fixed WaitResult."""

    result: WaitResult
    calls: List[dict] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> WaitResult:
        self.calls.append(dict(kwargs))
        return self.result


@dataclasses.dataclass
class FixedWaitStable:
    """A wait_stable use case returning a fixed WaitStableResult."""

    result: WaitStableResult
    calls: List[dict] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> WaitStableResult:
        self.calls.append(dict(kwargs))
        return self.result


@dataclasses.dataclass
class FixedReactive:
    """A reactive_wait use case returning a fixed ReactiveWaitResult."""

    result: ReactiveWaitResult
    calls: List[dict] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> ReactiveWaitResult:
        self.calls.append(dict(kwargs))
        return self.result


class FakeSessionRepo:
    """Returns a session with a known backend/resolution."""

    def load(self, session_id: str) -> Any:
        return SimpleNamespace(
            session_id=session_id,
            resolution="1080x2400",
            backend="android",
            device_serial="",
        )


class _RaisingStore(list):
    """Backing store whose append raises — an INTERNAL adapter failure.

    Injected into the PRODUCTION InMemoryDiagnosticLog (replacing its private
    _events list) so the adapter's OWN fail-open path runs. The store NEVER
    touches the adapter's failure counter — the assertion passes only if the
    ADAPTER owns the increment.
    """

    def append(self, _item: Any) -> None:
        raise RuntimeError("backing store down")


# ─── Result builders (real timeout / success shapes) ──────────────────────


def _wait_timeout() -> WaitResult:
    # found=False, timeout=True (wait.py:148). jsonable => {found,timeout,transient}.
    return WaitResult(found=False, timeout=True)


def _wait_success() -> WaitResult:
    return WaitResult(found=True, timeout=False, id="node-1")


def _wait_stable_timeout() -> WaitStableResult:
    return WaitStableResult(
        stable=False,
        timeout=True,
        polls=4,
        changes=({"kind": "added", "id": "x"},),
        comparison_mode="node_id",
    )


def _wait_stable_success() -> WaitStableResult:
    return WaitStableResult(stable=True, timeout=False, polls=3)


def _reactive_timeout() -> ReactiveWaitResult:
    return ReactiveWaitResult(
        condition="screen-change",
        matched=False,
        timeout=True,
        backend="android",
        source="adb_state_poll",
        elapsed_ms=10000,
        polls=5,
        failure_code="timeout",
    )


def _reactive_unmatched_terminal() -> ReactiveWaitResult:
    # matched=False, timeout=False, but a terminal failure_code => unmatched terminal.
    return ReactiveWaitResult(
        condition="node-appears",
        matched=False,
        timeout=False,
        backend="android",
        source="adb_state_poll",
        elapsed_ms=42,
        polls=2,
        failure_code="observer_error",
    )


def _reactive_match() -> ReactiveWaitResult:
    return ReactiveWaitResult.matched_result(
        condition="screen-change",
        backend="android",
        source="native_event",
        elapsed_ms=120,
        polls=1,
        events=(),
    )


# ─── Executor construction ────────────────────────────────────────────────


def _build_executor(
    *,
    wait: Any = None,
    wait_stable: Any = None,
    reactive_wait: Any = None,
    diagnostic_log: Any | None = None,
) -> ScenarioUseCaseExecutor:
    """Build a started executor with the requested wait-family doubles."""
    start = SimpleNamespace(
        execute=lambda **kw: SimpleNamespace(session_id="s1", backend="android")
    )
    kwargs: dict[str, Any] = dict(
        session_start=start,
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace(tree=None)),
        find=SimpleNamespace(execute=lambda **kw: []),
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        wait=wait,
        wait_stable=wait_stable,
        reactive_wait=reactive_wait,
        session_repo=FakeSessionRepo(),
        clock=SimpleNamespace(now=lambda: 0.0),
    )
    if diagnostic_log is not None:
        kwargs["diagnostic_log"] = diagnostic_log
    executor = ScenarioUseCaseExecutor(**kwargs)
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "adb", "wait_seconds": 0.0, "backend": "android"},
        )
    )
    return executor


def _wait_step(
    *, kind: str, params: dict[str, Any], step_id: str = "w"
) -> ScenarioStep:
    return ScenarioStep(id=step_id, kind=kind, parameters=params)


# ─── Validation document builder ──────────────────────────────────────────


def _wait_family_document(
    *,
    kind: str,
    allow_timeout: Any,
    include_allow_timeout: bool = True,
) -> dict:
    """A minimal valid scenario document with one wait-family step.

    The wait-family step optionally carries an `allow_timeout` key so the
    load-time validation (FC-VALID-06) can be exercised end to end. The wait
    step is at steps[1].
    """
    step: dict[str, Any] = {"id": "w", "kind": kind}
    if kind == "wait":
        step["role"] = "View"
        step["name_pattern"] = "Login"
    elif kind == "wait_reactive":
        step["condition"] = "screen-change"
    # wait_stable needs no required fields.
    if include_allow_timeout:
        step["allow_timeout"] = allow_timeout
    return {
        "schema_version": 1,
        "id": "scn",
        "title": "wait timeout policy scenario",
        "target": "android",
        "prerequisites": [],
        "steps": [
            {
                "id": "start",
                "kind": "start_session",
                "command": "adb",
                "backend": "android",
            },
            step,
            {"id": "stop", "kind": "stop_session"},
        ],
        "cleanup": [],
        "evidence_policy": {},
    }


# =====================================================================
# R1 — default-fail on timeout / unmatched terminal (FC-WAIT-01/02/03)
# =====================================================================


def test_wait_timeout_is_failed_by_default() -> None:
    """FC-WAIT-01: a wait step that times out classifies as failed (not passed).

    Grounded against the real WaitResult timeout shape
    (found=False, timeout=True => output {found,timeout,transient}).
    """
    executor = _build_executor(wait=FixedWait(_wait_timeout()))

    result = executor.execute(
        _wait_step(kind="wait", params={"role": "View", "name_pattern": "Login"})
    )

    assert result.status == "failed"
    # Observable post-state: the timeout output is still surfaced on the step.
    assert result.output["timeout"] is True
    assert result.output["found"] is False


def test_wait_stable_timeout_is_failed_by_default() -> None:
    """FC-WAIT-02: a wait_stable step that times out classifies as failed."""
    executor = _build_executor(wait_stable=FixedWaitStable(_wait_stable_timeout()))

    result = executor.execute(_wait_step(kind="wait_stable", params={}))

    assert result.status == "failed"
    assert result.output["timeout"] is True
    assert result.output["stable"] is False


def test_wait_reactive_timeout_is_failed_by_default() -> None:
    """FC-WAIT-03: a wait_reactive step with timeout=True classifies as failed."""
    executor = _build_executor(reactive_wait=FixedReactive(_reactive_timeout()))

    result = executor.execute(
        _wait_step(kind="wait_reactive", params={"condition": "screen-change"})
    )

    assert result.status == "failed"
    assert result.output["timeout"] is True
    assert result.output["matched"] is False


def test_wait_reactive_unmatched_terminal_failure_code_is_failed() -> None:
    """FC-WAIT-03: matched=False + terminal failure_code (no timeout) => failed.

    An unmatched terminal outcome is timeout=True OR (matched=False AND a
    terminal failure_code). This case has timeout=False but failure_code in the
    closed terminal set, so it MUST still fail by default.
    """
    executor = _build_executor(
        reactive_wait=FixedReactive(_reactive_unmatched_terminal())
    )

    result = executor.execute(
        _wait_step(kind="wait_reactive", params={"condition": "node-appears"})
    )

    assert result.status == "failed"
    assert result.output["matched"] is False
    assert result.output["timeout"] is False
    assert result.output["failure_code"] == "observer_error"


# =====================================================================
# R1 success guard — matched/satisfied stays passed (FC-WAIT-07)
# =====================================================================


def test_wait_success_stays_passed() -> None:
    """FC-WAIT-07: a wait that finds its node (no timeout) stays passed."""
    executor = _build_executor(wait=FixedWait(_wait_success()))

    result = executor.execute(
        _wait_step(kind="wait", params={"role": "View", "name_pattern": "Login"})
    )

    assert result.status == "passed"
    assert result.output["found"] is True
    assert result.output["timeout"] is False


def test_wait_stable_success_stays_passed() -> None:
    """FC-WAIT-07: a wait_stable that stabilizes stays passed."""
    executor = _build_executor(wait_stable=FixedWaitStable(_wait_stable_success()))

    result = executor.execute(_wait_step(kind="wait_stable", params={}))

    assert result.status == "passed"
    assert result.output["stable"] is True
    assert result.output["timeout"] is False


def test_wait_reactive_match_stays_passed() -> None:
    """FC-WAIT-07: a matched reactive wait stays passed."""
    executor = _build_executor(reactive_wait=FixedReactive(_reactive_match()))

    result = executor.execute(
        _wait_step(kind="wait_reactive", params={"condition": "screen-change"})
    )

    assert result.status == "passed"
    assert result.output["matched"] is True
    assert result.output["timeout"] is False


# =====================================================================
# R2 — allow_timeout opt-in (FC-POLICY-04/05)
# =====================================================================


def test_allow_timeout_true_keeps_wait_timeout_passed() -> None:
    """FC-POLICY-05: allow_timeout=true keeps a wait timeout a passed observation."""
    executor = _build_executor(wait=FixedWait(_wait_timeout()))

    result = executor.execute(
        _wait_step(
            kind="wait",
            params={"role": "View", "name_pattern": "Login", "allow_timeout": True},
        )
    )

    assert result.status == "passed"
    assert result.output["timeout"] is True


def test_allow_timeout_true_keeps_wait_stable_timeout_passed() -> None:
    """FC-POLICY-05: allow_timeout=true keeps a wait_stable timeout passed."""
    executor = _build_executor(wait_stable=FixedWaitStable(_wait_stable_timeout()))

    result = executor.execute(
        _wait_step(kind="wait_stable", params={"allow_timeout": True})
    )

    assert result.status == "passed"
    assert result.output["timeout"] is True


def test_allow_timeout_true_keeps_reactive_unmatched_terminal_passed() -> None:
    """FC-POLICY-05: allow_timeout=true keeps a reactive unmatched-terminal passed."""
    executor = _build_executor(
        reactive_wait=FixedReactive(_reactive_unmatched_terminal())
    )

    result = executor.execute(
        _wait_step(
            kind="wait_reactive",
            params={"condition": "node-appears", "allow_timeout": True},
        )
    )

    assert result.status == "passed"
    assert result.output["matched"] is False


def test_allow_timeout_false_is_default_fail() -> None:
    """FC-POLICY-04: allow_timeout=false is the default — timeout fails."""
    executor = _build_executor(wait=FixedWait(_wait_timeout()))

    result = executor.execute(
        _wait_step(
            kind="wait",
            params={"role": "View", "name_pattern": "Login", "allow_timeout": False},
        )
    )

    assert result.status == "failed"


def test_allow_timeout_absent_is_default_fail() -> None:
    """FC-POLICY-04: absent allow_timeout is the default — timeout fails."""
    executor = _build_executor(wait=FixedWait(_wait_timeout()))

    result = executor.execute(
        _wait_step(kind="wait", params={"role": "View", "name_pattern": "Login"})
    )

    assert result.status == "failed"


def test_allow_timeout_truthy_non_true_is_default_fail() -> None:
    """FC-POLICY-04: only the literal True opts in; a truthy non-True value
    (e.g. integer 1) is NOT the opt-in and the step fails by default.

    Grounds the `is True` (not mere truthiness) precision in the predicate.
    """
    executor = _build_executor(wait=FixedWait(_wait_timeout()))

    result = executor.execute(
        _wait_step(
            kind="wait",
            params={"role": "View", "name_pattern": "Login", "allow_timeout": 1},
        )
    )

    assert result.status == "failed"


# =====================================================================
# R2 — load-time validation (FC-VALID-06)
# =====================================================================


@pytest.mark.parametrize("kind", ["wait", "wait_stable", "wait_reactive"])
@pytest.mark.parametrize("value", [True, False])
def test_allow_timeout_boolean_validates_and_is_recoverable(
    kind: str, value: bool
) -> None:
    """FC-VALID-06: boolean allow_timeout => ok, recoverable on parameters,
    zero wait_allow_timeout_invalid issues, for every wait kind."""
    document = _wait_family_document(kind=kind, allow_timeout=value)

    result = validate_scenario_document(document)

    assert result.ok, [dataclasses.asdict(i) for i in result.issues]
    step = next(s for s in result.scenario.steps if s.kind == kind)
    assert step.parameters["allow_timeout"] == value
    assert not [i for i in result.issues if i.code == _ALLOW_TIMEOUT_INVALID_CODE]


@pytest.mark.parametrize("kind", ["wait", "wait_stable", "wait_reactive"])
def test_allow_timeout_absent_validates_with_no_policy_issue(kind: str) -> None:
    """FC-VALID-06: absent allow_timeout => ok, key not on parameters, zero
    wait_allow_timeout_invalid issues."""
    document = _wait_family_document(
        kind=kind, allow_timeout=None, include_allow_timeout=False
    )

    result = validate_scenario_document(document)

    assert result.ok, [dataclasses.asdict(i) for i in result.issues]
    step = next(s for s in result.scenario.steps if s.kind == kind)
    assert "allow_timeout" not in step.parameters
    assert not [i for i in result.issues if i.code == _ALLOW_TIMEOUT_INVALID_CODE]


@pytest.mark.parametrize("kind", ["wait", "wait_stable", "wait_reactive"])
@pytest.mark.parametrize("bad", ["true", "yes", 1, 0, 1.0, ["x"], {"v": True}])
def test_allow_timeout_non_bool_rejected_with_stable_code_and_path(
    kind: str, bad: Any
) -> None:
    """FC-VALID-06: non-bool allow_timeout => rejected, scenario None, exactly
    one ScenarioValidationIssue with code wait_allow_timeout_invalid at path
    steps[1].allow_timeout, for every wait kind."""
    document = _wait_family_document(kind=kind, allow_timeout=bad)

    result = validate_scenario_document(document)

    assert result.ok is False
    assert result.scenario is None
    matching = [i for i in result.issues if i.code == _ALLOW_TIMEOUT_INVALID_CODE]
    assert len(matching) == 1, [dataclasses.asdict(i) for i in result.issues]
    # wait-family step is at steps[1] in _wait_family_document.
    assert matching[0].path == "steps[1].allow_timeout"


# =====================================================================
# R3 — output preservation (FC-PRESERVE-08, ASM-06 key-presence equality)
# =====================================================================


def test_failed_wait_preserves_output_mapping_verbatim() -> None:
    """FC-PRESERVE-08: reclassifying a wait timeout to failed preserves the
    original jsonable output mapping key-for-key and value-for-value.

    Reference is the exact jsonable shape produced by the same result object.
    """
    from aiyes.adapters.scenario_use_case_executor import _jsonable_dict

    result_obj = _wait_timeout()
    reference = _jsonable_dict(result_obj)

    executor = _build_executor(wait=FixedWait(result_obj))
    result = executor.execute(
        _wait_step(kind="wait", params={"role": "View", "name_pattern": "Login"})
    )

    assert result.status == "failed"
    assert dict(result.output) == reference
    # ASM-06 key-presence equality (no key added or removed).
    assert set(result.output.keys()) == set(reference.keys())


def test_failed_wait_stable_preserves_output_mapping_verbatim() -> None:
    """FC-PRESERVE-08: a failed wait_stable preserves stable/timeout/polls/
    changes/comparison_mode verbatim."""
    from aiyes.adapters.scenario_use_case_executor import _jsonable_dict

    result_obj = _wait_stable_timeout()
    reference = _jsonable_dict(result_obj)

    executor = _build_executor(wait_stable=FixedWaitStable(result_obj))
    result = executor.execute(_wait_step(kind="wait_stable", params={}))

    assert result.status == "failed"
    assert dict(result.output) == reference
    assert set(result.output.keys()) == set(reference.keys())


def test_failed_wait_reactive_preserves_output_mapping_verbatim() -> None:
    """FC-PRESERVE-08: a failed wait_reactive preserves matched/timeout/
    failure_code/polls/events/... verbatim (None-drop aware)."""
    from aiyes.adapters.scenario_use_case_executor import _jsonable_dict

    result_obj = _reactive_timeout()
    reference = _jsonable_dict(result_obj)

    executor = _build_executor(reactive_wait=FixedReactive(result_obj))
    result = executor.execute(
        _wait_step(kind="wait_reactive", params={"condition": "screen-change"})
    )

    assert result.status == "failed"
    assert dict(result.output) == reference
    assert set(result.output.keys()) == set(reference.keys())
    # failure_code key present and equal (it was non-None on the source result).
    assert result.output["failure_code"] == "timeout"


# =====================================================================
# R1 — top-level run failure_code pinned (FC-FAILCODE-12)
# =====================================================================


def _wait_timeout_run() -> Any:
    """Run a scenario whose only failing step is a wait timeout (no opt-in).

    Returns the completed ScenarioRunResult; the failing step id is "w".
    Grounded against the real ScenarioRunUseCase + executor so the classified
    per-step output flows through run aggregation exactly as in production.
    """
    executor = _build_executor(wait=FixedWait(_wait_timeout()))
    document = {
        "schema_version": 1,
        "id": "scn",
        "title": "wait timeout run",
        "target": "android",
        "prerequisites": [],
        "steps": [
            {
                "id": "start",
                "kind": "start_session",
                "command": "adb",
                "backend": "android",
            },
            {"id": "w", "kind": "wait", "role": "View", "name_pattern": "Login"},
            {"id": "stop", "kind": "stop_session"},
        ],
        "cleanup": [],
        "evidence_policy": {},
    }
    validated = validate_scenario_document(document)
    assert validated.ok, [dataclasses.asdict(i) for i in validated.issues]
    return ScenarioRunUseCase(executor=executor).execute(validated.scenario)


def test_top_level_failure_code_is_step_timeout() -> None:
    """AIYES-108 FC-FAILCODE-13: a failed wait-timeout step yields
    run.status==failed and the top-level failure_code "step_timeout".

    SUPERSEDES AIYES-104 FC-FAILCODE-12 (authorized, SUP-108-1): the prior pin
    asserted run.failure_code == "executor_error" for a wait-timeout run; that pin
    is explicitly lifted by AIYES-108, which promotes the step-level step_timeout
    classification to the top-level failure_code so evidence consumers reading
    only the top-level code can distinguish a wait-timeout run from a generic
    executor failure. The step-level error remains "step_timeout" (unchanged)."""
    run = _wait_timeout_run()

    assert run.status == "failed"
    assert run.failure_code == _STEP_TIMEOUT_CODE
    failing = next(s for s in run.steps if s.step_id == "w")
    assert failing.status == "failed"
    # The step-level step_timeout code IS now promoted to the run failure_code.
    assert run.failure_code != "executor_error"


# =====================================================================
# LE-01 — diagnostic emission through the PRODUCTION adapter (FC-LOG-11)
# =====================================================================


def test_production_adapter_emits_exactly_one_le01_on_wait_timeout() -> None:
    """FC-LOG-11: the PRODUCTION InMemoryDiagnosticLog records exactly one
    scenario.diagnostic.failure_classified event with the LE-01 payload when a
    wait timeout is classified failed: contract_id=AIYES-104, step_id,
    failure_code=step_timeout, bounded single-line diagnostic_summary."""
    log = InMemoryDiagnosticLog()
    executor = _build_executor(wait=FixedWait(_wait_timeout()), diagnostic_log=log)

    result = executor.execute(
        _wait_step(
            kind="wait",
            params={"role": "View", "name_pattern": "Login"},
            step_id="w_timeout",
        )
    )

    assert result.status == "failed"
    assert len(log.events) == 1
    event = log.events[0]
    assert isinstance(event, DiagnosticEvent)
    assert event.action == "scenario.diagnostic.failure_classified"
    assert event.contract_id == "AIYES-104"
    assert event.step_id == "w_timeout"
    assert event.failure_code == _STEP_TIMEOUT_CODE
    summary = event.diagnostic_summary
    assert isinstance(summary, str)
    if summary:
        assert summary == summary.splitlines()[0][:SUMMARY_MAX_LEN]
    assert len(summary) <= SUMMARY_MAX_LEN


def test_production_adapter_emits_le01_for_each_wait_kind() -> None:
    """FC-LOG-11: each wait-family kind's default-fail classification emits one
    LE-01 step_timeout event via the production adapter."""
    cases = [
        ("wait", FixedWait(_wait_timeout()), {"role": "View", "name_pattern": "L"}),
        ("wait_stable", FixedWaitStable(_wait_stable_timeout()), {}),
        (
            "wait_reactive",
            FixedReactive(_reactive_timeout()),
            {"condition": "screen-change"},
        ),
    ]
    for kind, double, params in cases:
        log = InMemoryDiagnosticLog()
        kwargs: dict[str, Any] = {"diagnostic_log": log}
        if kind == "wait":
            kwargs["wait"] = double
        elif kind == "wait_stable":
            kwargs["wait_stable"] = double
        else:
            kwargs["reactive_wait"] = double
        executor = _build_executor(**kwargs)

        result = executor.execute(
            _wait_step(kind=kind, params=params, step_id=f"{kind}_step")
        )

        assert result.status == "failed", kind
        assert len(log.events) == 1, kind
        assert log.events[0].failure_code == _STEP_TIMEOUT_CODE, kind
        assert log.events[0].contract_id == "AIYES-104", kind
        assert log.events[0].step_id == f"{kind}_step", kind


def test_passed_wait_emits_no_le01_event() -> None:
    """FC-LOG-11: a passed classification (success, or allow_timeout opt-in)
    emits NO LE-01 failure event."""
    # Success path.
    log_success = InMemoryDiagnosticLog()
    executor = _build_executor(
        wait=FixedWait(_wait_success()), diagnostic_log=log_success
    )
    success = executor.execute(
        _wait_step(kind="wait", params={"role": "View", "name_pattern": "L"})
    )
    assert success.status == "passed"
    assert log_success.events == []

    # allow_timeout opt-in path (timeout, but observational => passed).
    log_optin = InMemoryDiagnosticLog()
    executor2 = _build_executor(
        wait=FixedWait(_wait_timeout()), diagnostic_log=log_optin
    )
    optin = executor2.execute(
        _wait_step(
            kind="wait",
            params={"role": "View", "name_pattern": "L", "allow_timeout": True},
        )
    )
    assert optin.status == "passed"
    assert log_optin.events == []


def test_production_adapter_fail_open_counts_internal_failure_through_executor() -> (
    None
):
    """FC-LOG-11: the PRODUCTION adapter OWNS the fail-open count. An INTERNAL
    storage failure (the adapter's backing store append raises) is swallowed by
    the adapter — the classification outcome is unchanged AND the adapter's
    emission_failure_count increments by exactly one.

    The store NEVER touches the counter, so this passes only if the adapter
    increments its own count (not the executor)."""
    log = InMemoryDiagnosticLog()
    log._events = _RaisingStore()  # noqa: SLF001 — exercise the adapter fail-open path
    assert log.emission_failure_count() == 0

    executor = _build_executor(wait=FixedWait(_wait_timeout()), diagnostic_log=log)

    # Reference outcome with a healthy production adapter.
    reference = _build_executor(
        wait=FixedWait(_wait_timeout()), diagnostic_log=InMemoryDiagnosticLog()
    ).execute(_wait_step(kind="wait", params={"role": "View", "name_pattern": "L"}))

    result = executor.execute(
        _wait_step(kind="wait", params={"role": "View", "name_pattern": "L"})
    )

    # Same status and output as the healthy sink; no exception propagated.
    assert result.status == reference.status == "failed"
    assert dict(result.output) == dict(reference.output)
    # The ADAPTER owned the increment on its swallowed internal failure.
    assert log.emission_failure_count() == 1


def test_no_logger_injected_means_no_emission_and_no_crash() -> None:
    """FC-LOG-11 wiring: when no logger is injected (None), classification still
    happens, no emission occurs, and nothing crashes."""
    executor = _build_executor(wait=FixedWait(_wait_timeout()), diagnostic_log=None)

    result = executor.execute(
        _wait_step(kind="wait", params={"role": "View", "name_pattern": "L"})
    )

    assert result.status == "failed"
    assert result.output["timeout"] is True


# =====================================================================
# R2 — truthful product-facing help / docs (FC-DOC-10)
# =====================================================================

_DOC_PATH = Path("docs/release-scenarios.md")


def test_docs_describe_wait_timeout_default_failure() -> None:
    """FC-DOC-10: docs assert wait-family timeout => default scenario-step
    failure, and document the allow_timeout observational opt-in, without
    implying timeout passes by default."""
    content = _DOC_PATH.read_text(encoding="utf-8")
    lowered = content.lower()

    # Names the policy field.
    assert "allow_timeout" in content
    # States that a timeout fails the scenario step by default.
    assert "timeout" in lowered
    assert "fail" in lowered
    # Truthfulness guard: must NOT claim timeout passes / is success by default.
    assert "timeout passes by default" not in lowered
    assert "timeout is success" not in lowered


def test_docs_do_not_describe_direct_cli_wait_exit_as_changed() -> None:
    """FC-DOC-10: the scenario timeout policy must not be described as changing
    the direct CLI/MCP wait exit semantics (timeout is still exit 0 there)."""
    content = _DOC_PATH.read_text(encoding="utf-8")
    lowered = content.lower()

    # No statement that the direct CLI wait command now exits non-zero on timeout.
    assert "wait command now fails" not in lowered
    assert "wait now exits non-zero" not in lowered
