"""AIYES-53 MCP output path safety and bounded argument regressions."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.domain.use_cases.screenshot import ScreenshotResult

_REAL_ASYNCIO_LOCK = asyncio.Lock


def _make_mock_deps(**overrides: Any) -> ServerDependencies:
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    fields.update(overrides)
    return ServerDependencies(**fields)


def _text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


class TestMcpScreenshotOutputSafety:
    @pytest.mark.asyncio
    async def test_screenshot_rejects_existing_output_file_before_use_case(
        self, tmp_path: Path
    ) -> None:
        existing = tmp_path / "existing.png"
        existing.write_bytes(b"keep")
        screenshot_uc = MagicMock()
        deps = _make_mock_deps(
            screenshot_uc=screenshot_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "screenshot", {"session_id": "sess-53", "output": str(existing)}
        )

        assert result.isError is True
        assert "output" in _text(result)
        screenshot_uc.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_screenshot_rejects_symlink_output_before_use_case(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "target.png"
        symlink = tmp_path / "link.png"
        symlink.symlink_to(target)
        screenshot_uc = MagicMock()
        deps = _make_mock_deps(
            screenshot_uc=screenshot_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "screenshot", {"session_id": "sess-53", "output": str(symlink)}
        )

        assert result.isError is True
        assert "symlink" in _text(result)
        screenshot_uc.execute.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "unsafe_output",
        [
            "../aiyes53.png",
            "~/.bashrc",
            "aiyes53-project-output.png",
        ],
    )
    async def test_screenshot_rejects_traversal_sensitive_and_project_paths(
        self, unsafe_output: str
    ) -> None:
        screenshot_uc = MagicMock()
        deps = _make_mock_deps(
            screenshot_uc=screenshot_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "screenshot", {"session_id": "sess-53", "output": unsafe_output}
        )

        assert result.isError is True
        assert "output" in _text(result)
        screenshot_uc.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_screenshot_without_output_keeps_session_storage_dispatch(
        self,
    ) -> None:
        screenshot_uc = MagicMock()
        screenshot_uc.execute.return_value = ScreenshotResult(path="/store/sess-53.png")
        deps = _make_mock_deps(
            screenshot_uc=screenshot_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool("screenshot", {"session_id": "sess-53"})

        assert result.isError is False
        assert screenshot_uc.execute.call_args.kwargs["output_path"] is None

    @pytest.mark.asyncio
    async def test_screenshot_allows_non_existing_temp_output_path(
        self, tmp_path: Path
    ) -> None:
        output = tmp_path / "new.png"
        screenshot_uc = MagicMock()
        screenshot_uc.execute.return_value = ScreenshotResult(path=str(output))
        deps = _make_mock_deps(
            screenshot_uc=screenshot_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "screenshot", {"session_id": "sess-53", "output": str(output)}
        )

        assert result.isError is False
        assert screenshot_uc.execute.call_args.kwargs["output_path"] == str(output)


class TestMcpBoundedArguments:
    @pytest.mark.asyncio
    async def test_wait_rejects_excessive_timeout_before_use_case(self) -> None:
        wait_uc = MagicMock()
        deps = _make_mock_deps(
            wait_uc=wait_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "wait", {"session_id": "sess-53", "role": "*", "timeout": 301.0}
        )

        assert result.isError is True
        assert "timeout" in _text(result)
        wait_uc.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_stable_rejects_excessive_timeout_and_interval(
        self,
    ) -> None:
        for args in (
            {"timeout": 301.0, "interval": 0.5},
            {"timeout": 10.0, "interval": 61.0},
        ):
            wait_stable_uc = MagicMock()
            deps = _make_mock_deps(
                wait_stable_uc=wait_stable_uc,
                resolve_session_id=MagicMock(return_value="sess-53"),
                clock=MagicMock(now=MagicMock(return_value=1000.0)),
                operation_log=MagicMock(),
            )
            server = create_mcp_server(deps)

            result = await server.call_tool(
                "wait_stable", {"session_id": "sess-53", **args}
            )

            assert result.isError is True
            wait_stable_uc.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_type_rejects_excessive_text_and_delay_before_use_case(
        self,
    ) -> None:
        for args in (
            {"text": "x" * 10001, "delay_ms": 0},
            {"text": "ok", "delay_ms": 1001},
        ):
            type_text_uc = MagicMock()
            deps = _make_mock_deps(
                type_text_uc=type_text_uc,
                resolve_session_id=MagicMock(return_value="sess-53"),
                clock=MagicMock(now=MagicMock(return_value=1000.0)),
                operation_log=MagicMock(),
            )
            server = create_mcp_server(deps)

            result = await server.call_tool("type", {"session_id": "sess-53", **args})

            assert result.isError is True
            type_text_uc.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_type_path_still_dispatches(self) -> None:
        type_text_uc = MagicMock()
        deps = _make_mock_deps(
            type_text_uc=type_text_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "type", {"session_id": "sess-53", "text": "hello", "delay_ms": 25}
        )

        assert result.isError is False
        type_text_uc.execute.assert_called_once_with(
            "sess-53", "hello", delay_ms=25
        )

    @pytest.mark.asyncio
    async def test_valid_wait_path_still_dispatches(self) -> None:
        wait_result = MagicMock()
        wait_result.found = True
        wait_result.timeout = False
        wait_result.id = "n_001"
        wait_result.transient = False
        wait_uc = MagicMock()
        wait_uc.execute.return_value = wait_result
        deps = _make_mock_deps(
            wait_uc=wait_uc,
            resolve_session_id=MagicMock(return_value="sess-53"),
            clock=MagicMock(now=MagicMock(return_value=1000.0)),
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool(
            "wait", {"session_id": "sess-53", "role": "*", "timeout": 30.0}
        )

        assert result.isError is False
        wait_uc.execute.assert_called_once()
        assert wait_uc.execute.call_args.kwargs["timeout"] == 30.0


class CountingLock:
    created = 0

    def __init__(self) -> None:
        type(self).created += 1
        self._lock = _REAL_ASYNCIO_LOCK()

    async def __aenter__(self) -> "CountingLock":
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._lock.release()


class TestInvalidSessionLockGrowth:
    @pytest.mark.asyncio
    async def test_invalid_session_resolution_does_not_allocate_session_locks(
        self,
    ) -> None:
        CountingLock.created = 0
        with patch("aiyes.adapters.mcp_server.asyncio.Lock", CountingLock):
            deps = _make_mock_deps(
                inspect_uc=MagicMock(),
                resolve_session_id=MagicMock(side_effect=RuntimeError("missing")),
                clock=MagicMock(now=MagicMock(return_value=1000.0)),
                operation_log=MagicMock(),
            )
            server = create_mcp_server(deps)

            for index in range(5):
                result = await server.call_tool(
                    "inspect", {"session_id": f"missing-{index}"}
                )
                assert result.isError is True

        assert CountingLock.created == 1
