"""AIYES-42 (CT-03 BUGFIX) — MCP schema JSON-serializability tests — RED phase.

Pins the desired behavior of src/aiyes/cli/schema_gen.click_to_json_schema
after the Click 8.3+ Sentinel.UNSET regression.

Symptom: enumerate_commands(cli) currently emits a wait_stable schema whose
`ignore_nodes` property carries `default=Sentinel.UNSET` (Click 8.3+ default
for multiple=True options without an explicit default), and is shaped as a
scalar string rather than an array of strings. Both bugs are independently
fatal for MCP tools/list, which pydantic-serializes the inputSchema and
fails with PydanticSerializationError, manifesting upstream as a 30s
tools/list timeout (BUG_INTAKE.yaml lines 7-12).

These tests MUST fail on the current code and pass once schema_gen is fixed.

Traceability — VALIDATED_INTENT_PKG.yaml:definition_of_done items 1-4.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from aiyes.cli.main import cli
from aiyes.cli.schema_gen import enumerate_commands


# ===================================================================
# Helpers
# ===================================================================


def _find_property(schema: dict, prop_name: str) -> dict:
    """Locate a named property within a JSON Schema or fail loudly."""
    props = schema.get("properties", {})
    assert prop_name in props, (
        f"Property {prop_name!r} not in schema properties: {list(props)}"
    )
    return props[prop_name]


# ===================================================================
# T1: every command schema is json.dumps-able
# ===================================================================


class TestAllSchemasJsonSerializable:
    """DoD #1: json.dumps succeeds for every emitted command schema."""

    def test_all_command_schemas_are_json_serializable(self) -> None:
        commands = enumerate_commands(cli)
        failures: list[str] = []
        for ci in commands:
            try:
                json.dumps(ci.json_schema)
            except TypeError as exc:
                failures.append(f"{ci.tool_name}: {exc}")
        assert not failures, (
            "json.dumps must not raise for any command schema; failed for: "
            + "; ".join(failures)
        )


# ===================================================================
# T2: multiple=True yields array shape (DoD #2)
# ===================================================================


class TestMultipleTrueYieldsArraySchema:
    """DoD #2: Click multiple=True maps to JSON Schema array-of-strings.

    Currently --ignore-node on wait-stable is the only multiple=True option
    in the CLI; the schema property must be:
        {"type": "array", "items": {"type": "string"}}
    with NO 'default' key (or default=[]) when no user default is supplied.
    """

    def test_multiple_true_option_yields_array_schema(self) -> None:
        commands = {ci.tool_name: ci for ci in enumerate_commands(cli)}
        assert "wait_stable" in commands, "wait_stable command missing"
        schema = commands["wait_stable"].json_schema
        prop = _find_property(schema, "ignore_nodes")

        assert prop.get("type") == "array", (
            f"ignore_nodes type must be 'array', got {prop.get('type')!r}"
        )
        items = prop.get("items")
        assert isinstance(items, dict), (
            f"ignore_nodes.items must be a dict, got {type(items).__name__}"
        )
        assert items.get("type") == "string", (
            f"ignore_nodes.items.type must be 'string', got {items.get('type')!r}"
        )
        if "default" in prop:
            assert prop["default"] == [], (
                f"ignore_nodes.default, if present, must be []; got {prop['default']!r}"
            )


# ===================================================================
# T3: no Sentinel/non-primitive default leaks into any schema (DoD #3)
# ===================================================================


_JSON_PRIMITIVE_DEFAULT_TYPES: tuple[type, ...] = (
    type(None),
    bool,
    int,
    float,
    str,
    list,
    dict,
)


class TestUnsetSentinelDefaultNotEmitted:
    """DoD #3: no 'default' value is ever a non-JSON-primitive (e.g. Sentinel)."""

    def test_unset_sentinel_default_not_emitted(self) -> None:
        commands = enumerate_commands(cli)
        offenders: list[str] = []
        for ci in commands:
            for prop_name, prop_schema in ci.json_schema.get("properties", {}).items():
                if "default" not in prop_schema:
                    continue
                value = prop_schema["default"]
                if not isinstance(value, _JSON_PRIMITIVE_DEFAULT_TYPES):
                    offenders.append(
                        f"{ci.tool_name}.{prop_name}: default={value!r} "
                        f"(type={type(value).__name__})"
                    )
        assert not offenders, (
            "All schema 'default' values must be JSON primitives "
            "(None/bool/int/float/str/list/dict). Offenders: " + "; ".join(offenders)
        )


# ===================================================================
# T4: end-to-end MCP list_tools envelope serializes (DoD #4)
# ===================================================================


class TestMcpListToolsResponseSerializesEndToEnd:
    """DoD #4: the pydantic-serialized list_tools envelope round-trips to JSON.

    We construct a real McpServerWrapper via create_mcp_server() with all
    ServerDependencies fields set to MagicMock — list_tools never invokes the
    use cases, so mock dependencies are sufficient. We then run each Tool
    through pydantic's .model_dump_json(...) using the same mode/exclude
    settings the MCP shared-session uses. Failure mode is identical to
    production: PydanticSerializationError on the offending inputSchema.

    If the optional `mcp` package is unavailable in this environment we skip,
    but only after exercising the schema_gen layer (T1-T3 already cover the
    pure-Python failure mode).
    """

    @pytest.mark.asyncio
    async def test_mcp_list_tools_response_serializes_end_to_end(self) -> None:
        mcp_types = pytest.importorskip("mcp.types")
        from aiyes.adapters.mcp_server import (  # local import: optional dep
            ServerDependencies,
            create_mcp_server,
        )

        fields: dict[str, Any] = {
            f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)
        }
        deps = ServerDependencies(**fields)
        wrapper = create_mcp_server(deps)

        tools = await wrapper.list_tools()
        assert tools, "list_tools() must return at least one Tool"

        # Mimic the pydantic serialization path used by MCP shared-session
        # when sending the tools/list response on the wire.
        serialized: list[str] = []
        for tool in tools:
            assert isinstance(tool, mcp_types.Tool)
            payload = tool.model_dump_json(by_alias=True, exclude_none=True)
            # Round-trip through json to confirm it's actually well-formed.
            decoded = json.loads(payload)
            assert isinstance(decoded, dict)
            assert decoded.get("name")
            serialized.append(payload)

        assert len(serialized) == len(tools)


# ===================================================================
# T5: parametrize-PBT — every (command, property) round-trips json.dumps
# ===================================================================


def _all_command_property_pairs() -> list[tuple[str, str, dict]]:
    """Enumerate (tool_name, property_name, property_schema) once at import."""
    pairs: list[tuple[str, str, dict]] = []
    for ci in enumerate_commands(cli):
        for prop_name, prop_schema in ci.json_schema.get("properties", {}).items():
            pairs.append((ci.tool_name, prop_name, prop_schema))
    return pairs


class TestEverySchemaPropertyRoundTrips:
    """T5: every (command, property) schema round-trips json.dumps + json.loads."""

    @pytest.mark.parametrize(
        "tool_name,prop_name,prop_schema",
        _all_command_property_pairs(),
        ids=lambda v: v if isinstance(v, str) else "schema",
    )
    def test_property_schema_roundtrips_json(
        self, tool_name: str, prop_name: str, prop_schema: dict
    ) -> None:
        try:
            encoded = json.dumps(prop_schema)
        except TypeError as exc:
            pytest.fail(
                f"{tool_name}.{prop_name} not JSON-serializable: {exc} "
                f"(schema={prop_schema!r})"
            )
        decoded = json.loads(encoded)
        assert decoded == prop_schema


# ===================================================================
# AIYES-42 second pass — A10 findings
# ===================================================================

import click  # noqa: E402

from aiyes.cli.schema_gen import (  # noqa: E402
    _is_json_primitive,
    click_to_json_schema,
)


def _prop(cmd: click.Command, name: str) -> dict:
    return _find_property(click_to_json_schema(cmd), name)


# F-01: multiple=True with explicit primitive default must be preserved
class TestF01MultipleTrueRespectsExplicitDefault:
    def test_multiple_true_with_string_tuple_default_preserved(self) -> None:
        @click.command()
        @click.option("--x", multiple=True, default=("a", "b"))
        def cmd(x):  # pragma: no cover - schema-only
            pass

        prop = _prop(cmd, "x")
        assert prop.get("type") == "array"
        assert prop.get("default") == ["a", "b"], (
            f"explicit multiple default must round-trip; got {prop.get('default')!r}"
        )

    def test_multiple_true_with_int_tuple_default_preserved(self) -> None:
        @click.command()
        @click.option("--n", type=int, multiple=True, default=(1, 2))
        def cmd(n):  # pragma: no cover
            pass

        prop = _prop(cmd, "n")
        assert prop.get("type") == "array"
        assert prop["items"]["type"] == "integer"
        assert prop.get("default") == [1, 2]

    def test_multiple_true_without_default_emits_empty_list(self) -> None:
        @click.command()
        @click.option("--x", multiple=True)
        def cmd(x):  # pragma: no cover
            pass

        prop = _prop(cmd, "x")
        assert prop.get("type") == "array"
        assert prop.get("default") == []


# F-02: nargs=-1 Argument items.type must mirror declared type
class TestF02NargsMinusOneItemsTypeFollowsDeclaration:
    def test_nargs_minus_one_int_argument_items_type_is_integer(self) -> None:
        @click.command()
        @click.argument("xs", nargs=-1, type=click.INT)
        def cmd(xs):  # pragma: no cover
            pass

        prop = _prop(cmd, "xs")
        assert prop.get("type") == "array"
        assert prop["items"]["type"] == "integer"

    def test_nargs_minus_one_string_argument_items_type_is_string(self) -> None:
        @click.command()
        @click.argument("xs", nargs=-1)
        def cmd(xs):  # pragma: no cover
            pass

        prop = _prop(cmd, "xs")
        assert prop.get("type") == "array"
        assert prop["items"]["type"] == "string"


# F-03: is_flag must respect param.default
class TestF03IsFlagRespectsDefault:
    def test_is_flag_default_true_emits_true(self) -> None:
        @click.command()
        @click.option("--verbose/--quiet", default=True)
        def cmd(verbose):  # pragma: no cover
            pass

        prop = _prop(cmd, "verbose")
        assert prop.get("type") == "boolean"
        assert prop.get("default") is True

    def test_is_flag_default_false_emits_false(self) -> None:
        @click.command()
        @click.option("--verbose/--quiet", default=False)
        def cmd(verbose):  # pragma: no cover
            pass

        prop = _prop(cmd, "verbose")
        assert prop.get("type") == "boolean"
        assert prop.get("default") is False

    def test_is_flag_no_default_emits_false(self) -> None:
        @click.command()
        @click.option("--flag", is_flag=True)
        def cmd(flag):  # pragma: no cover
            pass

        prop = _prop(cmd, "flag")
        assert prop.get("type") == "boolean"
        assert prop.get("default") is False


# F-04: distinguish None default from Sentinel/UNSET no-default
class TestF04SentinelVsExplicitNone:
    def test_explicit_none_default_yields_nullable_with_default_none(self) -> None:
        @click.command()
        @click.option("--x", type=str, default=None)
        def cmd(x):  # pragma: no cover
            pass

        prop = _prop(cmd, "x")
        assert prop.get("type") == ["string", "null"], (
            f"explicit None default must emit nullable union; got {prop.get('type')!r}"
        )
        assert prop.get("default") is None

    def test_no_default_declared_yields_plain_type_no_default_key(self) -> None:
        @click.command()
        @click.option("--x", type=str)
        def cmd(x):  # pragma: no cover
            pass

        prop = _prop(cmd, "x")
        # Sentinel.UNSET — user did not choose; not nullable, no default key
        assert prop.get("type") == "string", (
            f"no-default option must emit plain base type; got {prop.get('type')!r}"
        )
        assert "default" not in prop, (
            f"no-default option must omit 'default' key; got {prop.get('default')!r}"
        )


# F-07: _is_json_primitive must recurse into lists/dicts
class TestF07IsJsonPrimitiveRecursive:
    def test_list_of_sentinel_is_not_primitive(self) -> None:
        class Sentinel:
            pass

        assert _is_json_primitive([Sentinel()]) is False

    def test_dict_with_sentinel_value_is_not_primitive(self) -> None:
        class Sentinel:
            pass

        assert _is_json_primitive({"k": Sentinel()}) is False

    def test_nested_list_of_primitives_is_primitive(self) -> None:
        assert _is_json_primitive([1, "a", [True, None], {"k": 2}]) is True

    def test_nested_dict_of_primitives_is_primitive(self) -> None:
        assert _is_json_primitive({"a": [1, 2], "b": {"c": "x"}}) is True

    def test_dict_with_non_string_key_is_not_primitive(self) -> None:
        # JSON only allows string keys; recursion must enforce this
        assert _is_json_primitive({1: "x"}) is False

    def test_default_with_list_containing_sentinel_is_not_emitted(self) -> None:
        # Synthesize a Click option whose .default is mutated to a list with
        # a non-primitive sentinel after declaration. The schema must NOT
        # emit a 'default' key carrying that list.
        class Sentinel:
            pass

        @click.command()
        @click.option("--x", type=str)
        def cmd(x):  # pragma: no cover
            pass

        # Replace the option default with a non-primitive-bearing list.
        opt = cmd.params[0]
        object.__setattr__(opt, "default", [Sentinel()])

        prop = _prop(cmd, "x")
        assert "default" not in prop or _is_json_primitive(prop["default"]), (
            f"non-primitive list default must not leak; got {prop.get('default')!r}"
        )
