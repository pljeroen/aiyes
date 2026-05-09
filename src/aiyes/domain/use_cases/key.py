"""Key use case — send keyboard events."""

from __future__ import annotations

import dataclasses
from typing import List

from aiyes.ports.input import InputPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class KeyResult:
    """Result of a key operation."""

    status: str = "ok"


class KeyUseCase:
    """Send key events to the session display."""

    def __init__(
        self,
        input_port: InputPort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._input = input_port
        self._session_repo = session_repo

    def execute(self, session_id: str, key_specs: List[str]) -> KeyResult:
        """Send key events."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        self._input.key(session, key_specs)
        return KeyResult()
