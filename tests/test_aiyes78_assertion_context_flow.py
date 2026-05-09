"""AIYES-78: scenario assertion context flow."""

from __future__ import annotations

from aiyes.domain.scenario import ScenarioStep, validate_scenario_document
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult


class ContextExecutor:
    def __init__(self, outputs: dict[str, dict]) -> None:
        self.outputs = outputs
        self.calls: list[str] = []

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        self.calls.append(step.id)
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output=self.outputs.get(step.id, {}),
            session_id="s1" if step.kind == "start_session" else "",
        )


def test_runner_evaluates_assertion_steps_from_prior_step_context() -> None:
    scenario = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "assert-pass",
            "title": "Assertion pass",
            "target": "linux",
            "steps": [
                {"id": "inspect", "kind": "inspect"},
                {
                    "id": "assert_tree",
                    "kind": "assert",
                    "assertion": {
                        "kind": "tree_non_empty",
                        "source": "inspect",
                    },
                },
            ],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario
    assert scenario is not None
    executor = ContextExecutor(
        {"inspect": {"tree": {"children": [{"id": "n_001", "name": "Editor"}]}}}
    )

    result = ScenarioRunUseCase(executor).execute(scenario)

    assert result.status == "passed"
    assert executor.calls == ["inspect"]
    assert [step.step_id for step in result.steps] == ["inspect", "assert_tree"]
    assert result.steps[1].status == "passed"
    assert result.steps[1].output["assertion"]["status"] == "passed"


def test_runner_failed_assertion_fails_run_and_executes_cleanup() -> None:
    scenario = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "assert-fail",
            "title": "Assertion fail",
            "target": "linux",
            "steps": [
                {"id": "start", "kind": "start_session"},
                {"id": "inspect", "kind": "inspect"},
                {
                    "id": "assert_tree",
                    "kind": "assert",
                    "assertion": {
                        "kind": "tree_non_empty",
                        "source": "inspect",
                    },
                },
            ],
            "cleanup": [{"id": "stop", "kind": "stop_session"}],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario
    assert scenario is not None
    executor = ContextExecutor({"inspect": {"tree": {"children": []}}})

    result = ScenarioRunUseCase(executor).execute(scenario)

    assert result.status == "failed"
    assert executor.calls == ["start", "inspect", "stop"]
    assert [step.step_id for step in result.steps] == [
        "start",
        "inspect",
        "assert_tree",
        "stop",
    ]
    assert result.steps[2].status == "failed"
    assert result.steps[2].error == "assertion failed"
    assert result.steps[3].cleanup is True
