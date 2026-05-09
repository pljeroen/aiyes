"""AIYES-80: opt-in real scenario prerequisite gate."""

from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from aiyes.cli.main import cli
from aiyes.domain.scenario import ScenarioStep, validate_scenario_document
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult
from aiyes.ports.scenario_prerequisites import ScenarioPrerequisiteResult


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        self.calls.append(step.id)
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output={"kind": step.kind},
        )


class FakePrerequisiteChecker:
    def __init__(self, result: ScenarioPrerequisiteResult) -> None:
        self.result = result

    def check(self, prerequisites: tuple[dict[str, Any], ...]) -> tuple[ScenarioPrerequisiteResult, ...]:
        return (self.result,)


def test_real_runner_skips_before_execution_when_prerequisite_is_missing() -> None:
    scenario = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "missing-gedit",
            "title": "Missing gedit",
            "target": "linux",
            "prerequisites": [{"kind": "executable", "name": "gedit"}],
            "steps": [{"id": "inspect", "kind": "inspect"}],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario
    assert scenario is not None
    executor = RecordingExecutor()
    checker = FakePrerequisiteChecker(
        ScenarioPrerequisiteResult(
            prerequisite_id="executable:gedit",
            status="skipped",
            reason="required executable not found: gedit",
            details={"kind": "executable", "name": "gedit"},
        )
    )

    result = ScenarioRunUseCase(executor, prerequisite_checker=checker).execute(scenario)

    assert result.status == "skipped"
    assert executor.calls == []
    assert result.steps[0].step_id == "prerequisite:executable:gedit"
    assert result.steps[0].kind == "prerequisite"
    assert result.steps[0].status == "skipped"
    assert result.steps[0].output["reason"] == "required executable not found: gedit"


def test_dry_runner_without_gate_still_executes_declared_steps() -> None:
    scenario = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "dry-ungated",
            "title": "Dry ungated",
            "target": "linux",
            "prerequisites": [{"kind": "executable", "name": "definitely-missing"}],
            "steps": [{"id": "inspect", "kind": "inspect"}],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario
    assert scenario is not None
    executor = RecordingExecutor()

    result = ScenarioRunUseCase(executor).execute(scenario)

    assert result.status == "passed"
    assert executor.calls == ["inspect"]


def test_system_prerequisite_checker_skips_missing_executable() -> None:
    from aiyes.adapters.scenario_prerequisites import SystemScenarioPrerequisiteChecker

    checker = SystemScenarioPrerequisiteChecker(which=lambda _name: None)

    result = checker.check(({"kind": "executable", "name": "gedit"},))

    assert result[0].status == "skipped"
    assert result[0].prerequisite_id == "executable:gedit"


def test_cli_dry_run_public_fixture_does_not_evaluate_gui_assertions() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "scenario",
            "run",
            "--public-fixture",
            "examples/scenarios/linux-gedit-text.json",
        ],
    )

    assert result.exit_code == 0
