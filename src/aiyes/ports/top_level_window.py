"""Top-level window port — Protocol for enumerating top-level windows."""

from __future__ import annotations

from typing import List, Protocol

from aiyes.domain.top_level_window import TopLevelWindow


class TopLevelWindowPort(Protocol):
    """Port for enumerating top-level windows in a session."""

    def list_top_level_windows(self, session: object) -> List[TopLevelWindow]:
        """List all top-level windows visible in the session.

        Returns an empty list if the query fails (no exception propagated).
        """
        ...
