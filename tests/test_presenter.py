"""Presenter tests — AIYES-02 scope.

Tests for the CLI presenter that converts domain results to JSON.

Traceability:
  PRES-01: Domain result -> JSON conversion, two-tier error model
  PRES-02: Password masking for role=password_text
  PRES-03: MCP-oriented disclosure manifest formatting
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import Node


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_session(**overrides: Any) -> Session:
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


def _make_node(
    node_id: str = "n_001",
    role: str = "push_button",
    name: str = "OK",
    value: Optional[str] = None,
    children: tuple = (),
) -> Node:
    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=(100, 200, 80, 30),
        states=("enabled", "visible"),
        actions=("click",),
        children=children,
        value=value,
    )


# ═══════════════════════════════════════════════════════════════════════
# PRES-01: Domain result -> JSON conversion
# ═══════════════════════════════════════════════════════════════════════


class TestPresenterSessionToJson:
    """Presenter converts session results to JSON strings."""

    def test_session_start_to_json(self) -> None:
        from aiyes.cli.presenter import format_session_start

        session = _make_session()
        result = format_session_start(session)

        parsed = json.loads(result)
        assert parsed["session_id"] == "test-001"
        assert parsed["display"] == ":99"
        assert parsed["app_pid"] == 12345
        assert "atspi_bus_address" in parsed

    def test_session_stop_to_json(self) -> None:
        from aiyes.cli.presenter import format_session_stop

        result = format_session_stop("stopped", "test-001")
        parsed = json.loads(result)
        assert parsed["status"] == "stopped"
        assert parsed["session_id"] == "test-001"

    def test_session_stop_includes_errors_when_present(self) -> None:
        from aiyes.cli.presenter import format_session_stop

        result = format_session_stop(
            "stopped_with_errors",
            "test-001",
            errors=("app stop failed: boom", "display_server stop failed: nope"),
        )
        parsed = json.loads(result)

        assert parsed["status"] == "stopped_with_errors"
        assert parsed["session_id"] == "test-001"
        assert parsed["errors"] == [
            "app stop failed: boom",
            "display_server stop failed: nope",
        ]

    def test_session_list_to_json(self) -> None:
        from aiyes.cli.presenter import format_session_list

        entries = [
            {
                "session_id": "s1",
                "display": ":99",
                "app": "gedit",
                "status": "active",
                "uptime": 120.0,
            },
            {
                "session_id": "s2",
                "display": ":100",
                "app": "firefox",
                "status": "stale",
                "uptime": None,
            },
        ]
        result = format_session_list(entries)
        parsed = json.loads(result)

        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["session_id"] == "s1"

    def test_empty_session_list_to_json(self) -> None:
        from aiyes.cli.presenter import format_session_list

        result = format_session_list([])
        parsed = json.loads(result)
        assert parsed == []


class TestPresenterInspectToJson:
    def test_inspect_result_to_json(self) -> None:
        from aiyes.cli.presenter import format_inspect

        tree_dict = {
            "tree": [
                {
                    "id": "n_001",
                    "role": "frame",
                    "name": "Win",
                    "bounds": [0, 0, 800, 600],
                    "states": ["enabled"],
                    "actions": [],
                }
            ]
        }
        result = format_inspect(
            tree=tree_dict,
            screenshot="/path/to/screenshot.png",
            timestamp="2026-03-22T12:00:00+00:00",
        )
        parsed = json.loads(result)
        assert "tree" in parsed
        assert parsed["screenshot"] == "/path/to/screenshot.png"
        assert "timestamp" in parsed


class TestPresenterFindToJson:
    def test_find_results_to_json(self) -> None:
        from aiyes.cli.presenter import format_find

        nodes = [
            {
                "id": "n_001",
                "role": "push_button",
                "name": "OK",
                "bounds": [100, 200, 80, 30],
                "states": ["enabled"],
                "actions": ["click"],
            },
        ]
        result = format_find(nodes)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["id"] == "n_001"


class TestPresenterScreenshotToJson:
    def test_screenshot_path_to_json(self) -> None:
        from aiyes.cli.presenter import format_screenshot

        result = format_screenshot(path="/path/shot.png")
        parsed = json.loads(result)
        assert parsed["path"] == "/path/shot.png"

    def test_screenshot_base64_to_json(self) -> None:
        from aiyes.cli.presenter import format_screenshot

        result = format_screenshot(data="aGVsbG8=")
        parsed = json.loads(result)
        assert parsed["data"] == "aGVsbG8="


class TestPresenterActionToJson:
    def test_action_success_to_json(self) -> None:
        from aiyes.cli.presenter import format_action

        result = format_action(status="ok", action="click", target="n_001")
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["action"] == "click"
        assert parsed["target"] == "n_001"

    def test_action_semantic_failure_to_json(self) -> None:
        from aiyes.cli.presenter import format_action

        result = format_action(
            status="error",
            action="press",
            target="n_001",
            reason="Action 'press' not available",
            available_actions=["click", "activate"],
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["reason"] == "Action 'press' not available"
        assert parsed["available_actions"] == ["click", "activate"]


class TestPresenterDoctorToJson:
    def test_doctor_results_to_json(self) -> None:
        from aiyes.cli.presenter import format_doctor

        results = [
            {"name": "xvfb", "status": "pass", "message": "found"},
            {"name": "scrot", "status": "fail", "message": "not found"},
        ]
        result = format_doctor(results)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "xvfb"
        assert parsed[1]["status"] == "fail"


class TestPresenterMcpManifestToJson:
    def test_mcp_manifest_to_json(self) -> None:
        from aiyes.cli.presenter import format_mcp_manifest

        manifest = {
            "identity": {
                "name": "aieyes",
                "runtime_model": "local-cli",
                "reasoning": "external",
            },
            "mcp": {
                "server": False,
                "purpose": "disclosure-to-ai-tools",
            },
            "capabilities": {
                "inspect": ["tree", "screenshot"],
                "control": ["mouse", "keyboard", "semantic-action"],
            },
            "inspectability_requirements": [
                "Expose accessible names",
                "Expose AT-SPI roles and states",
            ],
        }

        result = format_mcp_manifest(manifest)
        parsed = json.loads(result)

        assert parsed["identity"]["name"] == "aieyes"
        assert parsed["mcp"]["server"] is False
        assert parsed["capabilities"]["inspect"] == ["tree", "screenshot"]
        assert parsed["inspectability_requirements"] == [
            "Expose accessible names",
            "Expose AT-SPI roles and states",
        ]

    def test_mcp_manifest_enforces_canonical_key_order(self) -> None:
        from aiyes.cli.presenter import format_mcp_manifest

        manifest = {
            "inspectability_requirements": ["a"],
            "identity": {"name": "test"},
            "capabilities": {"x": ["y"]},
            "non_goals": ["z"],
            "mcp": {"server": False},
            "common_loop": ["step1"],
        }

        result = format_mcp_manifest(manifest)
        parsed = json.loads(result)

        assert list(parsed.keys()) == [
            "identity",
            "non_goals",
            "mcp",
            "common_loop",
            "capabilities",
            "inspectability_requirements",
        ]


class TestPresenterMouseKeyTypeToJson:
    def test_mouse_result_to_json(self) -> None:
        from aiyes.cli.presenter import format_status_ok

        result = format_status_ok()
        parsed = json.loads(result)
        assert parsed["status"] == "ok"


class TestPresenterWaitToJson:
    def test_wait_found_to_json(self) -> None:
        from aiyes.cli.presenter import format_wait

        result = format_wait(found=True, node_id="n_001", timeout=False)
        parsed = json.loads(result)
        assert parsed["found"] is True
        assert parsed["id"] == "n_001"

    def test_wait_timeout_to_json(self) -> None:
        from aiyes.cli.presenter import format_wait

        result = format_wait(found=False, timeout=True)
        parsed = json.loads(result)
        assert parsed["found"] is False
        assert parsed["timeout"] is True


class TestPresenterErrorModel:
    """PRES-01: Two-tier error model."""

    def test_system_error_is_plain_text(self) -> None:
        from aiyes.cli.presenter import format_system_error

        result = format_system_error("Session not found: xyz")
        # System errors are plain text for stderr, NOT JSON
        assert isinstance(result, str)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_semantic_error_is_json(self) -> None:
        from aiyes.cli.presenter import format_action

        result = format_action(
            status="error",
            action="press",
            target="n_001",
            reason="not available",
            available_actions=["click"],
        )
        # Semantic failures ARE valid JSON
        parsed = json.loads(result)
        assert parsed["status"] == "error"


class TestPresenterNoAdapterImport:
    """PRES-01: presenter does NOT import from adapters or click."""

    def test_no_adapter_import(self) -> None:
        import ast
        from pathlib import Path

        source = Path("src/aiyes/cli/presenter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters"), (
                    f"Presenter illegally imports from {node.module}"
                )

    def test_no_click_import(self) -> None:
        import ast
        from pathlib import Path

        source = Path("src/aiyes/cli/presenter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "click", "Presenter must not import click"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "click", "Presenter must not import from click"


# ═══════════════════════════════════════════════════════════════════════
# PRES-02: Password masking
# ═══════════════════════════════════════════════════════════════════════


class TestPasswordMasking:
    """Nodes with role='password_text' must have value='***' in output."""

    def test_mask_password_text_node(self) -> None:
        from aiyes.cli.presenter import mask_node_dict

        node_dict = {
            "id": "n_001",
            "role": "password_text",
            "name": "Password",
            "value": "s3cr3t",
            "bounds": [0, 0, 200, 30],
            "states": ["enabled"],
            "actions": [],
        }
        result = mask_node_dict(node_dict)
        assert result["value"] == "***"

    def test_non_password_node_not_masked(self) -> None:
        from aiyes.cli.presenter import mask_node_dict

        node_dict = {
            "id": "n_001",
            "role": "text",
            "name": "Username",
            "value": "jeroen",
            "bounds": [0, 0, 200, 30],
            "states": ["enabled"],
            "actions": [],
        }
        result = mask_node_dict(node_dict)
        assert result["value"] == "jeroen"

    def test_masking_is_case_sensitive(self) -> None:
        """Only exact 'password_text' is masked, not 'Password_Text' etc."""
        from aiyes.cli.presenter import mask_node_dict

        node_dict = {
            "id": "n_001",
            "role": "Password_Text",
            "name": "Password",
            "value": "s3cr3t",
            "bounds": [0, 0, 200, 30],
            "states": ["enabled"],
            "actions": [],
        }
        result = mask_node_dict(node_dict)
        assert result["value"] == "s3cr3t"  # NOT masked

    def test_masking_recursive_in_tree(self) -> None:
        """Nested password_text nodes must also be masked."""
        from aiyes.cli.presenter import mask_node_dict

        tree_dict = {
            "id": "n_001",
            "role": "frame",
            "name": "Login",
            "bounds": [0, 0, 800, 600],
            "states": ["enabled"],
            "actions": [],
            "children": [
                {
                    "id": "n_002",
                    "role": "text",
                    "name": "Username",
                    "value": "jeroen",
                    "bounds": [0, 0, 200, 30],
                    "states": ["enabled"],
                    "actions": [],
                },
                {
                    "id": "n_003",
                    "role": "password_text",
                    "name": "Password",
                    "value": "topsecret",
                    "bounds": [0, 30, 200, 30],
                    "states": ["enabled"],
                    "actions": [],
                },
            ],
        }
        result = mask_node_dict(tree_dict)

        # Non-password child unchanged
        assert result["children"][0]["value"] == "jeroen"
        # Password child masked
        assert result["children"][1]["value"] == "***"

    def test_masking_deeply_nested(self) -> None:
        """Password nodes at arbitrary depth must be masked."""
        from aiyes.cli.presenter import mask_node_dict

        deep = {
            "id": "n_001",
            "role": "frame",
            "name": "Root",
            "bounds": [0, 0, 800, 600],
            "states": [],
            "actions": [],
            "children": [
                {
                    "id": "n_002",
                    "role": "panel",
                    "name": "Panel",
                    "bounds": [0, 0, 800, 600],
                    "states": [],
                    "actions": [],
                    "children": [
                        {
                            "id": "n_003",
                            "role": "password_text",
                            "name": "Deep PW",
                            "value": "deep_secret",
                            "bounds": [0, 0, 200, 30],
                            "states": [],
                            "actions": [],
                        }
                    ],
                }
            ],
        }
        result = mask_node_dict(deep)
        assert result["children"][0]["children"][0]["value"] == "***"

    def test_node_without_value_field_unaffected(self) -> None:
        """Nodes with role=password_text but no value key: no error."""
        from aiyes.cli.presenter import mask_node_dict

        node_dict = {
            "id": "n_001",
            "role": "password_text",
            "name": "Password",
            "bounds": [0, 0, 200, 30],
            "states": ["enabled"],
            "actions": [],
        }
        result = mask_node_dict(node_dict)
        # No crash; value key absent or masked
        assert result.get("value") is None or result.get("value") == "***"
