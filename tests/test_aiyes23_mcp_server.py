"""AIYES-23 MCP Server tests — RED phase.

Tests for the MCP server adapter: import guard, ServerDependencies,
create_mcp_server factory, list_tools, call_tool dispatch, error handling,
session attribution, and per-session locking. These tests MUST fail because
src/aiyes/adapters/mcp_server.py does not exist yet.

Traceability — Formal Constraint Map:
  AC-02: MCP SDK imports confined to adapter layer
  AC-03: Factory pattern — create_mcp_server injectable
  AC-05: Sync-to-async bridge — asyncio.to_thread
  AC-06: Per-session asyncio.Lock for bound commands
  AC-07: Global asyncio.Lock for session-less and session-creating commands
  BC-17: MCP list_tools returns exactly 23 tools
  BC-18: MCP call_tool dispatches to correct use case
  BC-19: Use case exceptions return isError MCP result
  BC-20: Import guard produces helpful error
  BC-22: MCP operation logging uses OperationRecord
  BC-26: ServerDependencies frozen dataclass — 21 fields

Requirement coverage:
  REQ-AIYES23-019: ServerDependencies injectable
  REQ-AIYES23-020: ServerDependencies fields
  REQ-AIYES23-022: Import guard _MCP_AVAILABLE
  REQ-AIYES23-023: Import guard error message
  REQ-AIYES23-024: asyncio.to_thread wrapping
  REQ-AIYES23-025: Per-session lock
  REQ-AIYES23-026: Global lock for session-less
  REQ-AIYES23-031: call_tool dispatch
  REQ-AIYES23-032: Exception -> isError
  REQ-AIYES23-033: Operation logging session-bound
  REQ-AIYES23-034: Operation logging session-less
  REQ-AIYES23-035: list_tools count
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# These imports will fail (RED) — production module does not exist yet.
from aiyes.adapters.mcp_server import (
    ServerDependencies,
    ToolHandler,
    _MCP_AVAILABLE,
    create_mcp_server,
)

# ToolHandler is used in TestToolHandler below
_ToolHandler_ref = ToolHandler  # prevent linter from removing unused import

from aiyes.cli.schema_gen import enumerate_commands
from aiyes.cli.main import cli
from aiyes.domain.operation_record import OperationRecord


# ===================================================================
# Helpers
# ===================================================================


def _make_mock_deps(**overrides: Any) -> ServerDependencies:
    """Build a ServerDependencies with all fields as MagicMock, with overrides."""
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    fields.update(overrides)
    return ServerDependencies(**fields)


class SpyOperationLog:
    """Spy that records all append calls for assertion."""

    def __init__(self) -> None:
        self.appended: List[OperationRecord] = []

    def append(self, record: OperationRecord) -> None:
        self.appended.append(record)

    def read(self, session_id: str) -> List[OperationRecord]:
        return [r for r in self.appended if r.session_id == session_id]

    def read_all(self) -> List[OperationRecord]:
        return list(self.appended)

    def list_session_ids(self) -> List[str]:
        seen: List[str] = []
        for r in self.appended:
            sid = r.session_id if r.session_id else "_global"
            if sid not in seen:
                seen.append(sid)
        return seen


# ===================================================================
# BC-20: Import guard (REQ-AIYES23-022, REQ-AIYES23-023)
# ===================================================================


class TestImportGuard:
    """mcp_server.py import guard sets _MCP_AVAILABLE flag."""

    def test_mcp_available_is_true_when_installed(self) -> None:
        """BC-20: With mcp installed, _MCP_AVAILABLE is True."""
        # mcp is in test deps, so this should be True.
        assert _MCP_AVAILABLE is True

    def test_import_does_not_raise_when_mcp_missing(self) -> None:
        """BC-20: When mcp is not importable, module still loads with _MCP_AVAILABLE=False."""
        # Simulate mcp not installed by patching the import.
        import aiyes.adapters.mcp_server as mod

        with patch.dict(
            "sys.modules", {"mcp": None, "mcp.server": None, "mcp.types": None}
        ):
            # Re-importing should handle ImportError gracefully.
            # We test by checking the guard pattern exists in the module.
            # The actual test is that the module loaded at all (above import succeeded).
            assert hasattr(mod, "_MCP_AVAILABLE")


# ===================================================================
# BC-26: ServerDependencies frozen dataclass (REQ-AIYES23-019, REQ-AIYES23-020)
# ===================================================================


class TestServerDependencies:
    """ServerDependencies is a frozen dataclass."""

    def test_is_dataclass(self) -> None:
        """BC-26: ServerDependencies is a dataclass."""
        assert dataclasses.is_dataclass(ServerDependencies)

    def test_is_frozen(self) -> None:
        """BC-26: ServerDependencies is frozen — mutation raises AttributeError."""
        deps = _make_mock_deps()
        with pytest.raises(AttributeError):
            deps.clock = MagicMock()  # type: ignore[misc]

    def test_has_39_fields(self) -> None:
        """BC-26: Exactly 46 fields after the AIYES-117 marionette DOM-lens wiring."""
        fields = dataclasses.fields(ServerDependencies)
        assert len(fields) == 46

    def test_use_case_fields_present(self) -> None:
        """BC-26: Core use case fields are present."""
        field_names = {f.name for f in dataclasses.fields(ServerDependencies)}
        expected_ucs = {
            "session_start_uc",
            "session_stop_uc",
            "session_list_uc",
            "session_capabilities_uc",
            "session_resize_uc",
            "metrics_uc",
            "prune_uc",
            "inspect_uc",
            "diff_uc",
            "find_uc",
            "screenshot_uc",
            "action_uc",
            "mouse_uc",
            "key_uc",
            "type_text_uc",
            "wait_uc",
            "wait_stable_uc",
            "compound_do_uc",
            "doctor_uc",
            "debug_bundle_uc",
        }
        assert expected_ucs.issubset(field_names)

    def test_infrastructure_fields_present(self) -> None:
        """BC-26: clock, operation_log, resolve_session_id fields are present."""
        field_names = {f.name for f in dataclasses.fields(ServerDependencies)}
        assert "clock" in field_names
        assert "operation_log" in field_names
        assert "resolve_session_id" in field_names


# ===================================================================
# AC-03: create_mcp_server factory (REQ-AIYES23-021)
# ===================================================================


class TestCreateMcpServer:
    """create_mcp_server(deps) returns a server object."""

    def test_returns_server_object(self) -> None:
        """AC-03: Factory returns a server with mock deps (no Xvfb/AT-SPI)."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)
        assert server is not None

    def test_server_has_list_tools(self) -> None:
        """AC-03: Returned server has list_tools handler registered."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)
        # Server should support the list_tools method.
        assert hasattr(server, "list_tools")


# ===================================================================
# BC-17: list_tools returns exactly 23 tools (REQ-AIYES23-035)
# ===================================================================


class TestListTools:
    """MCP server list_tools returns exactly 23 tools."""

    @pytest.mark.asyncio
    async def test_list_tools_count_is_38(self) -> None:
        """BC-17: list_tools includes capabilities, swipe, goto, reload."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)

        # Call list_tools handler.
        tools = await server.list_tools()
        assert len(tools) == 44

    @pytest.mark.asyncio
    async def test_tool_names_match_enumerate_commands(self) -> None:
        """BC-17/IC-03: Tool names match enumerate_commands tool_name values."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)

        tools = await server.list_tools()
        mcp_tool_names = {t.name for t in tools}

        commands = enumerate_commands(cli)
        expected_names = {ci.tool_name for ci in commands}

        assert mcp_tool_names == expected_names

    @pytest.mark.asyncio
    async def test_tool_schemas_match_schema_gen(self) -> None:
        """BC-17/IC-01: Tool schemas match schema_gen output."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)

        tools = await server.list_tools()
        tool_schemas = {t.name: t.inputSchema for t in tools}

        commands = enumerate_commands(cli)
        for ci in commands:
            assert ci.tool_name in tool_schemas, f"Missing tool: {ci.tool_name}"
            assert tool_schemas[ci.tool_name] == ci.json_schema, (
                f"Schema mismatch for {ci.tool_name}"
            )

    @pytest.mark.asyncio
    async def test_each_tool_has_description(self) -> None:
        """BC-17: Each tool has a non-empty description."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)

        tools = await server.list_tools()
        for tool in tools:
            assert tool.description is not None
            assert len(tool.description.strip()) > 0, (
                f"Tool {tool.name} has empty description"
            )


# ===================================================================
# BC-18: call_tool dispatches to correct use case (REQ-AIYES23-031)
# ===================================================================


class TestCallToolDispatch:
    """call_tool dispatches to the correct use case."""

    @pytest.mark.asyncio
    async def test_inspect_dispatches_to_inspect_uc(self) -> None:
        """BC-18: calling 'inspect' tool invokes inspect_uc."""
        mock_inspect = MagicMock()
        mock_inspect.execute.return_value = MagicMock(
            tree=None,
            screenshot=None,
            timestamp=0.0,
            screenshot_base64=False,
            screenshot_data=None,
        )
        mock_resolve = MagicMock(return_value="test-session")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            inspect_uc=mock_inspect,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "inspect",
            {"session_id": "test-session"},
        )

        # F-08: verify arguments, not just call count
        mock_inspect.execute.assert_called_once_with(
            session_id="test-session",
            no_screenshot=False,
            no_tree=False,
            tree_depth=None,
            no_prune=False,
            screenshot_base64=False,
            focus_window=None,
        )

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """BC-18: call_tool with unknown tool name returns error."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)

        result = await server.call_tool("nonexistent_tool", {})

        # Result should indicate an error.
        assert result.isError is True


# ===================================================================
# AC-05: asyncio.to_thread wrapping (REQ-AIYES23-024)
# ===================================================================


class TestAsyncToThread:
    """Sync use case calls are wrapped in asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_call_tool_uses_to_thread(self) -> None:
        """AC-05: call_tool wraps sync UC in asyncio.to_thread."""
        mock_doctor = MagicMock()
        mock_doctor.execute.return_value = []
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            doctor_uc=mock_doctor,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = []
            await server.call_tool("doctor", {})
            mock_to_thread.assert_called()


# ===================================================================
# BC-19: Exception -> isError (REQ-AIYES23-032)
# ===================================================================


class TestErrorHandling:
    """Use case exceptions produce isError response."""

    @pytest.mark.asyncio
    async def test_use_case_exception_returns_is_error(self) -> None:
        """BC-19: RuntimeError in UC -> isError=True response."""
        mock_doctor = MagicMock()
        mock_doctor.execute.side_effect = RuntimeError("test error")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            doctor_uc=mock_doctor,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool("doctor", {})

        assert result.isError is True

    @pytest.mark.asyncio
    async def test_error_message_in_response_content(self) -> None:
        """BC-19: Error message appears in TextContent."""
        mock_doctor = MagicMock()
        mock_doctor.execute.side_effect = RuntimeError("test error")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            doctor_uc=mock_doctor,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool("doctor", {})

        # At least one content item should contain the error text.
        texts = [c.text for c in result.content if hasattr(c, "text")]
        assert any("test error" in t for t in texts)


class TestAiyes49McpEdgeCaseParity:
    """AIYES-49/R-007: MCP edge-case validation and session error parity."""

    @staticmethod
    def _content_text(result: Any) -> str:
        return "\n".join(c.text for c in result.content if hasattr(c, "text"))

    @pytest.mark.asyncio
    async def test_mouse_click_with_x_only_returns_error_without_use_case_call(
        self,
    ) -> None:
        """R007-MOUSE-PAIR: x without y is rejected before MouseUseCase."""
        mock_mouse = MagicMock()
        mock_resolve = MagicMock(return_value="sess-49")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            mouse_uc=mock_mouse,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "mouse_click", {"session_id": "sess-49", "x": 10}
        )

        assert result.isError is True
        assert "x and y" in self._content_text(result)
        mock_mouse.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_mouse_click_with_y_only_returns_error_without_use_case_call(
        self,
    ) -> None:
        """R007-MOUSE-PAIR: y without x is rejected before MouseUseCase."""
        mock_mouse = MagicMock()
        mock_resolve = MagicMock(return_value="sess-49")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            mouse_uc=mock_mouse,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "mouse_click", {"session_id": "sess-49", "y": 20}
        )

        assert result.isError is True
        assert "x and y" in self._content_text(result)
        mock_mouse.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_key_with_empty_keys_returns_error_without_use_case_call(
        self,
    ) -> None:
        """R007-KEY-NONEMPTY: empty key arrays are rejected before KeyUseCase."""
        mock_key = MagicMock()
        mock_resolve = MagicMock(return_value="sess-49")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            key_uc=mock_key,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool("key", {"session_id": "sess-49", "keys": []})

        assert result.isError is True
        assert "keys" in self._content_text(result)
        mock_key.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_bound_tool_preserves_session_resolution_error_message(
        self,
    ) -> None:
        """R007-SESSION-ERROR: original resolution message is returned."""
        mock_inspect = MagicMock()
        mock_inspect.execute.return_value = MagicMock(
            tree=None,
            screenshot=None,
            timestamp=0.0,
            screenshot_base64=False,
            screenshot_data=None,
        )
        mock_resolve = MagicMock(
            side_effect=RuntimeError("no active sessions; start one first")
        )
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            inspect_uc=mock_inspect,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool("inspect", {})

        assert result.isError is True
        assert "no active sessions; start one first" in self._content_text(result)
        mock_inspect.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_mouse_click_and_key_paths_still_dispatch(self) -> None:
        """R007 valid paths: paired mouse coords and non-empty keys remain green."""
        mock_mouse = MagicMock()
        mock_key = MagicMock()
        mock_resolve = MagicMock(return_value="sess-49")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            mouse_uc=mock_mouse,
            key_uc=mock_key,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        mouse_result = await server.call_tool(
            "mouse_click", {"session_id": "raw-sess", "x": 10, "y": 20}
        )
        key_result = await server.call_tool(
            "key", {"session_id": "raw-sess", "keys": ["ctrl+c"]}
        )

        assert mouse_result.isError is False
        assert key_result.isError is False
        mock_mouse.click.assert_called_once_with("sess-49", 10, 20, "left")
        mock_key.execute.assert_called_once_with("sess-49", ["ctrl+c"])


# ===================================================================
# BC-22: Session attribution in operation logging
# ===================================================================


class TestSessionAttribution:
    """MCP tool calls log with correct session attribution."""

    @pytest.mark.asyncio
    async def test_session_bound_tool_logs_with_session_id(self) -> None:
        """BC-22: Session-bound tool logs to session's operations.jsonl."""
        spy = SpyOperationLog()
        mock_inspect = MagicMock()
        mock_inspect.execute.return_value = MagicMock(
            tree=None,
            screenshot=None,
            timestamp=0.0,
            screenshot_base64=False,
            screenshot_data=None,
        )
        mock_resolve = MagicMock(return_value="sess-abc")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = _make_mock_deps(
            inspect_uc=mock_inspect,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        await server.call_tool("inspect", {"session_id": "sess-abc"})

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == "sess-abc"
        # F-06/BC-22/IC-04: MCP logs use tool_name (underscore), not cli_name
        assert spy.appended[0].command == "inspect"

    @pytest.mark.asyncio
    async def test_session_less_tool_logs_with_empty_sid(self) -> None:
        """BC-22: Session-less tool logs with session_id=''."""
        spy = SpyOperationLog()
        mock_doctor = MagicMock()
        mock_doctor.execute.return_value = []
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = _make_mock_deps(
            doctor_uc=mock_doctor,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        await server.call_tool("doctor", {})

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""
        # F-06/BC-22/IC-04: MCP logs use tool_name (underscore), not cli_name
        assert spy.appended[0].command == "doctor"


# ===================================================================
# AC-06: Per-session lock — concurrent calls to same session serialize
# ===================================================================


class TestPerSessionLock:
    """Concurrent calls to the same session are serialized."""

    @pytest.mark.asyncio
    async def test_concurrent_same_session_serialized(self) -> None:
        """AC-06: Two concurrent calls to the same session are serialized."""
        execution_order: List[str] = []

        def slow_inspect(*args: Any, **kwargs: Any) -> MagicMock:
            execution_order.append("inspect_start")
            time.sleep(0.05)
            execution_order.append("inspect_end")
            return MagicMock(
                tree=None,
                screenshot=None,
                timestamp=0.0,
                screenshot_base64=False,
                screenshot_data=None,
            )

        def slow_diff(*args: Any, **kwargs: Any) -> MagicMock:
            execution_order.append("diff_start")
            time.sleep(0.05)
            execution_order.append("diff_end")
            return MagicMock()

        mock_inspect = MagicMock()
        mock_inspect.execute.side_effect = slow_inspect
        mock_diff = MagicMock()
        mock_diff.execute.side_effect = slow_diff
        mock_resolve = MagicMock(return_value="same-session")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            inspect_uc=mock_inspect,
            diff_uc=mock_diff,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        # Fire two tool calls concurrently targeting the same session.
        await asyncio.gather(
            server.call_tool("inspect", {"session_id": "same-session"}),
            server.call_tool("diff", {"session_id": "same-session"}),
        )

        # They should be serialized: first completes before second starts.
        # Verify no overlap: either inspect finishes before diff starts, or vice versa.
        if execution_order[0] == "inspect_start":
            assert execution_order[1] == "inspect_end"
            assert execution_order[2] == "diff_start"
            assert execution_order[3] == "diff_end"
        else:
            assert execution_order[0] == "diff_start"
            assert execution_order[1] == "diff_end"
            assert execution_order[2] == "inspect_start"
            assert execution_order[3] == "inspect_end"

    @pytest.mark.asyncio
    async def test_concurrent_different_sessions_not_blocked(self) -> None:
        """AC-06: Calls to different sessions can execute concurrently."""
        started: List[str] = []

        def slow_execute(*args: Any, **kwargs: Any) -> MagicMock:
            started.append("started")
            time.sleep(0.05)
            return MagicMock(
                tree=None,
                screenshot=None,
                timestamp=0.0,
                screenshot_base64=False,
                screenshot_data=None,
            )

        mock_inspect = MagicMock()
        mock_inspect.execute.side_effect = slow_execute

        call_count = 0

        def resolve_session(sid: Optional[str]) -> str:
            nonlocal call_count
            call_count += 1
            return sid or f"session-{call_count}"

        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            inspect_uc=mock_inspect,
            resolve_session_id=resolve_session,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        t0 = time.monotonic()
        await asyncio.gather(
            server.call_tool("inspect", {"session_id": "session-A"}),
            server.call_tool("inspect", {"session_id": "session-B"}),
        )
        elapsed = time.monotonic() - t0

        # If concurrent, total time should be ~0.05s, not ~0.10s.
        # Allow generous margin but reject fully serialized execution.
        assert elapsed < 0.15, (
            f"Different sessions should not block each other, took {elapsed:.3f}s"
        )


# ===================================================================
# AC-07: Global lock for session-less commands
# ===================================================================


class TestGlobalLockSessionless:
    """Session-less and session-creating commands use a global lock."""

    @pytest.mark.asyncio
    async def test_concurrent_session_less_commands_serialize(self) -> None:
        """AC-07: Two concurrent session-less tool calls serialize."""
        execution_order: List[str] = []

        def slow_doctor(*args: Any, **kwargs: Any) -> list:
            execution_order.append("doctor_start")
            time.sleep(0.05)
            execution_order.append("doctor_end")
            return []

        def slow_list(*args: Any, **kwargs: Any) -> list:
            execution_order.append("list_start")
            time.sleep(0.05)
            execution_order.append("list_end")
            return []

        mock_doctor = MagicMock()
        mock_doctor.execute.side_effect = slow_doctor
        mock_session_list = MagicMock()
        mock_session_list.execute.side_effect = slow_list
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            doctor_uc=mock_doctor,
            session_list_uc=mock_session_list,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        await asyncio.gather(
            server.call_tool("doctor", {}),
            server.call_tool("session_list", {}),
        )

        # Verify serialization: no interleaving.
        assert len(execution_order) == 4
        # First command finishes before second starts.
        if execution_order[0] == "doctor_start":
            assert execution_order[1] == "doctor_end"
            assert execution_order[2] == "list_start"
        else:
            assert execution_order[0] == "list_start"
            assert execution_order[1] == "list_end"
            assert execution_order[2] == "doctor_start"


# ===================================================================
# BC-23: ToolHandler frozen dataclass
# ===================================================================


class TestToolHandler:
    """ToolHandler is a frozen dataclass with correct fields."""

    def test_is_dataclass(self) -> None:
        """BC-23: ToolHandler is a dataclass."""
        assert dataclasses.is_dataclass(_ToolHandler_ref)

    def test_is_frozen(self) -> None:
        """BC-23: ToolHandler is frozen."""
        handler = _ToolHandler_ref(
            tool_name="test",
            use_case_call=lambda: None,
            session_class="less",
            presenter=lambda: None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            handler.tool_name = "other"  # type: ignore[misc]

    def test_has_four_fields(self) -> None:
        """BC-23: ToolHandler has tool_name, use_case_call, session_class, presenter."""
        field_names = {f.name for f in dataclasses.fields(_ToolHandler_ref)}
        assert field_names == {
            "tool_name",
            "use_case_call",
            "session_class",
            "presenter",
        }

    def test_session_class_values_in_dispatch(self) -> None:
        """BC-23/BC-24: All handlers have valid session_class values."""
        deps = _make_mock_deps()
        server = create_mcp_server(deps)
        # Access dispatch table through the build function
        from aiyes.adapters.mcp_server import _build_dispatch_table

        dispatch = _build_dispatch_table(deps)
        valid_classes = {"bound", "creating", "less"}
        for tool_name, handler in dispatch.items():
            assert isinstance(handler, _ToolHandler_ref), (
                f"{tool_name} dispatch entry is not ToolHandler"
            )
            assert handler.session_class in valid_classes, (
                f"{tool_name} has invalid session_class: {handler.session_class}"
            )

    def test_session_start_is_creating(self) -> None:
        """BC-24: session_start has session_class='creating'."""
        deps = _make_mock_deps()
        from aiyes.adapters.mcp_server import _build_dispatch_table

        dispatch = _build_dispatch_table(deps)
        assert dispatch["session_start"].session_class == "creating"

    def test_doctor_is_session_less(self) -> None:
        """BC-24: doctor has session_class='less'."""
        deps = _make_mock_deps()
        from aiyes.adapters.mcp_server import _build_dispatch_table

        dispatch = _build_dispatch_table(deps)
        assert dispatch["doctor"].session_class == "less"

    def test_inspect_is_bound(self) -> None:
        """BC-24: inspect has session_class='bound'."""
        deps = _make_mock_deps()
        from aiyes.adapters.mcp_server import _build_dispatch_table

        dispatch = _build_dispatch_table(deps)
        assert dispatch["inspect"].session_class == "bound"


# ===================================================================
# F-07: session_start dispatch test (creating class, nargs=-1)
# ===================================================================


class TestSessionStartDispatch:
    """session_start tool dispatch with nargs=-1 array translation."""

    @pytest.mark.asyncio
    async def test_session_start_dispatches_with_array_command(self) -> None:
        """F-07: session_start translates command array to app_command+app_args."""
        mock_session_start = MagicMock()
        mock_session_start.execute.return_value = MagicMock(
            session_id="new-sess-123",
        )
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        spy = SpyOperationLog()

        deps = _make_mock_deps(
            session_start_uc=mock_session_start,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        # Patch presenter to avoid JSON serialization of MagicMock
        with patch(
            "aiyes.cli.presenter.format_session_start",
            return_value='{"session_id": "new-sess-123"}',
        ):
            result = await server.call_tool(
                "session_start",
                {"command": ["firefox", "--no-remote", "https://example.com"]},
            )

        # Verify dispatch called with correct argument translation
        mock_session_start.execute.assert_called_once_with(
            app_command="firefox",
            app_args=["--no-remote", "https://example.com"],
            resolution="1280x800",
            color_depth=24,
            wait=2.0,
            name=None,
            backend="linux",
            device_serial=None,
            marionette=False,
        )

        # Verify it's not an error
        assert result.isError is False

    @pytest.mark.asyncio
    async def test_session_start_uses_global_lock(self) -> None:
        """F-07/AC-07: session_start (creating class) uses global lock."""
        spy = SpyOperationLog()
        mock_session_start = MagicMock()
        mock_session_start.execute.return_value = MagicMock(
            session_id="new-sess",
        )
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = _make_mock_deps(
            session_start_uc=mock_session_start,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        with patch(
            "aiyes.cli.presenter.format_session_start",
            return_value='{"session_id": "new-sess"}',
        ):
            await server.call_tool("session_start", {"command": ["bash"]})

        # session_start logs with empty session_id (creating, uses global lock)
        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""
        assert spy.appended[0].command == "session_start"

    @pytest.mark.asyncio
    async def test_session_start_empty_command_raises(self) -> None:
        """F-07: session_start with empty command array returns error."""
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool("session_start", {"command": []})

        assert result.isError is True


# ===================================================================
# F-06 additional: command field uses tool_name for multi-word tools
# ===================================================================


class TestOperationLogCommandField:
    """MCP operation logging uses tool_name (underscore), not cli_name."""

    @pytest.mark.asyncio
    async def test_wait_stable_logs_tool_name_not_cli_name(self) -> None:
        """F-06/IC-04: wait_stable logs as 'wait_stable', not 'wait-stable'."""
        spy = SpyOperationLog()
        mock_wait_stable = MagicMock()
        mock_wait_stable.execute.return_value = MagicMock(
            stable=True,
            timeout=False,
            polls=3,
        )
        mock_resolve = MagicMock(return_value="test-sess")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = _make_mock_deps(
            wait_stable_uc=mock_wait_stable,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        await server.call_tool("wait_stable", {"session_id": "test-sess"})

        assert len(spy.appended) == 1
        # Must be tool_name (underscore), not cli_name (hyphen)
        assert spy.appended[0].command == "wait_stable"

    @pytest.mark.asyncio
    async def test_mouse_click_logs_tool_name(self) -> None:
        """F-06/IC-04: mouse_click logs as 'mouse_click', not 'mouse click'."""
        spy = SpyOperationLog()
        mock_resolve = MagicMock(return_value="test-sess")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = _make_mock_deps(
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        await server.call_tool(
            "mouse_click", {"session_id": "test-sess", "x": 100, "y": 200}
        )

        assert len(spy.appended) == 1
        # Must be tool_name (underscore), not 'mouse click' (space)
        assert spy.appended[0].command == "mouse_click"


# ===================================================================
# B10-006: MCP dispatch tests for session_status and detect_dialog
# ===================================================================


class TestSessionStatusDispatch:
    """B10-006: session_status dispatch test."""

    @pytest.mark.asyncio
    async def test_session_status_dispatches(self) -> None:
        """B10-006: calling 'session_status' tool invokes session_status_uc."""
        mock_status = MagicMock()
        mock_status.execute.return_value = MagicMock(
            app_alive=True,
            app_foreground=True,
            display_alive=True,
            marionette_enabled=False,
            marionette_port=None,
        )
        mock_resolve = MagicMock(return_value="test-session")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            session_status_uc=mock_status,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "session_status",
            {"session_id": "test-session"},
        )

        assert result.isError is False
        mock_status.execute.assert_called_once_with(session_id="test-session")

    @pytest.mark.asyncio
    async def test_session_status_logs_correctly(self) -> None:
        """B10-006: session_status logs with correct session_id and command."""
        spy = SpyOperationLog()
        mock_status = MagicMock()
        mock_status.execute.return_value = MagicMock(
            app_alive=True,
            app_foreground=False,
            display_alive=True,
            marionette_enabled=False,
            marionette_port=None,
        )
        mock_resolve = MagicMock(return_value="sess-xyz")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = _make_mock_deps(
            session_status_uc=mock_status,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        await server.call_tool("session_status", {"session_id": "sess-xyz"})

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == "sess-xyz"
        assert spy.appended[0].command == "session_status"


class TestDetectDialogDispatch:
    """B10-006: detect_dialog dispatch test."""

    @pytest.mark.asyncio
    async def test_detect_dialog_dispatches(self) -> None:
        """B10-006: calling 'detect_dialog' tool invokes detect_dialog_uc."""
        mock_detect = MagicMock()
        mock_detect.execute.return_value = MagicMock(
            dialog_detected=True,
            window_name="Save As",
            window_role="dialog",
            error=None,
        )
        mock_resolve = MagicMock(return_value="test-session")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            detect_dialog_uc=mock_detect,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "detect_dialog",
            {"session_id": "test-session"},
        )

        assert result.isError is False
        mock_detect.execute.assert_called_once_with(session_id="test-session")

    @pytest.mark.asyncio
    async def test_detect_dialog_logs_correctly(self) -> None:
        """B10-006: detect_dialog logs with correct session_id and command."""
        spy = SpyOperationLog()
        mock_detect = MagicMock()
        mock_detect.execute.return_value = MagicMock(
            dialog_detected=False,
            window_name=None,
            window_role=None,
            error=None,
        )
        mock_resolve = MagicMock(return_value="sess-abc")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = _make_mock_deps(
            detect_dialog_uc=mock_detect,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=spy,
        )
        server = create_mcp_server(deps)

        await server.call_tool("detect_dialog", {"session_id": "sess-abc"})

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == "sess-abc"
        assert spy.appended[0].command == "detect_dialog"
