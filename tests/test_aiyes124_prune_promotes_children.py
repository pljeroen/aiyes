"""Tests for AIYES-124 — tree pruning promotes the children of excluded-role nodes.

Requirement authority: R-OUTPUT-04 ("Pruning preserves meaningful parent-child
relationships by promoting children of pruned nodes to the pruned node's
parent"), with the scope rulings that excluded roles are matched on the
normalized role spelling (the ``role_matches`` normalization, so the live AT-SPI
spelling ``redundant object`` is excluded) and that the unnamed single-child
panel rule counts children after they are pruned.

Requirements covered:
  R124-01  excluded-role nodes removed, children promoted in place
  R124-02  nested / mixed pruned roles resolve to the nearest kept ancestor
  R124-03  structural properties P1-P5 of the pruned output (Hypothesis)
  R124-04  prune=False returns the input unchanged
  R124-05  default find reaches widgets under excluded containers
  R124-06  default inspect contains promoted nodes; tree_depth counts pruned depth
  R124-07  promoted ids are stored and accepted by action
  R124-09  ALWAYS_EXCLUDED_ROLES contents unchanged
  R124-10  excluded-role membership follows role_matches normalization
  R124-11  caller-visible removal of live ``redundant object`` nodes
  R124-12  unnamed single-child panel rule counts children after pruning (P6)

Tree notation in comments: ``x > [a, b]`` is node x with children a, b in order.
All doubles are in-memory fakes from tests.conftest; no test touches shared state.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from aiyes.domain.matching import role_matches
from aiyes.domain.session import Session
from aiyes.domain.tree import (
    ALWAYS_EXCLUDED_ROLES,
    AccessibilityTree,
    Node,
    prune_tree,
)
from aiyes.domain.use_cases.action import ActionUseCase
from aiyes.domain.use_cases.find import FindUseCase
from aiyes.domain.use_cases.inspect import InspectUseCase

from tests.conftest import (
    FakeAccessibilityAction,
    FakeAccessibilityTree,
    FakeClock,
    FakeScreenshot,
    FakeScreenshotStore,
    FakeSessionRepository,
    FakeTreeStore,
)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

_SID = "aiyes124-s"

Shape = Tuple[Tuple[str, "Shape"], ...]


def _n(node_id: str, role: str, *children: Node, name: str = "") -> Node:
    """Build a domain Node with fixed non-structural fields."""
    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=(0, 0, 10, 10),
        states=("enabled", "visible"),
        actions=("click",),
        children=tuple(children),
    )


def _s(node_id: str, *children: Tuple[str, Shape]) -> Tuple[str, Shape]:
    """Expected-shape builder: (id, (child shapes...))."""
    return (node_id, tuple(children))


def _shape(nodes: Sequence[Node]) -> Shape:
    """Structural shape of a node list: ids and nesting, in document order."""
    return tuple((n.id, _shape(n.children)) for n in nodes)


def _tree(*roots: Node) -> AccessibilityTree:
    return AccessibilityTree(roots=tuple(roots))


def _walk(
    nodes: Sequence[Node], parent_id: Optional[str] = None
) -> Iterator[Tuple[Node, Optional[str]]]:
    """Pre-order walk yielding (node, parent id). Independent of domain helpers."""
    for node in nodes:
        yield node, parent_id
        yield from _walk(node.children, node.id)


def _all_ids(tree: AccessibilityTree) -> List[str]:
    return [node.id for node, _ in _walk(tree.roots)]


def _all_roles(tree: AccessibilityTree) -> List[str]:
    return [node.role for node, _ in _walk(tree.roots)]


def _is_excluded_role(role: str) -> bool:
    """R124-10 oracle: excluded iff role_matches some ALWAYS_EXCLUDED_ROLES member."""
    return any(role_matches(role, excluded) for excluded in ALWAYS_EXCLUDED_ROLES)


def _session() -> Session:
    return Session(
        session_id=_SID,
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=(),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
    )


def _ports(
    tree: AccessibilityTree,
) -> Tuple[FakeAccessibilityTree, FakeSessionRepository, FakeTreeStore]:
    repo = FakeSessionRepository()
    repo.save(_session())
    return FakeAccessibilityTree(tree=tree), repo, FakeTreeStore()


def _find(
    tree: AccessibilityTree,
) -> Tuple[FindUseCase, FakeSessionRepository, FakeTreeStore]:
    tree_port, repo, store = _ports(tree)
    return FindUseCase(tree=tree_port, session_repo=repo, tree_store=store), repo, store


def _inspect(tree: AccessibilityTree) -> Tuple[InspectUseCase, FakeTreeStore]:
    tree_port, repo, store = _ports(tree)
    uc = InspectUseCase(
        tree=tree_port,
        screenshot=FakeScreenshot(),
        session_repo=repo,
        tree_store=store,
        screenshot_store=FakeScreenshotStore(),
        clock=FakeClock(),
    )
    return uc, store


def _stored_ids(store: FakeTreeStore) -> List[str]:
    stored = store.load_tree(_SID)
    assert stored is not None, "find/inspect did not store a tree"
    return _all_ids(stored.tree)  # type: ignore[arg-type]


# ── Relay-shaped tree (raw AT-SPI role spellings, Qt Widgets layout) ──
#
# application > frame > [menu bar > menu item 'File',
#                        filler > [page tab list > [page tab 'A' > filler >
#                                    [combo box, button 'Run', label 'No file loaded'],
#                                  page tab 'B']],
#                        status bar]
# With wrap_status_bar=True the status bar sits under a live-spelled
# 'redundant object' wrapper (AC-05-2).


def _relay_tree(wrap_status_bar: bool = False) -> AccessibilityTree:
    status: Node = _n("n_status", "status bar")
    if wrap_status_bar:
        status = _n("n_red_wrap", "redundant object", status)
    tab_a = _n(
        "n_tab_a",
        "page tab",
        _n(
            "n_fill_inner",
            "filler",
            _n("n_combo", "combo box"),
            _n("n_run", "button", name="Run"),
            _n("n_label", "label", name="No file loaded"),
        ),
        name="A",
    )
    tab_list = _n(
        "n_tab_list", "page tab list", tab_a, _n("n_tab_b", "page tab", name="B")
    )
    frame = _n(
        "n_frame",
        "frame",
        _n("n_menu_bar", "menu bar", _n("n_file", "menu item", name="File")),
        _n("n_fill_outer", "filler", tab_list),
        status,
        name="Main Window",
    )
    return _tree(_n("n_app", "application", frame, name="nmstoolkit"))


# ── Live 'redundant object' tree (R124-11) ──
# frame(n_root, 'Window') > 'redundant object'(n_red, '') > 'push button'(n_btn, 'Run')


def _redundant_tree() -> AccessibilityTree:
    return _tree(
        _n(
            "n_root",
            "frame",
            _n("n_red", "redundant object", _n("n_btn", "push button", name="Run")),
            name="Window",
        )
    )


# ══════════════════════════════════════════════════════════════════════
# R124-01 / R124-02 / R124-10 / R124-12 — prune_tree examples
# ══════════════════════════════════════════════════════════════════════

_RED_EXAMPLES = [
    # R124-01
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("fl", "filler", _n("cb", "combo_box"), _n("pb", "push_button")),
            )
        ],
        (_s("f", _s("cb"), _s("pb")),),
        id="AC-01-1-filler-children-promoted",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "ro",
                    "redundant_object",
                    _n("cb", "combo_box"),
                    _n("pb", "push_button"),
                ),
            )
        ],
        (_s("f", _s("cb"), _s("pb")),),
        id="AC-01-2-redundant_object-children-promoted",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("a", "label", name="a"),
                _n(
                    "fl",
                    "filler",
                    _n("b", "label", name="b"),
                    _n("c", "label", name="c"),
                ),
                _n("d", "label", name="d"),
            )
        ],
        (_s("f", _s("a"), _s("b"), _s("c"), _s("d")),),
        id="AC-01-3-document-order",
    ),
    pytest.param(
        [_n("f", "frame", _n("fl", "filler", _n("pb", "push_button"), name="Central"))],
        (_s("f", _s("pb")),),
        id="AC-01-4-named-filler-removed",
    ),
    pytest.param(
        [
            _n(
                "fl",
                "filler",
                _n("w1", "frame", name="w1"),
                _n("w2", "frame", name="w2"),
            ),
            _n("w3", "frame", name="w3"),
        ],
        (_s("w1"), _s("w2"), _s("w3")),
        id="AC-01-5-root-level-promotion",
    ),
    # R124-02
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("fl1", "filler", _n("fl2", "filler", _n("pb", "push_button"))),
            )
        ],
        (_s("f", _s("pb")),),
        id="AC-02-1-filler-in-filler",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("sec", "section", _n("fl", "filler", _n("pb", "push_button"))),
            )
        ],
        (_s("f", _s("pb")),),
        id="AC-02-2-filler-in-unnamed-section",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "fl",
                    "filler",
                    _n(
                        "sec",
                        "section",
                        _n("b", "label", name="b"),
                        _n("c", "label", name="c"),
                    ),
                ),
            )
        ],
        (_s("f", _s("b"), _s("c")),),
        id="AC-02-3-unnamed-section-in-filler",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "sec",
                    "section",
                    _n("fl", "filler", _n("pb", "push_button")),
                    name="S",
                ),
            )
        ],
        (_s("f", _s("sec", _s("pb"))),),
        id="AC-02-4-named-section-keeps-promoted-child",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("pan", "panel", _n("fl", "filler", _n("x", "label", name="x"))),
            )
        ],
        (_s("f", _s("x")),),
        id="AC-02-5-and-AC-12-2-unnamed-panel-one-child-after-pruning-collapses",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "pan",
                    "panel",
                    _n(
                        "fl",
                        "filler",
                        _n("a", "label", name="a"),
                        _n("b", "label", name="b"),
                    ),
                    name="P",
                ),
            )
        ],
        (_s("f", _s("pan", _s("a"), _s("b"))),),
        id="AC-02-6-named-panel-keeps-promoted-children",
    ),
    # R124-10
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("ro", "redundant object", _n("btn", "push button", name="Run")),
            )
        ],
        (_s("f", _s("btn")),),
        id="AC-10-1-live-spelling-redundant-object-removed",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("ex", "redundant  object", _n("ch", "label", name="L"), name="X"),
            )
        ],
        (_s("f", _s("ch")),),
        id="AC-10-2-two-internal-spaces",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("ex", " redundant object ", _n("ch", "label", name="L"), name="X"),
            )
        ],
        (_s("f", _s("ch")),),
        id="AC-10-2-edge-spaces",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("ex", "redundant\tobject", _n("ch", "label", name="L"), name="X"),
            )
        ],
        (_s("f", _s("ch")),),
        id="AC-10-2-internal-tab",
    ),
    pytest.param(
        [_n("f", "frame", _n("ex", " filler ", _n("ch", "label", name="L"), name="X"))],
        (_s("f", _s("ch")),),
        id="AC-10-2-filler-edge-spaces",
    ),
    # R124-12
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "pan",
                    "panel",
                    _n(
                        "fl",
                        "filler",
                        _n("a", "label", name="a"),
                        _n("b", "label", name="b"),
                    ),
                ),
            )
        ],
        (_s("f", _s("pan", _s("a"), _s("b"))),),
        id="AC-12-1-unnamed-panel-two-children-after-pruning-kept",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "pan",
                    "panel",
                    _n(
                        "ro",
                        "redundant object",
                        _n("a", "label", name="a"),
                        _n("b", "label", name="b"),
                    ),
                ),
            )
        ],
        (_s("f", _s("pan", _s("a"), _s("b"))),),
        id="AC-12-3-unnamed-panel-newly-appears-redundant-object",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "pan",
                    "panel",
                    _n("fl", "filler", _n("a", "label", name="a")),
                    _n("b", "label", name="b"),
                ),
            )
        ],
        (_s("f", _s("pan", _s("a"), _s("b"))),),
        id="AC-12-7-unnamed-panel-newly-appears-filler-sibling",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n(
                    "pan",
                    "panel",
                    _n("ro", "redundant object"),
                    _n("a", "label", name="a"),
                ),
            )
        ],
        (_s("f", _s("a")),),
        id="AC-12-8-unnamed-panel-newly-disappears",
    ),
]

# Regression guards: behaviour identical at base and after the fix.
_GUARD_EXAMPLES = [
    pytest.param(
        [_n("f", "frame", _n("fl", "filler"), _n("pb", "push_button"))],
        (_s("f", _s("pb")),),
        id="AC-01-6-excluded-leaf-removed",
    ),
    pytest.param(
        [_n("f", "frame", _n("fl1", "filler", _n("fl2", "filler")))],
        (_s("f"),),
        id="AC-01-7-all-excluded-descendants",
    ),
    pytest.param(
        [
            _n(
                "f",
                "frame",
                _n("pan", "panel", _n("fl", "filler"), _n("a", "label", name="a")),
            )
        ],
        (_s("f", _s("a")),),
        id="AC-12-4-unnamed-panel-filler-leaf-plus-one",
    ),
    pytest.param(
        [_n("f", "frame", _n("pan", "panel", _n("fl", "filler")))],
        (_s("f", _s("pan")),),
        id="AC-12-5-unnamed-panel-zero-children-after-pruning-kept",
    ),
]


class TestPruneExamples:
    """Exact prune_tree outputs for the structurally defined cases."""

    @pytest.mark.parametrize("roots, expected", _RED_EXAMPLES)
    def test_excluded_role_children_are_promoted(
        self, roots: List[Node], expected: Shape
    ) -> None:
        pruned = prune_tree(_tree(*roots), prune=True)
        assert _shape(pruned.roots) == expected

    @pytest.mark.parametrize("roots, expected", _GUARD_EXAMPLES)
    def test_unchanged_cases_keep_base_output(
        self, roots: List[Node], expected: Shape
    ) -> None:
        pruned = prune_tree(_tree(*roots), prune=True)
        assert _shape(pruned.roots) == expected

    def test_promoted_node_keeps_raw_role_text(self) -> None:
        """AC-10-6: the promoted 'push button' keeps its raw role text."""
        tree = _tree(
            _n(
                "f",
                "frame",
                _n("ro", "redundant object", _n("btn", "push button", name="Run")),
            )
        )
        pruned = prune_tree(tree, prune=True)
        promoted = pruned.roots[0].children
        assert [n.id for n in promoted] == ["btn"]
        assert promoted[0].role == "push button"


class TestNoPruneAndConstant:
    """R124-04 / R124-09 / AC-10-5 guards."""

    @pytest.mark.parametrize(
        "tree",
        [
            pytest.param(
                _tree(
                    _n(
                        "f",
                        "frame",
                        _n("fl", "filler", _n("a", "label", name="a")),
                        _n("ro", "redundant_object", _n("b", "label", name="b")),
                        _n("sec", "section", _n("c", "label", name="c")),
                        _n("pan", "panel", _n("d", "label", name="d")),
                    )
                ),
                id="AC-04-1-all-pruned-roles-with-children",
            ),
            pytest.param(
                _tree(
                    _n(
                        "f",
                        "frame",
                        _n(
                            "ro",
                            "redundant object",
                            _n("btn", "push button", name="Run"),
                        ),
                    )
                ),
                id="AC-10-5-live-spelling-kept-without-pruning",
            ),
        ],
    )
    def test_prune_false_returns_input_unchanged(self, tree: AccessibilityTree) -> None:
        assert prune_tree(tree, prune=False) == tree

    def test_excluded_roles_constant_contents(self) -> None:
        """AC-09-4: the constant keeps exactly its two members."""
        assert ALWAYS_EXCLUDED_ROLES == frozenset({"filler", "redundant_object"})


# ══════════════════════════════════════════════════════════════════════
# R124-10 — excluded-role membership follows role_matches (AC-10-3)
# ══════════════════════════════════════════════════════════════════════

_ROLE_BASES = (
    "filler",
    "redundant_object",
    "redundant object",
    "Filler",
    "Redundant Object",
    "redundant-object",
    "redundantobject",
    "push button",
    "section",
    "panel",
)
_WS_RUN = st.text(alphabet=" \t", min_size=1, max_size=3)


@st.composite
def _role_spellings(draw: st.DrawFn) -> str:
    """Base role spelling with optional leading/trailing/internal space-tab runs."""
    base = draw(st.sampled_from(_ROLE_BASES))
    words = base.split(" ")
    core = words[0] + "".join(draw(_WS_RUN) + word for word in words[1:])
    if draw(st.booleans()):
        pos = draw(st.integers(min_value=0, max_value=len(core)))
        core = core[:pos] + draw(_WS_RUN) + core[pos:]
    lead = draw(st.one_of(st.just(""), _WS_RUN))
    trail = draw(st.one_of(st.just(""), _WS_RUN))
    return lead + core + trail


class TestExcludedRoleNormalization:
    @given(role=_role_spellings())
    @settings(max_examples=200, deadline=None)
    def test_excluded_iff_role_matches_an_excluded_role(self, role: str) -> None:
        """AC-10-3: frame > node(role=s, 'X') > [leaf] prunes to frame > [leaf]
        iff any(role_matches(s, r) for r in ALWAYS_EXCLUDED_ROLES)."""
        tree = _tree(
            _n("f", "frame", _n("ex", role, _n("leaf", "label", name="L"), name="X"))
        )
        pruned = prune_tree(tree, prune=True)
        if _is_excluded_role(role):
            assert _shape(pruned.roots) == (_s("f", _s("leaf")),)
        else:
            assert _shape(pruned.roots) == (_s("f", _s("ex", _s("leaf"))),)
            assert pruned.roots[0].children[0].role == role

    @pytest.mark.parametrize("role", ["Filler", "Redundant Object", "redundant-object"])
    def test_case_and_punctuation_variants_are_kept(self, role: str) -> None:
        """AC-10-3 pinned consequence: matching is case-sensitive and does not
        treat '-' as a separator, so these spellings are kept."""
        tree = _tree(
            _n("f", "frame", _n("ex", role, _n("leaf", "label", name="L"), name="X"))
        )
        pruned = prune_tree(tree, prune=True)
        assert _shape(pruned.roots) == (_s("f", _s("ex", _s("leaf"))),)


# ══════════════════════════════════════════════════════════════════════
# R124-03 / AC-10-4 / R124-12 — structural properties of the pruned output
# ══════════════════════════════════════════════════════════════════════

_TREE_ROLES = (
    "filler",
    "redundant_object",
    "redundant object",
    " filler ",
    "section",
    "panel",
    "push_button",
    "label",
    "frame",
)
_TREE_NAMES = ("", "X")
_MAX_DEPTH = 5
_NODE_BUDGET = 30


@st.composite
def _forests(draw: st.DrawFn) -> AccessibilityTree:
    """Forests of 1-3 roots, depth up to 5, unique pre-order ids, and every
    non-structural field set to a node-specific value (so P5 can see a change)."""
    counter = [0]

    def build(depth: int) -> Node:
        idx = counter[0]
        counter[0] += 1
        role = draw(st.sampled_from(_TREE_ROLES))
        name = draw(st.sampled_from(_TREE_NAMES))
        if depth < _MAX_DEPTH and counter[0] < _NODE_BUDGET:
            n_children = draw(st.integers(min_value=0, max_value=3))
        else:
            n_children = 0
        children = tuple(build(depth + 1) for _ in range(n_children))
        return Node(
            id=f"n{idx}",
            role=role,
            name=name,
            bounds=(idx, idx + 1, idx + 2, idx + 3),
            states=(f"s{idx}",),
            actions=(f"a{idx}",),
            children=children,
            value=f"v{idx}",
            parent_role=f"pr{idx}",
            parent_name=f"pn{idx}",
            index_in_parent=idx,
            depth=idx,
            sibling_count=idx,
            stable_id=f"st{idx}",
            resource_id=f"r{idx}",
        )

    n_roots = draw(st.integers(min_value=1, max_value=3))
    return AccessibilityTree(roots=tuple(build(0) for _ in range(n_roots)))


# A fixed depth-5 tree with every pruned role nested, used as an explicit example.
_DEEP_EXAMPLE = _tree(
    _n(
        "n0",
        "frame",
        _n(
            "n1",
            "filler",
            _n(
                "n2",
                "section",
                _n(
                    "n3",
                    "redundant object",
                    _n(
                        "n4",
                        "panel",
                        _n("n5", " filler ", _n("n6", "push_button", name="X")),
                        _n("n7", "label", name="X"),
                    ),
                ),
            ),
            _n("n8", "panel", _n("n9", "redundant_object", _n("n10", "label"))),
        ),
        _n("n11", "section", _n("n12", "filler"), name="X"),
    ),
    _n("n13", "filler", _n("n14", "label", name="X")),
)


def _parent_map(tree: AccessibilityTree) -> Dict[str, Optional[str]]:
    return {node.id: parent for node, parent in _walk(tree.roots)}


def _nearest_ancestor_in(
    node_id: str, keep: set, input_parent: Dict[str, Optional[str]]
) -> Optional[str]:
    ancestor = input_parent[node_id]
    while ancestor is not None and ancestor not in keep:
        ancestor = input_parent[ancestor]
    return ancestor


class TestPruneProperties:
    @given(tree=_forests())
    @example(tree=_DEEP_EXAMPLE)
    @settings(max_examples=200, deadline=None)
    def test_p1_to_p5_hold_for_any_tree(self, tree: AccessibilityTree) -> None:
        """AC-03-1 and AC-10-4: P1-P5 on prune_tree output. Excluded roles are
        decided by role_matches (R124-10), not by the code under test."""
        pruned = prune_tree(tree, prune=True)
        in_nodes = [node for node, _ in _walk(tree.roots)]
        out_pairs = list(_walk(pruned.roots))
        out_ids = [node.id for node, _ in out_pairs]
        out_id_set = set(out_ids)
        in_by_id = {node.id: node for node in in_nodes}

        # P1 — no excluded-role node and no unnamed section in the output.
        for node, _ in out_pairs:
            assert not _is_excluded_role(node.role), f"P1: {node.id} role {node.role!r}"
            assert not (node.role == "section" and not node.name), f"P1: {node.id}"

        # P2 — every non-collapsible input node is present.
        for node in in_nodes:
            collapsible = node.role in ("section", "panel") and not node.name
            if not _is_excluded_role(node.role) and not collapsible:
                assert node.id in out_id_set, f"P2: {node.id} ({node.role!r}) lost"

        # P3 — output pre-order ids are a subsequence of input pre-order ids.
        remaining = iter(node.id for node in in_nodes)
        assert all(node_id in remaining for node_id in out_ids), (
            "P3: order/dup/invented"
        )

        # P4 — output parent is the nearest proper input ancestor kept in output.
        input_parent = _parent_map(tree)
        for node, out_parent in out_pairs:
            expected = _nearest_ancestor_in(node.id, out_id_set, input_parent)
            assert out_parent == expected, f"P4: {node.id} under {out_parent}"

        # P5 — every output node equals its input node except for children.
        for node, _ in out_pairs:
            original = in_by_id[node.id]
            assert dataclasses.replace(node, children=()) == dataclasses.replace(
                original, children=()
            ), f"P5: {node.id} fields changed"

    @given(tree=_forests())
    @example(tree=_DEEP_EXAMPLE)
    @settings(max_examples=200, deadline=None)
    def test_p6_unnamed_panel_present_iff_not_exactly_one_child(
        self, tree: AccessibilityTree
    ) -> None:
        """AC-12-6: an unnamed panel U is present iff |S(U)| != 1, where S(U) are
        the output nodes whose nearest proper input ancestor among
        (output ids + {U}) is U."""
        pruned = prune_tree(tree, prune=True)
        out_ids = set(_all_ids(pruned))
        input_parent = _parent_map(tree)
        for node, _ in _walk(tree.roots):
            if node.role != "panel" or node.name:
                continue
            keep = out_ids | {node.id}
            s_u = [
                out_id
                for out_id in out_ids
                if _nearest_ancestor_in(out_id, keep, input_parent) == node.id
            ]
            assert (node.id in out_ids) == (len(s_u) != 1), (
                f"P6: panel {node.id} present={node.id in out_ids} |S(U)|={len(s_u)}"
            )


# ══════════════════════════════════════════════════════════════════════
# R124-05 — default find reaches widgets under excluded containers
# ══════════════════════════════════════════════════════════════════════


class TestDefaultFind:
    @pytest.mark.parametrize(
        "role, name_pattern, expected_ids",
        [
            pytest.param("combo_box", None, ["n_combo"], id="combo_box"),
            pytest.param("page_tab", None, ["n_tab_a", "n_tab_b"], id="page_tab"),
            pytest.param("push_button", None, ["n_run"], id="push_button"),
            pytest.param("label", "No file", ["n_label"], id="label-No-file"),
        ],
    )
    def test_widgets_under_filler_are_found(
        self, role: str, name_pattern: Optional[str], expected_ids: List[str]
    ) -> None:
        """AC-05-1: default find returns the widgets under filler containers."""
        uc, _, _ = _find(_relay_tree())
        result = uc.execute(session_id=_SID, role=role, name_pattern=name_pattern)
        assert [n.id for n in result] == expected_ids

    @pytest.mark.parametrize(
        "role, name_pattern, expected_ids",
        [
            pytest.param("menu_item", "File", ["n_file"], id="menu_item-File"),
            pytest.param("status_bar", None, ["n_status"], id="status_bar"),
        ],
    )
    def test_widgets_outside_filler_still_found(
        self, role: str, name_pattern: Optional[str], expected_ids: List[str]
    ) -> None:
        """AC-05-1 (guard half): nodes not under a filler are found as before."""
        uc, _, _ = _find(_relay_tree())
        result = uc.execute(session_id=_SID, role=role, name_pattern=name_pattern)
        assert [n.id for n in result] == expected_ids

    def test_wildcard_find_returns_no_excluded_role_node(self) -> None:
        """AC-05-2: role='*' with a live 'redundant object' wrapper returns no
        filler / redundant_object / 'redundant object' node; the wrapped widget
        is still returned."""
        uc, _, _ = _find(_relay_tree(wrap_status_bar=True))
        result = uc.execute(session_id=_SID, role="*")
        roles = {n.role for n in result}
        assert roles.isdisjoint({"filler", "redundant_object", "redundant object"}), (
            roles
        )
        assert "n_status" in [n.id for n in result]

    def test_promoted_combo_box_context_describes_pruned_position(self) -> None:
        """AC-05-3: pruned path is application(0) > frame(1) > page tab list(2) >
        page tab 'A'(3) > combo box(4); its --no-prune depth would be 6."""
        uc, _, _ = _find(_relay_tree())
        result = uc.execute(session_id=_SID, role="combo_box")
        assert [n.id for n in result] == ["n_combo"]
        combo = result[0]
        assert (combo.parent_role, combo.parent_name) == ("page tab", "A")
        assert combo.depth == 4
        assert combo.index_in_parent == 0
        assert combo.sibling_count == 3

    def test_scoped_find_reaches_promoted_descendant(self) -> None:
        """AC-05-4: push_button within page_tab 'A' finds button 'Run'."""
        uc, _, _ = _find(_relay_tree())
        result = uc.execute(
            session_id=_SID, role="push_button", within_role="page_tab", within_name="A"
        )
        assert [n.id for n in result] == ["n_run"]
        assert result.scope_matched is True


# ══════════════════════════════════════════════════════════════════════
# R124-06 — default inspect
# ══════════════════════════════════════════════════════════════════════


class TestDefaultInspect:
    def test_inspect_contains_promoted_widgets(self) -> None:
        """AC-06-1: default inspect has the widgets and both tabs, no filler."""
        uc, _ = _inspect(_relay_tree())
        result = uc.execute(session_id=_SID, no_screenshot=True)
        assert result.tree is not None
        ids = set(_all_ids(result.tree))
        assert {"n_combo", "n_run", "n_label", "n_tab_a", "n_tab_b"} <= ids, ids
        assert "filler" not in _all_roles(result.tree)

    def test_tree_depth_counts_depth_after_promotion(self) -> None:
        """AC-06-2: frame > filler > [page_tab_list > [page_tab]] with
        tree_depth=1 yields frame > [page_tab_list] (page_tab_list childless)."""
        tree = _tree(
            _n(
                "f",
                "frame",
                _n(
                    "fl",
                    "filler",
                    _n("ptl", "page_tab_list", _n("pt", "page_tab", name="T")),
                ),
            )
        )
        uc, _ = _inspect(tree)
        result = uc.execute(session_id=_SID, no_screenshot=True, tree_depth=1)
        assert result.tree is not None
        assert _shape(result.tree.roots) == (_s("f", _s("ptl")),)


# ══════════════════════════════════════════════════════════════════════
# R124-07 — stored tree and node_id validity
# ══════════════════════════════════════════════════════════════════════


class TestStoredTreeAndAction:
    def test_find_stores_promoted_nodes_with_original_ids(self) -> None:
        """AC-07-1."""
        uc, _, store = _find(_relay_tree())
        uc.execute(session_id=_SID, role="combo_box")
        assert {"n_combo", "n_run"} <= set(_stored_ids(store))

    def test_inspect_stores_promoted_nodes_with_original_ids(self) -> None:
        """AC-07-2."""
        uc, store = _inspect(_relay_tree())
        uc.execute(session_id=_SID, no_screenshot=True)
        assert {"n_combo", "n_run"} <= set(_stored_ids(store))

    def test_action_accepts_promoted_id_after_default_find(self) -> None:
        """AC-07-3: action on button 'Run' after a default find is accepted and
        reaches the action port with that id."""
        find_uc, repo, store = _find(_relay_tree())
        find_uc.execute(session_id=_SID, role="push_button")
        action_port = FakeAccessibilityAction()
        action_uc = ActionUseCase(
            action=action_port, session_repo=repo, tree_store=store
        )
        try:
            result = action_uc.execute(
                session_id=_SID, node_id="n_run", action_name="click"
            )
        except RuntimeError as exc:
            pytest.fail(f"action rejected a promoted node id: {exc}")
        assert result.status == "ok"
        assert [call[1][1] for call in action_port.calls] == ["n_run"]


# ══════════════════════════════════════════════════════════════════════
# R124-11 — caller-visible removal of live 'redundant object' nodes
# ══════════════════════════════════════════════════════════════════════


class TestLiveRedundantObjectRemoval:
    def test_default_find_for_redundant_object_returns_nothing(self) -> None:
        """AC-11-1."""
        uc, _, _ = _find(_redundant_tree())
        result = uc.execute(session_id=_SID, role="redundant_object")
        assert [n.id for n in result] == []

    def test_no_prune_find_still_returns_redundant_object(self) -> None:
        """AC-11-2 (guard)."""
        uc, _, _ = _find(_redundant_tree())
        result = uc.execute(session_id=_SID, role="redundant_object", no_prune=True)
        assert [n.id for n in result] == ["n_red"]

    def test_child_reports_nearest_kept_ancestor(self) -> None:
        """AC-11-3."""
        uc, _, _ = _find(_redundant_tree())
        result = uc.execute(session_id=_SID, role="push_button", name_pattern="Run")
        assert [n.id for n in result] == ["n_btn"]
        btn = result[0]
        assert (btn.parent_role, btn.parent_name) == ("frame", "Window")
        assert btn.depth == 1
        assert btn.index_in_parent == 0
        assert btn.sibling_count == 1

    def test_default_inspect_drops_redundant_object(self) -> None:
        """AC-11-4."""
        uc, _ = _inspect(_redundant_tree())
        result = uc.execute(session_id=_SID, no_screenshot=True)
        assert result.tree is not None
        assert _shape(result.tree.roots) == (_s("n_root", _s("n_btn")),)
        assert "redundant object" not in _all_roles(result.tree)

    def test_action_rejects_removed_redundant_object_id(self) -> None:
        """AC-11-5: the stored tree no longer holds n_red, so action rejects it."""
        find_uc, repo, store = _find(_redundant_tree())
        find_uc.execute(session_id=_SID, role="*")
        assert set(_stored_ids(store)) == {"n_root", "n_btn"}
        action_uc = ActionUseCase(
            action=FakeAccessibilityAction(), session_repo=repo, tree_store=store
        )
        with pytest.raises(RuntimeError, match="Unknown node_id"):
            action_uc.execute(session_id=_SID, node_id="n_red", action_name="click")
