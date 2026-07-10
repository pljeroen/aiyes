"""Tests for AIYES-110 — most-recent-session fallback for omitted session_id.

CT-02 EVOLUTION. When session_id is omitted AND more than one active session
exists, ``SessionResolveUseCase.execute()`` must select the most-recent active
session — ``max`` over the total order ``(started_at, session_id)`` — instead of
raising "Multiple sessions found". Boundaries (n==0, n==1, explicit id) are
preserved verbatim, and ``SessionStopUseCase`` DELIBERATELY keeps its
raise-on->1 behavior (operator decision OD-110-1 / constraint C5): auto-selecting
a session to STOP is destructive (INV-UX-03).

Coverage matrix (VALIDATED_INTENT_PKG.must_tier1_coverage_matrix):

  R1/C1  most-recent selection on n>1                    RED until A9
  R3     tie-break by session_id                          RED until A9
  R3     order-invariance (reversed load_all)             RED until A9
  R3     inactive-newer excluded from candidate pool      RED until A9
  C6     fallback returns a concrete session_id str       RED until A9
  R2/C4  explicit id never overridden by fallback         preservation (GREEN)
  R2/C2  single active session unchanged                  preservation (GREEN)
  R2/C3  zero active sessions still raises                preservation (GREEN)
  C7     domain purity (no adapter/third-party import)    structural (GREEN)
  C5     session_stop still raises on >1                  PINNING (GREEN, stays)

The five RED tests fail today because ``execute(None)`` on the n>1 path raises
``RuntimeError("Multiple sessions found, ...")`` — the call fails before the
assertion is reached. After A9 replaces that branch with the ``max`` selection,
they turn GREEN. The preservation / structural / pinning tests pass today and
MUST remain green after A9.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_resolve import SessionResolveUseCase
from aiyes.domain.use_cases.session_stop import SessionStopUseCase

from tests.conftest import (
    FakeAccessibilityBus,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_session(
    session_id: str,
    started_at: float,
    app_pid: int,
    xvfb_pid: int,
    **overrides: Any,
) -> Session:
    """Build a linux-backend Session with a controlled ``started_at``."""
    defaults = dict(
        session_id=session_id,
        display=":99",
        app_pid=app_pid,
        app_command="gedit",
        app_args=(),
        atspi_bus_pid=app_pid + 1,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=xvfb_pid,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=started_at,
    )
    defaults.update(overrides)
    return Session(**defaults)


def _register_active(
    repo: FakeSessionRepository,
    process: FakeProcess,
    session: Session,
) -> Session:
    """Persist ``session`` and mark its linux pids running (=> active)."""
    repo.save(session)
    process._running[session.app_pid] = True
    process._running[session.xvfb_pid] = True
    return session


def _load_all_count(repo: FakeSessionRepository) -> int:
    """Number of ``load_all()`` invocations recorded by the fake repo."""
    return len([c for c in repo.calls if c[0] == "load_all"])


# ═══════════════════════════════════════════════════════════════════════
# R1 / C1 — most-recent selection on >1 active sessions (RED until A9)
# ═══════════════════════════════════════════════════════════════════════


class TestMostRecentSelectionOnMultipleActive:
    """R1 / C1 — fallback returns the greatest-started_at session, no raise."""

    def test_most_recent_by_started_at_selected_on_multiple_active(self) -> None:
        """sid=None, n>1, distinct started_at -> newest session_id, no raise.

        RED: today execute(None) raises "Multiple sessions found"; the call
        fails before the assert. After A9 it returns "newer".
        """
        repo = FakeSessionRepository()
        process = FakeProcess()
        _register_active(
            repo, process, _make_session("older", 100.0, app_pid=100, xvfb_pid=200)
        )
        _register_active(
            repo, process, _make_session("newer", 900.0, app_pid=300, xvfb_pid=400)
        )

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        resolved = uc.execute(session_id=None)

        assert resolved == "newer"
        # C7 floor: the fallback consults the repository exactly once — no new
        # I/O, no new port surface, no per-candidate re-load.
        assert _load_all_count(repo) == 1


# ═══════════════════════════════════════════════════════════════════════
# R3 — tie-break, order-invariance, liveness-before-recency (RED until A9)
# ═══════════════════════════════════════════════════════════════════════


class TestTieBreakAndOrderInvariance:
    """R3 — deterministic tie-break by session_id, order-invariant selection."""

    def test_tie_break_deterministic_by_session_id(self) -> None:
        """Equal started_at -> lexicographically-greatest session_id wins.

        RED until A9 (execute raises on n>1). ``max((started_at, session_id))``
        over equal timestamps selects "sess-bbb" > "sess-aaa".
        """
        repo = FakeSessionRepository()
        process = FakeProcess()
        _register_active(
            repo, process, _make_session("sess-aaa", 500.0, app_pid=100, xvfb_pid=200)
        )
        _register_active(
            repo, process, _make_session("sess-bbb", 500.0, app_pid=300, xvfb_pid=400)
        )

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        assert uc.execute(session_id=None) == "sess-bbb"

    def test_selection_invariant_under_load_all_order(self) -> None:
        """Same sessions in reversed load_all() order -> identical result.

        Proves the selection is a pure function of field values, NOT of
        load_all()/dict insertion order. RED until A9 (both orders raise today).
        """
        s_older = _make_session("alpha", 100.0, app_pid=100, xvfb_pid=200)
        s_newer = _make_session("bravo", 900.0, app_pid=300, xvfb_pid=400)

        repo_fwd = FakeSessionRepository()
        proc_fwd = FakeProcess()
        _register_active(repo_fwd, proc_fwd, s_older)
        _register_active(repo_fwd, proc_fwd, s_newer)

        repo_rev = FakeSessionRepository()
        proc_rev = FakeProcess()
        _register_active(repo_rev, proc_rev, s_newer)
        _register_active(repo_rev, proc_rev, s_older)

        uc_fwd = SessionResolveUseCase(session_repo=repo_fwd, process=proc_fwd)
        uc_rev = SessionResolveUseCase(session_repo=repo_rev, process=proc_rev)

        forward = uc_fwd.execute(session_id=None)
        reverse = uc_rev.execute(session_id=None)

        assert forward == reverse == "bravo"

    def test_inactive_newer_session_excluded_from_candidate_pool(self) -> None:
        """Liveness filter runs BEFORE recency: a newer INACTIVE session is
        not selected.

        Two active sessions plus a third with a much newer started_at but dead
        pids. Correct behavior selects the newest ACTIVE session, never the
        newer inactive one. RED until A9 (2 active -> raises today).
        """
        repo = FakeSessionRepository()
        process = FakeProcess()
        _register_active(
            repo, process, _make_session("active-old", 100.0, app_pid=100, xvfb_pid=200)
        )
        _register_active(
            repo, process, _make_session("active-mid", 200.0, app_pid=300, xvfb_pid=400)
        )
        # Saved but NOT marked running -> is_session_active() is False.
        repo.save(_make_session("inactive-new", 9999.0, app_pid=500, xvfb_pid=600))

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        resolved = uc.execute(session_id=None)

        assert resolved == "active-mid"
        assert resolved != "inactive-new"


# ═══════════════════════════════════════════════════════════════════════
# C6 mandatory floor — concrete session_id str returned (RED until A9)
# ═══════════════════════════════════════════════════════════════════════


class TestObservabilityFloor:
    """R4 / C6 mandatory floor — WHICH session was auto-selected is
    recoverable from the return value (a concrete str)."""

    def test_fallback_returns_concrete_session_id_str(self) -> None:
        """Fallback return is a str equal to the selected session_id — never
        None, never a bool/sentinel/'ok'.

        RED until A9 (raises today).
        """
        repo = FakeSessionRepository()
        process = FakeProcess()
        _register_active(
            repo, process, _make_session("cand-old", 100.0, app_pid=100, xvfb_pid=200)
        )
        _register_active(
            repo, process, _make_session("cand-new", 900.0, app_pid=300, xvfb_pid=400)
        )

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        resolved = uc.execute(session_id=None)

        assert isinstance(resolved, str)
        assert resolved == "cand-new"
        assert resolved in {"cand-old", "cand-new"}


# ═══════════════════════════════════════════════════════════════════════
# R2 / C4 — explicit session_id precedence (preservation, GREEN)
# ═══════════════════════════════════════════════════════════════════════


class TestExplicitSessionIdPrecedence:
    """R2 / C4 — an explicit session_id is returned verbatim and is NEVER
    overridden by the most-recent fallback."""

    def test_explicit_session_id_never_overridden_by_fallback(self) -> None:
        """Explicit id with >1 active and id NOT among them -> returned verbatim,
        load_all() invoked ZERO times.

        Passes today (short-circuit at line 42-43) and MUST stay green: the
        fallback must never override an explicit id.
        """
        repo = FakeSessionRepository()
        process = FakeProcess()
        _register_active(
            repo, process, _make_session("active-a", 100.0, app_pid=100, xvfb_pid=200)
        )
        _register_active(
            repo, process, _make_session("active-b", 900.0, app_pid=300, xvfb_pid=400)
        )

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        resolved = uc.execute(session_id="explicit-xyz")

        assert resolved == "explicit-xyz"
        # C4: the explicit short-circuit consults the repository zero times.
        assert _load_all_count(repo) == 0


# ═══════════════════════════════════════════════════════════════════════
# R2 / C2, C3 — n==1 and n==0 boundaries unchanged (preservation, GREEN)
# ═══════════════════════════════════════════════════════════════════════


class TestBoundariesUnchanged:
    """R2 / C2, C3 — the single-active and zero-active paths are preserved."""

    def test_single_active_session_unchanged(self) -> None:
        """sid=None, n==1 -> the sole active session_id (unchanged baseline)."""
        repo = FakeSessionRepository()
        process = FakeProcess()
        _register_active(
            repo, process, _make_session("solo", 100.0, app_pid=100, xvfb_pid=200)
        )

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        assert uc.execute(session_id=None) == "solo"

    def test_zero_active_sessions_still_raises(self) -> None:
        """sid=None, n==0 -> RuntimeError beginning 'No active sessions found.'
        (type and message unchanged)."""
        repo = FakeSessionRepository()
        process = FakeProcess()

        uc = SessionResolveUseCase(session_repo=repo, process=process)

        with pytest.raises(RuntimeError) as exc_info:
            uc.execute(session_id=None)

        assert str(exc_info.value).startswith("No active sessions found.")


# ═══════════════════════════════════════════════════════════════════════
# C7 — domain purity (Rule 16): no adapter/third-party import (GREEN)
# ═══════════════════════════════════════════════════════════════════════


class TestDomainPurity:
    """C7 — session_resolve.py stays domain-pure; the only new import A9 may
    add is the intra-domain Session type."""

    def test_session_resolve_no_adapter_or_thirdparty_import(self) -> None:
        """No aiyes.adapters / aiyes.infrastructure import in session_resolve.py;
        every intra-project import is under aiyes.domain or aiyes.ports.

        Structural guard (Rule 16 / C7). GREEN now and MUST stay green after A9.
        """
        import aiyes.domain.use_cases.session_resolve as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        offending: list[str] = []
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Absolute (level 0) intra-project imports only.
                if node.module is not None and node.level == 0:
                    modules = [node.module]
            for mod in modules:
                if mod.startswith("aiyes.") and not (
                    mod.startswith("aiyes.domain") or mod.startswith("aiyes.ports")
                ):
                    offending.append(mod)

        assert offending == [], (
            "session_resolve.py must stay domain-pure (Rule 16 / C7): "
            f"non-domain/non-port aiyes imports found: {offending}"
        )


# ═══════════════════════════════════════════════════════════════════════
# C5 / OD-110-1 — session_stop DELIBERATELY keeps raise-on->1 (PINNING, GREEN)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionStopPinning:
    """C5 / OD-110-1 — PINNING GUARD (NOT a RED behavior test).

    Passes today and MUST STAY GREEN. AIYES-110's most-recent fallback is
    scoped to session_resolve.py ONLY. Stopping a session is destructive
    (INV-UX-03), so SessionStopUseCase must still force explicit selection on
    >1 active sessions. This pins that A9 does NOT touch session_stop.
    """

    def test_session_stop_still_raises_on_multiple_active(self) -> None:
        """SessionStopUseCase.execute(None) with 2 active -> RuntimeError
        matching 'Multiple sessions'."""
        repo = FakeSessionRepository()
        process = FakeProcess()
        display = FakeDisplayServer()
        bus = FakeAccessibilityBus()

        _register_active(
            repo, process, _make_session("stop-a", 100.0, app_pid=100, xvfb_pid=200)
        )
        _register_active(
            repo, process, _make_session("stop-b", 900.0, app_pid=300, xvfb_pid=400)
        )

        uc = SessionStopUseCase(
            display_server=display,
            atspi_bus=bus,
            process=process,
            session_repo=repo,
        )

        with pytest.raises(RuntimeError, match="Multiple sessions") as exc_info:
            uc.execute(session_id=None)

        assert "Multiple sessions" in str(exc_info.value)
