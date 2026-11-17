"""The description selector on the command line, the MCP tools and scenarios.

Covers how each surface passes the selector to the use cases, the schemas
and help text, the Android refusal, the empty-value rule on every surface,
scenario steps (threading, the free-text description label, the recorded
selector, load-time checks) and the listing keys across surfaces.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

import aiyes.cli.main as cli_main
from aiyes.adapters.diagnostic_log import InMemoryDiagnosticLog
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_loader import load_scenario_file
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.cli.schema_gen import enumerate_commands
from aiyes.domain.scenario import ScenarioStep, validate_scenario_document
from aiyes.domain.use_cases.compound_do import CompoundDoResult
from aiyes.domain.use_cases.find import FindResult
from aiyes.domain.use_cases.wait import WaitResult
from tests.description_support import (
    ABSENT,
    NOT_GIVEN_VALUES,
    SESSION_ID,
    Ports,
    forest,
    node,
    ports_for,
    run,
    takes_description_selector,
    to_tree,
)

_SELECTOR_TOOLS = {"find", "wait", "do"}


# ──────────────────────────────────────────────────────────────────────
# Surface harnesses
# ──────────────────────────────────────────────────────────────────────


class _NullOperationLog:
    def append(self, record: Any) -> None:
        pass


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    args: List[str],
    find: Any = None,
    wait: Any = None,
    do: Any = None,
) -> Tuple[str, str, int]:
    for name, value in (("find_uc", find), ("wait_uc", wait), ("compound_do_uc", do)):
        if value is not None:
            monkeypatch.setattr(cli_main, name, value)
    monkeypatch.setattr(cli_main, "resolve_session_id", lambda raw: SESSION_ID)
    monkeypatch.setattr(cli_main, "operation_log_adapter", _NullOperationLog())
    result = CliRunner().invoke(cli_main.cli, args)
    return result.stdout, result.stderr, result.exit_code


def _cli_ports(monkeypatch: pytest.MonkeyPatch, ports: Ports, args: List[str]):
    return _cli(
        monkeypatch, args, find=ports.find_uc(), wait=ports.wait_uc(), do=ports.do_uc()
    )


def _mcp(tool: str, arguments: Dict[str, Any], **use_cases: Any) -> Tuple[str, bool]:
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    fields.update(
        resolve_session_id=lambda raw: SESSION_ID,
        clock=SimpleNamespace(now=lambda: 0.0),
        operation_log=_NullOperationLog(),
        **use_cases,
    )
    server = create_mcp_server(ServerDependencies(**fields))
    result = asyncio.run(
        server.call_tool(tool, {"session_id": SESSION_ID, **arguments})
    )
    return result.content[0].text, bool(result.isError)


def _mcp_ports(ports: Ports, tool: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
    return _mcp(
        tool,
        arguments,
        find_uc=ports.find_uc(),
        wait_uc=ports.wait_uc(),
        compound_do_uc=ports.do_uc(),
    )


def _executor(find: Any, wait: Any, ports: Optional[Ports] = None, log: Any = None):
    executor = ScenarioUseCaseExecutor(
        session_start=SimpleNamespace(
            execute=lambda **kw: SimpleNamespace(session_id=SESSION_ID)
        ),
        inspect=None,
        find=find,
        wait=wait,
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: None),
        screenshot=SimpleNamespace(execute=lambda **kw: None),
        session_stop=SimpleNamespace(execute=lambda **kw: None),
        gesture=SimpleNamespace(execute=lambda **kw: None),
        session_repo=ports.repo if ports else None,
        clock=ports.clock if ports else SimpleNamespace(now=lambda: 0.0),
        diagnostic_log=log,
    )
    executor.execute(ScenarioStep(id="start", kind="start_session", parameters={}))
    return executor


def _scenario(ports: Ports, steps: List[Tuple[str, str, Dict[str, Any]]]):
    log = InMemoryDiagnosticLog()
    executor = _executor(ports.find_uc(), ports.wait_uc(), ports, log)
    results = [
        executor.execute(ScenarioStep(id=step_id, kind=kind, parameters=params))
        for step_id, kind, params in steps
    ]
    outcome = [(r.status, dict(r.output), r.error) for r in results]
    events = [dataclasses.asdict(e) for e in log.events]
    return outcome, events


class _Spy:
    """A use case that records its keyword arguments and returns a canned result."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: List[Dict[str, Any]] = []

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def _spies() -> Dict[str, _Spy]:
    return {
        "find": _Spy(FindResult()),
        "wait": _Spy(WaitResult(found=True, id="n_001")),
        "do": _Spy(
            CompoundDoResult(
                found=None, action_result=None, error="No matching node found"
            )
        ),
    }


# ──────────────────────────────────────────────────────────────────────
# Threading: each surface passes the selector to the use case
# ──────────────────────────────────────────────────────────────────────

_CLI_ARGS = {
    "find": ["find", "combo_box"],
    "wait": ["wait", "combo_box", "--timeout", "0"],
    "do": ["do", "--role", "combo_box", "--action", "click"],
}


@pytest.mark.parametrize("command", sorted(_SELECTOR_TOOLS))
@pytest.mark.parametrize("value", [ABSENT, "frigate  Class", "", "   "])
def test_the_cli_passes_the_option_value_unchanged(
    monkeypatch: pytest.MonkeyPatch, command: str, value: Any
) -> None:
    spies = _spies()
    args = list(_CLI_ARGS[command])
    if value is not ABSENT:
        args += ["--description", value]
    _, stderr, code = _cli(monkeypatch, args, **spies)
    assert code == 0, stderr
    (call,) = spies[command].calls
    assert "description_pattern" in call, call
    assert call["description_pattern"] == (None if value is ABSENT else value)


_MCP_ARGS = {
    "find": {"role": "combo_box"},
    "wait": {"role": "combo_box", "timeout": 0.0},
    "do": {"role": "combo_box", "action_name": "click"},
}
_USE_CASE_FIELD = {"find": "find_uc", "wait": "wait_uc", "do": "compound_do_uc"}


@pytest.mark.parametrize("tool", sorted(_SELECTOR_TOOLS))
@pytest.mark.parametrize("value", [ABSENT, "frigate  Class", None, "", "   "])
def test_mcp_handlers_pass_the_key_only_when_present(tool: str, value: Any) -> None:
    spies = _spies()
    arguments = dict(_MCP_ARGS[tool])
    if value is not ABSENT:
        arguments["description_pattern"] = value
    _, is_error = _mcp(
        tool, arguments, **{_USE_CASE_FIELD[t]: s for t, s in spies.items()}
    )
    assert not is_error
    (call,) = spies[tool].calls
    if value is ABSENT:
        # Guard (holds at the base): no description keyword without the key.
        assert "description_pattern" not in call
    else:
        assert "description_pattern" in call, call
        assert call["description_pattern"] == value


@pytest.mark.parametrize("kind", ["find", "wait"])
@pytest.mark.parametrize("value", [ABSENT, "frigate  Class", None, "", "   "])
def test_scenario_steps_pass_the_key_only_when_present(kind: str, value: Any) -> None:
    spies = _spies()
    executor = _executor(spies["find"], spies["wait"])
    params: Dict[str, Any] = {"role": "combo_box"}
    if value is not ABSENT:
        params["description_pattern"] = value
    executor.execute(ScenarioStep(id="s", kind=kind, parameters=params))
    (call,) = spies[kind].calls
    if value is ABSENT:
        # Guard (holds at the base): no description keyword without the key.
        assert "description_pattern" not in call
    else:
        assert "description_pattern" in call, call
        assert call["description_pattern"] == value


# ──────────────────────────────────────────────────────────────────────
# Schemas and help text
# ──────────────────────────────────────────────────────────────────────

# sha256 of every command's property types, defaults, required flags, enums
# and item types at the implementation base (help text excluded).
_BASE_SCHEMA_SHA256 = "ce635d97953a8db5b49d1d8a6331489635a9be73639f89646f8545f570967fb9"


def _schema_snapshot(commands: List[Any]) -> str:
    snapshot = {}
    for c in commands:
        required = set(c.json_schema.get("required", []))
        snapshot[c.tool_name] = {
            name: [
                prop.get("type"),
                "default" in prop,
                prop.get("default"),
                name in required,
                prop.get("enum"),
                prop.get("items"),
            ]
            for name, prop in c.json_schema["properties"].items()
            if not (name == "description_pattern" and c.tool_name in _SELECTOR_TOOLS)
        }
    return json.dumps(snapshot, sort_keys=True)


def _help_json(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    monkeypatch.setattr(cli_main, "operation_log_adapter", _NullOperationLog())
    result = CliRunner().invoke(cli_main.cli, ["help-json"])
    assert result.exit_code == 0, result.output
    return {
        c["tool_name"]: c["parameters"] for c in json.loads(result.stdout)["commands"]
    }


def _mcp_tools() -> Dict[str, Any]:
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    server = create_mcp_server(ServerDependencies(**fields))
    return {t.name: t.inputSchema for t in asyncio.run(server.list_tools())}


def test_find_wait_and_do_gain_exactly_one_optional_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = enumerate_commands(cli_main.cli)
    having = {
        c.tool_name
        for c in commands
        if "description_pattern" in c.json_schema["properties"]
    }
    assert having == _SELECTOR_TOOLS
    for c in commands:
        if c.tool_name in _SELECTOR_TOOLS:
            prop = c.json_schema["properties"]["description_pattern"]
            assert prop["type"] == ["string", "null"]
            assert prop["default"] is None
            assert "description_pattern" not in c.json_schema.get("required", [])
    snapshot = _schema_snapshot(commands)
    assert hashlib.sha256(snapshot.encode()).hexdigest() == _BASE_SCHEMA_SHA256, (
        snapshot
    )
    for listing in (_help_json(monkeypatch), _mcp_tools()):
        assert {
            name
            for name, schema in listing.items()
            if "description_pattern" in schema["properties"]
        } == _SELECTOR_TOOLS


def _option(command: str) -> click.Option:
    params = [
        p
        for p in cli_main.cli.commands[command].params
        if isinstance(p, click.Option) and "--description" in p.opts
    ]
    assert len(params) == 1, f"{command} has no --description option"
    return params[0]


def test_one_help_text_on_find_wait_do_and_in_the_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = {command: _option(command) for command in sorted(_SELECTOR_TOOLS)}
    assert {o.name for o in options.values()} == {"description_pattern"}
    texts = {o.help for o in options.values()}
    assert len(texts) == 1, texts
    (text,) = texts
    assert text
    for word in ("whole", "case", "whitespace", "part", "empty", "android"):
        assert word in text.lower(), word
    for listing in (_help_json(monkeypatch), _mcp_tools()):
        for tool in _SELECTOR_TOOLS:
            assert (
                listing[tool]["properties"]["description_pattern"]["description"]
                == text
            )


# ──────────────────────────────────────────────────────────────────────
# Android: a given selector is refused before the first tree read
# ──────────────────────────────────────────────────────────────────────

_ANDROID_TREE = forest(node("frame", "W", children=[node("Button", "Login", "x")]))


def _android_use_case_text(command: str) -> Tuple[str, List[Any]]:
    ports = ports_for(_ANDROID_TREE, backend="android")
    use_case = {"find": ports.find_uc, "wait": ports.wait_uc, "do": ports.do_uc}[
        command
    ]()
    assert takes_description_selector(use_case.execute)
    kwargs: Dict[str, Any] = {"session_id": SESSION_ID, "role": "*"}
    if command == "do":
        kwargs["action_name"] = "click"
    if command == "wait":
        kwargs["timeout"] = 1.0
    with pytest.raises(Exception) as caught:
        run(use_case.execute, "x", **kwargs)
    return str(caught.value), ports.log


@pytest.mark.parametrize("command", sorted(_SELECTOR_TOOLS))
def test_android_refuses_a_given_selector_on_every_surface(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    text, log = _android_use_case_text(command)
    # No tree read, no poll, no action, no stored tree.
    assert log == []
    for part in (
        "Android",
        "description",
        "content description",
        "NAME_PATTERN",
        "--name",
        "name_pattern",
    ):
        assert part in text, part

    cli_args = {
        "find": ["find", "*", "--description", "x"],
        "wait": ["wait", "*", "--timeout", "1", "--description", "x"],
        "do": ["do", "--role", "*", "--action", "click", "--description", "x"],
    }[command]
    ports = ports_for(_ANDROID_TREE, backend="android")
    stdout, stderr, code = _cli_ports(monkeypatch, ports, cli_args)
    assert (stdout, stderr.strip(), code) == ("", f"Error: {text}", 1)
    assert ports.log == []

    ports = ports_for(_ANDROID_TREE, backend="android")
    mcp_args = {"role": "*", "description_pattern": "x"}
    if command == "do":
        mcp_args["action_name"] = "click"
    assert _mcp_ports(ports, command, mcp_args) == (text, True)
    assert ports.log == []

    if command in ("find", "wait"):
        ports = ports_for(_ANDROID_TREE, backend="android")
        outcome, _ = _scenario(
            ports, [("s", command, {"role": "*", "description_pattern": "x"})]
        )
        assert [(status, error) for status, _, error in outcome] == [("failed", text)]
        assert ports.log == []


# ──────────────────────────────────────────────────────────────────────
# A selector that is not given changes nothing, on every surface
# ──────────────────────────────────────────────────────────────────────

_TREE = forest(
    node(
        "frame",
        "W",
        children=[
            node("combo box", "Alpha", "frigate-class"),
            node("push button", "Alpha", "other"),
            node("filler", "Named", children=[node("combo box", "Beta", "  ")]),
        ],
    )
)
_LATER = forest(node("frame", "W"))
_MODES = ["present", "absent", "transient"]


def _cli_runs(monkeypatch, backend: str, value: Any) -> List[Any]:
    extra = [] if value is ABSENT else ["--description", value]
    calls = [["find", "push_button", "Alpha", "--within-name", "Named"]]
    calls += [
        ["wait", "label", "Alpha", "--timeout", "0.4"]
        + ([f"--{mode}"] if mode != "present" else [])
        for mode in _MODES
    ]
    calls.append(
        [
            "do",
            "--role",
            "combo_box",
            "--name",
            "Alpha",
            "--action",
            "click",
            "--timeout",
            "0.5",
            "--verify",
        ]
    )
    out = []
    for args in calls:
        ports = Ports(snapshots=[to_tree(_TREE), to_tree(_LATER)], backend=backend)
        out.append((_cli_ports(monkeypatch, ports, args + extra), ports.log))
    return out


def _mcp_runs(backend: str, value: Any) -> List[Any]:
    extra = {} if value is ABSENT else {"description_pattern": value}
    calls = [
        (
            "find",
            {"role": "push_button", "name_pattern": "Alpha", "within_name": "Named"},
        )
    ]
    calls += [
        (
            "wait",
            {
                "role": "label",
                "name_pattern": "Alpha",
                "timeout": 0.4,
                "absent": mode == "absent",
                "transient": mode == "transient",
            },
        )
        for mode in _MODES
    ]
    calls.append(
        (
            "do",
            {
                "role": "combo_box",
                "name_pattern": "Alpha",
                "action_name": "click",
                "timeout": 0.5,
                "verify": True,
            },
        )
    )
    out = []
    for tool, arguments in calls:
        ports = Ports(snapshots=[to_tree(_TREE), to_tree(_LATER)], backend=backend)
        out.append((_mcp_ports(ports, tool, {**arguments, **extra}), ports.log))
    return out


def _scenario_runs(backend: str, value: Any) -> List[Any]:
    extra = {} if value is ABSENT else {"description_pattern": value}
    steps = [
        (
            "required",
            "find",
            {
                "role": "push_button",
                "name_pattern": "Alpha",
                "within_name": "Named",
                "required": True,
                **extra,
            },
        ),
        ("optional", "find", {"role": "label", "name_pattern": "Nothing", **extra}),
        ("consume", "action", {"source": "optional", "action": "click"}),
    ]
    steps += [
        (
            f"wait_{mode}",
            "wait",
            {
                "role": "label",
                "name_pattern": "Alpha",
                "timeout": 0.4,
                "absent": mode == "absent",
                "transient": mode == "transient",
                **extra,
            },
        )
        for mode in _MODES
    ]
    ports = Ports(snapshots=[to_tree(_TREE), to_tree(_LATER)], backend=backend)
    return [_scenario(ports, steps), ports.log]


@pytest.mark.parametrize("backend", ["linux", "android"])
@pytest.mark.parametrize("value", [v for v in NOT_GIVEN_VALUES if v is not None])
def test_on_the_cli_an_empty_option_is_the_absent_option(
    monkeypatch: pytest.MonkeyPatch, backend: str, value: str
) -> None:
    assert _cli_runs(monkeypatch, backend, value) == _cli_runs(
        monkeypatch, backend, ABSENT
    )


@pytest.mark.parametrize("surface", ["mcp", "scenario"])
@pytest.mark.parametrize("backend", ["linux", "android"])
@pytest.mark.parametrize("value", NOT_GIVEN_VALUES)
def test_an_empty_key_is_the_absent_key_on_mcp_and_scenarios(
    surface: str, backend: str, value: Optional[str]
) -> None:
    # Guard on this surface at the base (the key is ignored there); it pins
    # that an empty value is neither applied, echoed nor refused.
    runs = _mcp_runs if surface == "mcp" else _scenario_runs
    assert runs(backend, value) == runs(backend, ABSENT)


# ──────────────────────────────────────────────────────────────────────
# Scenario steps: the free-text label, the recorded selector, load checks
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["find", "wait", "scroll_into_view"])
def test_a_prose_description_key_changes_no_step(kind: str) -> None:
    # Guard (holds at the base): the free-text label is never a selector.
    def run_step(params: Dict[str, Any]) -> Tuple[Any, Any]:
        found = FindResult(
            nodes=(),
        )
        find = _Spy(found)
        wait = _Spy(WaitResult(found=True, id="n_001"))
        if kind == "scroll_into_view":
            from aiyes.domain.use_cases.find import FoundNode

            find = _Spy(
                FindResult(
                    nodes=(FoundNode("n_001", "label", "x", (0, 0, 1, 1), (), ()),)
                )
            )
        executor = _executor(find, wait)
        result = executor.execute(ScenarioStep(id="s", kind=kind, parameters=params))
        spy = wait if kind == "wait" else find
        return spy.calls, (result.status, dict(result.output), result.error)

    params = {"role": "label", "name_pattern": "x"}
    assert run_step({**params, "description": "Opens the settings page"}) == run_step(
        params
    )


def test_description_pattern_filters_and_the_prose_label_does_not() -> None:
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "A", "prose", node_id="prose_node"),
                node("combo box", "B", "frigate-class", node_id="target"),
            ],
        )
    )

    def ids(kind: str, params: Dict[str, Any]) -> Any:
        ports = ports_for(raw)
        outcome, _ = _scenario(ports, [("s", kind, params)])
        output = outcome[0][1]
        return (
            [n["id"] for n in output["nodes"]] if kind == "find" else output.get("id")
        )

    labelled = {"role": "combo_box", "description": "prose"}
    selected = {**labelled, "description_pattern": "frigate-class"}
    assert ids("find", selected) == ["target"]
    assert ids("find", labelled) == ["prose_node", "target"]
    assert ids("wait", {**selected, "timeout": 0}) == "target"
    assert ids("wait", {**labelled, "timeout": 0}) == "prose_node"


def _recorded(value: Any) -> Tuple[Any, str]:
    ports = ports_for(forest(node("frame", "W", children=[node("label", "Other")])))
    extra = {} if value is ABSENT else {"description_pattern": value}
    params = {"role": "combo_box", "name_pattern": "C", **extra}
    outcome, events = _scenario(
        ports,
        [
            ("must", "find", {**params, "required": True}),
            ("may", "find", params),
            ("use", "action", {"source": "may", "action": "click"}),
        ],
    )
    (summary,) = [e["diagnostic_summary"] for e in events if e["step_id"] == "must"]
    return outcome[2][1]["requested_selector"], summary


@pytest.mark.parametrize("value", [ABSENT, *NOT_GIVEN_VALUES, "frigate"])
def test_the_recorded_selector_and_summary_name_only_a_given_selector(
    value: Any,
) -> None:
    selector, summary = _recorded(value)
    base_selector = {"role": "combo_box", "name_pattern": "C", "state": None}
    base_summary = (
        "required find 'must' matched zero nodes (role='combo_box', name_pattern='C')"
    )
    if value == "frigate":
        assert selector == {**base_selector, "description_pattern": "frigate"}
        assert "description_pattern='frigate'" in summary
        assert "role='combo_box'" in summary and "name_pattern='C'" in summary
    else:
        # Guard (holds at the base): a value that is not given is not echoed.
        assert selector == base_selector
        assert summary == base_summary


def _document(step: Dict[str, Any], where: str) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = [
        {"id": "start", "kind": "start_session", "command": "app"},
    ]
    cleanup: List[Dict[str, Any]] = [{"id": "stop", "kind": "stop_session"}]
    (steps if where == "steps" else cleanup).insert(1 if where == "steps" else 0, step)
    return {
        "schema_version": 1,
        "id": "scn",
        "title": "description selector scenario",
        "target": "linux",
        "prerequisites": [],
        "steps": steps,
        "cleanup": cleanup,
        "evidence_policy": {},
    }


@pytest.mark.parametrize("kind", ["find", "wait"])
@pytest.mark.parametrize("where", ["steps", "cleanup"])
@pytest.mark.parametrize("value", [5, True, [], {}, ABSENT, None, "frigate-class"])
def test_load_and_preflight_check_the_selector_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, where: str, value: Any
) -> None:
    step: Dict[str, Any] = {"id": "s", "kind": kind, "role": "combo_box"}
    if value is not ABSENT:
        step["description_pattern"] = value
    document = _document(step, where)
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    loaded = load_scenario_file(path)
    issues = [dataclasses.asdict(i) for i in loaded.issues]
    monkeypatch.setattr(cli_main, "operation_log_adapter", _NullOperationLog())
    preflight = CliRunner().invoke(cli_main.cli, ["scenario", "preflight", str(path)])
    if value is ABSENT or value is None or isinstance(value, str):
        # Guard (holds at the base): a string, null or no key gives no issue.
        assert loaded.ok, issues
        assert validate_scenario_document(document).ok
        assert preflight.exit_code == 0, preflight.output
        return
    index = 1 if where == "steps" else 0
    ours = [i for i in issues if i["path"].endswith(".description_pattern")]
    assert len(ours) == 1, issues
    assert ours[0]["path"] == f"{where}[{index}].description_pattern"
    assert ours[0]["code"] == f"{kind}_description_pattern_not_string"
    assert "string" in ours[0]["message"]
    assert issues == ours
    payload = json.loads(preflight.stdout)
    assert preflight.exit_code == 2
    assert payload["failure_code"] == "validation_error"
    assert [e["code"] for e in payload["validation_errors"]] == [ours[0]["code"]]


# ──────────────────────────────────────────────────────────────────────
# The listing on every surface; outputs without it stay as they were
# ──────────────────────────────────────────────────────────────────────

_LISTING_TREE = forest(
    node(
        "frame",
        "W",
        children=[
            node(
                "panel",
                "Box",
                node_id="box",
                children=[
                    node("combo box", "C", "frigate-class", node_id="combo"),
                    node("label", "L", "frigate", node_id="label"),
                ],
            )
        ],
    )
)
_LISTING = {
    "descriptions_containing": ["frigate-class"],
    "descriptions_containing_count": 1,
}


def test_cli_mcp_and_scenario_carry_the_same_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    find_args = [
        "find",
        "combo_box",
        "--within-name",
        "Box",
        "--description",
        "frigate",
    ]
    stdout, stderr, code = _cli_ports(monkeypatch, ports_for(_LISTING_TREE), find_args)
    assert code == 0, stderr
    mcp_text, is_error = _mcp_ports(
        ports_for(_LISTING_TREE),
        "find",
        {"role": "combo_box", "within_name": "Box", "description_pattern": "frigate"},
    )
    assert (mcp_text, is_error) == (stdout.rstrip("\n"), False)
    parsed = json.loads(stdout)
    assert list(parsed) == [
        "nodes",
        "scope_matched",
        "matched_ancestors",
        "role_drift",
        "descriptions_containing",
        "descriptions_containing_count",
    ]
    assert {k: parsed[k] for k in _LISTING} == _LISTING

    outcome, _ = _scenario(
        ports_for(_LISTING_TREE),
        [
            (
                "s",
                "find",
                {
                    "role": "combo_box",
                    "within_name": "Box",
                    "description_pattern": "frigate",
                },
            )
        ],
    )
    output = outcome[0][1]
    assert {k: output.get(k) for k in _LISTING} == _LISTING
    keys = list(output)
    assert keys.index("role_drift") < keys.index("descriptions_containing")

    wait_args = ["wait", "combo_box", "--timeout", "0", "--description", "frigate"]
    stdout, stderr, code = _cli_ports(monkeypatch, ports_for(_LISTING_TREE), wait_args)
    assert code == 0, stderr
    mcp_text, _ = _mcp_ports(
        ports_for(_LISTING_TREE),
        "wait",
        {"role": "combo_box", "timeout": 0.0, "description_pattern": "frigate"},
    )
    assert mcp_text == stdout.rstrip("\n")
    parsed = json.loads(stdout)
    assert list(parsed)[-2:] == list(_LISTING)
    assert {k: parsed[k] for k in _LISTING} == _LISTING
    outcome, _ = _scenario(
        ports_for(_LISTING_TREE),
        [
            (
                "w",
                "wait",
                {"role": "combo_box", "timeout": 0, "description_pattern": "frigate"},
            )
        ],
    )
    assert {k: outcome[0][1].get(k) for k in _LISTING} == _LISTING


def test_new_result_fields_default_to_none_and_outputs_stay_byte_identical() -> None:
    for cls in (WaitResult, CompoundDoResult):
        fields = [f.name for f in dataclasses.fields(cls)]
        assert fields[-2:] == [
            "descriptions_containing",
            "descriptions_containing_count",
        ], cls.__name__
        for field in dataclasses.fields(cls)[-2:]:
            assert field.default is None

    raw = forest(node("frame", "W", children=[node("push button", "OK")]))
    ports = ports_for(raw)
    done = run(
        ports.do_uc().execute,
        ABSENT,
        session_id=SESSION_ID,
        role="push_button",
        action_name="click",
        verify=True,
    )
    from aiyes.cli.presenter import format_do

    leaf = {
        "id": "n_002",
        "role": "push button",
        "name": "OK",
        "bounds": [0, 0, 10, 10],
        "states": ["enabled"],
        "actions": ["click"],
    }
    assert format_do(done) == json.dumps(
        {
            "found": leaf,
            "action": {"status": "ok", "action": "click", "target": "n_002"},
            "verify": {
                "tree": [
                    {
                        "id": "n_001",
                        "role": "frame",
                        "name": "W",
                        "bounds": [0, 0, 10, 10],
                        "states": ["enabled"],
                        "actions": ["click"],
                        "children": [leaf],
                    }
                ]
            },
        },
        indent=2,
    )
    # A description query that finds nothing and lists nothing.
    ports = ports_for(raw)
    missed = run(
        ports.do_uc().execute,
        "zzz",
        session_id=SESSION_ID,
        role="push_button",
        action_name="click",
    )
    assert format_do(missed) == json.dumps(
        {"error": "No matching node found"}, indent=2
    )

    outcome, _ = _scenario(
        ports_for(raw),
        [
            ("hit", "wait", {"role": "push_button", "timeout": 0}),
            (
                "miss",
                "wait",
                {"role": "push_button", "timeout": 0, "description_pattern": "zzz"},
            ),
        ],
    )
    assert outcome[0][1] == {
        "found": True,
        "timeout": False,
        "id": "n_002",
        "transient": False,
    }
    assert outcome[1][1] == {"found": False, "timeout": True, "transient": False}
