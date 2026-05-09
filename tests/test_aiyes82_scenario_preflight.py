"""AIYES-82: scenario preflight surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_dry_run_executor import ScenarioDryRunExecutor
from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.adapters.scenario_loader import load_scenario_file
from aiyes.cli.main import cli
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.domain.use_cases.scenario_preflight import (
    ScenarioEvidencePathCheck,
    ScenarioPreflightUseCase,
    scenario_validation_preflight_result,
)


def _scenario(tmp_path: Path, prerequisites: list[dict] | None = None) -> Path:
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "preflight-smoke",
                "title": "Preflight smoke",
                "target": "linux",
                "prerequisites": prerequisites or [],
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": True, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )
    return path


def _deps() -> ServerDependencies:
    fields = {field: MagicMock() for field in ServerDependencies.__dataclass_fields__}
    fields["clock"].now.side_effect = [1.0, 1.1]
    fields["scenario_run_uc"] = ScenarioRunUseCase(ScenarioDryRunExecutor())
    fields["scenario_real_run_uc"] = ScenarioRunUseCase(ScenarioDryRunExecutor())
    fields["scenario_preflight_uc"] = ScenarioPreflightUseCase()
    fields["scenario_real_preflight_uc"] = ScenarioPreflightUseCase()
    fields["scenario_evidence_path_check"] = ScenarioEvidencePathCheck
    fields["scenario_validation_preflight_result"] = scenario_validation_preflight_result
    fields["load_scenario_file"] = load_scenario_file
    fields["write_scenario_evidence_bundle"] = write_scenario_evidence_bundle
    return ServerDependencies(**fields)


def test_cli_scenario_preflight_dry_run_does_not_execute_steps(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["scenario", "preflight", str(_scenario(tmp_path))])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["scenario_id"] == "preflight-smoke"
    assert payload["status"] == "passed"
    assert payload["mode"] == "preflight"
    assert payload["requested_mode"] == "dry_run"
    assert payload["steps_would_execute"] is False


def test_cli_scenario_preflight_real_missing_prerequisite_skips(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path,
        [{"kind": "executable", "name": "definitely-not-aiyes-agent-tool"}],
    )

    result = CliRunner().invoke(cli, ["scenario", "preflight", "--real", str(scenario)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "skipped"
    assert payload["failure_code"] == "prerequisite_missing"
    assert payload["prerequisites"][0]["status"] == "skipped"


@pytest.mark.asyncio
async def test_mcp_scenario_preflight_matches_cli_shape(tmp_path: Path) -> None:
    server = create_mcp_server(_deps())

    result = await server.call_tool(
        "scenario_preflight",
        {"scenario_path": str(_scenario(tmp_path))},
    )

    assert result.isError is False
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "passed"
    assert payload["mode"] == "preflight"
    assert payload["requested_mode"] == "dry_run"
