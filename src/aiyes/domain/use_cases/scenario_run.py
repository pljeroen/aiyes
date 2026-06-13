"""Deterministic release scenario runner use case."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Tuple

from aiyes.domain.scenario import ReleaseScenario
from aiyes.domain.scenario_assertions import evaluate_scenario_assertion
from aiyes.ports.scenario_executor import (
    ScenarioStepExecutionResult,
    ScenarioStepExecutorPort,
)
from aiyes.ports.scenario_prerequisites import (
    ScenarioPrerequisiteCheckerPort,
    ScenarioPrerequisiteResult,
)


@dataclasses.dataclass(frozen=True)
class ScenarioNextAction:
    """Deterministic remediation hint for agents."""

    code: str
    message: str
    command_hint: str = ""


@dataclasses.dataclass(frozen=True)
class ScenarioRunStepResult:
    """Recorded result for one scenario step."""

    step_id: str
    kind: str
    status: str
    output: dict
    error: str = ""
    cleanup: bool = False


@dataclasses.dataclass(frozen=True)
class ScenarioRunResult:
    """Result of running a scenario."""

    scenario_id: str
    status: str
    steps: Tuple[ScenarioRunStepResult, ...]
    mode: str = "dry_run"
    failure_code: str = ""
    next_actions: Tuple[ScenarioNextAction, ...] = ()


class ScenarioRunUseCase:
    """Run validated scenario steps through an injected executor port."""

    def __init__(
        self,
        executor: ScenarioStepExecutorPort,
        prerequisite_checker: ScenarioPrerequisiteCheckerPort | None = None,
        evaluate_assertions: bool = True,
        mode: str = "dry_run",
    ) -> None:
        self._executor = executor
        self._prerequisite_checker = prerequisite_checker
        self._evaluate_assertions = evaluate_assertions
        self._mode = mode

    def execute(self, scenario: ReleaseScenario) -> ScenarioRunResult:
        """Execute declared steps in order, then cleanup any started session."""
        prerequisite_skip = self._first_prerequisite_skip(scenario)
        if prerequisite_skip is not None:
            return ScenarioRunResult(
                scenario_id=scenario.id,
                status="skipped",
                steps=(_record_prerequisite_skip(prerequisite_skip),),
                mode=self._mode,
                failure_code="prerequisite_missing",
                next_actions=(
                    ScenarioNextAction(
                        code="install_prerequisite",
                        message=prerequisite_skip.reason,
                    ),
                ),
            )

        results: list[ScenarioRunStepResult] = []
        context: dict[str, Any] = {}
        status = "passed"
        session_started = False

        for step in scenario.steps:
            if step.kind == "assert" and self._evaluate_assertions:
                executed = _evaluate_assertion_step(step.id, step.parameters, context)
            else:
                executed = self._executor.execute(step)
            results.append(_record_step(step.kind, executed, cleanup=False))
            context[step.id] = dict(executed.output)
            if executed.session_id or step.kind == "start_session":
                session_started = True
            if executed.status != "passed":
                status = "failed" if executed.status != "skipped" else "skipped"
                failure_code = _failure_code(step.kind, executed.status, executed.error)
                if session_started:
                    results.extend(self._run_cleanup(scenario))
                return ScenarioRunResult(
                    scenario_id=scenario.id,
                    status=status,
                    steps=tuple(results),
                    mode=self._mode,
                    failure_code=failure_code,
                    next_actions=_next_actions_for_failure(
                        failure_code, executed.error
                    ),
                )

        if session_started:
            results.extend(self._run_cleanup(scenario))

        return ScenarioRunResult(
            scenario_id=scenario.id,
            status=status,
            steps=tuple(results),
            mode=self._mode,
        )

    def _first_prerequisite_skip(
        self, scenario: ReleaseScenario
    ) -> ScenarioPrerequisiteResult | None:
        if self._prerequisite_checker is None:
            return None
        for result in self._prerequisite_checker.check(scenario.prerequisites):
            if result.status != "passed":
                return result
        return None

    def _run_cleanup(
        self, scenario: ReleaseScenario
    ) -> Tuple[ScenarioRunStepResult, ...]:
        records: list[ScenarioRunStepResult] = []
        for step in scenario.cleanup:
            executed = self._executor.execute(step)
            records.append(_record_step(step.kind, executed, cleanup=True))
        return tuple(records)


def _record_prerequisite_skip(
    prerequisite: ScenarioPrerequisiteResult,
) -> ScenarioRunStepResult:
    return ScenarioRunStepResult(
        step_id=f"prerequisite:{prerequisite.prerequisite_id}",
        kind="prerequisite",
        status="skipped",
        output={
            "prerequisite_id": prerequisite.prerequisite_id,
            "status": prerequisite.status,
            "reason": prerequisite.reason,
            "details": dict(prerequisite.details),
        },
        error=prerequisite.reason,
    )


def _evaluate_assertion_step(
    step_id: str,
    parameters: Mapping[str, Any],
    context: Mapping[str, Any],
) -> ScenarioStepExecutionResult:
    assertion = parameters.get("assertion")
    if not isinstance(assertion, Mapping):
        assertion = {}
    result = evaluate_scenario_assertion(assertion, context)
    return ScenarioStepExecutionResult(
        step_id=step_id,
        status=result.status,
        output={"assertion": dataclasses.asdict(result)},
        error="" if result.status == "passed" else "assertion failed",
    )


def _record_step(
    kind: str, executed: ScenarioStepExecutionResult, cleanup: bool
) -> ScenarioRunStepResult:
    return ScenarioRunStepResult(
        step_id=executed.step_id,
        kind=kind,
        status=executed.status,
        output=dict(executed.output),
        error=executed.error,
        cleanup=cleanup,
    )


def _failure_code(kind: str, status: str, error: str = "") -> str:
    if status == "skipped":
        return "prerequisite_missing"
    if kind == "assert":
        return "assertion_failed"
    if error == "step_timeout":
        return "step_timeout"
    return "executor_error"


def _next_actions_for_failure(
    failure_code: str, error: str
) -> Tuple[ScenarioNextAction, ...]:
    if failure_code == "assertion_failed":
        return (
            ScenarioNextAction(
                code="inspect_evidence",
                message="Inspect run evidence and prior step outputs for assertion context.",
            ),
        )
    if failure_code == "prerequisite_missing":
        return (
            ScenarioNextAction(
                code="install_prerequisite",
                message=error
                or "Install or start the missing prerequisite, then rerun.",
            ),
        )
    return (
        ScenarioNextAction(
            code="inspect_error",
            message=error or "Inspect the failing step error and evidence bundle.",
        ),
    )
