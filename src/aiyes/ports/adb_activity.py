"""ADB activity query port — Protocol for checking Android foreground activity."""

from __future__ import annotations

from typing import Optional, Protocol


class AdbActivityQueryPort(Protocol):
    """Port for querying the currently resumed Android activity."""

    def get_resumed_activity(self, serial: str) -> Optional[str]:
        """Get the currently resumed activity package/class string.

        Returns None if the query fails or no activity is resumed.
        """
        ...
