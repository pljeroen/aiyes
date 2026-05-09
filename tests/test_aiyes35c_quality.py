"""AIYES-35C Code quality improvement tests.

Tests for findings B-02, B-06, R-01, R-02. Refactor findings B-05 and B-01
have no new behavior, so they rely on existing tests passing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session


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


# ═══════════════════════════════════════════════════════════════════════
# B-02: SubprocessAdapter.stop() SIGKILL escalation on timeout
# ═══════════════════════════════════════════════════════════════════════


class TestSubprocessAdapterSigkillEscalation:
    """B-02: When wait(timeout=3) times out, stop() must escalate to SIGKILL."""

    def test_sigkill_sent_when_wait_times_out(self) -> None:
        """Tracked process that refuses SIGTERM gets SIGKILL."""
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        mock_process = MagicMock()
        mock_process.pid = 777
        mock_process.wait.side_effect = TimeoutExpired(cmd="test", timeout=3)

        with patch("subprocess.Popen", return_value=mock_process):
            adapter.start("stubbornapp", [], None)

        adapter.stop(777)

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    def test_no_sigkill_when_wait_succeeds(self) -> None:
        """Tracked process that stops cleanly does NOT get SIGKILL."""
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        mock_process = MagicMock()
        mock_process.pid = 888
        mock_process.wait.return_value = 0  # exits cleanly

        with patch("subprocess.Popen", return_value=mock_process):
            adapter.start("goodapp", [], None)

        adapter.stop(888)

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# B-06: NodeIdRegistry.has_id() and lookup_id() performance
# ═══════════════════════════════════════════════════════════════════════


class TestNodeIdRegistryReverseLookup:
    """B-06: has_id() and lookup_id() should use a reverse map, not O(n) scan."""

    def test_has_id_returns_true_for_assigned_id(self) -> None:
        from aiyes.domain.node_id import NodeIdRegistry

        reg = NodeIdRegistry()
        nid = reg.get_or_assign("button", "OK", [0, 1])
        assert reg.has_id(nid) is True

    def test_has_id_returns_false_for_unknown_id(self) -> None:
        from aiyes.domain.node_id import NodeIdRegistry

        reg = NodeIdRegistry()
        assert reg.has_id("n_999") is False

    def test_lookup_id_returns_key_for_assigned_id(self) -> None:
        from aiyes.domain.node_id import NodeIdRegistry

        reg = NodeIdRegistry()
        nid = reg.get_or_assign("button", "OK", [0, 1])
        result = reg.lookup_id(nid)
        assert result == ("button", "OK", (0, 1))

    def test_lookup_id_returns_none_for_unknown_id(self) -> None:
        from aiyes.domain.node_id import NodeIdRegistry

        reg = NodeIdRegistry()
        assert reg.lookup_id("n_999") is None

    def test_reverse_map_attribute_exists(self) -> None:
        """Verify that the registry maintains a reverse lookup dict."""
        from aiyes.domain.node_id import NodeIdRegistry

        reg = NodeIdRegistry()
        reg.get_or_assign("button", "OK", [0, 1])
        # After B-06 fix, _reverse should exist as a dict
        assert hasattr(reg, "_reverse")
        assert isinstance(reg._reverse, dict)

    def test_from_mapping_populates_reverse(self) -> None:
        """from_mapping should also populate the reverse map."""
        import json
        from aiyes.domain.node_id import NodeIdRegistry

        # S-01: Use JSON array format instead of old Python tuple string
        mapping = {json.dumps(["button", "OK", [0, 1]]): "n_001"}
        reg = NodeIdRegistry.from_mapping(mapping)
        assert reg.has_id("n_001") is True
        assert reg.lookup_id("n_001") == ("button", "OK", (0, 1))


# ═══════════════════════════════════════════════════════════════════════
# R-01: Orphan process detection in prune
# ═══════════════════════════════════════════════════════════════════════


class FakeProcess:
    """Minimal fake for ProcessPort."""

    def __init__(self) -> None:
        self._running: Dict[int, bool] = {}
        self.calls: List[Tuple[str, Any]] = []

    def start(
        self, command: str, args: List[str], env: Optional[Dict[str, str]] = None
    ) -> int:
        return 0

    def stop(self, pid: int) -> None:
        self.calls.append(("stop", pid))
        self._running[pid] = False

    def is_running(self, pid: int) -> bool:
        self.calls.append(("is_running", pid))
        return self._running.get(pid, False)


class FakeClock:
    """Minimal fake for ClockPort."""

    def __init__(self, now_value: float = 1000.0) -> None:
        self._now = now_value

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


class FakeSessionRepository:
    """Minimal fake for SessionRepositoryPort."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Any] = {}

    def save(self, session: Any) -> None:
        self._sessions[session.session_id] = session

    def load(self, session_id: str) -> Optional[Any]:
        return self._sessions.get(session_id)

    def load_all(self) -> List[Any]:
        return list(self._sessions.values())

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class FakeSessionCleanup:
    """Minimal fake for SessionCleanupPort."""

    def __init__(self) -> None:
        self._directories: List[str] = []
        self._mtimes: Dict[str, float] = {}
        self.deleted: List[str] = []

    def add_directory(self, name: str, mtime: float = 0.0) -> None:
        self._directories.append(name)
        self._mtimes[name] = mtime

    def delete_session_directory(self, session_id: str) -> None:
        self.deleted.append(session_id)

    def list_session_directories(self) -> List[str]:
        return list(self._directories)

    def get_session_mtime(self, session_id: str) -> Optional[float]:
        return self._mtimes.get(session_id)


class TestOrphanProcessDetection:
    """R-01: Prune should detect orphaned Xvfb/dbus processes."""

    def test_orphan_detection_function_exists(self) -> None:
        """The prune use case should have orphan detection capability."""
        from aiyes.domain.use_cases.prune import PruneUseCase

        uc = PruneUseCase(
            session_repo=FakeSessionRepository(),
            cleanup=FakeSessionCleanup(),
            process=FakeProcess(),
            clock=FakeClock(),
        )
        # The execute method should accept an optional orphan scanner
        # or there should be a detect_orphans method
        result = uc.execute(max_age_hours=72.0, detect_orphans=True)
        assert hasattr(result, "orphans_found")

    def test_orphan_scanner_port_exists(self) -> None:
        """An OrphanScannerPort should exist for process scanning."""
        from aiyes.ports.orphan_scanner import OrphanScannerPort

        assert callable(getattr(OrphanScannerPort, "scan_orphan_xvfb", None))

    def test_prune_reports_orphans_in_result(self) -> None:
        """PruneResult should include orphan count."""
        from aiyes.domain.operation_record import PruneResult

        result = PruneResult(
            pruned_count=0,
            skipped_active=0,
            dry_run=False,
            sessions_pruned=(),
            orphans_found=2,
            orphans_killed=1,
        )
        assert result.orphans_found == 2
        assert result.orphans_killed == 1


# ═══════════════════════════════════════════════════════════════════════
# R-02: Operation log rotation
# ═══════════════════════════════════════════════════════════════════════


class TestOperationLogRotation:
    """R-02: Operation log files should rotate when exceeding max entries."""

    def test_rotate_drops_oldest_entries_when_over_limit(self, tmp_path: Path) -> None:
        """After exceeding max_entries, oldest records are dropped."""
        from aiyes.adapters.file_operation_log import FileOperationLog
        from aiyes.domain.operation_record import OperationRecord

        log = FileOperationLog(base_dir=str(tmp_path), max_entries=5)

        # Write 10 records
        for i in range(10):
            record = OperationRecord(
                timestamp=float(i),
                session_id="_global",
                command=f"cmd_{i}",
                duration_ms=10.0,
                exit_code=0,
            )
            log.append(record)

        # Read back — should only have the last 5
        records = log.read("_global")
        assert len(records) == 5
        # The oldest should be cmd_5 (indices 5..9 kept)
        assert records[0].command == "cmd_5"
        assert records[-1].command == "cmd_9"

    def test_no_rotation_when_under_limit(self, tmp_path: Path) -> None:
        """Records below max_entries are not rotated."""
        from aiyes.adapters.file_operation_log import FileOperationLog
        from aiyes.domain.operation_record import OperationRecord

        log = FileOperationLog(base_dir=str(tmp_path), max_entries=100)

        for i in range(5):
            record = OperationRecord(
                timestamp=float(i),
                session_id="_global",
                command=f"cmd_{i}",
                duration_ms=10.0,
                exit_code=0,
            )
            log.append(record)

        records = log.read("_global")
        assert len(records) == 5

    def test_default_max_entries_is_reasonable(self) -> None:
        """Default max_entries should be set (not unlimited)."""
        from aiyes.adapters.file_operation_log import FileOperationLog

        log = FileOperationLog()
        assert hasattr(log, "_max_entries")
        assert log._max_entries > 0
        assert log._max_entries <= 50000  # Reasonable upper bound

    def test_rotation_preserves_file_integrity(self, tmp_path: Path) -> None:
        """After rotation, file is valid JSONL with correct number of lines."""
        from aiyes.adapters.file_operation_log import FileOperationLog
        from aiyes.domain.operation_record import OperationRecord

        log = FileOperationLog(base_dir=str(tmp_path), max_entries=3)

        for i in range(7):
            record = OperationRecord(
                timestamp=float(i),
                session_id="_global",
                command=f"cmd_{i}",
                duration_ms=10.0,
                exit_code=0,
            )
            log.append(record)

        # Verify file is valid JSONL
        path = tmp_path / "_global" / "operations.jsonl"
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # Should not raise


# ═══════════════════════════════════════════════════════════════════════
# B-05: _is_session_active() shared function
# ═══════════════════════════════════════════════════════════════════════


class TestIsSessionActiveShared:
    """B-05: _is_session_active should be a shared domain function."""

    def test_shared_function_exists(self) -> None:
        """is_session_active should be importable from domain.session_liveness."""
        from aiyes.domain.use_cases.session_liveness import is_session_active

        assert callable(is_session_active)

    def test_linux_session_active_when_both_pids_running(self) -> None:
        from aiyes.domain.use_cases.session_liveness import is_session_active

        session = _make_linux_session(app_pid=100, xvfb_pid=200)
        process = FakeProcess()
        process._running[100] = True
        process._running[200] = True
        assert is_session_active(session, process) is True

    def test_linux_session_inactive_when_app_dead(self) -> None:
        from aiyes.domain.use_cases.session_liveness import is_session_active

        session = _make_linux_session(app_pid=100, xvfb_pid=200)
        process = FakeProcess()
        process._running[100] = False
        process._running[200] = True
        assert is_session_active(session, process) is False

    def test_linux_session_inactive_when_xvfb_dead(self) -> None:
        from aiyes.domain.use_cases.session_liveness import is_session_active

        session = _make_linux_session(app_pid=100, xvfb_pid=200)
        process = FakeProcess()
        process._running[100] = True
        process._running[200] = False
        assert is_session_active(session, process) is False

    def test_android_session_active_when_app_running(self) -> None:
        from aiyes.domain.use_cases.session_liveness import is_session_active

        session = Session(
            session_id="android-001",
            app_pid=300,
            app_command="com.example.app/.MainActivity",
            app_args=(),
            name=None,
            started_at=1000.0,
            backend="android",
            device_serial="emulator-5554",
        )
        process = FakeProcess()
        process._running[300] = True
        assert is_session_active(session, process) is True

    def test_android_session_inactive_when_app_dead(self) -> None:
        from aiyes.domain.use_cases.session_liveness import is_session_active

        session = Session(
            session_id="android-001",
            app_pid=300,
            app_command="com.example.app/.MainActivity",
            app_args=(),
            name=None,
            started_at=1000.0,
            backend="android",
            device_serial="emulator-5554",
        )
        process = FakeProcess()
        process._running[300] = False
        assert is_session_active(session, process) is False
