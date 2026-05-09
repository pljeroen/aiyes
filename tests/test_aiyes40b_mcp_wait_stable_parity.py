"""AIYES-40 Group B — MCP wait-stable parity tests.

Requirements:
  R-40-10: MCP wait_stable handler accepts and forwards tolerance (int, default 0).
  R-40-11: MCP wait_stable handler accepts and forwards ignore_nodes as frozenset.
  R-40-12: MCP wait_stable handler passes changes to presenter.
  R-40-13: MCP tool schema includes tolerance and ignore_nodes parameters.

Acceptance Criteria:
  AC-B01: tolerance=2 forwarded to use case.
  AC-B02: ignore_nodes=["n_001","n_002"] forwarded as frozenset.
  AC-B03: Timeout result includes changes array in JSON output.
  AC-B04: Schema includes tolerance and ignore_nodes properties.
  AC-B05: Default behavior unchanged when params omitted.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from aiyes.adapters.mcp_server import (
    ServerDependencies,
    create_mcp_server,
)
from aiyes.domain.operation_record import OperationRecord


# ===================================================================
# Helpers
# ===================================================================


def _make_mock_deps(**overrides: Any) -> ServerDependencies:
    """Build a ServerDependencies with all fields as MagicMock, with overrides."""
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    fields.update(overrides)
    return ServerDependencies(**fields)


def _make_wait_stable_result(
    stable: bool = True,
    timeout: bool = False,
    polls: int = 3,
    changes: tuple = (),
) -> MagicMock:
    """Build a mock WaitStableResult with all required fields."""
    result = MagicMock()
    result.stable = stable
    result.timeout = timeout
    result.polls = polls
    result.changes = changes
    return result


def _make_server_with_wait_stable(
    wait_stable_result: Any,
) -> tuple:
    """Create MCP server with mock wait_stable_uc returning the given result.

    Returns (server, mock_wait_stable_uc, deps).
    """
    mock_wait_stable = MagicMock()
    mock_wait_stable.execute.return_value = wait_stable_result
    mock_resolve = MagicMock(return_value="test-sess")
    mock_clock = MagicMock()
    mock_clock.now.return_value = 1000.0
    mock_op_log = MagicMock()

    deps = _make_mock_deps(
        wait_stable_uc=mock_wait_stable,
        resolve_session_id=mock_resolve,
        clock=mock_clock,
        operation_log=mock_op_log,
    )
    server = create_mcp_server(deps)
    return server, mock_wait_stable, deps


# ===================================================================
# AC-B01: MCP wait_stable forwards tolerance to use case (R-40-10)
# ===================================================================


class TestWaitStableToleranceForwarding:
    """R-40-10 / AC-B01: MCP wait_stable forwards tolerance parameter."""

    @pytest.mark.asyncio
    async def test_tolerance_forwarded_to_use_case(self) -> None:
        """AC-B01: tolerance=2 is forwarded to wait_stable_uc.execute."""
        result = _make_wait_stable_result(stable=True, polls=4)
        server, mock_uc, _ = _make_server_with_wait_stable(result)

        await server.call_tool(
            "wait_stable",
            {"session_id": "test-sess", "tolerance": 2},
        )

        mock_uc.execute.assert_called_once()
        call_kwargs = mock_uc.execute.call_args[1]
        assert call_kwargs["tolerance"] == 2

    @pytest.mark.asyncio
    async def test_tolerance_default_is_zero(self) -> None:
        """AC-B05: When tolerance is omitted, default 0 is forwarded."""
        result = _make_wait_stable_result(stable=True, polls=3)
        server, mock_uc, _ = _make_server_with_wait_stable(result)

        await server.call_tool(
            "wait_stable",
            {"session_id": "test-sess"},
        )

        mock_uc.execute.assert_called_once()
        call_kwargs = mock_uc.execute.call_args[1]
        assert call_kwargs["tolerance"] == 0


# ===================================================================
# AC-B02: MCP wait_stable forwards ignore_nodes as frozenset (R-40-11)
# ===================================================================


class TestWaitStableIgnoreNodesForwarding:
    """R-40-11 / AC-B02: MCP wait_stable forwards ignore_nodes as frozenset."""

    @pytest.mark.asyncio
    async def test_ignore_nodes_forwarded_as_frozenset(self) -> None:
        """AC-B02: ignore_nodes=["n_001","n_002"] forwarded as frozenset."""
        result = _make_wait_stable_result(stable=True, polls=3)
        server, mock_uc, _ = _make_server_with_wait_stable(result)

        await server.call_tool(
            "wait_stable",
            {
                "session_id": "test-sess",
                "ignore_nodes": ["n_001", "n_002"],
            },
        )

        mock_uc.execute.assert_called_once()
        call_kwargs = mock_uc.execute.call_args[1]
        assert call_kwargs["ignore_ids"] == frozenset({"n_001", "n_002"})

    @pytest.mark.asyncio
    async def test_ignore_nodes_default_is_empty_frozenset(self) -> None:
        """AC-B05: When ignore_nodes is omitted, empty frozenset is forwarded."""
        result = _make_wait_stable_result(stable=True, polls=3)
        server, mock_uc, _ = _make_server_with_wait_stable(result)

        await server.call_tool(
            "wait_stable",
            {"session_id": "test-sess"},
        )

        mock_uc.execute.assert_called_once()
        call_kwargs = mock_uc.execute.call_args[1]
        assert call_kwargs["ignore_ids"] == frozenset()


# ===================================================================
# AC-B03: MCP wait_stable includes changes in response (R-40-12)
# ===================================================================


class TestWaitStableChangesInResponse:
    """R-40-12 / AC-B03: MCP wait_stable passes changes to presenter."""

    @pytest.mark.asyncio
    async def test_unstable_result_includes_changes(self) -> None:
        """AC-B03: When stable=False and changes present, JSON includes changes."""
        changes = ({"id": "n_001", "field": "name", "before": "A", "after": "B"},)
        result = _make_wait_stable_result(
            stable=False,
            timeout=True,
            polls=5,
            changes=changes,
        )
        server, _, _ = _make_server_with_wait_stable(result)

        mcp_result = await server.call_tool(
            "wait_stable",
            {"session_id": "test-sess"},
        )

        assert mcp_result.isError is False
        response_text = mcp_result.content[0].text
        parsed = json.loads(response_text)
        assert "changes" in parsed
        assert len(parsed["changes"]) == 1
        assert parsed["changes"][0]["id"] == "n_001"

    @pytest.mark.asyncio
    async def test_stable_result_omits_changes(self) -> None:
        """AC-B03: When stable=True, changes is not in output."""
        result = _make_wait_stable_result(stable=True, polls=3, changes=())
        server, _, _ = _make_server_with_wait_stable(result)

        mcp_result = await server.call_tool(
            "wait_stable",
            {"session_id": "test-sess"},
        )

        assert mcp_result.isError is False
        response_text = mcp_result.content[0].text
        parsed = json.loads(response_text)
        assert "changes" not in parsed

    @pytest.mark.asyncio
    async def test_unstable_with_empty_changes_omits_changes(self) -> None:
        """AC-B03: When stable=False but changes is empty, changes is not in output."""
        result = _make_wait_stable_result(
            stable=False,
            timeout=True,
            polls=5,
            changes=(),
        )
        server, _, _ = _make_server_with_wait_stable(result)

        mcp_result = await server.call_tool(
            "wait_stable",
            {"session_id": "test-sess"},
        )

        assert mcp_result.isError is False
        response_text = mcp_result.content[0].text
        parsed = json.loads(response_text)
        assert "changes" not in parsed


# ===================================================================
# AC-B05: Default behavior unchanged (combined test)
# ===================================================================


class TestWaitStableDefaultsUnchanged:
    """AC-B05: Omitting tolerance and ignore_nodes gives same behavior as before."""

    @pytest.mark.asyncio
    async def test_default_call_produces_expected_json(self) -> None:
        """AC-B05: Default call produces JSON with stable/timeout/polls only."""
        result = _make_wait_stable_result(stable=True, timeout=False, polls=3)
        server, mock_uc, _ = _make_server_with_wait_stable(result)

        mcp_result = await server.call_tool(
            "wait_stable",
            {"session_id": "test-sess"},
        )

        assert mcp_result.isError is False
        response_text = mcp_result.content[0].text
        parsed = json.loads(response_text)
        assert parsed["stable"] is True
        assert parsed["timeout"] is False
        assert parsed["polls"] == 3
        assert "changes" not in parsed

        # Verify defaults forwarded
        call_kwargs = mock_uc.execute.call_args[1]
        assert call_kwargs["tolerance"] == 0
        assert call_kwargs["ignore_ids"] == frozenset()
        assert call_kwargs["timeout"] == 10.0
        assert call_kwargs["poll_interval"] == 0.5
        assert call_kwargs["consecutive"] == 3
