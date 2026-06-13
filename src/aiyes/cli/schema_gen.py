"""Schema generation — Click CLI introspection to JSON Schema.

Walks the Click command tree, produces CommandInfo frozen dataclasses
with JSON Schema for each leaf command. Single source of truth for
both help-json output and MCP tool definitions.

This module does NOT import from adapters or domain.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List

import click

_JSON_PRIMITIVE_SCALARS: tuple = (type(None), bool, int, float, str)


def _is_json_primitive(value: Any) -> bool:
    """True iff value is a JSON-serializable structure of primitives only.

    Recurses into list/tuple and dict (string keys only). A list or dict
    containing any non-primitive (e.g. Click Sentinel) is rejected.
    """
    if isinstance(value, _JSON_PRIMITIVE_SCALARS):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_primitive(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _is_json_primitive(v) for k, v in value.items()
        )
    return False


def _is_sentinel(value: Any) -> bool:
    """Detect Click 8.3+ Sentinel.UNSET (and any future Sentinel class).

    Identified by class name to stay forward-compatible across Click
    versions where the Sentinel symbol may move.
    """
    return type(value).__name__ == "Sentinel"


@dataclasses.dataclass(frozen=True)
class CommandInfo:
    """Frozen description of a single CLI leaf command."""

    cli_name: str
    tool_name: str
    description: str
    json_schema: Dict[str, Any]
    click_command: click.Command


def enumerate_commands(cli_group: click.Group) -> List[CommandInfo]:
    """Recursively walk a Click group, returning CommandInfo for each leaf command.

    Groups are excluded — only leaf commands are returned.
    Raises ValueError on duplicate tool_names (collision detection).
    """
    results: List[CommandInfo] = []
    _walk(cli_group, [], results)

    # Collision detection
    seen: Dict[str, str] = {}
    for ci in results:
        if ci.tool_name in seen:
            raise ValueError(
                f"Duplicate tool_name collision: {ci.tool_name!r} "
                f"from {ci.cli_name!r} and {seen[ci.tool_name]!r}"
            )
        seen[ci.tool_name] = ci.cli_name

    return results


def _walk(
    cmd: click.Command,
    prefix: List[str],
    out: List[CommandInfo],
) -> None:
    """Recursively walk the Click tree, collecting leaf commands."""
    if getattr(cmd, "hidden", False):
        return
    if isinstance(cmd, click.Group):
        for name in sorted(cmd.commands):  # type: ignore[union-attr]
            child = cmd.commands[name]  # type: ignore[union-attr]
            _walk(child, prefix + [name], out)
    elif isinstance(cmd, click.Command):
        cli_name = " ".join(prefix)
        tool_name = "_".join(seg.replace("-", "_") for seg in prefix)
        description = cmd.help or cmd.short_help or ""
        schema = click_to_json_schema(cmd)
        _augment_schema_descriptions(tool_name, schema)
        out.append(
            CommandInfo(
                cli_name=cli_name,
                tool_name=tool_name,
                description=description,
                json_schema=schema,
                click_command=cmd,
            )
        )


def click_to_json_schema(command: click.Command) -> Dict[str, Any]:
    """Convert a Click command's parameters to a JSON Schema dict."""
    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param in command.params:
        if param.name is None:
            continue
        # Skip the built-in --help option
        if param.name == "help":
            continue

        prop: Dict[str, Any] = {}
        is_required = False

        if isinstance(param, click.Option):
            if param.help:
                prop["description"] = param.help
            if param.is_flag:
                prop["type"] = "boolean"
                # Respect param.default when it's a JSON primitive; otherwise
                # default-to-False (Sentinel/UNSET → no user choice → False).
                if _is_json_primitive(param.default):
                    prop["default"] = bool(param.default)
                else:
                    prop["default"] = False
            elif getattr(param, "multiple", False):
                item_schema = _click_type_to_schema(param.type, param)
                prop = {"type": "array", "items": item_schema}
                if param.required:
                    is_required = True
                else:
                    # Preserve an explicit primitive list/tuple default; otherwise []
                    if (
                        param.default is not None
                        and not _is_sentinel(param.default)
                        and isinstance(param.default, (list, tuple))
                        and _is_json_primitive(list(param.default))
                    ):
                        prop["default"] = list(param.default)
                    else:
                        prop["default"] = []
            else:
                prop = _click_type_to_schema(param.type, param)
                if param.required:
                    is_required = True
                elif param.default is None:
                    # User explicitly chose None — nullable union with default=None
                    base_type = prop.get("type", "string")
                    if isinstance(base_type, str):
                        prop["type"] = [base_type, "null"]
                    prop["default"] = None
                elif _is_json_primitive(param.default):
                    prop["default"] = param.default
                else:
                    # Sentinel.UNSET / non-primitive marker — user did not choose.
                    # Emit plain base type; do NOT add 'null' to the type union
                    # and do NOT emit a 'default' key.
                    pass
        elif isinstance(param, click.Argument):
            if param.nargs == -1:
                prop["type"] = "array"
                prop["items"] = _click_type_to_schema(param.type, param)
                if param.required:
                    is_required = True
            else:
                prop = _click_type_to_schema(param.type, param)
                if param.required:
                    is_required = True
                elif param.default is None:
                    base_type = prop.get("type", "string")
                    if isinstance(base_type, str):
                        prop["type"] = [base_type, "null"]
                    prop["default"] = None
                elif _is_json_primitive(param.default):
                    prop["default"] = param.default
                else:
                    # Sentinel — user did not choose; plain base type, no default.
                    pass

        properties[param.name] = prop
        if is_required:
            required.append(param.name)

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _augment_schema_descriptions(tool_name: str, schema: Dict[str, Any]) -> None:
    descriptions = _SCENARIO_PARAM_DESCRIPTIONS.get(tool_name, {})
    for name, description in descriptions.items():
        if name in schema["properties"]:
            schema["properties"][name]["description"] = description


_SCENARIO_PARAM_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "scenario_run": {
        "real_execution": "opt-in real GUI/device execution. Defaults to dry-run.",
        "public_fixture": "Reject private/local references while loading public fixtures.",
        "evidence_dir": "Optional local evidence bundle directory.",
        "scenario_path": "Local JSON scenario file to validate and run.",
        "profile": "Evidence detail profile: compact (default) or deep.",
    },
    "scenario_preflight": {
        "real_execution": "opt-in readiness check for real GUI/device execution.",
        "public_fixture": "Reject private/local references while loading public fixtures.",
        "evidence_dir": "Optional evidence directory to validate without writing evidence.",
        "scenario_path": "Local JSON scenario file to validate without executing steps.",
    },
    "scenario_fixtures": {},
}


def _click_type_to_schema(
    click_type: click.ParamType,
    param: click.Parameter,
) -> Dict[str, Any]:
    """Map a Click parameter type to JSON Schema property dict."""
    if isinstance(click_type, click.Choice):
        return {"type": "string", "enum": list(click_type.choices)}
    elif isinstance(click_type, click.types.IntParamType):
        return {"type": "integer"}
    elif isinstance(click_type, click.types.FloatParamType):
        return {"type": "number"}
    elif isinstance(click_type, click.types.BoolParamType):
        return {"type": "boolean"}
    else:
        # Default: string (covers STRING and others)
        return {"type": "string"}
