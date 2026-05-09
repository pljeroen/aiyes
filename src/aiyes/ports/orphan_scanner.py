"""Orphan scanner port — Protocol for detecting orphaned processes.

Detects Xvfb and dbus-daemon processes that no longer belong to any
active session. Best-effort: should not fail if /proc is unavailable.
"""

from __future__ import annotations

from typing import List, Protocol


class OrphanScannerPort(Protocol):
    """Port for scanning and cleaning up orphaned session processes."""

    def scan_orphan_xvfb(self, known_session_dirs: List[str]) -> List[int]:
        """Scan for Xvfb processes not associated with any known session.

        Returns a list of orphaned PIDs. Best-effort: returns empty list
        on platforms where /proc is unavailable.
        """
        ...

    def kill_orphans(self, pids: List[int]) -> int:
        """Attempt to kill orphaned processes. Returns count of killed PIDs."""
        ...
