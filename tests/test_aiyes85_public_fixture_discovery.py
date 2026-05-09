"""AIYES-85: public fixture discovery."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_fixtures import list_public_scenario_fixtures
from aiyes.cli.main import cli


def test_cli_lists_public_scenario_fixtures() -> None:
    result = CliRunner().invoke(cli, ["scenario", "fixtures"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    ids = {fixture["scenario_id"] for fixture in payload["fixtures"]}
    assert {"linux-gedit-text", "android-settings"} <= ids
    assert all(item["path"].startswith("examples/scenarios/") for item in payload["fixtures"])


@pytest.mark.asyncio
async def test_mcp_lists_public_scenario_fixtures() -> None:
    fields = {field: MagicMock() for field in ServerDependencies.__dataclass_fields__}
    fields["clock"].now.side_effect = [1.0, 1.1]
    fields["list_public_scenario_fixtures"] = list_public_scenario_fixtures
    server = create_mcp_server(ServerDependencies(**fields))

    result = await server.call_tool("scenario_fixtures", {})

    assert result.isError is False
    payload = json.loads(result.content[0].text)
    assert payload["fixtures"][0]["path"].startswith("examples/scenarios/")
