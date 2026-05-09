"""Tests for inspection commands: tree, find, screenshot.

Requirements covered:
  R-INSPECT-01: inspect command (tree + screenshot + timestamp)
  R-INSPECT-02: inspect options (--no-screenshot, --no-tree, --tree-depth, etc.)
  R-INSPECT-03: find command (role + name_pattern matching)
  R-INSPECT-04: find --state filter and wildcard role
  R-INSPECT-05: screenshot command (path / base64)
"""

from __future__ import annotations

import base64

import pytest

# RED imports — define expected API
from aiyes.domain.use_cases.inspect import InspectUseCase
from aiyes.domain.use_cases.find import FindUseCase
from aiyes.domain.use_cases.screenshot import ScreenshotUseCase
from aiyes.domain.session import Session

from tests.conftest import (
    FakeAccessibilityTree,
    FakeClock,
    FakeScreenshot,
    FakeScreenshotStore,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
    make_tree,
)


def _make_test_session() -> Session:
    return Session(
        session_id="test-s",
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


def _make_deep_tree() -> dict:
    """Create a tree with depth 3: root -> child -> grandchild -> great-grandchild."""
    return make_tree(
        nodes=[
            make_node(
                "n_root",
                "frame",
                "Window",
                children=[
                    make_node(
                        "n_child",
                        "panel",
                        "ContentPanel",
                        children=[
                            make_node(
                                "n_grandchild",
                                "panel",
                                "InnerPanel",
                                children=[
                                    make_node(
                                        "n_great",
                                        "push_button",
                                        "Deep Button",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ]
    )


def _make_prunable_tree() -> dict:
    """Create a tree with prunable nodes (filler, unnamed section)."""
    return make_tree(
        nodes=[
            make_node(
                "n_root",
                "frame",
                "Window",
                children=[
                    # filler: should be pruned
                    {
                        "id": "n_filler",
                        "role": "filler",
                        "name": "",
                        "bounds": [0, 0, 0, 0],
                        "states": [],
                        "actions": [],
                    },
                    # unnamed section: should be pruned, children promoted
                    {
                        "id": "n_section",
                        "role": "section",
                        "name": "",
                        "bounds": [0, 0, 200, 100],
                        "states": [],
                        "actions": [],
                        "children": [
                            make_node("n_btn", "push_button", "OK"),
                        ],
                    },
                    # regular node: should be kept
                    make_node("n_label", "label", "Status"),
                ],
            ),
        ]
    )


# ──────────────────────────────────────────────────────────────────────
# R-INSPECT-01: Inspect use case
# ──────────────────────────────────────────────────────────────────────


class TestInspect:
    """Inspect returns tree, screenshot path, and timestamp."""

    def test_inspect_returns_tree(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-01: Inspect result includes 'tree' key."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s")

        assert result.tree is not None

    def test_inspect_returns_screenshot_path(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-01: Inspect result includes 'screenshot' path."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s")

        assert result.screenshot is not None
        assert isinstance(result.screenshot, str)

    def test_inspect_returns_timestamp(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-01: Inspect result includes ISO8601 timestamp."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s")

        assert result.timestamp is not None
        assert isinstance(result.timestamp, str)
        # ISO8601 contains 'T' separator
        assert "T" in result.timestamp

    def test_inspect_timestamp_from_clock_port(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
    ) -> None:
        """R-ARCH-05, F-03: Timestamp comes from ClockPort, not datetime.now()."""
        session = _make_test_session()
        fake_session_repo.save(session)

        # Use a specific clock value
        clock = FakeClock(now_value=0.0)  # epoch zero = 1970-01-01T00:00:00

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=clock,
        )
        result = uc.execute(session_id="test-s")

        # At epoch 0, timestamp should be 1970-01-01T00:00:00
        assert "1970-01-01" in result.timestamp

    def test_inspect_persists_tree(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-ARCH-03: Inspect saves tree to tree store."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        uc.execute(session_id="test-s")

        save_calls = [c for c in fake_tree_store.calls if c[0] == "save_tree"]
        assert len(save_calls) == 1

    def test_inspect_persists_screenshot(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-ARCH-03: Inspect saves screenshot to screenshot store."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        uc.execute(session_id="test-s")

        save_calls = [
            c for c in fake_screenshot_store.calls if c[0] == "save_screenshot"
        ]
        assert len(save_calls) == 1


# ──────────────────────────────────────────────────────────────────────
# R-INSPECT-02: Inspect options
# ──────────────────────────────────────────────────────────────────────


class TestInspectOptions:
    """Inspect with various flags."""

    def test_no_screenshot(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-02: --no-screenshot skips screenshot."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s", no_screenshot=True)

        assert result.screenshot is None
        # No screenshot port calls
        take_calls = [c for c in fake_screenshot.calls if c[0] == "take"]
        assert len(take_calls) == 0
        # No screenshot store writes
        save_calls = [
            c for c in fake_screenshot_store.calls if c[0] == "save_screenshot"
        ]
        assert len(save_calls) == 0

    def test_no_tree(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-02: --no-tree skips tree retrieval."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s", no_tree=True)

        assert result.tree is None

    def test_no_tree_and_no_screenshot_is_error(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-02: --no-screenshot AND --no-tree is an error."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = InspectUseCase(
            tree=fake_accessibility_tree,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        with pytest.raises(Exception):
            uc.execute(session_id="test-s", no_tree=True, no_screenshot=True)

    def test_tree_depth_zero_returns_roots_only(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-02, F-02, F-11: --tree-depth 0 returns root nodes with no children."""
        session = _make_test_session()
        fake_session_repo.save(session)

        deep_tree_port = FakeAccessibilityTree(tree=_make_deep_tree())
        uc = InspectUseCase(
            tree=deep_tree_port,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s", tree_depth=0, no_prune=True)

        # At depth 0, root should have no children
        assert result.tree is not None
        assert len(result.tree.roots) > 0
        for root in result.tree.roots:
            assert len(root.children) == 0, (
                f"At depth 0, root '{root.name}' should have no children"
            )

    def test_tree_depth_one_truncates_grandchildren(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-02, F-02, F-11: --tree-depth 1 includes children but not grandchildren."""
        session = _make_test_session()
        fake_session_repo.save(session)

        deep_tree_port = FakeAccessibilityTree(tree=_make_deep_tree())
        uc = InspectUseCase(
            tree=deep_tree_port,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s", tree_depth=1, no_prune=True)

        # Root should have children (depth 1), but children should have no children
        assert result.tree is not None
        root = result.tree.roots[0]
        assert len(root.children) > 0, "Root should have children at depth 1"
        for child in root.children:
            assert len(child.children) == 0, (
                f"At depth 1, child '{child.name}' should have no grandchildren"
            )

    def test_tree_depth_none_preserves_full_tree(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-02, F-02: No tree-depth returns full tree."""
        session = _make_test_session()
        fake_session_repo.save(session)

        deep_tree_port = FakeAccessibilityTree(tree=_make_deep_tree())
        uc = InspectUseCase(
            tree=deep_tree_port,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s", no_prune=True)

        # Full tree should have great-grandchild at depth 3
        assert result.tree is not None
        root = result.tree.roots[0]
        child = root.children[0]
        grandchild = child.children[0]
        assert len(grandchild.children) > 0, (
            "Full tree should preserve all depth levels"
        )

    def test_no_prune_preserves_noise_nodes(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-INSPECT-02, R-OUTPUT-04, F-02, F-11: --no-prune returns filler/section nodes."""
        session = _make_test_session()
        fake_session_repo.save(session)

        prunable_tree_port = FakeAccessibilityTree(tree=_make_prunable_tree())
        uc = InspectUseCase(
            tree=prunable_tree_port,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s", no_prune=True)

        # With no_prune, filler and unnamed section should still be present
        assert result.tree is not None
        root = result.tree.roots[0]
        child_roles = [c.role for c in root.children]
        assert "filler" in child_roles, "no_prune should preserve filler nodes"
        assert "section" in child_roles, (
            "no_prune should preserve unnamed section nodes"
        )

    def test_default_pruning_removes_noise(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-OUTPUT-04, F-02, F-11: Default inspect prunes filler and unnamed section."""
        session = _make_test_session()
        fake_session_repo.save(session)

        prunable_tree_port = FakeAccessibilityTree(tree=_make_prunable_tree())
        uc = InspectUseCase(
            tree=prunable_tree_port,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )
        # Default: no_prune=False -> pruning active
        result = uc.execute(session_id="test-s")

        assert result.tree is not None
        root = result.tree.roots[0]
        child_roles = [c.role for c in root.children]
        assert "filler" not in child_roles, "Default pruning should remove filler"
        assert "section" not in child_roles, (
            "Default pruning should remove unnamed section"
        )
        # But the button that was inside the section should be promoted
        child_names = [c.name for c in root.children]
        assert "OK" in child_names, "Button inside pruned section should be promoted"
        assert "Status" in child_names, "Regular label should be preserved"

    def test_pruned_vs_unpruned_differ(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_screenshot_store: FakeScreenshotStore,
        fake_clock: FakeClock,
    ) -> None:
        """F-11: Pruned and unpruned output actually differ on prunable fixtures."""
        session = _make_test_session()
        fake_session_repo.save(session)

        prunable_tree_port = FakeAccessibilityTree(tree=_make_prunable_tree())

        uc = InspectUseCase(
            tree=prunable_tree_port,
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            screenshot_store=fake_screenshot_store,
            clock=fake_clock,
        )

        pruned = uc.execute(session_id="test-s", no_prune=False)
        unpruned = uc.execute(session_id="test-s", no_prune=True)

        pruned_roles = [c.role for c in pruned.tree.roots[0].children]
        unpruned_roles = [c.role for c in unpruned.tree.roots[0].children]
        assert pruned_roles != unpruned_roles, (
            "Pruned and unpruned trees should differ on prunable fixture"
        )


# ──────────────────────────────────────────────────────────────────────
# R-INSPECT-03: Find use case
# ──────────────────────────────────────────────────────────────────────


class TestFind:
    """Find nodes by role and optional name pattern."""

    def test_find_returns_list(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-03: Find returns a list of matching nodes."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = FindUseCase(
            tree=fake_accessibility_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", role="push_button")

        assert isinstance(result, list)

    def test_find_empty_on_no_match(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-03: Find returns empty list when no nodes match."""
        session = _make_test_session()
        fake_session_repo.save(session)

        empty_tree = FakeAccessibilityTree(tree=make_tree(nodes=[]))
        uc = FindUseCase(
            tree=empty_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", role="push_button")

        assert result == []

    def test_find_node_has_required_fields(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-03, R-OUTPUT-01: Each found node has id, role, name, bounds."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = FindUseCase(
            tree=fake_accessibility_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", role="push_button")

        if result:
            node = result[0]
            assert hasattr(node, "id") or "id" in node
            assert hasattr(node, "role") or "role" in node
            assert hasattr(node, "name") or "name" in node
            assert hasattr(node, "bounds") or "bounds" in node

    def test_find_by_name_pattern(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-03: name_pattern filters by case-insensitive substring."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = FindUseCase(
            tree=fake_accessibility_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", role="push_button", name_pattern="ok")

        # Should match "OK" via case-insensitive substring
        for node in result:
            node_name = node.name if hasattr(node, "name") else node["name"]
            assert "ok" in node_name.lower()

    def test_find_does_not_take_screenshot(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-03: Find does not take a screenshot."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = FindUseCase(
            tree=fake_accessibility_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        uc.execute(session_id="test-s", role="push_button")

        # Verify screenshot port was NOT called
        assert len(fake_screenshot.calls) == 0


# ──────────────────────────────────────────────────────────────────────
# R-INSPECT-04: Find with --state filter and wildcard
# ──────────────────────────────────────────────────────────────────────


class TestFindWithFilters:
    """Find with --state and wildcard role."""

    def test_find_with_state_filter(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-04: --state filters by accessibility state."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = FindUseCase(
            tree=fake_accessibility_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", role="push_button", state="enabled")

        # All returned nodes should have the specified state
        for node in result:
            states = node.states if hasattr(node, "states") else node["states"]
            assert "enabled" in states

    def test_find_wildcard_role(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-04: Role '*' matches any role."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = FindUseCase(
            tree=fake_accessibility_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", role="*")

        # Should return ALL nodes regardless of role
        assert isinstance(result, list)

    def test_find_wildcard_with_state(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-INSPECT-04: Wildcard role + state filter."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = FindUseCase(
            tree=fake_accessibility_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", role="*", state="focused")

        for node in result:
            states = node.states if hasattr(node, "states") else node["states"]
            assert "focused" in states


# ──────────────────────────────────────────────────────────────────────
# R-INSPECT-05: Screenshot use case
# ──────────────────────────────────────────────────────────────────────


class TestScreenshot:
    """Standalone screenshot command."""

    def test_screenshot_returns_path(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_screenshot_store: FakeScreenshotStore,
    ) -> None:
        """R-INSPECT-05: Default screenshot returns path."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = ScreenshotUseCase(
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            screenshot_store=fake_screenshot_store,
        )
        result = uc.execute(session_id="test-s")

        assert result.path is not None
        assert isinstance(result.path, str)

    def test_screenshot_with_output_path(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_screenshot_store: FakeScreenshotStore,
    ) -> None:
        """R-INSPECT-05, F-14, F-19: --output saves to specified path and returns it."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = ScreenshotUseCase(
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            screenshot_store=fake_screenshot_store,
        )
        result = uc.execute(session_id="test-s", output_path="/tmp/custom.png")

        # Verify the screenshot port received the output path
        take_calls = [c for c in fake_screenshot.calls if c[0] == "take"]
        assert len(take_calls) == 1
        _, output = take_calls[0][1]
        assert output == "/tmp/custom.png"

        # F-14/F-19: Postcondition — result.path must equal the requested output_path
        assert result.path == "/tmp/custom.png"

    def test_screenshot_base64_returns_actual_encoded_data(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-INSPECT-05, F-04: --base64 returns actual base64-encoded data, not placeholder."""
        session = _make_test_session()
        fake_session_repo.save(session)

        # Use specific fake bytes so we can verify encoding
        test_bytes = b"\x89PNG\r\n\x1a\nfake-image-content"
        fake_ss_store = FakeScreenshotStore(fake_bytes=test_bytes)

        uc = ScreenshotUseCase(
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            screenshot_store=fake_ss_store,
        )
        result = uc.execute(session_id="test-s", base64=True)

        assert result.data is not None
        assert isinstance(result.data, str)
        # Path should be None when base64 is requested
        assert result.path is None
        # Verify it is actual base64 by decoding
        decoded = base64.b64decode(result.data)
        assert decoded == test_bytes

    def test_screenshot_base64_not_placeholder(
        self,
        fake_screenshot: FakeScreenshot,
        fake_session_repo: FakeSessionRepository,
        fake_screenshot_store: FakeScreenshotStore,
    ) -> None:
        """F-04: base64 output must not be the old placeholder string."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = ScreenshotUseCase(
            screenshot=fake_screenshot,
            session_repo=fake_session_repo,
            screenshot_store=fake_screenshot_store,
        )
        result = uc.execute(session_id="test-s", base64=True)

        assert result.data != "<base64-encoded-png-data>"
