"""Role text in command output is the text the backend gives, unchanged.

Every command that prints a role prints the text the backend adapter put on
the node, window or event: no alias-table mapping, no underscores for
spaces, no change of case. A field that names a parent (parent_role) carries
the parent's text the same way.

Each test feeds role texts in at a fake port (tree, store, window list or the
event worker's JSON lines), runs the production use case and then the output
function that the CLI command and the MCP tool both call, and reads the role
fields from the decoded JSON at every depth.

The texts are "push button" (spaced), "button" (a key of the alias table)
and "Button" (an Android class-derived name). Mapping spaces to underscores
changes the first, the alias table changes the second, and lower-casing
before the alias table changes all three, so each such change of the output
fails a case below.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

from aiyes.adapters.linux_reactive_wait_adapter import LinuxReactiveWaitObserver
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.cli import presenter
from aiyes.domain.scenario import ReleaseScenario, ScenarioStep
from aiyes.domain.top_level_window import TopLevelWindow
from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase
from aiyes.domain.use_cases.diff import DiffUseCase
from aiyes.domain.use_cases.inspect import InspectUseCase
from aiyes.domain.use_cases.reactive_wait import ReactiveWaitUseCase
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.domain.use_cases.wait_stable import WaitStableUseCase
from tests.description_support import SESSION_ID, Ports, forest, node, ports_for

TEXTS = ("push button", "button", "Button")

# (text of the reported nodes, text of their parent window): every text of
# TEXTS appears once in each position, so parent_role fields see them all.
CASES = [(text, TEXTS[(i + 1) % len(TEXTS)]) for i, text in enumerate(TEXTS)]

# Role requests the tests send; output fields that repeat a request back
# (requested_role, requested_selector) are not backend text.
REQUESTED_ROLE_ECHO_KEYS = ("requested_role", "requested_selector")

ROLE_KEYS = ("role", "parent_role", "window_role", "actual_role", "observed_role")

Raw = List[Dict[str, Any]]


# ──────────────────────────────────────────────────────────────────────
# Trees
# ──────────────────────────────────────────────────────────────────────


def window(text: str, parent: str) -> Raw:
    """A window with role ``parent`` whose children carry ``text``.

    "OK" and "OK again" share a name so a role request that misses both
    reports two role-drift candidates, one per text; "Twin" is the same.
    """
    return forest(
        node(
            parent,
            "Bar",
            node_id="r",
            children=[
                node(text, "OK", node_id="a"),
                node(
                    text,
                    "Box",
                    node_id="b",
                    children=[node(text, "Inner", node_id="c")],
                ),
                node(parent, "OK again", node_id="k"),
                node(text, "Target", node_id="x"),
                node(text, "Twin", node_id="y"),
                node(parent, "Twin", node_id="z"),
            ],
        )
    )


def flip(first: str, second: str) -> Tuple[Raw, Raw]:
    """Two snapshots: node "a" changes its role from ``first`` to ``second``,
    node "g" goes away and node "n" comes in."""
    before = forest(
        node(
            second,
            "Bar",
            node_id="r",
            children=[
                node(first, "OK", node_id="a"),
                node(first, "Gone", node_id="g"),
            ],
        )
    )
    after = forest(
        node(
            second,
            "Bar",
            node_id="r",
            children=[
                node(second, "OK", node_id="a"),
                node(first, "New", node_id="n"),
            ],
        )
    )
    return before, after


def roles_of(raw: Raw) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def walk(item: Dict[str, Any]) -> None:
        out[item["id"]] = item["role"]
        for child in item["children"]:
            walk(child)

    for root in raw:
        walk(root)
    return out


def parents_of(raw: Raw) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}

    def walk(item: Dict[str, Any], parent: Optional[str]) -> None:
        out[item["id"]] = parent
        for child in item["children"]:
            walk(child, item["id"])

    for root in raw:
        walk(root, None)
    return out


# ──────────────────────────────────────────────────────────────────────
# Reading the output
# ──────────────────────────────────────────────────────────────────────


def decode(text: str) -> Any:
    return json.loads(text)


def node_dicts(nodes: Sequence[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """Every node dict of a node list, children included, in pre-order."""
    for item in nodes:
        yield item
        yield from node_dicts(item.get("children", []))


def check_nodes(
    nodes: Sequence[Dict[str, Any]], raw: Raw, parent_role_emitted: bool
) -> List[str]:
    """Each node dict, at every depth, carries its own role text; a
    parent_role carries the parent's text. With ``parent_role_emitted`` every
    node that has a parent must carry the field. Returns the node ids seen."""
    roles = roles_of(raw)
    parents = parents_of(raw)
    seen = []
    for item in node_dicts(nodes):
        node_id = item["id"]
        assert item["role"] == roles[node_id], (node_id, item["role"])
        parent = parents[node_id]
        if parent is not None and (parent_role_emitted or "parent_role" in item):
            assert item["parent_role"] == roles[parent], (
                node_id,
                item.get("parent_role"),
            )
        seen.append(node_id)
    return seen


def role_values(value: Any, path: str = "$") -> Iterator[Tuple[str, str]]:
    """(path, value) of every role-text field at any depth of a decoded output."""
    if isinstance(value, dict):
        role_change = value.get("field") == "role"
        for key, item in value.items():
            here = f"{path}.{key}"
            if key in REQUESTED_ROLE_ECHO_KEYS:
                continue
            if key in ROLE_KEYS and isinstance(item, str):
                yield here, item
            elif key == "observed_roles" and isinstance(item, list):
                for index, text in enumerate(item):
                    yield f"{here}[{index}]", text
            elif role_change and key in ("before", "after", "old", "new"):
                yield here, item
            else:
                yield from role_values(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from role_values(item, f"{path}[{index}]")


def assert_only_fed_texts(decoded: Any) -> None:
    """No role-text field anywhere in the output holds a text that was not fed in."""
    found = list(role_values(decoded))
    assert found, "the output carries no role field"
    stray = [(path, text) for path, text in found if text not in TEXTS]
    assert not stray, stray


def entries(items: Sequence[Dict[str, Any]], *keys: str) -> List[Tuple[Any, ...]]:
    return [tuple(item.get(key) for key in keys) for item in items]


def inspect_use_case(ports: Ports) -> InspectUseCase:
    return InspectUseCase(ports.tree, None, ports.repo, ports.store, None, ports.clock)


def inspect_now(ports: Ports) -> None:
    """Run inspect once, so the store holds the tree with its context fields."""
    inspect_use_case(ports).execute(session_id=SESSION_ID, no_screenshot=True)


def polled_pair(snapshots: Sequence[Raw], ports: Ports) -> Tuple[Raw, Raw]:
    """The last two snapshots a polling use case read from the tree port."""
    polls = ports.log.count(("get_tree",))
    assert 2 <= polls < len(snapshots), polls
    return snapshots[polls - 2], snapshots[polls - 1]


class EventLines:
    """The AT-SPI event worker, replaced by the JSON lines it prints."""

    def __init__(self, lines: Sequence[str]) -> None:
        self._lines = tuple(lines)

    def wait_event(
        self, session: Any, condition: str, timeout: float
    ) -> Tuple[str, ...]:
        return self._lines


def event_lines(text: str, parent: str) -> EventLines:
    return EventLines(
        [
            json.dumps(
                {
                    "type": "focus",
                    "source": "native_event",
                    "timestamp": 1.0,
                    "node_id": "a",
                    "role": text,
                    "name": "OK",
                }
            ),
            json.dumps(
                {
                    "type": "focus",
                    "source": "native_event",
                    "timestamp": 2.0,
                    "node_id": "r",
                    "role": parent,
                    "name": "Bar",
                }
            ),
        ]
    )


class WindowList:
    """Top-level window port listing fixed windows."""

    def __init__(self, windows: Sequence[TopLevelWindow]) -> None:
        self._windows = list(windows)

    def list_top_level_windows(self, session: Any) -> List[TopLevelWindow]:
        return list(self._windows)


class SessionStart:
    """Session start use case that hands out the fakes' session."""

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return {"session_id": SESSION_ID}


# ──────────────────────────────────────────────────────────────────────
# One test per command that prints a role
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, parent", CASES)
def test_find_prints_each_role_as_the_backend_gave_it(text: str, parent: str) -> None:
    raw = window(text, parent)

    everything = decode(
        presenter.format_find_nodes(
            ports_for(raw).find_uc().execute(session_id=SESSION_ID, role="*")
        )
    )
    assert sorted(check_nodes(everything, raw, parent_role_emitted=True)) == sorted(
        roles_of(raw)
    )

    scoped = decode(
        presenter.format_find_nodes(
            ports_for(raw)
            .find_uc()
            .execute(session_id=SESSION_ID, role="*", within_name="Bar")
        )
    )
    assert check_nodes(scoped["nodes"], raw, parent_role_emitted=True)
    assert entries(scoped["matched_ancestors"], "id", "role") == [("r", parent)]

    drift = decode(
        presenter.format_find_nodes(
            ports_for(raw)
            .find_uc()
            .execute(session_id=SESSION_ID, role="tab", name_pattern="OK")
        )
    )
    assert entries(drift["role_drift"], "id", "role") == [("a", text), ("k", parent)]

    for output in (everything, scoped, drift):
        assert_only_fed_texts(output)


@pytest.mark.parametrize("text, parent", CASES)
def test_inspect_prints_each_role_as_the_backend_gave_it(
    text: str, parent: str
) -> None:
    raw = window(text, parent)
    result = inspect_use_case(ports_for(raw)).execute(
        session_id=SESSION_ID, no_screenshot=True
    )

    output = decode(
        presenter.format_inspect_result(
            tree=result.tree,
            screenshot=result.screenshot,
            timestamp=result.timestamp,
            screenshot_base64=result.screenshot_base64,
            screenshot_data=result.screenshot_data,
            diagnostics=result.diagnostics,
        )
    )

    seen = check_nodes(output["tree"]["tree"], raw, parent_role_emitted=True)
    assert sorted(seen) == sorted(roles_of(raw))
    assert_only_fed_texts(output)


@pytest.mark.parametrize("text, parent", CASES)
def test_wait_prints_role_drift_as_the_backend_gave_it(text: str, parent: str) -> None:
    raw = window(text, parent)
    result = (
        ports_for(raw)
        .wait_uc()
        .execute(session_id=SESSION_ID, role="tab", name_pattern="OK", timeout=0.0)
    )

    output = decode(
        presenter.format_wait(
            found=result.found,
            timeout=result.timeout,
            node_id=result.id,
            transient=result.transient,
            role_drift=result.role_drift,
            descriptions_containing=getattr(result, "descriptions_containing", None),
            descriptions_containing_count=getattr(
                result, "descriptions_containing_count", None
            ),
        )
    )

    assert entries(output["role_drift"], "id", "role") == [("a", text), ("k", parent)]
    assert_only_fed_texts(output)


@pytest.mark.parametrize("text, parent", CASES)
def test_do_prints_found_and_verify_roles_as_the_backend_gave_them(
    text: str, parent: str
) -> None:
    raw = window(text, parent)
    result = (
        ports_for(raw)
        .do_uc()
        .execute(
            session_id=SESSION_ID,
            role="*",
            action_name="click",
            name_pattern="Box",
            verify=True,
        )
    )

    output = decode(presenter.format_do(result))

    # The do path does not add parent context today; if it ever prints a
    # parent_role, check_nodes holds it to the parent's text.
    assert check_nodes([output["found"]], raw, parent_role_emitted=False) == ["b", "c"]
    seen = check_nodes(output["verify"]["tree"], raw, parent_role_emitted=False)
    assert sorted(seen) == sorted(roles_of(raw))
    assert_only_fed_texts(output)


@pytest.mark.parametrize("text, parent", CASES)
def test_diff_prints_added_removed_and_changed_roles_as_the_backend_gave_them(
    text: str, parent: str
) -> None:
    old = forest(
        node(
            parent,
            "Bar",
            node_id="r",
            children=[
                node(text, "OK", node_id="a"),
                node(
                    text,
                    "Box",
                    node_id="b",
                    children=[node(text, "Inner", node_id="c")],
                ),
            ],
        )
    )
    new = forest(
        node(
            parent,
            "Bar",
            node_id="r",
            children=[
                node(parent, "OK", node_id="a"),
                node(
                    text,
                    "New",
                    node_id="d",
                    children=[node(parent, "Deep", node_id="e")],
                ),
            ],
        )
    )
    # The tree store keeps the tree inspect saved, with its parent context.
    ports = ports_for(old, new)
    inspect_now(ports)

    output = decode(
        presenter.format_diff(
            DiffUseCase(ports.tree, ports.repo, ports.store).execute(SESSION_ID)
        )
    )

    assert set(check_nodes(output["removed"], old, parent_role_emitted=True)) == {
        "b",
        "c",
    }
    # The live tree is not enriched; a parent_role printed here must still
    # be the parent's text.
    assert set(check_nodes(output["added"], new, parent_role_emitted=False)) == {
        "d",
        "e",
    }
    role_changes = [item for item in output["changed"] if item["field"] == "role"]
    assert entries(role_changes, "id", "before", "after") == [("a", text, parent)]
    assert_only_fed_texts(output)


@pytest.mark.parametrize("text, parent", CASES)
def test_detect_dialog_prints_the_window_role_as_the_backend_gave_it(
    text: str, parent: str
) -> None:
    ports = ports_for(window(text, parent))
    inspect_now(ports)

    reported = []
    for role, name in ((text, "Dialog"), (parent, "Alert")):
        result = DetectDialogUseCase(
            ports.repo,
            ports.store,
            WindowList(
                [
                    TopLevelWindow(role=parent, name="Bar"),
                    TopLevelWindow(role=role, name=name),
                ]
            ),
            WindowList([]),
        ).execute(SESSION_ID)
        output = decode(
            presenter.format_detect_dialog(
                dialog_detected=result.dialog_detected,
                window_name=result.window_name,
                window_role=result.window_role,
                error=result.error,
            )
        )
        assert_only_fed_texts(output)
        reported.append((output["window_name"], output["window_role"]))

    assert reported == [("Dialog", text), ("Alert", parent)]


@pytest.mark.parametrize("text, parent", CASES)
def test_wait_reactive_prints_event_roles_as_the_backend_gave_them(
    text: str, parent: str
) -> None:
    ports = ports_for(window(text, parent))
    observer = LinuxReactiveWaitObserver(
        ports.tree, event_lines(text, parent), ports.clock
    )
    result = ReactiveWaitUseCase(ports.repo, observer).execute(
        session_id=SESSION_ID, condition="focus-change", timeout=1.0
    )

    output = decode(presenter.format_reactive_wait(result))

    assert entries(output["events"], "node_id", "role") == [("a", text), ("r", parent)]
    assert_only_fed_texts(output)


@pytest.mark.parametrize("text, parent", CASES)
def test_wait_stable_prints_role_changes_as_the_backend_gave_them(
    text: str, parent: str
) -> None:
    snapshots = list(flip(text, parent)) * 20
    ports = ports_for(*snapshots)
    result = WaitStableUseCase(ports.tree, ports.repo, ports.clock).execute(
        SESSION_ID, timeout=1.0, poll_interval=0.25, consecutive=3
    )

    output = decode(
        presenter.format_wait_stable(
            stable=result.stable,
            timeout=result.timeout,
            polls=result.polls,
            changes=result.changes,
            comparison_mode=result.comparison_mode,
        )
    )

    earlier, later = polled_pair(snapshots, ports)
    role_changes = [item for item in output["changes"] if item.get("field") == "role"]
    assert entries(role_changes, "node_id", "old", "new") == [
        ("a", roles_of(earlier)["a"], roles_of(later)["a"])
    ]
    assert_only_fed_texts(output)


# ──────────────────────────────────────────────────────────────────────
# Scenario run output, both evidence profiles
# ──────────────────────────────────────────────────────────────────────


def selector_candidates(step: Dict[str, Any]) -> List[List[Tuple[Any, ...]]]:
    """The candidates of every selector_diagnostics block a step record carries."""
    blocks = [
        step.get("selector_diagnostics"),
        step["output"].get("selector_diagnostics"),
    ]
    return [
        sorted(entries(block["candidates"], "node_id", "role", "observed_role"))
        for block in blocks
        if block is not None
    ]


@pytest.mark.parametrize("profile", ["compact", "deep"])
@pytest.mark.parametrize("text, parent", CASES)
def test_scenario_run_prints_every_step_role_as_the_backend_gave_it(
    text: str, parent: str, profile: str
) -> None:
    raw = window(text, parent)
    ports = ports_for(raw)
    settling = list(flip(text, parent)) * 20
    settle_ports = ports_for(*settling)
    executor = ScenarioUseCaseExecutor(
        session_start=SessionStart(),
        inspect=inspect_use_case(ports),
        find=ports.find_uc(),
        action=None,
        type_text=None,
        screenshot=None,
        session_stop=None,
        wait=ports.wait_uc(),
        wait_stable=WaitStableUseCase(
            settle_ports.tree, ports.repo, settle_ports.clock
        ),
        reactive_wait=ReactiveWaitUseCase(
            ports.repo,
            LinuxReactiveWaitObserver(
                ports.tree, event_lines(text, parent), ports.clock
            ),
        ),
        session_repo=ports.repo,
        clock=ports.clock,
    )
    steps = (
        ScenarioStep("start", "start_session", {"command": "app"}),
        ScenarioStep("find_all", "find", {"role": "*"}),
        ScenarioStep("find_scoped", "find", {"role": "*", "within_name": "Bar"}),
        ScenarioStep("find_drift", "find", {"role": "tab", "name_pattern": "OK"}),
        ScenarioStep("inspect", "inspect", {"no_screenshot": True}),
        ScenarioStep(
            "wait_drift",
            "wait",
            {
                "role": "tab",
                "name_pattern": "OK",
                "timeout": 0.0,
                "allow_timeout": True,
            },
        ),
        ScenarioStep(
            "events", "wait_reactive", {"condition": "focus-change", "timeout": 1.0}
        ),
        ScenarioStep(
            "settle",
            "wait_stable",
            {
                "timeout": 1.0,
                "interval": 0.25,
                "consecutive": 3,
                "allow_timeout": True,
            },
        ),
        ScenarioStep(
            "scroll_one", "scroll_into_view", {"role": "tab", "name_pattern": "Target"}
        ),
        # Two nodes named "Twin" match: the step fails as ambiguous, so it is last.
        ScenarioStep(
            "scroll_two", "scroll_into_view", {"role": "tab", "name_pattern": "Twin"}
        ),
    )
    scenario = ReleaseScenario(
        schema_version=1,
        id="role-text",
        title="role text",
        target="linux",
        prerequisites=(),
        steps=steps,
        cleanup=(),
        evidence_policy={},
    )
    result = ScenarioRunUseCase(executor).execute(scenario)

    output = decode(presenter.format_scenario_run(result, profile=profile))

    by_id = {step["step_id"]: step for step in output["steps"]}
    assert list(by_id) == [step.id for step in steps]
    out = {step_id: step["output"] for step_id, step in by_id.items()}

    assert sorted(
        check_nodes(out["find_all"]["nodes"], raw, parent_role_emitted=True)
    ) == sorted(roles_of(raw))
    assert check_nodes(out["find_scoped"]["nodes"], raw, parent_role_emitted=True)
    assert entries(out["find_scoped"]["matched_ancestors"], "id", "role") == [
        ("r", parent)
    ]
    assert entries(out["find_drift"]["role_drift"], "id", "role") == [
        ("a", text),
        ("k", parent),
    ]
    assert selector_candidates(by_id["find_drift"])
    for candidates in selector_candidates(by_id["find_drift"]):
        assert candidates == sorted([("a", text, text), ("k", parent, parent)])

    # The compact profile leaves the inspect tree out; the deep profile keeps it.
    if profile == "deep":
        assert "tree" in out["inspect"]
    if "tree" in out["inspect"]:
        seen = check_nodes(
            out["inspect"]["tree"]["roots"], raw, parent_role_emitted=True
        )
        assert sorted(seen) == sorted(roles_of(raw))

    assert entries(out["wait_drift"]["role_drift"], "id", "role") == [
        ("a", text),
        ("k", parent),
    ]
    assert entries(out["events"]["events"], "node_id", "role") == [
        ("a", text),
        ("r", parent),
    ]

    earlier, later = polled_pair(settling, settle_ports)
    role_changes = [
        item for item in out["settle"]["changes"] if item.get("field") == "role"
    ]
    assert entries(role_changes, "node_id", "old", "new") == [
        ("a", roles_of(earlier)["a"], roles_of(later)["a"])
    ]

    assert (out["scroll_one"]["node_id"], out["scroll_one"]["actual_role"]) == (
        "x",
        text,
    )
    assert out["scroll_two"]["observed_roles"] == sorted({text, parent})
    assert sorted(
        entries(out["scroll_two"]["candidates"], "node_id", "role", "observed_role")
    ) == sorted([("y", text, text), ("z", parent, parent)])
    assert selector_candidates(by_id["scroll_two"])
    for candidates in selector_candidates(by_id["scroll_two"]):
        assert candidates == sorted([("y", text, text), ("z", parent, parent)])

    assert_only_fed_texts(output)
