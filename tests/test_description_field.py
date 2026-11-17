"""The accessible description in node output.

Covers the Linux worker read, the domain conversion, every node serializer,
the stored tree, node identity and diff, and the Android outputs.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aiyes.adapters import atspi_persistent_worker as persistent_worker
from aiyes.adapters import atspi_subprocess_worker as worker
from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml
from aiyes.adapters.file_tree_store import FileTreeStore
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.cli.presenter import (
    format_diff,
    format_do,
    format_find_nodes,
    format_inspect_result,
)
from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.output_formatter import node_to_dict
from aiyes.domain.scenario import ScenarioStep
from aiyes.domain.tree import enrich_tree, flatten_nodes, prune_tree
from aiyes.domain.tree_diff import diff_trees
from aiyes.domain.tree import trees_structurally_equal
from aiyes.domain.use_cases.diff import DiffUseCase
from aiyes.domain.use_cases.inspect import InspectUseCase
from tests.description_support import (
    SESSION_ID,
    Ports,
    forest,
    node,
    node_description,
    ports_for,
    to_tree,
)
from tests.test_aiyes66_android_node_identity import (
    XML_WITH_RESOURCE_ID,
    XML_WITHOUT_RESOURCE_ID,
)
from tests.test_android_adapters import SAMPLE_UIAUTOMATOR_XML

_REPO = Path(__file__).resolve().parents[1]
_BUS = "unix:abstract=/tmp/dbus-description-test"


# ──────────────────────────────────────────────────────────────────────
# Worker read
# ──────────────────────────────────────────────────────────────────────


class _States:
    def get_states(self) -> List[str]:
        return []


class _FakeAccessible:
    """An in-memory AT-SPI accessible whose description read is scripted."""

    def __init__(
        self,
        role: str,
        name: str,
        description: Any,
        children: Optional[List["_FakeAccessible"]] = None,
    ) -> None:
        self.role = role
        self.name = name
        self.description = description
        self.children = children or []
        self.description_reads = 0

    def get_role_name(self) -> str:
        return self.role

    def get_name(self) -> str:
        return self.name

    def get_component(self) -> None:
        return None

    def get_state_set(self) -> _States:
        return _States()

    def get_n_actions(self) -> int:
        return 0

    def get_action_name(self, index: int) -> str:  # pragma: no cover - no actions
        return ""

    def get_child_count(self) -> int:
        return len(self.children)

    def get_child_at_index(self, index: int) -> "_FakeAccessible":
        return self.children[index]

    def get_description(self) -> Any:
        self.description_reads += 1
        if isinstance(self.description, Exception):
            raise self.description
        return self.description


class _FakeDesktop:
    def __init__(self, roots: List[_FakeAccessible]) -> None:
        self.roots = roots

    def get_child_count(self) -> int:
        return len(self.roots)

    def get_child_at_index(self, index: int) -> _FakeAccessible:
        return self.roots[index]


def _fake_gi(desktop: _FakeDesktop) -> Dict[str, types.ModuleType]:
    atspi = types.ModuleType("gi.repository.Atspi")
    atspi.get_desktop = lambda index: desktop  # type: ignore[attr-defined]
    repository = types.ModuleType("gi.repository")
    repository.Atspi = atspi  # type: ignore[attr-defined]
    gi = types.ModuleType("gi")
    gi.require_version = lambda namespace, version: None  # type: ignore[attr-defined]
    gi.repository = repository  # type: ignore[attr-defined]
    return {"gi": gi, "gi.repository": repository, "gi.repository.Atspi": atspi}


def _walk_one_shot(roots: List[_FakeAccessible]) -> Dict[str, Any]:
    """The one-shot worker's tree mode, parsed from what it prints."""
    out = io.StringIO()
    with (
        patch.dict(sys.modules, _fake_gi(_FakeDesktop(roots))),
        patch.object(worker, "_setup_env"),
        contextlib.redirect_stdout(out),
    ):
        worker._do_tree(":99", _BUS)
    return json.loads(out.getvalue())


def _walk_persistent(roots: List[_FakeAccessible]) -> Dict[str, Any]:
    """The persistent worker's get_tree request."""
    with (
        patch.dict(sys.modules, _fake_gi(_FakeDesktop(roots))),
        patch.object(worker, "_setup_env"),
    ):
        return persistent_worker._dispatch("get_tree", {}, ":99", _BUS)


def _accessibles(raising_description: Any) -> List[_FakeAccessible]:
    return [
        _FakeAccessible(
            "frame",
            "Win",
            "",
            [
                _FakeAccessible("combo box", "C", "frigate-class"),
                _FakeAccessible("push button", "Plain", None),
                _FakeAccessible(
                    "panel",
                    "Raising",
                    raising_description,
                    [_FakeAccessible("label", "Inner", "inner-desc")],
                ),
            ],
        )
    ]


def _flat_dicts(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in nodes:
        out.append(item)
        out.extend(_flat_dicts(item.get("children", [])))
    return out


def _all_accessibles(roots: List[_FakeAccessible]) -> List[_FakeAccessible]:
    out: List[_FakeAccessible] = []
    for item in roots:
        out.append(item)
        out.extend(_all_accessibles(item.children))
    return out


class TestWorkerReadsTheDescription:
    def test_values_isolation_single_read_and_both_entry_points(self, capsys) -> None:
        raising = _accessibles(RuntimeError("description read failed"))
        one_shot = _walk_one_shot(raising)
        warnings = [
            json.loads(line) for line in capsys.readouterr().err.splitlines() if line
        ]
        flat = _flat_dicts(one_shot["tree"])

        # A str is kept as it is; None and a failed read give "".
        assert [(d["name"], d.get("description")) for d in flat] == [
            ("Win", ""),
            ("C", "frigate-class"),
            ("Plain", ""),
            ("Raising", ""),
            ("Inner", "inner-desc"),
        ]
        # Exactly one read per visited accessible.
        assert [a.description_reads for a in _all_accessibles(raising)] == [1] * 5
        # The failed read is reported on stderr like the other sub-read warnings.
        assert any(
            w.get("error") == "RuntimeError"
            and w.get("message") == "description read failed"
            for w in warnings
        ), warnings

        # The failing node keeps every other key and its subtree.
        reading = _walk_one_shot(_accessibles("fine"))
        failed = next(d for d in flat if d["name"] == "Raising")
        same = next(d for d in _flat_dicts(reading["tree"]) if d["name"] == "Raising")
        assert {k: v for k, v in failed.items() if k != "description"} == {
            k: v for k, v in same.items() if k != "description"
        }
        assert one_shot["registry"] == reading["registry"]

        # The persistent worker produces the same node dicts.
        assert (
            _walk_persistent(_accessibles(RuntimeError("x")))["tree"]
            == (one_shot["tree"])
        )

    def test_description_reads_equal_the_node_count(self) -> None:
        roots = _accessibles("ok")
        result = _walk_persistent(roots)
        visited = _all_accessibles(roots)
        assert sum(a.description_reads for a in visited) == len(
            _flat_dicts(result["tree"])
        )
        assert all(a.description_reads == 1 for a in visited)


_ROLE = st.sampled_from(["frame", "panel", "push button", "combo box", "filler"])
_NAME = st.sampled_from(["", "A", "B", "A B"])
_DESC = st.one_of(
    st.none(), st.sampled_from(["", "   ", "frigate-class", "Ü"]), st.text(max_size=4)
)


def _shapes() -> st.SearchStrategy[Any]:
    leaf = st.tuples(_ROLE, _NAME, st.just(()))
    return st.recursive(
        leaf,
        lambda inner: st.tuples(_ROLE, _NAME, st.lists(inner, max_size=3).map(tuple)),
        max_leaves=8,
    )


def _build(shape: Any, descriptions: List[Any]) -> _FakeAccessible:
    role, name, children = shape
    description = descriptions.pop(0)
    return _FakeAccessible(
        role, name, description, [_build(c, descriptions) for c in children]
    )


def _count(shape: Any) -> int:
    return 1 + sum(_count(c) for c in shape[2])


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(shapes=st.lists(_shapes(), min_size=1, max_size=2), data=st.data())
def test_descriptions_never_change_ids_or_the_registry(shapes, data) -> None:
    total = sum(_count(s) for s in shapes)
    first = data.draw(st.lists(_DESC, min_size=total, max_size=total))
    second = data.draw(st.lists(_DESC, min_size=total, max_size=total))
    walks = []
    for labels in (first, second):
        pending = list(labels)
        roots = [_build(s, pending) for s in shapes]
        walks.append((labels, _walk_persistent(roots)))
    (labels_a, walk_a), (labels_b, walk_b) = walks
    flat_a, flat_b = _flat_dicts(walk_a["tree"]), _flat_dicts(walk_b["tree"])
    assert [d["id"] for d in flat_a] == [d["id"] for d in flat_b]
    assert walk_a["registry"] == walk_b["registry"]
    for labels, flat in ((labels_a, flat_a), (labels_b, flat_b)):
        assert [d.get("description") for d in flat] == [
            v if isinstance(v, str) else "" for v in labels
        ]


def test_the_accessibility_call_is_made_only_in_the_worker() -> None:
    callers = sorted(
        str(path.relative_to(_REPO))
        for path in (_REPO / "src" / "aiyes").rglob("*.py")
        if "get_description(" in path.read_text(encoding="utf-8")
    )
    assert callers == ["src/aiyes/adapters/atspi_subprocess_worker.py"]


# ──────────────────────────────────────────────────────────────────────
# Domain conversion and the stored tree
# ──────────────────────────────────────────────────────────────────────


def test_raw_conversion_reads_the_description_or_gives_empty() -> None:
    tree = to_tree(
        forest(
            node(
                "frame",
                "W",
                children=[
                    node("combo box", "C", description="frigate-class"),
                    node("push button", "B", description="   "),
                    node("label", "L"),
                ],
            )
        )
    )
    assert [node_description(n) for n in flatten_nodes(tree.roots)] == [
        "",
        "frigate-class",
        "   ",
        "",
    ]


def test_the_stored_tree_keeps_descriptions_and_old_files_load_empty(
    tmp_path: Path,
) -> None:
    store = FileTreeStore(base_dir=str(tmp_path))
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "C", description="frigate-class"),
                node("push button", "B", description="  two  spaces "),
                node("label", "L"),
            ],
        )
    )
    store.save_tree(SESSION_ID, to_tree(raw))
    loaded = store.load_tree(SESSION_ID)
    assert loaded is not None
    assert [node_description(n) for n in flatten_nodes(loaded.tree.roots)] == [
        "",
        "frigate-class",
        "  two  spaces ",
        "",
    ]

    old = {"tree": [{"id": "n_001", "role": "frame", "name": "W", "children": []}]}
    (tmp_path / SESSION_ID / "tree.json").write_text(json.dumps(old))
    reloaded = store.load_tree(SESSION_ID)
    assert reloaded is not None
    assert node_description(reloaded.tree.roots[0]) == ""


# ──────────────────────────────────────────────────────────────────────
# One presence rule on every node-output surface
# ──────────────────────────────────────────────────────────────────────


def _node_dict(value: Any, node_id: str) -> Dict[str, Any]:
    """Find the node dict with this id anywhere inside a JSON value."""
    if isinstance(value, dict):
        if value.get("id") == node_id and "role" in value:
            return value
        items = list(value.values())
    elif isinstance(value, list):
        items = value
    else:
        items = []
    for item in items:
        found = _node_dict(item, node_id)
        if found:
            return found
    return {}


def _target_tree(description: str) -> List[Dict[str, Any]]:
    return forest(
        node(
            "frame",
            "Win",
            children=[
                node(
                    "push button", "Target", description=description, node_id="target"
                ),
                node("label", "Other", description="other text"),
            ],
        )
    )


def _without_target(description: str) -> List[Dict[str, Any]]:
    return forest(node("frame", "Win", children=[node("label", "Other")]))


def _inspect_uc(ports: Ports) -> InspectUseCase:
    return InspectUseCase(
        ports.tree,
        None,
        ports.repo,
        ports.store,
        None,
        ports.clock,  # type: ignore[arg-type]
    )


def _surface_inspect(description: str, no_prune: bool) -> Any:
    ports = ports_for(_target_tree(description))
    result = _inspect_uc(ports).execute(
        session_id=SESSION_ID, no_screenshot=True, no_prune=no_prune
    )
    return json.loads(format_inspect_result(tree=result.tree))


def _surface_find(description: str) -> Any:
    ports = ports_for(_target_tree(description))
    result = ports.find_uc().execute(session_id=SESSION_ID, role="*")
    return json.loads(format_find_nodes(result))


def _surface_do(description: str) -> Any:
    ports = ports_for(_target_tree(description))
    result = ports.do_uc().execute(
        session_id=SESSION_ID, role="push_button", action_name="click", verify=True
    )
    return json.loads(format_do(result))


def _surface_diff(description: str, direction: str) -> Any:
    with_target = to_tree(_target_tree(description))
    without = to_tree(_without_target(description))
    before, after = (
        (without, with_target) if direction == "added" else (with_target, without)
    )
    ports = Ports(snapshots=[after])
    ports.store.saved = before
    result = DiffUseCase(ports.tree, ports.repo, ports.store).execute(SESSION_ID)
    return json.loads(format_diff(result))[direction]


def _executor(ports: Ports) -> ScenarioUseCaseExecutor:
    executor = ScenarioUseCaseExecutor(
        session_start=types.SimpleNamespace(
            execute=lambda **kw: types.SimpleNamespace(session_id=SESSION_ID)
        ),
        inspect=_inspect_uc(ports),
        find=ports.find_uc(),
        action=types.SimpleNamespace(execute=lambda **kw: None),
        type_text=types.SimpleNamespace(execute=lambda **kw: None),
        screenshot=types.SimpleNamespace(execute=lambda **kw: None),
        session_stop=types.SimpleNamespace(execute=lambda **kw: None),
        wait=ports.wait_uc(),
        session_repo=ports.repo,
        clock=ports.clock,
    )
    executor.execute(ScenarioStep(id="start", kind="start_session", parameters={}))
    return executor


def _surface_scenario(description: str, kind: str) -> Any:
    executor = _executor(ports_for(_target_tree(description)))
    parameters = {"role": "*"} if kind == "find" else {"no_screenshot": True}
    result = executor.execute(ScenarioStep(id="s", kind=kind, parameters=parameters))
    assert result.status == "passed", result.error
    return result.output


_SURFACES = {
    "node_to_dict": lambda v: node_to_dict(to_tree(_target_tree(v)).roots[0]),
    "inspect": lambda v: _surface_inspect(v, no_prune=False),
    "inspect --no-prune": lambda v: _surface_inspect(v, no_prune=True),
    "find": _surface_find,
    "do found and verify": _surface_do,
    "diff added": lambda v: _surface_diff(v, "added"),
    "diff removed": lambda v: _surface_diff(v, "removed"),
    "scenario find step": lambda v: _surface_scenario(v, "find"),
    "scenario inspect step": lambda v: _surface_scenario(v, "inspect"),
}


@pytest.mark.parametrize("surface", sorted(_SURFACES))
@pytest.mark.parametrize(
    "description", ["x", "   ", "Frigates - Class Ü  two  spaces", ""]
)
def test_every_node_surface_shows_a_non_empty_description_raw(
    surface: str, description: str
) -> None:
    output = _SURFACES[surface](description)
    if surface == "do found and verify":
        dicts = [output["found"], _node_dict(output["verify"], "target")]
    else:
        dicts = [_node_dict(output, "target")]
    for target in dicts:
        assert target, f"no node dict for the target in {surface} output"
        if description:
            assert target.get("description") == description
        else:
            # Guard (holds at the base): an empty description shows no key.
            assert "description" not in target
    # The other node of the tree keeps its own description.
    if surface not in ("diff added", "diff removed"):
        other = [d for d in _all_dicts(output) if d.get("name") == "Other"]
        assert other and all(d.get("description") == "other text" for d in other)


def _all_dicts(value: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        if "role" in value and "id" in value:
            out.append(value)
        for item in value.values():
            out.extend(_all_dicts(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(_all_dicts(item))
    return out


# ──────────────────────────────────────────────────────────────────────
# Identity, diff and wait-stable
# ──────────────────────────────────────────────────────────────────────


def test_a_change_of_description_alone_changes_no_id_and_no_diff() -> None:
    shape = forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "C", description="frigate-class"),
                node("push button", "B"),
            ],
        )
    )
    relabeled = forest(
        node(
            "frame",
            "W",
            description="now described",
            children=[
                node("combo box", "C", description="another"),
                node("push button", "B", description="new"),
            ],
        )
    )
    before, after = to_tree(shape), to_tree(relabeled)
    nodes_before, nodes_after = flatten_nodes(before.roots), flatten_nodes(after.roots)
    # The two trees really differ in their descriptions.
    assert [node_description(n) for n in nodes_before] == ["", "frigate-class", ""]
    assert [node_description(n) for n in nodes_after] == [
        "now described",
        "another",
        "new",
    ]
    assert [n.id for n in nodes_before] == [n.id for n in nodes_after]
    diff = diff_trees(before, after, NodeIdRegistry())
    assert (diff.added, diff.removed, diff.changed) == ((), (), ())
    assert trees_structurally_equal(before, after)


# ──────────────────────────────────────────────────────────────────────
# Android outputs (guard: these hold at the base and must keep holding)
# ──────────────────────────────────────────────────────────────────────

# sha256 of _android_outputs() at the implementation base.
_ANDROID_OUTPUTS_SHA256 = (
    "ff587953dcde57b1b39924a6facd2b03c3ad1fb7abbc068c431ba1d5de7a4935"
)
_ANDROID_FIXTURES = (
    SAMPLE_UIAUTOMATOR_XML,
    XML_WITH_RESOURCE_ID,
    XML_WITHOUT_RESOURCE_ID,
)


def _android_outputs() -> str:
    parts: List[str] = []
    for xml in _ANDROID_FIXTURES:
        tree, registry = parse_uiautomator_xml(xml)
        nodes = flatten_nodes(tree.roots)
        parts.append(
            json.dumps([[n.id, n.name, n.stable_id, n.resource_id] for n in nodes])
        )
        parts.append(json.dumps(registry.get_mapping(), sort_keys=True))
        parts.append(format_inspect_result(tree=enrich_tree(prune_tree(tree))))
        parts.append(format_inspect_result(tree=tree))
        ports = Ports(snapshots=[tree], backend="android")
        parts.append(
            format_find_nodes(ports.find_uc().execute(session_id=SESSION_ID, role="*"))
        )
        parts.append(
            format_do(
                ports.do_uc().execute(
                    session_id=SESSION_ID, role="*", action_name="click", verify=True
                )
            )
        )
    return "\n".join(parts)


def test_android_nodes_carry_no_description_and_outputs_are_unchanged() -> None:
    for xml in _ANDROID_FIXTURES:
        tree, _ = parse_uiautomator_xml(xml)
        assert all(
            getattr(n, "description", "") == "" for n in flatten_nodes(tree.roots)
        )
    digest = hashlib.sha256(_android_outputs().encode("utf-8")).hexdigest()
    assert digest == _ANDROID_OUTPUTS_SHA256


def test_found_node_and_node_carry_the_field_with_an_empty_default() -> None:
    from aiyes.domain.tree import Node
    from aiyes.domain.use_cases.find import FoundNode

    for cls in (Node, FoundNode):
        fields = [f for f in dataclasses.fields(cls)]
        assert fields[-1].name == "description", cls.__name__
        assert fields[-1].default == ""
