"""Type text use case — character-by-character text input."""

from __future__ import annotations

import dataclasses

from aiyes.ports.input import InputPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class TypeResult:
    """Result of a type operation."""

    status: str = "ok"


class TypeTextUseCase:
    """Type text character by character on the session display."""

    def __init__(
        self,
        input_port: InputPort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._input = input_port
        self._session_repo = session_repo

    def execute(self, session_id: str, text: str, delay_ms: int = 0) -> TypeResult:
        """Type text on the session's display.

        Args:
            session_id: Target session.
            text: Text to type.
            delay_ms: Inter-character delay in milliseconds.  On Android,
                when delay_ms is 0 a default of 20 ms is applied to prevent
                character dropping.  Pass an explicit value > 0 to override.
                On Linux (xdotool), 0 means no delay.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        self._input.type_text(session, text, delay_ms=delay_ms)
        return TypeResult()
