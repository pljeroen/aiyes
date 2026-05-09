"""Session repository port — Protocol for session persistence."""

from __future__ import annotations

from typing import List, Optional, Protocol

from aiyes.domain.session import Session


class SessionRepositoryPort(Protocol):
    """Port for persisting and loading sessions."""

    def save(self, session: Session) -> None:
        """Save a session."""
        ...

    def load(self, session_id: str) -> Optional[Session]:
        """Load a session by ID."""
        ...

    def load_all(self) -> List[Session]:
        """Load all sessions."""
        ...

    def delete(self, session_id: str) -> None:
        """Delete a session by ID."""
        ...
