"""AIYES-81: MCP scenario_run parity."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_dry_run_executor import ScenarioDryRunExecutor
from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.adapters.scenario_loader import load_scenario_file
from aiyes.cli.schema_gen import enumerate_commands
from aiyes.cli.main import cli
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase


def _deps() -> ServerDependencies:
    fields = {field: MagicMock() for field in ServerDependencies.__dataclass_fields__}
    fields["clock"].now.side_effect = [1.0, 1.1]
    fields["scenario_run_uc"] = ScenarioRunUseCase(ScenarioDryRunExecutor())
    fields["scenario_real_run_uc"] = ScenarioRunUseCase(ScenarioDryRunExecutor())
    fields["load_scenario_file"] = load_scenario_file
    fields["write_scenario_evidence_bundle"] = write_scenario_evidence_bundle
    return ServerDependencies(**fields)


def _scenario_file(tmp_path: Path) -> Path:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "mcp-scenario",
                "title": "MCP scenario",
                "target": "linux",
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": True, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )
    return scenario_path


def test_cli_schema_exposes_scenario_run_tool() -> None:
    tool_names = {command.tool_name for command in enumerate_commands(cli)}

    assert "scenario_run" in tool_names


@pytest.mark.asyncio
async def test_mcp_scenario_run_matches_cli_dry_run_result(tmp_path: Path) -> None:
    server = create_mcp_server(_deps())

    result = await server.call_tool(
        "scenario_run",
        {"scenario_path": str(_scenario_file(tmp_path))},
    )

    assert result.isError is False
    payload = json.loads(result.content[0].text)
    assert payload["scenario_id"] == "mcp-scenario"
    assert payload["status"] == "passed"
    assert payload["steps"][0]["output"]["dry_run"] is True


@pytest.mark.asyncio
async def test_mcp_scenario_run_writes_safe_evidence_dir(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    server = create_mcp_server(_deps())

    result = await server.call_tool(
        "scenario_run",
        {
            "scenario_path": str(_scenario_file(tmp_path)),
            "evidence_dir": str(evidence_dir),
        },
    )

    assert result.isError is False
    assert (evidence_dir / "run.json").exists()
    assert (evidence_dir / "steps.jsonl").exists()


@pytest.mark.asyncio
async def test_mcp_scenario_run_rejects_project_evidence_dir(tmp_path: Path) -> None:
    server = create_mcp_server(_deps())

    result = await server.call_tool(
        "scenario_run",
        {
            "scenario_path": str(_scenario_file(tmp_path)),
            "evidence_dir": str(Path.cwd() / "mcp-scenario-evidence"),
        },
    )

    assert result.isError is True
    assert "evidence_dir must not target a project path" in result.content[0].text
