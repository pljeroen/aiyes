"""Session resize use case — changes display resolution of a running session."""

from __future__ import annotations

import dataclasses

from aiyes.ports.clock import ClockPort
from aiyes.ports.display import DisplayServerPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class ResizeResult:
    """Immutable result of a session resize operation."""

    status: str
    resolution: str


class SessionResizeUseCase:
    """Resize the virtual display of an existing session."""

    def __init__(
        self,
        display_server: DisplayServerPort,
        session_repo: SessionRepositoryPort,
        clock: ClockPort,
    ) -> None:
        self._display_server = display_server
        self._session_repo = session_repo
        self._clock = clock

    def execute(
        self,
        session_id: str,
        resolution: str,
        settle: float = 0.5,
    ) -> ResizeResult:
        """Resize the session's display to the given resolution.

        Steps:
            1. Load session (raise if not found).
            2. Call display_server.resize (may raise on failure).
            3. Sleep for settle delay.
            4. Update session resolution via dataclasses.replace.
            5. Save updated session.
            6. Return ResizeResult.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        self._display_server.resize(session.display, resolution)

        self._clock.sleep(settle)

        new_session = dataclasses.replace(session, resolution=resolution)
        self._session_repo.save(new_session)

        return ResizeResult(status="ok", resolution=resolution)
