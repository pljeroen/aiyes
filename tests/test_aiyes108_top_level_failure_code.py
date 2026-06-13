"""AIYES-108: top-level scenario failure_code surfaces wait-timeout (RED).

These tests pin the AIYES-108 behavior the implementation (A9) must deliver:
the wait-family step-level "step_timeout" classification (set on
ScenarioStepExecutionResult.error by the executor per AIYES-104) is PROMOTED to
the TOP-level ScenarioRunResult.failure_code, so an evidence consumer reading
ONLY the top-level code can distinguish a wait-timeout run from a generic
executor failure.

The top-level failure_code is determined by the single total precedence
PREC-108-1 over (status, kind, error), first matching clause wins:

  rank 1  status == "skipped"                                  => prerequisite_missing
  rank 2  status != "skipped" AND kind == "assert"             => assertion_failed
  rank 3  status != "skipped" AND kind != "assert"
                                AND error == "step_timeout"     => step_timeout   (NEW)
  rank 4  status != "skipped" AND kind != "assert"
                                AND error != "step_timeout"     => executor_error

Test strategy (A8 lesson — test the PRODUCTION code path, not throwaway logic):
the failure_code-mapping tests drive the REAL ScenarioRunUseCase.execute with an
injected ScenarioStepExecutorPort double that returns a chosen
ScenarioStepExecutionResult. The double stands in ONLY at the port boundary
(legitimate); the assertions are against the real ScenarioRunUseCase mapping
(_failure_code + _next_actions_for_failure) and its observable post-state
(ScenarioRunResult.failure_code + next_actions). The FC-16 producer-scan test
reads the REAL adapter source. The domain-purity test scans the REAL domain
module source.

Every test asserts OBSERVABLE post-state (the run-level failure_code and
next_actions, or the source-level reserved-marker / import facts), never a bare
return value.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping

import pytest

from aiyes.domain.scenario import ReleaseScenario, ScenarioStep
from aiyes.domain.use_cases.scenario_run import (
    ScenarioRunResult,
    ScenarioRunUseCase,
)
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult

# AIYES-104 reserved step-level wait-family timeout marker, promoted to the
# top-level failure_code by AIYES-108.
_STEP_TIMEOUT_CODE = "step_timeout"

# Real source files under verification (absolute resolution from repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXECUTOR_SRC = (
    _REPO_ROOT / "src" / "aiyes" / "adapters" / "scenario_use_case_executor.py"
)
_DOMAIN_SRC = _REPO_ROOT / "src" / "aiyes" / "domain" / "use_cases" / "scenario_run.py"


# ─── Port double: a single-step executor returning a fixed result ──────────
#
# Stands in at the ScenarioStepExecutorPort boundary so the REAL
# ScenarioRunUseCase mapping runs over a controlled per-step result. It returns
# the supplied ScenarioStepExecutionResult for the run's single non-cleanup
# step; the step is non-session so no cleanup runs.


class _FixedResultExecutor:
    """A ScenarioStepExecutorPort double returning a fixed step result."""

    def __init__(self, result: ScenarioStepExecutionResult) -> None:
        self._result = result
        self.calls: list[ScenarioStep] = []

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        self.calls.append(step)
        return self._result


def _single_step_scenario(*, kind: str, step_id: str = "s1") -> ReleaseScenario:
    """A minimal scenario with exactly one non-session step of the given kind.

    No start_session/session step, so no cleanup is triggered on failure and the
    terminating step is the one the executor double classifies.
    """
    return ReleaseScenario(
        schema_version=1,
        id="scn",
        title="aiyes-108 failure_code mapping",
        target="android",
        prerequisites=(),
        steps=(ScenarioStep(id=step_id, kind=kind, parameters={}),),
        cleanup=(),
        evidence_policy={},
    )


def _run_with_step_result(
    *,
    kind: str,
    status: str,
    error: str,
    output: Mapping[str, Any] | None = None,
) -> ScenarioRunResult:
    """Drive the REAL ScenarioRunUseCase over one step result via the port double.

    evaluate_assertions=False routes an assert-kind step through the injected
    executor too, so an assert step can carry a chosen error == "step_timeout".
    """
    result = ScenarioStepExecutionResult(
        step_id="s1",
        status=status,
        output=dict(output or {"timeout": True}),
        error=error,
    )
    executor = _FixedResultExecutor(result)
    use_case = ScenarioRunUseCase(executor=executor, evaluate_assertions=False)
    return use_case.execute(_single_step_scenario(kind=kind))


# =====================================================================
# FC-FAILCODE-13 / R1 — promote wait-timeout to top-level failure_code
# =====================================================================


def test_wait_timeout_run_yields_step_timeout_failure_code() -> None:
    """FC-FAILCODE-13 (R1, must_tier1 R1 row): a non-skipped, non-assert failed
    step whose executed.error == "step_timeout" promotes the TOP-level
    ScenarioRunResult.failure_code to "step_timeout".

    RED: _failure_code does not yet thread executed.error, so the run currently
    classifies as "executor_error".
    """
    run = _run_with_step_result(kind="wait", status="failed", error=_STEP_TIMEOUT_CODE)

    assert run.status == "failed"
    assert run.failure_code == _STEP_TIMEOUT_CODE
    assert run.failure_code != "executor_error"


def test_assert_step_with_step_timeout_error_yields_assertion_failed() -> None:
    """PREC-108-1 / SV-01 edge: an ASSERT step that fails carrying
    error == "step_timeout" (reachable when evaluate_assertions=False routes the
    assert through the injected executor) resolves to "assertion_failed" — assert
    precedence (rank 2) outranks the step_timeout clause (rank 3), so it is NOT
    promoted to "step_timeout".

    This input has exactly one expected result. It is GREEN today (assert already
    maps to assertion_failed) and MUST stay GREEN once rank 3 is added — it guards
    the precedence edge against over-promotion.
    """
    run = _run_with_step_result(
        kind="assert", status="failed", error=_STEP_TIMEOUT_CODE
    )

    assert run.status == "failed"
    assert run.failure_code == "assertion_failed"
    assert run.failure_code != _STEP_TIMEOUT_CODE


# =====================================================================
# FC-FAILCODE-14 / R2 — non-timeout failures unchanged (regression)
# =====================================================================


def test_non_timeout_executor_failure_yields_executor_error() -> None:
    """FC-FAILCODE-14 (R2, must_tier1 R2 row): a non-skipped, non-assert failed
    step whose executed.error is NOT "step_timeout" keeps failure_code ==
    "executor_error".

    GREEN today (executor_error is the current residual return) and MUST stay
    GREEN — guards against the rank-3 guard firing on non-timeout errors.
    """
    run = _run_with_step_result(
        kind="action", status="failed", error="source step did not provide a node id"
    )

    assert run.status == "failed"
    assert run.failure_code == "executor_error"


def test_non_wait_executor_exception_stays_executor_error() -> None:
    """FC-FAILCODE-14 DISCRIMINATING (SV-01 falsifiable, no over-broadening): a
    non-skipped, non-assert failed step whose executed.error is the GENERIC
    str(exc) of an ordinary executor exception (error == "boom") yields
    failure_code == "executor_error", NOT "step_timeout".

    This proves the rank-3 promotion does not over-broaden on any real path: only
    the exact reserved literal "step_timeout" promotes. GREEN today and MUST stay
    GREEN once rank 3 is added (the guard is an exact-string match).
    """
    run = _run_with_step_result(kind="action", status="failed", error="boom")

    assert run.status == "failed"
    assert run.failure_code == "executor_error"
    assert run.failure_code != _STEP_TIMEOUT_CODE


def test_prerequisite_skip_mapping_unchanged() -> None:
    """FC-FAILCODE-14 (R2): a skipped step (rank 1) yields "prerequisite_missing"
    regardless of error value, including error == "step_timeout" (rank 1 outranks
    rank 3). GREEN today and MUST stay GREEN.
    """
    run = _run_with_step_result(kind="wait", status="skipped", error=_STEP_TIMEOUT_CODE)

    assert run.status == "skipped"
    assert run.failure_code == "prerequisite_missing"
    assert run.failure_code != _STEP_TIMEOUT_CODE


# =====================================================================
# FC-FAILCODE-15 / R1 — domain purity (no adapter import)
# =====================================================================


def test_failure_code_domain_purity_no_adapter_import() -> None:
    """FC-FAILCODE-15 (R1, STATIC): the domain module scenario_run.py imports no
    symbol from src/aiyes/adapters/ — in particular neither the adapter constant
    _FC_STEP_TIMEOUT nor the adapter-private kind set _WAIT_FAMILY_KINDS. The
    timeout recognition must be a value-match on the port-typed result field, not
    an adapter import.

    GREEN today (no adapter import exists) and MUST stay GREEN after A9: the
    promotion is a string value-match, never an adapter import.
    """
    tree = ast.parse(_DOMAIN_SRC.read_text(encoding="utf-8"))

    imported_modules: list[str] = []
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_modules.append(module)
            imported_names.extend(alias.name for alias in node.names)

    adapter_imports = [m for m in imported_modules if "aiyes.adapters" in m]
    assert adapter_imports == [], (
        f"domain scenario_run.py must not import from aiyes.adapters; "
        f"found: {adapter_imports}"
    )
    assert "_FC_STEP_TIMEOUT" not in imported_names
    assert "_WAIT_FAMILY_KINDS" not in imported_names


# =====================================================================
# FC-FAILCODE-16 / R1 — producer reserved-marker invariant (STATIC)
# =====================================================================


def test_step_timeout_is_reserved_in_executor_producer() -> None:
    """FC-FAILCODE-16 (R1, STATIC, PRODUCER-SCOPED): scan ONLY the producer file
    src/aiyes/adapters/scenario_use_case_executor.py and assert:

      (a) the "step_timeout" literal occurs there only as the _FC_STEP_TIMEOUT
          constant definition plus its wait-family-timeout assignment to
          executed.error (no other producer-side occurrence); and
      (b) no raised exception message in that file equals "step_timeout"
          (every raise carries a different message; the generic exception path
          sets error = str(exc), which is never the reserved literal).

    Scoped to the producer file ONLY — it asserts nothing about occurrences
    outside that file (the domain value-match in scenario_run.py is expected and
    is neither counted nor forbidden). This locks the producer-side guarantee the
    domain value-match trusts. Likely GREEN already; it is a static invariant
    guard, not a new-behavior driver.
    """
    source = _EXECUTOR_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # (a) Collect every "step_timeout" string-literal occurrence and classify it.
    step_timeout_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == _STEP_TIMEOUT_CODE
    ]
    # The literal must occur at least once (the constant definition) and every
    # occurrence must be a bare string Constant — never inside a Raise message.
    assert step_timeout_nodes, (
        'the producer must define the "step_timeout" reserved marker'
    )

    # Identify the line of the _FC_STEP_TIMEOUT = "step_timeout" assignment.
    constant_def_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            if any(t.id == "_FC_STEP_TIMEOUT" for t in targets) and (
                isinstance(node.value, ast.Constant)
                and node.value.value == _STEP_TIMEOUT_CODE
            ):
                constant_def_lines.add(node.value.lineno)

    assert constant_def_lines, (
        'the producer must define _FC_STEP_TIMEOUT = "step_timeout"'
    )
    # Every other "step_timeout" string literal (if any) is permitted only as a
    # plain value occurrence; in this producer the only other occurrences are the
    # _FC_STEP_TIMEOUT NAME references on the wait-family return path (NOT string
    # literals), so the literal itself appears solely at the constant definition.
    other_literal_lines = {
        n.lineno for n in step_timeout_nodes if n.lineno not in constant_def_lines
    }
    assert other_literal_lines == set(), (
        f'the "step_timeout" literal must occur in the producer only as the '
        f"_FC_STEP_TIMEOUT definition; stray literal lines: {sorted(other_literal_lines)}"
    )

    # (b) No raised exception carries the message "step_timeout".
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            for sub in ast.walk(node.exc):
                if (
                    isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)
                    and sub.value == _STEP_TIMEOUT_CODE
                ):
                    pytest.fail(
                        "no raised exception in the producer may use the reserved "
                        f'message "step_timeout" (line {sub.lineno})'
                    )


# =====================================================================
# FC-FAILCODE-17 / R1 — pinned generic next_action for step_timeout
# =====================================================================


def test_step_timeout_run_next_action_is_inspect_error() -> None:
    """FC-FAILCODE-17 (R1): a run with top-level failure_code == "step_timeout"
    yields the GENERIC next_action (code == "inspect_error") and NO
    timeout-specific code — locking the deliberate anti-extraction decision
    (RSK-108-4) as tested behavior.

    RED: until the failure_code is promoted to "step_timeout", the run reports
    "executor_error" so this assertion's premise (failure_code == "step_timeout")
    is not yet reached; the assertion fails on the failure_code precondition.
    """
    run = _run_with_step_result(kind="wait", status="failed", error=_STEP_TIMEOUT_CODE)

    assert run.failure_code == _STEP_TIMEOUT_CODE
    assert run.next_actions, "a failing run must carry at least one next_action"
    assert run.next_actions[0].code == "inspect_error"
    timeout_specific = {"wait_longer", "increase_timeout"}
    assert not any(action.code in timeout_specific for action in run.next_actions), (
        "no timeout-specific next_action may be added (anti-extraction)"
    )
