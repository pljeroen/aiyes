"""Session cleanup port — Protocol for session directory lifecycle."""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class SessionCleanupPort(Protocol):
    """Port for session directory management (list, delete, mtime)."""

    def delete_session_directory(self, session_id: str) -> None:
        """Delete a session directory and all its contents."""
        ...

    def list_session_directories(self) -> List[str]:
        """List session directory names (excludes reserved dirs)."""
        ...

    def get_session_mtime(self, session_id: str) -> Optional[float]:
        """Return directory modification time as epoch float, or None if missing."""
        ...
