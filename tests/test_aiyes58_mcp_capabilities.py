"""AIYES-58: MCP parity for session capability disclosure."""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import MagicMock

import pytest

from aiyes.adapters.mcp_server import ServerDependencies, _build_dispatch_table
from aiyes.adapters.mcp_server import create_mcp_server
from aiyes.domain.use_cases.session_capabilities import (
    Capability,
    SessionCapabilitiesResult,
)


def _make_deps(**overrides):
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    fields.update(overrides)
    return ServerDependencies(**fields)


class TestMcpSessionCapabilitiesDispatch:
    def test_dispatch_table_has_session_capabilities_handler(self) -> None:
        deps = _make_deps()
        table = _build_dispatch_table(deps)
        assert "session_capabilities" in table
        assert table["session_capabilities"].session_class == "bound"

    @pytest.mark.asyncio
    async def test_call_tool_returns_session_capabilities_json(self) -> None:
        capability_result = SessionCapabilitiesResult(
            session_id="abc123",
            backend="android",
            capabilities={
                "semantic_tree": Capability(
                    status="available",
                    reason="Android exposes a UIAutomator-derived tree.",
                    operations=("inspect", "find", "wait"),
                ),
            },
        )
        deps = _make_deps()
        deps.resolve_session_id.return_value = "abc123"
        deps.clock.now.side_effect = [10.0, 10.1]
        deps.session_capabilities_uc.execute.return_value = capability_result

        server = create_mcp_server(deps)
        result = await server.call_tool("session_capabilities", {})

        assert result.isError is False
        data = json.loads(result.content[0].text)
        assert data["session_id"] == "abc123"
        assert data["backend"] == "android"
        assert data["capabilities"]["semantic_tree"]["status"] == "available"
        deps.session_capabilities_uc.execute.assert_called_once_with(
            session_id="abc123",
            live=False,
        )
