"""AIYES-12 — Wait-for-Absent (R-TDDV6-03).

Tests for the `absent` parameter on WaitUseCase.execute() and the
CLI `--absent` flag on the `wait` command.

Traceability:
  AC-01: absent=True, node not present -> immediate return, found=False, timeout=False
  AC-02: absent=True, node present throughout -> found=True, timeout=True
  AC-03: absent=True, node disappears mid-poll -> found=False, timeout=False
  AC-04: absent=False (default) regression — existing behavior unchanged
  AC-05: absent=True, role not in tree -> immediate return
  AC-06: absent=True -> id always None
  AC-07: absent=True with state filter, role match but state mismatch -> absent satisfied
  AC-08: CLI --absent flag in help output
  AC-09: CLI --absent, no match -> JSON found=false, exit 0
  AC-10: CLI --absent, match + timeout -> JSON found=true timeout=true, exit 0
  AC-11: CLI without --absent retains existing behavior (regression)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import patch

from click.testing import CliRunner

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.use_cases.wait import WaitResult, WaitUseCase

from tests.conftest import (
    FakeAccessibilityTree,
    FakeClock,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
    make_tree,
)


# ──────────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────────


def _make_session() -> Session:
    """Create a minimal Session for wait tests."""
    return Session(
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
    )


class SequenceFakeTree:
    """Fake AccessibilityTreePort that returns different trees per call.

    Used for AC-03: node present on first polls, absent on later polls.
    """

    def __init__(self, trees: List[AccessibilityTree]) -> None:
        self._trees = trees
        self._call_index = 0
        self.calls: List[Tuple[str, Any]] = []

    def get_tree(self, session) -> AccessibilityTree:
        self.calls.append(("get_tree", session))
        tree = self._trees[min(self._call_index, len(self._trees) - 1)]
        self._call_index += 1
        return tree


def _build_use_case(
    tree_port: Any,
    session_repo: FakeSessionRepository,
    clock: FakeClock,
) -> WaitUseCase:
    """Wire a WaitUseCase with fakes."""
    tree_store = FakeTreeStore()
    return WaitUseCase(
        tree=tree_port,
        session_repo=session_repo,
        tree_store=tree_store,
        clock=clock,
    )


def _tree_with_button() -> FakeAccessibilityTree:
    """Tree containing a push_button named OK with states [enabled, visible]."""
    return FakeAccessibilityTree(tree=make_tree())


def _empty_tree() -> FakeAccessibilityTree:
    """Tree with no nodes."""
    return FakeAccessibilityTree(tree=make_tree(nodes=[]))


def _tree_with_only_frame() -> FakeAccessibilityTree:
    """Tree with a frame node but no push_button."""
    return FakeAccessibilityTree(
        tree=make_tree(nodes=[make_node("n_frame", "frame", "Window")])
    )


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-03.1: WaitUseCase.execute with absent parameter
# ══════════════════════════════════════════════════════════════════════


class TestWaitAbsentNodeNotPresent:
    """AC-01: absent=True, node not present -> immediate return."""

    def test_returns_found_false(self) -> None:
        """absent=True with no matching node: found=False."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_empty_tree(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=5.0,
            absent=True,
        )

        assert result.found is False

    def test_returns_timeout_false(self) -> None:
        """absent=True with no matching node: timeout=False (no timeout occurred)."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_empty_tree(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=5.0,
            absent=True,
        )

        assert result.timeout is False

    def test_no_sleep_calls(self) -> None:
        """AC-01/TEST-03: Immediate return means zero sleep calls."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_empty_tree(), repo, clock)
        uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=5.0,
            absent=True,
        )

        assert len(clock.sleep_calls) == 0

    def test_id_is_none(self) -> None:
        """AC-06: In absent mode, id is always None."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_empty_tree(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=5.0,
            absent=True,
        )

        assert result.id is None


class TestWaitAbsentNodePresent:
    """AC-02: absent=True, node present throughout -> timeout."""

    def test_returns_found_true(self) -> None:
        """Node still present at timeout: found=True (reports final poll state)."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=1.0,
            absent=True,
        )

        assert result.found is True

    def test_returns_timeout_true(self) -> None:
        """Node still present: timeout=True (timeout was the termination reason)."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=1.0,
            absent=True,
        )

        assert result.timeout is True

    def test_id_is_none_on_timeout(self) -> None:
        """AC-06: Even on timeout, absent mode returns id=None."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=1.0,
            absent=True,
        )

        assert result.id is None

    def test_sleep_calls_occurred(self) -> None:
        """Polling happened: at least one sleep call before timeout."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=1.0,
            absent=True,
        )

        assert len(clock.sleep_calls) >= 1


class TestWaitAbsentNodeDisappears:
    """AC-03: absent=True, node present initially, disappears mid-poll."""

    def _make_sequence_tree(self) -> SequenceFakeTree:
        """First 2 polls: button present. Then: empty tree."""
        from aiyes.domain.tree import raw_tree_to_domain

        tree_with = raw_tree_to_domain(make_tree())
        tree_without = raw_tree_to_domain(make_tree(nodes=[]))
        return SequenceFakeTree([tree_with, tree_with, tree_without])

    def test_returns_found_false(self) -> None:
        """Node disappeared: found=False."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(self._make_sequence_tree(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=10.0,
            absent=True,
        )

        assert result.found is False

    def test_returns_timeout_false(self) -> None:
        """Node disappeared before timeout: timeout=False."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(self._make_sequence_tree(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=10.0,
            absent=True,
        )

        assert result.timeout is False

    def test_id_is_none(self) -> None:
        """AC-06: id=None when node disappeared."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(self._make_sequence_tree(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=10.0,
            absent=True,
        )

        assert result.id is None

    def test_sleep_calls_before_disappearance(self) -> None:
        """Polling occurred: sleep was called while node was present."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        seq = self._make_sequence_tree()
        uc = _build_use_case(seq, repo, clock)
        uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=10.0,
            absent=True,
        )

        # At least 2 sleeps (node present on first 2 polls, gone on 3rd)
        assert len(clock.sleep_calls) >= 2

    def test_poll_count(self) -> None:
        """Verify get_tree was called exactly 3 times (2 present + 1 absent)."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        seq = self._make_sequence_tree()
        uc = _build_use_case(seq, repo, clock)
        uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=10.0,
            absent=True,
        )

        get_tree_calls = [c for c in seq.calls if c[0] == "get_tree"]
        assert len(get_tree_calls) == 3


class TestWaitAbsentFalseRegression:
    """AC-04/TEST-02: absent=False (default) retains existing behavior."""

    def test_node_found_returns_found_true(self) -> None:
        """Default mode: node found -> found=True."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
        )

        assert result.found is True
        assert result.timeout is False

    def test_node_found_returns_id(self) -> None:
        """Default mode: found node has an id."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
        )

        assert result.id is not None

    def test_timeout_returns_found_false(self) -> None:
        """Default mode: timeout -> found=False, timeout=True."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_empty_tree(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=1.0,
        )

        assert result.found is False
        assert result.timeout is True

    def test_absent_false_explicit_same_as_default(self) -> None:
        """Passing absent=False explicitly gives same result as omitting it."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            absent=False,
        )

        assert result.found is True
        assert result.timeout is False
        assert result.id is not None


class TestWaitAbsentRoleNotInTree:
    """AC-05/TEST-03: absent=True, role not in tree -> immediate return."""

    def test_returns_found_false(self) -> None:
        """Role does not exist in tree at all: found=False."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        # Tree has frame nodes but no push_button
        uc = _build_use_case(_tree_with_only_frame(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            timeout=5.0,
            absent=True,
        )

        assert result.found is False
        assert result.timeout is False

    def test_no_sleep_calls(self) -> None:
        """Immediate return: zero sleep calls."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_only_frame(), repo, clock)
        uc.execute(
            session_id="test-s",
            role="push_button",
            timeout=5.0,
            absent=True,
        )

        assert len(clock.sleep_calls) == 0


class TestWaitAbsentStateFilter:
    """AC-07/TEST-04: absent=True with state filter, role matches but state doesn't."""

    def test_state_mismatch_means_absent_satisfied(self) -> None:
        """Node matches role but not state -> filtered set is empty -> absent success."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        # Tree has push_button with states ["enabled", "visible"]
        uc = _build_use_case(_tree_with_button(), repo, clock)
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            state="disabled",  # Node has "enabled", not "disabled"
            timeout=5.0,
            absent=True,
        )

        assert result.found is False
        assert result.timeout is False

    def test_state_mismatch_no_sleep(self) -> None:
        """State filter yields empty set on first poll: immediate return."""
        repo = FakeSessionRepository()
        repo.save(_make_session())
        clock = FakeClock()

        uc = _build_use_case(_tree_with_button(), repo, clock)
        uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            state="disabled",
            timeout=5.0,
            absent=True,
        )

        assert len(clock.sleep_calls) == 0


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-03.2: CLI --absent flag
# ══════════════════════════════════════════════════════════════════════


class TestWaitAbsentCliHelp:
    """AC-08/TEST-05: --absent flag appears in CLI help."""

    def test_absent_in_help(self) -> None:
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["wait", "--help"])
        assert result.exit_code == 0
        assert "--absent" in result.output


class TestWaitAbsentCliNoMatch:
    """AC-09/TEST-05/TEST-06: CLI --absent with non-existent node."""

    def test_json_found_false_exit_0(self) -> None:
        """--absent with no matching node: JSON found=false, exit 0."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = WaitResult(
                found=False, timeout=False, id=None
            )

            result = runner.invoke(
                cli,
                ["wait", "--session", "s1", "--absent", "push_button", "OK"],
            )

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["found"] is False
            assert parsed["timeout"] is False

    def test_absent_passed_to_use_case(self) -> None:
        """--absent flag passes absent=True to execute()."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = WaitResult(
                found=False, timeout=False, id=None
            )

            runner.invoke(
                cli,
                ["wait", "--session", "s1", "--absent", "push_button", "OK"],
            )

            call_kwargs = mock_uc.execute.call_args
            assert call_kwargs is not None
            # Check absent=True was passed
            if call_kwargs.kwargs:
                assert call_kwargs.kwargs.get("absent") is True
            else:
                # Positional — absent should be in the call
                assert "absent" in str(call_kwargs)


class TestWaitAbsentCliTimeout:
    """AC-10/TEST-05/TEST-06: CLI --absent with matching node -> timeout."""

    def test_json_found_true_timeout_exit_0(self) -> None:
        """--absent with node present throughout: JSON found=true timeout=true, exit 0."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = WaitResult(found=True, timeout=True, id=None)

            result = runner.invoke(
                cli,
                [
                    "wait",
                    "--session",
                    "s1",
                    "--absent",
                    "--timeout",
                    "1",
                    "push_button",
                    "OK",
                ],
            )

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["found"] is True
            assert parsed["timeout"] is True


class TestWaitCliWithoutAbsentRegression:
    """AC-11/TEST-02: CLI without --absent retains existing behavior."""

    def test_no_absent_passes_default(self) -> None:
        """Without --absent, absent is not passed (or defaults to False)."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = WaitResult(
                found=True, timeout=False, id="n_002"
            )

            result = runner.invoke(
                cli,
                ["wait", "--session", "s1", "push_button", "OK"],
            )

            assert result.exit_code == 0
            call_kwargs = mock_uc.execute.call_args
            assert call_kwargs is not None
            # absent should be False or not passed
            if call_kwargs.kwargs and "absent" in call_kwargs.kwargs:
                assert call_kwargs.kwargs["absent"] is False

    def test_json_output_includes_id(self) -> None:
        """Normal mode with found node includes id in JSON."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.wait_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = WaitResult(
                found=True, timeout=False, id="n_002"
            )

            result = runner.invoke(
                cli,
                ["wait", "--session", "s1", "push_button", "OK"],
            )

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed["found"] is True
            assert parsed["id"] == "n_002"


# ══════════════════════════════════════════════════════════════════════
# Architecture tests
# ══════════════════════════════════════════════════════════════════════


class TestWaitAbsentArchitecture:
    """ARCH-01/ARCH-02: wait.py remains stdlib+domain+ports only."""

    def test_wait_py_no_new_imports(self) -> None:
        """wait.py uses only stdlib, domain, and ports imports."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)
        allowed_prefixes = ("aiyes.domain.", "aiyes.ports.")

        source = Path("src/aiyes/domain/use_cases/wait.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules:
                    continue
                if top == "__future__":
                    continue
                is_allowed = any(node.module.startswith(p) for p in allowed_prefixes)
                assert is_allowed, f"wait.py has disallowed import: {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in stdlib_modules or top == "__future__", (
                        f"wait.py has disallowed import: {alias.name}"
                    )

    def test_wait_result_fields(self) -> None:
        """ARCH-02: WaitResult fields are found, timeout, id, transient."""
        import dataclasses

        fields = {f.name: f.type for f in dataclasses.fields(WaitResult)}
        assert set(fields.keys()) == {"found", "timeout", "id", "transient"}
        assert fields["found"] == "bool"
