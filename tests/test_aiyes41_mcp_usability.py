"""AIYES-41 — MCP usability fixes: Android type default delay + do role/name params.

Traceability — Acceptance Criteria:
  AIYES-41A: Android type default delay
    AC-41A-01: Android adapter type_text with delay_ms=0 uses per-character mode with 20ms default
    AC-41A-02: Android adapter type_text with explicit delay_ms>0 uses explicit value
    AC-41A-03: Linux xdotool adapter NOT affected (delay_ms=0 still means no delay)
    AC-41A-04: CLI --delay help text documents Android 20ms default
    AC-41A-05: Port InputPort.type_text docstring documents Android default
    AC-41A-06: Use case TypeTextUseCase.execute docstring documents Android default
    AC-41A-07: MCP type tool passes delay_ms through (schema exposes it)

  AIYES-41B: do command separate role + name_pattern
    AC-41B-01: CompoundDoUseCase.execute takes role + name_pattern instead of find_spec
    AC-41B-02: _parse_find_spec removed
    AC-41B-03: CLI do uses --role (required) and --name (optional) instead of --find
    AC-41B-04: MCP _handle_do passes role and name_pattern separately
    AC-41B-05: do with role='View' name_pattern='Home' matches multiline name
    AC-41B-06: do with role='View' name_pattern=None matches all View nodes
    AC-41B-07: MCP tool schema for do exposes role and name_pattern separately
    AC-41B-08: CLI --role and --name help text describes parameters
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.use_cases.compound_do import CompoundDoUseCase
from aiyes.domain.use_cases.type_text import TypeTextUseCase
from aiyes.ports.input import InputPort

from tests.conftest import (
    FakeAccessibilityAction,
    FakeAccessibilityTree,
    FakeClock,
    FakeSessionRepository,
    FakeTreeStore,
    make_domain_tree,
    make_node,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_android_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="android-test",
        app_pid=0,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_linux_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="linux-test",
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
    defaults.update(overrides)
    return Session(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# AIYES-41A: Android type default delay
# ═══════════════════════════════════════════════════════════════════════


class TestAndroidTypeDefaultDelay:
    """AC-41A-01 through AC-41A-03: Android adapter default delay behavior."""

    def test_ac_41a_01_android_default_delay_sends_per_character(self) -> None:
        """AC-41A-01: delay_ms=0 on Android uses per-character with 20ms default."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                with patch(
                    "aiyes.adapters.android_input_adapter.time.sleep"
                ) as mock_sleep:
                    adapter.type_text(session, "abc", delay_ms=0)

        # Per-character mode: 3 adb calls for 3 characters
        assert mock_run.call_count == 3, (
            f"delay_ms=0 on Android should use per-character mode, "
            f"got {mock_run.call_count} calls instead of 3"
        )

        # Sleep with 20ms (0.02s) default between characters (not after last)
        assert mock_sleep.call_count == 2
        for c in mock_sleep.call_args_list:
            assert c[0][0] == pytest.approx(0.02)

    def test_ac_41a_02_android_explicit_delay_uses_explicit_value(self) -> None:
        """AC-41A-02: Explicit delay_ms>0 uses the caller's value."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                with patch(
                    "aiyes.adapters.android_input_adapter.time.sleep"
                ) as mock_sleep:
                    adapter.type_text(session, "ab", delay_ms=50)

        # Per-character mode with explicit delay
        assert mock_run.call_count == 2
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args[0][0] == pytest.approx(0.05)

    def test_ac_41a_03_linux_not_affected(self) -> None:
        """AC-41A-03: Linux xdotool adapter delay_ms=0 still means no delay flag."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session()

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=b""),
        ) as mock_run:
            adapter.type_text(session, "hello", delay_ms=0)

        cmd = mock_run.call_args[0][0]
        # No --delay flag should be present
        assert "--delay" not in cmd


class TestAndroidTypeDocumentation:
    """AC-41A-04 through AC-41A-06: Documentation mentions Android default delay."""

    def test_ac_41a_04_cli_delay_help_mentions_android_default(self) -> None:
        """AC-41A-04: CLI --delay help text documents Android 20ms default."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["type", "--help"])
        assert result.exit_code == 0
        # Help text must mention the Android default
        assert "20" in result.output, (
            f"CLI --delay help should mention 20ms Android default.\n"
            f"Help output:\n{result.output}"
        )

    def test_ac_41a_05_port_docstring_mentions_android_default(self) -> None:
        """AC-41A-05: InputPort.type_text docstring documents Android default."""
        doc = InputPort.type_text.__doc__
        assert doc is not None
        assert "20" in doc, (
            f"InputPort.type_text docstring should mention 20ms Android default.\n"
            f"Docstring: {doc}"
        )

    def test_ac_41a_06_use_case_docstring_mentions_android_default(self) -> None:
        """AC-41A-06: TypeTextUseCase.execute docstring documents Android default."""
        doc = TypeTextUseCase.execute.__doc__
        assert doc is not None
        assert "20" in doc, (
            f"TypeTextUseCase.execute docstring should mention 20ms Android default.\n"
            f"Docstring: {doc}"
        )

    def test_ac_41a_07_mcp_type_schema_has_delay_ms(self) -> None:
        """AC-41A-07: MCP type tool schema exposes delay_ms parameter."""
        from aiyes.cli.main import cli
        from aiyes.cli.schema_gen import enumerate_commands

        commands = enumerate_commands(cli)
        type_cmd = next(c for c in commands if c.tool_name == "type")
        props = type_cmd.json_schema.get("properties", {})
        assert "delay_ms" in props, (
            f"MCP type tool schema must expose delay_ms. Props: {list(props.keys())}"
        )


# ═══════════════════════════════════════════════════════════════════════
# AIYES-41B: do command separate role + name_pattern
# ═══════════════════════════════════════════════════════════════════════


class TestCompoundDoNewSignature:
    """AC-41B-01, AC-41B-02: CompoundDoUseCase takes role + name_pattern."""

    def test_ac_41b_01_execute_takes_role_and_name_pattern(self) -> None:
        """AC-41B-01: execute() signature has role and name_pattern params."""
        sig = inspect.signature(CompoundDoUseCase.execute)
        params = list(sig.parameters.keys())
        assert "role" in params, f"execute() must have 'role' param, got: {params}"
        assert "name_pattern" in params, (
            f"execute() must have 'name_pattern' param, got: {params}"
        )
        assert "find_spec" not in params, (
            f"execute() must NOT have 'find_spec' param, got: {params}"
        )

    def test_ac_41b_02_parse_find_spec_removed(self) -> None:
        """AC-41B-02: _parse_find_spec method is removed."""
        assert not hasattr(CompoundDoUseCase, "_parse_find_spec"), (
            "_parse_find_spec should be removed — role and name_pattern are now separate params"
        )


class TestCompoundDoMatching:
    """AC-41B-05, AC-41B-06: do with separate role + name_pattern matches correctly."""

    def _setup_uc(self, tree: AccessibilityTree) -> CompoundDoUseCase:
        repo = FakeSessionRepository()
        repo.save(_make_linux_session(session_id="test-s"))
        return CompoundDoUseCase(
            tree=FakeAccessibilityTree(tree),
            action=FakeAccessibilityAction(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
            clock=FakeClock(),
        )

    def test_ac_41b_05_multiline_name_match(self) -> None:
        """AC-41B-05: do(role='View', name_pattern='Home') matches 'Home\\nTab 1 of 4'."""
        tree = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_view", "View", "Home\nTab 1 of 4"),
                    ],
                ),
            ]
        )
        uc = self._setup_uc(tree)
        result = uc.execute(
            session_id="test-s",
            role="View",
            name_pattern="Home",
            action_name="click",
        )
        assert result.found is not None
        assert result.found.id == "n_view"
        assert result.error is None

    def test_ac_41b_06_role_only_matches_all(self) -> None:
        """AC-41B-06: do(role='View', name_pattern=None) matches all View nodes."""
        tree = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_v1", "View", "Home\nTab 1 of 4"),
                        make_node("n_v2", "View", "Me\nMe"),
                    ],
                ),
            ]
        )
        uc = self._setup_uc(tree)
        result = uc.execute(
            session_id="test-s",
            role="View",
            name_pattern=None,
            action_name="click",
        )
        # Should find the first View node
        assert result.found is not None
        assert result.found.role == "View"
        assert result.error is None

    def test_41b_flutter_multiline_me_me(self) -> None:
        """Regression: 'Me' matches 'Me\\nMe' via whitespace-normalized substring."""
        tree = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_me", "View", "Me\nMe"),
                    ],
                ),
            ]
        )
        uc = self._setup_uc(tree)
        result = uc.execute(
            session_id="test-s",
            role="View",
            name_pattern="Me",
            action_name="click",
        )
        assert result.found is not None
        assert result.found.id == "n_me"

    def test_41b_flutter_multiline_businesses(self) -> None:
        """Regression: 'Businesses' matches 'Add first\\nMy Businesses\\nAdd first'."""
        tree = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node(
                            "n_biz", "View", "Add first\nMy Businesses\nAdd first"
                        ),
                    ],
                ),
            ]
        )
        uc = self._setup_uc(tree)
        result = uc.execute(
            session_id="test-s",
            role="View",
            name_pattern="Businesses",
            action_name="click",
        )
        assert result.found is not None
        assert result.found.id == "n_biz"


class TestCompoundDoCliParams:
    """AC-41B-03, AC-41B-08: CLI do uses --role and --name."""

    def test_ac_41b_03_cli_has_role_and_name(self) -> None:
        """AC-41B-03: CLI do --help shows --role and --name, not --find."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["do", "--help"])
        assert result.exit_code == 0
        assert "--role" in result.output, f"--role not in help:\n{result.output}"
        assert "--name" in result.output, f"--name not in help:\n{result.output}"
        assert "--find" not in result.output, (
            f"--find should be removed:\n{result.output}"
        )

    def test_ac_41b_08_role_help_text(self) -> None:
        """AC-41B-08: --role and --name have clear help descriptions."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["do", "--help"])
        # Help text should describe what role and name do
        output_lower = result.output.lower()
        assert "role" in output_lower
        assert "name" in output_lower


class TestCompoundDoMcpSchema:
    """AC-41B-04, AC-41B-07: MCP do tool uses role + name_pattern."""

    def test_ac_41b_07_mcp_schema_has_role_and_name_pattern(self) -> None:
        """AC-41B-07: MCP schema for do has role (required) and name_pattern (optional)."""
        from aiyes.cli.main import cli
        from aiyes.cli.schema_gen import enumerate_commands

        commands = enumerate_commands(cli)
        do_cmd = next(c for c in commands if c.tool_name == "do")
        props = do_cmd.json_schema.get("properties", {})
        required = set(do_cmd.json_schema.get("required", []))

        assert "role" in props, (
            f"MCP do schema must have 'role'. Props: {list(props.keys())}"
        )
        assert "role" in required, f"'role' must be required. Required: {required}"
        assert "name_pattern" in props, (
            f"MCP do schema must have 'name_pattern'. Props: {list(props.keys())}"
        )
        assert "find_spec" not in props, (
            f"MCP do schema must NOT have 'find_spec'. Props: {list(props.keys())}"
        )
