"""Selecting nodes by their whole accessible description in find, wait and do.

Covers the match rule, the conjunction with the other selectors, the
per-command outcomes, the empty-value rule at the use cases, role drift for
description queries, and the listing of descriptions that contain the given
text when a description query matches nothing.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aiyes.cli.presenter import format_do, format_find_nodes, format_inspect_result
from aiyes.domain.output_formatter import node_to_dict
from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes
from aiyes.domain.use_cases.inspect import InspectUseCase
from tests.description_support import (
    base_find_dicts,
    check_node_dict,
    ABSENT,
    NOT_GIVEN_VALUES,
    SESSION_ID,
    Ports,
    Query,
    cmp,
    description_of,
    drop_description_keys,
    expect_do,
    expect_find,
    expect_wait,
    expected_find_output,
    flat_raw,
    forest,
    given as is_given,
    node,
    ports_for,
    pruned_ids,
    reduce_find_output,
    relabel_descriptions,
    run,
    selected_ids_on,
    strip_descriptions,
    to_tree,
)

_DOMAIN = Path(__file__).resolve().parents[1] / "src" / "aiyes" / "domain"


def _find(raw: Any, description: Any = ABSENT, **kwargs: Any) -> Tuple[Any, Ports]:
    ports = ports_for(raw)
    kwargs.setdefault("role", "*")
    result = run(ports.find_uc().execute, description, session_id=SESSION_ID, **kwargs)
    return result, ports


def _find_json(raw: Any, description: Any = ABSENT, **kwargs: Any) -> Any:
    result, _ = _find(raw, description, **kwargs)
    return json.loads(format_find_nodes(result))


def _wait(
    snapshots: List[Any],
    description: Any = ABSENT,
    mode: str = "present",
    timeout: float = 0.0,
    **kwargs: Any,
) -> Tuple[Any, Ports]:
    ports = ports_for(*snapshots)
    kwargs.setdefault("role", "*")
    result = run(
        ports.wait_uc().execute,
        description,
        session_id=SESSION_ID,
        timeout=timeout,
        absent=mode == "absent",
        transient=mode == "transient",
        **kwargs,
    )
    return result, ports


def _do(
    snapshots: List[Any],
    description: Any = ABSENT,
    timeout: Optional[float] = None,
    action_success: bool = True,
    **kwargs: Any,
) -> Tuple[Any, Ports]:
    ports = ports_for(*snapshots, action_success=action_success)
    kwargs.setdefault("role", "*")
    result = run(
        ports.do_uc().execute,
        description,
        session_id=SESSION_ID,
        action_name="click",
        timeout=timeout,
        **kwargs,
    )
    return result, ports


def _listing_of_wait(result: Any) -> Tuple[Any, Any]:
    texts = getattr(result, "descriptions_containing", None)
    return (
        None if texts is None else list(texts),
        getattr(result, "descriptions_containing_count", None),
    )


def _drift(items: Any) -> List[Tuple[str, str, str]]:
    return [(c.id, c.role, c.name) for c in (items or ())]


# ──────────────────────────────────────────────────────────────────────
# The match rule: the whole description, case and whitespace runs ignored
# ──────────────────────────────────────────────────────────────────────

_RULE_EXAMPLES = [
    ("group-combo", "group-combo", True),
    ("group-combo", "dup-group-combo", False),
    ("Frigates - class", "frigates - CLASS", True),
    ("Frigates - class", "Frigates  -\nclass", True),
    ("Frigates - Class Ü  two  spaces", "frigates - class ü two spaces", True),
    ("a b", "a b", True),
    ("STRASSE", "straße", False),
    ("target", "", False),
    ("target", "   ", False),
]


@pytest.mark.parametrize("query, description, selected", _RULE_EXAMPLES)
def test_the_match_rule_on_find_wait_and_do(
    query: str, description: str, selected: bool
) -> None:
    # The node's name equals the query, so only the description rule decides.
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "target", description=description, node_id="hit")
            ],
        )
    )
    found, _ = _find(raw, query, role="combo_box")
    waited, _ = _wait([raw], query, role="combo_box")
    done, ports = _do([raw], query, role="combo_box")
    assert [n.id for n in found] == (["hit"] if selected else [])
    assert waited.id == ("hit" if selected else None)
    assert (done.found.id if done.found else None) == ("hit" if selected else None)
    actions = [call for call in ports.log if call[0] == "do_action"]
    assert actions == ([("do_action", "hit", "click")] if selected else [])


# Descriptions a real application sets on its drop-downs: no one contains
# another, so each selects exactly its own widget, and a fragment selects none.
_APP_DESCRIPTIONS = [
    "Squadron - pilot ship",
    "Squadron - pilot rank",
    "Ships - class",
    "Settlements - settlement",
    "Settlements - perk to add",
    "Selector - save slot",
    "Selector - save profile",
    "Selector - save context",
    "Recipes - category filter",
    "Multitool - class",
    "Frigates - class",
    "Expedition - season rewards expedition filter",
    "Expedition - offline replay expedition",
    "Edit Slot - item",
    "Edit Slot - inventory type",
    "Discovery - type filter",
    "Corvette - corvette",
    "Corvette - class",
    "Bases & Storage - base",
] + [f"Settlements - production line {n} output" for n in range(1, 13)]


def test_a_full_description_selects_one_widget_and_a_fragment_none() -> None:
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "C", description=text) for text in _APP_DESCRIPTIONS
            ],
        )
    )
    ids = [n["id"] for n in flat_raw(raw)][1:]
    for text, node_id in zip(_APP_DESCRIPTIONS, ids):
        found, _ = _find(raw, text.upper(), role="combo_box")
        assert [n.id for n in found] == [node_id], text
    for fragment in ("class", "filter", "slot"):
        found, _ = _find(raw, fragment, role="combo_box")
        assert list(found) == [], fragment


# ──────────────────────────────────────────────────────────────────────
# Outcomes per command for two nodes that differ only in description
# ──────────────────────────────────────────────────────────────────────


def _pair(include_target: bool = True) -> List[Dict[str, Any]]:
    children = [node("combo box", "Same", description="other", node_id="other")]
    if include_target:
        children.append(node("combo box", "Same", description="target", node_id="tgt"))
    return forest(node("frame", "W", children=children))


def test_outcomes_per_command_for_nodes_differing_only_in_description() -> None:
    found, _ = _find(_pair(), "target", role="combo_box", name_pattern="Same")
    assert [n.id for n in found] == ["tgt"]

    present, _ = _wait([_pair()], "target", role="combo_box")
    assert (present.found, present.timeout, present.id) == (True, False, "tgt")

    still_there, _ = _wait([_pair()], "target", mode="absent", role="combo_box")
    assert (still_there.found, still_there.timeout, still_there.id) == (
        True,
        True,
        None,
    )

    gone, _ = _wait([_pair(False)], "target", mode="absent", role="combo_box")
    assert (gone.found, gone.timeout, gone.id) == (False, False, None)

    seen, _ = _wait(
        [_pair(), _pair(False)],
        "target",
        mode="transient",
        timeout=0.2,
        role="combo_box",
    )
    assert (seen.found, seen.transient, seen.id) == (True, True, "tgt")

    done, ports = _do([_pair()], "target", role="combo_box")
    assert done.found is not None and done.found.id == "tgt"
    assert [c for c in ports.log if c[0] == "do_action"] == [
        ("do_action", "tgt", "click")
    ]


# ──────────────────────────────────────────────────────────────────────
# The description narrows together with every other selector (AND)
# ──────────────────────────────────────────────────────────────────────


def _rid(raw: Dict[str, Any], value: str) -> Dict[str, Any]:
    raw["resource_id"] = value
    return raw


def _three(both: Dict[str, Any], d_only: Dict[str, Any], other_only: Dict[str, Any]):
    both["id"], d_only["id"], other_only["id"] = "both", "d_only", "other_only"
    return forest(node("frame", "W", children=[d_only, other_only, both]))


# (commands, selector kwargs, tree with one node satisfying both, one only the
# description and one only the other selector)
_CONJUNCTS = [
    (
        ("find", "wait", "do"),
        {"role": "push_button"},
        _three(
            node("push button", "x", "t"),
            node("combo box", "x", "t"),
            node("push button", "x", "u"),
        ),
    ),
    (
        ("find", "wait", "do"),
        {"role": "*", "name_pattern": "Alpha"},
        _three(
            node("label", "Alpha", "t"),
            node("label", "Beta", "t"),
            node("label", "Alpha", "u"),
        ),
    ),
    (
        ("find", "wait"),
        {"role": "*", "state": "focused"},
        _three(
            node("label", "x", "t", states=("focused",)),
            node("label", "x", "t", states=("enabled",)),
            node("label", "x", "u", states=("focused",)),
        ),
    ),
    (
        ("find",),
        {"role": "*", "resource_id": "r1"},
        _three(
            _rid(node("label", "x", "t"), "r1"),
            _rid(node("label", "x", "t"), "r2"),
            _rid(node("label", "x", "u"), "r1"),
        ),
    ),
]


def _scope_case() -> Any:
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node(
                    "panel",
                    "Box",
                    children=[
                        node("label", "x", "u", node_id="other_only"),
                        node("label", "x", "t", node_id="both"),
                    ],
                ),
                node(
                    "panel",
                    "Away",
                    children=[node("label", "x", "t", node_id="d_only")],
                ),
            ],
        )
    )
    return (("find",), {"role": "label", "within_name": "Box"}, raw)


@pytest.mark.parametrize("case", list(range(len(_CONJUNCTS) + 1)))
def test_the_description_narrows_together_with_each_other_selector(case: int) -> None:
    commands, kwargs, raw = (_CONJUNCTS + [_scope_case()])[case]
    for command in commands:
        if command == "find":
            result, _ = _find(raw, "T", **kwargs)
            assert [n.id for n in result] == ["both"], kwargs
        elif command == "wait":
            result, _ = _wait([raw], "T", **kwargs)
            assert result.id == "both", kwargs
        else:
            result, _ = _do([raw], "T", **kwargs)
            assert result.found is not None and result.found.id == "both", kwargs


# ──────────────────────────────────────────────────────────────────────
# The empty-value rule at the use cases (Linux and Android)
# ──────────────────────────────────────────────────────────────────────

_EMPTY_TREE = forest(
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


def _outcome(command: str, mode: str, description: Any, backend: str) -> Any:
    snapshots = [to_tree(_EMPTY_TREE), to_tree(forest(node("frame", "W")))]
    ports = Ports(snapshots=snapshots, backend=backend)
    if command == "find":
        result = run(
            ports.find_uc().execute,
            description,
            session_id=SESSION_ID,
            role="push_button",
            name_pattern="Alpha",
            within_name="Named",
        )
        output = (format_find_nodes(result), _drift(result.role_drift))
    elif command == "wait":
        result = run(
            ports.wait_uc().execute,
            description,
            session_id=SESSION_ID,
            role="label",
            name_pattern="Alpha",
            timeout=0.4,
            absent=mode == "absent",
            transient=mode == "transient",
        )
        output = dataclasses.asdict(result)
    else:
        result = run(
            ports.do_uc().execute,
            description,
            session_id=SESSION_ID,
            role="combo_box",
            name_pattern="Alpha",
            action_name="click",
            timeout=0.5,
            verify=True,
        )
        output = format_do(result)
    return output, ports.log


_CALLS = [
    ("find", "-"),
    ("wait", "present"),
    ("wait", "absent"),
    ("wait", "transient"),
    ("do", "-"),
]


@pytest.mark.parametrize("backend", ["linux", "android"])
@pytest.mark.parametrize("command, mode", _CALLS)
@pytest.mark.parametrize("value", NOT_GIVEN_VALUES)
def test_a_selector_that_is_not_given_changes_nothing_at_the_use_case(
    backend: str, command: str, mode: str, value: Optional[str]
) -> None:
    assert _outcome(command, mode, value, backend) == _outcome(
        command, mode, ABSENT, backend
    )


# ──────────────────────────────────────────────────────────────────────
# The selection oracle (property)
# ──────────────────────────────────────────────────────────────────────

_ALL_ROLES = [
    "combo box",
    "push button",
    "label",
    "panel",
    "filler",
    "section",
    "redundant object",
    "page tab",
]
_UNPRUNED_ROLES = ["combo box", "push button", "label", "page tab"]
_NAMES = ["", "  ", "Alpha", "alpha  beta", "Beta", "Dup"]
_DESCRIPTION_VALUES = [
    "",
    "   ",
    "\t ",
    "frigate-class",
    "Frigate  CLASS",
    "frigate",
    "group-combo",
    "dup-group-combo",
    "Ü ber",
    "über",
    "straße",
    "a b",
    "a b",
    "x",
]
_TEXT = "aAbß Üü-\t "
_DESCRIPTIONS = st.one_of(
    st.none(),
    st.sampled_from(_DESCRIPTION_VALUES),
    st.text(alphabet=_TEXT, max_size=5),
)
_STATES = st.sampled_from([("enabled",), ("enabled", "focused"), ()])
_QUERIES = st.one_of(
    st.sampled_from([ABSENT, *NOT_GIVEN_VALUES]),
    st.sampled_from(
        [
            "frigate-class",
            "FRIGATE class",
            "frigate",
            "class",
            "a b",
            "über",
            "STRASSE",
            "x",
            "group-combo",
        ]
    ),
    st.text(alphabet=_TEXT, min_size=1, max_size=4),
)


def _forests(roles: List[str]) -> st.SearchStrategy[List[Dict[str, Any]]]:
    def make(role, name, description, states, children=()):
        return node(role, name, description, list(children), states)

    leaf = st.builds(
        make, st.sampled_from(roles), st.sampled_from(_NAMES), _DESCRIPTIONS, _STATES
    )
    tree = st.recursive(
        leaf,
        lambda kids: st.builds(
            make,
            st.sampled_from(roles),
            st.sampled_from(_NAMES),
            _DESCRIPTIONS,
            _STATES,
            st.lists(kids, max_size=3),
        ),
        max_leaves=7,
    )
    return st.lists(tree, min_size=1, max_size=2).map(lambda roots: forest(*roots))


def _from_the_tree(snapshots: List[Any]) -> st.SearchStrategy[str]:
    """Queries built from descriptions in the snapshots: the whole text in
    another case or spacing, or a part of it (so matches, drift and the
    listing occur often)."""
    texts = sorted(
        {
            description_of(raw)
            for snapshot in snapshots
            for raw in flat_raw(snapshot)
            if cmp(description_of(raw))
        }
    )

    def variants(text: str) -> st.SearchStrategy[str]:
        return st.one_of(
            st.just(text),
            st.just(text.upper()),
            st.just("  " + text.replace(" ", "  ") + " "),
            st.integers(0, len(text) - 1).flatmap(
                lambda i: st.integers(i + 1, len(text)).map(lambda j: text[i:j])
            ),
            st.integers(1, len(text)).map(lambda j: text[:j]),
        )

    return st.sampled_from(texts).flatmap(variants) if texts else _QUERIES


def _near_misses(snapshots: List[Any]) -> st.SearchStrategy[Any]:
    """A strict part of a description in the snapshots (a query the listing
    answers when no node's whole description equals it)."""
    texts = sorted(
        {
            description_of(raw)
            for snapshot in snapshots
            for raw in flat_raw(snapshot)
            if len(cmp(description_of(raw))) > 1
        }
    )
    if not texts:
        return _QUERIES
    return st.sampled_from(texts).flatmap(
        lambda text: st.integers(1, len(text) - 1).map(lambda j: text[:j])
    )


def _query(data: Any, snapshots: Optional[List[Any]] = None) -> Query:
    if snapshots and data.draw(st.integers(0, 3)) == 3:
        # Every other selector open, the query a part of a description.
        return Query(description=data.draw(_near_misses(snapshots)))
    description = data.draw(st.one_of(_QUERIES, _from_the_tree(snapshots or [])))
    return Query(
        role=data.draw(
            st.sampled_from(
                ["*", "*", "combo_box", "push_button", "filler", "label", "panel"]
            )
        ),
        name_pattern=data.draw(
            st.sampled_from([None, None, None, "", "alpha", "Beta", "Dup"])
        ),
        state=data.draw(st.sampled_from([None, None, None, "focused"])),
        description=description,
        within_role=data.draw(st.sampled_from([None] * 4 + ["filler", "panel"])),
        within_name=data.draw(
            st.sampled_from([None] * 6 + ["", "Dup", "alpha", "Beta"])
        ),
        no_prune=data.draw(st.booleans()),
    )


def _find_checks(snapshot: Any, q: Query) -> Tuple[List[str], set, Optional[str]]:
    expected = expect_find(snapshot, q)
    result, ports = _find(
        snapshot,
        q.description,
        role=q.role,
        name_pattern=q.name_pattern,
        state=q.state,
        no_prune=q.no_prune,
        within_role=q.within_role,
        within_name=q.within_name,
    )
    output = json.loads(format_find_nodes(result))
    assert reduce_find_output(output) == expected_find_output(expected)
    assert ports.log == [("get_tree",), ("save_tree",)]
    by_id = {raw["id"]: raw for raw in flat_raw(snapshot)}
    base = base_find_dicts(snapshot, q.no_prune)
    for emitted in output if isinstance(output, list) else output["nodes"]:
        check_node_dict(emitted, base[emitted["id"]], by_id[emitted["id"]], q.d)
    if expected.listing:
        for text in expected.listing[0]:
            again, _ = _find(
                snapshot,
                text,
                role=q.role,
                name_pattern=q.name_pattern,
                state=q.state,
                no_prune=q.no_prune,
                within_role=q.within_role,
                within_name=q.within_name,
            )
            assert list(again), f"listed {text!r} selects nothing"
    first = expected.selected[0] if expected.selected else None
    return expected.pool, set(expected.selected), first


def _expected_listing(listing: Any) -> Tuple[Any, Any]:
    return (list(listing[0]), listing[1]) if listing else (None, None)


def _wait_checks(snapshots: List[Any], q: Query, mode: str, polls: int):
    timeout = (0.2 if mode == "transient" else 0.5) * (polls - 1)
    expected = expect_wait(snapshots, q, mode, timeout)
    result, ports = _wait(
        snapshots,
        q.description,
        mode=mode,
        timeout=timeout,
        role=q.role,
        name_pattern=q.name_pattern,
        state=q.state,
    )
    assert (result.found, result.timeout, result.id, result.transient) == (
        expected.found,
        expected.timeout,
        expected.id,
        expected.transient,
    )
    assert _drift(result.role_drift) == expected.drift
    assert _listing_of_wait(result) == _expected_listing(expected.listing)
    assert ports.log == expected.port_calls
    deciding = snapshots[min(expected.deciding, len(snapshots) - 1)]
    if expected.listing:
        for text in expected.listing[0]:
            again, _ = _wait(
                [deciding],
                text,
                role=q.role,
                name_pattern=q.name_pattern,
                state=q.state,
            )
            assert again.found and again.id, f"listed {text!r} selects nothing"
    return expected, deciding


def _do_checks(snapshots: List[Any], q: Query, timeout: Optional[float], verify: bool):
    expected = expect_do(snapshots, q, timeout, verify=verify)
    result, ports = _do(
        snapshots,
        q.description,
        timeout=timeout,
        role=q.role,
        name_pattern=q.name_pattern,
        verify=verify,
    )
    output = json.loads(format_do(result))
    keys = ["found", "action"] + (["verify"] if verify else [])
    if expected.acted_on is None:
        keys = ["error"]
    if expected.listing:
        keys += ["descriptions_containing", "descriptions_containing_count"]
        assert output["descriptions_containing"] == list(expected.listing[0])
        assert output["descriptions_containing_count"] == expected.listing[1]
    assert list(output) == keys
    assert output.get("error") == expected.error
    assert ports.log == expected.port_calls
    deciding = snapshots[min(expected.deciding, len(snapshots) - 1)]
    by_id = {raw["id"]: raw for raw in flat_raw(deciding)}
    if expected.acted_on is not None:
        stripped = {
            n.id: n for n in flatten_nodes(to_tree(strip_descriptions(deciding)).roots)
        }
        base = list(node_to_dict(stripped[expected.acted_on]).items())
        check_node_dict(output["found"], base, by_id[expected.acted_on], q.d)
    if expected.listing:
        for text in expected.listing[0]:
            again, _ = _do([deciding], text, role=q.role, name_pattern=q.name_pattern)
            assert again.found is not None, f"listed {text!r} selects nothing"
    return expected, deciding


@settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(unpruned_only=st.booleans(), data=st.data())
def test_find_wait_and_do_agree_with_the_selection_oracle(unpruned_only, data) -> None:
    roles = _UNPRUNED_ROLES if unpruned_only else _ALL_ROLES
    count = data.draw(st.sampled_from([1, 1, 2, 3]))
    snapshots = data.draw(st.lists(_forests(roles), min_size=count, max_size=count))
    q = _query(data, snapshots)
    mode = data.draw(st.sampled_from(["present", "absent", "transient"]))
    polls = data.draw(st.integers(min_value=1, max_value=3))
    do_timeout = data.draw(st.sampled_from([None, 0.0, 0.5, 1.0]))
    verify = data.draw(st.booleans())

    find_pool, find_selected, find_first = _find_checks(snapshots[0], q)
    waited, wait_snapshot = _wait_checks(snapshots, q, mode, polls)
    done, do_snapshot = _do_checks(snapshots, q, do_timeout, verify)

    # Cross-command equality: two commands with the same ordered pool whose
    # selectors accept the same ids from it report the same first node.
    reported = {"find": (find_pool, find_selected, find_first)}
    do_pool = [raw["id"] for raw in flat_raw(do_snapshot)]
    reported["do"] = (
        do_pool,
        set(selected_ids_on(flat_raw(do_snapshot), q, "do")),
        done.acted_on,
    )
    if mode == "present":
        wait_pool = [raw["id"] for raw in flat_raw(wait_snapshot)]
        reported["wait"] = (
            wait_pool,
            set(selected_ids_on(flat_raw(wait_snapshot), q, "wait")),
            waited.id,
        )
    names = sorted(reported)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            pool_a, sel_a, first_a = reported[a]
            pool_b, sel_b, first_b = reported[b]
            if pool_a == pool_b and sel_a == sel_b:
                assert first_a == first_b, (a, b)


# ──────────────────────────────────────────────────────────────────────
# Calls whose selector is not given never read descriptions (property)
# ──────────────────────────────────────────────────────────────────────


def _q0_outputs(raw: Any, q: Query, command: str) -> Any:
    if command == "find":
        result, _ = _find(
            raw,
            q.description,
            role=q.role,
            name_pattern=q.name_pattern,
            state=q.state,
            no_prune=q.no_prune,
            within_role=q.within_role,
            within_name=q.within_name,
        )
        out = json.loads(format_find_nodes(result))
        return drop_description_keys(out), [n.id for n in result]
    if command == "wait":
        result, _ = _wait(
            [raw], q.description, role=q.role, name_pattern=q.name_pattern
        )
        return dataclasses.asdict(result)
    if command == "do":
        result, _ = _do([raw], q.description, role=q.role, name_pattern=q.name_pattern)
        return drop_description_keys(json.loads(format_do(result)))
    ports = ports_for(raw)
    inspected = InspectUseCase(
        ports.tree,
        None,
        ports.repo,
        ports.store,
        None,
        ports.clock,  # type: ignore[arg-type]
    ).execute(session_id=SESSION_ID, no_screenshot=True, no_prune=q.no_prune)
    return drop_description_keys(json.loads(format_inspect_result(tree=inspected.tree)))


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    raw=_forests(_ALL_ROLES),
    labels=st.lists(_DESCRIPTIONS, min_size=1, max_size=6),
    data=st.data(),
)
def test_without_a_given_selector_only_description_keys_depend_on_descriptions(
    raw, labels, data
) -> None:
    q = dataclasses.replace(
        _query(data),
        description=data.draw(st.sampled_from([ABSENT, *NOT_GIVEN_VALUES])),
    )
    relabeled = relabel_descriptions(raw, labels)
    assert pruned_ids(raw, False) == pruned_ids(relabeled, False)
    for command in ("find", "wait", "do", "inspect"):
        assert _q0_outputs(raw, q, command) == _q0_outputs(relabeled, q, command), (
            command
        )


# ──────────────────────────────────────────────────────────────────────
# Role drift for description queries
# ──────────────────────────────────────────────────────────────────────


def test_role_drift_for_description_queries() -> None:
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "C", "frigate-class", node_id="combo"),
                node("push button", "P", "other", node_id="button"),
                node("combo box", "Alpha", "frigate-class", node_id="alpha"),
                node("label", "Alpha", "unrelated", node_id="label"),
            ],
        )
    )
    # A description-only query of the wrong role points at the described node.
    found, _ = _find(raw, "frigate-class", role="push_button")
    assert list(found) == []
    assert _drift(found.role_drift) == [
        ("combo", "combo box", "C"),
        ("alpha", "combo box", "Alpha"),
    ]
    # With a name pattern too, every candidate satisfies both.
    found, _ = _find(raw, "frigate-class", role="push_button", name_pattern="Alpha")
    assert _drift(found.role_drift) == [("alpha", "combo box", "Alpha")]

    # wait: the never-matched timeout and the never-seen transient timeout.
    for mode in ("present", "transient"):
        waited, _ = _wait([raw], "frigate-class", mode=mode, role="push_button")
        assert (waited.found, waited.timeout) == (False, True)
        assert _drift(waited.role_drift) == [
            ("combo", "combo box", "C"),
            ("alpha", "combo box", "Alpha"),
        ]
    # The other wait outcomes carry no drift.
    for mode, description in (
        ("present", "other"),
        ("absent", "frigate-class"),
        ("absent", "other"),
        ("transient", "other"),
    ):
        waited, _ = _wait([raw], description, mode=mode, role="push_button")
        assert waited.role_drift is None, (mode, description)


# ──────────────────────────────────────────────────────────────────────
# The listing of descriptions that contain the given text
# ──────────────────────────────────────────────────────────────────────

_PROBE_DESCRIPTIONS = [
    "frigate-class",
    "panel-combo",
    "unnamed-combo",
    "tab-combo",
    "tab2-combo",
    "group-combo",
    "dup-group-combo",
    "dup-panel-combo",
]


def test_listing_examples_on_find() -> None:
    raw = forest(
        node(
            "frame",
            "W",
            children=[node("combo box", "C", text) for text in _PROBE_DESCRIPTIONS],
        )
    )
    assert _find_json(raw, "combo", role="combo_box") == {
        "nodes": [],
        "descriptions_containing": [
            "panel-combo",
            "unnamed-combo",
            "tab-combo",
            "tab2-combo",
            "group-combo",
        ],
        "descriptions_containing_count": 7,
    }
    assert _find_json(raw, "frigate", role="combo_box") == {
        "nodes": [],
        "descriptions_containing": ["frigate-class"],
        "descriptions_containing_count": 1,
    }
    selected = _find_json(raw, "group-combo", role="combo_box")
    assert isinstance(selected, list)
    assert [n["description"] for n in selected] == ["group-combo"]


def test_listing_respects_the_other_selectors_scope_and_duplicates() -> None:
    raw = forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "A", "frigate-a"),
                node("combo box", "B", "frigate-b", states=("enabled", "focused")),
                node("push button", "A", "frigate-help"),
                node("panel", "Box", children=[node("combo box", "In", "frigate-in")]),
                node(
                    "panel", "Away", children=[node("combo box", "Out", "frigate-out")]
                ),
                node("combo box", "D", "frigate-dup"),
                node("combo box", "D", "frigate-dup"),
            ],
        )
    )

    def listing(output: Any) -> Any:
        return (
            output.get("descriptions_containing"),
            output.get("descriptions_containing_count"),
        )

    every = _find_json(raw, "frigate", role="combo_box")
    assert listing(every) == (
        ["frigate-a", "frigate-b", "frigate-in", "frigate-out", "frigate-dup"],
        5,
    )
    assert listing(_find_json(raw, "frigate", role="combo_box", name_pattern="A")) == (
        ["frigate-a"],
        1,
    )
    assert listing(_find_json(raw, "frigate", role="combo_box", state="focused")) == (
        ["frigate-b"],
        1,
    )
    scoped = _find_json(raw, "frigate", role="combo_box", within_name="Box")
    assert list(scoped) == [
        "nodes",
        "scope_matched",
        "matched_ancestors",
        "descriptions_containing",
        "descriptions_containing_count",
    ]
    assert listing(scoped) == (["frigate-in"], 1)
    assert _find_json(raw, "frigate", role="combo_box", within_name="Nowhere") == {
        "nodes": [],
        "scope_matched": False,
        "matched_ancestors": [],
    }


def _near(*extra: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A tree with a combo whose description contains 'frigate' but is longer."""
    return forest(
        node(
            "frame",
            "W",
            children=[
                node("combo box", "Near", "frigate-class", node_id="near"),
                *extra,
            ],
        )
    )


def _exact() -> Dict[str, Any]:
    return node("combo box", "Exact", "frigate", node_id="exact")


_FIRST_NEAR = forest(
    node(
        "frame",
        "W",
        children=[node("combo box", "Near", "frigate-first", node_id="near")],
    )
)
_LISTED = (["frigate-class"], 1)
_NONE = (None, None)


def _invalid_id_tree() -> AccessibilityTree:
    tree = to_tree(_near(_exact()))

    def blank(n: Node) -> Node:
        children = tuple(blank(c) for c in n.children)
        new_id = " " if n.id == "exact" else n.id
        return dataclasses.replace(n, id=new_id, children=children)

    return AccessibilityTree(roots=tuple(blank(r) for r in tree.roots))


def _scoped_near() -> List[Dict[str, Any]]:
    return forest(
        node(
            "frame",
            "W",
            children=[
                node(
                    "panel",
                    "Box",
                    children=[
                        node("combo box", "Near", "frigate-class", node_id="near")
                    ],
                )
            ],
        )
    )


def _find_outcome(raw: Any, **kwargs: Any) -> Tuple[Any, int]:
    output = _find_json(raw, "frigate", role="combo_box", **kwargs)
    if isinstance(output, list):
        return _NONE, 1
    return (
        (
            output.get("descriptions_containing"),
            output.get("descriptions_containing_count"),
        ),
        1,
    )


def _wait_outcome(snapshots: List[Any], mode: str, timeout: float) -> Tuple[Any, int]:
    result, ports = _wait(
        snapshots, "frigate", mode=mode, timeout=timeout, role="combo_box"
    )
    return _listing_of_wait(result), ports.log.count(("get_tree",))


def _do_outcome(
    snapshots: List[Any], timeout: Optional[float] = None, success: bool = True
) -> Tuple[Any, int]:
    if snapshots and isinstance(snapshots[0], AccessibilityTree):
        ports = Ports(snapshots=snapshots)
        result = run(
            ports.do_uc().execute,
            "frigate",
            session_id=SESSION_ID,
            role="combo_box",
            action_name="click",
            timeout=timeout,
        )
    else:
        result, ports = _do(
            snapshots,
            "frigate",
            timeout=timeout,
            action_success=success,
            role="combo_box",
        )
    output = json.loads(format_do(result))
    return (
        (
            output.get("descriptions_containing"),
            output.get("descriptions_containing_count"),
        ),
        ports.log.count(("get_tree",)),
    )


# One example per outcome: (outcome, listing expected, tree reads at the base).
_OUTCOMES = {
    "find, nothing selected": (lambda: _find_outcome(_near()), _LISTED, 1),
    "find, nothing selected in the scope": (
        lambda: _find_outcome(_scoped_near(), within_name="Box"),
        _LISTED,
        1,
    ),
    "find returns a node": (lambda: _find_outcome(_near(_exact())), _NONE, 1),
    "find, scope not matched": (
        lambda: _find_outcome(_near(), within_name="Missing"),
        _NONE,
        1,
    ),
    "wait succeeds": (
        lambda: _wait_outcome([_near(_exact())], "present", 0.0),
        _NONE,
        1,
    ),
    "wait times out": (
        lambda: _wait_outcome([_FIRST_NEAR, _near()], "present", 0.5),
        _LISTED,
        2,
    ),
    "wait --absent succeeds": (
        lambda: _wait_outcome([_near()], "absent", 0.0),
        _NONE,
        1,
    ),
    "wait --absent times out": (
        lambda: _wait_outcome([_near(_exact())], "absent", 0.0),
        _NONE,
        1,
    ),
    "wait --transient, present at the end": (
        lambda: _wait_outcome([_near(_exact())], "transient", 0.0),
        _NONE,
        1,
    ),
    "wait --transient, seen earlier": (
        lambda: _wait_outcome([_near(_exact()), _near()], "transient", 0.2),
        _NONE,
        2,
    ),
    "wait --transient, never seen": (
        lambda: _wait_outcome([_FIRST_NEAR, _near()], "transient", 0.2),
        _LISTED,
        2,
    ),
    "do, action succeeds": (lambda: _do_outcome([_near(_exact())]), _NONE, 1),
    "do, action fails": (
        lambda: _do_outcome([_near(_exact())], success=False),
        _NONE,
        1,
    ),
    "do, invalid id": (lambda: _do_outcome([_invalid_id_tree()]), _NONE, 1),
    "do, nothing selected": (lambda: _do_outcome([_near()]), _LISTED, 1),
    "do, nothing selected before the timeout": (
        lambda: _do_outcome([_FIRST_NEAR, _near()], timeout=0.5),
        _LISTED,
        2,
    ),
}


@pytest.mark.parametrize("outcome", list(_OUTCOMES))
def test_the_listing_appears_on_exactly_the_no_match_outcomes(outcome: str) -> None:
    call, listing, reads = _OUTCOMES[outcome]
    actual_listing, actual_reads = call()
    assert actual_listing == listing
    assert actual_reads == reads


# ──────────────────────────────────────────────────────────────────────
# One shared predicate
# ──────────────────────────────────────────────────────────────────────

_RAW_TEXT = "Shared  Predicate Probe"
_QUERY_TEXT = "shared predicate probe"


def _predicate_calls(call: Any) -> set:
    """Domain functions called with the query and, separately, a description."""
    hits: set = set()

    def is_query(value: Any) -> bool:
        return isinstance(value, str) and cmp(value) == _QUERY_TEXT

    def is_description(value: Any) -> bool:
        if isinstance(value, str):
            return value in (_RAW_TEXT, _QUERY_TEXT)
        return getattr(value, "description", None) == _RAW_TEXT

    def profiler(frame: Any, event: str, arg: Any) -> None:
        if event != "call":
            return
        code = frame.f_code
        path = Path(code.co_filename).resolve()
        if _DOMAIN not in path.parents:
            return
        names = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
        values = [frame.f_locals.get(name) for name in names]
        queries = [i for i, v in enumerate(values) if is_query(v)]
        descriptions = [i for i, v in enumerate(values) if is_description(v)]
        if any(q != d for q in queries for d in descriptions):
            hits.add((str(path), code.co_firstlineno, code.co_name))

    sys.setprofile(profiler)
    try:
        call()
    finally:
        sys.setprofile(None)
    return hits


def test_find_wait_and_do_share_one_description_predicate() -> None:
    raw = forest(
        node("frame", "W", children=[node("combo box", "C", _RAW_TEXT, node_id="hit")])
    )
    find_hits = _predicate_calls(lambda: _find(raw, _QUERY_TEXT, role="combo_box"))
    wait_hits = _predicate_calls(lambda: _wait([raw], _QUERY_TEXT, role="combo_box"))
    do_hits = _predicate_calls(lambda: _do([raw], _QUERY_TEXT, role="combo_box"))
    assert find_hits & wait_hits & do_hits, (find_hits, wait_hits, do_hits)
