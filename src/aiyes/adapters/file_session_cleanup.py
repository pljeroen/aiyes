"""FileSessionCleanup — implements SessionCleanupPort via filesystem operations.

Manages session directory listing, deletion, and mtime queries.
Protects reserved directories (_global, lessons) from deletion.
Validates session_id against path traversal before any filesystem operation.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional


_RESERVED_DIRS = frozenset(("_global", "lessons"))


def _validate_session_id(session_id: str) -> None:
    """Reject session_id values that could escape the base directory.

    Raises ValueError for: empty strings, path separators, '..',
    absolute paths, and reserved directory names (after normalization).
    """
    if not session_id:
        raise ValueError("session_id must not be empty")

    # Reject path separators and parent directory references
    if os.sep in session_id or (os.altsep and os.altsep in session_id):
        raise ValueError(f"session_id contains path separator: {session_id!r}")
    if "/" in session_id:
        raise ValueError(f"session_id contains path separator: {session_id!r}")
    if ".." in session_id:
        raise ValueError(f"session_id contains '..': {session_id!r}")

    # Reject absolute paths (e.g. "/etc/passwd")
    if os.path.isabs(session_id):
        raise ValueError(f"session_id is an absolute path: {session_id!r}")

    # Reject reserved names (normalized)
    normalized = session_id.strip().lower()
    if normalized in _RESERVED_DIRS:
        raise ValueError(f"Cannot target reserved directory: {session_id!r}")


class FileSessionCleanup:
    """File-based session cleanup operations."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".aieyes")
        self._base_dir = Path(base_dir).resolve()

    def list_session_directories(self) -> List[str]:
        """List session directory names, excluding _global and lessons."""
        result: List[str] = []
        if not self._base_dir.exists():
            return result

        for entry in self._base_dir.iterdir():
            if entry.is_dir() and entry.name not in _RESERVED_DIRS:
                result.append(entry.name)
        return result

    def delete_session_directory(self, session_id: str) -> None:
        """Delete a session directory. Blocks reserved and traversal paths.

        Raises ValueError if session_id is invalid or targets a reserved
        directory. Performs resolved-path containment check before delete.
        """
        _validate_session_id(session_id)
        target = (self._base_dir / session_id).resolve()

        # Containment check: resolved target must be inside base_dir
        base_resolved = self._base_dir.resolve()
        try:
            target.relative_to(base_resolved)
        except ValueError:
            raise ValueError(
                f"session_id resolves outside base directory: {session_id!r}"
            )

        if target.exists():
            shutil.rmtree(target)

    def get_session_mtime(self, session_id: str) -> Optional[float]:
        """Return directory modification time as epoch float, or None.

        Validates session_id and checks resolved-path containment before stat.
        """
        _validate_session_id(session_id)
        target = (self._base_dir / session_id).resolve()

        # Containment check
        base_resolved = self._base_dir.resolve()
        try:
            target.relative_to(base_resolved)
        except ValueError:
            raise ValueError(
                f"session_id resolves outside base directory: {session_id!r}"
            )

        try:
            return target.stat().st_mtime
        except FileNotFoundError:
            return None
