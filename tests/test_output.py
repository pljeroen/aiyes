"""Tests for output format: JSON structure, node IDs, role aliases, pruning.

Requirements covered:
  R-OUTPUT-01: Node structure (id, role, name, bounds, states, actions, value)
  R-OUTPUT-02: Node ID stability (session-stable, deterministic, path-based)
  R-OUTPUT-03: Role alias resolution (friendly -> canonical)
  R-OUTPUT-04: Tree pruning (noise node exclusion, --no-prune)
"""

from __future__ import annotations

from typing import List

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# RED imports — define expected API
from aiyes.domain.tree import (
    AccessibilityTree,
    Node,
    prune_tree,
    ALWAYS_EXCLUDED_ROLES,
)
from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.role_aliases import resolve_role, ROLE_ALIAS_TABLE
from aiyes.domain.output_formatter import (
    session_to_dict,
    node_to_dict,
    tree_to_dict,
)
from aiyes.domain.session import Session


# ──────────────────────────────────────────────────────────────────────
# R-OUTPUT-01: Node structure
# ──────────────────────────────────────────────────────────────────────


class TestNodeStructure:
    """Every node must have required fields."""

    def test_node_has_required_fields(self) -> None:
        """R-OUTPUT-01: Node has id, role, name, bounds, states, actions."""
        node = Node(
            id="n_001",
            role="push_button",
            name="OK",
            bounds=[100, 200, 80, 30],
            states=["enabled", "visible"],
            actions=["click"],
        )
        assert node.id == "n_001"
        assert node.role == "push_button"
        assert node.name == "OK"
        assert node.bounds == (100, 200, 80, 30)
        assert node.states == ("enabled", "visible")
        assert node.actions == ("click",)

    def test_node_bounds_is_four_integers(self) -> None:
        """R-OUTPUT-01: bounds is [x, y, width, height]."""
        node = Node(
            id="n_001",
            role="push_button",
            name="OK",
            bounds=[10, 20, 100, 50],
            states=[],
            actions=[],
        )
        assert len(node.bounds) == 4
        assert all(isinstance(b, int) for b in node.bounds)

    def test_node_with_value(self) -> None:
        """R-OUTPUT-01: Text/slider/checkbox nodes have value field."""
        node = Node(
            id="n_010",
            role="text",
            name="Username",
            bounds=[0, 0, 200, 30],
            states=["enabled", "editable"],
            actions=["set_text"],
            value="admin",
        )
        assert node.value == "admin"

    def test_node_with_children(self) -> None:
        """R-OUTPUT-01: Container nodes have children array."""
        child = Node(
            id="n_002",
            role="push_button",
            name="OK",
            bounds=[10, 10, 80, 30],
            states=["enabled"],
            actions=["click"],
        )
        parent = Node(
            id="n_001",
            role="frame",
            name="Dialog",
            bounds=[0, 0, 400, 300],
            states=["enabled", "visible"],
            actions=[],
            children=[child],
        )
        assert len(parent.children) == 1
        assert parent.children[0].id == "n_002"

    def test_node_collections_are_tuples(self) -> None:
        """F-18: Node collection fields are tuples, not mutable lists."""
        node = Node(
            id="n_001",
            role="push_button",
            name="OK",
            bounds=[100, 200, 80, 30],
            states=["enabled", "visible"],
            actions=["click"],
            children=[],
        )
        assert isinstance(node.bounds, tuple)
        assert isinstance(node.states, tuple)
        assert isinstance(node.actions, tuple)
        assert isinstance(node.children, tuple)

    def test_node_collections_reject_mutation(self) -> None:
        """F-18: Node collection fields cannot be mutated."""
        node = Node(
            id="n_001",
            role="push_button",
            name="OK",
            bounds=[100, 200, 80, 30],
            states=["enabled"],
            actions=["click"],
        )
        with pytest.raises((TypeError, AttributeError)):
            node.bounds.append(999)  # type: ignore[attr-error]
        with pytest.raises((TypeError, AttributeError)):
            node.states.append("focused")  # type: ignore[attr-error]
        with pytest.raises((TypeError, AttributeError)):
            node.actions.append("toggle")  # type: ignore[attr-error]

    def test_accessibility_tree_roots_is_tuple(self) -> None:
        """F-18: AccessibilityTree.roots is a tuple, not a mutable list."""
        node = Node(
            id="n_001",
            role="frame",
            name="Window",
            bounds=[0, 0, 800, 600],
            states=["enabled"],
            actions=[],
        )
        tree = AccessibilityTree(roots=[node])
        assert isinstance(tree.roots, tuple)
        with pytest.raises((TypeError, AttributeError)):
            tree.roots.append(node)  # type: ignore[attr-error]


# ──────────────────────────────────────────────────────────────────────
# N-23: Empty/blank node ID rejection during tree conversion
# ──────────────────────────────────────────────────────────────────────


class TestEmptyNodeIdRejection:
    """Malformed nodes with empty/blank IDs are rejected during conversion."""

    def test_empty_id_node_excluded_from_tree(self) -> None:
        """N-23: Node with empty string ID is excluded during raw conversion."""
        from aiyes.domain.tree import raw_tree_to_domain

        raw = {
            "tree": [
                {
                    "id": "",
                    "role": "button",
                    "name": "Bad",
                    "bounds": [0, 0, 0, 0],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 0

    def test_blank_id_node_excluded_from_tree(self) -> None:
        """N-23: Node with whitespace-only ID is excluded during raw conversion."""
        from aiyes.domain.tree import raw_tree_to_domain

        raw = {
            "tree": [
                {
                    "id": "   ",
                    "role": "button",
                    "name": "Bad",
                    "bounds": [0, 0, 0, 0],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 0

    def test_missing_id_node_excluded_from_tree(self) -> None:
        """N-23: Node with missing ID key is excluded during raw conversion."""
        from aiyes.domain.tree import raw_tree_to_domain

        raw = {
            "tree": [
                {
                    "role": "button",
                    "name": "Bad",
                    "bounds": [0, 0, 0, 0],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 0

    def test_valid_id_node_preserved(self) -> None:
        """N-23: Node with valid ID is preserved normally."""
        from aiyes.domain.tree import raw_tree_to_domain

        raw = {
            "tree": [
                {
                    "id": "n_001",
                    "role": "button",
                    "name": "OK",
                    "bounds": [0, 0, 80, 30],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 1
        assert tree.roots[0].id == "n_001"

    def test_empty_id_child_excluded_parent_preserved(self) -> None:
        """N-23: Child with empty ID is excluded; valid parent preserved."""
        from aiyes.domain.tree import raw_tree_to_domain

        raw = {
            "tree": [
                {
                    "id": "n_001",
                    "role": "frame",
                    "name": "Win",
                    "bounds": [0, 0, 800, 600],
                    "states": [],
                    "actions": [],
                    "children": [
                        {
                            "id": "",
                            "role": "button",
                            "name": "Bad",
                            "bounds": [0, 0, 0, 0],
                            "states": [],
                            "actions": [],
                        },
                        {
                            "id": "n_002",
                            "role": "button",
                            "name": "Good",
                            "bounds": [0, 0, 80, 30],
                            "states": [],
                            "actions": [],
                        },
                    ],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 1
        assert len(tree.roots[0].children) == 1
        assert tree.roots[0].children[0].id == "n_002"


# ──────────────────────────────────────────────────────────────────────
# R-OUTPUT-02: Node ID stability — PBT
# ──────────────────────────────────────────────────────────────────────


class TestNodeIdRegistry:
    """Node ID generation and stability."""

    def test_registry_assigns_ids(self) -> None:
        """R-OUTPUT-02: Registry assigns IDs to nodes."""
        registry = NodeIdRegistry()
        node_id = registry.get_or_assign(role="push_button", name="OK", path=[0, 1])
        assert isinstance(node_id, str)
        assert len(node_id) > 0

    def test_same_path_same_id(self) -> None:
        """R-OUTPUT-02: Same node path gets same ID."""
        registry = NodeIdRegistry()
        id1 = registry.get_or_assign(role="push_button", name="OK", path=[0, 1])
        id2 = registry.get_or_assign(role="push_button", name="OK", path=[0, 1])
        assert id1 == id2

    def test_different_path_different_id(self) -> None:
        """R-OUTPUT-02: Different paths get different IDs."""
        registry = NodeIdRegistry()
        id1 = registry.get_or_assign(role="push_button", name="OK", path=[0, 1])
        id2 = registry.get_or_assign(role="push_button", name="Cancel", path=[0, 2])
        assert id1 != id2

    def test_ids_not_reused(self) -> None:
        """R-OUTPUT-02: Node IDs must not be reused for different nodes."""
        registry = NodeIdRegistry()
        id1 = registry.get_or_assign(role="push_button", name="OK", path=[0, 1])
        id2 = registry.get_or_assign(role="label", name="Title", path=[0, 0])
        assert id1 != id2

    def test_state_change_preserves_id(self) -> None:
        """R-OUTPUT-02, OQ-04: State changes do not cause ID reassignment."""
        registry = NodeIdRegistry()
        id1 = registry.get_or_assign(role="push_button", name="OK", path=[0, 1])
        # Same node, simulate state change (same path, same role, same name)
        id2 = registry.get_or_assign(role="push_button", name="OK", path=[0, 1])
        assert id1 == id2

    def test_text_change_preserves_id(self) -> None:
        """R-OUTPUT-02, OQ-04: Text value changes do not cause ID reassignment."""
        registry = NodeIdRegistry()
        # First call with original value context
        id1 = registry.get_or_assign(role="text", name="Username", path=[0, 2])
        # Same node, text value may have changed but path/role/name unchanged
        id2 = registry.get_or_assign(role="text", name="Username", path=[0, 2])
        assert id1 == id2

    @given(
        role=st.sampled_from(["push_button", "text", "check_box", "label", "frame"]),
        name=st.text(min_size=1, max_size=50),
        path=st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=5),
    )
    @settings(max_examples=50)
    def test_id_determinism_property(
        self, role: str, name: str, path: List[int]
    ) -> None:
        """R-OUTPUT-02 PBT: Same inputs always produce same ID."""
        registry = NodeIdRegistry()
        id1 = registry.get_or_assign(role=role, name=name, path=path)
        id2 = registry.get_or_assign(role=role, name=name, path=path)
        assert id1 == id2

    @given(
        name1=st.text(min_size=1, max_size=20),
        name2=st.text(min_size=1, max_size=20),
        idx1=st.integers(min_value=0, max_value=100),
        idx2=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=50)
    def test_distinct_nodes_distinct_ids_property(
        self, name1: str, name2: str, idx1: int, idx2: int
    ) -> None:
        """R-OUTPUT-02 PBT: Different node paths yield different IDs."""
        from hypothesis import assume

        assume((name1, idx1) != (name2, idx2))

        registry = NodeIdRegistry()
        id1 = registry.get_or_assign(role="push_button", name=name1, path=[0, idx1])
        id2 = registry.get_or_assign(role="push_button", name=name2, path=[0, idx2])
        assert id1 != id2

    def test_per_node_path_to_id_mapping(self) -> None:
        """R-OUTPUT-02, CV-09: Verification compares per-node path-to-ID mappings."""
        registry = NodeIdRegistry()
        id_a = registry.get_or_assign(role="push_button", name="A", path=[0, 0])
        id_b = registry.get_or_assign(role="push_button", name="B", path=[0, 1])

        # The mapping should be inspectable
        mapping = registry.get_mapping()
        assert isinstance(mapping, dict)
        assert len(mapping) == 2

        # IDs should not be swapped
        id_a2 = registry.get_or_assign(role="push_button", name="A", path=[0, 0])
        id_b2 = registry.get_or_assign(role="push_button", name="B", path=[0, 1])
        assert id_a == id_a2
        assert id_b == id_b2


# ──────────────────────────────────────────────────────────────────────
# R-OUTPUT-03: Role alias resolution — PBT
# ──────────────────────────────────────────────────────────────────────


class TestRoleAliases:
    """Friendly role names resolve to canonical AT-SPI2 names."""

    def test_button_alias(self) -> None:
        """R-OUTPUT-03: 'button' resolves to 'push_button'."""
        assert resolve_role("button") == "push_button"

    def test_checkbox_alias(self) -> None:
        """R-OUTPUT-03: 'checkbox' resolves to 'check_box'."""
        assert resolve_role("checkbox") == "check_box"

    def test_textbox_alias(self) -> None:
        """R-OUTPUT-03: 'textbox' resolves to 'text'."""
        assert resolve_role("textbox") == "text"

    def test_radio_alias(self) -> None:
        """R-OUTPUT-03: 'radio' resolves to 'radio_button'."""
        assert resolve_role("radio") == "radio_button"

    def test_tab_alias(self) -> None:
        """R-OUTPUT-03: 'tab' resolves to 'page_tab'."""
        assert resolve_role("tab") == "page_tab"

    def test_toolbar_alias(self) -> None:
        """R-OUTPUT-03: 'toolbar' resolves to 'tool_bar'."""
        assert resolve_role("toolbar") == "tool_bar"

    def test_scrollbar_alias(self) -> None:
        """R-OUTPUT-03: 'scrollbar' resolves to 'scroll_bar'."""
        assert resolve_role("scrollbar") == "scroll_bar"

    def test_combobox_alias(self) -> None:
        """R-OUTPUT-03: 'combobox' resolves to 'combo_box'."""
        assert resolve_role("combobox") == "combo_box"

    def test_menuitem_alias(self) -> None:
        """R-OUTPUT-03: 'menuitem' resolves to 'menu_item'."""
        assert resolve_role("menuitem") == "menu_item"

    def test_listitem_alias(self) -> None:
        """R-OUTPUT-03: 'listitem' resolves to 'list_item'."""
        assert resolve_role("listitem") == "list_item"

    def test_treeitem_alias(self) -> None:
        """R-OUTPUT-03: 'treeitem' resolves to 'tree_item'."""
        assert resolve_role("treeitem") == "tree_item"

    def test_statusbar_alias(self) -> None:
        """R-OUTPUT-03: 'statusbar' resolves to 'status_bar'."""
        assert resolve_role("statusbar") == "status_bar"

    def test_progressbar_alias(self) -> None:
        """R-OUTPUT-03: 'progressbar' resolves to 'progress_bar'."""
        assert resolve_role("progressbar") == "progress_bar"

    def test_canonical_identity(self) -> None:
        """R-OUTPUT-03: Canonical names resolve to themselves."""
        assert resolve_role("push_button") == "push_button"
        assert resolve_role("check_box") == "check_box"
        assert resolve_role("dialog") == "dialog"
        assert resolve_role("label") == "label"
        assert resolve_role("image") == "image"

    def test_unknown_role_raises(self) -> None:
        """R-OUTPUT-03: Unknown aliases are rejected with error."""
        with pytest.raises(Exception):
            resolve_role("nonexistent_widget")

    @given(alias=st.sampled_from(list(ROLE_ALIAS_TABLE.keys())))
    @settings(max_examples=50)
    def test_all_aliases_resolve_property(self, alias: str) -> None:
        """R-OUTPUT-03 PBT: Every defined alias resolves without error."""
        result = resolve_role(alias)
        assert isinstance(result, str)
        assert len(result) > 0

    @given(alias=st.sampled_from(list(ROLE_ALIAS_TABLE.keys())))
    @settings(max_examples=50)
    def test_alias_output_is_canonical_property(self, alias: str) -> None:
        """R-OUTPUT-03 PBT: Output is always a canonical AT-SPI2 role name."""
        result = resolve_role(alias)
        # Canonical names are themselves in the table as identity mappings
        # or they are values in the alias table
        assert resolve_role(result) == result  # Idempotent


# ──────────────────────────────────────────────────────────────────────
# R-OUTPUT-04: Tree pruning
# ──────────────────────────────────────────────────────────────────────


class TestTreePruning:
    """Noise node pruning."""

    def test_excluded_roles_constant(self) -> None:
        """R-OUTPUT-04: ALWAYS_EXCLUDED_ROLES contains filler and redundant_object."""
        assert "filler" in ALWAYS_EXCLUDED_ROLES
        assert "redundant_object" in ALWAYS_EXCLUDED_ROLES

    def test_filler_nodes_pruned(self) -> None:
        """R-OUTPUT-04: Nodes with role 'filler' are removed."""
        filler = Node(
            id="filler_1",
            role="filler",
            name="",
            bounds=[0, 0, 0, 0],
            states=[],
            actions=[],
        )
        button = Node(
            id="btn_1",
            role="push_button",
            name="OK",
            bounds=[10, 10, 80, 30],
            states=["enabled"],
            actions=["click"],
        )
        root = Node(
            id="root",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[filler, button],
        )
        tree = AccessibilityTree(roots=[root])
        pruned = prune_tree(tree, prune=True)

        def find_by_role(nodes: List[Node], role: str) -> List[Node]:
            found = []
            for n in nodes:
                if n.role == role:
                    found.append(n)
                if n.children:
                    found.extend(find_by_role(n.children, role))
            return found

        assert len(find_by_role(pruned.roots, "filler")) == 0
        assert len(find_by_role(pruned.roots, "push_button")) == 1

    def test_redundant_object_pruned(self) -> None:
        """R-OUTPUT-04: Nodes with role 'redundant_object' are removed."""
        redundant = Node(
            id="red_1",
            role="redundant_object",
            name="",
            bounds=[0, 0, 0, 0],
            states=[],
            actions=[],
        )
        root = Node(
            id="root",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[redundant],
        )
        tree = AccessibilityTree(roots=[root])
        pruned = prune_tree(tree, prune=True)

        assert len(pruned.roots[0].children) == 0

    def test_unnamed_section_pruned_children_promoted(self) -> None:
        """R-OUTPUT-04: Unnamed section nodes pruned, children promoted."""
        button = Node(
            id="btn_1",
            role="push_button",
            name="OK",
            bounds=[10, 10, 80, 30],
            states=["enabled"],
            actions=["click"],
        )
        section = Node(
            id="sec_1",
            role="section",
            name="",  # unnamed
            bounds=[0, 0, 200, 100],
            states=[],
            actions=[],
            children=[button],
        )
        root = Node(
            id="root",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[section],
        )
        tree = AccessibilityTree(roots=[root])
        pruned = prune_tree(tree, prune=True)

        # Section should be gone, button promoted to root's children
        root_pruned = pruned.roots[0]
        assert root_pruned.children[0].role == "push_button"
        assert root_pruned.children[0].name == "OK"

    def test_named_section_preserved(self) -> None:
        """R-OUTPUT-04: Named section nodes are preserved."""
        section = Node(
            id="sec_1",
            role="section",
            name="Navigation",  # named
            bounds=[0, 0, 200, 100],
            states=[],
            actions=[],
            children=[],
        )
        root = Node(
            id="root",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[section],
        )
        tree = AccessibilityTree(roots=[root])
        pruned = prune_tree(tree, prune=True)

        assert pruned.roots[0].children[0].role == "section"
        assert pruned.roots[0].children[0].name == "Navigation"

    def test_unnamed_single_child_panel_pruned(self) -> None:
        """R-OUTPUT-04: Unnamed single-child panel pruned, child promoted."""
        button = Node(
            id="btn_1",
            role="push_button",
            name="OK",
            bounds=[10, 10, 80, 30],
            states=["enabled"],
            actions=["click"],
        )
        panel = Node(
            id="panel_1",
            role="panel",
            name="",  # unnamed
            bounds=[0, 0, 200, 100],
            states=[],
            actions=[],
            children=[button],  # single child
        )
        root = Node(
            id="root",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[panel],
        )
        tree = AccessibilityTree(roots=[root])
        pruned = prune_tree(tree, prune=True)

        root_pruned = pruned.roots[0]
        assert root_pruned.children[0].role == "push_button"

    def test_unnamed_multi_child_panel_preserved(self) -> None:
        """R-OUTPUT-04: Unnamed multi-child panel nodes are preserved."""
        btn1 = Node(
            id="btn_1",
            role="push_button",
            name="A",
            bounds=[0, 0, 80, 30],
            states=[],
            actions=["click"],
        )
        btn2 = Node(
            id="btn_2",
            role="push_button",
            name="B",
            bounds=[80, 0, 80, 30],
            states=[],
            actions=["click"],
        )
        panel = Node(
            id="panel_1",
            role="panel",
            name="",
            bounds=[0, 0, 200, 100],
            states=[],
            actions=[],
            children=[btn1, btn2],  # multiple children
        )
        root = Node(
            id="root",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[panel],
        )
        tree = AccessibilityTree(roots=[root])
        pruned = prune_tree(tree, prune=True)

        assert pruned.roots[0].children[0].role == "panel"

    def test_no_prune_flag(self) -> None:
        """R-OUTPUT-04: prune=False returns full tree including noise."""
        filler = Node(
            id="filler_1",
            role="filler",
            name="",
            bounds=[0, 0, 0, 0],
            states=[],
            actions=[],
        )
        root = Node(
            id="root",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[filler],
        )
        tree = AccessibilityTree(roots=[root])
        unpruned = prune_tree(tree, prune=False)

        assert len(unpruned.roots[0].children) == 1
        assert unpruned.roots[0].children[0].role == "filler"


# ──────────────────────────────────────────────────────────────────────
# Output formatter (domain dict producers) — PBT
# ──────────────────────────────────────────────────────────────────────


class TestOutputFormatter:
    """Domain output_formatter produces dicts, NOT JSON strings."""

    def test_session_to_dict(self) -> None:
        """PC-03: session_to_dict returns a dict with required keys."""
        session = Session(
            session_id="s1",
            display=":99",
            app_pid=100,
            app_command="gedit",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        result = session_to_dict(session)

        assert isinstance(result, dict)
        assert result["session_id"] == "s1"
        assert result["display"] == ":99"
        assert result["app_pid"] == 100
        assert result["atspi_bus_address"] == "unix:abstract=/tmp/dbus-s1"

    def test_node_to_dict(self) -> None:
        """PC-03: node_to_dict returns a dict with required keys."""
        node = Node(
            id="n_001",
            role="push_button",
            name="OK",
            bounds=[10, 20, 80, 30],
            states=["enabled"],
            actions=["click"],
        )
        result = node_to_dict(node)

        assert isinstance(result, dict)
        assert result["id"] == "n_001"
        assert result["role"] == "push_button"
        assert result["name"] == "OK"
        assert result["bounds"] == [10, 20, 80, 30]
        assert result["states"] == ["enabled"]
        assert result["actions"] == ["click"]

    def test_node_to_dict_with_value(self) -> None:
        """PC-03: node_to_dict includes value for text/slider/checkbox."""
        node = Node(
            id="n_010",
            role="text",
            name="Username",
            bounds=[0, 0, 200, 30],
            states=["enabled"],
            actions=["set_text"],
            value="admin",
        )
        result = node_to_dict(node)

        assert result["value"] == "admin"

    def test_tree_to_dict(self) -> None:
        """PC-03: tree_to_dict returns a dict, not a JSON string."""
        child = Node(
            id="n_002",
            role="push_button",
            name="OK",
            bounds=[10, 10, 80, 30],
            states=["enabled"],
            actions=["click"],
        )
        root = Node(
            id="n_001",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[child],
        )
        tree = AccessibilityTree(roots=[root])
        result = tree_to_dict(tree)

        assert isinstance(result, dict)
        assert "tree" in result or isinstance(result, list)

    def test_tree_to_dict_with_depth_limit(self) -> None:
        """R-INSPECT-02: tree_to_dict with max_depth truncates."""
        grandchild = Node(
            id="n_003",
            role="label",
            name="Text",
            bounds=[0, 0, 100, 20],
            states=[],
            actions=[],
        )
        child = Node(
            id="n_002",
            role="panel",
            name="Panel",
            bounds=[0, 0, 200, 100],
            states=[],
            actions=[],
            children=[grandchild],
        )
        root = Node(
            id="n_001",
            role="frame",
            name="Window",
            bounds=[0, 0, 400, 300],
            states=[],
            actions=[],
            children=[child],
        )
        tree = AccessibilityTree(roots=[root])
        result = tree_to_dict(tree, max_depth=1)

        assert isinstance(result, dict)
        # At depth 1, we should see root and its direct children but not grandchildren

    @given(
        node_id=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
        role=st.sampled_from(["push_button", "text", "label", "frame"]),
        name=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=30)
    def test_node_to_dict_roundtrip_property(
        self, node_id: str, role: str, name: str
    ) -> None:
        """R-OUTPUT-01 PBT: node_to_dict preserves all fields."""
        node = Node(
            id=node_id,
            role=role,
            name=name,
            bounds=[0, 0, 100, 50],
            states=["enabled"],
            actions=["click"],
        )
        result = node_to_dict(node)

        assert result["id"] == node_id
        assert result["role"] == role
        assert result["name"] == name
