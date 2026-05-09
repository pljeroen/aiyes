"""AIYES-11 — Wait-for-Stable (Tree Stability Check) tests.

RED phase: all tests must FAIL before implementation exists.

Traceability:
  R-TDDV6-04.1: trees_structurally_equal() — AC-01 through AC-08
  R-TDDV6-04.2: WaitStableUseCase — AC-09 through AC-15
  R-TDDV6-04.3: CLI wait-stable command — AC-16 through AC-20
  WIRE: composition root wiring
  ARCH: architecture boundary enforcement
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
from typing import Any, List, Optional, Tuple
from unittest.mock import patch

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node

from tests.conftest import (
    FakeClock,
    FakeSessionRepository,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_test_session(session_id: str = "test-s") -> Session:
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


def _build_tree(*root_nodes: Node) -> AccessibilityTree:
    """Build an AccessibilityTree from one or more root Nodes."""
    return AccessibilityTree(roots=root_nodes)


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


class FakeAccessibilityTreeSequence:
    """Fake AccessibilityTreePort that returns a sequence of trees.

    Each call to get_tree() returns the next tree in the sequence.
    After the sequence is exhausted, the last tree is repeated.
    """

    def __init__(self, trees: List[AccessibilityTree]) -> None:
        assert len(trees) > 0, "Must provide at least one tree"
        self._trees = trees
        self._index = 0
        self.calls: List[Tuple[str, Any]] = []

    def get_tree(self, session) -> AccessibilityTree:
        self.calls.append(("get_tree", session))
        tree = self._trees[min(self._index, len(self._trees) - 1)]
        self._index += 1
        return tree


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-04.1: trees_structurally_equal() — AC-01 through AC-08
# ══════════════════════════════════════════════════════════════════════


class TestTreesStructurallyEqual:
    """Pure domain function: structural equality of two AccessibilityTree instances."""

    def test_identical_trees_are_equal(self) -> None:
        """AC-01: Two identical trees -> True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(
                    _node("n_002", "push_button", "OK"),
                    _node("n_003", "push_button", "Cancel"),
                ),
            ),
        )
        tree_b = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(
                    _node("n_002", "push_button", "OK"),
                    _node("n_003", "push_button", "Cancel"),
                ),
            ),
        )

        assert trees_structurally_equal(tree_a, tree_b) is True

    def test_different_node_count_not_equal(self) -> None:
        """AC-03: Tree with extra node -> False."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(_node("n_002", "push_button", "OK"),),
            ),
        )
        tree_b = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(
                    _node("n_002", "push_button", "OK"),
                    _node("n_003", "push_button", "Cancel"),
                ),
            ),
        )

        assert trees_structurally_equal(tree_a, tree_b) is False

    def test_node_removed_not_equal(self) -> None:
        """AC-04: Second tree has one fewer node -> False."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(
                    _node("n_002", "push_button", "OK"),
                    _node("n_003", "push_button", "Cancel"),
                ),
            ),
        )
        tree_b = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(_node("n_002", "push_button", "OK"),),
            ),
        )

        assert trees_structurally_equal(tree_a, tree_b) is False

    def test_same_count_different_ids_not_equal(self) -> None:
        """AC-03/04 variant: Same count, different ID sets -> False."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK"),
        )
        tree_b = _build_tree(
            _node("n_999", "push_button", "OK"),
        )

        assert trees_structurally_equal(tree_a, tree_b) is False

    def test_same_ids_different_roles_not_equal(self) -> None:
        """AC-05: Same IDs but different role for one node -> False."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK"),
        )
        tree_b = _build_tree(
            _node("n_001", "toggle_button", "OK"),
        )

        assert trees_structurally_equal(tree_a, tree_b) is False

    def test_same_ids_different_names_not_equal(self) -> None:
        """AC-06: Same IDs but different name for one node -> False."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK"),
        )
        tree_b = _build_tree(
            _node("n_001", "push_button", "Apply"),
        )

        assert trees_structurally_equal(tree_a, tree_b) is False

    def test_value_only_change_is_equal(self) -> None:
        """AC-02: Value-only change -> True (structural equality ignores values)."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "text", "Clock", value="12:00"),
        )
        tree_b = _build_tree(
            _node("n_001", "text", "Clock", value="12:01"),
        )

        assert trees_structurally_equal(tree_a, tree_b) is True

    def test_state_only_change_is_equal(self) -> None:
        """AC-07: State-only change -> True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK", states=("enabled", "visible")),
        )
        tree_b = _build_tree(
            _node("n_001", "push_button", "OK", states=("enabled", "focused")),
        )

        assert trees_structurally_equal(tree_a, tree_b) is True

    def test_action_only_change_is_equal(self) -> None:
        """AC-07: Action-only change -> True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK", actions=("click",)),
        )
        tree_b = _build_tree(
            _node("n_001", "push_button", "OK", actions=("click", "activate")),
        )

        assert trees_structurally_equal(tree_a, tree_b) is True

    def test_bounds_only_change_is_equal(self) -> None:
        """AC-07: Bounds-only change -> True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK", bounds=(100, 200, 80, 30)),
        )
        tree_b = _build_tree(
            _node("n_001", "push_button", "OK", bounds=(110, 210, 90, 40)),
        )

        assert trees_structurally_equal(tree_a, tree_b) is True

    def test_two_empty_trees_are_equal(self) -> None:
        """AC-08: Two empty trees (no roots) -> True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = AccessibilityTree(roots=())
        tree_b = AccessibilityTree(roots=())

        assert trees_structurally_equal(tree_a, tree_b) is True

    def test_single_node_trees_equal(self) -> None:
        """Single node trees with same structure -> True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(_node("n_001", "frame", "App"))
        tree_b = _build_tree(_node("n_001", "frame", "App"))

        assert trees_structurally_equal(tree_a, tree_b) is True


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-04.2: WaitStableUseCase — AC-09 through AC-15
# ══════════════════════════════════════════════════════════════════════


class TestWaitStableUseCase:
    """Poll tree until structurally stable or timeout."""

    def test_static_tree_stable_after_consecutive_polls(self) -> None:
        """AC-09: Static tree -> stable=True, polls = consecutive + 1."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        static_tree = _build_tree(
            _node("n_001", "frame", "App"),
        )
        tree_port = FakeAccessibilityTreeSequence([static_tree])

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=10.0,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is True
        assert result.timeout is False
        assert result.polls == 4  # 1 baseline + 3 consecutive

    def test_changing_tree_waits_until_stable(self) -> None:
        """AC-10: Changing tree then stable -> waits, counts correctly."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        tree_v1 = _build_tree(_node("n_001", "frame", "Loading"))
        tree_v2 = _build_tree(
            _node("n_001", "frame", "Loading"),
            _node("n_002", "push_button", "OK"),
        )
        tree_v3 = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "push_button", "OK"),
            _node("n_003", "label", "Ready"),
        )
        # 3 changing trees, then tree_v3 repeated for stability
        trees = [tree_v1, tree_v2, tree_v3, tree_v3, tree_v3, tree_v3]
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=30.0,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is True
        # tree_v1 (baseline), tree_v2 (change, reset), tree_v3 (new baseline),
        # tree_v3 (consec=1), tree_v3 (consec=2), tree_v3 (consec=3)
        assert result.polls == 6

    def test_timeout_returns_stable_false(self) -> None:
        """AC-11: Timeout before stability -> stable=False, timeout=True."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)

        # Clock that times out quickly: start at 1000, timeout=0.5
        # Each sleep adds 0.1, so after 5 sleeps we exceed timeout
        clock = FakeClock(now_value=1000.0)

        # Continuously changing trees (never stable)
        trees = [
            _build_tree(_node(f"n_{i:03d}", "label", f"Label {i}")) for i in range(20)
        ]
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=0.5,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is False
        assert result.timeout is True
        assert result.polls >= 1

    def test_value_only_changes_do_not_break_stability(self) -> None:
        """AC-15: Value-only changes between polls count as stable."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        # Same structure, different values each time (clock ticking)
        trees = [
            _build_tree(_node("n_001", "text", "Clock", value=f"12:0{i}"))
            for i in range(5)
        ]
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=10.0,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is True
        assert result.polls == 4  # 1 baseline + 3 consecutive

    def test_session_not_found_raises_runtime_error(self) -> None:
        """AC-14: Unknown session -> RuntimeError with session_id in message."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        repo = FakeSessionRepository()  # Empty — no sessions stored
        clock = FakeClock()
        tree_port = FakeAccessibilityTreeSequence(
            [_build_tree(_node("n_001", "frame", "App"))]
        )

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )

        with pytest.raises(RuntimeError, match="nonexistent-session"):
            uc.execute(session_id="nonexistent-session")

    def test_polls_count_matches_get_tree_calls(self) -> None:
        """AC-12: polls field counts every tree fetch."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        static_tree = _build_tree(_node("n_001", "frame", "App"))
        tree_port = FakeAccessibilityTreeSequence([static_tree])

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=10.0,
            poll_interval=0.1,
            consecutive=2,
        )

        # Count actual get_tree calls on the fake
        get_tree_calls = [c for c in tree_port.calls if c[0] == "get_tree"]
        assert result.polls == len(get_tree_calls)

    def test_consecutive_threshold_configurable(self) -> None:
        """AC-09/AC-18: Different consecutive values produce different poll counts."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        static_tree = _build_tree(_node("n_001", "frame", "App"))

        # consecutive=2
        repo2 = FakeSessionRepository()
        repo2.save(session)
        clock2 = FakeClock(now_value=1000.0)
        tree_port2 = FakeAccessibilityTreeSequence([static_tree])
        uc2 = WaitStableUseCase(tree=tree_port2, session_repo=repo2, clock=clock2)
        result2 = uc2.execute(
            session_id="test-s", timeout=10.0, poll_interval=0.1, consecutive=2
        )

        # consecutive=5
        repo5 = FakeSessionRepository()
        repo5.save(session)
        clock5 = FakeClock(now_value=1000.0)
        tree_port5 = FakeAccessibilityTreeSequence([static_tree])
        uc5 = WaitStableUseCase(tree=tree_port5, session_repo=repo5, clock=clock5)
        result5 = uc5.execute(
            session_id="test-s", timeout=10.0, poll_interval=0.1, consecutive=5
        )

        assert result2.polls == 3  # 1 baseline + 2 consecutive
        assert result5.polls == 6  # 1 baseline + 5 consecutive
        assert result2.polls < result5.polls

    def test_counter_resets_on_structural_change_mid_sequence(self) -> None:
        """TEST-03: Counter resets when structural change occurs mid-sequence.

        Sequence: stable-stable-change-stable-stable-stable (consecutive=3).
        Must NOT return stable after the first two stable polls.
        """
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        tree_v1 = _build_tree(_node("n_001", "frame", "App"))
        tree_v2 = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "label", "New"),
        )

        # Sequence: v1(baseline), v1(consec=1), v1(consec=2),
        #           v2(change! reset), v2(baseline already set),
        #           v2(consec=1), v2(consec=2), v2(consec=3)
        trees = [tree_v1, tree_v1, tree_v1, tree_v2, tree_v2, tree_v2, tree_v2]
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=30.0,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is True
        # v1 baseline(1), v1 consec1(2), v1 consec2(3), v2 change(4),
        # v2 consec1(5), v2 consec2(6), v2 consec3(7)
        assert result.polls == 7


class TestWaitStableResult:
    """WaitStableResult is a frozen dataclass with correct fields."""

    def test_result_is_frozen_dataclass(self) -> None:
        """ARCH-05: WaitStableResult must be a frozen dataclass."""
        from aiyes.domain.use_cases.wait_stable import WaitStableResult

        assert dataclasses.is_dataclass(WaitStableResult)
        # Frozen check: attempting to set a field should raise
        result = WaitStableResult(stable=True, polls=4)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.stable = False  # type: ignore[misc]

    def test_result_fields(self) -> None:
        """ARCH-05: Fields are stable, timeout, polls with correct defaults."""
        from aiyes.domain.use_cases.wait_stable import WaitStableResult

        result = WaitStableResult(stable=True)
        assert result.stable is True
        assert result.timeout is False
        assert result.polls == 0

    def test_timeout_result_fields(self) -> None:
        """ARCH-05/TEST-04: Timeout result has correct field values."""
        from aiyes.domain.use_cases.wait_stable import WaitStableResult

        result = WaitStableResult(stable=False, timeout=True, polls=7)
        assert result.stable is False
        assert result.timeout is True
        assert result.polls == 7


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-04.3: CLI wait-stable command — AC-16 through AC-20
# ══════════════════════════════════════════════════════════════════════


class TestWaitStableCli:
    """CLI command: aieyes wait-stable."""

    def test_wait_stable_static_returns_stable_json(self) -> None:
        """AC-16: Static app -> JSON with stable=true, polls int, exit 0."""
        from click.testing import CliRunner

        from aiyes.cli.main import cli

        runner = CliRunner()

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="test-s"),
            patch("aiyes.cli.main.wait_stable_uc") as mock_uc,
            patch("aiyes.cli.main.format_wait_stable") as mock_fmt,
        ):
            # Simulate stable result
            from aiyes.domain.use_cases.wait_stable import WaitStableResult

            mock_uc.execute.return_value = WaitStableResult(stable=True, polls=4)
            mock_fmt.return_value = json.dumps({"stable": True, "polls": 4}, indent=2)

            result = runner.invoke(cli, ["wait-stable", "--session", "test-s"])

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["stable"] is True
            assert isinstance(parsed["polls"], int)
            assert parsed["polls"] == 4
            assert "timeout" not in parsed

    def test_wait_stable_timeout_returns_timeout_json(self) -> None:
        """AC-17: Timeout -> JSON with stable=false, timeout=true, exit 0."""
        from click.testing import CliRunner

        from aiyes.cli.main import cli

        runner = CliRunner()

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="test-s"),
            patch("aiyes.cli.main.wait_stable_uc") as mock_uc,
            patch("aiyes.cli.main.format_wait_stable") as mock_fmt,
        ):
            from aiyes.domain.use_cases.wait_stable import WaitStableResult

            mock_uc.execute.return_value = WaitStableResult(
                stable=False, timeout=True, polls=8
            )
            mock_fmt.return_value = json.dumps(
                {"stable": False, "timeout": True, "polls": 8}, indent=2
            )

            result = runner.invoke(
                cli,
                ["wait-stable", "--session", "test-s", "--timeout", "0.1"],
            )

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["stable"] is False
            assert parsed["timeout"] is True
            assert isinstance(parsed["polls"], int)

    def test_wait_stable_invalid_session_error_exit_1(self) -> None:
        """AC-20: Unknown session -> stderr error, exit 1."""
        import inspect as _insp

        from aiyes.cli.main import cli
        from click.testing import CliRunner

        _kw = (
            {"mix_stderr": False}
            if "mix_stderr" in _insp.signature(CliRunner.__init__).parameters
            else {}
        )
        runner = CliRunner(**_kw)

        with patch(
            "aiyes.cli.main.resolve_session_id",
            side_effect=RuntimeError("Session not found: bad-session"),
        ):
            result = runner.invoke(cli, ["wait-stable", "--session", "bad-session"])

            assert result.exit_code == 1
            # Click 8.2+ removed mix_stderr; error may be in output or stderr
            all_output = (result.output or "") + (getattr(result, "stderr", "") or "")
            assert "Error:" in all_output

    def test_wait_stable_command_registered(self) -> None:
        """AC-16: wait-stable is registered as a top-level CLI command."""
        from aiyes.cli.main import cli

        commands = cli.commands if hasattr(cli, "commands") else {}
        assert "wait-stable" in commands, (
            f"wait-stable not found in CLI commands: {list(commands.keys())}"
        )


# ══════════════════════════════════════════════════════════════════════
# Wiring tests — composition root
# ══════════════════════════════════════════════════════════════════════


class TestWaitStableWiring:
    """ARCH-06: Composition root wires wait_stable_uc and format_wait_stable."""

    def test_wait_stable_uc_exists_in_composition_root(self) -> None:
        """ARCH-06: wait_stable_uc is present and is a WaitStableUseCase."""
        from aiyes.cli import composition_root
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        assert hasattr(composition_root, "wait_stable_uc")
        assert isinstance(composition_root.wait_stable_uc, WaitStableUseCase)

    def test_format_wait_stable_exists_in_composition_root(self) -> None:
        """ARCH-06: format_wait_stable is re-exported from composition_root."""
        from aiyes.cli import composition_root

        assert hasattr(composition_root, "format_wait_stable")
        assert callable(composition_root.format_wait_stable)


# ══════════════════════════════════════════════════════════════════════
# Architecture tests — import boundary enforcement
# ══════════════════════════════════════════════════════════════════════


class TestWaitStableArchitecture:
    """ARCH-01, ARCH-03: Domain purity for new code."""

    def test_trees_structurally_equal_no_external_deps(self) -> None:
        """ARCH-01: trees_structurally_equal lives in domain/tree.py with no
        imports from ports, adapters, or external packages."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)

        source = Path("src/aiyes/domain/tree.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules or top == "__future__":
                    continue
                # Allowed: aiyes.domain.* (same layer)
                assert node.module.startswith("aiyes.domain"), (
                    f"tree.py imports from non-domain module: {node.module}"
                )

    def test_wait_stable_use_case_imports_only_domain_and_ports(self) -> None:
        """ARCH-03: wait_stable.py imports only from domain + ports, not adapters/cli."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)

        source = Path("src/aiyes/domain/use_cases/wait_stable.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules or top == "__future__":
                    continue
                # Allowed: aiyes.domain.* and aiyes.ports.*
                assert node.module.startswith(("aiyes.domain", "aiyes.ports")), (
                    f"wait_stable.py imports from disallowed module: {node.module}"
                )


# ══════════════════════════════════════════════════════════════════════
# Presenter tests — format_wait_stable
# ══════════════════════════════════════════════════════════════════════


class TestFormatWaitStable:
    """IMPL-06: format_wait_stable JSON output format."""

    def test_stable_result_json(self) -> None:
        """AC-16: Stable result: {"stable": true, "timeout": false, "polls": N}."""
        from aiyes.cli.presenter import format_wait_stable

        output = format_wait_stable(stable=True, timeout=False, polls=4)
        parsed = json.loads(output)

        assert parsed["stable"] is True
        assert parsed["polls"] == 4
        assert parsed["timeout"] is False

    def test_timeout_result_json(self) -> None:
        """AC-17: Timeout result: {"stable": false, "timeout": true, "polls": N}."""
        from aiyes.cli.presenter import format_wait_stable

        output = format_wait_stable(stable=False, timeout=True, polls=8)
        parsed = json.loads(output)

        assert parsed["stable"] is False
        assert parsed["timeout"] is True
        assert parsed["polls"] == 8

    def test_output_is_indented_json(self) -> None:
        """IMPL-06: Output uses indent=2 (project convention)."""
        from aiyes.cli.presenter import format_wait_stable

        output = format_wait_stable(stable=True, timeout=False, polls=3)
        # indent=2 means lines start with spaces
        assert "\n  " in output


class TestWaitStableIntervalPassthrough:
    """F-01: Verify --interval parameter passes through to clock.sleep."""

    def test_interval_value_passed_to_clock_sleep(self) -> None:
        """TEST-06/AC-19: custom interval value used for sleep calls."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        n = _node("n_001", "frame", "Win")
        static_tree = _build_tree(n)
        tree_port = FakeAccessibilityTreeSequence([static_tree] * 5)
        session = _make_test_session("sess-interval")
        repo = FakeSessionRepository()
        repo._sessions["sess-interval"] = session
        clock = FakeClock(now_value=1000.0)

        uc = WaitStableUseCase(tree=tree_port, session_repo=repo, clock=clock)
        uc.execute("sess-interval", timeout=10.0, poll_interval=0.25, consecutive=3)

        sleep_values = [args for op, args in clock.calls if op == "sleep"]
        assert len(sleep_values) >= 3
        assert all(v == 0.25 for v in sleep_values), (
            f"Expected 0.25, got {sleep_values}"
        )


class TestWaitStableZeroInterval:
    """F-02: Verify poll_interval=0 skips sleep."""

    def test_zero_interval_skips_sleep(self) -> None:
        """IMPL-05: poll_interval <= 0 means no sleep between polls."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        n = _node("n_001", "frame", "Win")
        static_tree = _build_tree(n)
        tree_port = FakeAccessibilityTreeSequence([static_tree] * 5)
        session = _make_test_session("sess-zero")
        repo = FakeSessionRepository()
        repo._sessions["sess-zero"] = session
        clock = FakeClock(now_value=1000.0)

        uc = WaitStableUseCase(tree=tree_port, session_repo=repo, clock=clock)
        result = uc.execute("sess-zero", timeout=10.0, poll_interval=0, consecutive=3)

        assert result.stable is True
        sleep_calls = [op for op, _ in clock.calls if op == "sleep"]
        assert len(sleep_calls) == 0, f"Expected no sleeps, got {len(sleep_calls)}"


# ══════════════════════════════════════════════════════════════════════
# AIYES-38 Group B: Tolerance tests — AC-B01 through AC-B06
# ══════════════════════════════════════════════════════════════════════


class TestTreesStructurallyEqualTolerance:
    """R-38-10/R-38-11/R-38-12: tolerance parameter for trees_structurally_equal."""

    def test_tolerance_zero_preserves_existing_behavior_identical(self) -> None:
        """AC-B01: tolerance=0 on identical trees -> True (regression)."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(_node("n_002", "push_button", "OK"),),
            ),
        )
        tree_b = _build_tree(
            _node(
                "n_001",
                "frame",
                "Window",
                children=(_node("n_002", "push_button", "OK"),),
            ),
        )
        assert trees_structurally_equal(tree_a, tree_b, tolerance=0) is True

    def test_tolerance_zero_preserves_existing_behavior_different(self) -> None:
        """AC-B01: tolerance=0 on different trees -> False (regression)."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(_node("n_001", "frame", "Window"))
        tree_b = _build_tree(
            _node("n_001", "frame", "Window"),
            _node("n_002", "push_button", "OK"),
        )
        assert trees_structurally_equal(tree_a, tree_b, tolerance=0) is False

    def test_two_added_nodes_tolerance_boundary(self) -> None:
        """AC-B02: 2 added nodes — tolerance=1 False, tolerance=2 True, tolerance=3 True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(_node("n_001", "frame", "Window"))
        tree_b = _build_tree(
            _node("n_001", "frame", "Window"),
            _node("n_002", "push_button", "OK"),
            _node("n_003", "push_button", "Cancel"),
        )

        assert trees_structurally_equal(tree_a, tree_b, tolerance=1) is False
        assert trees_structurally_equal(tree_a, tree_b, tolerance=2) is True
        assert trees_structurally_equal(tree_a, tree_b, tolerance=3) is True

    def test_changed_name_tolerance(self) -> None:
        """AC-B03: 1 node with changed name — tolerance=0 False, tolerance=1 True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(_node("n_001", "push_button", "OK"))
        tree_b = _build_tree(_node("n_001", "push_button", "Apply"))

        assert trees_structurally_equal(tree_a, tree_b, tolerance=0) is False
        assert trees_structurally_equal(tree_a, tree_b, tolerance=1) is True

    def test_removed_plus_changed_role_tolerance(self) -> None:
        """AC-B04: 1 removed + 1 changed role — tolerance=1 False, tolerance=2 True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK"),
            _node("n_002", "label", "Info"),
        )
        tree_b = _build_tree(
            _node("n_001", "toggle_button", "OK"),
            # n_002 removed
        )

        assert trees_structurally_equal(tree_a, tree_b, tolerance=1) is False
        assert trees_structurally_equal(tree_a, tree_b, tolerance=2) is True


class TestWaitStableUseCaseTolerance:
    """R-38-13: WaitStableUseCase tolerance passthrough."""

    def test_tolerance_converges_with_minor_churn(self) -> None:
        """AC-B05: tolerance=3, churn <= 3 nodes per poll -> stable=True."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        # Trees differ by 2 nodes each time (within tolerance=3)
        base = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "label", "Static"),
        )
        churn_v1 = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "label", "Static"),
            _node("n_hot1", "label", "hot1"),
            _node("n_hot2", "label", "hot2"),
        )
        churn_v2 = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "label", "Static"),
            _node("n_hot3", "label", "hot3"),
        )
        # All differ by <=3 from each other (within tolerance)
        trees = [base, churn_v1, churn_v2, churn_v2, churn_v2]
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=30.0,
            poll_interval=0.1,
            consecutive=3,
            tolerance=3,
        )

        assert result.stable is True

    def test_default_tolerance_zero_regression(self) -> None:
        """AC-B06: default tolerance=0 behaves identically to current."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        static_tree = _build_tree(_node("n_001", "frame", "App"))
        tree_port = FakeAccessibilityTreeSequence([static_tree])

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=10.0,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is True
        assert result.polls == 4


# ══════════════════════════════════════════════════════════════════════
# AIYES-38 Group B: Subtree Masking tests — AC-B10 through AC-B14
# ══════════════════════════════════════════════════════════════════════


class TestTreesStructurallyEqualIgnoreIds:
    """R-38-20/R-38-21: ignore_ids parameter for trees_structurally_equal."""

    def test_ignored_subtree_excluded_from_comparison(self) -> None:
        """AC-B10: Only diffs in ignored subtree -> True."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node(
                "n_001",
                "frame",
                "App",
                children=(
                    _node(
                        "hot_reload_node",
                        "panel",
                        "HR",
                        children=(_node("hr_child1", "label", "v1"),),
                    ),
                    _node("n_002", "push_button", "OK"),
                ),
            ),
        )
        tree_b = _build_tree(
            _node(
                "n_001",
                "frame",
                "App",
                children=(
                    _node(
                        "hot_reload_node",
                        "panel",
                        "HR-changed",
                        children=(
                            _node("hr_child1", "label", "v2"),
                            _node("hr_child2", "label", "new"),
                        ),
                    ),
                    _node("n_002", "push_button", "OK"),
                ),
            ),
        )

        assert (
            trees_structurally_equal(
                tree_a, tree_b, ignore_ids=frozenset({"hot_reload_node"})
            )
            is True
        )

    def test_ignore_parent_ignores_all_descendants(self) -> None:
        """AC-B11: Ignoring parent excludes entire subtree."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node(
                "root",
                "frame",
                "App",
                children=(
                    _node(
                        "parent",
                        "panel",
                        "P",
                        children=(
                            _node("child1", "label", "A"),
                            _node(
                                "child2",
                                "label",
                                "B",
                                children=(_node("grandchild", "label", "C"),),
                            ),
                        ),
                    ),
                ),
            ),
        )
        tree_b = _build_tree(
            _node(
                "root",
                "frame",
                "App",
                children=(
                    _node(
                        "parent",
                        "panel",
                        "P-changed",
                        children=(
                            _node("child1", "label", "X"),
                            _node("new_child", "label", "Y"),
                        ),
                    ),
                ),
            ),
        )

        # Without ignore: trees differ
        assert trees_structurally_equal(tree_a, tree_b) is False
        # With ignore on parent: all descendants excluded
        assert (
            trees_structurally_equal(tree_a, tree_b, ignore_ids=frozenset({"parent"}))
            is True
        )

    def test_nonexistent_ignore_id_harmless(self) -> None:
        """AC-B12: Non-existent ID in ignore_ids has no effect."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(_node("n_001", "push_button", "OK"))
        tree_b = _build_tree(_node("n_001", "push_button", "OK"))

        assert (
            trees_structurally_equal(
                tree_a, tree_b, ignore_ids=frozenset({"no_such_id"})
            )
            is True
        )

    def test_empty_ignore_ids_regression(self) -> None:
        """AC-B14: Empty ignore_ids behaves identically to current."""
        from aiyes.domain.tree import trees_structurally_equal

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK"),
            _node("n_002", "label", "Info"),
        )
        tree_b = _build_tree(
            _node("n_001", "push_button", "OK"),
        )

        assert trees_structurally_equal(tree_a, tree_b, ignore_ids=frozenset()) is False


class TestWaitStableUseCaseIgnoreIds:
    """R-38-22: WaitStableUseCase ignore_ids passthrough."""

    def test_ignore_ids_excludes_churning_subtree(self) -> None:
        """AC-B13 domain-level: ignore_ids passed to comparison."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        # Trees differ only in the ignored subtree
        tree_v1 = _build_tree(
            _node(
                "n_001",
                "frame",
                "App",
                children=(
                    _node("stream", "label", "token1"),
                    _node("n_002", "push_button", "OK"),
                ),
            ),
        )
        tree_v2 = _build_tree(
            _node(
                "n_001",
                "frame",
                "App",
                children=(
                    _node("stream", "label", "token2"),
                    _node("n_002", "push_button", "OK"),
                ),
            ),
        )
        tree_v3 = _build_tree(
            _node(
                "n_001",
                "frame",
                "App",
                children=(
                    _node("stream", "label", "token3"),
                    _node("n_002", "push_button", "OK"),
                ),
            ),
        )
        trees = [tree_v1, tree_v2, tree_v3, tree_v3]
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=30.0,
            poll_interval=0.1,
            consecutive=3,
            ignore_ids=frozenset({"stream"}),
        )

        assert result.stable is True


class TestWaitStableCliIgnoreNode:
    """R-38-23: CLI --ignore-node option passthrough."""

    def test_ignore_node_cli_option_passes_through(self) -> None:
        """AC-B13: --ignore-node X --ignore-node Y passes frozenset to use case."""
        from click.testing import CliRunner

        from aiyes.cli.main import cli

        runner = CliRunner()

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="test-s"),
            patch("aiyes.cli.main.wait_stable_uc") as mock_uc,
            patch("aiyes.cli.main.format_wait_stable") as mock_fmt,
        ):
            from aiyes.domain.use_cases.wait_stable import WaitStableResult

            mock_uc.execute.return_value = WaitStableResult(stable=True, polls=4)
            mock_fmt.return_value = json.dumps(
                {"stable": True, "timeout": False, "polls": 4}, indent=2
            )

            result = runner.invoke(
                cli,
                [
                    "wait-stable",
                    "--session",
                    "test-s",
                    "--ignore-node",
                    "hot_reload_1",
                    "--ignore-node",
                    "vm_service_2",
                ],
            )

            assert result.exit_code == 0
            call_kwargs = mock_uc.execute.call_args
            assert call_kwargs is not None
            passed_ids = (
                call_kwargs.kwargs.get(
                    "ignore_ids", call_kwargs[1].get("ignore_ids", None)
                )
                if call_kwargs.kwargs
                else None
            )
            if passed_ids is None and len(call_kwargs) > 1:
                passed_ids = call_kwargs[1].get("ignore_ids")
            assert passed_ids == frozenset({"hot_reload_1", "vm_service_2"})

    def test_tolerance_cli_option_passes_through(self) -> None:
        """AC-B05 CLI: --tolerance 3 passes integer to use case."""
        from click.testing import CliRunner

        from aiyes.cli.main import cli

        runner = CliRunner()

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="test-s"),
            patch("aiyes.cli.main.wait_stable_uc") as mock_uc,
            patch("aiyes.cli.main.format_wait_stable") as mock_fmt,
        ):
            from aiyes.domain.use_cases.wait_stable import WaitStableResult

            mock_uc.execute.return_value = WaitStableResult(stable=True, polls=4)
            mock_fmt.return_value = json.dumps(
                {"stable": True, "timeout": False, "polls": 4}, indent=2
            )

            result = runner.invoke(
                cli,
                ["wait-stable", "--session", "test-s", "--tolerance", "3"],
            )

            assert result.exit_code == 0
            call_kwargs = mock_uc.execute.call_args
            assert call_kwargs is not None
            # Check tolerance was passed
            if call_kwargs.kwargs:
                assert call_kwargs.kwargs.get("tolerance") == 3
            else:
                assert call_kwargs[1].get("tolerance") == 3


# ══════════════════════════════════════════════════════════════════════
# AIYES-38 Group B: Diagnostic Output tests — AC-B20 through AC-B23
# ══════════════════════════════════════════════════════════════════════


class TestComputeTreeDiff:
    """compute_tree_diff() function tests."""

    def test_identical_trees_empty_diff(self) -> None:
        """Identical trees produce empty diff tuple."""
        from aiyes.domain.tree import compute_tree_diff

        tree_a = _build_tree(_node("n_001", "push_button", "OK"))
        tree_b = _build_tree(_node("n_001", "push_button", "OK"))

        diffs = compute_tree_diff(tree_a, tree_b)
        assert diffs == ()

    def test_added_node_diff(self) -> None:
        """Added node produces type='added' entry."""
        from aiyes.domain.tree import TreeDiff, compute_tree_diff

        tree_a = _build_tree(_node("n_001", "frame", "App"))
        tree_b = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "push_button", "OK"),
        )

        diffs = compute_tree_diff(tree_a, tree_b)
        assert len(diffs) == 1
        assert diffs[0].type == "added"
        assert diffs[0].node_id == "n_002"

    def test_removed_node_diff(self) -> None:
        """Removed node produces type='removed' entry."""
        from aiyes.domain.tree import TreeDiff, compute_tree_diff

        tree_a = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "push_button", "OK"),
        )
        tree_b = _build_tree(_node("n_001", "frame", "App"))

        diffs = compute_tree_diff(tree_a, tree_b)
        assert len(diffs) == 1
        assert diffs[0].type == "removed"
        assert diffs[0].node_id == "n_002"

    def test_changed_role_diff(self) -> None:
        """Changed role produces type='changed', field='role'."""
        from aiyes.domain.tree import TreeDiff, compute_tree_diff

        tree_a = _build_tree(_node("n_001", "push_button", "OK"))
        tree_b = _build_tree(_node("n_001", "toggle_button", "OK"))

        diffs = compute_tree_diff(tree_a, tree_b)
        assert len(diffs) == 1
        assert diffs[0].type == "changed"
        assert diffs[0].node_id == "n_001"
        assert diffs[0].field == "role"
        assert diffs[0].old == "push_button"
        assert diffs[0].new == "toggle_button"

    def test_changed_name_diff(self) -> None:
        """Changed name produces type='changed', field='name'."""
        from aiyes.domain.tree import TreeDiff, compute_tree_diff

        tree_a = _build_tree(_node("n_001", "push_button", "OK"))
        tree_b = _build_tree(_node("n_001", "push_button", "Apply"))

        diffs = compute_tree_diff(tree_a, tree_b)
        assert len(diffs) == 1
        assert diffs[0].type == "changed"
        assert diffs[0].node_id == "n_001"
        assert diffs[0].field == "name"
        assert diffs[0].old == "OK"
        assert diffs[0].new == "Apply"

    def test_diff_respects_ignore_ids(self) -> None:
        """compute_tree_diff ignores subtrees in ignore_ids."""
        from aiyes.domain.tree import compute_tree_diff

        tree_a = _build_tree(
            _node(
                "n_001",
                "frame",
                "App",
                children=(
                    _node("ignored", "label", "old"),
                    _node("n_002", "push_button", "OK"),
                ),
            ),
        )
        tree_b = _build_tree(
            _node(
                "n_001",
                "frame",
                "App",
                children=(
                    _node("ignored", "label", "new"),
                    _node("n_002", "push_button", "OK"),
                ),
            ),
        )

        diffs = compute_tree_diff(tree_a, tree_b, ignore_ids=frozenset({"ignored"}))
        assert diffs == ()

    def test_diff_both_role_and_name_changed(self) -> None:
        """Node with both role and name changed produces two diff entries."""
        from aiyes.domain.tree import compute_tree_diff

        tree_a = _build_tree(_node("n_001", "push_button", "OK"))
        tree_b = _build_tree(_node("n_001", "toggle_button", "Apply"))

        diffs = compute_tree_diff(tree_a, tree_b)
        assert len(diffs) == 2
        types_fields = {(d.type, d.field) for d in diffs}
        assert ("changed", "role") in types_fields
        assert ("changed", "name") in types_fields


class TestTreeDiff:
    """TreeDiff dataclass structure tests."""

    def test_treediff_is_frozen_dataclass(self) -> None:
        """TreeDiff must be a frozen dataclass."""
        from aiyes.domain.tree import TreeDiff

        assert dataclasses.is_dataclass(TreeDiff)
        diff = TreeDiff(type="added", node_id="n_001")
        with pytest.raises(dataclasses.FrozenInstanceError):
            diff.type = "removed"  # type: ignore[misc]

    def test_treediff_default_fields(self) -> None:
        """TreeDiff: field, old, new default to None."""
        from aiyes.domain.tree import TreeDiff

        diff = TreeDiff(type="added", node_id="n_001")
        assert diff.field is None
        assert diff.old is None
        assert diff.new is None


class TestWaitStableResultChanges:
    """R-38-32: WaitStableResult carries changes field."""

    def test_result_has_changes_field_default_empty(self) -> None:
        """changes field defaults to empty tuple."""
        from aiyes.domain.use_cases.wait_stable import WaitStableResult

        result = WaitStableResult(stable=True)
        assert result.changes == ()

    def test_result_carries_changes_on_timeout(self) -> None:
        """changes field can hold diff dicts."""
        from aiyes.domain.use_cases.wait_stable import WaitStableResult

        changes = (
            {"type": "added", "node_id": "n_002"},
            {"type": "removed", "node_id": "n_003"},
        )
        result = WaitStableResult(stable=False, timeout=True, polls=5, changes=changes)
        assert result.changes == changes
        assert len(result.changes) == 2


class TestWaitStableUseCaseDiagnostics:
    """R-38-30: Diagnostic output on timeout."""

    def test_timeout_populates_changes(self) -> None:
        """AC-B20: Timeout result includes changes from final comparison."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        # Continuously changing trees (never stable)
        trees = [
            _build_tree(_node(f"n_{i:03d}", "label", f"Label {i}")) for i in range(20)
        ]
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=0.5,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is False
        assert result.timeout is True
        assert len(result.changes) > 0
        # Each change entry must have 'type' and 'node_id'
        for change in result.changes:
            assert "type" in change
            assert "node_id" in change

    def test_stable_result_has_no_changes(self) -> None:
        """AC-B22: Stable result has empty changes."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        static_tree = _build_tree(_node("n_001", "frame", "App"))
        tree_port = FakeAccessibilityTreeSequence([static_tree])

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=10.0,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is True
        assert result.changes == ()

    def test_changes_show_full_diff_even_within_tolerance(self) -> None:
        """AC-B23: 5 added nodes, tolerance=3 -> changes shows all 5."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        tree_a = _build_tree(_node("n_001", "frame", "App"))
        tree_b = _build_tree(
            _node("n_001", "frame", "App"),
            _node("n_002", "label", "A"),
            _node("n_003", "label", "B"),
            _node("n_004", "label", "C"),
            _node("n_005", "label", "D"),
            _node("n_006", "label", "E"),
        )
        # a, b alternating -> never actually stable, times out
        trees = [tree_a, tree_b, tree_a, tree_b, tree_a, tree_b] * 5
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=0.5,
            poll_interval=0.1,
            consecutive=3,
            tolerance=3,
        )

        assert result.stable is False
        assert result.timeout is True
        # The changes array should show all 5 added nodes
        added_changes = [c for c in result.changes if c["type"] in ("added", "removed")]
        assert len(added_changes) == 5

    def test_change_entries_have_correct_structure(self) -> None:
        """AC-B21: Each change entry has type, node_id, and for changed: field/old/new."""
        from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

        session = _make_test_session()
        repo = FakeSessionRepository()
        repo.save(session)
        clock = FakeClock(now_value=1000.0)

        tree_a = _build_tree(
            _node("n_001", "push_button", "OK"),
            _node("n_002", "label", "Info"),
        )
        tree_b = _build_tree(
            _node("n_001", "toggle_button", "Apply"),
            _node("n_003", "label", "New"),
        )
        # Alternate so it never stabilizes
        trees = [tree_a, tree_b, tree_a, tree_b, tree_a, tree_b] * 5
        tree_port = FakeAccessibilityTreeSequence(trees)

        uc = WaitStableUseCase(
            tree=tree_port,
            session_repo=repo,
            clock=clock,
        )
        result = uc.execute(
            session_id="test-s",
            timeout=0.5,
            poll_interval=0.1,
            consecutive=3,
        )

        assert result.stable is False
        assert len(result.changes) > 0

        for change in result.changes:
            assert "type" in change
            assert change["type"] in ("added", "removed", "changed")
            assert "node_id" in change
            if change["type"] == "changed":
                assert "field" in change
                assert "old" in change
                assert "new" in change


# ══════════════════════════════════════════════════════════════════════
# AIYES-38 Group B: Presenter diagnostic output — AC-B20/AC-B22/AC-B31
# ══════════════════════════════════════════════════════════════════════


class TestFormatWaitStableChanges:
    """R-38-31: format_wait_stable includes changes conditionally."""

    def test_changes_included_when_not_stable(self) -> None:
        """AC-B20: Timeout output contains changes array."""
        from aiyes.cli.presenter import format_wait_stable

        changes = (
            {"type": "added", "node_id": "n_002"},
            {
                "type": "changed",
                "node_id": "n_001",
                "field": "name",
                "old": "OK",
                "new": "Apply",
            },
        )
        output = format_wait_stable(
            stable=False,
            timeout=True,
            polls=5,
            changes=changes,
        )
        parsed = json.loads(output)

        assert "changes" in parsed
        assert len(parsed["changes"]) == 2
        assert parsed["changes"][0]["type"] == "added"
        assert parsed["changes"][1]["field"] == "name"

    def test_no_changes_when_stable(self) -> None:
        """AC-B22: Stable output has no changes field."""
        from aiyes.cli.presenter import format_wait_stable

        output = format_wait_stable(stable=True, timeout=False, polls=4)
        parsed = json.loads(output)

        assert "changes" not in parsed

    def test_no_changes_field_when_empty_and_not_stable(self) -> None:
        """R-38-31: Empty changes on not-stable -> no changes field."""
        from aiyes.cli.presenter import format_wait_stable

        output = format_wait_stable(
            stable=False,
            timeout=True,
            polls=5,
            changes=(),
        )
        parsed = json.loads(output)

        assert "changes" not in parsed
