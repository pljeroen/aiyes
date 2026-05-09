"""FileOperationLog — implements OperationLogPort via JSONL files.

Operations are stored as ~/.aieyes/<session-id>/operations.jsonl.
Session-less commands (session_id="") log to ~/.aieyes/_global/operations.jsonl.
Uses os.open(O_WRONLY|O_APPEND|O_CREAT) + os.write() for POSIX atomic appends.

JSONL serialization mapping lives here in the adapter layer, not in the domain.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from aiyes.domain.operation_record import OperationRecord
from aiyes.domain.session import validate_session_id


# ─── JSONL mapping (adapter concern) ─────────────────────────────────


def _record_to_jsonl_dict(record: OperationRecord) -> dict:
    """Serialize an OperationRecord to a dict with short JSONL keys.

    This is adapter-layer logic: the domain value object has no format awareness.
    """
    d: dict = {
        "ts": record.timestamp,
        "sid": record.session_id,
        "cmd": record.command,
        "dur_ms": record.duration_ms,
        "exit": record.exit_code,
    }
    if record.error:
        d["err"] = record.error
    return d


def _record_from_jsonl_dict(d: dict) -> OperationRecord:
    """Deserialize from a dict with short JSONL keys to an OperationRecord.

    This is adapter-layer logic: the domain value object has no format awareness.
    """
    return OperationRecord(
        timestamp=d["ts"],
        session_id=d["sid"],
        command=d["cmd"],
        duration_ms=d["dur_ms"],
        exit_code=d["exit"],
        error=d.get("err", ""),
    )


class FileOperationLog:
    """File-based append-only operation log with entry count rotation."""

    # Default max entries per session log file. 10000 entries at ~200 bytes
    # each is ~2MB — reasonable for CLI operation logs.
    _DEFAULT_MAX_ENTRIES = 10000

    def __init__(self, base_dir: Optional[str] = None, max_entries: int = 0) -> None:
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".aieyes")
        self._base_dir = Path(base_dir)
        self._max_entries = (
            max_entries if max_entries > 0 else self._DEFAULT_MAX_ENTRIES
        )

    def _resolve_path(self, session_id: str) -> Path:
        """Map session_id to operations.jsonl path.

        Empty session_id maps to _global/operations.jsonl without going
        through validate_session_id (which rejects empty strings).
        """
        if session_id == "" or session_id == "_global":
            return self._base_dir / "_global" / "operations.jsonl"
        validate_session_id(session_id)
        return self._base_dir / session_id / "operations.jsonl"

    # POSIX PIPE_BUF — maximum bytes for guaranteed atomic write
    _PIPE_BUF = 4096

    def append(self, record: OperationRecord) -> None:
        """Append a single operation record as a JSONL line.

        Uses os.open with O_WRONLY|O_APPEND|O_CREAT for POSIX atomic writes.
        Parent directory created with mkdir(parents=True, exist_ok=True).

        Enforces PIPE_BUF (4096 bytes) ceiling: if the serialized line exceeds
        the limit, the error field is truncated until the line fits.
        """
        path = self._resolve_path(record.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(path.parent), 0o700)

        d = _record_to_jsonl_dict(record)
        line = json.dumps(d, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")

        # Enforce PIPE_BUF ceiling by truncating variable-length fields.
        # Try err first (least important), then cmd if still over.
        if len(data) > self._PIPE_BUF:
            for field in ("err", "cmd"):
                if field not in d or not d[field]:
                    continue
                value = d[field]
                value_bytes = value.encode("utf-8")
                overhead = len(data) - len(value_bytes)
                max_bytes = self._PIPE_BUF - overhead - 3  # 3 = len("...")
                if max_bytes < 0:
                    max_bytes = 0
                truncated = value_bytes[:max_bytes].decode("utf-8", errors="ignore")
                d[field] = truncated + "..."
                line = json.dumps(d, separators=(",", ":")) + "\n"
                data = line.encode("utf-8")
                if len(data) <= self._PIPE_BUF:
                    break

        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

        # Rotate after append if entry count exceeds limit
        self._rotate_if_needed(path)

    def _rotate_if_needed(self, path: Path) -> None:
        """Drop oldest entries when file exceeds max_entries.

        Reads the file, keeps only the last max_entries lines, and
        rewrites the file atomically via a temp file + rename.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return

        if len(lines) <= self._max_entries:
            return

        # Keep only the newest entries
        kept = lines[-self._max_entries :]
        tmp_path = path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(kept)
            tmp_path.replace(path)
        except OSError:
            # Best-effort: if rotation fails, leave file as-is
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def read(self, session_id: str) -> List[OperationRecord]:
        """Read all records for a session. Silently skips corrupt/empty lines."""
        path = self._resolve_path(session_id)
        if not path.exists():
            return []

        records: List[OperationRecord] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    records.append(_record_from_jsonl_dict(d))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
        return records

    def read_all(self) -> List[OperationRecord]:
        """Read all records across all sessions including _global."""
        records: List[OperationRecord] = []
        if not self._base_dir.exists():
            return records

        for entry in self._base_dir.iterdir():
            if entry.is_dir():
                jsonl_path = entry / "operations.jsonl"
                if jsonl_path.exists():
                    # Read using the directory name as session_id
                    # For _global dir, we read directly
                    session_records = self._read_file(jsonl_path)
                    records.extend(session_records)
        return records

    def list_session_ids(self) -> List[str]:
        """Return directory names that contain operations.jsonl."""
        result: List[str] = []
        if not self._base_dir.exists():
            return result

        for entry in self._base_dir.iterdir():
            if entry.is_dir():
                if (entry / "operations.jsonl").exists():
                    result.append(entry.name)
        return result

    def _read_file(self, path: Path) -> List[OperationRecord]:
        """Read JSONL file, skipping corrupt/empty lines."""
        records: List[OperationRecord] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    records.append(_record_from_jsonl_dict(d))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
        return records
