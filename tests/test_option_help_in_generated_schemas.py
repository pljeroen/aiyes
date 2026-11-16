"""An option's help text is the description of its parameter in the generated schemas.

The schema generator turns the Click parameters of each command into a JSON
Schema, which help-json (the CLI command and the MCP tool) and the MCP tool
definitions publish. These tests check that:

* an option's help text, exactly as Click stores it, becomes the
  ``description`` of its parameter whatever the kind of option, and nothing
  else in the parameter changes; an option without help text and a positional
  argument get no description (commands made up in this file, one parameter
  each);
* on every command of the real CLI, each parameter carries the expected
  description on the generator output, the ``help-json`` command and the MCP
  tool definitions: the scenario tools' own text where they describe a
  parameter, otherwise the option's help text, and no description for any
  other parameter;
* the MCP ``help_json`` tool returns the same parameters as help-json;
* the generated definitions serialize as JSON and every description is a
  non-empty string.

No display, network or running MCP server is needed: the MCP server is built
in-process with mock dependencies and the operation log is patched.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock, patch

import click
import pytest

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.cli.help_json import build_help_json
from aiyes.cli.main import cli
from aiyes.cli.schema_gen import click_to_json_schema, enumerate_commands

from tests.conftest import make_cli_runner

Schemas = Dict[str, Dict[str, Any]]

# The scenario tools describe these parameters with their own text, which is
# published instead of the option's help text (and is the only description of
# the scenario_path argument).
_SCENARIO_TEXT: Dict[Tuple[str, str], str] = {
    ("scenario_run", "real_execution"): (
        "opt-in real GUI/device execution. Defaults to dry-run."
    ),
    ("scenario_run", "public_fixture"): (
        "Reject private/local references while loading public fixtures."
    ),
    ("scenario_run", "evidence_dir"): "Optional local evidence bundle directory.",
    ("scenario_run", "scenario_path"): (
        "Local JSON scenario file to validate and run."
    ),
    ("scenario_run", "profile"): "Evidence detail profile: compact (default) or deep.",
    ("scenario_preflight", "real_execution"): (
        "opt-in readiness check for real GUI/device execution."
    ),
    ("scenario_preflight", "public_fixture"): (
        "Reject private/local references while loading public fixtures."
    ),
    ("scenario_preflight", "evidence_dir"): (
        "Optional evidence directory to validate without writing evidence."
    ),
    ("scenario_preflight", "scenario_path"): (
        "Local JSON scenario file to validate without executing steps."
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# One-parameter commands
# ═══════════════════════════════════════════════════════════════════════

_ONE_PARAMETER_CASES = [
    pytest.param(
        click.Option(["--verbose"], is_flag=True, help="Show more output."),
        {"type": "boolean", "default": False, "description": "Show more output."},
        False,
        id="flag",
    ),
    pytest.param(
        click.Option(["--name"], default=None, help="Name pattern to match."),
        {
            "type": ["string", "null"],
            "default": None,
            "description": "Name pattern to match.",
        },
        False,
        id="string-default-none",
    ),
    pytest.param(
        click.Option(
            ["--count"], type=int, default=3, show_default=True, help="How many."
        ),
        {"type": "integer", "default": 3, "description": "How many."},
        False,
        id="integer-with-default",
    ),
    pytest.param(
        click.Option(["--ratio"], type=float, help="Scale factor."),
        {"type": "number", "description": "Scale factor."},
        False,
        id="number-no-default",
    ),
    pytest.param(
        click.Option(["--out"], type=click.Path(), help="Output file."),
        {"type": "string", "description": "Output file."},
        False,
        id="path",
    ),
    pytest.param(
        click.Option(["--url"], required=True, help="Address to open."),
        {"type": "string", "description": "Address to open."},
        True,
        id="required-string",
    ),
    pytest.param(
        click.Option(
            ["--backend"],
            type=click.Choice(["auto", "x11"]),
            default="auto",
            help="Display backend.",
        ),
        {
            "type": "string",
            "enum": ["auto", "x11"],
            "default": "auto",
            "description": "Display backend.",
        },
        False,
        id="choice-with-default",
    ),
    pytest.param(
        click.Option(["--ignore"], multiple=True, help="Node to ignore."),
        {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": "Node to ignore.",
        },
        False,
        id="multiple-no-default",
    ),
    pytest.param(
        click.Option(["--tag"], multiple=True, default=["a", "b"], help="Tag to add."),
        {
            "type": "array",
            "items": {"type": "string"},
            "default": ["a", "b"],
            "description": "Tag to add.",
        },
        False,
        id="multiple-list-default",
    ),
    pytest.param(
        click.Option(["--path"], multiple=True, required=True, help="File to read."),
        {"type": "array", "items": {"type": "string"}, "description": "File to read."},
        True,
        id="required-multiple",
    ),
    pytest.param(
        click.Option(["--mode"], default=None),
        {"type": ["string", "null"], "default": None},
        False,
        id="option-without-help",
    ),
    pytest.param(
        click.Option(["--level"], default=None, help=""),
        {"type": ["string", "null"], "default": None},
        False,
        id="option-with-empty-help",
    ),
    pytest.param(
        click.Argument(["target"]),
        {"type": "string"},
        True,
        id="positional-argument",
    ),
]


@pytest.mark.parametrize(
    ("parameter", "expected_property", "required"), _ONE_PARAMETER_CASES
)
def test_the_schema_of_a_one_parameter_command(
    parameter: click.Parameter,
    expected_property: Dict[str, Any],
    required: bool,
) -> None:
    schema = click_to_json_schema(click.Command("probe", params=[parameter]))

    assert parameter.name is not None
    expected: Dict[str, Any] = {
        "type": "object",
        "properties": {parameter.name: expected_property},
    }
    if required:
        expected["required"] = [parameter.name]
    assert schema == expected


# ═══════════════════════════════════════════════════════════════════════
# The real command tree
# ═══════════════════════════════════════════════════════════════════════


def _option_kind(option: click.Option) -> str:
    if option.is_flag:
        return "flag"
    if option.multiple:
        return "multiple"
    if isinstance(option.type, click.Choice):
        return "choice"
    return "value"


def _expected_descriptions() -> Tuple[Dict[str, Dict[str, Optional[str]]], Set[str]]:
    """The description each parameter of each command should carry (None for
    none), and the kinds of the options expected to carry their own help text."""
    expected: Dict[str, Dict[str, Optional[str]]] = {}
    kinds: Set[str] = set()
    for command in enumerate_commands(cli):
        per_parameter: Dict[str, Optional[str]] = {}
        for parameter in command.click_command.params:
            if parameter.name is None or parameter.name == "help":
                continue
            key = (command.tool_name, parameter.name)
            if key in _SCENARIO_TEXT:
                per_parameter[parameter.name] = _SCENARIO_TEXT[key]
            elif isinstance(parameter, click.Option) and parameter.help:
                per_parameter[parameter.name] = parameter.help
                kinds.add(_option_kind(parameter))
            else:
                per_parameter[parameter.name] = None
        expected[command.tool_name] = per_parameter
    return expected, kinds


def _mock_mcp_server() -> Any:
    fields = {
        field.name: MagicMock() for field in dataclasses.fields(ServerDependencies)
    }
    return create_mcp_server(ServerDependencies(**fields))


def _generator_schemas() -> Schemas:
    return {
        command.tool_name: command.json_schema for command in enumerate_commands(cli)
    }


def _help_json_command_schemas() -> Schemas:
    runner = make_cli_runner()
    with patch("aiyes.cli.main.operation_log_adapter", MagicMock()):
        result = runner.invoke(cli, ["help-json"])
    assert result.exit_code == 0, result.stderr
    return {
        command["tool_name"]: command["parameters"]
        for command in json.loads(result.stdout)["commands"]
    }


def _mcp_tool_schemas() -> Schemas:
    tools = asyncio.run(_mock_mcp_server().list_tools())
    return {tool.name: tool.inputSchema for tool in tools}


@pytest.mark.parametrize(
    "read_schemas",
    [_generator_schemas, _help_json_command_schemas, _mcp_tool_schemas],
    ids=["generator", "help-json-command", "mcp-tool-definitions"],
)
def test_every_parameter_of_the_real_commands_has_the_expected_description(
    read_schemas: Callable[[], Schemas],
) -> None:
    expected, kinds = _expected_descriptions()
    schemas = read_schemas()

    assert set(schemas) == set(expected)
    assert {"flag", "value", "choice", "multiple"} <= kinds
    described = {(tool, name) for tool, names in expected.items() for name in names}
    assert set(_SCENARIO_TEXT) <= described

    wrong: List[str] = []
    for tool, per_parameter in expected.items():
        properties = schemas[tool]["properties"]
        for name, want in per_parameter.items():
            if name not in properties:
                wrong.append(f"{tool}.{name}: parameter missing")
                continue
            got = properties[name]
            if ("description" in got, got.get("description")) != (
                want is not None,
                want,
            ):
                shown = got.get("description", "<no description>")
                wanted = "<no description>" if want is None else want
                wrong.append(f"{tool}.{name}: {shown!r}, expected {wanted!r}")
    assert wrong == []


def test_the_help_json_tool_returns_the_described_parameters() -> None:
    result = asyncio.run(_mock_mcp_server().call_tool("help_json", {}))

    assert not result.isError
    returned = json.loads("".join(item.text for item in result.content))
    parameters = {
        command["tool_name"]: command["parameters"] for command in returned["commands"]
    }
    within_name = next(
        parameter
        for parameter in cli.commands["find"].params
        if parameter.name == "within_name"
    )
    assert within_name.help
    assert (
        parameters["find"]["properties"]["within_name"].get("description")
        == within_name.help
    )
    assert parameters == {
        command["tool_name"]: command["parameters"]
        for command in build_help_json(cli)["commands"]
    }


def test_generated_definitions_serialize_and_every_description_is_text() -> None:
    help_json = build_help_json(cli)
    json.dumps(help_json)
    tools = asyncio.run(_mock_mcp_server().list_tools())
    for tool in tools:
        json.dumps(tool.inputSchema)

    schemas = [command["parameters"] for command in help_json["commands"]]
    schemas += [tool.inputSchema for tool in tools]
    descriptions = [
        prop["description"]
        for schema in schemas
        for prop in schema["properties"].values()
        if "description" in prop
    ]
    assert descriptions
    assert [d for d in descriptions if type(d) is not str or not d] == []
