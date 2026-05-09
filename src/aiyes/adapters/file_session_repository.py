"""FileSessionRepository — implements SessionRepositoryPort via JSON files.

Sessions are stored as ~/.aieyes/<session-id>/session.json.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import List, Optional

from aiyes.domain.session import Session, validate_session_id


class FileSessionRepository:
    """File-based session persistence."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".aieyes")
        self._base_dir = Path(base_dir)

    def _safe_session_dir(self, session_id: str) -> Path:
        """Validate session_id and return the session directory path.

        Raises ValueError if the session_id contains path traversal characters.
        """
        validate_session_id(session_id)
        return self._base_dir / session_id

    def save(self, session: Session) -> None:
        """Save a session to disk as JSON."""
        session_dir = self._safe_session_dir(session.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(str(session_dir), 0o700)

        session_file = session_dir / "session.json"
        data = dataclasses.asdict(session)
        # app_args: tuple -> list for JSON
        data["app_args"] = list(session.app_args)

        content = json.dumps(data, indent=2)
        fd = os.open(str(session_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)

    def load(self, session_id: str) -> Optional[Session]:
        """Load a session from disk. Returns None if not found."""
        session_file = self._safe_session_dir(session_id) / "session.json"
        if not session_file.exists():
            return None

        data = json.loads(session_file.read_text())
        # app_args: list -> tuple
        data["app_args"] = tuple(data.get("app_args", []))
        return Session(**data)

    def load_all(self) -> List[Session]:
        """Load all sessions from disk."""
        sessions: List[Session] = []
        if not self._base_dir.exists():
            return sessions

        for entry in self._base_dir.iterdir():
            if entry.is_dir():
                try:
                    session = self.load(entry.name)
                except (ValueError, json.JSONDecodeError, TypeError):
                    continue
                if session is not None:
                    sessions.append(session)

        return sessions

    def delete(self, session_id: str) -> None:
        """Delete the session.json file (not the directory)."""
        session_file = self._safe_session_dir(session_id) / "session.json"
        if session_file.exists():
            session_file.unlink()
