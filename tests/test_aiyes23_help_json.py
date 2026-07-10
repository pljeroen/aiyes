"""AIYES-23 help-json CLI command tests — RED phase.

Tests for the `aieyes help-json` CLI command that outputs valid JSON
describing all commands and their schemas. These tests MUST fail because
src/aiyes/cli/help_json.py does not exist yet and the help-json command
is not registered.

Traceability — Formal Constraint Map:
  BC-14: help-json outputs valid JSON with exact structure
  BC-15: help-json commands count matches enumerate_commands
  BC-16: help-json is session-less, logs to _global

Requirement coverage:
  REQ-AIYES23-016: help-json is session-less
  REQ-AIYES23-017: help-json output structure
  REQ-AIYES23-018: help-json commands count
"""

from __future__ import annotations

import json
from typing import List
from unittest.mock import patch

from click.testing import CliRunner

from aiyes.domain.operation_record import OperationRecord

# Existing CLI entry point — already exists.
from aiyes.cli.main import cli

# help_json module provides build_help_json; command is registered in main.py.
from aiyes.cli.help_json import build_help_json  # noqa: F401


# ===================================================================
# Helpers
# ===================================================================


class SpyOperationLog:
    """Spy that records all append calls for assertion."""

    def __init__(self) -> None:
        self.appended: List[OperationRecord] = []

    def append(self, record: OperationRecord) -> None:
        self.appended.append(record)

    def read(self, session_id: str) -> List[OperationRecord]:
        return [r for r in self.appended if r.session_id == session_id]

    def read_all(self) -> List[OperationRecord]:
        return list(self.appended)

    def list_session_ids(self) -> List[str]:
        seen: List[str] = []
        for r in self.appended:
            sid = r.session_id if r.session_id else "_global"
            if sid not in seen:
                seen.append(sid)
        return seen


# ===================================================================
# BC-14: help-json outputs valid JSON with exact structure
# ===================================================================


class TestHelpJsonOutputStructure:
    """help-json command outputs valid JSON with correct top-level keys."""

    def test_help_json_exits_zero(self) -> None:
        """BC-14: help-json exits with code 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        assert result.exit_code == 0, (
            f"exit_code={result.exit_code}, output={result.output}"
        )

    def test_help_json_outputs_valid_json(self) -> None:
        """BC-14: stdout is valid JSON."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_help_json_has_name_key(self) -> None:
        """BC-14: Output has 'name' key with value 'aieyes'."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        assert data["name"] == "aieyes"

    def test_help_json_has_version_key(self) -> None:
        """BC-14: Output has 'version' key with string value."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_help_json_has_commands_key(self) -> None:
        """BC-14: Output has 'commands' key with list value."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        assert "commands" in data
        assert isinstance(data["commands"], list)

    def test_help_json_has_exactly_three_top_level_keys(self) -> None:
        """BC-14: Only 'name', 'version', 'commands' at top level."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        assert set(data.keys()) == {"name", "version", "commands"}


# ===================================================================
# BC-15: commands array matches enumerate_commands
# ===================================================================


class TestHelpJsonCommandsArray:
    """Commands array has correct structure and count."""

    def test_commands_count_is_38(self) -> None:
        """BC-15: commands array includes capabilities, swipe, goto, reload."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        assert len(data["commands"]) == 40

    def test_each_command_has_four_keys(self) -> None:
        """BC-14: Each command entry has exactly name, tool_name, description, parameters."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        for entry in data["commands"]:
            assert set(entry.keys()) == {
                "name",
                "tool_name",
                "description",
                "parameters",
            }, f"Unexpected keys in command entry: {set(entry.keys())}"

    def test_each_command_has_name_string(self) -> None:
        """BC-14: Each entry's 'name' is a non-empty string (cli_name)."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        for entry in data["commands"]:
            assert isinstance(entry["name"], str)
            assert len(entry["name"].strip()) > 0

    def test_each_command_has_tool_name_string(self) -> None:
        """BC-14: Each entry's 'tool_name' is a non-empty string."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        for entry in data["commands"]:
            assert isinstance(entry["tool_name"], str)
            assert len(entry["tool_name"].strip()) > 0

    def test_each_command_has_description_string(self) -> None:
        """BC-14: Each entry's 'description' is a non-empty string."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        for entry in data["commands"]:
            assert isinstance(entry["description"], str)
            assert len(entry["description"].strip()) > 0

    def test_each_command_parameters_is_valid_json_schema(self) -> None:
        """BC-14: Each entry's 'parameters' is valid JSON Schema."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        for entry in data["commands"]:
            params = entry["parameters"]
            assert isinstance(params, dict)
            assert params["type"] == "object", (
                f"{entry['tool_name']}: parameters should have type=object"
            )
            assert "properties" in params, (
                f"{entry['tool_name']}: parameters should have properties"
            )

    def test_help_json_includes_itself(self) -> None:
        """BC-15: help-json itself appears in the commands array."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help-json"])
        data = json.loads(result.output)
        tool_names = {entry["tool_name"] for entry in data["commands"]}
        assert "help_json" in tool_names


# ===================================================================
# BC-16: Session attribution (REQ-AIYES23-016)
# ===================================================================


class TestHelpJsonSessionAttribution:
    """help-json logs as session-less with sid=''."""

    def test_help_json_logs_with_empty_session_id(self) -> None:
        """BC-16: help-json operation record has session_id=''."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.composition_root.operation_log_adapter", spy),
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            result = runner.invoke(cli, ["help-json"])

        assert result.exit_code == 0
        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""

    def test_help_json_logs_command_name(self) -> None:
        """BC-16: Operation record command field is 'help-json'."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.composition_root.operation_log_adapter", spy),
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            result = runner.invoke(cli, ["help-json"])

        assert result.exit_code == 0
        assert len(spy.appended) == 1
        assert spy.appended[0].command == "help-json"
