"""Clipboard use case — read and write clipboard contents."""

from __future__ import annotations

import dataclasses

from aiyes.ports.clipboard import ClipboardPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class ClipboardReadResult:
    """Result of a clipboard read operation."""

    text: str


@dataclasses.dataclass(frozen=True)
class ClipboardWriteResult:
    """Result of a clipboard write operation."""

    status: str = "ok"


class ClipboardUseCase:
    """Read and write clipboard contents for a session."""

    def __init__(
        self,
        clipboard_port: ClipboardPort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._clipboard = clipboard_port
        self._session_repo = session_repo

    def read(self, session_id: str) -> ClipboardReadResult:
        """Read clipboard text for the given session."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        text = self._clipboard.read(session)
        return ClipboardReadResult(text=text)

    def write(self, session_id: str, text: str) -> ClipboardWriteResult:
        """Write text to clipboard for the given session."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        self._clipboard.write(session, text)
        return ClipboardWriteResult()
