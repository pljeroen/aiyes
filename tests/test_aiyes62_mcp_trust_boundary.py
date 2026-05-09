"""AIYES-62: MCP trust boundary metadata."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.cli.main import _build_mcp_manifest


def _make_deps() -> ServerDependencies:
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    return ServerDependencies(**fields)


class TestMcpManifestTrustBoundary:
    def test_manifest_states_trusted_local_stdio_and_no_sandbox(self) -> None:
        manifest = _build_mcp_manifest()

        boundary = manifest["trust_boundary"]

        assert boundary["scope"] == "trusted-local-stdio"
        assert boundary["transport"] == "stdio"
        assert boundary["sandbox"] is False
        assert "trusted local" in boundary["operator_model"].lower()
        assert "not a sandbox" in boundary["warning"].lower()


class TestMcpToolTrustMetadata:
    @pytest.mark.asyncio
    async def test_each_tool_exposes_trust_boundary_metadata(self) -> None:
        server = create_mcp_server(_make_deps())

        tools = await server.list_tools()

        assert tools
        for tool in tools:
            meta = tool.meta["aiyes"]
            assert meta["trust_boundary"] == "trusted-local-stdio"
            assert meta["sandbox"] is False
            assert "trusted local" in meta["warning"].lower()

    @pytest.mark.asyncio
    async def test_gui_control_tools_expose_control_risk(self) -> None:
        server = create_mcp_server(_make_deps())

        tools = {tool.name: tool for tool in await server.list_tools()}

        assert tools["action"].meta["aiyes"]["risk"] == "gui-control"
        assert tools["mouse_click"].meta["aiyes"]["risk"] == "gui-control"
        assert tools["type"].meta["aiyes"]["risk"] == "gui-control"
        assert tools["inspect"].meta["aiyes"]["risk"] == "gui-observation"
        assert tools["action"].annotations.destructiveHint is True
        assert tools["inspect"].annotations.readOnlyHint is True

