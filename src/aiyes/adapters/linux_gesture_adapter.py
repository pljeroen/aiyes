"""LinuxGestureAdapter — partial GesturePort on Linux.

Desktop Linux doesn't use pinch/multi-touch gestures. swipe is
supported as a synthetic mouse drag because scroll_into_view dispatches
to swipe on both platforms — Xvfb mouse-drag is the closest analogue
to a touch swipe and enables cross-platform list scrolling.
"""

from __future__ import annotations

from typing import Any


class LinuxGestureAdapter:
    """Gesture stub for Linux — pinch and two_finger_scroll unsupported.

    swipe routes to an injected mouse adapter (drag) since Xvfb has no
    touch input.
    """

    def __init__(self, mouse: Any = None) -> None:
        self._mouse = mouse

    def pinch(self, session, x: int, y: int, scale_factor: float) -> None:
        """Not supported on Linux."""
        raise RuntimeError("Gestures not supported on Linux backend")

    def two_finger_scroll(
        self, session, x: int, y: int, direction: str, amount: int = 3
    ) -> None:
        """Not supported on Linux."""
        raise RuntimeError("Gestures not supported on Linux backend")

    def swipe(
        self,
        session,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> None:
        """Synthetic swipe via mouse drag on Xvfb."""
        if self._mouse is None:
            raise RuntimeError("Linux swipe requires a mouse adapter; none injected")
        self._mouse.drag(session.session_id, x1, y1, x2, y2)
