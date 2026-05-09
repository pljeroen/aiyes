"""AIYES-25 Group B — Inspection Enrichment: tests.

Tests for GAP-05 (element context), GAP-06 (transient wait),
GAP-09 (app status), GAP-10 (dialog detection).

Traceability:
  REQ-B01..B05: Element context info (GAP-05)
  REQ-B06..B10: Transient wait (GAP-06)
  REQ-B11..B15: App status (GAP-09)
  REQ-B16..B20: Dialog detection (GAP-10)
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
from typing import Any, List, Optional, Tuple
from unittest.mock import patch

from click.testing import CliRunner

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node, raw_tree_to_domain

from tests.conftest import (
    FakeAccessibilityTree,
    FakeClock,
    FakeProcess,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
    make_tree,
)


# ═══════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="test-s",
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
        started_at=1000.0,
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_android_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="android-test",
        app_pid=200,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# GAP-05: Element Context Info
# ═══════════════════════════════════════════════════════════════════════


class TestNodeContextFields:
    """REQ-B01: Node dataclass gains five optional context fields."""

    def test_node_default_context_fields_are_none(self) -> None:
        """REQ-B01: New fields default to None for backward compatibility."""
        node = Node(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=("enabled",),
            actions=("click",),
        )
        assert node.parent_role is None
        assert node.parent_name is None
        assert node.index_in_parent is None
        assert node.depth is None
        assert node.sibling_count is None

    def test_node_with_context_fields_set(self) -> None:
        """REQ-B01: Node with all five context fields set."""
        node = Node(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=("enabled",),
            actions=("click",),
            parent_role="frame",
            parent_name="Window",
            index_in_parent=0,
            depth=1,
            sibling_count=2,
        )
        assert node.parent_role == "frame"
        assert node.parent_name == "Window"
        assert node.index_in_parent == 0
        assert node.depth == 1
        assert node.sibling_count == 2

    def test_node_still_frozen(self) -> None:
        """REQ-B01: Node is still a frozen dataclass after field addition."""
        node = Node(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=(),
            actions=(),
            depth=1,
        )
        assert dataclasses.is_dataclass(node)
        try:
            node.depth = 5  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except (dataclasses.FrozenInstanceError, AttributeError):
            pass

    def test_existing_node_creation_still_works(self) -> None:
        """REQ-B01: Existing Node(...) creation without context fields still works."""
        node = Node(
            id="n1",
            role="button",
            name="OK",
            bounds=[0, 0, 80, 30],
            states=["enabled"],
            actions=["click"],
            children=[],
            value="val",
        )
        assert node.id == "n1"
        assert node.value == "val"


class TestEnrichTree:
    """REQ-B02: enrich_tree function computes context fields in a single pass."""

    def test_enrich_tree_exists(self) -> None:
        """REQ-B02: enrich_tree function exists in tree module."""
        from aiyes.domain.tree import enrich_tree

        assert callable(enrich_tree)

    def test_root_node_depth_zero(self) -> None:
        """REQ-B02: Root nodes get depth=0."""
        from aiyes.domain.tree import enrich_tree

        root = Node(
            id="r1",
            role="frame",
            name="Window",
            bounds=(0, 0, 100, 100),
            states=(),
            actions=(),
        )
        tree = AccessibilityTree(roots=(root,))
        enriched = enrich_tree(tree)
        assert enriched.roots[0].depth == 0

    def test_root_node_parent_fields_none(self) -> None:
        """REQ-B02: Root nodes have parent_role=None, parent_name=None."""
        from aiyes.domain.tree import enrich_tree

        root = Node(
            id="r1",
            role="frame",
            name="Window",
            bounds=(0, 0, 100, 100),
            states=(),
            actions=(),
        )
        tree = AccessibilityTree(roots=(root,))
        enriched = enrich_tree(tree)
        assert enriched.roots[0].parent_role is None
        assert enriched.roots[0].parent_name is None

    def test_child_node_has_parent_info(self) -> None:
        """REQ-B02: Child node gets parent's role and name."""
        from aiyes.domain.tree import enrich_tree

        child = Node(
            id="c1",
            role="button",
            name="OK",
            bounds=(10, 10, 80, 30),
            states=(),
            actions=(),
        )
        root = Node(
            id="r1",
            role="frame",
            name="Window",
            bounds=(0, 0, 100, 100),
            states=(),
            actions=(),
            children=(child,),
        )
        tree = AccessibilityTree(roots=(root,))
        enriched = enrich_tree(tree)
        enriched_child = enriched.roots[0].children[0]
        assert enriched_child.parent_role == "frame"
        assert enriched_child.parent_name == "Window"
        assert enriched_child.depth == 1

    def test_index_in_parent(self) -> None:
        """REQ-B02: index_in_parent is 0-based index among siblings."""
        from aiyes.domain.tree import enrich_tree

        c1 = Node(
            id="c1", role="button", name="A", bounds=(0, 0, 0, 0), states=(), actions=()
        )
        c2 = Node(
            id="c2", role="button", name="B", bounds=(0, 0, 0, 0), states=(), actions=()
        )
        root = Node(
            id="r1",
            role="frame",
            name="Win",
            bounds=(0, 0, 100, 100),
            states=(),
            actions=(),
            children=(c1, c2),
        )
        tree = AccessibilityTree(roots=(root,))
        enriched = enrich_tree(tree)
        assert enriched.roots[0].children[0].index_in_parent == 0
        assert enriched.roots[0].children[1].index_in_parent == 1

    def test_sibling_count(self) -> None:
        """REQ-B02: sibling_count is total number of siblings (len(parent.children))."""
        from aiyes.domain.tree import enrich_tree

        c1 = Node(
            id="c1", role="button", name="A", bounds=(0, 0, 0, 0), states=(), actions=()
        )
        c2 = Node(
            id="c2", role="button", name="B", bounds=(0, 0, 0, 0), states=(), actions=()
        )
        c3 = Node(
            id="c3", role="button", name="C", bounds=(0, 0, 0, 0), states=(), actions=()
        )
        root = Node(
            id="r1",
            role="frame",
            name="Win",
            bounds=(0, 0, 100, 100),
            states=(),
            actions=(),
            children=(c1, c2, c3),
        )
        tree = AccessibilityTree(roots=(root,))
        enriched = enrich_tree(tree)
        for child in enriched.roots[0].children:
            assert child.sibling_count == 3

    def test_enrich_tree_idempotent(self) -> None:
        """REQ-B02: Calling enrich_tree twice yields same result."""
        from aiyes.domain.tree import enrich_tree

        child = Node(
            id="c1",
            role="button",
            name="OK",
            bounds=(10, 10, 80, 30),
            states=(),
            actions=(),
        )
        root = Node(
            id="r1",
            role="frame",
            name="Window",
            bounds=(0, 0, 100, 100),
            states=(),
            actions=(),
            children=(child,),
        )
        tree = AccessibilityTree(roots=(root,))
        enriched_once = enrich_tree(tree)
        enriched_twice = enrich_tree(enriched_once)
        assert enriched_once == enriched_twice

    def test_deep_nesting(self) -> None:
        """REQ-B02: Deep nesting sets depth correctly."""
        from aiyes.domain.tree import enrich_tree

        leaf = Node(
            id="l1",
            role="label",
            name="Text",
            bounds=(0, 0, 0, 0),
            states=(),
            actions=(),
        )
        mid = Node(
            id="m1",
            role="panel",
            name="P",
            bounds=(0, 0, 0, 0),
            states=(),
            actions=(),
            children=(leaf,),
        )
        root = Node(
            id="r1",
            role="frame",
            name="W",
            bounds=(0, 0, 0, 0),
            states=(),
            actions=(),
            children=(mid,),
        )
        tree = AccessibilityTree(roots=(root,))
        enriched = enrich_tree(tree)
        assert enriched.roots[0].depth == 0
        assert enriched.roots[0].children[0].depth == 1
        assert enriched.roots[0].children[0].children[0].depth == 2

    def test_multiple_roots(self) -> None:
        """REQ-B02: Multiple roots each get depth=0 with correct sibling_count."""
        from aiyes.domain.tree import enrich_tree

        r1 = Node(
            id="r1", role="frame", name="W1", bounds=(0, 0, 0, 0), states=(), actions=()
        )
        r2 = Node(
            id="r2", role="frame", name="W2", bounds=(0, 0, 0, 0), states=(), actions=()
        )
        tree = AccessibilityTree(roots=(r1, r2))
        enriched = enrich_tree(tree)
        # Root nodes have no parent — sibling_count should reflect root-level count
        assert enriched.roots[0].depth == 0
        assert enriched.roots[1].depth == 0
        assert enriched.roots[0].index_in_parent == 0
        assert enriched.roots[1].index_in_parent == 1
        assert enriched.roots[0].sibling_count == 2
        assert enriched.roots[1].sibling_count == 2

    def test_empty_tree(self) -> None:
        """REQ-B02: Empty tree enrichment returns empty tree."""
        from aiyes.domain.tree import enrich_tree

        tree = AccessibilityTree(roots=())
        enriched = enrich_tree(tree)
        assert enriched.roots == ()


class TestFoundNodeContextFields:
    """REQ-B03: FoundNode gains context fields from Node."""

    def test_found_node_has_context_fields(self) -> None:
        """REQ-B03: FoundNode has the five optional context fields."""
        from aiyes.domain.use_cases.find import FoundNode

        fn = FoundNode(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=(),
            actions=(),
            parent_role="frame",
            parent_name="Win",
            index_in_parent=0,
            depth=1,
            sibling_count=2,
        )
        assert fn.parent_role == "frame"
        assert fn.depth == 1

    def test_found_node_defaults_none(self) -> None:
        """REQ-B03: FoundNode context fields default to None."""
        from aiyes.domain.use_cases.find import FoundNode

        fn = FoundNode(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=(),
            actions=(),
        )
        assert fn.parent_role is None
        assert fn.parent_name is None
        assert fn.index_in_parent is None
        assert fn.depth is None
        assert fn.sibling_count is None

    def test_find_use_case_maps_context_fields(self) -> None:
        """REQ-B03: FindUseCase maps Node context fields to FoundNode."""
        from aiyes.domain.use_cases.find import FindUseCase

        # Build a tree with context-enrichable structure
        child = make_node("n_002", "push_button", "OK")
        root = make_node("n_001", "frame", "Window", children=[child])
        tree_port = FakeAccessibilityTree(tree=make_tree(nodes=[root]))
        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        uc = FindUseCase(tree=tree_port, session_repo=repo, tree_store=tree_store)
        results = uc.execute(session_id="test-s", role="push_button", name_pattern="OK")

        assert len(results) >= 1
        found = results[0]
        # After enrichment, the found node should have context
        assert found.depth is not None
        assert found.parent_role is not None


class TestInspectTreeEnrichment:
    """REQ-B04: InspectUseCase returns enriched tree."""

    def test_inspect_returns_enriched_nodes(self) -> None:
        """REQ-B04: InspectUseCase calls enrich_tree after tree retrieval."""
        from aiyes.domain.use_cases.inspect import InspectUseCase

        child = make_node("n_002", "push_button", "OK")
        root = make_node("n_001", "frame", "Window", children=[child])
        tree_port = FakeAccessibilityTree(tree=make_tree(nodes=[root]))
        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        from tests.conftest import FakeClock, FakeScreenshot, FakeScreenshotStore

        uc = InspectUseCase(
            tree=tree_port,
            screenshot=FakeScreenshot(),
            session_repo=repo,
            tree_store=tree_store,
            screenshot_store=FakeScreenshotStore(),
            clock=FakeClock(),
        )
        result = uc.execute(session_id="test-s", no_screenshot=True)

        # The tree should have enriched nodes
        assert result.tree is not None
        root_node = result.tree.roots[0]
        assert root_node.depth == 0
        if root_node.children:
            child_node = root_node.children[0]
            assert child_node.depth is not None
            assert child_node.depth > 0


class TestContextFieldsSerialization:
    """REQ-B04: Serialized tree JSON contains context fields when non-None."""

    def test_node_to_dict_includes_context_fields(self) -> None:
        """REQ-B04: node_to_dict includes context fields when non-None."""
        from aiyes.domain.output_formatter import node_to_dict

        node = Node(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=(),
            actions=(),
            parent_role="frame",
            parent_name="Win",
            index_in_parent=0,
            depth=1,
            sibling_count=2,
        )
        d = node_to_dict(node)
        assert d["parent_role"] == "frame"
        assert d["parent_name"] == "Win"
        assert d["index_in_parent"] == 0
        assert d["depth"] == 1
        assert d["sibling_count"] == 2

    def test_node_to_dict_omits_none_context_fields(self) -> None:
        """REQ-B04: node_to_dict omits context fields when None (compactness)."""
        from aiyes.domain.output_formatter import node_to_dict

        node = Node(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=(),
            actions=(),
        )
        d = node_to_dict(node)
        assert "parent_role" not in d
        assert "parent_name" not in d
        assert "index_in_parent" not in d
        assert "depth" not in d
        assert "sibling_count" not in d

    def test_format_find_nodes_includes_context(self) -> None:
        """REQ-B03: format_find_nodes renders context fields when non-None."""
        from aiyes.domain.use_cases.find import FoundNode
        from aiyes.cli.presenter import format_find_nodes

        fn = FoundNode(
            id="n1",
            role="button",
            name="OK",
            bounds=(0, 0, 80, 30),
            states=(),
            actions=(),
            parent_role="frame",
            depth=1,
            sibling_count=2,
            index_in_parent=0,
            parent_name="Win",
        )
        output = format_find_nodes([fn])
        parsed = json.loads(output)
        assert parsed[0]["parent_role"] == "frame"
        assert parsed[0]["depth"] == 1


class TestContextFieldsDomainPurity:
    """REQ-B05: Context fields are domain-layer — stdlib only."""

    def test_tree_py_imports_stdlib_only(self) -> None:
        """REQ-B05: tree.py uses only stdlib and domain/ports imports."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)
        source = Path("src/aiyes/domain/tree.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules or top == "__future__":
                    continue
                assert node.module.startswith(
                    "aiyes.domain."
                ) or node.module.startswith("aiyes.ports."), (
                    f"tree.py has disallowed import: {node.module}"
                )

    def test_find_py_imports_stdlib_only(self) -> None:
        """REQ-B05: find.py uses only stdlib, domain, and ports imports."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)
        source = Path("src/aiyes/domain/use_cases/find.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules or top == "__future__":
                    continue
                assert node.module.startswith(
                    "aiyes.domain."
                ) or node.module.startswith("aiyes.ports."), (
                    f"find.py has disallowed import: {node.module}"
                )


# ═══════════════════════════════════════════════════════════════════════
# GAP-06: Transient Wait
# ═══════════════════════════════════════════════════════════════════════


class SequenceFakeTree:
    """Fake AccessibilityTreePort that returns different trees per call."""

    def __init__(self, trees: List[AccessibilityTree]) -> None:
        self._trees = trees
        self._call_index = 0
        self.calls: List[Tuple[str, Any]] = []

    def get_tree(self, session) -> AccessibilityTree:
        self.calls.append(("get_tree", session))
        tree = self._trees[min(self._call_index, len(self._trees) - 1)]
        self._call_index += 1
        return tree


class TestWaitResultTransientField:
    """REQ-B08: WaitResult gains transient field."""

    def test_wait_result_has_transient_field(self) -> None:
        """REQ-B08: WaitResult has a transient bool field defaulting to False."""
        from aiyes.domain.use_cases.wait import WaitResult

        r = WaitResult(found=True)
        assert r.transient is False

    def test_wait_result_transient_true(self) -> None:
        """REQ-B08: WaitResult with transient=True."""
        from aiyes.domain.use_cases.wait import WaitResult

        r = WaitResult(found=True, transient=True, id="n_001")
        assert r.transient is True
        assert r.id == "n_001"

    def test_wait_result_backward_compat(self) -> None:
        """REQ-B08: Existing WaitResult creation still works."""
        from aiyes.domain.use_cases.wait import WaitResult

        r = WaitResult(found=False, timeout=True)
        assert r.transient is False


class TestTransientPollInterval:
    """REQ-B07: Transient mode polls at 200ms interval."""

    def test_transient_poll_interval_constant(self) -> None:
        """REQ-B07: WaitUseCase.TRANSIENT_POLL_INTERVAL = 0.2."""
        from aiyes.domain.use_cases.wait import WaitUseCase

        assert WaitUseCase.TRANSIENT_POLL_INTERVAL == 0.2

    def test_transient_mode_uses_200ms_sleep(self) -> None:
        """REQ-B07: In transient mode, clock.sleep is called with 0.2."""
        from aiyes.domain.use_cases.wait import WaitUseCase

        # Tree with no matching nodes -> will timeout
        empty_tree = raw_tree_to_domain(make_tree(nodes=[]))
        tree_port = FakeAccessibilityTree(tree=empty_tree)
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = WaitUseCase(
            tree=tree_port, session_repo=repo, tree_store=FakeTreeStore(), clock=clock
        )
        uc.execute(session_id="test-s", role="push_button", timeout=0.5, transient=True)

        # All sleep calls should be 0.2
        assert len(clock.sleep_calls) > 0
        for s in clock.sleep_calls:
            assert s == 0.2


class TestTransientMutualExclusion:
    """REQ-B06: transient and absent are mutually exclusive."""

    def test_transient_and_absent_raises(self) -> None:
        """REQ-B06: transient=True + absent=True raises ValueError."""
        from aiyes.domain.use_cases.wait import WaitUseCase

        tree_port = FakeAccessibilityTree(tree=raw_tree_to_domain(make_tree(nodes=[])))
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = WaitUseCase(
            tree=tree_port, session_repo=repo, tree_store=FakeTreeStore(), clock=clock
        )
        try:
            uc.execute(session_id="test-s", role="button", transient=True, absent=True)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


class TestTransientNodeAppearedThenGone:
    """REQ-B08: Node appeared at some point but is gone at timeout."""

    def test_returns_found_true_transient_true(self) -> None:
        """REQ-B08: If node was seen but is now gone, found=True transient=True."""
        from aiyes.domain.use_cases.wait import WaitUseCase

        # Polls: empty, has_button, empty, empty (timeout)
        tree_empty = raw_tree_to_domain(make_tree(nodes=[]))
        tree_with = raw_tree_to_domain(make_tree())  # has push_button "OK"
        seq = SequenceFakeTree([tree_empty, tree_with, tree_empty, tree_empty])

        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = WaitUseCase(
            tree=seq, session_repo=repo, tree_store=FakeTreeStore(), clock=clock
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=0.8,
            transient=True,
        )

        assert result.found is True
        assert result.transient is True
        assert result.id is not None

    def test_seen_ids_accumulated(self) -> None:
        """REQ-B07: seen_ids accumulates across polls."""
        from aiyes.domain.use_cases.wait import WaitUseCase

        tree_empty = raw_tree_to_domain(make_tree(nodes=[]))
        tree_with = raw_tree_to_domain(make_tree())
        # Node appears on poll 2, gone on poll 3+
        seq = SequenceFakeTree([tree_empty, tree_with, tree_empty, tree_empty])

        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = WaitUseCase(
            tree=seq, session_repo=repo, tree_store=FakeTreeStore(), clock=clock
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=0.8,
            transient=True,
        )

        # Even though node is gone, it was accumulated and result shows it
        assert result.found is True
        assert result.transient is True


class TestTransientNodeStillPresent:
    """REQ-B08: Node currently present at end of loop."""

    def test_returns_found_true_transient_false(self) -> None:
        """REQ-B08: If node is currently present, transient=False."""
        from aiyes.domain.use_cases.wait import WaitUseCase

        # Node present on every poll
        tree_with = raw_tree_to_domain(make_tree())
        tree_port = FakeAccessibilityTree(tree=tree_with)

        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = WaitUseCase(
            tree=tree_port, session_repo=repo, tree_store=FakeTreeStore(), clock=clock
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=0.5,
            transient=True,
        )

        assert result.found is True
        assert result.transient is False
        assert result.id is not None


class TestTransientNodeNeverAppeared:
    """REQ-B08: No node ever appeared, timeout."""

    def test_returns_found_false_transient_false(self) -> None:
        """REQ-B08: If no node ever appeared and timeout, found=False."""
        from aiyes.domain.use_cases.wait import WaitUseCase

        tree_empty = raw_tree_to_domain(make_tree(nodes=[]))
        tree_port = FakeAccessibilityTree(tree=tree_empty)

        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = WaitUseCase(
            tree=tree_port, session_repo=repo, tree_store=FakeTreeStore(), clock=clock
        )
        result = uc.execute(
            session_id="test-s", role="push_button", timeout=0.5, transient=True
        )

        assert result.found is False
        assert result.timeout is True
        assert result.transient is False


class TestTransientDomainPurity:
    """REQ-B09: WaitUseCase imports no adapters."""

    def test_wait_py_no_adapter_imports(self) -> None:
        """REQ-B09: wait.py has no import from adapters."""

        source = Path("src/aiyes/domain/use_cases/wait.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aiyes.adapters."), (
                    f"wait.py has disallowed adapter import: {node.module}"
                )


class TestTransientPresenter:
    """REQ-B10: format_wait renders transient field."""

    def test_format_wait_with_transient_true(self) -> None:
        """REQ-B10: format_wait includes transient=true when set."""
        from aiyes.cli.presenter import format_wait

        output = format_wait(found=True, node_id="n_001", transient=True)
        parsed = json.loads(output)
        assert parsed["transient"] is True

    def test_format_wait_without_transient(self) -> None:
        """REQ-B10: format_wait omits transient when False (backward compat)."""
        from aiyes.cli.presenter import format_wait

        output = format_wait(found=True, node_id="n_001")
        parsed = json.loads(output)
        # transient should not appear when False
        assert "transient" not in parsed

    def test_format_wait_existing_output_unchanged(self) -> None:
        """REQ-B10: Existing non-transient output is unchanged."""
        from aiyes.cli.presenter import format_wait

        output = format_wait(found=True, timeout=False, node_id="n_001")
        parsed = json.loads(output)
        assert parsed["found"] is True
        assert parsed["id"] == "n_001"
        assert parsed["timeout"] is False


class TestTransientCli:
    """REQ-B06: CLI --transient flag."""

    def test_transient_flag_in_help(self) -> None:
        """REQ-B06: --transient appears in wait --help."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["wait", "--help"])
        assert result.exit_code == 0
        assert "--transient" in result.output

    def test_transient_flag_passed_to_use_case(self) -> None:
        """REQ-B06: --transient flag passes transient=True to execute()."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            from aiyes.domain.use_cases.wait import WaitResult

            mock_uc.execute.return_value = WaitResult(
                found=True,
                timeout=False,
                id="n_001",
                transient=True,
            )

            result = runner.invoke(
                cli,
                ["wait", "--session", "s1", "--transient", "push_button", "OK"],
            )

            assert result.exit_code == 0
            call_kwargs = mock_uc.execute.call_args
            assert call_kwargs is not None
            if call_kwargs.kwargs:
                assert call_kwargs.kwargs.get("transient") is True

    def test_transient_and_absent_cli_error(self) -> None:
        """REQ-B06: --transient and --absent together is an error."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            mock_uc.execute.side_effect = ValueError(
                "transient and absent are mutually exclusive"
            )

            result = runner.invoke(
                cli,
                [
                    "wait",
                    "--session",
                    "s1",
                    "--transient",
                    "--absent",
                    "push_button",
                    "OK",
                ],
            )
            # Should exit with error
            assert result.exit_code != 0

    def test_transient_output_json(self) -> None:
        """REQ-B10: CLI output includes transient=true when node was transient."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            from aiyes.domain.use_cases.wait import WaitResult

            mock_uc.execute.return_value = WaitResult(
                found=True,
                timeout=False,
                id="n_001",
                transient=True,
            )

            result = runner.invoke(
                cli,
                ["wait", "--session", "s1", "--transient", "push_button", "OK"],
            )

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["transient"] is True


# ═══════════════════════════════════════════════════════════════════════
# GAP-09: App Status
# ═══════════════════════════════════════════════════════════════════════


class TestSessionStatusResult:
    """REQ-B12: SessionStatusResult frozen dataclass."""

    def test_session_status_result_exists(self) -> None:
        """REQ-B12: SessionStatusResult is importable and has required fields."""
        from aiyes.domain.use_cases.session_status import SessionStatusResult

        r = SessionStatusResult(
            app_alive=True,
            app_foreground=True,
            display_alive=True,
        )
        assert r.app_alive is True
        assert r.app_foreground is True
        assert r.display_alive is True

    def test_session_status_result_frozen(self) -> None:
        """REQ-B12: SessionStatusResult is frozen."""
        from aiyes.domain.use_cases.session_status import SessionStatusResult

        r = SessionStatusResult(
            app_alive=True, app_foreground=False, display_alive=True
        )
        assert dataclasses.is_dataclass(r)
        try:
            r.app_alive = False  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except (dataclasses.FrozenInstanceError, AttributeError):
            pass


class TestSessionStatusUseCase:
    """REQ-B12, B13, B14, B15: SessionStatusUseCase."""

    def test_app_alive_true_when_running(self) -> None:
        """REQ-B12: app_alive=True when process.is_running(app_pid)."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = True  # app_pid
        process._running[99] = True  # xvfb_pid

        # Fake window query that returns no foreground info
        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return None

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="test-s")
        assert result.app_alive is True

    def test_app_alive_false_when_not_running(self) -> None:
        """REQ-B12: app_alive=False when process is not running."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = False
        process._running[99] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return None

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="test-s")
        assert result.app_alive is False

    def test_display_alive_linux(self) -> None:
        """REQ-B12: display_alive=True when xvfb_pid is running (Linux)."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = True
        process._running[99] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return None

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="test-s")
        assert result.display_alive is True

    def test_display_alive_android_always_true(self) -> None:
        """REQ-B12: display_alive=True for Android (no display process)."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[200] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return "com.example.app/.MainActivity"

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="android-test")
        assert result.display_alive is True

    def test_linux_foreground_via_window_query(self) -> None:
        """REQ-B13: Linux foreground check via xdotool window query."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = True
        process._running[99] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return "12345"

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return 100  # matches app_pid

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return None

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="test-s")
        assert result.app_foreground is True

    def test_linux_foreground_false_different_pid(self) -> None:
        """REQ-B13: Linux foreground=False when window pid doesn't match."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = True
        process._running[99] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return "12345"

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return 999  # different from app_pid

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return None

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="test-s")
        assert result.app_foreground is False

    def test_linux_foreground_false_no_window(self) -> None:
        """REQ-B13: Linux foreground=False when no active window."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = True
        process._running[99] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return None

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="test-s")
        assert result.app_foreground is False

    def test_android_foreground_via_adb(self) -> None:
        """REQ-B14: Android foreground check via adb resumed activity."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[200] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return "com.example.app/.MainActivity"

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="android-test")
        assert result.app_foreground is True

    def test_android_foreground_false_wrong_package(self) -> None:
        """REQ-B14: Android foreground=False when different package is active."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[200] = True

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return "com.other.app/.OtherActivity"

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )
        result = uc.execute(session_id="android-test")
        assert result.app_foreground is False

    def test_no_tree_port_dependency(self) -> None:
        """REQ-B15: SessionStatusUseCase has no AccessibilityTreePort dependency."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        import inspect

        sig = inspect.signature(SessionStatusUseCase.__init__)
        param_names = set(sig.parameters.keys()) - {"self"}
        # Must NOT have 'tree' parameter
        assert "tree" not in param_names


class TestWindowQueryPort:
    """REQ-B13: WindowQueryPort protocol."""

    def test_window_query_port_exists(self) -> None:
        """REQ-B13: WindowQueryPort is importable."""
        from aiyes.ports.window_query import WindowQueryPort

        assert WindowQueryPort is not None

    def test_window_query_port_has_methods(self) -> None:
        """REQ-B13: WindowQueryPort has required methods."""
        from aiyes.ports.window_query import WindowQueryPort

        import inspect

        members = dict(inspect.getmembers(WindowQueryPort))
        assert "get_active_window_id" in members
        assert "get_window_pid" in members


class TestAdbActivityQueryPort:
    """REQ-B14: AdbActivityQueryPort protocol."""

    def test_adb_activity_port_exists(self) -> None:
        """REQ-B14: AdbActivityQueryPort is importable."""
        from aiyes.ports.adb_activity import AdbActivityQueryPort

        assert AdbActivityQueryPort is not None

    def test_adb_activity_port_has_method(self) -> None:
        """REQ-B14: AdbActivityQueryPort has get_resumed_activity."""
        from aiyes.ports.adb_activity import AdbActivityQueryPort

        import inspect

        members = dict(inspect.getmembers(AdbActivityQueryPort))
        assert "get_resumed_activity" in members


class TestSessionStatusPresenter:
    """REQ-B11: Session status presenter formatting."""

    def test_format_session_status(self) -> None:
        """REQ-B11: format_session_status renders JSON."""
        from aiyes.cli.presenter import format_session_status

        output = format_session_status(
            app_alive=True,
            app_foreground=True,
            display_alive=True,
        )
        parsed = json.loads(output)
        assert parsed["app_alive"] is True
        assert parsed["app_foreground"] is True
        assert parsed["display_alive"] is True

    def test_format_session_status_all_false(self) -> None:
        """REQ-B11: format_session_status with all False."""
        from aiyes.cli.presenter import format_session_status

        output = format_session_status(
            app_alive=False,
            app_foreground=False,
            display_alive=False,
        )
        parsed = json.loads(output)
        assert parsed["app_alive"] is False


class TestSessionStatusCli:
    """REQ-B11: CLI session status subcommand."""

    def test_session_status_in_help(self) -> None:
        """REQ-B11: 'status' appears in session --help."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "--help"])
        assert result.exit_code == 0
        assert "status" in result.output

    def test_session_status_invocation(self) -> None:
        """REQ-B11: session status --session <id> invokes use case."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.composition_root.session_status_uc") as mock_uc,
        ):
            from aiyes.domain.use_cases.session_status import SessionStatusResult

            mock_uc.execute.return_value = SessionStatusResult(
                app_alive=True,
                app_foreground=True,
                display_alive=True,
            )

            result = runner.invoke(cli, ["session", "status", "--session", "s1"])
            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["app_alive"] is True


# ═══════════════════════════════════════════════════════════════════════
# GAP-10: Dialog Detection
# ═══════════════════════════════════════════════════════════════════════


class TestDetectDialogResult:
    """REQ-B18: DetectDialogResult frozen dataclass."""

    def test_detect_dialog_result_exists(self) -> None:
        """REQ-B18: DetectDialogResult is importable."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogResult

        r = DetectDialogResult(
            dialog_detected=True,
            window_name="Save As",
            window_role="dialog",
        )
        assert r.dialog_detected is True

    def test_detect_dialog_result_no_dialog(self) -> None:
        """REQ-B18: DetectDialogResult with no dialog."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogResult

        r = DetectDialogResult(
            dialog_detected=False,
            window_name=None,
            window_role=None,
        )
        assert r.dialog_detected is False
        assert r.window_name is None
        assert r.window_role is None

    def test_detect_dialog_result_frozen(self) -> None:
        """REQ-B18: DetectDialogResult is frozen."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogResult

        r = DetectDialogResult(
            dialog_detected=False, window_name=None, window_role=None
        )
        assert dataclasses.is_dataclass(r)
        try:
            r.dialog_detected = True  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except (dataclasses.FrozenInstanceError, AttributeError):
            pass


class TestTopLevelWindowType:
    """REQ-B19: TopLevelWindow domain type."""

    def test_top_level_window_exists(self) -> None:
        """REQ-B19: TopLevelWindow is importable from domain."""
        from aiyes.domain.top_level_window import TopLevelWindow

        w = TopLevelWindow(role="frame", name="Test Window")
        assert w.role == "frame"
        assert w.name == "Test Window"

    def test_top_level_window_frozen(self) -> None:
        """REQ-B19: TopLevelWindow is a frozen dataclass."""
        from aiyes.domain.top_level_window import TopLevelWindow

        w = TopLevelWindow(role="dialog", name="Alert")
        assert dataclasses.is_dataclass(w)
        try:
            w.name = "x"  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except (dataclasses.FrozenInstanceError, AttributeError):
            pass


class TestTopLevelWindowPort:
    """REQ-B19: TopLevelWindowPort protocol."""

    def test_port_exists(self) -> None:
        """REQ-B19: TopLevelWindowPort is importable."""
        from aiyes.ports.top_level_window import TopLevelWindowPort

        assert TopLevelWindowPort is not None

    def test_port_has_method(self) -> None:
        """REQ-B19: TopLevelWindowPort has list_top_level_windows."""
        from aiyes.ports.top_level_window import TopLevelWindowPort

        import inspect

        members = dict(inspect.getmembers(TopLevelWindowPort))
        assert "list_top_level_windows" in members


class TestDetectDialogUseCase:
    """REQ-B17: DetectDialogUseCase compares current windows vs stored tree."""

    def test_no_stored_tree_returns_false(self) -> None:
        """REQ-B17: No stored tree -> dialog_detected=False."""
        from aiyes.domain.use_cases.detect_dialog import (
            DetectDialogUseCase,
        )
        from aiyes.domain.top_level_window import TopLevelWindow

        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()  # No stored tree

        class FakeWindowPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                return [TopLevelWindow(role="frame", name="Test Window")]

        uc = DetectDialogUseCase(
            session_repo=repo,
            tree_store=tree_store,
            linux_window_port=FakeWindowPort(),
            android_window_port=FakeWindowPort(),
        )
        result = uc.execute(session_id="test-s")
        assert result.dialog_detected is False

    def test_new_window_detected_as_dialog(self) -> None:
        """REQ-B17: New window not in stored roots = dialog detected."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase
        from aiyes.domain.top_level_window import TopLevelWindow

        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        # Store a tree with one root: "Test Window"
        stored_tree = raw_tree_to_domain(
            make_tree(
                nodes=[
                    make_node("n_001", "frame", "Test Window"),
                ]
            )
        )
        tree_store.save_tree("test-s", stored_tree, None)

        class FakeWindowPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                return [
                    TopLevelWindow(role="frame", name="Test Window"),
                    TopLevelWindow(role="dialog", name="Save As"),
                ]

        uc = DetectDialogUseCase(
            session_repo=repo,
            tree_store=tree_store,
            linux_window_port=FakeWindowPort(),
            android_window_port=FakeWindowPort(),
        )
        result = uc.execute(session_id="test-s")
        assert result.dialog_detected is True
        assert result.window_name == "Save As"
        assert result.window_role == "dialog"

    def test_no_new_windows_no_dialog(self) -> None:
        """REQ-B17: Same windows as stored -> no dialog."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase
        from aiyes.domain.top_level_window import TopLevelWindow

        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        stored_tree = raw_tree_to_domain(
            make_tree(
                nodes=[
                    make_node("n_001", "frame", "Test Window"),
                ]
            )
        )
        tree_store.save_tree("test-s", stored_tree, None)

        class FakeWindowPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                return [TopLevelWindow(role="frame", name="Test Window")]

        uc = DetectDialogUseCase(
            session_repo=repo,
            tree_store=tree_store,
            linux_window_port=FakeWindowPort(),
            android_window_port=FakeWindowPort(),
        )
        result = uc.execute(session_id="test-s")
        assert result.dialog_detected is False

    def test_android_session_uses_android_port(self) -> None:
        """REQ-B20: Android sessions use android window port."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase
        from aiyes.domain.top_level_window import TopLevelWindow

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)
        tree_store = FakeTreeStore()

        stored_tree = raw_tree_to_domain(
            make_tree(
                nodes=[
                    make_node("n_001", "frame", "Test Window"),
                ]
            )
        )
        tree_store.save_tree("android-test", stored_tree, None)

        class FakeLinuxPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                raise RuntimeError("Should not be called for Android")

        class FakeAndroidPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                return [
                    TopLevelWindow(role="frame", name="Test Window"),
                    TopLevelWindow(role="dialog", name="Alert"),
                ]

        uc = DetectDialogUseCase(
            session_repo=repo,
            tree_store=tree_store,
            linux_window_port=FakeLinuxPort(),
            android_window_port=FakeAndroidPort(),
        )
        result = uc.execute(session_id="android-test")
        assert result.dialog_detected is True
        assert result.window_name == "Alert"


class TestDetectDialogPresenter:
    """REQ-B16: Detect dialog presenter formatting."""

    def test_format_detect_dialog_detected(self) -> None:
        """REQ-B16: format_detect_dialog renders detected dialog."""
        from aiyes.cli.presenter import format_detect_dialog

        output = format_detect_dialog(
            dialog_detected=True,
            window_name="Save As",
            window_role="dialog",
        )
        parsed = json.loads(output)
        assert parsed["dialog_detected"] is True
        assert parsed["window_name"] == "Save As"
        assert parsed["window_role"] == "dialog"

    def test_format_detect_dialog_not_detected(self) -> None:
        """REQ-B16: format_detect_dialog when no dialog."""
        from aiyes.cli.presenter import format_detect_dialog

        output = format_detect_dialog(
            dialog_detected=False,
        )
        parsed = json.loads(output)
        assert parsed["dialog_detected"] is False


class TestDetectDialogCli:
    """REQ-B16: CLI detect-dialog command."""

    def test_detect_dialog_in_help(self) -> None:
        """REQ-B16: detect-dialog appears in top-level --help."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "detect-dialog" in result.output

    def test_detect_dialog_invocation(self) -> None:
        """REQ-B16: detect-dialog --session <id> invokes use case."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.composition_root.detect_dialog_uc") as mock_uc,
        ):
            from aiyes.domain.use_cases.detect_dialog import DetectDialogResult

            mock_uc.execute.return_value = DetectDialogResult(
                dialog_detected=True,
                window_name="Alert",
                window_role="dialog",
            )

            result = runner.invoke(cli, ["detect-dialog", "--session", "s1"])
            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["dialog_detected"] is True
            assert parsed["window_name"] == "Alert"


# ═══════════════════════════════════════════════════════════════════════
# Architecture — updated AIYES-12 field check
# ═══════════════════════════════════════════════════════════════════════


class TestWaitResultFieldsUpdated:
    """ARCH: WaitResult fields include transient after GAP-06."""

    def test_wait_result_has_transient_field(self) -> None:
        """WaitResult fields now include found, timeout, id, transient."""
        from aiyes.domain.use_cases.wait import WaitResult

        fields = {f.name for f in dataclasses.fields(WaitResult)}
        assert fields == {"found", "timeout", "id", "transient"}


class TestSessionStatusSessionNotFound:
    """B10-003: SessionStatusUseCase raises RuntimeError for non-existent session."""

    def test_session_not_found_raises_runtime_error(self) -> None:
        """B10-003: execute() with non-existent session_id raises RuntimeError."""
        from aiyes.domain.use_cases.session_status import SessionStatusUseCase

        repo = FakeSessionRepository()  # empty — no sessions
        process = FakeProcess()

        class FakeWindowQuery:
            def get_active_window_id(self, display: str) -> Optional[str]:
                return None

            def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
                return None

        class FakeAdbActivity:
            def get_resumed_activity(self, serial: str) -> Optional[str]:
                return None

        uc = SessionStatusUseCase(
            session_repo=repo,
            process=process,
            window_query=FakeWindowQuery(),
            adb_activity=FakeAdbActivity(),
        )

        import pytest

        with pytest.raises(RuntimeError, match="Session not found: nonexistent"):
            uc.execute(session_id="nonexistent")


class TestDetectDialogSessionNotFound:
    """B10-003: DetectDialogUseCase raises RuntimeError for non-existent session."""

    def test_session_not_found_raises_runtime_error(self) -> None:
        """B10-003: execute() with non-existent session_id raises RuntimeError."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase
        from aiyes.domain.top_level_window import TopLevelWindow

        repo = FakeSessionRepository()  # empty — no sessions
        tree_store = FakeTreeStore()

        class FakeWindowPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                return []

        uc = DetectDialogUseCase(
            session_repo=repo,
            tree_store=tree_store,
            linux_window_port=FakeWindowPort(),
            android_window_port=FakeWindowPort(),
        )

        import pytest

        with pytest.raises(RuntimeError, match="Session not found: nonexistent"):
            uc.execute(session_id="nonexistent")


class TestDetectDialogErrorField:
    """B10-010: DetectDialogResult reports window enumeration errors."""

    def test_window_enumeration_error_sets_error_field(self) -> None:
        """B10-010: When list_top_level_windows raises, error field is populated."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase
        from aiyes.domain.top_level_window import TopLevelWindow

        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        stored_tree = raw_tree_to_domain(
            make_tree(nodes=[make_node("n_001", "frame", "Test Window")])
        )
        tree_store.save_tree("test-s", stored_tree, None)

        class FailingWindowPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                raise PermissionError("AT-SPI access denied")

        uc = DetectDialogUseCase(
            session_repo=repo,
            tree_store=tree_store,
            linux_window_port=FailingWindowPort(),
            android_window_port=FailingWindowPort(),
        )
        result = uc.execute(session_id="test-s")
        assert result.dialog_detected is False
        assert result.error is not None
        assert "AT-SPI access denied" in result.error

    def test_no_error_on_success(self) -> None:
        """B10-010: error field is None on successful enumeration."""
        from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase
        from aiyes.domain.top_level_window import TopLevelWindow

        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        stored_tree = raw_tree_to_domain(
            make_tree(nodes=[make_node("n_001", "frame", "Test Window")])
        )
        tree_store.save_tree("test-s", stored_tree, None)

        class FakeWindowPort:
            def list_top_level_windows(self, session) -> List[TopLevelWindow]:
                return [TopLevelWindow(role="frame", name="Test Window")]

        uc = DetectDialogUseCase(
            session_repo=repo,
            tree_store=tree_store,
            linux_window_port=FakeWindowPort(),
            android_window_port=FakeWindowPort(),
        )
        result = uc.execute(session_id="test-s")
        assert result.dialog_detected is False
        assert result.error is None


class TestCompositionRootWiring:
    """Verify new use cases are wired in composition_root."""

    def test_session_status_uc_exists(self) -> None:
        """session_status_uc is exported from composition_root."""
        from aiyes.cli.composition_root import session_status_uc

        assert session_status_uc is not None

    def test_detect_dialog_uc_exists(self) -> None:
        """detect_dialog_uc is exported from composition_root."""
        from aiyes.cli.composition_root import detect_dialog_uc

        assert detect_dialog_uc is not None
