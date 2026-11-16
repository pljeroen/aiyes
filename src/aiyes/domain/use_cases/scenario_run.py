"""Deterministic release scenario runner use case."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Tuple

from aiyes.domain.scenario import _EXPECT_REFUSED, ReleaseScenario
from aiyes.domain.scenario_assertions import evaluate_scenario_assertion
from aiyes.domain.types import ACTION_DECLINED, ACTION_NOT_ENABLED
from aiyes.domain.use_cases.action import failed_action_reason
from aiyes.ports.scenario_executor import (
    ScenarioStepExecutionResult,
    ScenarioStepExecutorPort,
)
from aiyes.ports.scenario_prerequisites import (
    ScenarioPrerequisiteCheckerPort,
    ScenarioPrerequisiteResult,
)

# Stable codes of a failed action step: the action did not report ok, or it
# went through although the step expected a refusal. The step's error and the
# "failure_code" in its output carry the code, and the run reports it as its
# top-level failure_code.
_ACTION_FAILED = "action_failed"
_ACTION_NOT_REFUSED = "action_not_refused"
_ACTION_FAILURE_CODES = frozenset((_ACTION_FAILED, _ACTION_NOT_REFUSED))


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
                failure_code = _failure_code(
                    step.kind, executed.status, executed.error, executed.output
                )
                if session_started:
                    results.extend(self._run_cleanup(scenario))
                return ScenarioRunResult(
                    scenario_id=scenario.id,
                    status=status,
                    steps=tuple(results),
                    mode=self._mode,
                    failure_code=failure_code,
                    next_actions=_next_actions_for_failure(
                        failure_code, executed.error, step.id, executed.output
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


def action_refusal_cause_reported(output: Mapping[str, Any]) -> bool:
    """Whether an action step's output reports a refusal cause.

    True only when the action reported "status": "error" and its reason is
    exactly the text for one of the two refusal causes, for the output's
    action and target: the node is not enabled (the action was not sent), or
    the application declined the action. The list of actions the target
    offers is no evidence of a refusal on its own: an action the target does
    not offer was never sent, and an error without a cause on a target that
    lists no actions is also what a target missing when the action runs
    looks like.
    """
    if output.get("status") != "error":
        return False
    reason = output.get("reason")
    if not isinstance(reason, str):
        return False
    action = str(output.get("action", ""))
    target = str(output.get("target", ""))
    return reason in (
        failed_action_reason(action, target, ACTION_NOT_ENABLED),
        failed_action_reason(action, target, ACTION_DECLINED),
    )


def _failure_code(
    kind: str,
    status: str,
    error: str = "",
    output: Mapping[str, Any] | None = None,
) -> str:
    if status == "skipped":
        return "prerequisite_missing"
    if kind == "assert":
        return "assertion_failed"
    if error == "step_timeout":
        return "step_timeout"
    if _is_classified_action_failure(kind, error, output):
        return error
    return "executor_error"


def _is_classified_action_failure(
    kind: str, error: str, output: Mapping[str, Any] | None
) -> bool:
    """An action step the executor failed with one of the action codes.

    The code must be both the step's error and the "failure_code" in its
    output, so an exception whose message happens to equal a code still
    reports executor_error.
    """
    return (
        kind == "action"
        and error in _ACTION_FAILURE_CODES
        and isinstance(output, Mapping)
        and output.get("failure_code") == error
    )


def _next_actions_for_failure(
    failure_code: str,
    error: str,
    step_id: str = "",
    output: Mapping[str, Any] | None = None,
) -> Tuple[ScenarioNextAction, ...]:
    if failure_code in _ACTION_FAILURE_CODES:
        return (
            ScenarioNextAction(
                code="inspect_error",
                message=_action_failure_message(step_id, output or {}),
            ),
        )
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


def _action_failure_message(step_id: str, output: Mapping[str, Any]) -> str:
    """The run's single-line failure message for a failed action step.

    It names the step, quotes the action's reason when the output has one,
    says what aiyes observed and names only fixes that can change the outcome
    of that case: an action that went through under "refused"; a result
    without a readable status; a refusal cause (not enabled or declined); an
    action the target does not offer (naming the actions it offers); an
    offered action that failed without a refusal cause; or an error without a
    cause on a target that lists no actions, which aiyes cannot tell apart
    from a target that is not there.
    """
    step = f"Action step {step_id!r}"
    status = output.get("status")
    reason = _one_line(output.get("reason"))
    observed = reason or "the action reported an error without a reason"
    expects_refusal = output.get("expect") == _EXPECT_REFUSED
    if output.get("failure_code") == _ACTION_NOT_REFUSED:
        return (
            f"{step} failed: the action went through although the step expected "
            'a refusal ("expect": "refused"); check that the application is in '
            "the state the check expects at this point."
        )
    if status not in ("ok", "error"):
        received = "had no status" if status is None else f"had status {status!r}"
        given = f" Reason given: {reason}." if reason else ""
        return (
            f"{step} failed: aiyes could not confirm that the action happened "
            f"(the action result {received}).{given}"
        )
    if action_refusal_cause_reported(output):
        return (
            f"{step} failed: {observed}. Check the application state at this "
            'point; if the refusal is intended, set "expect": "refused" '
            '(negative check) or "expect": "any" (best effort) on that step.'
        )
    action = _one_line(output.get("action"))
    offered = _offered_actions(output)
    if offered:
        return _target_lists_actions_message(
            step, observed, action, offered, expects_refusal
        )
    if expects_refusal:
        return (
            f"{step} expected a refusal, but aiyes could not confirm the target "
            f"was there: {observed} (no actions and no refusal cause were "
            "reported, which is also what a missing target looks like). If the "
            "target is there, this is not a refusal: a refusal counts only when "
            "the node is not enabled or the application declines the action. On "
            "Android a refusal cannot be observed (the Android backend reports no "
            "refusal cause). If the target may not be there yet, wait for it "
            'before this step; if it may be absent, set "expect": "any" (best '
            "effort; the step then no longer checks a refusal)."
        )
    return (
        f"{step} failed: {observed}. aiyes could not confirm the target was there: "
        "no actions and no refusal cause were reported, which is also what a "
        "missing target looks like. If the target may not be there yet, wait for "
        'it before this step; if it may be absent, set "expect": "any" on that '
        "step (best effort). If it is there, it offers no actions, so the action "
        "cannot be sent to it: check that it is the node you meant."
    )


def _target_lists_actions_message(
    step: str,
    observed: str,
    action: str,
    offered: Tuple[str, ...],
    expects_refusal: bool,
) -> str:
    """Message for an error without a refusal cause on a target listing actions.

    When the action asked for is not among the target's actions, the message
    names them; when it is, the request failed without a cause. Under
    "refused" the message adds that neither case is a refusal.
    """
    rule = (
        " A refusal counts only when the node is not enabled or the application "
        "declines the action."
        if expects_refusal
        else ""
    )
    if action not in offered:
        not_asked = (
            " The application was not asked, so this is not a refusal."
            if expects_refusal
            else ""
        )
        return (
            f"{step} failed: {observed}. '{action}' is not offered by the target: "
            f"it offers {', '.join(offered)}: name one of those.{not_asked}{rule}"
        )
    not_refusal = " This is not a refusal." if expects_refusal else ""
    return (
        f"{step} failed: {observed}. The target offers '{action}', but the request "
        "did not succeed and no refusal cause was reported: check the application "
        f"state at this point.{not_refusal}{rule}"
    )


def _offered_actions(output: Mapping[str, Any]) -> Tuple[str, ...]:
    """The actions the target listed in an action step's output, as text."""
    actions = output.get("available_actions")
    if not isinstance(actions, (list, tuple)):
        return ()
    return tuple(_one_line(action) for action in actions if isinstance(action, str))


def _one_line(text: object) -> str:
    """``text`` with its line breaks replaced by spaces; "" for no text."""
    if not isinstance(text, str):
        return ""
    return " ".join(text.splitlines())
