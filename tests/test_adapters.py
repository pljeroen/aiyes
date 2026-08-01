"""Adapter unit tests — AIYES-02 scope.

Tests for concrete adapter implementations (ADAPT-01 through ADAPT-13).
Each adapter is tested in isolation using mocked subprocess/system calls.
Ports are imported to verify structural typing conformance.

Traceability:
  ADAPT-01: XvfbAdapter (test_xvfb_adapter_*)
  ADAPT-02: XDisplayAllocatorAdapter (test_display_allocator_*)
  ADAPT-03: AtSpiBusAdapter (test_atspi_bus_adapter_*)
  ADAPT-04: AtSpi2TreeAdapter (test_atspi_tree_adapter_*)
  ADAPT-05: AtSpi2ActionAdapter (test_atspi_action_adapter_*)
  ADAPT-06: ScrotAdapter (test_scrot_adapter_*)
  ADAPT-07: XdotoolAdapter (test_xdotool_adapter_*)
  ADAPT-08: SubprocessAdapter (test_subprocess_adapter_*)
  ADAPT-09: FileSessionRepository (test_file_session_repo_*)
  ADAPT-10: FileTreeStore (test_file_tree_store_*)
  ADAPT-11: FileScreenshotStore (test_file_screenshot_store_*)
  ADAPT-12: SystemClock (test_system_clock_*)
  ADAPT-13: SystemDependencyCheck (test_system_dep_check_*)
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import (
    ActionPortResult,
    BusStartResult,
    DependencyResult,
    StoredTree,
)
from aiyes.domain.node_id import NodeIdRegistry


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_session(**overrides: Any) -> Session:
    """Create a Session value object with sensible defaults."""
    defaults = dict(
        session_id="test-001",
        display=":99",
        app_pid=12345,
        app_command="gedit",
        app_args=(),
        atspi_bus_pid=12346,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=12344,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
    )
    defaults.update(overrides)
    return Session(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-01: XvfbAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestXvfbAdapterStart:
    """XvfbAdapter.start() launches Xvfb subprocess and returns PID."""

    def test_start_launches_xvfb_with_correct_display(self) -> None:
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 42
            mock_popen.return_value = mock_process

            pid = adapter.start(99, "1280x1024", 24)

            assert pid == 42
            args = mock_popen.call_args
            cmd = args[0][0] if args[0] else args[1].get("args", [])
            # Xvfb must be called with the display number
            assert any(":99" in str(a) for a in cmd)

    def test_start_passes_resolution_to_xvfb(self) -> None:
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 42
            mock_popen.return_value = mock_process

            adapter.start(5, "1920x1080", 16)

            args = mock_popen.call_args
            cmd = args[0][0] if args[0] else args[1].get("args", [])
            cmd_str = " ".join(str(a) for a in cmd)
            assert "1920x1080" in cmd_str

    def test_start_passes_color_depth_to_xvfb(self) -> None:
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 42
            mock_popen.return_value = mock_process

            adapter.start(5, "1280x1024", 16)

            args = mock_popen.call_args
            cmd = args[0][0] if args[0] else args[1].get("args", [])
            cmd_str = " ".join(str(a) for a in cmd)
            assert "16" in cmd_str

    def test_start_rejects_xvfb_exit_with_wslg_socket_diagnostic(self) -> None:
        """An Xvfb Unix-socket startup failure must never yield a usable PID."""
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        failed_process = MagicMock()
        failed_process.pid = 42
        failed_process.poll.return_value = 1

        with patch("subprocess.Popen", return_value=failed_process):
            with pytest.raises(RuntimeError) as exc_info:
                adapter.start(99, "1280x1024", 24)

        message = str(exc_info.value)
        assert "Xvfb" in message
        assert ":99" in message
        assert "WSLg" in message
        assert "unix socket" in message.lower()
        assert adapter._processes == {}


class TestXvfbAdapterStop:
    """XvfbAdapter.stop() terminates Xvfb by PID."""

    def test_stop_terminates_process_by_pid(self) -> None:
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        with patch("os.kill") as mock_kill:
            adapter.stop(42)
            mock_kill.assert_called()
            assert mock_kill.call_args[0][0] == 42

    def test_stop_reaps_tracked_xvfb_process(self) -> None:
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        mock_process = MagicMock()
        mock_process.pid = 42

        with patch("subprocess.Popen", return_value=mock_process):
            adapter.start(99, "1280x1024", 24)

        with patch("os.kill") as mock_kill:
            adapter.stop(42)

        mock_process.terminate.assert_called_once_with()
        mock_process.wait.assert_called_once_with(timeout=3)
        mock_kill.assert_not_called()


class TestXvfbAdapterConformance:
    """XvfbAdapter structurally conforms to DisplayServerPort."""

    def test_implements_display_server_port(self) -> None:
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        # Structural typing: verify method signatures exist
        assert hasattr(adapter, "start")
        assert hasattr(adapter, "stop")

    def test_no_adapter_imports(self) -> None:
        """XvfbAdapter must not import from other adapters or CLI."""
        source = Path("src/aiyes/adapters/xvfb_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("aiyes.adapters."), (
                        f"Illegal adapter cross-import: {node.module}"
                    )
                    assert not node.module.startswith("aiyes.cli"), (
                        f"Illegal CLI import: {node.module}"
                    )


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-02: XDisplayAllocatorAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestDisplayAllocatorAllocate:
    """XDisplayAllocatorAdapter.allocate() probes sockets, not subprocess."""

    def test_allocate_returns_int_gte_1(self) -> None:
        from aiyes.adapters.display_allocator_adapter import XDisplayAllocatorAdapter

        adapter = XDisplayAllocatorAdapter()
        with patch("os.path.exists", return_value=False):
            result = adapter.allocate()
            assert isinstance(result, int)
            assert result >= 1

    def test_allocate_never_returns_zero(self) -> None:
        from aiyes.adapters.display_allocator_adapter import XDisplayAllocatorAdapter

        adapter = XDisplayAllocatorAdapter()
        # Even when display :1 is free, it should return >= 1 (not 0)
        with patch("os.path.exists", return_value=False):
            result = adapter.allocate()
            assert result != 0

    def test_allocate_skips_occupied_displays(self) -> None:
        """When X1 socket exists, allocate() returns >= 2."""
        from aiyes.adapters.display_allocator_adapter import XDisplayAllocatorAdapter

        adapter = XDisplayAllocatorAdapter()

        def exists_side_effect(path: str) -> bool:
            # Display :1 is occupied (socket or lock exists)
            return "/X1" in path or "X1-lock" in path

        with patch("os.path.exists", side_effect=exists_side_effect):
            result = adapter.allocate()
            assert result >= 2

    def test_no_subprocess_import(self) -> None:
        """ADAPT-02/CV-03: subprocess module MUST NOT be imported."""
        source = Path("src/aiyes/adapters/display_allocator_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "subprocess", (
                        "display_allocator_adapter must not import subprocess"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "subprocess", (
                    "display_allocator_adapter must not import from subprocess"
                )

    def test_no_adapter_or_cli_imports(self) -> None:
        source = Path("src/aiyes/adapters/display_allocator_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-03: AtSpiBusAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestAtSpiBusAdapterStart:
    """AtSpiBusAdapter.start_bus() launches dbus-daemon + at-spi-bus-launcher."""

    def _make_mocks(self):
        """Create mock Popen objects for dbus-daemon, launcher, registryd."""
        mock_dbus = MagicMock()
        mock_dbus.pid = 88
        mock_dbus.stdout.readline.return_value = b"unix:abstract=/tmp/dbus-test\n"
        mock_dbus.poll.return_value = None

        mock_launcher = MagicMock()
        mock_launcher.pid = 89
        mock_launcher.poll.return_value = None

        mock_registryd = MagicMock()
        mock_registryd.pid = 90
        mock_registryd.poll.return_value = None

        return mock_dbus, mock_launcher, mock_registryd

    def test_start_bus_launches_subprocess_with_display(self) -> None:
        from aiyes.adapters.atspi_bus_adapter import AtSpiBusAdapter

        adapter = AtSpiBusAdapter()
        mock_dbus, mock_launcher, mock_registryd = self._make_mocks()
        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            result = adapter.start_bus(":99")

            assert isinstance(result, BusStartResult)
            assert result.pid == 88
            assert result.bus_address.startswith("unix:")

    def test_start_bus_sets_display_env(self) -> None:
        from aiyes.adapters.atspi_bus_adapter import AtSpiBusAdapter

        adapter = AtSpiBusAdapter()
        mock_dbus, mock_launcher, mock_registryd = self._make_mocks()
        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ) as mock_popen,
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            adapter.start_bus(":42")

            # Second Popen call is at-spi-bus-launcher — check DISPLAY in its env
            launcher_call = mock_popen.call_args_list[1]
            env = launcher_call[1].get("env", {})
            assert env.get("DISPLAY") == ":42"


class TestAtSpiBusAdapterStop:
    """AtSpiBusAdapter.stop_bus() terminates bus by PID."""

    def test_stop_bus_terminates_pid(self) -> None:
        from aiyes.adapters.atspi_bus_adapter import AtSpiBusAdapter

        adapter = AtSpiBusAdapter()
        with patch("os.kill") as mock_kill:
            adapter.stop_bus(88)
            mock_kill.assert_called()
            assert mock_kill.call_args[0][0] == 88


class TestAtSpiBusAdapterConformance:
    def test_no_adapter_or_cli_imports(self) -> None:
        source = Path("src/aiyes/adapters/atspi_bus_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-04: AtSpi2TreeAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestAtSpi2TreeAdapterGetTree:
    """AtSpi2TreeAdapter.get_tree() uses subprocess isolation (mocked)."""

    def test_get_tree_returns_domain_tree(self) -> None:
        """Unit test with mocked subprocess — must PASS without gi installed."""
        import aiyes.adapters.atspi_tree_adapter as tree_mod
        from aiyes.adapters.atspi_tree_adapter import AtSpi2TreeAdapter

        adapter = AtSpi2TreeAdapter()

        tree_data = {
            "tree": [
                {
                    "id": "n_001",
                    "role": "application",
                    "name": "TestApp",
                    "bounds": [0, 0, 800, 600],
                    "states": [],
                    "actions": [],
                    "children": [],
                }
            ],
            "registry": {
                "('application', 'TestApp', (0,))": "n_001",
            },
        }

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(tree_data)
        mock_result.stderr = ""

        with (
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = adapter.get_tree(":99", "unix:abstract=/tmp/dbus-test")

            assert isinstance(result, AccessibilityTree)

    def test_gi_import_guarded(self) -> None:
        """The gi import must be try/except guarded — importing the module
        must not fail when gi is unavailable."""
        # This test imports the adapter module without gi available.
        # If gi import is not guarded, this will raise ImportError.
        import sys

        # Remove gi from sys.modules if present
        saved = {}
        for key in list(sys.modules.keys()):
            if key == "gi" or key.startswith("gi."):
                saved[key] = sys.modules.pop(key)

        try:
            # Block gi import
            orig_import = (
                __builtins__.__import__
                if hasattr(__builtins__, "__import__")
                else __import__
            )

            def blocked_import(name, *args, **kwargs):
                if name == "gi" or name.startswith("gi."):
                    raise ImportError(f"No module named '{name}'")
                return orig_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=blocked_import):
                # Force re-import
                if "aiyes.adapters.atspi_tree_adapter" in sys.modules:
                    del sys.modules["aiyes.adapters.atspi_tree_adapter"]
                # This should NOT raise
                import aiyes.adapters.atspi_tree_adapter  # noqa: F401
        finally:
            sys.modules.update(saved)


class TestAtSpi2TreeAdapterConformance:
    def test_no_adapter_or_cli_imports(self) -> None:
        _ALLOWED_ADAPTER_UTILS = {
            "aiyes.adapters.atspi_worker_connection",
        }
        source = Path("src/aiyes/adapters/atspi_tree_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in _ALLOWED_ADAPTER_UTILS:
                    continue
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-05: AtSpi2ActionAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestAtSpi2ActionAdapter:
    """AtSpi2ActionAdapter.do_action() uses subprocess isolation (mocked)."""

    def test_do_action_success_returns_action_port_result(self) -> None:
        import aiyes.adapters.atspi_action_adapter as action_mod
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"success": True, "available_actions": ["click"]}
        )
        mock_result.stderr = ""

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = adapter.do_action(
                _make_session(atspi_bus_address="unix:abstract=/tmp/test"),
                "n_001",
                "click",
            )

        assert isinstance(result, ActionPortResult)
        assert result.success is True

    def test_do_action_unsupported_returns_failure(self) -> None:
        import aiyes.adapters.atspi_action_adapter as action_mod
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"success": False, "available_actions": ["click"]}
        )
        mock_result.stderr = ""

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = adapter.do_action(
                _make_session(atspi_bus_address="unix:abstract=/tmp/test"),
                "n_001",
                "press",
            )

        assert isinstance(result, ActionPortResult)
        assert result.success is False
        assert "click" in result.available_actions

    def test_do_action_worker_failure_raises_runtime_error(self) -> None:
        import aiyes.adapters.atspi_action_adapter as action_mod
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = json.dumps(
            {"error": "ModuleNotFoundError", "message": "No module named 'gi'"}
        )

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_result),
            pytest.raises(RuntimeError, match="No module named 'gi'"),
        ):
            adapter.do_action(
                _make_session(atspi_bus_address="unix:abstract=/tmp/test"),
                "n_001",
                "click",
            )

    def test_do_action_invalid_worker_json_raises_runtime_error(self) -> None:
        import aiyes.adapters.atspi_action_adapter as action_mod
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "{invalid"
        mock_result.stderr = ""

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_result),
            pytest.raises(RuntimeError, match="invalid JSON"),
        ):
            adapter.do_action(
                _make_session(atspi_bus_address="unix:abstract=/tmp/test"),
                "n_001",
                "click",
            )

    def test_gi_import_guarded(self) -> None:
        """gi import must be guarded; importing module must not fail."""
        import sys

        saved = {}
        for key in list(sys.modules.keys()):
            if key == "gi" or key.startswith("gi."):
                saved[key] = sys.modules.pop(key)

        try:
            orig_import = __import__

            def blocked_import(name, *args, **kwargs):
                if name == "gi" or name.startswith("gi."):
                    raise ImportError(f"No module named '{name}'")
                return orig_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=blocked_import):
                if "aiyes.adapters.atspi_action_adapter" in sys.modules:
                    del sys.modules["aiyes.adapters.atspi_action_adapter"]
                import aiyes.adapters.atspi_action_adapter  # noqa: F401
        finally:
            sys.modules.update(saved)

    def test_no_adapter_or_cli_imports(self) -> None:
        _ALLOWED_ADAPTER_UTILS = {
            "aiyes.adapters.atspi_worker_connection",
        }
        source = Path("src/aiyes/adapters/atspi_action_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in _ALLOWED_ADAPTER_UTILS:
                    continue
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-06: ScrotAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestScrotAdapterTake:
    """ScrotAdapter.take() tries scrot first, then imagemagick import."""

    def test_take_uses_scrot_first(self) -> None:
        from aiyes.adapters.scrot_adapter import ScrotAdapter

        adapter = ScrotAdapter()
        with (
            patch("subprocess.run") as mock_run,
            patch("shutil.which", return_value="/usr/bin/scrot"),
        ):
            mock_run.return_value = MagicMock(returncode=0)

            result = adapter.take(":99", "/tmp/shot.png")

            # scrot should be the first tool tried
            cmd = mock_run.call_args[0][0]
            assert "scrot" in str(cmd)

    def test_take_falls_back_to_imagemagick(self) -> None:
        from aiyes.adapters.scrot_adapter import ScrotAdapter

        adapter = ScrotAdapter()

        def which_side_effect(name: str) -> Any:
            if name == "scrot":
                return None
            if name == "import":
                return "/usr/bin/import"
            return None

        with (
            patch("subprocess.run") as mock_run,
            patch("shutil.which", side_effect=which_side_effect),
        ):
            mock_run.return_value = MagicMock(returncode=0)

            result = adapter.take(":99", "/tmp/shot.png")

            cmd = mock_run.call_args[0][0]
            assert "import" in str(cmd)

    def test_take_raises_when_no_tool_available(self) -> None:
        from aiyes.adapters.scrot_adapter import ScrotAdapter

        adapter = ScrotAdapter()
        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = FileNotFoundError("scrot not found")

            with pytest.raises(Exception):
                adapter.take(":99", "/tmp/shot.png")

    def test_take_returns_path(self) -> None:
        from aiyes.adapters.scrot_adapter import ScrotAdapter

        adapter = ScrotAdapter()
        with (
            patch("subprocess.run") as mock_run,
            patch("shutil.which", return_value="/usr/bin/scrot"),
        ):
            mock_run.return_value = MagicMock(returncode=0)

            result = adapter.take(":99", "/tmp/shot.png")
            assert isinstance(result, str)

    def test_no_adapter_or_cli_imports(self) -> None:
        source = Path("src/aiyes/adapters/scrot_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-07: XdotoolAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestXdotoolAdapterMouseMove:
    """XdotoolAdapter mouse operations use DISPLAY env var."""

    def test_mouse_move_calls_xdotool_with_display(self) -> None:
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            adapter.mouse_move(":99", 100, 200)

            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(str(a) for a in cmd)
            assert "--display" not in cmd_str
            env = mock_run.call_args.kwargs["env"]
            assert env["DISPLAY"] == ":99"
            assert "mousemove" in cmd_str.lower() or "mouse" in cmd_str.lower()


class TestXdotoolAdapterMouseClick:
    def test_mouse_click_with_coordinates(self) -> None:
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            adapter.mouse_click(":99", 100, 200, "left")

            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(str(a) for a in cmd)
            assert "--display" not in cmd_str
            env = mock_run.call_args.kwargs["env"]
            assert env["DISPLAY"] == ":99"


class TestXdotoolAdapterMouseDrag:
    def test_mouse_drag_specifies_display(self) -> None:
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            adapter.mouse_drag(":99", 10, 20, 100, 200)

            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(str(a) for a in cmd)
            assert "--display" not in cmd_str
            env = mock_run.call_args.kwargs["env"]
            assert env["DISPLAY"] == ":99"


class TestXdotoolAdapterMouseScroll:
    def test_mouse_scroll_specifies_display(self) -> None:
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            adapter.mouse_scroll(":99", "down", 3)

            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(str(a) for a in cmd)
            assert "--display" not in cmd_str
            env = mock_run.call_args.kwargs["env"]
            assert env["DISPLAY"] == ":99"


class TestXdotoolAdapterKey:
    def test_key_sends_key_specs_with_display(self) -> None:
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            adapter.key(":99", ["Return", "Tab"])

            # At least one call per key spec, all with DISPLAY env
            for c in mock_run.call_args_list:
                cmd_str = " ".join(str(a) for a in c[0][0])
                assert "--display" not in cmd_str
                env = c.kwargs["env"]
                assert env["DISPLAY"] == ":99"


class TestXdotoolAdapterTypeText:
    def test_type_text_with_display(self) -> None:
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            adapter.type_text(":99", "hello")

            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(str(a) for a in cmd)
            assert "--display" not in cmd_str
            env = mock_run.call_args.kwargs["env"]
            assert env["DISPLAY"] == ":99"
            assert "hello" in cmd_str


class TestXdotoolAdapterConformance:
    """XdotoolAdapter must use env-based DISPLAY, not --display flag."""

    def test_all_calls_use_env_display_not_flag(self) -> None:
        """Verify that xdotool adapter uses os.environ for DISPLAY, not --display flag."""
        source = Path("src/aiyes/adapters/xdotool_adapter.py").read_text()
        # Must NOT contain --display token anywhere in the source
        assert "--display" not in source, (
            "xdotool_adapter.py must not reference --display anywhere"
        )
        # Must use os.environ to build env dict
        assert "os.environ" in source, (
            "xdotool_adapter.py must use os.environ to build subprocess env"
        )

    def test_no_adapter_or_cli_imports(self) -> None:
        source = Path("src/aiyes/adapters/xdotool_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-08: SubprocessAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestSubprocessAdapterStart:
    def test_start_launches_popen_returns_pid(self) -> None:
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 555
            mock_popen.return_value = mock_process

            pid = adapter.start("gedit", [], None)

            assert pid == 555
            mock_popen.assert_called_once()

    def test_start_with_env(self) -> None:
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        env = {"DISPLAY": ":99"}
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 555
            mock_popen.return_value = mock_process

            adapter.start("gedit", ["file.txt"], env)

            call_kwargs = mock_popen.call_args
            # env should be passed through
            assert call_kwargs[1].get("env") is not None or (
                len(call_kwargs[0]) > 1 and call_kwargs[0][1] is not None
            )


class TestSubprocessAdapterStop:
    def test_stop_terminates_by_pid(self) -> None:
        """Untracked PID stop sends SIGTERM after ownership verification (S-02)."""
        import os
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        my_uid = os.getuid()
        fake_status = f"Name:\tfake\nUid:\t{my_uid}\t{my_uid}\t{my_uid}\t{my_uid}\n"

        with (
            patch(
                "builtins.open",
                MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(
                            return_value=MagicMock(
                                read=MagicMock(return_value=fake_status)
                            )
                        ),
                        __exit__=MagicMock(return_value=False),
                    )
                ),
            ),
            patch("os.kill") as mock_kill,
        ):
            adapter.stop(555)
            mock_kill.assert_called()
            assert mock_kill.call_args[0][0] == 555

    def test_stop_reaps_tracked_subprocess(self) -> None:
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        mock_process = MagicMock()
        mock_process.pid = 555

        with patch("subprocess.Popen", return_value=mock_process):
            adapter.start("gedit", [], None)

        with patch("os.kill") as mock_kill:
            adapter.stop(555)

        mock_process.terminate.assert_called_once_with()
        mock_process.wait.assert_called_once_with(timeout=3)
        mock_kill.assert_not_called()


class TestSubprocessAdapterIsRunning:
    def test_is_running_true_for_alive_process(self) -> None:
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        with patch("os.kill") as mock_kill:
            # os.kill with signal 0 checks existence
            mock_kill.return_value = None
            result = adapter.is_running(555)
            assert result is True

    def test_is_running_false_for_dead_process(self) -> None:
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        with patch("os.kill") as mock_kill:
            mock_kill.side_effect = OSError("No such process")
            result = adapter.is_running(555)
            assert result is False

    def test_is_running_false_for_zombie_process(self) -> None:
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        proc = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
        try:
            time.sleep(0.2)
            assert adapter.is_running(proc.pid) is False
        finally:
            try:
                os.waitpid(proc.pid, 0)
            except ChildProcessError:
                pass


class TestSubprocessAdapterConformance:
    def test_no_adapter_or_cli_imports(self) -> None:
        source = Path("src/aiyes/adapters/subprocess_adapter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-09: FileSessionRepository
# ═══════════════════════════════════════════════════════════════════════


class TestFileSessionRepoSave:
    def test_save_writes_session_json(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        session = _make_session(session_id="abc-123")

        repo.save(session)

        session_file = tmp_path / "abc-123" / "session.json"
        assert session_file.exists()
        data = json.loads(session_file.read_text())
        assert data["session_id"] == "abc-123"

    def test_save_serializes_app_args_as_list(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        session = _make_session(app_args=("--file", "test.txt"))

        repo.save(session)

        session_file = tmp_path / session.session_id / "session.json"
        data = json.loads(session_file.read_text())
        assert isinstance(data["app_args"], list)
        assert data["app_args"] == ["--file", "test.txt"]


class TestFileSessionRepoLoad:
    def test_load_returns_session(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        original = _make_session(session_id="abc-123")
        repo.save(original)

        loaded = repo.load("abc-123")
        assert loaded is not None
        assert isinstance(loaded, Session)
        assert loaded.session_id == "abc-123"

    def test_load_returns_none_for_missing(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        assert repo.load("nonexistent") is None

    def test_load_deserializes_app_args_as_tuple(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        original = _make_session(app_args=("--file", "test.txt"))
        repo.save(original)

        loaded = repo.load(original.session_id)
        assert isinstance(loaded.app_args, tuple)
        assert loaded.app_args == ("--file", "test.txt")


class TestFileSessionRepoLoadAll:
    def test_load_all_returns_all_sessions(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        repo.save(_make_session(session_id="s1"))
        repo.save(_make_session(session_id="s2"))

        all_sessions = repo.load_all()
        assert len(all_sessions) == 2
        ids = {s.session_id for s in all_sessions}
        assert ids == {"s1", "s2"}

    def test_load_all_skips_invalid_directory_names(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        repo.save(_make_session(session_id="s1"))
        (tmp_path / "bad.dir").mkdir()

        all_sessions = repo.load_all()

        assert len(all_sessions) == 1
        assert all_sessions[0].session_id == "s1"

    def test_load_all_skips_corrupt_session_json(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        repo.save(_make_session(session_id="s1"))
        bad_dir = tmp_path / "broken"
        bad_dir.mkdir()
        (bad_dir / "session.json").write_text("{not-json")

        all_sessions = repo.load_all()

        assert len(all_sessions) == 1
        assert all_sessions[0].session_id == "s1"


class TestFileSessionRepoDelete:
    def test_delete_removes_session_json(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        repo.save(_make_session(session_id="del-me"))

        session_file = tmp_path / "del-me" / "session.json"
        assert session_file.exists()

        repo.delete("del-me")
        assert not session_file.exists()
        # Directory may still exist (ADAPT-09: only removes session.json)
        assert (tmp_path / "del-me").exists()


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-10: FileTreeStore
# ═══════════════════════════════════════════════════════════════════════


class TestFileTreeStoreSave:
    def test_save_tree_writes_tree_json(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_tree_store import FileTreeStore

        store = FileTreeStore(base_dir=str(tmp_path))
        tree = AccessibilityTree(
            roots=(
                Node(
                    id="n_001",
                    role="frame",
                    name="Win",
                    bounds=(0, 0, 800, 600),
                    states=("enabled",),
                    actions=(),
                    children=(),
                ),
            )
        )
        registry = NodeIdRegistry()

        store.save_tree("s1", tree, registry)

        tree_file = tmp_path / "s1" / "tree.json"
        assert tree_file.exists()


class TestFileTreeStoreLoad:
    def test_load_tree_returns_stored_tree(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_tree_store import FileTreeStore

        store = FileTreeStore(base_dir=str(tmp_path))
        tree = AccessibilityTree(
            roots=(
                Node(
                    id="n_001",
                    role="frame",
                    name="Win",
                    bounds=(0, 0, 800, 600),
                    states=("enabled",),
                    actions=(),
                    children=(),
                ),
            )
        )

        store.save_tree("s1", tree, None)
        loaded = store.load_tree("s1")

        assert loaded is not None
        assert isinstance(loaded, StoredTree)
        assert isinstance(loaded.tree, AccessibilityTree)

    def test_load_tree_returns_none_for_missing(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_tree_store import FileTreeStore

        store = FileTreeStore(base_dir=str(tmp_path))
        assert store.load_tree("nonexistent") is None

    def test_load_tree_deserializes_nodes_as_domain_objects(
        self, tmp_path: Path
    ) -> None:
        """ADAPT-10: deserialized tree nodes must be Node instances, not dicts."""
        from aiyes.adapters.file_tree_store import FileTreeStore

        store = FileTreeStore(base_dir=str(tmp_path))
        tree = AccessibilityTree(
            roots=(
                Node(
                    id="n_001",
                    role="frame",
                    name="Win",
                    bounds=(0, 0, 800, 600),
                    states=("enabled",),
                    actions=("click",),
                    children=(
                        Node(
                            id="n_002",
                            role="push_button",
                            name="OK",
                            bounds=(10, 10, 80, 30),
                            states=("enabled",),
                            actions=("click",),
                            children=(),
                        ),
                    ),
                ),
            )
        )

        store.save_tree("s1", tree, None)
        loaded = store.load_tree("s1")

        # All nodes must be domain Node objects
        root = loaded.tree.roots[0]
        assert isinstance(root, Node)
        assert isinstance(root.children[0], Node)


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-11: FileScreenshotStore
# ═══════════════════════════════════════════════════════════════════════


class TestFileScreenshotStoreSave:
    def test_save_screenshot_copies_to_session_dir(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path))

        # Create a source file
        source = tmp_path / "raw.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = store.save_screenshot("s1", str(source))

        assert isinstance(result, str)
        assert "s1" in result
        assert "screenshot.png" in result
        assert Path(result).exists()


class TestFileScreenshotStoreGetPath:
    def test_get_screenshot_path_returns_expected(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path))
        path = store.get_screenshot_path("s1")

        assert "s1" in path
        assert "screenshot.png" in path


class TestFileScreenshotStoreReadBytes:
    def test_read_screenshot_bytes_returns_content(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path))
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        source = tmp_path / "raw.png"
        source.write_bytes(content)
        store.save_screenshot("s1", str(source))

        result = store.read_screenshot_bytes("s1")
        assert result == content


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-12: SystemClock
# ═══════════════════════════════════════════════════════════════════════


class TestSystemClockNow:
    def test_now_returns_float(self) -> None:
        from aiyes.adapters.system_clock import SystemClock

        clock = SystemClock()
        result = clock.now()
        assert isinstance(result, float)
        assert result > 0

    def test_now_uses_time_time(self) -> None:
        from aiyes.adapters.system_clock import SystemClock

        clock = SystemClock()
        with patch("time.time", return_value=12345.678):
            assert clock.now() == 12345.678


class TestSystemClockSleep:
    def test_sleep_calls_time_sleep(self) -> None:
        from aiyes.adapters.system_clock import SystemClock

        clock = SystemClock()
        with patch("time.sleep") as mock_sleep:
            clock.sleep(0.5)
            mock_sleep.assert_called_once_with(0.5)


class TestSystemClockConformance:
    def test_no_adapter_or_cli_imports(self) -> None:
        source = Path("src/aiyes/adapters/system_clock.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# ADAPT-13: SystemDependencyCheck
# ═══════════════════════════════════════════════════════════════════════


class TestSystemDependencyCheckSingle:
    def test_check_xvfb_pass(self) -> None:
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with patch("shutil.which", return_value="/usr/bin/Xvfb"):
            result = checker.check("xvfb")
            assert isinstance(result, DependencyResult)
            assert result.status == "pass"

    def test_check_xvfb_fail(self) -> None:
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with patch("shutil.which", return_value=None):
            result = checker.check("xvfb")
            assert result.status == "fail"


class TestSystemDependencyCheckAll:
    def test_check_all_returns_all_results(self) -> None:
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with (
            patch("shutil.which", return_value="/usr/bin/mock"),
            patch.dict(
                "sys.modules", {"gi": MagicMock(), "gi.repository": MagicMock()}
            ),
        ):
            results = checker.check_all()
            assert len(results) == 12
            for r in results:
                assert isinstance(r, DependencyResult)
                assert r.status in ("pass", "fail", "warn")

    def test_check_all_includes_mandatory_deps(self) -> None:
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with (
            patch("shutil.which", return_value="/usr/bin/mock"),
            patch.dict(
                "sys.modules", {"gi": MagicMock(), "gi.repository": MagicMock()}
            ),
        ):
            results = checker.check_all()
            names = {r.name for r in results}
            # Must check all 6 mandatory dependencies
            expected = {
                "xvfb",
                "screenshot_tool",
                "xdotool",
                "at-spi2-core",
                "python3-gi",
                "gir1.2-atspi-2.0",
            }
            assert expected.issubset(names), f"Missing deps: {expected - names}"

    def test_gi_import_error_handled_gracefully(self) -> None:
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with patch("shutil.which", return_value=None):
            # gi not importable
            result = checker.check("python3-gi")
            assert isinstance(result, DependencyResult)
            assert result.status == "fail"


class TestSystemDependencyCheckConformance:
    def test_no_adapter_or_cli_imports(self) -> None:
        source = Path("src/aiyes/adapters/system_dependency_check.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                # AIYES-43: aiyes.adapters.adb_path is the sole permitted exception
                if node.module == "aiyes.adapters.adb_path":
                    continue
                assert not node.module.startswith("aiyes.adapters.")
                assert not node.module.startswith("aiyes.cli")


# ═══════════════════════════════════════════════════════════════════════
# FIX-01: output_formatter node_to_dict must NOT mask password_text
# ═══════════════════════════════════════════════════════════════════════


class TestOutputFormatterNoMasking:
    """FIX-01: domain output_formatter must pass through all values unchanged."""

    def test_node_to_dict_does_not_mask_password_text(self) -> None:
        from aiyes.domain.output_formatter import node_to_dict

        node = Node(
            id="n_001",
            role="password_text",
            name="Password",
            bounds=(0, 0, 200, 30),
            states=("enabled",),
            actions=(),
            value="s3cr3t",
        )

        result = node_to_dict(node)

        # Domain layer MUST NOT mask — raw value passes through
        assert result["value"] == "s3cr3t"
        assert result["value"] != "***"

    def test_node_to_dict_preserves_non_password_values(self) -> None:
        from aiyes.domain.output_formatter import node_to_dict

        node = Node(
            id="n_001",
            role="text",
            name="Username",
            bounds=(0, 0, 200, 30),
            states=("enabled",),
            actions=(),
            value="jeroen",
        )

        result = node_to_dict(node)
        assert result["value"] == "jeroen"


# ═══════════════════════════════════════════════════════════════════════
# FIX-02: Path traversal validation in file adapters (AIYES-02 HIGH)
# ═══════════════════════════════════════════════════════════════════════


class TestFileSessionRepoPathTraversal:
    """FileSessionRepository must reject path traversal session IDs."""

    def test_load_rejects_dotdot(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid characters"):
            repo.load("../../etc")

    def test_load_rejects_slash(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid characters"):
            repo.load("abc/def")

    def test_delete_rejects_traversal(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid characters"):
            repo.delete("../secrets")

    def test_load_rejects_empty(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_session_repository import FileSessionRepository

        repo = FileSessionRepository(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="must not be empty"):
            repo.load("")


class TestFileTreeStorePathTraversal:
    """FileTreeStore must reject path traversal session IDs."""

    def test_save_tree_rejects_dotdot(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_tree_store import FileTreeStore

        store = FileTreeStore(base_dir=str(tmp_path))
        tree = AccessibilityTree(roots=())
        with pytest.raises(ValueError, match="invalid characters"):
            store.save_tree("../../etc", tree, None)

    def test_load_tree_rejects_slash(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_tree_store import FileTreeStore

        store = FileTreeStore(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid characters"):
            store.load_tree("abc/def")

    def test_save_tree_rejects_empty(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_tree_store import FileTreeStore

        store = FileTreeStore(base_dir=str(tmp_path))
        tree = AccessibilityTree(roots=())
        with pytest.raises(ValueError, match="must not be empty"):
            store.save_tree("", tree, None)


class TestFileScreenshotStorePathTraversal:
    """FileScreenshotStore must reject path traversal session IDs."""

    def test_save_screenshot_rejects_dotdot(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid characters"):
            store.save_screenshot("../../etc", "/tmp/fake.png")

    def test_get_screenshot_path_rejects_slash(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid characters"):
            store.get_screenshot_path("abc/def")

    def test_read_screenshot_bytes_rejects_traversal(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid characters"):
            store.read_screenshot_bytes("../secrets")

    def test_save_screenshot_rejects_empty(self, tmp_path: Path) -> None:
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="must not be empty"):
            store.save_screenshot("", "/tmp/fake.png")


# ═══════════════════════════════════════════════════════════════════════
# FIX-03: Node ID stability — action adapter uses persisted registry
# ═══════════════════════════════════════════════════════════════════════


class TestNodeIdRegistryLookup:
    """NodeIdRegistry.lookup_id returns stored (role, name, path) tuples."""

    def test_lookup_existing_id(self) -> None:
        registry = NodeIdRegistry()
        registry.get_or_assign("push_button", "OK", [0, 1])
        registry.get_or_assign("label", "Status", [0, 2])

        result = registry.lookup_id("n_001")
        assert result is not None
        role, name, path = result
        assert role == "push_button"
        assert name == "OK"
        assert path == (0, 1)

    def test_lookup_missing_id_returns_none(self) -> None:
        registry = NodeIdRegistry()
        registry.get_or_assign("push_button", "OK", [0, 1])

        assert registry.lookup_id("n_999") is None

    def test_lookup_after_from_mapping_roundtrip(self) -> None:
        """lookup_id works on registries reconstructed from persisted data."""
        registry = NodeIdRegistry()
        registry.get_or_assign("frame", "Window", [0])
        registry.get_or_assign("push_button", "OK", [0, 0])

        mapping = registry.get_mapping()
        restored = NodeIdRegistry.from_mapping(mapping)

        result = restored.lookup_id("n_002")
        assert result is not None
        role, name, path = result
        assert role == "push_button"
        assert name == "OK"
        assert path == (0, 0)


class TestActionUseCasePassesRegistry:
    """ActionUseCase passes persisted registry to action port."""

    def test_action_passes_stored_registry_to_port(self) -> None:
        """The persisted registry must be forwarded for stable node resolution."""
        from tests.conftest import (
            FakeAccessibilityAction,
            FakeSessionRepository,
            FakeTreeStore,
            make_domain_tree,
        )
        from aiyes.domain.session import Session
        from aiyes.domain.use_cases.action import ActionUseCase

        session = Session(
            session_id="test-s",
            display=":99",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-test",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_action = FakeAccessibilityAction()
        fake_repo = FakeSessionRepository()
        fake_repo.save(session)

        # Save tree with a registry
        registry = NodeIdRegistry()
        registry.get_or_assign("push_button", "OK", [0, 1])
        tree = make_domain_tree()
        fake_tree_store = FakeTreeStore()
        fake_tree_store.save_tree("test-s", tree, registry)

        uc = ActionUseCase(
            action=fake_action,
            session_repo=fake_repo,
            tree_store=fake_tree_store,
        )
        uc.execute(session_id="test-s", node_id="n_002", action_name="click")

        do_calls = [c for c in fake_action.calls if c[0] == "do_action"]
        assert len(do_calls) == 1
        # The 5th element is the registry passed to do_action
        _, _, _, _, passed_registry = do_calls[0][1]
        assert passed_registry is registry

    def test_action_passes_none_registry_when_no_stored_registry(self) -> None:
        """When stored tree has no registry, None is passed."""
        from tests.conftest import (
            FakeAccessibilityAction,
            FakeSessionRepository,
            FakeTreeStore,
            make_domain_tree,
        )
        from aiyes.domain.session import Session
        from aiyes.domain.use_cases.action import ActionUseCase

        session = Session(
            session_id="test-s",
            display=":99",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-test",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_action = FakeAccessibilityAction()
        fake_repo = FakeSessionRepository()
        fake_repo.save(session)

        tree = make_domain_tree()
        fake_tree_store = FakeTreeStore()
        fake_tree_store.save_tree("test-s", tree, None)

        uc = ActionUseCase(
            action=fake_action,
            session_repo=fake_repo,
            tree_store=fake_tree_store,
        )
        uc.execute(session_id="test-s", node_id="n_002", action_name="click")

        do_calls = [c for c in fake_action.calls if c[0] == "do_action"]
        _, _, _, _, passed_registry = do_calls[0][1]
        assert passed_registry is None


class TestActionAdapterRegistryPathResolution:
    """AtSpi2ActionAdapter passes registry to subprocess for path-based node lookup."""

    def test_do_action_with_registry_passes_registry_to_subprocess(self) -> None:
        """When registry is provided, it is serialized and passed to subprocess."""
        import aiyes.adapters.atspi_action_adapter as action_mod
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        # Create a persisted registry mapping n_001 -> ("push_button", "OK", (0, 1))
        registry = NodeIdRegistry()
        registry.get_or_assign("push_button", "OK", [0, 1])

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"success": True, "available_actions": ["click"]}
        )
        mock_result.stderr = ""

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            result = adapter.do_action(
                _make_session(atspi_bus_address="unix:abstract=/tmp/test"),
                "n_001",
                "click",
                registry=registry,
            )

        assert result.success is True
        # Verify registry was passed to subprocess as --registry arg
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
        assert "--registry" in cmd, (
            f"--registry flag not found in subprocess args: {cmd}"
        )

    def test_do_action_without_registry_no_registry_arg(self) -> None:
        """When no registry is provided, --registry is not passed to subprocess."""
        import aiyes.adapters.atspi_action_adapter as action_mod
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"success": False, "available_actions": []})
        mock_result.stderr = ""

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            result = adapter.do_action(
                _make_session(atspi_bus_address="unix:abstract=/tmp/test"),
                "n_001",
                "click",
            )

        assert result.success is False
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
        assert "--registry" not in cmd, (
            f"--registry should not be in args when no registry provided: {cmd}"
        )
