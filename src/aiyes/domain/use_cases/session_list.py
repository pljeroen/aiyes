"""Session list use case — lists all sessions with status."""

from __future__ import annotations

import dataclasses
from typing import List, Optional

from aiyes.domain.use_cases.session_liveness import is_session_active
from aiyes.ports.android_app_lifecycle import AndroidAppLifecyclePort
from aiyes.ports.clock import ClockPort
from aiyes.ports.process import ProcessPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class SessionListEntry:
    """A single entry in the session list."""

    session_id: str
    display: str
    app: str
    status: str
    uptime: Optional[float] = None
    backend: str = "linux"


class SessionListUseCase:
    """List all sessions with their status (active or stale)."""

    def __init__(
        self,
        session_repo: SessionRepositoryPort,
        process: ProcessPort,
        clock: ClockPort,
        android_lifecycle: Optional[AndroidAppLifecyclePort] = None,
    ) -> None:
        self._session_repo = session_repo
        self._process = process
        self._clock = clock
        self._android_lifecycle = android_lifecycle

    def execute(self, active_only: bool = False) -> List[SessionListEntry]:
        """List all sessions.

        Checks if processes are still running to determine active vs stale status.
        Computes uptime from started_at for active sessions.
        """
        sessions = self._session_repo.load_all()
        now = self._clock.now()

        entries: List[SessionListEntry] = []
        for session in sessions:
            active = is_session_active(
                session, self._process, self._android_lifecycle
            )
            status = "active" if active else "stale"
            if active_only and status != "active":
                continue

            # Compute uptime for active sessions
            uptime: Optional[float] = None
            if status == "active" and session.started_at > 0:
                uptime = now - session.started_at

            entries.append(
                SessionListEntry(
                    session_id=session.session_id,
                    display=session.display,
                    app=session.app_command,
                    status=status,
                    uptime=uptime,
                    backend=getattr(session, "backend", "linux"),
                )
            )

        return entries
