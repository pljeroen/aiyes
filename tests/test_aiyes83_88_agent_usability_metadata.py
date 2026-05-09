"""AIYES-83/88: scenario next actions and failure taxonomy."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiyes.domain.scenario import ScenarioStep, validate_scenario_document
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult
from aiyes.ports.scenario_prerequisites import ScenarioPrerequisiteResult
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_dry_run_executor import ScenarioDryRunExecutor
from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.adapters.scenario_loader import load_scenario_file


class FailingExecutor:
    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="failed",
            output={},
            error="boom",
        )


class MissingPrerequisite:
    def check(self, _prerequisites: tuple) -> tuple[ScenarioPrerequisiteResult, ...]:
        return (
            ScenarioPrerequisiteResult(
                prerequisite_id="executable:gedit",
                status="skipped",
                reason="required executable not found: gedit",
                details={"kind": "executable", "name": "gedit"},
            ),
        )


def test_prerequisite_skip_includes_next_action_and_failure_code() -> None:
    scenario = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "skip-meta",
            "title": "Skip meta",
            "target": "linux",
            "prerequisites": [{"kind": "executable", "name": "gedit"}],
            "steps": [{"id": "inspect", "kind": "inspect"}],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario
    assert scenario is not None

    result = ScenarioRunUseCase(
        FailingExecutor(),
        prerequisite_checker=MissingPrerequisite(),
        mode="real",
    ).execute(scenario)

    assert result.mode == "real"
    assert result.status == "skipped"
    assert result.failure_code == "prerequisite_missing"
    assert result.next_actions[0].code == "install_prerequisite"


def test_executor_failure_includes_next_action_and_failure_code() -> None:
    scenario = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "executor-fail",
            "title": "Executor fail",
            "target": "linux",
            "steps": [{"id": "inspect", "kind": "inspect"}],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario
    assert scenario is not None

    result = ScenarioRunUseCase(FailingExecutor(), mode="real").execute(scenario)

    assert result.status == "failed"
    assert result.failure_code == "executor_error"
    assert result.next_actions[0].code == "inspect_error"


def test_validation_errors_include_stable_failure_code(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from aiyes.cli.main import cli

    scenario_path = tmp_path / "invalid.json"
    scenario_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(cli, ["scenario", "run", str(scenario_path)])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["failure_code"] == "validation_error"


@pytest.mark.asyncio
async def test_mcp_evidence_path_rejection_includes_stable_failure_code(
    tmp_path: Path,
) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "evidence-path-code",
                "title": "Evidence path code",
                "target": "linux",
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": True, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )
    fields = {field: MagicMock() for field in ServerDependencies.__dataclass_fields__}
    fields["clock"].now.side_effect = [1.0, 1.1]
    fields["scenario_run_uc"] = ScenarioRunUseCase(ScenarioDryRunExecutor())
    fields["scenario_real_run_uc"] = ScenarioRunUseCase(ScenarioDryRunExecutor())
    fields["load_scenario_file"] = load_scenario_file
    fields["write_scenario_evidence_bundle"] = write_scenario_evidence_bundle
    server = create_mcp_server(ServerDependencies(**fields))

    result = await server.call_tool(
        "scenario_run",
        {
            "scenario_path": str(scenario_path),
            "evidence_dir": str(Path.cwd() / "bad-evidence-dir"),
        },
    )

    assert result.isError is True
    payload = json.loads(result.content[0].text)
    assert payload["failure_code"] == "evidence_path_rejected"
