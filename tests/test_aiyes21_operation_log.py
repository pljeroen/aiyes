"""AIYES-21 Operation Log tests — RED phase.

Tests for OperationRecord value object, FileOperationLog adapter, and
OperationLogPort port contract. These tests MUST fail because the production
code does not exist yet.

Traceability — Formal Constraint Map:
  FC-AIYES21-001: Domain purity
  FC-AIYES21-002: OperationLogPort Protocol pattern
  FC-AIYES21-005: ClockPort exclusivity
  FC-AIYES21-006: OperationRecord immutability
  FC-AIYES21-007: Operation log append-only semantics
  FC-AIYES21-010: JSONL write atomicity
  FC-AIYES21-012: Corrupt line resilience
  FC-AIYES21-020: Missing operations.jsonl = empty log

Requirement coverage:
  REQ-AIYES21-001: OperationRecord frozen dataclass
  REQ-AIYES21-002: OperationLogPort Protocol
  REQ-AIYES21-003: FileOperationLog append (POSIX atomic)
  REQ-AIYES21-004: JSONL short keys
  REQ-AIYES21-005: Session-less to _global
  REQ-AIYES21-008: Corrupt line resilience
  REQ-AIYES21-009: read_all across sessions
  REQ-AIYES21-010: list_session_ids
  REQ-AIYES21-012: Concurrent append atomicity
  REQ-AIYES21-029: Domain purity
  REQ-AIYES21-031: ClockPort exclusivity
  REQ-AIYES21-032: Missing operations.jsonl
  REQ-AIYES21-034: Performance < 5ms
"""

from __future__ import annotations

import ast
import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import Any, List, Tuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# These imports will fail (RED) — production modules do not exist yet.
from aiyes.domain.operation_record import (
    OperationRecord,
)
from aiyes.ports.operation_log import OperationLogPort
from aiyes.adapters.file_operation_log import (
    FileOperationLog,
    _record_to_jsonl_dict,  # noqa: F401
    _record_from_jsonl_dict,  # noqa: F401
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_record(**overrides: Any) -> OperationRecord:
    """Create an OperationRecord with sensible defaults."""
    defaults = dict(
        timestamp=1000.0,
        session_id="test-session-001",
        command="inspect",
        duration_ms=42.5,
        exit_code=0,
        error="",
    )
    defaults.update(overrides)
    return OperationRecord(**defaults)


class FakeOperationLog:
    """In-memory fake for OperationLogPort — structural typing, no base class."""

    def __init__(self) -> None:
        self._records: List[OperationRecord] = []
        self.calls: List[Tuple[str, Any]] = []

    def append(self, record: OperationRecord) -> None:
        self.calls.append(("append", record))
        self._records.append(record)

    def read(self, session_id: str) -> List[OperationRecord]:
        self.calls.append(("read", session_id))
        return [r for r in self._records if r.session_id == session_id]

    def read_all(self) -> List[OperationRecord]:
        self.calls.append(("read_all", None))
        return list(self._records)

    def list_session_ids(self) -> List[str]:
        self.calls.append(("list_session_ids", None))
        seen: List[str] = []
        for r in self._records:
            sid = r.session_id if r.session_id else "_global"
            if sid not in seen:
                seen.append(sid)
        return seen


# ═══════════════════════════════════════════════════════════════════════
# OperationRecord creation and immutability (FC-AIYES21-006, REQ-AIYES21-001)
# ═══════════════════════════════════════════════════════════════════════


class TestOperationRecordCreation:
    """OperationRecord is a frozen dataclass with 6 fields and correct defaults."""

    def test_all_fields_present(self) -> None:
        """REQ-AIYES21-001: OperationRecord has 6 fields."""
        record = _make_record()
        assert record.timestamp == 1000.0
        assert record.session_id == "test-session-001"
        assert record.command == "inspect"
        assert record.duration_ms == 42.5
        assert record.exit_code == 0
        assert record.error == ""

    def test_error_defaults_to_empty_string(self) -> None:
        """REQ-AIYES21-001: error field defaults to empty string."""
        record = OperationRecord(
            timestamp=1000.0,
            session_id="s1",
            command="find",
            duration_ms=10.0,
            exit_code=0,
        )
        assert record.error == ""

    def test_error_can_be_set(self) -> None:
        """REQ-AIYES21-001: error field accepts non-empty string."""
        record = _make_record(error="something went wrong", exit_code=1)
        assert record.error == "something went wrong"
        assert record.exit_code == 1

    def test_field_count_is_six(self) -> None:
        """REQ-AIYES21-001: Exactly 6 fields on the dataclass."""
        import dataclasses

        fields = dataclasses.fields(OperationRecord)
        assert len(fields) == 6
        expected_names = {
            "timestamp",
            "session_id",
            "command",
            "duration_ms",
            "exit_code",
            "error",
        }
        actual_names = {f.name for f in fields}
        assert actual_names == expected_names


class TestOperationRecordFrozen:
    """OperationRecord is immutable — mutation raises AttributeError."""

    def test_frozen_flag(self) -> None:
        """FC-AIYES21-006: __dataclass_params__.frozen == True."""
        assert OperationRecord.__dataclass_params__.frozen is True

    def test_mutation_raises_attribute_error(self) -> None:
        """FC-AIYES21-006: Assignment raises AttributeError."""
        record = _make_record()
        with pytest.raises(AttributeError):
            record.timestamp = 9999.0  # type: ignore[misc]

    def test_mutation_session_id_raises(self) -> None:
        record = _make_record()
        with pytest.raises(AttributeError):
            record.session_id = "other"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# OperationRecord JSONL serialization (REQ-AIYES21-004)
# ═══════════════════════════════════════════════════════════════════════


class TestOperationRecordSerialization:
    """Adapter-layer JSONL mapping to/from OperationRecord with short keys."""

    def test_to_jsonl_dict_short_keys(self) -> None:
        """REQ-AIYES21-004: Short keys ts, sid, cmd, dur_ms, exit."""
        record = _make_record()
        d = _record_to_jsonl_dict(record)
        assert d["ts"] == 1000.0
        assert d["sid"] == "test-session-001"
        assert d["cmd"] == "inspect"
        assert d["dur_ms"] == 42.5
        assert d["exit"] == 0

    def test_to_jsonl_dict_omits_err_when_empty(self) -> None:
        """REQ-AIYES21-004: err key omitted when error is empty string."""
        record = _make_record(error="")
        d = _record_to_jsonl_dict(record)
        assert "err" not in d

    def test_to_jsonl_dict_includes_err_when_nonempty(self) -> None:
        """REQ-AIYES21-004: err key present when error is non-empty."""
        record = _make_record(error="timeout", exit_code=1)
        d = _record_to_jsonl_dict(record)
        assert d["err"] == "timeout"

    def test_from_jsonl_dict_roundtrip(self) -> None:
        """REQ-AIYES21-004: _record_from_jsonl_dict restores OperationRecord."""
        original = _make_record()
        d = _record_to_jsonl_dict(original)
        restored = _record_from_jsonl_dict(d)
        assert restored == original

    def test_from_jsonl_dict_with_error(self) -> None:
        """REQ-AIYES21-004: _record_from_jsonl_dict handles err key."""
        original = _make_record(error="boom", exit_code=1)
        d = _record_to_jsonl_dict(original)
        restored = _record_from_jsonl_dict(d)
        assert restored == original

    def test_from_jsonl_dict_missing_err_defaults_empty(self) -> None:
        """REQ-AIYES21-004: Missing err in dict means error=""."""
        d = {"ts": 1.0, "sid": "s1", "cmd": "find", "dur_ms": 5.0, "exit": 0}
        record = _record_from_jsonl_dict(d)
        assert record.error == ""

    def test_jsonl_dict_only_has_expected_keys_success(self) -> None:
        """REQ-AIYES21-004: Success record has exactly 5 short keys (no err)."""
        record = _make_record(error="")
        d = _record_to_jsonl_dict(record)
        assert set(d.keys()) == {"ts", "sid", "cmd", "dur_ms", "exit"}

    def test_jsonl_dict_only_has_expected_keys_failure(self) -> None:
        """REQ-AIYES21-004: Failure record has exactly 6 short keys (with err)."""
        record = _make_record(error="fail", exit_code=1)
        d = _record_to_jsonl_dict(record)
        assert set(d.keys()) == {"ts", "sid", "cmd", "dur_ms", "exit", "err"}


# ═══════════════════════════════════════════════════════════════════════
# PBT: OperationRecord roundtrip (FC-AIYES21-006)
# ═══════════════════════════════════════════════════════════════════════


class TestOperationRecordRoundtripPBT:
    """Property-based: adapter JSONL dict roundtrip preserves all fields."""

    @given(
        timestamp=st.floats(
            min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False
        ),
        session_id=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"), whitelist_characters="-_"
            ),
            min_size=0,
            max_size=50,
        ),
        command=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"), whitelist_characters="-_ "
            ),
            min_size=1,
            max_size=30,
        ),
        duration_ms=st.floats(
            min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
        ),
        exit_code=st.sampled_from([0, 1]),
        error=st.text(max_size=100),
    )
    @settings(max_examples=200)
    def test_roundtrip_preserves_all_fields(
        self,
        timestamp: float,
        session_id: str,
        command: str,
        duration_ms: float,
        exit_code: int,
        error: str,
    ) -> None:
        """PBT: OperationRecord -> _record_to_jsonl_dict -> _record_from_jsonl_dict preserves all fields."""
        original = OperationRecord(
            timestamp=timestamp,
            session_id=session_id,
            command=command,
            duration_ms=duration_ms,
            exit_code=exit_code,
            error=error,
        )
        d = _record_to_jsonl_dict(original)
        restored = _record_from_jsonl_dict(d)
        assert restored.timestamp == original.timestamp
        assert restored.session_id == original.session_id
        assert restored.command == original.command
        assert restored.duration_ms == original.duration_ms
        assert restored.exit_code == original.exit_code
        assert restored.error == original.error


# ═══════════════════════════════════════════════════════════════════════
# FileOperationLog append (FC-AIYES21-007, REQ-AIYES21-003)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogAppend:
    """FileOperationLog.append() writes valid JSONL lines."""

    def test_append_creates_file_and_writes_one_line(self, tmp_path: Path) -> None:
        """REQ-AIYES21-003: append() writes a single JSONL line."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = _make_record(session_id="sess-001")
        log.append(record)

        jsonl_path = tmp_path / "sess-001" / "operations.jsonl"
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["cmd"] == "inspect"
        assert parsed["sid"] == "sess-001"

    def test_append_preserves_existing_lines(self, tmp_path: Path) -> None:
        """FC-AIYES21-007: Append-only — existing lines unchanged after second append."""
        log = FileOperationLog(base_dir=str(tmp_path))
        r1 = _make_record(session_id="s1", command="inspect", timestamp=100.0)
        r2 = _make_record(session_id="s1", command="find", timestamp=200.0)

        log.append(r1)
        log.append(r2)

        jsonl_path = tmp_path / "s1" / "operations.jsonl"
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 2

        first = json.loads(lines[0])
        assert first["cmd"] == "inspect"
        assert first["ts"] == 100.0

        second = json.loads(lines[1])
        assert second["cmd"] == "find"
        assert second["ts"] == 200.0

    def test_append_creates_parent_directories(self, tmp_path: Path) -> None:
        """REQ-AIYES21-003: Parent directory created if it does not exist."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = _make_record(session_id="new-session")
        log.append(record)

        assert (tmp_path / "new-session" / "operations.jsonl").exists()


# ═══════════════════════════════════════════════════════════════════════
# FileOperationLog JSONL key format (REQ-AIYES21-004)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogJsonKeys:
    """JSONL on disk uses short keys."""

    def test_success_record_has_short_keys_no_err(self, tmp_path: Path) -> None:
        """REQ-AIYES21-004: Success record omits err key."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = _make_record(session_id="s1", error="")
        log.append(record)

        jsonl_path = tmp_path / "s1" / "operations.jsonl"
        line = jsonl_path.read_text().strip()
        parsed = json.loads(line)
        assert set(parsed.keys()) == {"ts", "sid", "cmd", "dur_ms", "exit"}

    def test_failure_record_has_err_key(self, tmp_path: Path) -> None:
        """REQ-AIYES21-004: Failure record includes err key."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = _make_record(session_id="s1", error="oops", exit_code=1)
        log.append(record)

        jsonl_path = tmp_path / "s1" / "operations.jsonl"
        line = jsonl_path.read_text().strip()
        parsed = json.loads(line)
        assert parsed["err"] == "oops"
        assert set(parsed.keys()) == {"ts", "sid", "cmd", "dur_ms", "exit", "err"}


# ═══════════════════════════════════════════════════════════════════════
# FileOperationLog session-less -> _global (REQ-AIYES21-005, FC-AIYES21-017)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogSessionLessGlobal:
    """Session-less commands (sid="") log to _global/operations.jsonl."""

    def test_empty_session_id_writes_to_global(self, tmp_path: Path) -> None:
        """REQ-AIYES21-005: session_id="" writes to _global directory."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = _make_record(session_id="", command="doctor")
        log.append(record)

        global_path = tmp_path / "_global" / "operations.jsonl"
        assert global_path.exists()
        line = global_path.read_text().strip()
        parsed = json.loads(line)
        assert parsed["sid"] == ""
        assert parsed["cmd"] == "doctor"


# ═══════════════════════════════════════════════════════════════════════
# FileOperationLog read (FC-AIYES21-012, REQ-AIYES21-008)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogRead:
    """read() returns valid records, silently skipping corrupt lines."""

    def test_read_returns_records_for_session(self, tmp_path: Path) -> None:
        """REQ-AIYES21-008: read() returns OperationRecord list."""
        log = FileOperationLog(base_dir=str(tmp_path))
        r1 = _make_record(session_id="s1", command="inspect", timestamp=100.0)
        r2 = _make_record(session_id="s1", command="find", timestamp=200.0)
        log.append(r1)
        log.append(r2)

        records = log.read("s1")
        assert len(records) == 2
        assert records[0].command == "inspect"
        assert records[1].command == "find"

    def test_read_skips_empty_lines(self, tmp_path: Path) -> None:
        """FC-AIYES21-012: Empty lines are silently skipped."""
        session_dir = tmp_path / "s1"
        session_dir.mkdir(parents=True)
        jsonl_path = session_dir / "operations.jsonl"

        valid_record = _make_record(session_id="s1")
        valid_line = json.dumps(_record_to_jsonl_dict(valid_record))

        jsonl_path.write_text(f"{valid_line}\n\n\n")

        log = FileOperationLog(base_dir=str(tmp_path))
        records = log.read("s1")
        assert len(records) == 1

    def test_read_skips_corrupt_json_lines(self, tmp_path: Path) -> None:
        """FC-AIYES21-012: Corrupt JSON lines are silently skipped."""
        session_dir = tmp_path / "s1"
        session_dir.mkdir(parents=True)
        jsonl_path = session_dir / "operations.jsonl"

        valid_record = _make_record(session_id="s1")
        valid_line = json.dumps(_record_to_jsonl_dict(valid_record))

        content = f"{valid_line}\n{{corrupt json\n{valid_line}\n"
        jsonl_path.write_text(content)

        log = FileOperationLog(base_dir=str(tmp_path))
        records = log.read("s1")
        assert len(records) == 2

    def test_read_mixed_valid_empty_corrupt(self, tmp_path: Path) -> None:
        """FC-AIYES21-012: Mix of valid, empty, corrupt -> only valid returned."""
        session_dir = tmp_path / "s1"
        session_dir.mkdir(parents=True)
        jsonl_path = session_dir / "operations.jsonl"

        valid = json.dumps(_record_to_jsonl_dict(_make_record(session_id="s1")))
        content = f"{valid}\n\n{{bad\n"
        jsonl_path.write_text(content)

        log = FileOperationLog(base_dir=str(tmp_path))
        records = log.read("s1")
        assert len(records) == 1


# ═══════════════════════════════════════════════════════════════════════
# FileOperationLog read_all (REQ-AIYES21-009)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogReadAll:
    """read_all() returns records from all sessions."""

    def test_read_all_across_sessions(self, tmp_path: Path) -> None:
        """REQ-AIYES21-009: read_all() includes records from multiple sessions."""
        log = FileOperationLog(base_dir=str(tmp_path))
        log.append(_make_record(session_id="s1", command="inspect"))
        log.append(_make_record(session_id="s2", command="find"))

        all_records = log.read_all()
        commands = {r.command for r in all_records}
        assert "inspect" in commands
        assert "find" in commands
        assert len(all_records) >= 2

    def test_read_all_skips_dirs_without_operations_jsonl(self, tmp_path: Path) -> None:
        """REQ-AIYES21-009, FC-AIYES21-020: Dirs without operations.jsonl skipped."""
        log = FileOperationLog(base_dir=str(tmp_path))
        log.append(_make_record(session_id="s1", command="inspect"))

        # Create a session directory without operations.jsonl
        (tmp_path / "s2").mkdir(parents=True)

        all_records = log.read_all()
        assert len(all_records) == 1
        assert all_records[0].command == "inspect"

    def test_read_all_includes_global(self, tmp_path: Path) -> None:
        """REQ-AIYES21-009: read_all() includes _global records."""
        log = FileOperationLog(base_dir=str(tmp_path))
        log.append(_make_record(session_id="", command="doctor"))
        log.append(_make_record(session_id="s1", command="inspect"))

        all_records = log.read_all()
        commands = {r.command for r in all_records}
        assert "doctor" in commands
        assert "inspect" in commands


# ═══════════════════════════════════════════════════════════════════════
# FileOperationLog list_session_ids (REQ-AIYES21-010)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogListSessionIds:
    """list_session_ids() returns directory names of dirs with operations.jsonl."""

    def test_returns_session_ids_with_logs(self, tmp_path: Path) -> None:
        """REQ-AIYES21-010: Returns dirs containing operations.jsonl."""
        log = FileOperationLog(base_dir=str(tmp_path))
        log.append(_make_record(session_id="s1"))
        log.append(_make_record(session_id="s2"))

        ids = log.list_session_ids()
        assert "s1" in ids
        assert "s2" in ids

    def test_excludes_dirs_without_operations_jsonl(self, tmp_path: Path) -> None:
        """REQ-AIYES21-010: Dirs without operations.jsonl excluded."""
        log = FileOperationLog(base_dir=str(tmp_path))
        log.append(_make_record(session_id="s1"))
        (tmp_path / "s2").mkdir(parents=True)

        ids = log.list_session_ids()
        assert "s1" in ids
        assert "s2" not in ids

    def test_includes_global_when_present(self, tmp_path: Path) -> None:
        """REQ-AIYES21-010: _global included if it has operations.jsonl."""
        log = FileOperationLog(base_dir=str(tmp_path))
        log.append(_make_record(session_id=""))  # writes to _global

        ids = log.list_session_ids()
        assert "_global" in ids

    def test_empty_base_dir_returns_empty(self, tmp_path: Path) -> None:
        """REQ-AIYES21-010: No session dirs means empty list."""
        log = FileOperationLog(base_dir=str(tmp_path))
        ids = log.list_session_ids()
        assert ids == []


# ═══════════════════════════════════════════════════════════════════════
# FileOperationLog missing operations.jsonl (FC-AIYES21-020, REQ-AIYES21-032)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogReadMissing:
    """read() on missing operations.jsonl returns empty list."""

    def test_read_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """REQ-AIYES21-032: Session dir without operations.jsonl -> []."""
        (tmp_path / "s1").mkdir(parents=True)
        log = FileOperationLog(base_dir=str(tmp_path))
        records = log.read("s1")
        assert records == []

    def test_read_nonexistent_session_returns_empty(self, tmp_path: Path) -> None:
        """REQ-AIYES21-032: Non-existent session dir -> []."""
        log = FileOperationLog(base_dir=str(tmp_path))
        records = log.read("nonexistent-session")
        assert records == []

    def test_read_empty_file_returns_empty(self, tmp_path: Path) -> None:
        """FC-AIYES21-020: Empty operations.jsonl -> []."""
        session_dir = tmp_path / "s1"
        session_dir.mkdir(parents=True)
        (session_dir / "operations.jsonl").write_text("")

        log = FileOperationLog(base_dir=str(tmp_path))
        records = log.read("s1")
        assert records == []


# ═══════════════════════════════════════════════════════════════════════
# Concurrent append atomicity (FC-AIYES21-010, REQ-AIYES21-012)
# ═══════════════════════════════════════════════════════════════════════


def _worker_append(args: Tuple[str, int]) -> None:
    """Worker function for multiprocessing concurrent append test."""
    base_dir, worker_id = args
    log = FileOperationLog(base_dir=base_dir)
    record = OperationRecord(
        timestamp=1000.0 + worker_id,
        session_id="concurrent",
        command=f"cmd-{worker_id}",
        duration_ms=1.0,
        exit_code=0,
        error="",
    )
    log.append(record)


class TestFileOperationLogConcurrentAppend:
    """Concurrent append produces N valid JSONL lines with no interleaving."""

    def test_concurrent_processes_no_interleaving(self, tmp_path: Path) -> None:
        """REQ-AIYES21-012: N processes each append once -> N valid lines."""
        n_workers = 20
        base_dir = str(tmp_path)

        # Pre-create directory so workers don't race on mkdir
        (tmp_path / "concurrent").mkdir(parents=True)

        args = [(base_dir, i) for i in range(n_workers)]
        with multiprocessing.Pool(
            processes=min(n_workers, os.cpu_count() or 4)
        ) as pool:
            pool.map(_worker_append, args)

        jsonl_path = tmp_path / "concurrent" / "operations.jsonl"
        assert jsonl_path.exists()

        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == n_workers

        # Each line must be valid JSON
        parsed_commands = set()
        for line in lines:
            data = json.loads(line)
            assert "cmd" in data
            parsed_commands.add(data["cmd"])

        # All N distinct commands are present
        assert len(parsed_commands) == n_workers


# ═══════════════════════════════════════════════════════════════════════
# PIPE_BUF enforcement (FC-AIYES21-010, A10-002)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogPipeBuf:
    """Serialized JSONL lines must not exceed PIPE_BUF (4096 bytes)."""

    def test_oversize_error_truncated_to_pipe_buf(self, tmp_path: Path) -> None:
        """A10-002: Large error field is truncated so line <= 4096 bytes."""
        log = FileOperationLog(base_dir=str(tmp_path))
        big_error = "X" * 8000  # Way over 4096
        record = _make_record(session_id="s1", error=big_error, exit_code=1)
        log.append(record)

        jsonl_path = tmp_path / "s1" / "operations.jsonl"
        raw_bytes = jsonl_path.read_bytes()
        # The written line (including newline) must be <= 4096 bytes
        assert len(raw_bytes) <= 4096, (
            f"JSONL line is {len(raw_bytes)} bytes, exceeds PIPE_BUF 4096"
        )

        # The line must still be valid JSON
        line = raw_bytes.decode("utf-8").strip()
        parsed = json.loads(line)
        assert "err" in parsed
        # The truncated error ends with "..."
        assert parsed["err"].endswith("...")

    def test_normal_record_not_truncated(self, tmp_path: Path) -> None:
        """A10-002: Normal-sized records are written verbatim."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = _make_record(session_id="s1", error="short error", exit_code=1)
        log.append(record)

        jsonl_path = tmp_path / "s1" / "operations.jsonl"
        line = jsonl_path.read_text().strip()
        parsed = json.loads(line)
        assert parsed["err"] == "short error"

    def test_oversize_record_still_readable(self, tmp_path: Path) -> None:
        """A10-002: Oversize record, once truncated, can be read back."""
        log = FileOperationLog(base_dir=str(tmp_path))
        big_error = "Z" * 8000
        record = _make_record(session_id="s1", error=big_error, exit_code=1)
        log.append(record)

        records = log.read("s1")
        assert len(records) == 1
        assert records[0].exit_code == 1
        assert records[0].error.endswith("...")

    def test_oversize_command_truncated_to_pipe_buf(self, tmp_path: Path) -> None:
        """A10-002r2: Oversize command field (not just error) is truncated."""
        log = FileOperationLog(base_dir=str(tmp_path))
        big_cmd = "X" * 5000
        record = OperationRecord(
            timestamp=1.0,
            session_id="s1",
            command=big_cmd,
            duration_ms=10.0,
            exit_code=0,
            error="",
        )
        log.append(record)

        jsonl_path = tmp_path / "s1" / "operations.jsonl"
        raw_bytes = jsonl_path.read_bytes()
        assert len(raw_bytes) <= 4096, (
            f"JSONL line is {len(raw_bytes)} bytes, exceeds PIPE_BUF 4096"
        )
        line = raw_bytes.decode("utf-8").strip()
        parsed = json.loads(line)
        assert parsed["cmd"].endswith("...")

    def test_oversize_both_fields_truncated(self, tmp_path: Path) -> None:
        """A10-002r2: Both cmd and err oversize — truncated in order."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = OperationRecord(
            timestamp=1.0,
            session_id="s1",
            command="C" * 3000,
            duration_ms=10.0,
            exit_code=1,
            error="E" * 3000,
        )
        log.append(record)

        jsonl_path = tmp_path / "s1" / "operations.jsonl"
        raw_bytes = jsonl_path.read_bytes()
        assert len(raw_bytes) <= 4096


# ═══════════════════════════════════════════════════════════════════════
# Port contract: OperationLogPort (FC-AIYES21-002, REQ-AIYES21-002)
# ═══════════════════════════════════════════════════════════════════════


class TestOperationLogPortProtocol:
    """OperationLogPort is a Protocol with 4 methods."""

    def test_fake_satisfies_protocol(self) -> None:
        """REQ-AIYES21-002: FakeOperationLog structurally satisfies Protocol."""
        fake = FakeOperationLog()
        # Structural typing check — these methods must exist
        assert callable(fake.append)
        assert callable(fake.read)
        assert callable(fake.read_all)
        assert callable(fake.list_session_ids)

    def test_file_operation_log_satisfies_protocol(self, tmp_path: Path) -> None:
        """REQ-AIYES21-002: FileOperationLog satisfies OperationLogPort."""
        log = FileOperationLog(base_dir=str(tmp_path))
        assert callable(log.append)
        assert callable(log.read)
        assert callable(log.read_all)
        assert callable(log.list_session_ids)

    def test_protocol_has_no_mutating_methods(self) -> None:
        """FC-AIYES21-007: OperationLogPort has no update/delete/remove/clear/truncate."""
        port_methods = set(dir(OperationLogPort))
        forbidden = {"update", "delete", "remove", "clear", "truncate"}
        assert port_methods.isdisjoint(forbidden), (
            f"OperationLogPort has forbidden methods: {port_methods & forbidden}"
        )

    def test_consumer_side_append_and_read(self) -> None:
        """Port contract consumer test: append then read returns the record."""
        fake = FakeOperationLog()
        record = _make_record(session_id="s1")
        fake.append(record)
        result = fake.read("s1")
        assert len(result) == 1
        assert result[0] == record

    def test_provider_side_file_append_and_read(self, tmp_path: Path) -> None:
        """Port contract provider test: FileOperationLog append/read roundtrips."""
        log = FileOperationLog(base_dir=str(tmp_path))
        record = _make_record(session_id="s1")
        log.append(record)
        result = log.read("s1")
        assert len(result) == 1
        assert result[0] == record


# ═══════════════════════════════════════════════════════════════════════
# Domain purity (FC-AIYES21-001, REQ-AIYES21-029)
# ═══════════════════════════════════════════════════════════════════════


class TestOperationLogDomainPurity:
    """domain/operation_record.py has no external imports."""

    def test_no_non_stdlib_imports(self) -> None:
        """REQ-AIYES21-029: AST check for stdlib-only imports."""
        source_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "aiyes"
            / "domain"
            / "operation_record.py"
        )
        assert source_path.exists(), f"File not found: {source_path}"

        source = source_path.read_text()
        tree = ast.parse(source)

        allowed_prefixes = ("__future__", "dataclasses", "typing")
        # Allow domain imports (aiyes.domain.*, aiyes.ports.*)
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


# ═══════════════════════════════════════════════════════════════════════
# ClockPort exclusivity (FC-AIYES21-005, REQ-AIYES21-031)
# ═══════════════════════════════════════════════════════════════════════


class TestOperationLogNoDirectTimeCalls:
    """New files must not call datetime.now(), time.time(), or time.monotonic() directly."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/aiyes/domain/operation_record.py",
            "src/aiyes/ports/operation_log.py",
            "src/aiyes/adapters/file_operation_log.py",
            "src/aiyes/domain/use_cases/metrics.py",
            "src/aiyes/domain/use_cases/prune.py",
            "src/aiyes/cli/main.py",
            "src/aiyes/adapters/file_session_cleanup.py",
        ],
    )
    def test_no_forbidden_time_patterns(self, rel_path: str) -> None:
        """REQ-AIYES21-031, A10-006: No direct time acquisition in AIYES-21 files."""
        source_path = Path(__file__).resolve().parent.parent / rel_path
        if not source_path.exists():
            pytest.skip(f"File not yet created: {rel_path}")

        source = source_path.read_text()
        forbidden = ["datetime.now()", "time.time()", "time.monotonic()"]
        for pattern in forbidden:
            assert pattern not in source, (
                f"Forbidden time pattern {pattern!r} found in {rel_path}"
            )


# ═══════════════════════════════════════════════════════════════════════
# Performance (FC-AIYES21-024, REQ-AIYES21-034)
# ═══════════════════════════════════════════════════════════════════════


class TestFileOperationLogPerformance:
    """Logging overhead must be < 5ms per operation."""

    def test_append_mean_under_5ms(self, tmp_path: Path) -> None:
        """REQ-AIYES21-034: 1000 appends, mean < 5ms."""
        log = FileOperationLog(base_dir=str(tmp_path))
        n = 1000

        # Pre-create directory
        (tmp_path / "perf-session").mkdir(parents=True)

        start = time.monotonic()
        for i in range(n):
            record = OperationRecord(
                timestamp=1000.0 + i,
                session_id="perf-session",
                command="inspect",
                duration_ms=1.0,
                exit_code=0,
                error="",
            )
            log.append(record)
        elapsed = time.monotonic() - start

        mean_ms = (elapsed / n) * 1000.0
        assert mean_ms < 5.0, f"Mean append time {mean_ms:.2f}ms exceeds 5ms limit"
