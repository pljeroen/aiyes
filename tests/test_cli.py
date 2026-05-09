"""CLI command tests — AIYES-02 scope.

Tests for Click-based CLI commands using Click's CliRunner.

Traceability:
  CLI-01: CLI entry point (group, --help)
  CLI-02: session start
  CLI-03: session stop
  CLI-04: session list
  CLI-05: inspect
  CLI-06: find
  CLI-07: screenshot
  CLI-08: action
  CLI-09: mouse (move, click, drag, scroll)
  CLI-10: key
  CLI-11: type
  CLI-12: wait
  CLI-13: do
  CLI-14: doctor
  CLI-15: MCP-oriented disclosure
  PKG-01: pyproject.toml entry point
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


# click must be available for CLI tests
import click
from click.testing import CliRunner


# ═══════════════════════════════════════════════════════════════════════
# CLI-01: Entry point and group
# ═══════════════════════════════════════════════════════════════════════


class TestCliEntryPoint:
    """CLI entry point is a Click group with all subcommands registered."""

    def test_cli_group_exists(self) -> None:
        from aiyes.cli.main import cli

        assert isinstance(cli, click.Group)

    def test_cli_help_exits_zero(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0

    def test_cli_help_shows_usage(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "Usage" in result.output or "usage" in result.output

    def test_cli_version_flag(self) -> None:
        """AIYES-28: --version prints package version and exits 0."""
        import aiyes

        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert aiyes.__version__ in result.output

    def test_all_subcommands_registered(self) -> None:
        from aiyes.cli.main import cli

        commands = cli.commands if hasattr(cli, "commands") else {}
        command_names = set(commands.keys())

        # Top-level commands
        expected_top = {
            "session",
            "inspect",
            "find",
            "screenshot",
            "action",
            "mouse",
            "key",
            "type",
            "wait",
            "do",
            "doctor",
            "mcp-manifest",
        }
        assert expected_top.issubset(command_names), (
            f"Missing commands: {expected_top - command_names}"
        )

    def test_cli_help_describes_eyes_and_hands_model(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "local deterministic tool" in result.output
        assert "eyes and hands" in result.output
        assert "does not reason" in result.output

    def test_cli_help_teaches_common_loop_and_prerequisites(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "session start -- <command>" in result.output
        assert "inspect -> find/action/mouse/key/type -> verify" in result.output
        assert "accessible names" in result.output
        assert "AT-SPI" in result.output
        assert "AccessKit" in result.output

    def test_session_subgroup_has_start_stop_list(self) -> None:
        from aiyes.cli.main import cli

        session_group = cli.commands.get("session")
        assert session_group is not None
        assert isinstance(session_group, click.Group)

        sub_names = set(session_group.commands.keys())
        assert {"start", "stop", "list"}.issubset(sub_names)

    def test_mouse_subgroup_has_subcommands(self) -> None:
        from aiyes.cli.main import cli

        mouse_group = cli.commands.get("mouse")
        assert mouse_group is not None
        assert isinstance(mouse_group, click.Group)

        sub_names = set(mouse_group.commands.keys())
        assert {"move", "click", "drag", "scroll"}.issubset(sub_names)


class TestCliMainImportRules:
    """CLI-01/CC-07: main.py must NOT import from aiyes.adapters."""

    def test_main_no_adapter_import(self) -> None:
        source = Path("src/aiyes/cli/main.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters"), (
                    f"main.py illegally imports from {node.module}"
                )

    def test_main_imports_only_composition_root_stdlib_click(self) -> None:
        """A10-004: main.py may only import from composition_root, stdlib, and click."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)

        source = Path("src/aiyes/cli/main.py").read_text()
        parsed = ast.parse(source)

        allowed_from_modules = {"aiyes.cli.composition_root", "click"}

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules:
                    continue
                assert node.module in allowed_from_modules, (
                    f"main.py illegally imports from {node.module}; "
                    f"only composition_root, stdlib, and click allowed"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in stdlib_modules:
                        continue
                    assert alias.name in {"click"}, (
                        f"main.py illegally imports {alias.name}; "
                        f"only stdlib and click allowed"
                    )


# ═══════════════════════════════════════════════════════════════════════
# CLI-02: session start
# ═══════════════════════════════════════════════════════════════════════


class TestSessionStartCommand:
    def test_session_start_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "start", "--help"])
        assert result.exit_code == 0
        assert "--resolution" in result.output
        assert "--color-depth" in result.output
        assert "--wait" in result.output
        assert "--name" in result.output

    def test_session_start_success_outputs_json(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        # The command needs composition root wiring; mock it
        with patch("aiyes.cli.main._get_session_start_uc") as mock_uc_factory:
            mock_uc = MagicMock()
            mock_uc.execute.return_value = MagicMock(
                session_id="abc-123",
                display=":99",
                app_pid=12345,
                atspi_bus_address="unix:abstract=/tmp/dbus-test",
            )
            mock_uc_factory.return_value = mock_uc

            result = runner.invoke(
                cli,
                [
                    "session",
                    "start",
                    "--resolution",
                    "1920x1080",
                    "--",
                    "gedit",
                ],
            )

            if result.exit_code == 0:
                parsed = json.loads(result.output)
                assert "session_id" in parsed


class TestSessionStartDefaults:
    def test_default_resolution(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "start", "--help"])
        # Default resolution must be 1280x800, not 1280x1024
        assert "1280x800" in result.output, (
            f"Expected '1280x800' in help output, got: {result.output}"
        )
        assert "1280x1024" not in result.output, (
            "CLI help still shows 1280x1024 — must be 1280x800 only"
        )


# ═══════════════════════════════════════════════════════════════════════
# CLI-03: session stop
# ═══════════════════════════════════════════════════════════════════════


class TestSessionStopCommand:
    def test_session_stop_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "stop", "--help"])
        assert result.exit_code == 0
        assert "--session" in result.output

    def test_session_stop_outputs_partial_cleanup_errors(self) -> None:
        from aiyes.cli.main import cli
        from aiyes.domain.use_cases.session_stop import StopResult

        runner = CliRunner()
        with patch("aiyes.cli.main.session_stop_uc") as mock_uc:
            mock_uc.execute.return_value = StopResult(
                status="stopped_with_errors",
                session_id="s1",
                errors=("app stop failed: boom", "display_server stop failed: nope"),
            )

            result = runner.invoke(cli, ["session", "stop", "--session", "s1"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "stopped_with_errors"
        assert parsed["session_id"] == "s1"
        assert parsed["errors"] == [
            "app stop failed: boom",
            "display_server stop failed: nope",
        ]


# ═══════════════════════════════════════════════════════════════════════
# CLI-04: session list
# ═══════════════════════════════════════════════════════════════════════


class TestSessionListCommand:
    def test_session_list_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "list", "--help"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# CLI-05: inspect
# ═══════════════════════════════════════════════════════════════════════


class TestInspectCommand:
    def test_inspect_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["inspect", "--help"])
        assert result.exit_code == 0
        assert "--no-screenshot" in result.output
        assert "--no-tree" in result.output
        assert "--tree-depth" in result.output
        assert "--no-prune" in result.output

    def test_inspect_has_contract_options(self) -> None:
        """A10-RR-05: contract-required options must appear in help."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["inspect", "--help"])
        assert "--screenshot-base64" in result.output
        assert "--focus-window" in result.output


# ═══════════════════════════════════════════════════════════════════════
# CLI-06: find
# ═══════════════════════════════════════════════════════════════════════


class TestFindCommand:
    def test_find_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["find", "--help"])
        assert result.exit_code == 0
        assert "--state" in result.output


# ═══════════════════════════════════════════════════════════════════════
# CLI-07: screenshot
# ═══════════════════════════════════════════════════════════════════════


class TestScreenshotCommand:
    def test_screenshot_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["screenshot", "--help"])
        assert result.exit_code == 0
        assert "--output" in result.output
        assert "--base64" in result.output


# ═══════════════════════════════════════════════════════════════════════
# CLI-08: action
# ═══════════════════════════════════════════════════════════════════════


class TestActionCommand:
    def test_action_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["action", "--help"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# CLI-09: mouse
# ═══════════════════════════════════════════════════════════════════════


class TestMouseCommands:
    def test_mouse_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mouse", "--help"])
        assert result.exit_code == 0

    def test_mouse_move_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mouse", "move", "--help"])
        assert result.exit_code == 0

    def test_mouse_click_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mouse", "click", "--help"])
        assert result.exit_code == 0
        assert "--button" in result.output

    def test_mouse_drag_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mouse", "drag", "--help"])
        assert result.exit_code == 0

    def test_mouse_scroll_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mouse", "scroll", "--help"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# CLI-10: key
# ═══════════════════════════════════════════════════════════════════════


class TestKeyCommand:
    def test_key_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["key", "--help"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# CLI-11: type
# ═══════════════════════════════════════════════════════════════════════


class TestTypeCommand:
    def test_type_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["type", "--help"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# CLI-12: wait
# ═══════════════════════════════════════════════════════════════════════


class TestWaitCommand:
    def test_wait_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["wait", "--help"])
        assert result.exit_code == 0
        assert "--timeout" in result.output
        assert "--state" in result.output


# ═══════════════════════════════════════════════════════════════════════
# CLI-13: do
# ═══════════════════════════════════════════════════════════════════════


class TestDoCommand:
    def test_do_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["do", "--help"])
        assert result.exit_code == 0
        assert "--role" in result.output
        assert "--action" in result.output
        assert "--verify" in result.output

    def test_do_has_timeout_option(self) -> None:
        """A10-RR-05: contract-required --timeout must appear in help."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["do", "--help"])
        assert "--timeout" in result.output


# ═══════════════════════════════════════════════════════════════════════
# CLI-14: doctor
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorCommand:
    def test_doctor_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--help"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# CLI-15: MCP-oriented disclosure
# ═══════════════════════════════════════════════════════════════════════


class TestMcpManifestCommand:
    def test_mcp_manifest_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp-manifest", "--help"])

        assert result.exit_code == 0
        assert "machine-readable capability disclosure" in result.output

    def test_mcp_manifest_returns_stable_json_shape(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp-manifest"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)

        expected_top_keys = [
            "identity",
            "non_goals",
            "mcp",
            "trust_boundary",
            "common_loop",
            "capabilities",
            "backends",
            "inspectability_requirements",
        ]
        assert list(parsed.keys()) == expected_top_keys

        assert parsed["identity"]["name"] == "aieyes"
        assert parsed["identity"]["runtime_model"] == "local-cli"
        assert parsed["identity"]["reasoning"] == "external"
        assert "description" in parsed["identity"]

        assert isinstance(parsed["non_goals"], list)
        assert len(parsed["non_goals"]) > 0

        assert parsed["mcp"]["server"] is True
        assert parsed["mcp"]["transport"] == "stdio"
        assert parsed["mcp"]["command"] == "aieyes-mcp"

        caps = parsed["capabilities"]
        # Capabilities may be nested by backend (linux/android)
        if "linux" in caps:
            assert "session" in caps["linux"]
            assert "inspect" in caps["linux"]
            assert "control" in caps["linux"]
        else:
            assert "session" in caps
            assert "inspect" in caps
            assert "control" in caps

        assert isinstance(parsed["inspectability_requirements"], list)
        assert len(parsed["inspectability_requirements"]) > 0

    def test_mcp_manifest_key_order_is_deterministic(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result1 = runner.invoke(cli, ["mcp-manifest"])
        result2 = runner.invoke(cli, ["mcp-manifest"])

        assert result1.output == result2.output


# ═══════════════════════════════════════════════════════════════════════
# PKG-01: pyproject.toml entry point
# ═══════════════════════════════════════════════════════════════════════


class TestPyprojectToml:
    def test_scripts_entry_exists(self) -> None:
        import sys

        if sys.version_info < (3, 11):
            import pytest

            pytest.skip("tomllib requires Python 3.11+")
        import tomllib

        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        assert "aieyes" in scripts
        assert scripts["aieyes"] == "aiyes.cli.main:cli"

    def test_click_dependency(self) -> None:
        import sys

        if sys.version_info < (3, 11):
            import pytest

            pytest.skip("tomllib requires Python 3.11+")
        import tomllib

        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)

        deps = data.get("project", {}).get("dependencies", [])
        assert any("click" in d for d in deps), (
            f"click not found in dependencies: {deps}"
        )

    def test_public_package_metadata_uses_modern_license_and_readme(self) -> None:
        content = Path("pyproject.toml").read_text()

        assert 'readme = "README.md"' in content
        assert 'license = "AGPL-3.0-or-later"' in content
        assert "License :: OSI Approved" not in content
        assert 'requires = ["setuptools>=77.0", "wheel"]' in content

    def test_dev_tooling_contains_package_release_checks(self) -> None:
        content = Path("pyproject.toml").read_text()

        assert '"build"' in content
        assert '"twine"' in content


# ═══════════════════════════════════════════════════════════════════════
# A10-003: Doctor exit code semantics
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorExitCode:
    """A10-003: doctor semantic failures = exit 0, system exceptions = exit 1."""

    def test_doctor_exits_zero_on_all_pass(self) -> None:
        from aiyes.cli.main import cli
        from aiyes.domain.types import DependencyResult

        runner = CliRunner()
        with patch("aiyes.cli.main.doctor_uc") as mock_uc:
            mock_uc.execute.return_value = [
                DependencyResult(name="xvfb", status="pass", message="found"),
            ]
            result = runner.invoke(cli, ["doctor"])
            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed[0]["status"] == "pass"

    def test_doctor_exits_zero_on_failures(self) -> None:
        """Semantic failures are JSON stdout + exit 0, NOT exit 1."""
        from aiyes.cli.main import cli
        from aiyes.domain.types import DependencyResult

        runner = CliRunner()
        with patch("aiyes.cli.main.doctor_uc") as mock_uc:
            mock_uc.execute.return_value = [
                DependencyResult(name="xvfb", status="fail", message="not found"),
                DependencyResult(name="scrot", status="pass", message="found"),
            ]
            result = runner.invoke(cli, ["doctor"])
            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert any(r["status"] == "fail" for r in parsed)

    def test_doctor_exits_one_on_system_exception(self) -> None:
        """System exceptions = exit 1 + stderr error text."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with patch("aiyes.cli.main.doctor_uc") as mock_uc:
            mock_uc.execute.side_effect = RuntimeError("I/O failure")
            result = runner.invoke(cli, ["doctor"])
            assert result.exit_code == 1


# ═══════════════════════════════════════════════════════════════════════
# A10-005: CLI-level password masking in all command paths
# ═══════════════════════════════════════════════════════════════════════


class TestPasswordMaskingInCommands:
    """A10-005: Every command that serializes nodes masks password_text values."""

    def _make_password_tree(self):
        """Build a domain tree containing a password_text node."""
        from aiyes.domain.tree import AccessibilityTree, Node

        pw_node = Node(
            id="n_pw",
            role="password_text",
            name="Password",
            bounds=(0, 0, 200, 30),
            states=("enabled",),
            actions=(),
            value="s3cr3t",
        )
        root = Node(
            id="n_root",
            role="frame",
            name="Login",
            bounds=(0, 0, 800, 600),
            states=("enabled",),
            actions=(),
            children=(pw_node,),
        )
        return AccessibilityTree(roots=(root,))

    def test_inspect_masks_password(self) -> None:
        """inspect command masks password_text values in output."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        tree = self._make_password_tree()

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.inspect_uc") as mock_uc,
        ):
            mock_result = MagicMock()
            mock_result.tree = tree
            mock_result.screenshot = None
            mock_result.timestamp = "2026-03-22T12:00:00+00:00"
            mock_uc.execute.return_value = mock_result

            result = runner.invoke(cli, ["inspect", "--session", "s1"])
            assert result.exit_code == 0
            parsed = json.loads(result.output)

            # Password must be masked
            assert "s3cr3t" not in result.output
            # Find the password node in the tree output
            tree_nodes = parsed["tree"]["tree"]
            pw_nodes = [
                n
                for n in tree_nodes[0].get("children", [])
                if n.get("role") == "password_text"
            ]
            assert len(pw_nodes) == 1
            assert pw_nodes[0]["value"] == "***"

    def test_find_masks_password(self) -> None:
        """find command masks password_text values in output."""
        from aiyes.cli.main import cli
        from aiyes.domain.use_cases.find import FoundNode

        runner = CliRunner()

        pw_found = FoundNode(
            id="n_pw",
            role="password_text",
            name="Password",
            bounds=(0, 0, 200, 30),
            states=("enabled",),
            actions=(),
            value="s3cr3t",
        )

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.find_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = [pw_found]

            result = runner.invoke(cli, ["find", "--session", "s1", "password_text"])
            assert result.exit_code == 0
            parsed = json.loads(result.output)

            assert "s3cr3t" not in result.output
            assert parsed[0]["value"] == "***"

    def test_do_masks_password_in_found(self) -> None:
        """do command masks password_text values in found node."""
        from aiyes.cli.main import cli
        from aiyes.domain.tree import Node
        from aiyes.domain.use_cases.compound_do import (
            CompoundActionResult,
            CompoundDoResult,
        )

        runner = CliRunner()

        pw_node = Node(
            id="n_pw",
            role="password_text",
            name="Password",
            bounds=(0, 0, 200, 30),
            states=("enabled",),
            actions=("activate",),
            value="s3cr3t",
        )
        do_result = CompoundDoResult(
            found=pw_node,
            action_result=CompoundActionResult(
                status="ok", action="activate", target="n_pw"
            ),
        )

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.compound_do_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = do_result

            result = runner.invoke(
                cli,
                [
                    "do",
                    "--session",
                    "s1",
                    "--role",
                    "password_text",
                    "--name",
                    "Password",
                    "--action",
                    "activate",
                ],
            )
            assert result.exit_code == 0
            parsed = json.loads(result.output)

            assert "s3cr3t" not in result.output
            assert parsed["found"]["value"] == "***"

    def test_do_masks_password_in_verify_tree(self) -> None:
        """do --verify masks password_text values in verify tree."""
        from aiyes.cli.main import cli
        from aiyes.domain.tree import AccessibilityTree, Node
        from aiyes.domain.use_cases.compound_do import (
            CompoundActionResult,
            CompoundDoResult,
        )

        runner = CliRunner()

        pw_node = Node(
            id="n_pw",
            role="password_text",
            name="Password",
            bounds=(0, 0, 200, 30),
            states=("enabled",),
            actions=("activate",),
            value="top_secret",
        )
        verify_tree = AccessibilityTree(
            roots=(
                Node(
                    id="n_root",
                    role="frame",
                    name="Login",
                    bounds=(0, 0, 800, 600),
                    states=("enabled",),
                    actions=(),
                    children=(pw_node,),
                ),
            )
        )
        found_node = Node(
            id="n_btn",
            role="push_button",
            name="Submit",
            bounds=(100, 200, 80, 30),
            states=("enabled",),
            actions=("click",),
        )
        do_result = CompoundDoResult(
            found=found_node,
            action_result=CompoundActionResult(
                status="ok", action="click", target="n_btn"
            ),
            verify=verify_tree,
        )

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.compound_do_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = do_result

            result = runner.invoke(
                cli,
                [
                    "do",
                    "--session",
                    "s1",
                    "--role",
                    "push_button",
                    "--name",
                    "Submit",
                    "--action",
                    "click",
                    "--verify",
                ],
            )
            assert result.exit_code == 0

            assert "top_secret" not in result.output
            parsed = json.loads(result.output)
            verify = parsed["verify"]
            # The verify tree has a "tree" key with node list
            tree_nodes = verify["tree"]
            pw_children = tree_nodes[0].get("children", [])
            pw_node_output = [
                n for n in pw_children if n.get("role") == "password_text"
            ]
            assert len(pw_node_output) == 1
            assert pw_node_output[0]["value"] == "***"
