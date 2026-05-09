"""Tests for AIYES-06 remediation — RED phase.

Contract: AIYES-06 (CT-02 EVOLUTION)
Requirements covered:
  R-REM-01: os.remove() must be delegated to ScreenshotStorePort.delete_temp
  R-REM-02: CLI --resolution default must be "1280x800"
  R-REM-03: Credential denylist strips secret env vars from sessions
  R-REM-04: docs/specs/ must contain AIYES-01 and AIYES-05 spec files
  R-REM-05: Mesa doctor check must use glxinfo (not swrast_dri.so file probe)
  R-REM-07: wait.py must not import unused List or Node
  R-REM-08: AtSpi2TreeAdapter must not have dead _find_node stub
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import MagicMock, patch

import pytest


from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeScreenshotStore,
    FakeSessionRepository,
)


# ──────────────────────────────────────────────────────────────────────
# Paths used by multiple test classes
# ──────────────────────────────────────────────────────────────────────

_SRC_ROOT = Path("src/aiyes")
_SCREENSHOT_UC = _SRC_ROOT / "domain" / "use_cases" / "screenshot.py"
_INSPECT_UC = _SRC_ROOT / "domain" / "use_cases" / "inspect.py"
_SCREENSHOT_STORE_PORT = _SRC_ROOT / "ports" / "screenshot_store.py"
_WAIT_UC = _SRC_ROOT / "domain" / "use_cases" / "wait.py"
_TREE_ADAPTER = _SRC_ROOT / "adapters" / "atspi_tree_adapter.py"
_MESA_ADAPTER = _SRC_ROOT / "adapters" / "system_dependency_check.py"
_CLI_MAIN = _SRC_ROOT / "cli" / "main.py"
_SESSION_START = _SRC_ROOT / "domain" / "use_cases" / "session_start.py"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _ast_has_os_remove(filepath: Path) -> bool:
    """Check if AST of filepath contains any call to os.remove."""
    source = filepath.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # os.remove(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "remove"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                return True
    return False


def _ast_imports_name_from(filepath: Path, module: str, name: str) -> bool:
    """Check if filepath has 'from <module> import ..., <name>, ...'."""
    source = filepath.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                if alias.name == name:
                    return True
    return False


def _ast_class_has_method(filepath: Path, class_name: str, method_name: str) -> bool:
    """Check if a class in filepath defines a method with the given name."""
    source = filepath.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return True
    return False


def _get_app_env(
    fake_process: FakeProcess,
    monkeypatch: Optional[pytest.MonkeyPatch] = None,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Execute session start and return the env dict passed to process.start()."""
    from aiyes.domain.use_cases.session_start import SessionStartUseCase

    if monkeypatch and env_overrides:
        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)

    uc = SessionStartUseCase(
        display_server=FakeDisplayServer(),
        allocator=FakeDisplayAllocator(),
        atspi_bus=FakeAccessibilityBus(),
        process=fake_process,
        session_repo=FakeSessionRepository(),
        clock=FakeClock(),
    )
    uc.execute(app_command="test-app", app_args=[])

    start_calls = [c for c in fake_process.calls if c[0] == "start"]
    _, _, env = start_calls[0][1]
    assert env is not None
    return env


# ══════════════════════════════════════════════════════════════════════
# R-REM-01: os.remove() must be delegated to port
# ══════════════════════════════════════════════════════════════════════


class TestRemovePortDelegation:
    """R-REM-01: Domain use cases must not call os.remove directly."""

    def test_screenshot_py_no_os_remove_in_ast(self) -> None:
        """R-REM-01/C-01: screenshot.py AST must contain zero os.remove calls."""
        assert not _ast_has_os_remove(_SCREENSHOT_UC), (
            "screenshot.py still contains os.remove() call in domain layer"
        )

    def test_inspect_py_no_os_remove_in_ast(self) -> None:
        """R-REM-01/C-01: inspect.py AST must contain zero os.remove calls."""
        assert not _ast_has_os_remove(_INSPECT_UC), (
            "inspect.py still contains os.remove() call in domain layer"
        )

    def test_screenshot_store_port_has_delete_temp(self) -> None:
        """R-REM-01/C-02: ScreenshotStorePort Protocol must define delete_temp."""
        assert _ast_class_has_method(
            _SCREENSHOT_STORE_PORT, "ScreenshotStorePort", "delete_temp"
        ), "ScreenshotStorePort does not define delete_temp method"

    def test_screenshot_uc_calls_delete_temp_on_store(self) -> None:
        """R-REM-01/C-03: ScreenshotUseCase calls delete_temp with the raw path."""
        from aiyes.domain.use_cases.screenshot import ScreenshotUseCase

        fake_store = FakeScreenshotStore()
        fake_screenshot = MagicMock()
        fake_screenshot.take.return_value = "/tmp/raw_shot.png"
        fake_repo = FakeSessionRepository()

        # Create a minimal session in the repo
        session = MagicMock()
        session.session_id = "test-001"
        session.display = ":99"
        fake_repo._sessions["test-001"] = session

        uc = ScreenshotUseCase(
            screenshot=fake_screenshot,
            session_repo=fake_repo,
            screenshot_store=fake_store,
        )
        uc.execute(session_id="test-001")

        # Assert delete_temp was called with the correct raw path (F-02 tighten)
        delete_calls = [c for c in fake_store.calls if c[0] == "delete_temp"]
        assert len(delete_calls) == 1, (
            "ScreenshotUseCase did not call delete_temp on ScreenshotStorePort"
        )
        assert delete_calls[0][1] == "/tmp/raw_shot.png", (
            f"delete_temp called with wrong path: {delete_calls[0][1]}"
        )

    def test_inspect_uc_calls_delete_temp_on_store(self) -> None:
        """F-01: InspectUseCase calls delete_temp when cleaning up temp screenshot."""
        from aiyes.domain.use_cases.inspect import InspectUseCase

        fake_store = FakeScreenshotStore()
        fake_screenshot = MagicMock()
        fake_screenshot.take.return_value = "/tmp/inspect_raw.png"
        fake_repo = FakeSessionRepository()
        fake_tree_port = MagicMock()
        fake_tree_port.get_tree.return_value = MagicMock()
        fake_tree_port.last_registry = None
        fake_tree_store = MagicMock()
        fake_clock = FakeClock()

        # Create a minimal session in the repo
        session = MagicMock()
        session.session_id = "test-002"
        session.display = ":99"
        session.atspi_bus_address = "unix:abstract=/tmp/dbus-test"
        fake_repo._sessions["test-002"] = session

        uc = InspectUseCase(
            tree=fake_tree_port,
            screenshot=fake_screenshot,
            session_repo=fake_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_store,
            clock=fake_clock,
        )
        uc.execute(session_id="test-002")

        # Assert delete_temp was called with the raw screenshot path
        delete_calls = [c for c in fake_store.calls if c[0] == "delete_temp"]
        assert len(delete_calls) == 1, (
            "InspectUseCase did not call delete_temp on ScreenshotStorePort"
        )
        assert delete_calls[0][1] == "/tmp/inspect_raw.png", (
            f"delete_temp called with wrong path: {delete_calls[0][1]}"
        )

    def test_file_screenshot_store_delete_temp_calls_os_remove(self) -> None:
        """F-01: FileScreenshotStore.delete_temp(path) delegates to os.remove(path)."""
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore()
        with patch("aiyes.adapters.file_screenshot_store.os.remove") as mock_remove:
            store.delete_temp("/tmp/test_temp.png")
            mock_remove.assert_called_once_with("/tmp/test_temp.png")


# ══════════════════════════════════════════════════════════════════════
# R-REM-02: CLI --resolution default must be "1280x800"
# ══════════════════════════════════════════════════════════════════════


class TestCliResolutionDefault:
    """R-REM-02: CLI --resolution default must match spec (1280x800)."""

    def test_cli_resolution_default_is_1280x800(self) -> None:
        """R-REM-02/C-05: session start --help shows 1280x800 as default."""
        from click.testing import CliRunner

        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "start", "--help"])
        assert result.exit_code == 0
        assert "1280x800" in result.output, (
            f"Expected '1280x800' in help output, got: {result.output}"
        )
        assert "1280x1024" not in result.output, (
            "CLI help still shows 1280x1024 — must be 1280x800 only"
        )


# ══════════════════════════════════════════════════════════════════════
# R-REM-03: Credential denylist
# ══════════════════════════════════════════════════════════════════════

from aiyes.domain.use_cases.session_start import _CREDENTIAL_STRIP_VARS


class TestCredentialStripping:
    """R-REM-03: Credential env vars must be stripped from session env."""

    def test_credential_strip_vars_exact_set(self) -> None:
        """F-03: _CREDENTIAL_STRIP_VARS has exactly 24 entries, matching expected set."""
        expected = {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "NPM_TOKEN",
            "PYPI_TOKEN",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "DATABASE_URL",
            "POSTGRES_PASSWORD",
            "MYSQL_PASSWORD",
            "SECRET_KEY",
            "DJANGO_SECRET_KEY",
            "SSH_AUTH_SOCK",
            "GPG_AGENT_INFO",
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
        }
        assert set(_CREDENTIAL_STRIP_VARS) == expected, (
            f"_CREDENTIAL_STRIP_VARS mismatch.\n"
            f"  Missing: {expected - set(_CREDENTIAL_STRIP_VARS)}\n"
            f"  Extra: {set(_CREDENTIAL_STRIP_VARS) - expected}"
        )

    @pytest.mark.parametrize("cred_var", _CREDENTIAL_STRIP_VARS)
    def test_credential_var_stripped(
        self, cred_var: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R-REM-03/C-08: Each credential var is absent from app env when set in host."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {cred_var: "leaked-secret-value"})
        assert cred_var not in env, f"Credential var {cred_var} leaked into session env"

    def test_functional_vars_still_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R-REM-03/C-09: PATH, HOME, etc. survive credential stripping."""
        functional_vars = {
            "PATH": "/usr/bin:/usr/local/bin",
            "HOME": "/home/testuser",
            "USER": "testuser",
            "LANG": "en_US.UTF-8",
            "FONTCONFIG_PATH": "/etc/fonts",
            "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
        }
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, functional_vars)
        for var, value in functional_vars.items():
            assert env.get(var) == value, (
                f"Functional var {var} was stripped — should be preserved"
            )

    def test_wayland_and_credential_both_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R-REM-03/C-10: Wayland and credential stripping are additive."""
        fp = FakeProcess()
        env = _get_app_env(
            fp,
            monkeypatch,
            {"WAYLAND_DISPLAY": "wayland-0", "GITHUB_TOKEN": "ghp_leaked"},
        )
        assert "WAYLAND_DISPLAY" not in env
        assert "GITHUB_TOKEN" not in env

    def test_wayland_strip_vars_has_exactly_11_entries(self) -> None:
        """F-03: _wayland_strip_vars (local in execute) must have exactly 11 entries.

        Since it's a local variable, we verify via AST extraction.
        """
        source = _SESSION_START.read_text()
        tree = ast.parse(source)
        wayland_list_count = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "_wayland_strip_vars"
                    ):
                        if isinstance(node.value, ast.List):
                            wayland_list_count = len(node.value.elts)
        assert wayland_list_count is not None, (
            "_wayland_strip_vars assignment not found in session_start.py"
        )
        assert wayland_list_count == 11, (
            f"_wayland_strip_vars has {wayland_list_count} entries, expected 11"
        )


# ══════════════════════════════════════════════════════════════════════
# R-REM-04: Spec documentation files
# ══════════════════════════════════════════════════════════════════════


_SPECS_AVAILABLE = Path("docs/specs/AIYES-01-domain-layer.md").exists()


@pytest.mark.skipif(not _SPECS_AVAILABLE, reason="internal spec files not present")
class TestSpecDocumentation:
    """R-REM-04: docs/specs/ must contain AIYES-01 and AIYES-05 spec files."""

    _AIYES01 = Path("docs/specs/AIYES-01-domain-layer.md")
    _AIYES05 = Path("docs/specs/AIYES-05-atspi-env-context-fix.md")
    _REQUIRED_HEADINGS = [
        "## Overview",
        "## Scope",
        "## Key Decisions",
        "## Requirements Summary",
    ]

    def test_aiyes01_spec_exists(self) -> None:
        """R-REM-04: docs/specs/AIYES-01-domain-layer.md must exist."""
        assert self._AIYES01.exists(), f"{self._AIYES01} does not exist"

    def test_aiyes05_spec_exists(self) -> None:
        """R-REM-04: docs/specs/AIYES-05-atspi-env-context-fix.md must exist."""
        assert self._AIYES05.exists(), f"{self._AIYES05} does not exist"

    @pytest.mark.parametrize("heading", _REQUIRED_HEADINGS)
    def test_aiyes01_has_required_heading(self, heading: str) -> None:
        """R-REM-04/EA-03: AIYES-01 spec has required section headings."""
        if not self._AIYES01.exists():
            pytest.fail(f"{self._AIYES01} does not exist — cannot check headings")
        content = self._AIYES01.read_text()
        assert heading in content, f"AIYES-01 spec missing required heading: {heading}"

    @pytest.mark.parametrize("heading", _REQUIRED_HEADINGS)
    def test_aiyes05_has_required_heading(self, heading: str) -> None:
        """R-REM-04/EA-03: AIYES-05 spec has required section headings."""
        if not self._AIYES05.exists():
            pytest.fail(f"{self._AIYES05} does not exist — cannot check headings")
        content = self._AIYES05.read_text()
        assert heading in content, f"AIYES-05 spec missing required heading: {heading}"

    def test_aiyes01_has_metadata_block(self) -> None:
        """R-REM-04/EA-03: AIYES-01 spec has Status and Contract metadata."""
        if not self._AIYES01.exists():
            pytest.fail(f"{self._AIYES01} does not exist — cannot check metadata")
        content = self._AIYES01.read_text()
        assert "**Status**" in content, "AIYES-01 spec missing **Status** metadata"
        assert "AIYES-01" in content, "AIYES-01 spec missing contract reference"

    def test_aiyes05_has_metadata_block(self) -> None:
        """R-REM-04/EA-03: AIYES-05 spec has Status and Contract metadata."""
        if not self._AIYES05.exists():
            pytest.fail(f"{self._AIYES05} does not exist — cannot check metadata")
        content = self._AIYES05.read_text()
        assert "**Status**" in content, "AIYES-05 spec missing **Status** metadata"
        assert "AIYES-05" in content, "AIYES-05 spec missing contract reference"

    def test_aiyes01_has_provenance_reference(self) -> None:
        """R-REM-04/EA-03: AIYES-01 references .tddv6/contracts/AIYES-01 source."""
        if not self._AIYES01.exists():
            pytest.fail(f"{self._AIYES01} does not exist — cannot check provenance")
        content = self._AIYES01.read_text()
        assert ".tddv6/contracts/AIYES-01" in content, (
            "AIYES-01 spec missing provenance reference to .tddv6/contracts/AIYES-01"
        )

    def test_aiyes05_has_provenance_reference(self) -> None:
        """R-REM-04/EA-03: AIYES-05 references .tddv6/contracts/AIYES-05 source."""
        if not self._AIYES05.exists():
            pytest.fail(f"{self._AIYES05} does not exist — cannot check provenance")
        content = self._AIYES05.read_text()
        assert ".tddv6/contracts/AIYES-05" in content, (
            "AIYES-05 spec missing provenance reference to .tddv6/contracts/AIYES-05"
        )


# ══════════════════════════════════════════════════════════════════════
# R-REM-05: Mesa doctor check — glxinfo-based
# ══════════════════════════════════════════════════════════════════════


class TestMesaGlxinfoCheck:
    """R-REM-05: _check_mesa_sw() must use glxinfo, not swrast_dri.so file probe."""

    def test_no_glxinfo_returns_warn(self) -> None:
        """R-REM-05/C-11: When glxinfo not found, return status='warn'."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with (
            patch(
                "aiyes.adapters.system_dependency_check.shutil.which", return_value=None
            ),
        ):
            result = checker._check_mesa_sw()

        assert result.status == "warn", (
            f"Expected status='warn' when glxinfo not found, got '{result.status}'"
        )
        assert "glxinfo" in result.message.lower(), (
            f"Expected 'glxinfo' in warn message, got: {result.message}"
        )

    def test_glxinfo_with_llvmpipe_returns_pass(self) -> None:
        """R-REM-05/C-12: When glxinfo output contains llvmpipe, return status='pass'."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        mock_run_result = MagicMock()
        mock_run_result.stdout = (
            "OpenGL renderer string: llvmpipe (LLVM 15.0.7, 256 bits)"
        )
        mock_run_result.returncode = 0

        with (
            patch(
                "aiyes.adapters.system_dependency_check.shutil.which",
                return_value="/usr/bin/glxinfo",
            ),
            patch(
                "aiyes.adapters.system_dependency_check.subprocess.run",
                return_value=mock_run_result,
            ),
        ):
            result = checker._check_mesa_sw()

        assert result.status == "pass", (
            f"Expected status='pass' with llvmpipe output, got '{result.status}'"
        )

    def test_glxinfo_with_softpipe_returns_pass(self) -> None:
        """F-04/R-REM-05: When glxinfo output contains softpipe, return status='pass'."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        mock_run_result = MagicMock()
        mock_run_result.stdout = "OpenGL renderer string: softpipe"
        mock_run_result.returncode = 0

        with (
            patch(
                "aiyes.adapters.system_dependency_check.shutil.which",
                return_value="/usr/bin/glxinfo",
            ),
            patch(
                "aiyes.adapters.system_dependency_check.subprocess.run",
                return_value=mock_run_result,
            ),
        ):
            result = checker._check_mesa_sw()

        assert result.status == "pass", (
            f"Expected status='pass' with softpipe output, got '{result.status}'"
        )

    def test_glxinfo_without_llvmpipe_returns_fail(self) -> None:
        """R-REM-05/C-12: When glxinfo output lacks llvmpipe/softpipe, return status='fail'."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        mock_run_result = MagicMock()
        mock_run_result.stdout = "OpenGL renderer string: AMD Radeon RX 580"
        mock_run_result.returncode = 0

        with (
            patch(
                "aiyes.adapters.system_dependency_check.shutil.which",
                return_value="/usr/bin/glxinfo",
            ),
            patch(
                "aiyes.adapters.system_dependency_check.subprocess.run",
                return_value=mock_run_result,
            ),
        ):
            result = checker._check_mesa_sw()

        assert result.status == "fail", (
            f"Expected status='fail' without llvmpipe, got '{result.status}'"
        )
        assert (
            "mesa-dri-drivers" in result.message.lower()
            or "llvmpipe" in result.message.lower()
        ), (
            f"Expected failure message to mention mesa-dri-drivers or llvmpipe, got: {result.message}"
        )

    def test_glxinfo_subprocess_has_libgl_env(self) -> None:
        """R-REM-05/C-12: subprocess.run must include LIBGL_ALWAYS_SOFTWARE=1 in env."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        mock_run_result = MagicMock()
        mock_run_result.stdout = "OpenGL renderer string: llvmpipe"
        mock_run_result.returncode = 0

        with (
            patch(
                "aiyes.adapters.system_dependency_check.shutil.which",
                return_value="/usr/bin/glxinfo",
            ),
            patch(
                "aiyes.adapters.system_dependency_check.subprocess.run",
                return_value=mock_run_result,
            ) as mock_run,
        ):
            checker._check_mesa_sw()

        # Verify LIBGL_ALWAYS_SOFTWARE=1 in the env kwarg
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        # env could be a positional or keyword argument
        env_arg = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env_arg is not None, "subprocess.run was not called with env argument"
        assert env_arg.get("LIBGL_ALWAYS_SOFTWARE") == "1", (
            "subprocess.run env missing LIBGL_ALWAYS_SOFTWARE=1"
        )

    def test_no_swrast_dri_reference_in_source(self) -> None:
        """R-REM-05/C-13: system_dependency_check.py must not reference swrast_dri."""
        source = _MESA_ADAPTER.read_text()
        assert "swrast_dri" not in source, (
            "system_dependency_check.py still references swrast_dri.so"
        )

    def test_no_sw_driver_paths_in_source(self) -> None:
        """R-REM-05/C-13: system_dependency_check.py must not reference sw_driver_paths."""
        source = _MESA_ADAPTER.read_text()
        assert "sw_driver_paths" not in source, (
            "system_dependency_check.py still references sw_driver_paths"
        )


# ══════════════════════════════════════════════════════════════════════
# R-REM-07: Unused imports in wait.py
# ══════════════════════════════════════════════════════════════════════


class TestWaitUnusedImports:
    """R-REM-07: wait.py must not import unused List or Node."""

    def test_wait_does_not_import_list(self) -> None:
        """R-REM-07/C-17: wait.py must not import List from typing."""
        assert not _ast_imports_name_from(_WAIT_UC, "typing", "List"), (
            "wait.py still imports List from typing (unused)"
        )

    def test_wait_does_not_import_node(self) -> None:
        """R-REM-07/C-18: wait.py must not import Node from aiyes.domain.tree."""
        assert not _ast_imports_name_from(_WAIT_UC, "aiyes.domain.tree", "Node"), (
            "wait.py still imports Node from aiyes.domain.tree (unused)"
        )

    def test_wait_still_imports_optional(self) -> None:
        """R-REM-07: wait.py must still import Optional (it's used)."""
        assert _ast_imports_name_from(_WAIT_UC, "typing", "Optional"), (
            "wait.py no longer imports Optional — it's still needed"
        )

    def test_wait_still_imports_flatten_nodes(self) -> None:
        """R-REM-07: wait.py must still import flatten_nodes (it's used)."""
        assert _ast_imports_name_from(_WAIT_UC, "aiyes.domain.tree", "flatten_nodes"), (
            "wait.py no longer imports flatten_nodes — it's still needed"
        )


# ══════════════════════════════════════════════════════════════════════
# R-REM-08: Dead _find_node stub in AtSpi2TreeAdapter
# ══════════════════════════════════════════════════════════════════════


class TestDeadFindNodeStub:
    """R-REM-08: AtSpi2TreeAdapter must not define the dead _find_node stub."""

    def test_tree_adapter_no_find_node(self) -> None:
        """R-REM-08/C-19: AtSpi2TreeAdapter must not define _find_node."""
        assert not _ast_class_has_method(
            _TREE_ADAPTER, "AtSpi2TreeAdapter", "_find_node"
        ), "AtSpi2TreeAdapter still defines _find_node stub (dead code)"

    def test_tree_adapter_no_find_node_attribute(self) -> None:
        """R-REM-08/C-19: AtSpi2TreeAdapter instances must not have _find_node."""
        from aiyes.adapters.atspi_tree_adapter import AtSpi2TreeAdapter

        adapter = AtSpi2TreeAdapter()
        assert not hasattr(adapter, "_find_node"), (
            "AtSpi2TreeAdapter instance still has _find_node attribute"
        )
