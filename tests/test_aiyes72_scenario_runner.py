"""AIYES-72: deterministic release scenario runner."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aiyes.cli.main import cli
from aiyes.domain.scenario import ScenarioStep, validate_scenario_document
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult


class FakeScenarioExecutor:
    def __init__(self, fail_on: str = "") -> None:
        self.fail_on = fail_on
        self.calls: list[str] = []

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        self.calls.append(step.id)
        if step.id == self.fail_on:
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="failed",
                output={},
                error="boom",
            )
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output={"kind": step.kind},
            error="",
            session_id="s1" if step.kind == "start_session" else "",
        )


def _scenario_with_cleanup() -> object:
    return validate_scenario_document(
        {
            "schema_version": 1,
            "id": "runner-smoke",
            "title": "Runner smoke",
            "target": "linux",
            "steps": [
                {"id": "start", "kind": "start_session"},
                {"id": "inspect", "kind": "inspect"},
            ],
            "cleanup": [{"id": "stop", "kind": "stop_session"}],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario


def test_runner_executes_declared_steps_in_order() -> None:
    scenario = _scenario_with_cleanup()
    assert scenario is not None
    executor = FakeScenarioExecutor()

    result = ScenarioRunUseCase(executor).execute(scenario)

    assert result.status == "passed"
    assert executor.calls == ["start", "inspect", "stop"]
    assert [step.step_id for step in result.steps] == ["start", "inspect", "stop"]


def test_runner_executes_cleanup_on_success_once_session_started() -> None:
    scenario = _scenario_with_cleanup()
    assert scenario is not None
    executor = FakeScenarioExecutor()

    result = ScenarioRunUseCase(executor).execute(scenario)

    assert executor.calls == ["start", "inspect", "stop"]
    assert result.steps[-1].cleanup is True


def test_runner_executes_cleanup_after_failure_once_session_started() -> None:
    scenario = _scenario_with_cleanup()
    assert scenario is not None
    executor = FakeScenarioExecutor(fail_on="inspect")

    result = ScenarioRunUseCase(executor).execute(scenario)

    assert result.status == "failed"
    assert executor.calls == ["start", "inspect", "stop"]
    assert [step.step_id for step in result.steps] == ["start", "inspect", "stop"]
    assert result.steps[1].status == "failed"
    assert result.steps[2].cleanup is True


def test_cli_scenario_run_emits_machine_readable_json(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "cli-smoke",
                "title": "CLI smoke",
                "target": "linux",
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": True, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["scenario", "run", str(scenario_path)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "passed"
    assert payload["scenario_id"] == "cli-smoke"
    assert payload["steps"][0]["step_id"] == "inspect"
    assert payload["steps"][0]["output"]["dry_run"] is True


def test_cli_scenario_run_returns_validation_errors(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(cli, ["scenario", "run", str(scenario_path)])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["errors"][0]["code"] == "missing_required"
