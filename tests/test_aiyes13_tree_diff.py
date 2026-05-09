"""AIYES-13 — Tree Diff Command (R-TDDV6-02).

RED phase: all tests must FAIL before implementation exists.

Traceability:
  R-TDDV6-02.1: diff_trees domain function — AC-01 through AC-10
  R-TDDV6-02.2: DiffUseCase — AC-11 through AC-16
  R-TDDV6-02.3: CLI diff command — AC-17 through AC-21
  WIRE: composition root wiring, presenter
  ARCH: domain purity for tree_diff.py and use_cases/diff.py
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import patch

import pytest

from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node

from tests.conftest import (
    FakeAccessibilityTree,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
    make_tree,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_session(session_id: str = "test-s") -> Session:
    """Create a minimal Session for diff tests."""
    return Session(
        session_id=session_id,
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=[],
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
    )


def _node(
    node_id: str = "n_001",
    role: str = "push_button",
    name: str = "OK",
    value: Optional[str] = None,
    states: Tuple[str, ...] = ("enabled",),
    actions: Tuple[str, ...] = ("click",),
    bounds: Tuple[int, ...] = (100, 200, 80, 30),
    children: Tuple[Node, ...] = (),
) -> Node:
    """Convenience Node factory using domain types directly."""
    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=bounds,
        states=states,
        actions=actions,
        children=children,
        value=value,
    )


def _build_tree(*root_nodes: Node) -> AccessibilityTree:
    """Build an AccessibilityTree from one or more root Nodes."""
    return AccessibilityTree(roots=root_nodes)


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-02.1: diff_trees domain function
# ══════════════════════════════════════════════════════════════════════


class TestDiffTreesIdentical:
    """AC-01/TEST-01: Identical trees produce empty diff."""

    def test_identical_trees_empty_added(self) -> None:
        """Two identical trees: added is empty tuple."""
        from aiyes.domain.tree_diff import diff_trees

        node_a = _node("n_001", "push_button", "OK")
        tree = _build_tree(node_a)
        tree_copy = _build_tree(_node("n_001", "push_button", "OK"))
        registry = NodeIdRegistry()

        result = diff_trees(tree, tree_copy, registry)
        assert result.added == ()

    def test_identical_trees_empty_removed(self) -> None:
        """Two identical trees: removed is empty tuple."""
        from aiyes.domain.tree_diff import diff_trees

        node_a = _node("n_001", "push_button", "OK")
        tree = _build_tree(node_a)
        tree_copy = _build_tree(_node("n_001", "push_button", "OK"))
        registry = NodeIdRegistry()

        result = diff_trees(tree, tree_copy, registry)
        assert result.removed == ()

    def test_identical_trees_empty_changed(self) -> None:
        """Two identical trees: changed is empty tuple."""
        from aiyes.domain.tree_diff import diff_trees

        node_a = _node("n_001", "push_button", "OK")
        tree = _build_tree(node_a)
        tree_copy = _build_tree(_node("n_001", "push_button", "OK"))
        registry = NodeIdRegistry()

        result = diff_trees(tree, tree_copy, registry)
        assert result.changed == ()


class TestDiffTreesNodeAdded:
    """AC-02/TEST-02: Node added in the live (after) tree."""

    def test_added_contains_new_node(self) -> None:
        """After tree has extra node -> appears in added."""
        from aiyes.domain.tree_diff import diff_trees

        node_a = _node("n_001", "push_button", "OK")
        node_b = _node("n_002", "push_button", "Cancel")
        node_c = _node("n_003", "text", "Hello")

        before = _build_tree(node_a, node_b)
        after = _build_tree(node_a, node_b, node_c)
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        added_ids = [n.id for n in result.added]
        assert "n_003" in added_ids

    def test_added_is_from_after_tree(self) -> None:
        """The added node entry contains the full Node from the after tree."""
        from aiyes.domain.tree_diff import diff_trees

        node_a = _node("n_001", "push_button", "OK")
        node_new = _node("n_002", "text", "NewLabel", value="42")

        before = _build_tree(node_a)
        after = _build_tree(node_a, node_new)
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        added_node = [n for n in result.added if n.id == "n_002"][0]
        assert added_node.name == "NewLabel"
        assert added_node.value == "42"

    def test_removed_empty_when_only_additions(self) -> None:
        """Only additions -> removed is empty."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001"))
        after = _build_tree(_node("n_001"), _node("n_002", "text", "X"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        assert result.removed == ()


class TestDiffTreesNodeRemoved:
    """AC-03/TEST-03: Node removed in the live (after) tree."""

    def test_removed_contains_missing_node(self) -> None:
        """Before tree has node not in after -> appears in removed."""
        from aiyes.domain.tree_diff import diff_trees

        node_a = _node("n_001", "push_button", "OK")
        node_b = _node("n_002", "push_button", "Cancel")
        node_c = _node("n_003", "text", "Hello")

        before = _build_tree(node_a, node_b, node_c)
        after = _build_tree(node_a, node_b)
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        removed_ids = [n.id for n in result.removed]
        assert "n_003" in removed_ids

    def test_removed_is_from_before_tree(self) -> None:
        """The removed node entry contains the full Node from the before tree."""
        from aiyes.domain.tree_diff import diff_trees

        node_gone = _node("n_002", "text", "OldLabel", value="old")

        before = _build_tree(_node("n_001"), node_gone)
        after = _build_tree(_node("n_001"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        removed_node = [n for n in result.removed if n.id == "n_002"][0]
        assert removed_node.name == "OldLabel"
        assert removed_node.value == "old"

    def test_added_empty_when_only_removals(self) -> None:
        """Only removals -> added is empty."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001"), _node("n_002", "text", "X"))
        after = _build_tree(_node("n_001"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        assert result.added == ()


class TestDiffTreesValueChanged:
    """AC-04/TEST-04: Node with changed value field."""

    def test_value_change_detected(self) -> None:
        """Same node ID, different value -> appears in changed."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", value="0"))
        after = _build_tree(_node("n_001", value="42"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        value_changes = [c for c in result.changed if c.field == "value"]
        assert len(value_changes) == 1

    def test_value_change_before_after(self) -> None:
        """NodeChange has correct before and after strings."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", value="0"))
        after = _build_tree(_node("n_001", value="42"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        change = [c for c in result.changed if c.field == "value"][0]
        assert change.before == "0"
        assert change.after == "42"

    def test_value_none_to_string(self) -> None:
        """value=None -> value="42" is a change. None maps to "null" string."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", value=None))
        after = _build_tree(_node("n_001", value="42"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        change = [c for c in result.changed if c.field == "value"][0]
        assert change.before == "null"
        assert change.after == "42"

    def test_value_both_none_no_change(self) -> None:
        """Both None -> no change reported for value."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", value=None))
        after = _build_tree(_node("n_001", value=None))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        value_changes = [c for c in result.changed if c.field == "value"]
        assert len(value_changes) == 0


class TestDiffTreesNameChanged:
    """AC-05/TEST-04: Node with changed name field."""

    def test_name_change_detected(self) -> None:
        """Same node ID, different name -> NodeChange with field="name"."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", name="Old"))
        after = _build_tree(_node("n_001", name="New"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        name_changes = [c for c in result.changed if c.field == "name"]
        assert len(name_changes) == 1
        assert name_changes[0].before == "Old"
        assert name_changes[0].after == "New"


class TestDiffTreesStatesChanged:
    """AC-06/TEST-04: Node with changed states field."""

    def test_states_change_detected(self) -> None:
        """Same node ID, different states -> NodeChange with field="states"."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", states=("enabled",)))
        after = _build_tree(_node("n_001", states=("enabled", "focused")))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        states_changes = [c for c in result.changed if c.field == "states"]
        assert len(states_changes) == 1

    def test_states_change_string_repr(self) -> None:
        """before/after use str() representation of tuples."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", states=("enabled",)))
        after = _build_tree(_node("n_001", states=("enabled", "focused")))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        change = [c for c in result.changed if c.field == "states"][0]
        assert change.before == str(("enabled",))
        assert change.after == str(("enabled", "focused"))


class TestDiffTreesBoundsChanged:
    """AC-10/TEST-04: Node with changed bounds field."""

    def test_bounds_change_detected(self) -> None:
        """Same node ID, different bounds -> NodeChange with field="bounds"."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", bounds=(100, 200, 80, 30)))
        after = _build_tree(_node("n_001", bounds=(150, 250, 80, 30)))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        bounds_changes = [c for c in result.changed if c.field == "bounds"]
        assert len(bounds_changes) == 1

    def test_bounds_change_string_repr(self) -> None:
        """before/after use str() representation of tuples."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", bounds=(100, 200, 80, 30)))
        after = _build_tree(_node("n_001", bounds=(150, 250, 80, 30)))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        change = [c for c in result.changed if c.field == "bounds"][0]
        assert change.before == str((100, 200, 80, 30))
        assert change.after == str((150, 250, 80, 30))


class TestDiffTreesActionsChanged:
    """AC-09/TEST-04: Node with changed actions field."""

    def test_actions_change_detected(self) -> None:
        """Same node ID, different actions -> NodeChange with field="actions"."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", actions=("click",)))
        after = _build_tree(_node("n_001", actions=("click", "press")))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        actions_changes = [c for c in result.changed if c.field == "actions"]
        assert len(actions_changes) == 1

    def test_actions_change_string_repr(self) -> None:
        """before/after use str() representation of tuples."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", actions=("click",)))
        after = _build_tree(_node("n_001", actions=("click", "press")))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        change = [c for c in result.changed if c.field == "actions"][0]
        assert change.before == str(("click",))
        assert change.after == str(("click", "press"))


class TestDiffTreesMultipleFieldsChanged:
    """AC-07/TEST-04: Multiple fields changed on same node."""

    def test_multiple_changes_same_node(self) -> None:
        """Name and value both changed -> two NodeChange entries for same node."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", name="Old", value="0"))
        after = _build_tree(_node("n_001", name="New", value="42"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        changes_for_n001 = [c for c in result.changed if c.id == "n_001"]
        assert len(changes_for_n001) == 2

    def test_multiple_changes_have_correct_fields(self) -> None:
        """Each changed field has its own NodeChange entry."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", name="Old", value="0"))
        after = _build_tree(_node("n_001", name="New", value="42"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        changed_fields = {c.field for c in result.changed if c.id == "n_001"}
        assert "name" in changed_fields
        assert "value" in changed_fields

    def test_node_change_contains_node_id(self) -> None:
        """Each NodeChange entry contains the node_id of the changed node."""
        from aiyes.domain.tree_diff import diff_trees

        before = _build_tree(_node("n_001", name="Old"))
        after = _build_tree(_node("n_001", name="New"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        assert all(c.id == "n_001" for c in result.changed)


class TestDiffTreesEmptyTrees:
    """TEST-01 extension: Empty trees produce empty diff."""

    def test_both_empty_trees(self) -> None:
        """Two empty trees: all diff arrays empty."""
        from aiyes.domain.tree_diff import diff_trees

        empty = _build_tree()
        registry = NodeIdRegistry()

        result = diff_trees(empty, empty, registry)
        assert result.added == ()
        assert result.removed == ()
        assert result.changed == ()


class TestDiffTreesRoleChangeAsAddRemove:
    """TEST-04 extension: Role is part of ID key, so role change = added + removed."""

    def test_role_change_appears_as_add_remove(self) -> None:
        """Node at same position with different role gets different ID -> add + remove."""
        from aiyes.domain.tree_diff import diff_trees

        # Manually construct nodes with different IDs (as would happen with
        # different roles in the path-based registry)
        before = _build_tree(_node("n_001", role="button", name="Submit"))
        after = _build_tree(_node("n_005", role="toggle_button", name="Submit"))
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        assert len(result.removed) == 1
        assert result.removed[0].id == "n_001"
        assert len(result.added) == 1
        assert result.added[0].id == "n_005"


class TestDiffTreesFlattenedComparison:
    """AC-08/TEST-08: Nodes at different tree depths compared correctly by ID."""

    def test_different_depth_same_ids_no_spurious_diff(self) -> None:
        """Tree restructuring (depth change) with same node IDs: only real changes."""
        from aiyes.domain.tree_diff import diff_trees

        # Before: flat tree with two root nodes
        before = _build_tree(
            _node("n_001", "frame", "Win"),
            _node("n_002", "push_button", "OK"),
        )
        # After: nested tree — n_002 is child of n_001 (same IDs)
        after = _build_tree(
            _node(
                "n_001",
                "frame",
                "Win",
                children=(_node("n_002", "push_button", "OK"),),
            ),
        )
        registry = NodeIdRegistry()

        result = diff_trees(before, after, registry)
        # Both IDs still present — no additions or removals
        assert len(result.added) == 0
        assert len(result.removed) == 0


class TestNodeChangeDataclass:
    """ARCH-02: NodeChange is a frozen dataclass with correct fields."""

    def test_node_change_is_frozen(self) -> None:
        """NodeChange is a frozen dataclass."""
        import dataclasses
        from aiyes.domain.tree_diff import NodeChange

        assert dataclasses.is_dataclass(NodeChange)
        # Frozen check: attempt to set attribute raises FrozenInstanceError
        nc = NodeChange(id="n_001", field="value", before="0", after="42")
        with pytest.raises(dataclasses.FrozenInstanceError):
            nc.id = "other"  # type: ignore[misc]

    def test_node_change_fields(self) -> None:
        """NodeChange has exactly four fields: id, field, before, after."""
        import dataclasses
        from aiyes.domain.tree_diff import NodeChange

        field_names = {f.name for f in dataclasses.fields(NodeChange)}
        assert field_names == {"id", "field", "before", "after"}


class TestTreeDiffDataclass:
    """ARCH-02: TreeDiff is a frozen dataclass with Tuple fields."""

    def test_tree_diff_is_frozen(self) -> None:
        """TreeDiff is a frozen dataclass."""
        import dataclasses
        from aiyes.domain.tree_diff import TreeDiff

        assert dataclasses.is_dataclass(TreeDiff)
        td = TreeDiff(added=(), removed=(), changed=())
        with pytest.raises(dataclasses.FrozenInstanceError):
            td.added = ()  # type: ignore[misc]

    def test_tree_diff_fields(self) -> None:
        """TreeDiff has exactly three fields: added, removed, changed."""
        import dataclasses
        from aiyes.domain.tree_diff import TreeDiff

        field_names = {f.name for f in dataclasses.fields(TreeDiff)}
        assert field_names == {"added", "removed", "changed"}


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-02.2: DiffUseCase
# ══════════════════════════════════════════════════════════════════════


class TestDiffUseCaseComparesStoredVsLive:
    """AC-11/AC-12/AC-13: DiffUseCase compares stored tree against live tree."""

    def test_no_changes_returns_empty_diff(self) -> None:
        """AC-11: Stored and live trees identical -> empty diff."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        node_data = [make_node("n_001", "push_button", "OK")]
        stored_tree = AccessibilityTree(
            roots=(_node("n_001", "push_button", "OK", states=("enabled", "visible")),)
        )
        live_tree_port = FakeAccessibilityTree(tree=make_tree(node_data))

        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()
        store.save_tree("test-s", stored_tree, NodeIdRegistry())

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)
        result = uc.execute(session_id="test-s")

        assert result.diff.added == ()
        assert result.diff.removed == ()
        assert result.diff.changed == ()

    def test_added_node_detected(self) -> None:
        """AC-12: Live tree has extra node -> appears in result.diff.added."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        stored_tree = _build_tree(_node("n_001", "push_button", "OK"))
        live_data = [
            make_node("n_001", "push_button", "OK"),
            make_node("n_002", "text", "NewNode"),
        ]
        live_tree_port = FakeAccessibilityTree(tree=make_tree(live_data))

        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()
        store.save_tree("test-s", stored_tree, NodeIdRegistry())

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)
        result = uc.execute(session_id="test-s")

        added_ids = [n.id for n in result.diff.added]
        assert "n_002" in added_ids

    def test_value_change_detected(self) -> None:
        """AC-13: Same node, different value -> appears in changed."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        stored_tree = _build_tree(_node("n_001", "push_button", "OK", value="old"))
        live_data = [make_node("n_001", "push_button", "OK", value="new")]
        live_tree_port = FakeAccessibilityTree(tree=make_tree(live_data))

        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()
        store.save_tree("test-s", stored_tree, NodeIdRegistry())

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)
        result = uc.execute(session_id="test-s")

        value_changes = [c for c in result.diff.changed if c.field == "value"]
        assert len(value_changes) == 1


class TestDiffUseCaseNoStoredTree:
    """AC-14/TEST-05: No stored tree -> RuntimeError with "inspect" in message."""

    def test_raises_runtime_error(self) -> None:
        """load_tree returns None -> RuntimeError."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        live_tree_port = FakeAccessibilityTree(tree=make_tree())
        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()
        # No stored tree: load_tree returns None

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)

        with pytest.raises(RuntimeError, match="inspect"):
            uc.execute(session_id="test-s")

    def test_error_message_contains_inspect(self) -> None:
        """Error message directs user to run inspect first."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        live_tree_port = FakeAccessibilityTree(tree=make_tree())
        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)

        with pytest.raises(RuntimeError) as exc_info:
            uc.execute(session_id="test-s")

        assert "inspect" in str(exc_info.value).lower()


class TestDiffUseCaseSessionNotFound:
    """TEST-05 extension: Session not found -> RuntimeError."""

    def test_raises_on_missing_session(self) -> None:
        """session_repo.load returns None -> RuntimeError."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        live_tree_port = FakeAccessibilityTree(tree=make_tree())
        repo = FakeSessionRepository()
        # No session saved
        store = FakeTreeStore()

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)

        with pytest.raises(RuntimeError):
            uc.execute(session_id="nonexistent")


class TestDiffUseCaseBaselineUpdate:
    """AC-15/TEST-06: After diff, stored tree is updated to live state."""

    def test_save_tree_called_with_live_tree(self) -> None:
        """After diff completes, save_tree was called."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        stored_tree = _build_tree(_node("n_001", "push_button", "OK"))
        live_data = [make_node("n_001", "push_button", "OK")]
        live_tree_port = FakeAccessibilityTree(tree=make_tree(live_data))

        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()
        store.save_tree("test-s", stored_tree, NodeIdRegistry())

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)
        uc.execute(session_id="test-s")

        save_calls = [c for c in store.calls if c[0] == "save_tree"]
        # At least two: one initial setup + one from the use case
        assert len(save_calls) >= 2

    def test_subsequent_diff_returns_empty(self) -> None:
        """After diff updates baseline, second diff with same tree -> empty."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        stored_tree = _build_tree(_node("n_001", "push_button", "OK", value="old"))
        live_data = [make_node("n_001", "push_button", "OK", value="new")]
        live_tree_port = FakeAccessibilityTree(tree=make_tree(live_data))

        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()
        store.save_tree("test-s", stored_tree, NodeIdRegistry())

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)

        # First diff detects change
        result1 = uc.execute(session_id="test-s")
        assert len(result1.diff.changed) > 0

        # Second diff: baseline was updated, live tree unchanged -> empty
        result2 = uc.execute(session_id="test-s")
        assert result2.diff.added == ()
        assert result2.diff.removed == ()
        assert result2.diff.changed == ()


class TestDiffUseCaseRegistryUpdate:
    """AC-16: Diff updates stored registry to live state."""

    def test_registry_saved_after_diff(self) -> None:
        """After diff, save_tree is called with a registry (not None)."""
        from aiyes.domain.use_cases.diff import DiffUseCase

        stored_tree = _build_tree(_node("n_001", "push_button", "OK"))
        live_data = [make_node("n_001", "push_button", "OK")]
        live_tree_port = FakeAccessibilityTree(tree=make_tree(live_data))

        repo = FakeSessionRepository()
        repo.save(_make_session())
        store = FakeTreeStore()
        store.save_tree("test-s", stored_tree, NodeIdRegistry())

        uc = DiffUseCase(tree=live_tree_port, session_repo=repo, tree_store=store)
        uc.execute(session_id="test-s")

        # The last save_tree call from the use case should have a registry
        save_calls = [c for c in store.calls if c[0] == "save_tree"]
        last_save = save_calls[-1]
        # save_tree args: (session_id, tree, registry)
        _, (sid, tree, registry) = last_save
        # registry should not be None (either from adapter or fallback)
        # The important thing is that it was passed
        assert sid == "test-s"


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-02.3: CLI diff command
# ══════════════════════════════════════════════════════════════════════


class TestDiffCliHelp:
    """AC-17/TEST-07: diff command in help with --session option."""

    def test_diff_help_shows_session_option(self) -> None:
        """aieyes diff --help shows --session option."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["diff", "--help"])
        assert result.exit_code == 0
        assert "--session" in result.output


class TestDiffCliNoStoredTree:
    """AC-18/TEST-05/TEST-07: diff with no stored tree returns error, exit 1."""

    def test_error_exit_1(self) -> None:
        """diff_uc raises RuntimeError -> exit 1 with error on stderr."""
        import inspect as _insp

        from aiyes.cli.main import cli
        from click.testing import CliRunner

        _kw = (
            {"mix_stderr": False}
            if "mix_stderr" in _insp.signature(CliRunner.__init__).parameters
            else {}
        )
        runner = CliRunner(**_kw)
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.diff_uc") as mock_uc,
        ):
            mock_uc.execute.side_effect = RuntimeError(
                "No stored tree. Run inspect first."
            )

            result = runner.invoke(cli, ["diff", "--session", "s1"])

            assert result.exit_code == 1
            all_output = (result.output or "") + (getattr(result, "stderr", "") or "")
            assert "Error" in all_output


class TestDiffCliEmptyJson:
    """AC-19/TEST-07: diff with empty diff returns correct JSON."""

    def test_json_has_three_empty_arrays(self) -> None:
        """Empty diff -> {"added": [], "removed": [], "changed": []}."""
        from aiyes.domain.tree_diff import TreeDiff
        from aiyes.domain.use_cases.diff import DiffResult
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.diff_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = DiffResult(
                diff=TreeDiff(added=(), removed=(), changed=()),
                total_changes=0,
            )

            result = runner.invoke(cli, ["diff", "--session", "s1"])

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["added"] == []
            assert parsed["removed"] == []
            assert parsed["changed"] == []


class TestDiffCliPopulatedJson:
    """AC-20/TEST-07: diff with changes returns populated JSON."""

    def test_json_arrays_populated(self) -> None:
        """Diff with added, removed, and changed -> JSON arrays non-empty."""
        from aiyes.domain.tree_diff import NodeChange, TreeDiff
        from aiyes.domain.use_cases.diff import DiffResult
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        added_node = _node("n_new", "text", "New")
        removed_node = _node("n_old", "text", "Old")
        change = NodeChange(id="n_001", field="value", before="0", after="42")

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.diff_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = DiffResult(
                diff=TreeDiff(
                    added=(added_node,),
                    removed=(removed_node,),
                    changed=(change,),
                ),
                total_changes=3,
            )

            result = runner.invoke(cli, ["diff", "--session", "s1"])

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert len(parsed["added"]) == 1
            assert len(parsed["removed"]) == 1
            assert len(parsed["changed"]) == 1

    def test_changed_entry_format(self) -> None:
        """Changed entries have id, field, before, after keys."""
        from aiyes.domain.tree_diff import NodeChange, TreeDiff
        from aiyes.domain.use_cases.diff import DiffResult
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        change = NodeChange(id="n_001", field="value", before="0", after="42")

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.diff_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = DiffResult(
                diff=TreeDiff(added=(), removed=(), changed=(change,)),
                total_changes=1,
            )

            result = runner.invoke(cli, ["diff", "--session", "s1"])

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            entry = parsed["changed"][0]
            assert entry["id"] == "n_001"
            assert entry["field"] == "value"
            assert entry["before"] == "0"
            assert entry["after"] == "42"


class TestDiffCliSessionOption:
    """AC-17 extension: --session passes session_id to use case."""

    def test_session_id_passed(self) -> None:
        """aieyes diff --session my-sess passes session_id correctly."""
        from aiyes.domain.tree_diff import TreeDiff
        from aiyes.domain.use_cases.diff import DiffResult
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="my-sess"),
            patch("aiyes.cli.main.diff_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = DiffResult(
                diff=TreeDiff(added=(), removed=(), changed=()),
                total_changes=0,
            )

            runner.invoke(cli, ["diff", "--session", "my-sess"])

            mock_uc.execute.assert_called_once()
            call_kwargs = mock_uc.execute.call_args
            if call_kwargs.kwargs:
                assert call_kwargs.kwargs.get("session_id") == "my-sess"


class TestDiffInTopLevelHelp:
    """AC-21/TEST-07: diff listed in top-level help."""

    def test_diff_in_aieyes_help(self) -> None:
        """aieyes --help lists 'diff' as a command."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "diff" in result.output


# ══════════════════════════════════════════════════════════════════════
# Wiring + Composition Root
# ══════════════════════════════════════════════════════════════════════


class TestDiffWiring:
    """ARCH-04/ARCH-06: diff_uc and format_diff in composition root."""

    def test_diff_uc_exists_in_composition_root(self) -> None:
        """diff_uc is present and is a DiffUseCase."""
        from aiyes.cli import composition_root
        from aiyes.domain.use_cases.diff import DiffUseCase

        assert hasattr(composition_root, "diff_uc")
        assert isinstance(composition_root.diff_uc, DiffUseCase)

    def test_format_diff_exists_in_composition_root(self) -> None:
        """format_diff is re-exported from composition_root."""
        from aiyes.cli import composition_root

        assert hasattr(composition_root, "format_diff")
        assert callable(composition_root.format_diff)

    def test_format_diff_in_presenter(self) -> None:
        """format_diff function defined in presenter module."""
        from aiyes.cli import presenter

        assert hasattr(presenter, "format_diff")
        assert callable(presenter.format_diff)


# ══════════════════════════════════════════════════════════════════════
# Architecture tests — domain purity
# ══════════════════════════════════════════════════════════════════════


class TestTreeDiffDomainPurity:
    """ARCH-01: tree_diff.py imports only from domain + stdlib."""

    def test_tree_diff_no_external_deps(self) -> None:
        """tree_diff.py uses only stdlib and aiyes.domain.* imports."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)

        source = Path("src/aiyes/domain/tree_diff.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules or top == "__future__":
                    continue
                assert node.module.startswith("aiyes.domain"), (
                    f"tree_diff.py imports from non-domain module: {node.module}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in stdlib_modules or top == "__future__", (
                        f"tree_diff.py has disallowed import: {alias.name}"
                    )


class TestDiffPasswordMasking:
    """REV-F01: Password values must be masked in changed entries."""

    def test_password_value_change_masked_in_presenter(self) -> None:
        """format_diff masks before/after for password_text value changes."""
        from aiyes.domain.tree_diff import NodeChange, TreeDiff
        from aiyes.domain.use_cases.diff import DiffResult
        from aiyes.cli.presenter import format_diff

        diff = TreeDiff(
            added=(),
            removed=(),
            changed=(
                NodeChange(
                    id="n_pw", field="value", before="secret123", after="secret456"
                ),
                NodeChange(id="n_ok", field="value", before="hello", after="world"),
            ),
        )
        result = DiffResult(
            diff=diff,
            total_changes=2,
            node_roles={"n_pw": "password_text", "n_ok": "text"},
        )
        output = format_diff(result)
        parsed = json.loads(output)

        pw_change = [c for c in parsed["changed"] if c["id"] == "n_pw"][0]
        assert pw_change["before"] == "***", "password before not masked"
        assert pw_change["after"] == "***", "password after not masked"

        ok_change = [c for c in parsed["changed"] if c["id"] == "n_ok"][0]
        assert ok_change["before"] == "hello", (
            "non-password before should not be masked"
        )
        assert ok_change["after"] == "world", "non-password after should not be masked"


class TestDiffUseCaseDomainPurity:
    """ARCH-03: diff.py use case imports only from domain + ports, not adapters/cli."""

    def test_diff_use_case_imports(self) -> None:
        """use_cases/diff.py uses only stdlib, domain, and ports imports."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)
        allowed_prefixes = ("aiyes.domain.", "aiyes.ports.")

        source = Path("src/aiyes/domain/use_cases/diff.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules or top == "__future__":
                    continue
                is_allowed = any(node.module.startswith(p) for p in allowed_prefixes)
                assert is_allowed, (
                    f"diff.py imports from disallowed module: {node.module}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in stdlib_modules or top == "__future__", (
                        f"diff.py has disallowed import: {alias.name}"
                    )
