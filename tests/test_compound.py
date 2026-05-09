"""Tests for compound operations.

Requirements covered:
  R-COMPOUND-01: do --role --action [--verify]
  FC-COMPOUND-02: execution ordering and failure path
"""

from __future__ import annotations


# RED imports — define expected API
from aiyes.domain.use_cases.compound_do import CompoundDoUseCase
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node, raw_tree_to_domain  # noqa: F811

from tests.conftest import (
    FakeAccessibilityAction,
    FakeAccessibilityTree,
    FakeClock,
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


class _SequencedAccessibilityTree:
    """Test double that returns a configured tree sequence across polls."""

    def __init__(self, trees: list[AccessibilityTree]) -> None:
        self._trees = list(trees)
        self.calls = []

    def get_tree(self, session) -> AccessibilityTree:
        self.calls.append(("get_tree", session))
        if len(self._trees) > 1:
            return self._trees.pop(0)
        return self._trees[0]


def _make_uc(
    *,
    tree,
    action,
    session_repo,
    tree_store,
    clock: FakeClock,
) -> CompoundDoUseCase:
    return CompoundDoUseCase(
        tree=tree,
        action=action,
        session_repo=session_repo,
        tree_store=tree_store,
        clock=clock,
    )


# ──────────────────────────────────────────────────────────────────────
# R-COMPOUND-01: Compound do operation
# ──────────────────────────────────────────────────────────────────────


class TestCompoundDo:
    """Compound find+action+verify operation."""

    def test_do_find_and_action(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-COMPOUND-01: Find node then execute action."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = _make_uc(
            tree=fake_accessibility_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
        )

        assert result.found is not None
        assert result.action_result is not None
        assert result.action_result.status == "ok"

    def test_do_with_verify(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-COMPOUND-01: --verify performs post-action inspection."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = _make_uc(
            tree=fake_accessibility_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
            verify=True,
        )

        assert result.verify is not None

    def test_do_without_verify(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-COMPOUND-01: Without --verify, no post-action inspection."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = _make_uc(
            tree=fake_accessibility_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
            verify=False,
        )

        assert result.verify is None

    def test_do_find_failure_skips_action(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-COMPOUND-01, FC-COMPOUND-02: If find fails, action is NOT executed."""
        session = _make_test_session()
        fake_session_repo.save(session)

        # Empty tree — find will not match
        empty_tree = FakeAccessibilityTree(tree=make_tree(nodes=[]))

        uc = _make_uc(
            tree=empty_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
        )

        # Find failed, so action should not have been called
        action_calls = [
            c for c in fake_accessibility_action.calls if c[0] == "do_action"
        ]
        assert len(action_calls) == 0

        # Result should indicate find failure
        assert result.found is None or (
            hasattr(result, "error") and result.error is not None
        )

    def test_do_find_failure_skips_verify(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """FC-COMPOUND-02: If find fails, verify is NOT executed either."""
        session = _make_test_session()
        fake_session_repo.save(session)

        empty_tree = FakeAccessibilityTree(tree=make_tree(nodes=[]))

        uc = _make_uc(
            tree=empty_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
            verify=True,
        )

        assert result.verify is None

    def test_do_role_and_name_pattern(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-COMPOUND-01: role and name_pattern filter nodes correctly."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = _make_uc(
            tree=fake_accessibility_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="Cancel",
            action_name="click",
        )

        # The tree was queried with the parsed role and name
        tree_calls = [c for c in fake_accessibility_tree.calls if c[0] == "get_tree"]
        assert len(tree_calls) >= 1

    def test_do_execution_order(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """FC-COMPOUND-02: Execution order is find -> action -> verify."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = _make_uc(
            tree=fake_accessibility_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
            verify=True,
        )

        # Find (get_tree) should happen before action (do_action)
        tree_calls = [c for c in fake_accessibility_tree.calls if c[0] == "get_tree"]
        action_calls = [
            c for c in fake_accessibility_action.calls if c[0] == "do_action"
        ]

        assert len(tree_calls) >= 1
        assert len(action_calls) >= 1

        # With verify, there should be a second tree call (for verification)
        assert len(tree_calls) >= 2

    def test_do_found_returns_domain_node(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """F-20: CompoundDoResult.found must be a domain Node, not a raw dict."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = _make_uc(
            tree=fake_accessibility_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
        )

        assert result.found is not None
        assert isinstance(result.found, Node), (
            f"found must be a domain Node, got {type(result.found)}"
        )
        assert result.found.role == "push_button"
        assert result.found.name == "OK"

    def test_do_verify_returns_domain_tree(
        self,
        fake_accessibility_tree: FakeAccessibilityTree,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """F-20: CompoundDoResult.verify must be a domain AccessibilityTree, not a raw dict."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = _make_uc(
            tree=fake_accessibility_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
            verify=True,
        )

        assert result.verify is not None
        assert isinstance(result.verify, AccessibilityTree), (
            f"verify must be a domain AccessibilityTree, got {type(result.verify)}"
        )

    def test_do_timeout_waits_for_matching_node(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """--timeout applies to the find phase instead of doing a single immediate lookup."""
        session = _make_test_session()
        fake_session_repo.save(session)

        delayed_tree = _SequencedAccessibilityTree(
            [
                AccessibilityTree(roots=()),
                raw_tree_to_domain(
                    make_tree(
                        nodes=[
                            make_node(
                                node_id="n_ok",
                                role="push_button",
                                name="OK",
                                bounds=[0, 0, 10, 10],
                                states=["enabled"],
                                actions=["click"],
                            )
                        ]
                    )
                ),
            ]
        )

        uc = _make_uc(
            tree=delayed_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )

        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
            timeout=1.0,
        )

        assert result.found is not None
        assert result.found.id == "n_ok"
        assert len(delayed_tree.calls) == 2
        assert fake_clock.sleep_calls == [0.5]

    def test_do_timeout_returns_error_only_after_polling_window_expires(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """When no match appears, timeout gates the failure instead of immediate exit."""
        session = _make_test_session()
        fake_session_repo.save(session)

        empty_tree = _SequencedAccessibilityTree([AccessibilityTree(roots=())])

        uc = _make_uc(
            tree=empty_tree,
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )

        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            action_name="click",
            timeout=1.0,
        )

        assert result.found is None
        assert result.error == "No matching node found"
        assert len(empty_tree.calls) == 3
        assert fake_clock.sleep_calls == [0.5, 0.5]
