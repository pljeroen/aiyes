"""Tests for persistent worker fallback in AT-SPI adapters.

Verifies the key invariant: the persistent worker is a pure performance
optimization. If it fails at any point, behavior must be identical to the
current production code (one-shot subprocess).

See CONTRACT.md section 12.4.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

import aiyes.adapters.atspi_tree_adapter as tree_mod
import aiyes.adapters.atspi_action_adapter as action_mod
import aiyes.adapters.atspi_window_adapter as window_mod
from aiyes.adapters.atspi_tree_adapter import AtSpi2TreeAdapter
from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter
from aiyes.adapters.atspi_window_adapter import AtSpiWindowAdapter
from aiyes.domain.node_id import NodeIdRegistry


# ─── Helpers ──────────────────────────────────────────────────────────


class _FakeSession:
    """Minimal session-like object for adapter calls."""

    def __init__(
        self,
        display: str = ":99",
        atspi_bus_address: str = "unix:abstract=/tmp/dbus-test",
        backend: str = "linux",
    ) -> None:
        self.display = display
        self.atspi_bus_address = atspi_bus_address
        self.backend = backend


def _make_tree_data() -> Dict[str, Any]:
    """Minimal valid tree data from the subprocess worker."""
    return {
        "tree": [
            {
                "id": "n_001",
                "role": "push_button",
                "name": "OK",
                "bounds": [100, 200, 80, 30],
                "states": ["enabled", "visible"],
                "actions": ["click"],
            }
        ],
        "registry": {},
    }


def _make_action_result() -> Dict[str, Any]:
    """Minimal valid action result from the subprocess worker."""
    return {
        "success": True,
        "available_actions": ["click"],
        "node_value": None,
        "node_states": None,
    }


def _make_mock_worker(*, alive: bool = True, send_result: Any = None) -> MagicMock:
    """Create a mock AtSpiWorkerConnection."""
    worker = MagicMock()
    worker.is_alive.return_value = alive
    if send_result is not None:
        worker.send.return_value = send_result
    return worker


def _make_failing_worker(exc: Exception) -> MagicMock:
    """Create a mock worker whose send() raises the given exception."""
    worker = MagicMock()
    worker.is_alive.return_value = True
    worker.send.side_effect = exc
    return worker


def _mock_subprocess_result(data: Dict[str, Any]) -> MagicMock:
    """Create a mock subprocess.run result returning JSON data."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(data)
    mock_result.stderr = ""
    return mock_result


# ─── Tree adapter tests ──────────────────────────────────────────────


class TestTreeAdapterWorkerFallback:
    """AtSpi2TreeAdapter: worker-first with one-shot fallback."""

    def test_tree_adapter_uses_worker_when_alive(self) -> None:
        """get_tree calls worker.send instead of subprocess.run when alive."""
        adapter = AtSpi2TreeAdapter()
        tree_data = _make_tree_data()
        worker = _make_mock_worker(alive=True, send_result=tree_data)
        adapter.set_worker(worker)

        with (
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch.object(tree_mod, "subprocess") as mock_sub,
        ):
            result = adapter.get_tree(_FakeSession())

        worker.send.assert_called_once_with("get_tree")
        mock_sub.run.assert_not_called()
        assert result is not None

    def test_tree_adapter_falls_back_on_worker_death(self) -> None:
        """get_tree uses subprocess.run when worker is dead."""
        adapter = AtSpi2TreeAdapter()
        worker = _make_mock_worker(alive=False)
        adapter.set_worker(worker)

        with (
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch.object(tree_mod, "subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = _mock_subprocess_result(_make_tree_data())
            result = adapter.get_tree(_FakeSession())

        worker.send.assert_not_called()
        mock_sub.run.assert_called_once()
        assert result is not None

    def test_tree_adapter_falls_back_on_worker_error(self) -> None:
        """get_tree uses subprocess.run when worker.send raises RuntimeError."""
        adapter = AtSpi2TreeAdapter()
        worker = _make_failing_worker(RuntimeError("Worker process died"))
        adapter.set_worker(worker)

        with (
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch.object(tree_mod, "subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = _mock_subprocess_result(_make_tree_data())
            result = adapter.get_tree(_FakeSession())

        worker.send.assert_called_once()
        mock_sub.run.assert_called_once()
        assert result is not None

    def test_tree_adapter_no_worker_uses_subprocess(self) -> None:
        """get_tree uses subprocess.run when set_worker(None)."""
        adapter = AtSpi2TreeAdapter()

        with (
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch.object(tree_mod, "subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = _mock_subprocess_result(_make_tree_data())
            result = adapter.get_tree(_FakeSession())

        mock_sub.run.assert_called_once()
        assert result is not None

    def test_tree_adapter_falls_back_on_timeout_error(self) -> None:
        """get_tree falls back on TimeoutError from worker."""
        adapter = AtSpi2TreeAdapter()
        worker = _make_failing_worker(TimeoutError("Worker response timeout"))
        adapter.set_worker(worker)

        with (
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch.object(tree_mod, "subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = _mock_subprocess_result(_make_tree_data())
            result = adapter.get_tree(_FakeSession())

        worker.send.assert_called_once()
        mock_sub.run.assert_called_once()
        assert result is not None


# ─── Action adapter tests ────────────────────────────────────────────


class TestActionAdapterWorkerFallback:
    """AtSpi2ActionAdapter: worker-first with one-shot fallback."""

    def test_action_adapter_uses_worker_when_alive(self) -> None:
        """do_action calls worker.send instead of subprocess.run when alive."""
        adapter = AtSpi2ActionAdapter()
        action_data = _make_action_result()
        worker = _make_mock_worker(alive=True, send_result=action_data)
        adapter.set_worker(worker)

        with patch.object(action_mod, "subprocess") as mock_sub:
            result = adapter.do_action(
                _FakeSession(), node_id="n_001", action_name="click"
            )

        worker.send.assert_called_once_with(
            "do_action",
            node_id="n_001",
            action_name="click",
            value=None,
            registry=None,
        )
        mock_sub.run.assert_not_called()
        assert result.success is True

    def test_action_adapter_falls_back_on_worker_death(self) -> None:
        """do_action uses subprocess.run when worker is dead."""
        adapter = AtSpi2ActionAdapter()
        worker = _make_mock_worker(alive=False)
        adapter.set_worker(worker)

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch.object(action_mod, "subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = _mock_subprocess_result(_make_action_result())
            result = adapter.do_action(
                _FakeSession(), node_id="n_001", action_name="click"
            )

        worker.send.assert_not_called()
        mock_sub.run.assert_called_once()
        assert result.success is True

    def test_action_adapter_falls_back_on_worker_error(self) -> None:
        """do_action uses subprocess.run when worker.send raises."""
        adapter = AtSpi2ActionAdapter()
        worker = _make_failing_worker(RuntimeError("Worker error"))
        adapter.set_worker(worker)

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch.object(action_mod, "subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = _mock_subprocess_result(_make_action_result())
            result = adapter.do_action(
                _FakeSession(), node_id="n_001", action_name="click"
            )

        worker.send.assert_called_once()
        mock_sub.run.assert_called_once()
        assert result.success is True

    def test_action_adapter_sends_registry_data(self) -> None:
        """do_action passes registry mapping to worker when available."""
        adapter = AtSpi2ActionAdapter()
        action_data = _make_action_result()
        worker = _make_mock_worker(alive=True, send_result=action_data)
        adapter.set_worker(worker)

        registry = NodeIdRegistry.from_mapping({"key": "n_001"})

        with patch.object(action_mod, "subprocess") as mock_sub:
            result = adapter.do_action(
                _FakeSession(),
                node_id="n_001",
                action_name="click",
                registry=registry,
            )

        call_kwargs = worker.send.call_args
        assert call_kwargs[1]["registry"] is not None
        mock_sub.run.assert_not_called()


# ─── Window adapter tests ────────────────────────────────────────────


class TestWindowAdapterWorkerFallback:
    """AtSpiWindowAdapter: worker-first with one-shot fallback."""

    def test_window_adapter_uses_worker_when_alive(self) -> None:
        """list_top_level_windows calls worker.send when alive."""
        adapter = AtSpiWindowAdapter()
        worker = _make_mock_worker(
            alive=True,
            send_result=[{"role": "application", "name": "Firefox"}],
        )
        adapter.set_worker(worker)

        with patch.object(window_mod, "subprocess") as mock_sub:
            result = adapter.list_top_level_windows(_FakeSession())

        worker.send.assert_called_once_with("list_windows")
        mock_sub.run.assert_not_called()
        assert len(result) == 1
        assert result[0].name == "Firefox"

    def test_window_adapter_falls_back_on_worker_death(self) -> None:
        """list_top_level_windows uses subprocess.run when worker is dead."""
        adapter = AtSpiWindowAdapter()
        worker = _make_mock_worker(alive=False)
        adapter.set_worker(worker)

        windows_data = [{"role": "application", "name": "Chrome"}]

        with patch.object(window_mod, "subprocess") as mock_sub:
            mock_sub.run.return_value = _mock_subprocess_result(windows_data)
            result = adapter.list_top_level_windows(_FakeSession())

        worker.send.assert_not_called()
        mock_sub.run.assert_called_once()
        assert len(result) == 1
        assert result[0].name == "Chrome"

    def test_window_adapter_falls_back_on_worker_error(self) -> None:
        """list_top_level_windows falls back on worker.send exception."""
        adapter = AtSpiWindowAdapter()
        worker = _make_failing_worker(RuntimeError("Worker died"))
        adapter.set_worker(worker)

        windows_data = [{"role": "application", "name": "Nautilus"}]

        with patch.object(window_mod, "subprocess") as mock_sub:
            mock_sub.run.return_value = _mock_subprocess_result(windows_data)
            result = adapter.list_top_level_windows(_FakeSession())

        worker.send.assert_called_once()
        mock_sub.run.assert_called_once()
        assert len(result) == 1

    def test_window_adapter_no_worker_uses_subprocess(self) -> None:
        """list_top_level_windows uses subprocess.run with no worker."""
        adapter = AtSpiWindowAdapter()

        windows_data = [{"role": "application", "name": "Gedit"}]

        with patch.object(window_mod, "subprocess") as mock_sub:
            mock_sub.run.return_value = _mock_subprocess_result(windows_data)
            result = adapter.list_top_level_windows(_FakeSession())

        mock_sub.run.assert_called_once()
        assert len(result) == 1
