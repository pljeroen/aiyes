"""Named containers that default pruning hides are --within-name scopes.

On a default (pruned) find, a scope ancestor can be a node of the pruned tree
or a named filler of the unpruned tree; candidates stay nodes of the pruned
tree. With pruning off, scope resolution is the implementation base's.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from aiyes.cli.presenter import format_find_nodes
from aiyes.domain.tree import flatten_nodes
from aiyes.domain.matching import role_matches
from tests.description_support import (
    ABSENT,
    SESSION_ID,
    Query,
    base_find_dicts,
    check_node_dict,
    expect_find,
    expected_find_output,
    flat_raw,
    forest,
    is_named_filler,
    node,
    ports_for,
    pruned_ids,
    reduce_find_output,
    run,
)


def _qt_shaped() -> List[Dict[str, Any]]:
    """A tree shaped like the measured Qt windows."""

    def combo(name: str) -> Dict[str, Any]:
        return node("combo box", name, node_id=name)

    return forest(
        node(
            "frame",
            "Win",
            node_id="frame",
            children=[
                node(
                    "filler",
                    "",
                    node_id="root_filler",
                    children=[
                        node(
                            "filler",
                            "PanelScope",
                            node_id="f_panel",
                            children=[combo("P1")],
                        ),
                        node(
                            "filler",
                            "TabsScope",
                            node_id="f_tabs",
                            children=[
                                node(
                                    "layered pane",
                                    "",
                                    node_id="pane",
                                    children=[
                                        node(
                                            "filler",
                                            "TabPageScope",
                                            node_id="f_page",
                                            children=[combo("T1")],
                                        ),
                                        node(
                                            "filler",
                                            "",
                                            node_id="f_unnamed",
                                            children=[combo("S1")],
                                        ),
                                    ],
                                ),
                                node(
                                    "page tab list",
                                    "",
                                    node_id="tabs",
                                    children=[
                                        node("page tab", "First Tab", node_id="tab")
                                    ],
                                ),
                            ],
                        ),
                        node(
                            "panel",
                            "GroupScope",
                            node_id="p_group",
                            children=[combo("G1")],
                        ),
                        node("panel", "Dup", node_id="p_dup", children=[combo("D1")]),
                        node("filler", "Dup", node_id="f_dup", children=[combo("D2")]),
                        node(
                            "filler",
                            "OuterPanel",
                            node_id="f_outer",
                            children=[
                                node(
                                    "filler",
                                    "InnerPanel",
                                    node_id="f_inner",
                                    children=[combo("N1")],
                                )
                            ],
                        ),
                        node(
                            "filler",
                            "ScrollScope",
                            node_id="f_scroll",
                            children=[
                                node(
                                    "filler",
                                    "",
                                    node_id="f_s1",
                                    children=[
                                        node(
                                            "filler",
                                            "",
                                            node_id="f_s2",
                                            children=[combo("R1")],
                                        )
                                    ],
                                )
                            ],
                        ),
                    ],
                )
            ],
        )
    )


def _scoped(
    raw: Any,
    within_name: Optional[str] = None,
    within_role: Optional[str] = None,
    no_prune: bool = False,
    role: str = "combo_box",
    name_pattern: Optional[str] = None,
    description: Any = ABSENT,
):
    ports = ports_for(raw)
    result = run(
        ports.find_uc().execute,
        description,
        session_id=SESSION_ID,
        role=role,
        name_pattern=name_pattern,
        within_name=within_name,
        within_role=within_role,
        no_prune=no_prune,
    )
    return json.loads(format_find_nodes(result)), ports


_QT_QUERIES = [
    ({"within_name": "PanelScope"}, ["P1"], [("f_panel", "filler", "PanelScope")]),
    ({"within_name": "TabPageScope"}, ["T1"], [("f_page", "filler", "TabPageScope")]),
    ({"within_name": "TabsScope"}, ["T1", "S1"], [("f_tabs", "filler", "TabsScope")]),
    (
        {"within_role": "filler", "within_name": "PanelScope"},
        ["P1"],
        [("f_panel", "filler", "PanelScope")],
    ),
    ({"within_name": "OuterPanel"}, ["N1"], [("f_outer", "filler", "OuterPanel")]),
    ({"within_name": "InnerPanel"}, ["N1"], [("f_inner", "filler", "InnerPanel")]),
    ({"within_name": "ScrollScope"}, ["R1"], [("f_scroll", "filler", "ScrollScope")]),
    # Guard (base behaviour): a named group box stays a scope.
    ({"within_name": "GroupScope"}, ["G1"], [("p_group", "panel", "GroupScope")]),
    (
        {"within_name": "Dup"},
        ["D1", "D2"],
        [("p_dup", "panel", "Dup"), ("f_dup", "filler", "Dup")],
    ),
    # Guard (base behaviour): a Qt tab title is its own node, not the page.
    ({"within_name": "First Tab"}, [], [("tab", "page tab", "First Tab")]),
]


@pytest.mark.parametrize("kwargs, nodes, ancestors", _QT_QUERIES)
def test_named_qt_containers_are_scopes_on_a_default_find(
    kwargs: Dict[str, str], nodes: List[str], ancestors: List[Any]
) -> None:
    output, ports = _scoped(_qt_shaped(), **kwargs)
    assert output == {
        "nodes": output["nodes"],
        "scope_matched": True,
        "matched_ancestors": [
            {"id": i, "role": role, "name": name} for i, role, name in ancestors
        ],
    }
    assert [n["id"] for n in output["nodes"]] == nodes
    # Default output and the stored tree still never contain a filler.
    stored = [n.id for n in flatten_nodes(ports.store.saved.roots)]
    assert stored == pruned_ids(_qt_shaped(), no_prune=False)
    assert not any(role_matches(n["role"], "filler") for n in output["nodes"])


def _fillers_and_redundant() -> List[Dict[str, Any]]:
    return forest(
        node(
            "frame",
            "W",
            node_id="frame",
            children=[
                node(
                    "filler",
                    "",
                    node_id="unnamed",
                    children=[node("combo box", "A", node_id="A")],
                ),
                node(
                    "filler",
                    "  ",
                    node_id="blank",
                    children=[node("combo box", "B", node_id="B")],
                ),
                node(
                    "redundant object",
                    "Named",
                    node_id="redundant",
                    children=[node("combo box", "C", node_id="C")],
                ),
                node(
                    "filler",
                    "Named",
                    node_id="named",
                    children=[node("combo box", "D", node_id="D")],
                ),
            ],
        )
    )


@pytest.mark.parametrize(
    "kwargs, no_prune, ancestors, nodes",
    [
        ({"within_role": "filler"}, False, ["named"], ["D"]),
        ({"within_name": "Named"}, False, ["named"], ["D"]),
        # Guards (hold at the base): with pruning off, the base's ancestors.
        (
            {"within_role": "filler"},
            True,
            ["unnamed", "blank", "named"],
            ["A", "B", "D"],
        ),
        ({"within_name": "Named"}, True, ["redundant", "named"], ["C", "D"]),
    ],
)
def test_only_named_fillers_widen_the_scope_and_only_with_pruning_on(
    kwargs: Dict[str, str], no_prune: bool, ancestors: List[str], nodes: List[str]
) -> None:
    output, _ = _scoped(_fillers_and_redundant(), no_prune=no_prune, **kwargs)
    assert output["scope_matched"] is True
    assert [a["id"] for a in output["matched_ancestors"]] == ancestors
    assert [n["id"] for n in output["nodes"]] == nodes


def test_role_drift_follows_the_widened_pool() -> None:
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node(
                    "filler",
                    "PanelScope",
                    node_id="panel",
                    children=[node("entry", "Wanted", node_id="entry")],
                ),
                node("entry", "Wanted", node_id="outside"),
            ],
        )
    )
    output, _ = _scoped(raw, within_name="PanelScope", name_pattern="Wanted")
    assert output["nodes"] == []
    assert output["scope_matched"] is True
    assert output["role_drift"] == [{"id": "entry", "role": "entry", "name": "Wanted"}]


# ──────────────────────────────────────────────────────────────────────
# Property: the exact ancestor set, pool and status; the bounded widening
# ──────────────────────────────────────────────────────────────────────

_ROLES = [
    "filler",
    "panel",
    "section",
    "redundant object",
    "combo box",
    "page tab",
    "label",
]
_NAMES = ["", "  ", "Dup", "Panel", "Scope Panel", "x"]


def _trees() -> st.SearchStrategy[List[Dict[str, Any]]]:
    def make(role, name, children=()):
        return node(role, name, children=list(children))

    leaf = st.builds(make, st.sampled_from(_ROLES), st.sampled_from(_NAMES))
    tree = st.recursive(
        leaf,
        lambda kids: st.builds(
            make,
            st.sampled_from(_ROLES),
            st.sampled_from(_NAMES),
            st.lists(kids, max_size=3),
        ),
        max_leaves=9,
    )
    return st.lists(tree, min_size=1, max_size=2).map(lambda roots: forest(*roots))


def _is_subsequence(short: List[Any], long: List[Any]) -> bool:
    it = iter(long)
    return all(item in it for item in short)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    raw=_trees(),
    role=st.sampled_from(["*", "combo_box", "label", "panel"]),
    within_role=st.sampled_from([None, None, "filler", "panel"]),
    within_name=st.sampled_from(["Dup", "Panel", "Scope", "", None]),
    no_prune=st.booleans(),
    description=st.sampled_from([ABSENT, None, "", "   "]),
)
@example(
    raw=forest(
        node(
            "frame",
            "W",
            children=[node("filler", "Panel", children=[node("combo box", "C")])],
        )
    ),
    role="combo_box",
    within_role=None,
    within_name="Panel",
    no_prune=False,
    description=ABSENT,
)
def test_scoped_find_agrees_with_the_scope_oracle(
    raw, role, within_role, within_name, no_prune, description
) -> None:
    q = Query(
        role=role,
        description=description,
        within_role=within_role,
        within_name=within_name,
        no_prune=no_prune,
    )
    if not q.scope_requested:
        q = Query(
            role=role, description=description, within_name="Dup", no_prune=no_prune
        )
    expected = expect_find(raw, q)
    output, ports = _scoped(
        raw,
        within_name=q.within_name,
        within_role=q.within_role,
        no_prune=no_prune,
        role=role,
        description=description,
    )
    assert reduce_find_output(output) == expected_find_output(expected)

    kept = pruned_ids(raw, no_prune)
    stored = [n.id for n in flatten_nodes(ports.store.saved.roots)]
    assert stored == kept
    by_id = {r["id"]: r for r in flat_raw(raw)}
    base = base_find_dicts(raw, no_prune)
    for emitted in output["nodes"]:
        assert emitted["id"] in kept
        check_node_dict(emitted, base[emitted["id"]], by_id[emitted["id"]], q.d)

    ancestor_ids = [a[0] for a in expected.ancestors]
    base_ids = [a[0] for a in expected.base_ancestors]
    if no_prune:
        # Pruning off: the implementation base's scope resolution.
        assert expected.ancestors == expected.base_ancestors
        assert expected.selected == expected.base_selected
        return
    widening = [
        r["id"] for r in flat_raw(raw) if is_named_filler(r) and r["id"] in ancestor_ids
    ]
    assert _is_subsequence(base_ids, ancestor_ids)
    assert [i for i in ancestor_ids if i not in base_ids] == widening
    if not widening:
        # No named filler matches: byte-identical to the base.
        assert ancestor_ids == base_ids
        assert expected.selected == expected.base_selected
        return
    assert _is_subsequence(expected.base_selected, expected.selected)
    above = {}

    def walk(r: Dict[str, Any], chain: List[str]) -> None:
        above[r["id"]] = chain
        for child in r["children"]:
            walk(child, chain + [r["id"]])

    for root in raw:
        walk(root, [])
    added = [i for i in expected.selected if i not in expected.base_selected]
    assert added == [i for i in expected.selected if not set(above[i]) & set(base_ids)]
