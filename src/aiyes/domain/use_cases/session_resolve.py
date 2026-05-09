"""Session resolve use case — auto-select session when only one is active."""

from __future__ import annotations

from typing import List, Optional

from aiyes.domain.use_cases.session_liveness import is_session_active
from aiyes.ports.android_app_lifecycle import AndroidAppLifecyclePort
from aiyes.ports.process import ProcessPort
from aiyes.ports.storage import SessionRepositoryPort


class SessionResolveUseCase:
    """Resolve a session ID.

    If session_id is given, use it directly.
    If None, find the sole active session or raise an error.
    """

    def __init__(
        self,
        session_repo: SessionRepositoryPort,
        process: ProcessPort,
        android_lifecycle: Optional[AndroidAppLifecyclePort] = None,
    ) -> None:
        self._session_repo = session_repo
        self._process = process
        self._android_lifecycle = android_lifecycle

    def _is_session_active(self, session: object) -> bool:
        """Check if a session is active based on its backend."""
        return is_session_active(session, self._process, self._android_lifecycle)

    def execute(self, session_id: Optional[str] = None) -> str:
        """Resolve to a concrete session ID.

        Returns the given session_id if not None.
        Otherwise finds the sole active session.
        Uses backend-aware liveness rules.
        Raises RuntimeError if no active sessions or multiple active sessions.
        """
        if session_id is not None:
            return session_id

        sessions = self._session_repo.load_all()

        active: List[str] = []
        for s in sessions:
            if self._is_session_active(s):
                active.append(s.session_id)

        if len(active) == 0:
            raise RuntimeError(
                "No active sessions found. Run 'aieyes session start' to start one."
            )
        if len(active) > 1:
            raise RuntimeError(
                f"Multiple sessions found, specify one: {active}. "
                "Run 'aieyes session list' to see available sessions."
            )

        return active[0]
