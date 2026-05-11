"""AIYES-23 Schema Generation tests — RED phase.

Tests for CommandInfo dataclass, enumerate_commands() introspection,
tool naming algorithm, collision detection, and Click-to-JSON-Schema
type mapping. These tests MUST fail because src/aiyes/cli/schema_gen.py
does not exist yet.

Traceability — Formal Constraint Map:
  BC-01: enumerate_commands returns exactly 23 leaf commands
  BC-02: Tool name algorithm — underscore-joined path segments
  BC-03: CLI name algorithm — space-joined path segments
  BC-04: Collision detection raises ValueError
  BC-05: Click STRING -> JSON Schema string
  BC-06: Click INT -> JSON Schema integer
  BC-07: Click FLOAT -> JSON Schema number
  BC-08: Click is_flag=True -> JSON Schema boolean with default false
  BC-09: Click Choice -> JSON Schema string with enum
  BC-10: Click nargs=-1 -> JSON Schema array of strings
  BC-11: Optional params with non-None default include default in schema
  BC-12: Optional nullable params -> union type with null
  BC-13: Required vs optional placement in JSON Schema
  BC-25: CommandInfo frozen dataclass with five fields
  AC-04: Schema generation lives in cli/ layer
  IC-01: Schema generation is the single source of truth

Requirement coverage:
  REQ-AIYES23-003: CommandInfo frozen dataclass
  REQ-AIYES23-004: STRING mapping
  REQ-AIYES23-005: INT mapping
  REQ-AIYES23-006: FLOAT mapping
  REQ-AIYES23-007: is_flag mapping
  REQ-AIYES23-008: Choice mapping
  REQ-AIYES23-009: nargs=-1 mapping
  REQ-AIYES23-010: Optional with default
  REQ-AIYES23-011: Optional nullable
  REQ-AIYES23-012: Required vs optional
  REQ-AIYES23-013: Exactly 23 commands
  REQ-AIYES23-014: Naming algorithm
  REQ-AIYES23-015: Collision detection
"""

from __future__ import annotations

import dataclasses
from typing import List

import click
import pytest

# These imports will fail (RED) — production module does not exist yet.
from aiyes.cli.schema_gen import CommandInfo, enumerate_commands

# Existing CLI entry point — already exists.
from aiyes.cli.main import cli


# ===================================================================
# Helpers
# ===================================================================


def _find_command_info(commands: List[CommandInfo], tool_name: str) -> CommandInfo:
    """Find a CommandInfo by tool_name or raise AssertionError."""
    for ci in commands:
        if ci.tool_name == tool_name:
            return ci
    raise AssertionError(f"No CommandInfo with tool_name={tool_name!r}")


def _make_synthetic_group_with_collision() -> click.Group:
    """Build a Click group where two subcommands produce the same tool_name."""
    group = click.Group("root")

    @click.command("foo_bar")
    def cmd_a() -> None:
        """First."""

    sub = click.Group("foo")

    @click.command("bar")
    def cmd_b() -> None:
        """Second."""

    sub.add_command(cmd_b)
    group.add_command(cmd_a)
    group.add_command(sub)
    return group


# ===================================================================
# BC-25: CommandInfo frozen dataclass (REQ-AIYES23-003)
# ===================================================================


class TestCommandInfoDataclass:
    """CommandInfo is a frozen dataclass with exactly five fields."""

    def test_commandinfo_is_dataclass(self) -> None:
        """BC-25: CommandInfo is a dataclass."""
        assert dataclasses.is_dataclass(CommandInfo)

    def test_commandinfo_is_frozen(self) -> None:
        """BC-25: CommandInfo is frozen — mutation raises AttributeError."""
        dummy_cmd = click.Command("test", callback=lambda: None)
        ci = CommandInfo(
            cli_name="test",
            tool_name="test",
            description="Test",
            json_schema={"type": "object", "properties": {}},
            click_command=dummy_cmd,
        )
        with pytest.raises(AttributeError):
            ci.cli_name = "changed"  # type: ignore[misc]

    def test_commandinfo_has_exactly_five_fields(self) -> None:
        """BC-25: Exactly five fields."""
        fields = dataclasses.fields(CommandInfo)
        assert len(fields) == 5

    def test_commandinfo_field_names(self) -> None:
        """BC-25: Fields are cli_name, tool_name, description, json_schema, click_command."""
        field_names = {f.name for f in dataclasses.fields(CommandInfo)}
        expected = {
            "cli_name",
            "tool_name",
            "description",
            "json_schema",
            "click_command",
        }
        assert field_names == expected


# ===================================================================
# BC-01: enumerate_commands returns exactly 23 (REQ-AIYES23-013)
# ===================================================================


class TestEnumerateCommands:
    """enumerate_commands(cli) returns exactly 23 CommandInfo instances."""

    def test_enumerate_commands_returns_list(self) -> None:
        """BC-01: Return type is a list."""
        result = enumerate_commands(cli)
        assert isinstance(result, list)

    def test_enumerate_commands_count_is_38(self) -> None:
        """BC-01: Leaf commands include session capabilities and swipe."""
        result = enumerate_commands(cli)
        assert len(result) == 38

    def test_enumerate_commands_all_commandinfo(self) -> None:
        """BC-01: Every element is a CommandInfo instance."""
        result = enumerate_commands(cli)
        for item in result:
            assert isinstance(item, CommandInfo)

    def test_enumerate_commands_excludes_group_nodes(self) -> None:
        """BC-01: Group nodes (session, mouse) are excluded."""
        result = enumerate_commands(cli)
        tool_names = {ci.tool_name for ci in result}
        # "session" and "mouse" are groups, not leaf commands
        assert "session" not in tool_names
        assert "mouse" not in tool_names


# ===================================================================
# BC-02, BC-03: Naming algorithm (REQ-AIYES23-014)
# ===================================================================


class TestNamingAlgorithm:
    """Tool name and CLI name algorithms produce correct values."""

    def test_top_level_inspect(self) -> None:
        """BC-02/03: Top-level 'inspect' -> cli_name='inspect', tool_name='inspect'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "inspect")
        assert ci.cli_name == "inspect"
        assert ci.tool_name == "inspect"

    def test_group_session_start(self) -> None:
        """BC-02/03: Group 'session start' -> tool_name='session_start'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        assert ci.cli_name == "session start"
        assert ci.tool_name == "session_start"

    def test_hyphenated_wait_stable(self) -> None:
        """BC-02/03: Hyphenated 'wait-stable' -> tool_name='wait_stable'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "wait_stable")
        assert ci.cli_name == "wait-stable"
        assert ci.tool_name == "wait_stable"

    def test_hyphenated_mcp_manifest(self) -> None:
        """BC-02/03: 'mcp-manifest' -> tool_name='mcp_manifest'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "mcp_manifest")
        assert ci.cli_name == "mcp-manifest"
        assert ci.tool_name == "mcp_manifest"

    def test_hyphenated_help_json(self) -> None:
        """BC-02/03: 'help-json' -> tool_name='help_json'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "help_json")
        assert ci.cli_name == "help-json"
        assert ci.tool_name == "help_json"

    def test_mouse_subcommand(self) -> None:
        """BC-02/03: 'mouse click' -> tool_name='mouse_click'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "mouse_click")
        assert ci.cli_name == "mouse click"
        assert ci.tool_name == "mouse_click"

    def test_all_expected_tool_names_present(self) -> None:
        """BC-01/02: All expected tool_names are present."""
        result = enumerate_commands(cli)
        tool_names = {ci.tool_name for ci in result}
        expected = {
            # session group (8)
            "session_start",
            "session_stop",
            "session_list",
            "session_capabilities",
            "session_resize",
            "session_metrics",
            "session_prune",
            "session_status",
            # mouse group (4)
            "mouse_move",
            "mouse_click",
            "mouse_drag",
            "mouse_scroll",
            # clipboard group (2)
            "clipboard_read",
            "clipboard_write",
            # gesture group (2)
            "gesture_pinch",
            "gesture_two_finger_scroll",
            # top-level (16)
            "inspect",
            "diff",
            "find",
            "screenshot",
            "action",
            "key",
            "type",
            "wait",
            "wait_reactive",
            "wait_stable",
            "do",
            "doctor",
            "debug_bundle",
            "mcp_manifest",
            "help_json",
            "detect_dialog",
            "navigate",
            "menu",
            "scenario_run",
            "scenario_preflight",
            "scenario_fixtures",
            "swipe",
        }
        assert tool_names == expected


# ===================================================================
# BC-04: Collision detection (REQ-AIYES23-015)
# ===================================================================


class TestCollisionDetection:
    """Duplicate tool_name raises ValueError."""

    def test_collision_raises_value_error(self) -> None:
        """BC-04: Synthetic group with colliding tool_names raises ValueError."""
        group = _make_synthetic_group_with_collision()
        with pytest.raises(ValueError, match="[Cc]ollision|[Dd]uplicate"):
            enumerate_commands(group)


# ===================================================================
# BC-05 through BC-12: Click param type -> JSON Schema mapping
# ===================================================================


class TestClickTypeToJsonSchemaString:
    """BC-05: Click STRING -> {"type": "string"} (REQ-AIYES23-004)."""

    def test_string_param_type(self) -> None:
        """BC-05: session_start 'resolution' is STRING with default."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["resolution"]["type"] == "string"


class TestClickTypeToJsonSchemaInteger:
    """BC-06: Click INT -> {"type": "integer"} (REQ-AIYES23-005)."""

    def test_int_param_type(self) -> None:
        """BC-06: session_start 'color_depth' is INT with default 24."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["color_depth"]["type"] == "integer"


class TestClickTypeToJsonSchemaFloat:
    """BC-07: Click FLOAT -> {"type": "number"} (REQ-AIYES23-006)."""

    def test_float_param_type(self) -> None:
        """BC-07: session_start 'wait_secs' is FLOAT with default 2.0."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["wait_secs"]["type"] == "number"


class TestClickTypeToJsonSchemaBoolean:
    """BC-08: Click is_flag=True -> {"type": "boolean", "default": false} (REQ-AIYES23-007)."""

    def test_flag_param_type(self) -> None:
        """BC-08: inspect 'no_screenshot' is a flag."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "inspect")
        props = ci.json_schema["properties"]
        assert props["no_screenshot"]["type"] == "boolean"
        assert props["no_screenshot"]["default"] is False

    def test_flag_not_in_required(self) -> None:
        """BC-08: Flags are never in required."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "inspect")
        required = ci.json_schema.get("required", [])
        assert "no_screenshot" not in required

    def test_multiple_flags_on_inspect(self) -> None:
        """BC-08: All inspect flags map to boolean with default false."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "inspect")
        props = ci.json_schema["properties"]
        for flag_name in ["no_screenshot", "no_tree", "no_prune", "screenshot_base64"]:
            assert props[flag_name]["type"] == "boolean", (
                f"{flag_name} should be boolean"
            )
            assert props[flag_name]["default"] is False, (
                f"{flag_name} default should be false"
            )


class TestClickTypeToJsonSchemaChoice:
    """BC-09: Click Choice -> {"type": "string", "enum": [...]} (REQ-AIYES23-008)."""

    def test_choice_param_type(self) -> None:
        """BC-09: session_start 'backend' is Choice(["linux", "android"])."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["backend"]["type"] == "string"
        assert props["backend"]["enum"] == ["linux", "android"]


class TestClickTypeToJsonSchemaArray:
    """BC-10: Click nargs=-1 -> {"type": "array", "items": {"type": "string"}} (REQ-AIYES23-009)."""

    def test_nargs_param_type(self) -> None:
        """BC-10: session_start 'command' is nargs=-1."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["command"]["type"] == "array"
        assert props["command"]["items"] == {"type": "string"}

    def test_nargs_key_command(self) -> None:
        """BC-10: key 'keys' is nargs=-1."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "key")
        props = ci.json_schema["properties"]
        assert props["keys"]["type"] == "array"
        assert props["keys"]["items"] == {"type": "string"}


class TestOptionalWithDefault:
    """BC-11: Optional params with non-None default include 'default' key (REQ-AIYES23-010)."""

    def test_string_default(self) -> None:
        """BC-11: session_start 'resolution' default is '1280x800'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["resolution"]["default"] == "1280x800"

    def test_int_default(self) -> None:
        """BC-11: session_start 'color_depth' default is 24."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["color_depth"]["default"] == 24

    def test_float_default(self) -> None:
        """BC-11: session_start 'wait_secs' default is 2.0."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["wait_secs"]["default"] == 2.0

    def test_choice_default(self) -> None:
        """BC-11: session_start 'backend' default is 'linux'."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["backend"]["default"] == "linux"

    def test_default_not_in_required(self) -> None:
        """BC-11: Params with defaults are NOT in required."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        required = ci.json_schema.get("required", [])
        for param in ["resolution", "color_depth", "wait_secs", "backend"]:
            assert param not in required, f"{param} should not be in required"


class TestOptionalNullable:
    """BC-12: Optional nullable -> {"type": ["<base_type>", "null"]} (REQ-AIYES23-011)."""

    def test_nullable_string(self) -> None:
        """BC-12: session_start 'name' is nullable string."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        assert props["name"]["type"] == ["string", "null"]

    def test_nullable_integer(self) -> None:
        """BC-12: mouse_click 'x' is nullable integer."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "mouse_click")
        props = ci.json_schema["properties"]
        assert props["x"]["type"] == ["integer", "null"]

    def test_nullable_not_in_required(self) -> None:
        """BC-12: Nullable params are NOT in required."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "mouse_click")
        required = ci.json_schema.get("required", [])
        assert "x" not in required
        assert "y" not in required


class TestRequiredVsOptional:
    """BC-13: Required vs optional placement in JSON Schema (REQ-AIYES23-012)."""

    def test_required_argument(self) -> None:
        """BC-13: Required arguments are in 'required' array."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "mouse_move")
        required = ci.json_schema.get("required", [])
        assert "x" in required
        assert "y" in required

    def test_required_option(self) -> None:
        """BC-13: do 'role' and 'action_name' are required=True options."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "do")
        required = ci.json_schema.get("required", [])
        assert "role" in required
        assert "action_name" in required

    def test_required_nargs_argument(self) -> None:
        """BC-13: session_start 'command' (nargs=-1, required=True) is in required."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        required = ci.json_schema.get("required", [])
        assert "command" in required

    def test_all_params_in_properties(self) -> None:
        """BC-13: All parameters always appear in properties."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "do")
        props = ci.json_schema["properties"]
        expected_params = {
            "session_id",
            "role",
            "name_pattern",
            "action_name",
            "verify",
            "value",
            "timeout",
        }
        assert expected_params == set(props.keys())

    def test_json_schema_has_type_object(self) -> None:
        """BC-13: JSON Schema top-level type is 'object'."""
        result = enumerate_commands(cli)
        for ci in result:
            assert ci.json_schema["type"] == "object", (
                f"{ci.tool_name} schema should have type=object"
            )


# ===================================================================
# PBT: every CommandInfo has valid JSON Schema
# ===================================================================


class TestSchemaValidityPBT:
    """Property-based: every CommandInfo.json_schema is valid JSON Schema."""

    def test_all_schemas_have_type_object_and_properties(self) -> None:
        """IC-01: Every schema has type=object and properties dict."""
        result = enumerate_commands(cli)
        for ci in result:
            schema = ci.json_schema
            assert schema["type"] == "object", f"{ci.tool_name}: missing type=object"
            assert "properties" in schema, f"{ci.tool_name}: missing properties"
            assert isinstance(schema["properties"], dict), (
                f"{ci.tool_name}: properties should be dict"
            )

    def test_all_schemas_required_is_list_of_strings(self) -> None:
        """IC-01: 'required' (if present) is a list of strings from properties."""
        result = enumerate_commands(cli)
        for ci in result:
            schema = ci.json_schema
            if "required" in schema:
                assert isinstance(schema["required"], list), (
                    f"{ci.tool_name}: required should be list"
                )
                props = set(schema["properties"].keys())
                for req in schema["required"]:
                    assert isinstance(req, str), (
                        f"{ci.tool_name}: required entry should be str"
                    )
                    assert req in props, (
                        f"{ci.tool_name}: required '{req}' not in properties"
                    )

    def test_all_property_types_are_valid(self) -> None:
        """IC-01: Every property has a valid 'type' field."""
        valid_types = {"string", "integer", "number", "boolean", "array", "object"}
        result = enumerate_commands(cli)
        for ci in result:
            for prop_name, prop_schema in ci.json_schema["properties"].items():
                prop_type = prop_schema.get("type")
                if isinstance(prop_type, list):
                    # Union type like ["integer", "null"]
                    for t in prop_type:
                        assert t in valid_types | {"null"}, (
                            f"{ci.tool_name}.{prop_name}: invalid type {t!r}"
                        )
                else:
                    assert prop_type in valid_types, (
                        f"{ci.tool_name}.{prop_name}: invalid type {prop_type!r}"
                    )


# ===================================================================
# Contract test: known-good Click parameters validate against schema
# ===================================================================


class TestSchemaContractAgainstKnownCommands:
    """Generated schemas match known Click parameter structures."""

    def test_session_start_schema_complete(self) -> None:
        """Contract: session_start schema has all expected params."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "session_start")
        props = ci.json_schema["properties"]
        expected = {
            "resolution",
            "color_depth",
            "wait_secs",
            "name",
            "backend",
            "device_serial",
            "command",
        }
        assert set(props.keys()) == expected

    def test_inspect_schema_complete(self) -> None:
        """Contract: inspect schema has all expected params."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "inspect")
        props = ci.json_schema["properties"]
        expected = {
            "session_id",
            "no_screenshot",
            "no_tree",
            "tree_depth",
            "no_prune",
            "screenshot_base64",
            "focus_window",
        }
        assert set(props.keys()) == expected

    def test_do_schema_required_options(self) -> None:
        """Contract: do command has exactly role and action_name required."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "do")
        required = set(ci.json_schema.get("required", []))
        assert required == {"role", "action_name"}

    def test_wait_stable_schema_defaults(self) -> None:
        """Contract: wait-stable has correct defaults for all params."""
        result = enumerate_commands(cli)
        ci = _find_command_info(result, "wait_stable")
        props = ci.json_schema["properties"]
        assert props["timeout"]["default"] == 10.0
        assert props["interval"]["default"] == 0.5
        assert props["consecutive"]["default"] == 3

    def test_description_is_nonempty_string(self) -> None:
        """Contract: every command has a non-empty description."""
        result = enumerate_commands(cli)
        for ci in result:
            assert isinstance(ci.description, str), (
                f"{ci.tool_name}: description should be str"
            )
            assert len(ci.description.strip()) > 0, (
                f"{ci.tool_name}: description should not be empty"
            )
