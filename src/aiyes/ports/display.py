"""Display server port — Protocol for Xvfb management."""

from __future__ import annotations

from typing import Protocol


class DisplayServerPort(Protocol):
    """Port for managing the virtual display server (Xvfb)."""

    def start(self, display_num: int, resolution: str, color_depth: int) -> int:
        """Start display server, return PID."""
        ...

    def stop(self, pid: int) -> None:
        """Stop display server by PID."""
        ...

    def resize(self, display: str, resolution: str) -> None:
        """Resize the display to the given resolution."""
        ...

    def configure_keyboard(self, display: str) -> None:
        """Configure keyboard layout for the display. Best-effort, never fatal."""
        ...
