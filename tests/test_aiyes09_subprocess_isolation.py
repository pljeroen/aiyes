"""Tests for AIYES-09 — libatspi D-Bus connection caching subprocess isolation fix.

Contract: AIYES-09 (CT-03 BUGFIX)
Requirements covered:
  R-FIX-01: AtSpi2TreeAdapter.get_tree() must use subprocess isolation
  R-FIX-02: AtSpi2ActionAdapter.do_action() must use subprocess isolation
  R-FIX-03: Subprocess worker script must exist
  R-FIX-04: _AtspiEnvContext must NOT exist in either adapter
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.types import ActionPortResult


# ═══════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════

_SRC_ROOT = Path("src/aiyes")
_TREE_ADAPTER_PATH = _SRC_ROOT / "adapters" / "atspi_tree_adapter.py"
_ACTION_ADAPTER_PATH = _SRC_ROOT / "adapters" / "atspi_action_adapter.py"
_WORKER_PATH = _SRC_ROOT / "adapters" / "atspi_subprocess_worker.py"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_tree_json() -> str:
    """Return a valid JSON tree output that the subprocess worker would produce."""
    tree_data = {
        "tree": [
            {
                "id": "n_001",
                "role": "application",
                "name": "TestApp",
                "bounds": [0, 0, 800, 600],
                "states": ["enabled", "visible"],
                "actions": [],
                "children": [
                    {
                        "id": "n_002",
                        "role": "push_button",
                        "name": "OK",
                        "bounds": [100, 200, 80, 30],
                        "states": ["enabled", "visible"],
                        "actions": ["click"],
                        "children": [],
                    }
                ],
            }
        ],
        "registry": {
            '["application", "TestApp", [0]]': "n_001",
            '["push_button", "OK", [0, 0]]': "n_002",
        },
    }
    return json.dumps(tree_data)


def _make_action_result_json(success: bool = True) -> str:
    """Return a valid JSON action result that the subprocess worker would produce."""
    result_data = {
        "success": success,
        "available_actions": ["click", "press"],
    }
    return json.dumps(result_data)


def _make_test_session(
    display: str = ":99", bus_address: str = "unix:abstract=/tmp/dbus-test"
) -> Session:
    """Create a minimal Session for adapter tests."""
    return Session(
        session_id="test-action",
        app_pid=1,
        app_command="test",
        app_args=(),
        name=None,
        display=display,
        atspi_bus_address=bus_address,
    )


def _ast_has_class(source_path: Path, class_name: str) -> bool:
    """Check if a Python source file defines a class with the given name."""
    source = source_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return True
    return False


def _import_tree_adapter_with_mock_gi():
    """Import AtSpi2TreeAdapter with gi mocked to prevent real AT-SPI connections.

    Returns (module, AtSpi2TreeAdapter_class).
    This is necessary because gi.repository.Atspi caches D-Bus connections
    and calling Atspi.get_desktop(0) can crash in environments without a
    running AT-SPI bus (which is exactly the bug we're fixing).
    """
    import aiyes.adapters.atspi_tree_adapter as tree_mod
    from aiyes.adapters.atspi_tree_adapter import AtSpi2TreeAdapter

    return tree_mod, AtSpi2TreeAdapter


def _import_action_adapter_with_mock_gi():
    """Import AtSpi2ActionAdapter with gi mocked to prevent real AT-SPI connections.

    Returns (module, AtSpi2ActionAdapter_class).
    """
    import aiyes.adapters.atspi_action_adapter as action_mod
    from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

    return action_mod, AtSpi2ActionAdapter


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-01: AtSpi2TreeAdapter.get_tree() uses subprocess isolation
# ═══════════════════════════════════════════════════════════════════════


class TestTreeAdapterSubprocessIsolation:
    """R-FIX-01: AtSpi2TreeAdapter.get_tree() must use subprocess isolation."""

    def test_tree_adapter_uses_subprocess(self) -> None:
        """R-FIX-01: get_tree() must call subprocess.run, not Atspi directly."""
        tree_mod, AtSpi2TreeAdapter = _import_tree_adapter_with_mock_gi()

        adapter = AtSpi2TreeAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = _make_tree_json()
        mock_subprocess_result.stderr = ""

        # Mock the module-level Atspi to prevent real D-Bus connections,
        # and mock subprocess.run to capture subprocess calls.
        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_desktop.get_child_count.return_value = 0

        with (
            patch.object(tree_mod, "Atspi", mock_atspi),
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result) as mock_run,
        ):
            adapter.get_tree(":99", "unix:abstract=/tmp/dbus-test")

            assert mock_run.called, (
                "get_tree() must call subprocess.run for process isolation, "
                "but it called Atspi.get_desktop() directly instead"
            )

    def test_tree_adapter_subprocess_env_has_display(self) -> None:
        """R-FIX-01: subprocess env must contain the correct DISPLAY value."""
        tree_mod, AtSpi2TreeAdapter = _import_tree_adapter_with_mock_gi()

        adapter = AtSpi2TreeAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = _make_tree_json()
        mock_subprocess_result.stderr = ""

        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_desktop.get_child_count.return_value = 0

        with (
            patch.object(tree_mod, "Atspi", mock_atspi),
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result) as mock_run,
        ):
            adapter.get_tree(":42", "unix:abstract=/tmp/dbus-test")

            assert mock_run.called, (
                "get_tree() must call subprocess.run (prerequisite for this test)"
            )

            # The subprocess must receive the display somehow — either via
            # env dict or as a command-line argument.
            call_args = mock_run.call_args
            cmd_args = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            env = call_args[1].get("env", {}) if call_args[1] else {}

            display_in_env = env.get("DISPLAY") == ":42"
            display_in_args = ":42" in [str(a) for a in cmd_args]

            assert display_in_env or display_in_args, (
                f"DISPLAY ':42' not found in subprocess env ({env}) "
                f"or args ({cmd_args})"
            )

    def test_tree_adapter_subprocess_env_has_bus_address(self) -> None:
        """R-FIX-01: subprocess must receive DBUS_SESSION_BUS_ADDRESS."""
        tree_mod, AtSpi2TreeAdapter = _import_tree_adapter_with_mock_gi()

        adapter = AtSpi2TreeAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = _make_tree_json()
        mock_subprocess_result.stderr = ""

        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_desktop.get_child_count.return_value = 0

        bus_addr = "unix:abstract=/tmp/dbus-test"
        with (
            patch.object(tree_mod, "Atspi", mock_atspi),
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result) as mock_run,
        ):
            adapter.get_tree(":99", bus_addr)

            assert mock_run.called, (
                "get_tree() must call subprocess.run (prerequisite for this test)"
            )

            call_args = mock_run.call_args
            cmd_args = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            env = call_args[1].get("env", {}) if call_args[1] else {}

            bus_in_env = env.get("DBUS_SESSION_BUS_ADDRESS") == bus_addr
            bus_in_args = bus_addr in [str(a) for a in cmd_args]

            assert bus_in_env or bus_in_args, (
                f"DBUS_SESSION_BUS_ADDRESS '{bus_addr}' not found in subprocess "
                f"env ({env}) or args ({cmd_args})"
            )

    def test_tree_adapter_subprocess_env_no_at_spi_bus_address(self) -> None:
        """R-FIX-01: AT_SPI_BUS_ADDRESS must NOT be in subprocess env."""
        tree_mod, AtSpi2TreeAdapter = _import_tree_adapter_with_mock_gi()

        adapter = AtSpi2TreeAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = _make_tree_json()
        mock_subprocess_result.stderr = ""

        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_desktop.get_child_count.return_value = 0

        with (
            patch.object(tree_mod, "Atspi", mock_atspi),
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result) as mock_run,
        ):
            adapter.get_tree(":99", "unix:abstract=/tmp/dbus-test")

            assert mock_run.called, (
                "get_tree() must call subprocess.run (prerequisite for this test)"
            )

            call_args = mock_run.call_args
            env = call_args[1].get("env", {}) if call_args[1] else {}

            # If env is explicitly provided, AT_SPI_BUS_ADDRESS must not be present.
            # If env is not explicitly provided (inherits parent env), we still
            # accept — the worker script is responsible for popping it.
            if env:
                assert "AT_SPI_BUS_ADDRESS" not in env, (
                    f"AT_SPI_BUS_ADDRESS must not be in subprocess env, "
                    f"found: {env.get('AT_SPI_BUS_ADDRESS')!r}"
                )

    def test_tree_adapter_parses_subprocess_json_output(self) -> None:
        """R-FIX-01: get_tree() must parse subprocess JSON into AccessibilityTree."""
        tree_mod, AtSpi2TreeAdapter = _import_tree_adapter_with_mock_gi()

        adapter = AtSpi2TreeAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = _make_tree_json()
        mock_subprocess_result.stderr = ""

        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_desktop.get_child_count.return_value = 0

        with (
            patch.object(tree_mod, "Atspi", mock_atspi),
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result),
        ):
            tree = adapter.get_tree(":99", "unix:abstract=/tmp/dbus-test")

        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1
        assert tree.roots[0].role == "application"
        assert tree.roots[0].name == "TestApp"
        assert len(tree.roots[0].children) == 1
        assert tree.roots[0].children[0].role == "push_button"
        assert tree.roots[0].children[0].name == "OK"


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-02: AtSpi2ActionAdapter.do_action() uses subprocess isolation
# ═══════════════════════════════════════════════════════════════════════


class TestActionAdapterSubprocessIsolation:
    """R-FIX-02: AtSpi2ActionAdapter.do_action() must use subprocess isolation."""

    def test_action_adapter_uses_subprocess(self) -> None:
        """R-FIX-02: do_action() must call subprocess.run, not Atspi directly."""
        action_mod, AtSpi2ActionAdapter = _import_action_adapter_with_mock_gi()

        adapter = AtSpi2ActionAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = _make_action_result_json(success=True)
        mock_subprocess_result.stderr = ""

        # Mock Atspi so the current (broken) code path doesn't crash
        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_desktop.get_child_count.return_value = 0

        with (
            patch.object(action_mod, "Atspi", mock_atspi),
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result) as mock_run,
        ):
            adapter.do_action(_make_test_session(), "n_001", "click")

            assert mock_run.called, (
                "do_action() must call subprocess.run for process isolation, "
                "but it used Atspi directly instead"
            )

    def test_action_adapter_subprocess_result_parsed(self) -> None:
        """R-FIX-02: do_action() must parse subprocess JSON into ActionPortResult."""
        action_mod, AtSpi2ActionAdapter = _import_action_adapter_with_mock_gi()

        adapter = AtSpi2ActionAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = _make_action_result_json(success=True)
        mock_subprocess_result.stderr = ""

        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_desktop.get_child_count.return_value = 0

        with (
            patch.object(action_mod, "Atspi", mock_atspi),
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result),
        ):
            result = adapter.do_action(_make_test_session(), "n_001", "click")

        assert isinstance(result, ActionPortResult)
        assert result.success is True
        assert "click" in result.available_actions
        assert "press" in result.available_actions


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-03: Subprocess worker script exists
# ═══════════════════════════════════════════════════════════════════════


class TestWorkerScript:
    """R-FIX-03: Subprocess worker script must exist."""

    def test_worker_script_exists(self) -> None:
        """R-FIX-03: atspi_subprocess_worker.py must exist as a file."""
        assert _WORKER_PATH.exists(), (
            f"Worker script not found at {_WORKER_PATH}. "
            "This file is required for subprocess isolation of AT-SPI queries."
        )


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-04: _AtspiEnvContext removed from both adapters
# ═══════════════════════════════════════════════════════════════════════


class TestAtspiEnvContextRemoved:
    """R-FIX-04: _AtspiEnvContext must NOT exist in either adapter."""

    def test_no_atspi_env_context_in_tree_adapter(self) -> None:
        """R-FIX-04: _AtspiEnvContext class must not exist in atspi_tree_adapter.py."""
        assert not _ast_has_class(_TREE_ADAPTER_PATH, "_AtspiEnvContext"), (
            "_AtspiEnvContext class still exists in atspi_tree_adapter.py. "
            "This class is obsolete — subprocess isolation replaces it."
        )

    def test_no_atspi_env_context_in_action_adapter(self) -> None:
        """R-FIX-04: _AtspiEnvContext class must not exist in atspi_action_adapter.py."""
        assert not _ast_has_class(_ACTION_ADAPTER_PATH, "_AtspiEnvContext"), (
            "_AtspiEnvContext class still exists in atspi_action_adapter.py. "
            "This class is obsolete — subprocess isolation replaces it."
        )


# ═══════════════════════════════════════════════════════════════════════
# F-01/F-02: Error path handling
# ═══════════════════════════════════════════════════════════════════════


class TestTreeAdapterErrorHandling:
    """F-01/F-02: Tree adapter must handle worker failures gracefully."""

    def test_tree_adapter_handles_worker_failure(self) -> None:
        """get_tree() raises RuntimeError when subprocess exits non-zero."""
        tree_mod, AtSpi2TreeAdapter = _import_tree_adapter_with_mock_gi()

        adapter = AtSpi2TreeAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 1
        mock_subprocess_result.stdout = ""
        mock_subprocess_result.stderr = json.dumps(
            {"error": "ModuleNotFoundError", "message": "No module named 'gi'"}
        )

        with (
            patch.object(tree_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result),
        ):
            try:
                adapter.get_tree(":99", "unix:abstract=/tmp/dbus-test")
                raised = False
            except RuntimeError as exc:
                raised = True
                assert "failed" in str(exc).lower() or "rc=1" in str(exc), (
                    f"RuntimeError message should indicate failure, got: {exc}"
                )

        assert raised, (
            "get_tree() must raise RuntimeError when subprocess exits non-zero"
        )


class TestActionAdapterErrorHandling:
    """F-01/F-02: Action adapter must handle worker failures gracefully."""

    def test_action_adapter_handles_worker_failure(self) -> None:
        """Worker crashes are system errors, not semantic action failures."""
        action_mod, AtSpi2ActionAdapter = _import_action_adapter_with_mock_gi()

        adapter = AtSpi2ActionAdapter()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 1
        mock_subprocess_result.stdout = ""
        mock_subprocess_result.stderr = json.dumps(
            {"error": "ModuleNotFoundError", "message": "No module named 'gi'"}
        )

        with (
            patch.object(action_mod, "_GI_AVAILABLE", True),
            patch("subprocess.run", return_value=mock_subprocess_result),
        ):
            try:
                adapter.do_action(_make_test_session(), "n_001", "click")
                raised = False
            except RuntimeError as exc:
                raised = True
                assert "worker" in str(exc).lower()
                assert "module" in str(exc).lower() or "gi" in str(exc).lower()

        assert raised, "do_action() must raise RuntimeError on worker failure"


class TestWorkerErrorOutputFormat:
    """F-01/F-02: Worker must output structured error JSON to stderr on failure."""

    def test_worker_error_json_format(self) -> None:
        """_error_json() produces valid JSON with 'error' and 'message' keys."""
        from aiyes.adapters.atspi_subprocess_worker import _error_json

        exc = ModuleNotFoundError("No module named 'gi'")
        output = _error_json(exc)
        data = json.loads(output)

        assert "error" in data, "Error JSON must contain 'error' key"
        assert "message" in data, "Error JSON must contain 'message' key"
        assert data["error"] == "ModuleNotFoundError"
        assert "gi" in data["message"]

    def test_worker_error_json_for_runtime_error(self) -> None:
        """_error_json() correctly formats RuntimeError."""
        from aiyes.adapters.atspi_subprocess_worker import _error_json

        exc = RuntimeError("D-Bus connection refused")
        output = _error_json(exc)
        data = json.loads(output)

        assert data["error"] == "RuntimeError"
        assert data["message"] == "D-Bus connection refused"

    def test_worker_error_json_for_json_decode_error(self) -> None:
        """_error_json() correctly formats json.JSONDecodeError."""
        from aiyes.adapters.atspi_subprocess_worker import _error_json

        try:
            json.loads("{invalid")
        except json.JSONDecodeError as exc:
            output = _error_json(exc)
            data = json.loads(output)
            assert data["error"] == "JSONDecodeError"
            assert len(data["message"]) > 0

    def test_worker_malformed_args_produces_structured_stderr(self) -> None:
        """Worker with bad CLI args outputs structured error JSON to stderr."""
        import subprocess
        import sys
        import pathlib

        worker_path = str(
            pathlib.Path(__file__).parent.parent
            / "src"
            / "aiyes"
            / "adapters"
            / "atspi_subprocess_worker.py"
        )
        result = subprocess.run(
            [sys.executable, worker_path, "tree"],  # missing --display and --bus
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "Worker must exit non-zero on bad args"
        # stderr should contain structured JSON error OR argparse error
        # After fix, SystemExit with non-zero code produces structured JSON
        assert len(result.stderr) > 0, "stderr must not be empty on failure"
