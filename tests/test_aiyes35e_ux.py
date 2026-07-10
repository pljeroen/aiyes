"""Tests for AIYES-35E — UX improvements.

Findings covered:
  U-01: Error messages lack actionable suggestions
  I-03: format_wait_stable omits timeout key when False (inconsistent with format_wait)
  I-05: find command does not apply tree pruning before searching
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.use_cases.find import FindUseCase, FoundNode
from aiyes.domain.use_cases.session_resolve import SessionResolveUseCase
from aiyes.domain.use_cases.session_stop import SessionStopUseCase

from tests.conftest import (
    FakeAccessibilityBus,
    FakeAccessibilityTree,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
    make_tree,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_session(
    session_id: str = "test-s",
    app_pid: int = 100,
    xvfb_pid: int = 200,
    **overrides: Any,
) -> Session:
    defaults = dict(
        session_id=session_id,
        display=":99",
        app_pid=app_pid,
        app_command="gedit",
        app_args=(),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=xvfb_pid,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_domain_node(
    node_id: str = "n_001",
    role: str = "push_button",
    name: str = "OK",
    children: Optional[tuple] = None,
    **kwargs: Any,
) -> Node:
    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=(100, 200, 80, 30),
        states=("enabled", "visible"),
        actions=("click",),
        children=children if children is not None else (),
        **kwargs,
    )


# ═══════════════════════════════════════════════════════════════════════
# U-01: Error messages with actionable suggestions
# ═══════════════════════════════════════════════════════════════════════


class TestSessionResolveActionableSuggestions:
    """U-01: SessionResolveUseCase error messages suggest next steps."""

    def test_no_active_sessions_suggests_session_start(self) -> None:
        """When no active sessions, suggest 'aieyes session start'."""
        repo = FakeSessionRepository()
        process = FakeProcess()

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        with pytest.raises(RuntimeError, match="session start") as exc_info:
            uc.execute(session_id=None)
        assert "No active sessions" in str(exc_info.value)

    def test_multiple_sessions_suggests_session_list(self) -> None:
        """SUPERSEDED by AIYES-110-R1 (authorized supersede).

        Previously asserted that >1 active sessions raise a RuntimeError
        matching 'session list' / 'Multiple sessions' on the RESOLVE path.
        AIYES-110 replaces that raise with a most-recent-session fallback
        (max over (started_at, session_id)), so this now asserts the new
        selection. RED until A9 lands the fallback (execute(None) still raises
        'Multiple sessions found' today, so the call fails before the assert).

        The sibling TestSessionStopActionableSuggestions test is UNCHANGED:
        session_stop DELIBERATELY still raises on >1 (OD-110-1 / C5).
        """
        repo = FakeSessionRepository()
        process = FakeProcess()

        s1 = _make_session(session_id="s1", app_pid=100, xvfb_pid=200, started_at=100.0)
        s2 = _make_session(session_id="s2", app_pid=300, xvfb_pid=400, started_at=900.0)
        repo.save(s1)
        repo.save(s2)
        process._running[100] = True
        process._running[200] = True
        process._running[300] = True
        process._running[400] = True

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        resolved = uc.execute(session_id=None)

        assert resolved == "s2"


class TestSessionStopActionableSuggestions:
    """U-01: SessionStopUseCase error messages suggest next steps."""

    def test_no_active_sessions_suggests_session_start(self) -> None:
        """When no active sessions found, suggest 'aieyes session start'."""
        repo = FakeSessionRepository()
        process = FakeProcess()
        display = FakeDisplayServer()
        bus = FakeAccessibilityBus()

        uc = SessionStopUseCase(
            display_server=display,
            atspi_bus=bus,
            process=process,
            session_repo=repo,
        )

        with pytest.raises(RuntimeError, match="session start") as exc_info:
            uc.execute(session_id=None)
        assert "No active sessions" in str(exc_info.value)

    def test_multiple_sessions_suggests_session_list(self) -> None:
        """When multiple active sessions, suggest 'aieyes session list'."""
        repo = FakeSessionRepository()
        process = FakeProcess()
        display = FakeDisplayServer()
        bus = FakeAccessibilityBus()

        s1 = _make_session(session_id="s1", app_pid=100, xvfb_pid=200)
        s2 = _make_session(session_id="s2", app_pid=300, xvfb_pid=400)
        repo.save(s1)
        repo.save(s2)
        process._running[100] = True
        process._running[200] = True
        process._running[300] = True
        process._running[400] = True

        uc = SessionStopUseCase(
            display_server=display,
            atspi_bus=bus,
            process=process,
            session_repo=repo,
        )

        with pytest.raises(RuntimeError, match="session list") as exc_info:
            uc.execute(session_id=None)
        assert "Multiple sessions" in str(exc_info.value)

    def test_session_not_found_suggests_session_list(self) -> None:
        """When session ID not found, suggest 'aieyes session list'."""
        repo = FakeSessionRepository()
        process = FakeProcess()
        display = FakeDisplayServer()
        bus = FakeAccessibilityBus()

        uc = SessionStopUseCase(
            display_server=display,
            atspi_bus=bus,
            process=process,
            session_repo=repo,
        )

        with pytest.raises(RuntimeError, match="session list") as exc_info:
            uc.execute(session_id="nonexistent-abc")
        assert "Session not found" in str(exc_info.value)


class TestFindSessionNotFoundSuggestion:
    """U-01: FindUseCase error messages suggest next steps."""

    def test_session_not_found_suggests_session_list(self) -> None:
        """When session ID not found, suggest 'aieyes session list'."""
        repo = FakeSessionRepository()
        tree_port = FakeAccessibilityTree()
        tree_store = FakeTreeStore()

        uc = FindUseCase(tree=tree_port, session_repo=repo, tree_store=tree_store)

        with pytest.raises(RuntimeError, match="session list") as exc_info:
            uc.execute(session_id="nonexistent-xyz", role="push_button")
        assert "Session not found" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════
# I-03: format_wait_stable always includes timeout field
# ═══════════════════════════════════════════════════════════════════════


class TestFormatWaitStableTimeout:
    """I-03: format_wait_stable must always include timeout field."""

    def test_wait_stable_includes_timeout_when_false(self) -> None:
        """timeout key must be present even when False."""
        from aiyes.cli.presenter import format_wait_stable

        result = format_wait_stable(stable=True, timeout=False, polls=5)
        parsed = json.loads(result)
        assert "timeout" in parsed
        assert parsed["timeout"] is False

    def test_wait_stable_includes_timeout_when_true(self) -> None:
        """timeout key must be present when True (was already working)."""
        from aiyes.cli.presenter import format_wait_stable

        result = format_wait_stable(stable=False, timeout=True, polls=10)
        parsed = json.loads(result)
        assert "timeout" in parsed
        assert parsed["timeout"] is True

    def test_wait_stable_consistent_with_wait(self) -> None:
        """Both format_wait and format_wait_stable always include timeout."""
        from aiyes.cli.presenter import format_wait, format_wait_stable

        wait_result = json.loads(format_wait(found=True, timeout=False))
        wait_stable_result = json.loads(
            format_wait_stable(stable=True, timeout=False, polls=3)
        )

        # Both must have a "timeout" key
        assert "timeout" in wait_result
        assert "timeout" in wait_stable_result


# ═══════════════════════════════════════════════════════════════════════
# I-05: find applies tree pruning (consistent with inspect)
# ═══════════════════════════════════════════════════════════════════════


class TestFindAppliesTreePruning:
    """I-05: FindUseCase applies prune_tree before searching (default on)."""

    def test_find_excludes_filler_nodes(self) -> None:
        """Filler nodes (ALWAYS_EXCLUDED_ROLES) should not appear in results."""
        filler_node = _make_domain_node(node_id="n_filler", role="filler", name="")
        button_node = _make_domain_node(node_id="n_btn", role="push_button", name="OK")
        root = _make_domain_node(
            node_id="n_root",
            role="frame",
            name="Window",
            children=(filler_node, button_node),
        )
        tree = AccessibilityTree(roots=(root,))
        tree_port = FakeAccessibilityTree(tree=tree)
        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        uc = FindUseCase(tree=tree_port, session_repo=repo, tree_store=tree_store)
        results = uc.execute(session_id="test-s", role="*")

        result_roles = [r.role for r in results]
        assert "filler" not in result_roles

    def test_find_excludes_redundant_object_nodes(self) -> None:
        """redundant_object nodes should not appear in results."""
        redundant = _make_domain_node(node_id="n_red", role="redundant_object", name="")
        button = _make_domain_node(node_id="n_btn", role="push_button", name="Save")
        root = _make_domain_node(
            node_id="n_root",
            role="frame",
            name="Window",
            children=(redundant, button),
        )
        tree = AccessibilityTree(roots=(root,))
        tree_port = FakeAccessibilityTree(tree=tree)
        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        uc = FindUseCase(tree=tree_port, session_repo=repo, tree_store=tree_store)
        results = uc.execute(session_id="test-s", role="*")

        result_roles = [r.role for r in results]
        assert "redundant_object" not in result_roles

    def test_find_with_no_prune_includes_filler_nodes(self) -> None:
        """When no_prune=True, filler nodes should appear in results."""
        filler_node = _make_domain_node(node_id="n_filler", role="filler", name="")
        button_node = _make_domain_node(node_id="n_btn", role="push_button", name="OK")
        root = _make_domain_node(
            node_id="n_root",
            role="frame",
            name="Window",
            children=(filler_node, button_node),
        )
        tree = AccessibilityTree(roots=(root,))
        tree_port = FakeAccessibilityTree(tree=tree)
        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        uc = FindUseCase(tree=tree_port, session_repo=repo, tree_store=tree_store)
        results = uc.execute(session_id="test-s", role="*", no_prune=True)

        result_roles = [r.role for r in results]
        assert "filler" in result_roles

    def test_find_pruning_does_not_break_role_filter(self) -> None:
        """Pruning + role filter: only matching non-pruned nodes returned."""
        filler_node = _make_domain_node(node_id="n_filler", role="filler", name="")
        btn1 = _make_domain_node(node_id="n_btn1", role="push_button", name="OK")
        btn2 = _make_domain_node(node_id="n_btn2", role="push_button", name="Cancel")
        label = _make_domain_node(node_id="n_lbl", role="label", name="Title")
        root = _make_domain_node(
            node_id="n_root",
            role="frame",
            name="Window",
            children=(filler_node, btn1, btn2, label),
        )
        tree = AccessibilityTree(roots=(root,))
        tree_port = FakeAccessibilityTree(tree=tree)
        repo = FakeSessionRepository()
        repo.save(_make_session())
        tree_store = FakeTreeStore()

        uc = FindUseCase(tree=tree_port, session_repo=repo, tree_store=tree_store)
        results = uc.execute(session_id="test-s", role="push_button")

        assert len(results) == 2
        assert all(r.role == "push_button" for r in results)
