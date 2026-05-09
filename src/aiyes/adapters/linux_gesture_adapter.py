"""LinuxGestureAdapter — stub for GesturePort on Linux.

Desktop Linux doesn't use pinch/multi-touch gestures.
All methods raise RuntimeError with a clear message.
"""

from __future__ import annotations


class LinuxGestureAdapter:
    """Gesture stub for Linux — all methods raise RuntimeError."""

    def pinch(self, session, x: int, y: int, scale_factor: float) -> None:
        """Not supported on Linux."""
        raise RuntimeError("Gestures not supported on Linux backend")

    def two_finger_scroll(
        self, session, x: int, y: int, direction: str, amount: int = 3
    ) -> None:
        """Not supported on Linux."""
        raise RuntimeError("Gestures not supported on Linux backend")
