"""AIYES-109 — swipe MCP dispatch handler (CT-03 BUGFIX), RED phase.

`swipe` is advertised as an MCP tool (schema_gen.enumerate_commands lists the
top-level `swipe` CLI command; list_tools() returns 38 tools) but
_build_dispatch_table has NO "swipe" key, so a tools/call name="swipe" falls
through to the "Unknown tool: swipe" isError branch instead of routing to the
SAME GestureUseCase.swipe execution path the `swipe` CLI command invokes
(src/aiyes/cli/main.py:893-934 -> gesture_uc.swipe(sid, x1, y1, x2, y2,
duration_ms)).

These tests MUST FAIL before A9 adds the dispatch entry + handler:
  R1 (dispatch routing)             -> currently "Unknown tool: swipe"; swipe
                                       never reaches GestureUseCase.swipe.
  R2 (arg validation surfacing)     -> currently "Unknown tool: swipe", NOT the
                                       GestureUseCase.swipe validation message.
  DERIVED-PARITY-GUARD              -> advertised - dispatchable == {"swipe"}.

Each test asserts observable post-state (isError flag, routed call capture,
structured error text, set-equality), never a bare return value.

Traceability:
  AIYES-109-R1                    : test_swipe_tools_call_routes_to_gesture_uc_swipe
  AIYES-109-R2                    : test_swipe_invalid_duration_surfaces_structured_tool_error
  AIYES-109-DERIVED-PARITY-GUARD  : test_advertised_tool_names_equal_dispatch_keys
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, List, Tuple
from unittest.mock import MagicMock

import pytest

from aiyes.adapters.mcp_server import (
    ServerDependencies,
    _build_dispatch_table,
    create_mcp_server,
)
from aiyes.cli.main import cli
from aiyes.cli.schema_gen import enumerate_commands

from tests.conftest import FakeSessionRepository


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_mock_deps(**overrides: Any) -> ServerDependencies:
    """Build a ServerDependencies with all fields MagicMock, with overrides."""
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    fields.update(overrides)
    return ServerDependencies(**fields)


def _content_text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


class _RecordingGestureUseCase:
    """Captures GestureUseCase.swipe calls.

    The swipe signature accepts the geometry either positionally or by keyword,
    so this fake records the bound arguments regardless of how the handler
    invokes it (it does not over-constrain A9's call convention).
    """

    def __init__(self) -> None:
        self.swipe_calls: List[Tuple[Any, ...]] = []

    def swipe(
        self,
        session_id: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> Any:
        self.swipe_calls.append((session_id, x1, y1, x2, y2, duration_ms))
        from aiyes.domain.use_cases.gesture import GestureResult

        return GestureResult()


class _FakeGesturePort:
    """Minimal GesturePort double for wiring a REAL GestureUseCase.

    Only swipe is exercised here, and only after the use case's own
    validation passes — the negative-duration guard raises before the port is
    ever touched, so this stub's swipe is a no-op recorder.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []

    def swipe(
        self, session: Any, x1: int, y1: int, x2: int, y2: int, duration_ms: int
    ) -> None:
        self.calls.append((session, x1, y1, x2, y2, duration_ms))


# ═══════════════════════════════════════════════════════════════════════
# AIYES-109-R1 — dispatch routing
# ═══════════════════════════════════════════════════════════════════════


class TestSwipeDispatchRouting:
    """AIYES-109-R1: a tools/call name="swipe" with valid geometry routes to
    the shared GestureUseCase.swipe path and does NOT return "Unknown tool"."""

    @pytest.mark.asyncio
    async def test_swipe_tools_call_routes_to_gesture_uc_swipe(self) -> None:
        """R1: swipe dispatches to deps.gesture_uc.swipe (isError False,
        gesture-result JSON), and the optional duration_ms defaults to 300."""
        recording = _RecordingGestureUseCase()
        mock_resolve = MagicMock(return_value="sess-109")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            gesture_uc=recording,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        # Explicit duration_ms.
        result = await server.call_tool(
            "swipe",
            {
                "session_id": "raw-sess",
                "x1": 100,
                "y1": 200,
                "x2": 100,
                "y2": 700,
                "duration_ms": 250,
            },
        )

        text = _content_text(result)
        # Must NOT be the Unknown-tool fallthrough (the current defect).
        assert "Unknown tool" not in text, (
            f"swipe fell through to the Unknown-tool branch: {text!r}"
        )
        assert result.isError is False, (
            f"swipe tools/call returned isError; content={text!r}"
        )
        # Routed to the shared GestureUseCase.swipe path exactly once, with the
        # dispatch-resolved session_id and the translated geometry/duration.
        assert recording.swipe_calls == [("sess-109", 100, 200, 100, 700, 250)], (
            f"swipe did not route to GestureUseCase.swipe as expected: "
            f"{recording.swipe_calls!r}"
        )
        # Content is the gesture-result JSON ({"status": "ok"}), not an error.
        assert json.loads(text) == {"status": "ok"}

        # Omitting duration_ms must default to 300 (CLI parity: optional arg).
        recording.swipe_calls.clear()
        default_result = await server.call_tool(
            "swipe",
            {"session_id": "raw-sess", "x1": 10, "y1": 20, "x2": 30, "y2": 40},
        )
        default_text = _content_text(default_result)
        assert "Unknown tool" not in default_text, (
            f"swipe (default duration) fell through to Unknown-tool: {default_text!r}"
        )
        assert default_result.isError is False
        assert recording.swipe_calls == [("sess-109", 10, 20, 30, 40, 300)], (
            f"omitted duration_ms must default to 300; got: {recording.swipe_calls!r}"
        )


# ═══════════════════════════════════════════════════════════════════════
# AIYES-109-R2 — argument validation surfaced as a structured tool error
# ═══════════════════════════════════════════════════════════════════════


class TestSwipeArgValidationSurfacing:
    """AIYES-109-R2: an invalid swipe arg surfaces a structured tool error
    (isError=True carrying the message), not an uncaught exception and not
    "Unknown tool"."""

    @pytest.mark.asyncio
    async def test_swipe_invalid_duration_surfaces_structured_tool_error(
        self,
    ) -> None:
        """R2: negative duration_ms is rejected by the REAL GestureUseCase.swipe
        (ValueError) and surfaces as a structured CallToolResult(isError=True)
        carrying the validation message — no exception escapes the call path."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        # REAL use case so the production validation genuinely runs. The
        # duration_ms < 0 guard raises before any session load / port call.
        gesture_uc = GestureUseCase(
            gesture_port=_FakeGesturePort(),
            session_repo=FakeSessionRepository(),
        )
        mock_resolve = MagicMock(return_value="sess-109")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0
        mock_op_log = MagicMock()

        deps = _make_mock_deps(
            gesture_uc=gesture_uc,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=mock_op_log,
        )
        server = create_mcp_server(deps)

        # No exception may propagate out of call_tool.
        result = await server.call_tool(
            "swipe",
            {
                "session_id": "raw-sess",
                "x1": 0,
                "y1": 0,
                "x2": 0,
                "y2": 0,
                "duration_ms": -5,
            },
        )

        text = _content_text(result)
        # RED today: every swipe call returns "Unknown tool: swipe" — the
        # handler that would surface the validation error does not exist yet.
        assert "Unknown tool" not in text, (
            f"swipe fell through to the Unknown-tool branch instead of routing "
            f"to the handler where the arg error would surface: {text!r}"
        )
        # Structured tool error carrying the GestureUseCase.swipe message.
        assert result.isError is True, (
            f"invalid swipe args must yield isError=True; content={text!r}"
        )
        assert "duration_ms" in text and "non-negative" in text, (
            f"structured tool error must carry the GestureUseCase.swipe "
            f"validation message; got: {text!r}"
        )


# ═══════════════════════════════════════════════════════════════════════
# AIYES-109-DERIVED-PARITY-GUARD — advertised set == dispatchable set
# ═══════════════════════════════════════════════════════════════════════


class TestDispatchParityGuard:
    """AIYES-109-DERIVED-PARITY-GUARD: the advertised-tool-name set MUST EQUAL
    the dispatchable-key set.

    The pre-existing count check (test_list_tools_count_is_38) and the
    dispatch-iteration check (test_session_class_values_in_dispatch) each
    validate only ONE side of the parity relation, so an advertised-but-
    undispatched tool (swipe) escapes both. This set-equality cross-check
    closes that class-level gap and prevents the defect from recurring for any
    future CLI command.
    """

    def test_advertised_tool_names_equal_dispatch_keys(self) -> None:
        """Advertised names (schema_gen.enumerate_commands) == dispatch keys
        (_build_dispatch_table). RED now: advertised - dispatchable == {"swipe"}."""
        advertised = {ci.tool_name for ci in enumerate_commands(cli)}
        deps = _make_mock_deps()
        dispatchable = set(_build_dispatch_table(deps).keys())

        assert advertised == dispatchable, (
            "advertised-but-undispatched: "
            f"{sorted(advertised - dispatchable)}; "
            "dispatchable-but-unadvertised: "
            f"{sorted(dispatchable - advertised)}"
        )
