"""Window query port — Protocol for querying active window information."""

from __future__ import annotations

from typing import Optional, Protocol


class WindowQueryPort(Protocol):
    """Port for querying window ownership via window manager tools."""

    def get_active_window_id(self, display: str) -> Optional[str]:
        """Get the active window ID for the given display.

        Returns None if no active window or query fails.
        """
        ...

    def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
        """Get the PID that owns the given window ID.

        Returns None if the PID cannot be determined or query fails.
        """
        ...
