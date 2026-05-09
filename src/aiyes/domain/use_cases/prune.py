"""Prune use case — remove stale session directories.

Lists sessions via cleanup port, checks liveness via process port,
computes age from started_at or mtime fallback, and deletes old dead sessions.
Optionally detects orphaned Xvfb/dbus processes (best-effort).
"""

from __future__ import annotations

from typing import List, Optional

from aiyes.domain.operation_record import PruneResult
from aiyes.domain.use_cases.session_liveness import is_session_active
from aiyes.ports.clock import ClockPort
from aiyes.ports.process import ProcessPort
from aiyes.ports.session_cleanup import SessionCleanupPort
from aiyes.ports.storage import SessionRepositoryPort


class PruneUseCase:
    """Prune stale session directories based on age and liveness."""

    def __init__(
        self,
        session_repo: SessionRepositoryPort,
        cleanup: SessionCleanupPort,
        process: ProcessPort,
        clock: ClockPort,
        orphan_scanner: Optional[object] = None,
    ) -> None:
        self._session_repo = session_repo
        self._cleanup = cleanup
        self._process = process
        self._clock = clock
        self._orphan_scanner = orphan_scanner

    def execute(
        self,
        max_age_hours: float = 72.0,
        dry_run: bool = False,
        detect_orphans: bool = False,
    ) -> PruneResult:
        """Execute prune operation.

        Lists all session directories, checks each for liveness and age.
        Deletes old dead sessions (unless dry_run=True).
        If detect_orphans=True, scans for orphaned Xvfb/dbus processes.
        """
        now = self._clock.now()
        max_age_seconds = max_age_hours * 3600.0

        session_dirs = self._cleanup.list_session_directories()

        pruned: List[str] = []
        skipped_active = 0

        for sid in session_dirs:
            session = self._session_repo.load(sid)

            # Determine liveness using shared function
            if session is not None:
                is_alive = is_session_active(session, self._process)
            else:
                # No session.json -> treated as dead
                is_alive = False

            if is_alive:
                skipped_active += 1
                continue

            # Determine age
            if session is not None and session.started_at > 0:
                age = now - session.started_at
            else:
                # Fallback to directory mtime
                mtime = self._cleanup.get_session_mtime(sid)
                if mtime is not None:
                    age = now - mtime
                else:
                    # No mtime available, skip
                    continue

            if age <= max_age_seconds:
                continue

            # Session is dead and old enough
            pruned.append(sid)

            if not dry_run:
                self._cleanup.delete_session_directory(sid)

        # Orphan detection (best-effort)
        orphans_found = 0
        orphans_killed = 0
        if detect_orphans and self._orphan_scanner is not None:
            scan = getattr(self._orphan_scanner, "scan_orphan_xvfb", None)
            if scan is not None:
                try:
                    orphan_pids = scan(session_dirs)
                    orphans_found = len(orphan_pids)
                    if not dry_run:
                        kill = getattr(self._orphan_scanner, "kill_orphans", None)
                        if kill is not None:
                            orphans_killed = kill(orphan_pids)
                except Exception:
                    pass  # Best-effort — don't fail prune on scan errors

        return PruneResult(
            pruned_count=len(pruned),
            skipped_active=skipped_active,
            dry_run=dry_run,
            sessions_pruned=tuple(pruned),
            orphans_found=orphans_found,
            orphans_killed=orphans_killed,
        )
