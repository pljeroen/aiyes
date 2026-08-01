"""AIYES-40 Group A — Name Pattern Matching Whitespace Normalization.

Tests for centralized whitespace-normalized name matching across all
matching locations: find, wait, compound_do, filter_tree_by_window, menu.

Traceability — Acceptance Criteria:
  AC-A01: find matches node with newline in name using space in pattern
  AC-A02: find substring match after normalization ("Me\\nMe" -> "Me Me")
  AC-A03: find matches with multiple newlines collapsed
  AC-A04: find matches with leading/trailing/multiple spaces normalized
  AC-A05: tab in pattern normalized to space for matching
  AC-A06: wait uses same normalization
  AC-A07: compound_do uses same normalization
  AC-A08: filter_tree_by_window uses normalization for window title
  AC-A09: menu exact match after normalization
  AC-A10: output preserves original (un-normalized) name
  AC-A11: NodeIdRegistry key uses original name (not tested here — no change)
  AC-A12: whitespace-only pattern treated as no name filter
  AC-A13: empty pattern treated as no name filter
  AC-A14: empty name not matched by non-empty pattern
  AC-A15: newlines-only name normalizes to empty, not matched by "hello"

Additional:
  None guard: node_name=None treated as empty string
  Idempotency: normalize(normalize(x)) == normalize(x)
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from aiyes.domain.matching import name_matches, normalize_whitespace
from aiyes.domain.session import Session
from aiyes.domain.tree import (
    AccessibilityTree,
    Node,
    filter_tree_by_window,
    raw_tree_to_domain,
)
from aiyes.domain.use_cases.compound_do import CompoundDoUseCase
from aiyes.domain.use_cases.find import FindUseCase
from aiyes.domain.use_cases.menu import MenuResult, _find_node_by_name, _ITEM_ROLES
from aiyes.domain.use_cases.wait import WaitUseCase

from tests.conftest import (
    FakeAccessibilityAction,
    FakeAccessibilityTree,
    FakeClock,
    FakeSessionRepository,
    FakeTreeStore,
    make_domain_tree,
    make_node,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_linux_session(session_id: str = "test-s") -> Session:
    return Session(
        session_id=session_id,
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


def _tree_with_nodes(nodes: list) -> AccessibilityTree:
    """Build domain tree from raw node dicts."""
    return make_domain_tree(nodes)


def _setup_find_uc(tree: AccessibilityTree, session_id: str = "test-s") -> FindUseCase:
    """Set up FindUseCase with given tree and session."""
    repo = FakeSessionRepository()
    repo.save(_make_linux_session(session_id))
    tree_port = FakeAccessibilityTree(tree)
    tree_store = FakeTreeStore()
    return FindUseCase(tree=tree_port, session_repo=repo, tree_store=tree_store)


def _setup_wait_uc(tree: AccessibilityTree, session_id: str = "test-s") -> WaitUseCase:
    """Set up WaitUseCase with given tree and session."""
    repo = FakeSessionRepository()
    repo.save(_make_linux_session(session_id))
    tree_port = FakeAccessibilityTree(tree)
    tree_store = FakeTreeStore()
    clock = FakeClock()
    return WaitUseCase(
        tree=tree_port, session_repo=repo, tree_store=tree_store, clock=clock
    )


def _setup_compound_do_uc(
    tree: AccessibilityTree, session_id: str = "test-s"
) -> CompoundDoUseCase:
    """Set up CompoundDoUseCase with given tree and session."""
    repo = FakeSessionRepository()
    repo.save(_make_linux_session(session_id))
    tree_port = FakeAccessibilityTree(tree)
    action_port = FakeAccessibilityAction()
    tree_store = FakeTreeStore()
    clock = FakeClock()
    return CompoundDoUseCase(
        tree=tree_port,
        action=action_port,
        session_repo=repo,
        tree_store=tree_store,
        clock=clock,
    )


# ═══════════════════════════════════════════════════════════════════════
# Unit tests: normalize_whitespace
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeWhitespace:
    """Pure function tests for normalize_whitespace."""

    def test_newline_to_space(self) -> None:
        assert normalize_whitespace("Home\nTab") == "Home Tab"

    def test_multiple_newlines_collapsed(self) -> None:
        assert normalize_whitespace("A\n\nB") == "A B"

    def test_tab_to_space(self) -> None:
        assert normalize_whitespace("A\tB") == "A B"

    def test_crlf_to_space(self) -> None:
        assert normalize_whitespace("A\r\nB") == "A B"

    def test_mixed_whitespace(self) -> None:
        assert normalize_whitespace("  A  \n  B  ") == "A B"

    def test_empty_string(self) -> None:
        assert normalize_whitespace("") == ""

    def test_whitespace_only(self) -> None:
        assert normalize_whitespace("  \n\t  ") == ""

    def test_no_change_needed(self) -> None:
        assert normalize_whitespace("hello") == "hello"

    def test_leading_trailing_stripped(self) -> None:
        assert normalize_whitespace("  hello  ") == "hello"

    def test_multiple_spaces_collapsed(self) -> None:
        assert normalize_whitespace("hello   world") == "hello world"

    def test_idempotent(self) -> None:
        """normalize(normalize(x)) == normalize(x)."""
        cases = [
            "Home\nTab 1 of 4",
            "  hello   world  ",
            "Me\nMe",
            "\n\n",
            "",
            "normal text",
        ]
        for text in cases:
            once = normalize_whitespace(text)
            twice = normalize_whitespace(once)
            assert once == twice, f"Not idempotent for {text!r}"

    def test_newlines_only_becomes_empty(self) -> None:
        """AC-A15 prerequisite: newlines-only normalizes to empty."""
        assert normalize_whitespace("\n\n") == ""


# ═══════════════════════════════════════════════════════════════════════
# Unit tests: name_matches
# ═══════════════════════════════════════════════════════════════════════


class TestNameMatches:
    """Pure function tests for name_matches."""

    def test_ac_a01_newline_in_name_space_in_pattern(self) -> None:
        """AC-A01: 'Home Tab' matches 'Home\\nTab 1 of 4'."""
        assert name_matches("Home\nTab 1 of 4", "Home Tab") is True

    def test_ac_a02_substring_after_normalization(self) -> None:
        """AC-A02: 'Me' matches 'Me\\nMe' (normalized: 'Me Me' contains 'me')."""
        assert name_matches("Me\nMe", "Me") is True

    def test_ac_a03_multiple_newlines(self) -> None:
        """AC-A03: 'search results' matches 'Search\\n\\nResults'."""
        assert name_matches("Search\n\nResults", "search results") is True

    def test_ac_a04_multiple_spaces_normalized(self) -> None:
        """AC-A04: 'hello world' matches '  hello   world  '."""
        assert name_matches("  hello   world  ", "hello world") is True

    def test_ac_a05_tab_in_pattern(self) -> None:
        """AC-A05: pattern with tab matches name with space."""
        assert name_matches("hello world", "hello\tworld") is True

    def test_ac_a12_whitespace_only_pattern(self) -> None:
        """AC-A12: whitespace-only pattern matches everything."""
        assert name_matches("anything", " ") is True

    def test_ac_a13_empty_pattern(self) -> None:
        """AC-A13: empty pattern matches everything."""
        assert name_matches("anything", "") is True

    def test_ac_a14_empty_name_not_matched(self) -> None:
        """AC-A14: empty name not matched by 'hello'."""
        assert name_matches("", "hello") is False

    def test_ac_a15_newlines_only_not_matched(self) -> None:
        """AC-A15: name '\\n\\n' normalizes to '', not matched by 'hello'."""
        assert name_matches("\n\n", "hello") is False

    def test_case_insensitive(self) -> None:
        """Case insensitivity: 'HOME' matches 'Home\\nTab'."""
        assert name_matches("Home\nTab", "HOME") is True

    def test_no_false_positive(self) -> None:
        """'Cancel' does NOT match 'OK Button'."""
        assert name_matches("OK Button", "Cancel") is False

    def test_none_node_name_guard(self) -> None:
        """None node_name is treated as empty string."""
        # Passing None directly (defensive guard)
        assert name_matches(None, "hello") is False  # type: ignore[arg-type]
        assert name_matches(None, "") is True  # type: ignore[arg-type]

    def test_whitespace_only_pattern_matches_empty_name(self) -> None:
        """Whitespace-only pattern matches even empty names (acts as no filter)."""
        assert name_matches("", " ") is True

    def test_tab_pattern_matches_tab_name(self) -> None:
        """Tab in both name and pattern both normalize to space."""
        assert name_matches("hello\tworld", "hello\tworld") is True


# ═══════════════════════════════════════════════════════════════════════
# Integration tests: FindUseCase with normalization
# ═══════════════════════════════════════════════════════════════════════


class TestFindUseCaseNormalization:
    """AC-A01, AC-A02, AC-A03, AC-A04, AC-A10: Find with whitespace normalization."""

    def test_ac_a01_find_newline_name(self) -> None:
        """AC-A01: find push_button 'Home Tab' matches 'Home\\nTab 1 of 4'."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_btn", "push_button", "Home\nTab 1 of 4"),
                    ],
                ),
            ]
        )
        uc = _setup_find_uc(tree)
        result = uc.execute("test-s", "push_button", name_pattern="Home Tab")
        assert len(result) == 1
        assert result[0].id == "n_btn"

    def test_ac_a02_find_substring_normalized(self) -> None:
        """AC-A02: find push_button 'Me' matches 'Me\\nMe'."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_btn", "push_button", "Me\nMe"),
                    ],
                ),
            ]
        )
        uc = _setup_find_uc(tree)
        result = uc.execute("test-s", "push_button", name_pattern="Me")
        assert len(result) == 1
        assert result[0].id == "n_btn"

    def test_ac_a03_multiple_newlines(self) -> None:
        """AC-A03: find * 'search results' matches 'Search\\n\\nResults'."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_lbl", "label", "Search\n\nResults"),
                    ],
                ),
            ]
        )
        uc = _setup_find_uc(tree)
        result = uc.execute("test-s", "*", name_pattern="search results")
        # Should match at least the label (frame may also match since * = all)
        ids = [r.id for r in result]
        assert "n_lbl" in ids

    def test_ac_a04_multiple_spaces(self) -> None:
        """AC-A04: find * 'hello world' matches '  hello   world  '."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_lbl", "label", "  hello   world  "),
                    ],
                ),
            ]
        )
        uc = _setup_find_uc(tree)
        result = uc.execute("test-s", "*", name_pattern="hello world")
        ids = [r.id for r in result]
        assert "n_lbl" in ids

    def test_ac_a10_output_preserves_original_name(self) -> None:
        """AC-A10: output name field preserves original 'Home\\nTab 1 of 4'."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_btn", "push_button", "Home\nTab 1 of 4"),
                    ],
                ),
            ]
        )
        uc = _setup_find_uc(tree)
        result = uc.execute("test-s", "push_button", name_pattern="Home Tab")
        assert len(result) == 1
        assert result[0].name == "Home\nTab 1 of 4"  # Original preserved!

    def test_ac_a12_whitespace_only_pattern_is_no_filter(self) -> None:
        """AC-A12: find push_button ' ' returns all push_buttons (no name filter)."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_btn1", "push_button", "OK"),
                        make_node("n_btn2", "push_button", "Cancel"),
                    ],
                ),
            ]
        )
        uc = _setup_find_uc(tree)
        result = uc.execute("test-s", "push_button", name_pattern=" ")
        assert len(result) == 2

    def test_ac_a13_empty_pattern_is_no_filter(self) -> None:
        """AC-A13: find push_button '' returns all push_buttons."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_btn1", "push_button", "OK"),
                        make_node("n_btn2", "push_button", "Cancel"),
                    ],
                ),
            ]
        )
        uc = _setup_find_uc(tree)
        result = uc.execute("test-s", "push_button", name_pattern="")
        assert len(result) == 2

    def test_canonical_combo_box_matches_raw_atspi_role(self) -> None:
        """A Firefox raw ``combo box`` role satisfies ``combo_box`` requests."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_combo",
                    "combo box",
                    "Search engine",
                ),
            ]
        )

        result = _setup_find_uc(tree).execute(
            "test-s", "combo_box", name_pattern="Search engine"
        )

        assert [(node.id, node.role, node.name) for node in result] == [
            ("n_combo", "combo box", "Search engine")
        ]
        assert result.role_drift == ()

    def test_canonical_push_button_matches_raw_atspi_alias(self) -> None:
        """A Firefox raw ``button`` role satisfies ``push_button`` requests."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_button",
                    "button",
                    "Submit",
                ),
            ]
        )

        result = _setup_find_uc(tree).execute(
            "test-s", "push_button", name_pattern="Submit"
        )

        assert [(node.id, node.role, node.name) for node in result] == [
            ("n_button", "button", "Submit")
        ]
        assert result.role_drift == ()


# ═══════════════════════════════════════════════════════════════════════
# Integration tests: WaitUseCase with normalization
# ═══════════════════════════════════════════════════════════════════════


class TestWaitUseCaseNormalization:
    """AC-A06: Wait uses same normalization."""

    def test_ac_a06_wait_newline_name(self) -> None:
        """AC-A06: wait push_button 'Home Tab' matches 'Home\\nTab 1 of 4'."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_btn", "push_button", "Home\nTab 1 of 4"),
                    ],
                ),
            ]
        )
        uc = _setup_wait_uc(tree)
        result = uc.execute("test-s", "push_button", name_pattern="Home Tab")
        assert result.found is True
        assert result.id == "n_btn"


# ═══════════════════════════════════════════════════════════════════════
# Integration tests: CompoundDoUseCase with normalization
# ═══════════════════════════════════════════════════════════════════════


class TestCompoundDoUseCaseNormalization:
    """AC-A07: CompoundDo uses same normalization."""

    def test_ac_a07_do_find_newline_name(self) -> None:
        """AC-A07: do --role push_button --name 'Home Tab' matches multiline name."""
        tree = _tree_with_nodes(
            [
                make_node(
                    "n_root",
                    "frame",
                    "Window",
                    children=[
                        make_node("n_btn", "push_button", "Home\nTab 1 of 4"),
                    ],
                ),
            ]
        )
        uc = _setup_compound_do_uc(tree)
        result = uc.execute(
            "test-s", role="push_button", action_name="click", name_pattern="Home Tab"
        )
        assert result.found is not None
        assert result.found.id == "n_btn"
        assert result.error is None


# ═══════════════════════════════════════════════════════════════════════
# Integration tests: filter_tree_by_window with normalization
# ═══════════════════════════════════════════════════════════════════════


class TestFilterTreeByWindowNormalization:
    """AC-A08: filter_tree_by_window uses normalization for window title."""

    def test_ac_a08_window_title_newline(self) -> None:
        """AC-A08: window title 'My App' matches root 'My\\nApp - Title'."""
        root = Node(
            id="r1",
            role="frame",
            name="My\nApp - Title",
            bounds=(0, 0, 1280, 800),
            states=("enabled",),
            actions=(),
        )
        tree = AccessibilityTree(roots=(root,))
        filtered = filter_tree_by_window(tree, "My App")
        assert len(filtered.roots) == 1
        assert filtered.roots[0].id == "r1"

    def test_window_title_preserves_original_name(self) -> None:
        """Output preserves original root name after filtering."""
        root = Node(
            id="r1",
            role="frame",
            name="My\nApp - Title",
            bounds=(0, 0, 1280, 800),
            states=("enabled",),
            actions=(),
        )
        tree = AccessibilityTree(roots=(root,))
        filtered = filter_tree_by_window(tree, "My App")
        assert filtered.roots[0].name == "My\nApp - Title"

    def test_window_title_none_returns_unchanged(self) -> None:
        """None window_title returns tree unchanged."""
        root = Node(
            id="r1",
            role="frame",
            name="My App",
            bounds=(0, 0, 1280, 800),
            states=("enabled",),
            actions=(),
        )
        tree = AccessibilityTree(roots=(root,))
        filtered = filter_tree_by_window(tree, None)
        assert len(filtered.roots) == 1

    def test_window_title_no_match(self) -> None:
        """Non-matching window title returns empty tree."""
        root = Node(
            id="r1",
            role="frame",
            name="My\nApp - Title",
            bounds=(0, 0, 1280, 800),
            states=("enabled",),
            actions=(),
        )
        tree = AccessibilityTree(roots=(root,))
        filtered = filter_tree_by_window(tree, "Other Window")
        assert len(filtered.roots) == 0


# ═══════════════════════════════════════════════════════════════════════
# Integration tests: menu _find_node_by_name with normalization
# ═══════════════════════════════════════════════════════════════════════


class TestMenuNormalization:
    """AC-A09: menu exact match after normalization."""

    def test_ac_a09_menu_newline_name(self) -> None:
        """AC-A09: menu 'Save As' matches item with name 'Save\\nAs'."""
        item = Node(
            id="m1",
            role="menu_item",
            name="Save\nAs",
            bounds=(0, 0, 100, 30),
            states=("enabled",),
            actions=("click",),
        )
        found = _find_node_by_name((item,), "Save As", _ITEM_ROLES)
        assert found is not None
        assert found.id == "m1"

    def test_menu_preserves_original_name(self) -> None:
        """Found node preserves original name."""
        item = Node(
            id="m1",
            role="menu_item",
            name="Save\nAs",
            bounds=(0, 0, 100, 30),
            states=("enabled",),
            actions=("click",),
        )
        found = _find_node_by_name((item,), "Save As", _ITEM_ROLES)
        assert found is not None
        assert found.name == "Save\nAs"

    def test_menu_no_match(self) -> None:
        """Non-matching name returns None."""
        item = Node(
            id="m1",
            role="menu_item",
            name="Save\nAs",
            bounds=(0, 0, 100, 30),
            states=("enabled",),
            actions=("click",),
        )
        found = _find_node_by_name((item,), "Open", _ITEM_ROLES)
        assert found is None

    def test_menu_exact_match_still_works(self) -> None:
        """Normal exact match (no whitespace issues) still works."""
        item = Node(
            id="m1",
            role="menu_item",
            name="Save",
            bounds=(0, 0, 100, 30),
            states=("enabled",),
            actions=("click",),
        )
        found = _find_node_by_name((item,), "Save", _ITEM_ROLES)
        assert found is not None
        assert found.id == "m1"


# ═══════════════════════════════════════════════════════════════════════
# Defensive: raw_tree_to_domain None coercion (R-40-20, R-40-21)
# ═══════════════════════════════════════════════════════════════════════


class TestRawTreeNoneCoercion:
    """AC-D01, AC-D02: None name/role in raw input coerced to empty string."""

    def test_ac_d01_null_name(self) -> None:
        """AC-D01: raw node with name=None produces Node with name=''."""
        raw = {
            "tree": [
                {
                    "id": "n1",
                    "name": None,
                    "role": "button",
                    "bounds": [0, 0, 1, 1],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 1
        assert tree.roots[0].name == ""

    def test_ac_d02_null_role(self) -> None:
        """AC-D02: raw node with role=None produces Node with role=''."""
        raw = {
            "tree": [
                {
                    "id": "n1",
                    "name": "hello",
                    "role": None,
                    "bounds": [0, 0, 1, 1],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 1
        assert tree.roots[0].role == ""

    def test_missing_name_key_defaults_to_empty(self) -> None:
        """Missing 'name' key defaults to empty string."""
        raw = {
            "tree": [
                {
                    "id": "n1",
                    "role": "button",
                    "bounds": [0, 0, 1, 1],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 1
        assert tree.roots[0].name == ""

    def test_missing_role_key_defaults_to_empty(self) -> None:
        """Missing 'role' key defaults to empty string."""
        raw = {
            "tree": [
                {
                    "id": "n1",
                    "name": "hello",
                    "bounds": [0, 0, 1, 1],
                    "states": [],
                    "actions": [],
                }
            ]
        }
        tree = raw_tree_to_domain(raw)
        assert len(tree.roots) == 1
        assert tree.roots[0].role == ""
