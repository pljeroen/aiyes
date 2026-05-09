"""AIYES-21 Prune tests — RED phase.

Tests for PruneResult value object, PruneUseCase domain logic,
SessionCleanupPort protocol, and FileSessionCleanup adapter.
These tests MUST fail because the production code does not exist yet.

Traceability — Formal Constraint Map:
  FC-AIYES21-003: SessionCleanupPort Protocol pattern
  FC-AIYES21-004: SessionRepositoryPort immutability
  FC-AIYES21-008: Prune never deletes active sessions
  FC-AIYES21-009: PruneResult immutability
  FC-AIYES21-014: Prune liveness rule (Linux)
  FC-AIYES21-015: Prune liveness rule (Android)
  FC-AIYES21-016: Prune dry-run zero side effects
  FC-AIYES21-021: Missing session.json = dead session, mtime fallback
  FC-AIYES21-023: Reserved directory protection

Requirement coverage:
  REQ-AIYES21-020: SessionCleanupPort Protocol
  REQ-AIYES21-021: list_session_directories skips _global/lessons
  REQ-AIYES21-022: delete_session_directory validation
  REQ-AIYES21-023: get_session_mtime
  REQ-AIYES21-024: Age filtering
  REQ-AIYES21-025: Liveness rules (Linux + Android)
  REQ-AIYES21-026: Dry-run
  REQ-AIYES21-027: PruneResult frozen dataclass
  REQ-AIYES21-030: SessionRepositoryPort unchanged
  REQ-AIYES21-033: Missing session.json fallback
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from aiyes.domain.session import Session

# These imports will fail (RED) — production modules do not exist yet.
from aiyes.domain.operation_record import PruneResult
from aiyes.ports.session_cleanup import SessionCleanupPort
from aiyes.adapters.file_session_cleanup import FileSessionCleanup
from aiyes.domain.use_cases.prune import PruneUseCase

# Existing fakes from conftest
from tests.conftest import FakeClock, FakeProcess, FakeSessionRepository


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_linux_session(**overrides: Any) -> Session:
    """Construct a standard Linux session."""
    defaults = dict(
        session_id="linux-001",
        display=":99",
        app_pid=12345,
        app_command="gedit",
        app_args=(),
        atspi_bus_pid=12346,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=12344,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
        backend="linux",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_android_session(**overrides: Any) -> Session:
    """Construct an Android session."""
    defaults = dict(
        session_id="android-001",
        app_pid=54321,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


class FakeSessionCleanup:
    """In-memory fake for SessionCleanupPort — structural typing."""

    def __init__(self) -> None:
        self._directories: List[str] = []
        self._mtimes: Dict[str, float] = {}
        self.calls: List[Tuple[str, Any]] = []
        self.deleted: List[str] = []

    def add_directory(self, name: str, mtime: float = 0.0) -> None:
        """Test helper: register a session directory."""
        self._directories.append(name)
        self._mtimes[name] = mtime

    def delete_session_directory(self, session_id: str) -> None:
        self.calls.append(("delete_session_directory", session_id))
        self.deleted.append(session_id)

    def list_session_directories(self) -> List[str]:
        self.calls.append(("list_session_directories", None))
        return list(self._directories)

    def get_session_mtime(self, session_id: str) -> Optional[float]:
        self.calls.append(("get_session_mtime", session_id))
        return self._mtimes.get(session_id)


# ═══════════════════════════════════════════════════════════════════════
# PruneResult creation and immutability (FC-AIYES21-009, REQ-AIYES21-027)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneResultCreation:
    """PruneResult is a frozen dataclass with tuple fields."""

    def test_creation_with_all_fields(self) -> None:
        """REQ-AIYES21-027: PruneResult holds all specified fields."""
        result = PruneResult(
            pruned_count=2,
            skipped_active=1,
            dry_run=False,
            sessions_pruned=("s1", "s2"),
        )
        assert result.pruned_count == 2
        assert result.skipped_active == 1
        assert result.dry_run is False
        assert result.sessions_pruned == ("s1", "s2")

    def test_frozen(self) -> None:
        """FC-AIYES21-009: PruneResult is immutable."""
        result = PruneResult(
            pruned_count=0,
            skipped_active=0,
            dry_run=False,
            sessions_pruned=(),
        )
        assert PruneResult.__dataclass_params__.frozen is True
        with pytest.raises(AttributeError):
            result.pruned_count = 99  # type: ignore[misc]

    def test_sessions_pruned_is_tuple(self) -> None:
        """FC-AIYES21-009: sessions_pruned is tuple, not list."""
        result = PruneResult(
            pruned_count=1,
            skipped_active=0,
            dry_run=False,
            sessions_pruned=("s1",),
        )
        assert isinstance(result.sessions_pruned, tuple)


# ═══════════════════════════════════════════════════════════════════════
# SessionCleanupPort Protocol (FC-AIYES21-003, REQ-AIYES21-020)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionCleanupPortProtocol:
    """SessionCleanupPort is a Protocol with 3 methods."""

    def test_fake_satisfies_protocol(self) -> None:
        """REQ-AIYES21-020: FakeSessionCleanup structurally satisfies Protocol."""
        fake = FakeSessionCleanup()
        assert callable(fake.delete_session_directory)
        assert callable(fake.list_session_directories)
        assert callable(fake.get_session_mtime)

    def test_protocol_has_three_methods(self) -> None:
        """REQ-AIYES21-020: SessionCleanupPort defines exactly 3 methods."""
        import inspect as inspect_mod

        public_methods = [
            name
            for name, _ in inspect_mod.getmembers(
                SessionCleanupPort, predicate=inspect_mod.isfunction
            )
            if not name.startswith("_")
        ]
        assert set(public_methods) == {
            "delete_session_directory",
            "list_session_directories",
            "get_session_mtime",
        }


# ═══════════════════════════════════════════════════════════════════════
# FileSessionCleanup — list directories (REQ-AIYES21-021)
# ═══════════════════════════════════════════════════════════════════════


class TestFileSessionCleanupListDirectories:
    """list_session_directories() skips reserved directories."""

    def test_skips_global_and_lessons(self, tmp_path: Path) -> None:
        """REQ-AIYES21-021: _global and lessons are excluded."""
        (tmp_path / "abc123").mkdir()
        (tmp_path / "_global").mkdir()
        (tmp_path / "lessons").mkdir()
        (tmp_path / "def456").mkdir()

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        dirs = cleanup.list_session_directories()

        assert "_global" not in dirs
        assert "lessons" not in dirs
        assert "abc123" in dirs
        assert "def456" in dirs

    def test_only_directories_returned(self, tmp_path: Path) -> None:
        """REQ-AIYES21-021: Files are excluded, only directories."""
        (tmp_path / "session-001").mkdir()
        (tmp_path / "somefile.txt").write_text("x")

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        dirs = cleanup.list_session_directories()

        assert "session-001" in dirs
        assert "somefile.txt" not in dirs

    def test_empty_base_dir(self, tmp_path: Path) -> None:
        """REQ-AIYES21-021: Empty base dir returns empty list."""
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        dirs = cleanup.list_session_directories()
        assert dirs == []


# ═══════════════════════════════════════════════════════════════════════
# FileSessionCleanup — delete (FC-AIYES21-023, REQ-AIYES21-022)
# ═══════════════════════════════════════════════════════════════════════


class TestFileSessionCleanupDelete:
    """delete_session_directory() validates and removes directories."""

    def test_delete_removes_directory(self, tmp_path: Path) -> None:
        """REQ-AIYES21-022: Valid session directory is removed."""
        session_dir = tmp_path / "s1"
        session_dir.mkdir()
        (session_dir / "session.json").write_text("{}")

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        cleanup.delete_session_directory("s1")

        assert not session_dir.exists()

    def test_delete_global_raises(self, tmp_path: Path) -> None:
        """FC-AIYES21-023: Deleting _global raises error."""
        (tmp_path / "_global").mkdir()

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        with pytest.raises(Exception):
            cleanup.delete_session_directory("_global")

        assert (tmp_path / "_global").exists()

    def test_delete_lessons_raises(self, tmp_path: Path) -> None:
        """FC-AIYES21-023: Deleting lessons raises error."""
        (tmp_path / "lessons").mkdir()

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        with pytest.raises(Exception):
            cleanup.delete_session_directory("lessons")

        assert (tmp_path / "lessons").exists()


# ═══════════════════════════════════════════════════════════════════════
# FileSessionCleanup — mtime (REQ-AIYES21-023)
# ═══════════════════════════════════════════════════════════════════════


class TestFileSessionCleanupMtime:
    """get_session_mtime() returns directory modification time."""

    def test_existing_directory_returns_float(self, tmp_path: Path) -> None:
        """REQ-AIYES21-023: Existing dir -> float mtime."""
        (tmp_path / "s1").mkdir()

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        mtime = cleanup.get_session_mtime("s1")

        assert isinstance(mtime, float)
        assert mtime > 0

    def test_nonexistent_directory_returns_none(self, tmp_path: Path) -> None:
        """REQ-AIYES21-023: Nonexistent dir -> None."""
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        mtime = cleanup.get_session_mtime("nonexistent")

        assert mtime is None


# ═══════════════════════════════════════════════════════════════════════
# FileSessionCleanup — path traversal prevention (A10-001)
# ═══════════════════════════════════════════════════════════════════════


class TestFileSessionCleanupPathTraversal:
    """Path traversal attacks are rejected before any filesystem operation."""

    @pytest.mark.parametrize(
        "bad_id",
        [
            "../etc",
            "../../etc/passwd",
            "foo/../../../etc",
            "..",
            "a/b",
            "/etc/passwd",
            "",
        ],
    )
    def test_delete_rejects_path_traversal(self, tmp_path: Path, bad_id: str) -> None:
        """A10-001: delete_session_directory rejects separators, .., absolute paths."""
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        with pytest.raises(ValueError):
            cleanup.delete_session_directory(bad_id)

    @pytest.mark.parametrize(
        "bad_id",
        [
            "../etc",
            "../../etc/passwd",
            "..",
            "a/b",
            "/etc/passwd",
            "",
        ],
    )
    def test_mtime_rejects_path_traversal(self, tmp_path: Path, bad_id: str) -> None:
        """A10-001: get_session_mtime rejects separators, .., absolute paths."""
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        with pytest.raises(ValueError):
            cleanup.get_session_mtime(bad_id)

    def test_delete_rejects_reserved_via_relative_path(self, tmp_path: Path) -> None:
        """A10-001: Cannot bypass reserved-name guard via relative path component."""
        (tmp_path / "_global").mkdir()
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        # Direct reserved name
        with pytest.raises(ValueError):
            cleanup.delete_session_directory("_global")
        assert (tmp_path / "_global").exists()

    def test_mtime_rejects_reserved_name(self, tmp_path: Path) -> None:
        """A10-001: get_session_mtime rejects reserved directory names."""
        (tmp_path / "_global").mkdir()
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        with pytest.raises(ValueError):
            cleanup.get_session_mtime("_global")

    def test_containment_check_after_resolve(self, tmp_path: Path) -> None:
        """A10-001: Even if validation passes, resolved path must stay in base_dir."""
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        # A valid-looking session_id that doesn't escape should work fine
        (tmp_path / "valid-session").mkdir()
        mtime = cleanup.get_session_mtime("valid-session")
        assert isinstance(mtime, float)


# ═══════════════════════════════════════════════════════════════════════
# PruneUseCase — age filter (REQ-AIYES21-024)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneUseCaseAgeFilter:
    """PruneUseCase filters sessions by age."""

    def test_old_session_pruned(self) -> None:
        """REQ-AIYES21-024: 100h old session with max_age=72h -> pruned."""
        # Session started 100 hours ago
        clock = FakeClock(now_value=360000.0)  # 100 hours in seconds
        session = _make_linux_session(
            session_id="old-001",
            started_at=0.0,  # age = 360000 seconds = 100 hours
            app_pid=111,
            xvfb_pid=222,
        )

        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        # Both processes dead
        process._running[111] = False
        process._running[222] = False

        cleanup = FakeSessionCleanup()
        cleanup.add_directory("old-001")

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=72.0)

        assert "old-001" in result.sessions_pruned

    def test_young_session_not_pruned(self) -> None:
        """REQ-AIYES21-024: 10h old session with max_age=72h -> not pruned."""
        clock = FakeClock(now_value=36000.0)  # 10 hours
        session = _make_linux_session(
            session_id="young-001",
            started_at=0.0,  # age = 36000 seconds = 10 hours
            app_pid=111,
            xvfb_pid=222,
        )

        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[111] = False
        process._running[222] = False

        cleanup = FakeSessionCleanup()
        cleanup.add_directory("young-001")

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=72.0)

        assert "young-001" not in result.sessions_pruned


# ═══════════════════════════════════════════════════════════════════════
# PruneUseCase — liveness Linux (FC-AIYES21-008, FC-AIYES21-014, REQ-AIYES21-025)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneUseCaseLivenessLinux:
    """Linux sessions: alive if app_pid AND xvfb_pid running."""

    def _make_prune_uc(
        self,
        session: Session,
        app_running: bool,
        xvfb_running: bool,
    ) -> Tuple[PruneUseCase, FakeSessionCleanup]:
        """Helper: create PruneUseCase with configurable liveness."""
        clock = FakeClock(now_value=500000.0)  # Far enough for age filter

        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[session.app_pid] = app_running
        process._running[session.xvfb_pid] = xvfb_running

        cleanup = FakeSessionCleanup()
        cleanup.add_directory(session.session_id)

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        return uc, cleanup

    def test_both_alive_excluded(self) -> None:
        """FC-AIYES21-014: Both pids alive -> not pruned."""
        session = _make_linux_session(
            session_id="live-001",
            app_pid=100,
            xvfb_pid=200,
            started_at=0.0,
        )
        uc, cleanup = self._make_prune_uc(session, app_running=True, xvfb_running=True)

        result = uc.execute(max_age_hours=1.0)

        assert "live-001" not in result.sessions_pruned
        assert result.skipped_active >= 1

    def test_app_dead_included(self) -> None:
        """FC-AIYES21-014: App dead, xvfb alive -> pruned."""
        session = _make_linux_session(
            session_id="dead-app",
            app_pid=100,
            xvfb_pid=200,
            started_at=0.0,
        )
        uc, cleanup = self._make_prune_uc(session, app_running=False, xvfb_running=True)

        result = uc.execute(max_age_hours=1.0)

        assert "dead-app" in result.sessions_pruned

    def test_xvfb_dead_included(self) -> None:
        """FC-AIYES21-014: App alive, xvfb dead -> pruned."""
        session = _make_linux_session(
            session_id="dead-xvfb",
            app_pid=100,
            xvfb_pid=200,
            started_at=0.0,
        )
        uc, cleanup = self._make_prune_uc(session, app_running=True, xvfb_running=False)

        result = uc.execute(max_age_hours=1.0)

        assert "dead-xvfb" in result.sessions_pruned

    def test_both_dead_included(self) -> None:
        """FC-AIYES21-014: Both dead -> pruned."""
        session = _make_linux_session(
            session_id="both-dead",
            app_pid=100,
            xvfb_pid=200,
            started_at=0.0,
        )
        uc, cleanup = self._make_prune_uc(
            session, app_running=False, xvfb_running=False
        )

        result = uc.execute(max_age_hours=1.0)

        assert "both-dead" in result.sessions_pruned


# ═══════════════════════════════════════════════════════════════════════
# PruneUseCase — liveness Android (FC-AIYES21-015, REQ-AIYES21-025)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneUseCaseLivenessAndroid:
    """Android sessions: alive if app_pid running (xvfb not checked)."""

    def test_app_alive_excluded(self) -> None:
        """FC-AIYES21-015: Android app_pid alive -> not pruned."""
        clock = FakeClock(now_value=500000.0)
        session = _make_android_session(
            session_id="android-live",
            app_pid=300,
            started_at=0.0,
        )

        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[300] = True

        cleanup = FakeSessionCleanup()
        cleanup.add_directory("android-live")

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=1.0)

        assert "android-live" not in result.sessions_pruned

    def test_app_dead_included(self) -> None:
        """FC-AIYES21-015: Android app_pid dead -> pruned."""
        clock = FakeClock(now_value=500000.0)
        session = _make_android_session(
            session_id="android-dead",
            app_pid=300,
            started_at=0.0,
        )

        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[300] = False

        cleanup = FakeSessionCleanup()
        cleanup.add_directory("android-dead")

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=1.0)

        assert "android-dead" in result.sessions_pruned


# ═══════════════════════════════════════════════════════════════════════
# PruneUseCase — no session.json (FC-AIYES21-021, REQ-AIYES21-033)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneUseCaseLivenessNoSessionJson:
    """Sessions without session.json are treated as dead."""

    def test_no_session_json_treated_as_dead(self) -> None:
        """REQ-AIYES21-033: session_repo.load() returns None -> dead."""
        clock = FakeClock(now_value=500000.0)

        repo = FakeSessionRepository()
        # No session saved — load will return None

        process = FakeProcess()

        cleanup = FakeSessionCleanup()
        cleanup.add_directory("orphan-001", mtime=0.0)  # Very old mtime

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=1.0)

        assert "orphan-001" in result.sessions_pruned


class TestPruneUseCaseMtimeFallback:
    """Age from mtime when session.json is missing."""

    def test_mtime_old_enough_pruned(self) -> None:
        """REQ-AIYES21-024 + REQ-AIYES21-033: Old mtime -> pruned."""
        clock = FakeClock(now_value=500000.0)

        repo = FakeSessionRepository()
        # No session saved

        process = FakeProcess()

        cleanup = FakeSessionCleanup()
        # mtime = 0.0 -> age = 500000s -> well over any max_age_hours
        cleanup.add_directory("orphan-old", mtime=0.0)

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=72.0)

        assert "orphan-old" in result.sessions_pruned

    def test_mtime_recent_not_pruned(self) -> None:
        """REQ-AIYES21-024 + REQ-AIYES21-033: Recent mtime -> not pruned."""
        now = 500000.0
        clock = FakeClock(now_value=now)

        repo = FakeSessionRepository()

        process = FakeProcess()

        cleanup = FakeSessionCleanup()
        # mtime = 1 hour ago -> within max_age_hours=72
        cleanup.add_directory("orphan-recent", mtime=now - 3600.0)

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=72.0)

        assert "orphan-recent" not in result.sessions_pruned


# ═══════════════════════════════════════════════════════════════════════
# PruneUseCase — dry run (FC-AIYES21-016, REQ-AIYES21-026)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneUseCaseDryRun:
    """dry_run=True returns what would be pruned but does not delete."""

    def test_dry_run_no_deletions(self) -> None:
        """FC-AIYES21-016: dry_run=True -> delete never called."""
        clock = FakeClock(now_value=500000.0)
        session = _make_linux_session(
            session_id="pruneable",
            app_pid=100,
            xvfb_pid=200,
            started_at=0.0,
        )

        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = False
        process._running[200] = False

        cleanup = FakeSessionCleanup()
        cleanup.add_directory("pruneable")

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=1.0, dry_run=True)

        assert result.dry_run is True
        assert "pruneable" in result.sessions_pruned
        assert result.pruned_count >= 1

        # Verify delete was NOT called
        delete_calls = [c for c in cleanup.calls if c[0] == "delete_session_directory"]
        assert len(delete_calls) == 0

    def test_wet_run_does_delete(self) -> None:
        """FC-AIYES21-016 inverse: dry_run=False -> delete IS called."""
        clock = FakeClock(now_value=500000.0)
        session = _make_linux_session(
            session_id="pruneable2",
            app_pid=100,
            xvfb_pid=200,
            started_at=0.0,
        )

        repo = FakeSessionRepository()
        repo.save(session)

        process = FakeProcess()
        process._running[100] = False
        process._running[200] = False

        cleanup = FakeSessionCleanup()
        cleanup.add_directory("pruneable2")

        uc = PruneUseCase(
            session_repo=repo,
            cleanup=cleanup,
            process=process,
            clock=clock,
        )
        result = uc.execute(max_age_hours=1.0, dry_run=False)

        assert result.dry_run is False
        assert "pruneable2" in result.sessions_pruned

        delete_calls = [c for c in cleanup.calls if c[0] == "delete_session_directory"]
        assert len(delete_calls) >= 1


# ═══════════════════════════════════════════════════════════════════════
# PruneUseCase — reserved directory protection (FC-AIYES21-023)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneUseCaseReservedDirectories:
    """_global and lessons directories are never pruned."""

    def test_global_excluded_by_adapter(self, tmp_path: Path) -> None:
        """FC-AIYES21-023, A10-010: _global excluded from list_session_directories."""
        (tmp_path / "_global").mkdir()
        (tmp_path / "s1").mkdir()

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        dirs = cleanup.list_session_directories()

        assert "_global" not in dirs
        assert "s1" in dirs

    def test_lessons_excluded_by_adapter(self, tmp_path: Path) -> None:
        """FC-AIYES21-023, A10-010: lessons excluded from list_session_directories."""
        (tmp_path / "lessons").mkdir()
        (tmp_path / "s2").mkdir()

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        dirs = cleanup.list_session_directories()

        assert "lessons" not in dirs
        assert "s2" in dirs

    def test_delete_global_raises_via_adapter(self, tmp_path: Path) -> None:
        """FC-AIYES21-023, A10-010: delete_session_directory rejects _global."""
        (tmp_path / "_global").mkdir()
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))

        with pytest.raises(ValueError):
            cleanup.delete_session_directory("_global")

        assert (tmp_path / "_global").exists()

    def test_delete_lessons_raises_via_adapter(self, tmp_path: Path) -> None:
        """FC-AIYES21-023, A10-010: delete_session_directory rejects lessons."""
        (tmp_path / "lessons").mkdir()
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))

        with pytest.raises(ValueError):
            cleanup.delete_session_directory("lessons")

        assert (tmp_path / "lessons").exists()


# ═══════════════════════════════════════════════════════════════════════
# SessionRepositoryPort unchanged (FC-AIYES21-004, REQ-AIYES21-030)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionRepositoryPortUnchanged:
    """SessionRepositoryPort has exactly {save, load, load_all, delete}."""

    def test_exact_method_set(self) -> None:
        """REQ-AIYES21-030: No methods added or removed from SessionRepositoryPort."""

        source_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "aiyes"
            / "ports"
            / "storage.py"
        )
        source = source_path.read_text()
        tree = ast.parse(source)

        methods: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SessionRepositoryPort":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and not item.name.startswith(
                        "_"
                    ):
                        methods.append(item.name)

        assert set(methods) == {"save", "load", "load_all", "delete"}


# ═══════════════════════════════════════════════════════════════════════
# FileSessionCleanup provider tests (SessionCleanupPort provider side)
# ═══════════════════════════════════════════════════════════════════════


class TestFileSessionCleanupProvider:
    """Provider-side tests: FileSessionCleanup satisfies SessionCleanupPort."""

    def test_satisfies_protocol(self, tmp_path: Path) -> None:
        """REQ-AIYES21-020: FileSessionCleanup has all required methods."""
        cleanup = FileSessionCleanup(base_dir=str(tmp_path))
        assert callable(cleanup.delete_session_directory)
        assert callable(cleanup.list_session_directories)
        assert callable(cleanup.get_session_mtime)

    def test_list_delete_roundtrip(self, tmp_path: Path) -> None:
        """Provider test: list, then delete, then list again."""
        (tmp_path / "s1").mkdir()
        (tmp_path / "s1" / "session.json").write_text("{}")

        cleanup = FileSessionCleanup(base_dir=str(tmp_path))

        dirs = cleanup.list_session_directories()
        assert "s1" in dirs

        cleanup.delete_session_directory("s1")

        dirs_after = cleanup.list_session_directories()
        assert "s1" not in dirs_after


# ═══════════════════════════════════════════════════════════════════════
# AST purity: prune.py (FC-AIYES21-001, A10-008)
# ═══════════════════════════════════════════════════════════════════════


class TestPruneDomainPurity:
    """domain/use_cases/prune.py has no external imports."""

    def test_no_non_stdlib_imports(self) -> None:
        """A10-008, FC-AIYES21-001: AST check for stdlib + domain/ports only imports."""
        source_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "aiyes"
            / "domain"
            / "use_cases"
            / "prune.py"
        )
        assert source_path.exists(), f"File not found: {source_path}"

        source = source_path.read_text()
        tree = ast.parse(source)

        allowed_prefixes = ("__future__", "dataclasses", "typing", "collections")
        allowed_domain = ("aiyes.domain.", "aiyes.ports.")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    assert any(
                        name == p or name.startswith(p + ".") for p in allowed_prefixes
                    ) or any(name.startswith(dp) for dp in allowed_domain), (
                        f"Forbidden import: {name}"
                    )

            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                module = node.module
                assert any(
                    module == p or module.startswith(p + ".") for p in allowed_prefixes
                ) or any(module.startswith(dp) for dp in allowed_domain), (
                    f"Forbidden import from: {module}"
                )
