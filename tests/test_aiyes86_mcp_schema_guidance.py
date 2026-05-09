"""AIYES-86: MCP scenario schema guidance."""

from __future__ import annotations

from aiyes.cli.main import cli
from aiyes.cli.schema_gen import enumerate_commands


def test_scenario_mcp_schemas_include_agent_guidance_descriptions() -> None:
    commands = {command.tool_name: command for command in enumerate_commands(cli)}

    for tool_name in ("scenario_run", "scenario_preflight", "scenario_fixtures"):
        assert tool_name in commands
        schema = commands[tool_name].json_schema
        for prop in schema.get("properties", {}).values():
            assert prop.get("description")

    real_description = commands["scenario_run"].json_schema["properties"][
        "real_execution"
    ]["description"]
    assert "opt-in" in real_description
