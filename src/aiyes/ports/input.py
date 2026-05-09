"""Input port — Protocol for mouse and keyboard input."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Protocol

if TYPE_CHECKING:
    from aiyes.domain.session import Session


class InputPort(Protocol):
    """Port for sending input events to a session."""

    def mouse_move(self, session: "Session", x: int, y: int) -> None:
        """Move mouse to coordinates."""
        ...

    def mouse_click(
        self,
        session: "Session",
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
    ) -> None:
        """Click at position (or current position if x/y are None)."""
        ...

    def mouse_drag(
        self, session: "Session", x1: int, y1: int, x2: int, y2: int
    ) -> None:
        """Drag from (x1,y1) to (x2,y2)."""
        ...

    def mouse_scroll(self, session: "Session", direction: str, amount: int = 3) -> None:
        """Scroll in direction by amount."""
        ...

    def key(self, session: "Session", key_specs: List[str]) -> None:
        """Send key events."""
        ...

    def type_text(self, session: "Session", text: str, delay_ms: int = 0) -> None:
        """Type text character by character.

        Args:
            session: Target session.
            text: Text to type.
            delay_ms: Inter-character delay in milliseconds.  On Android,
                when delay_ms is 0 a default of 20 ms is applied to prevent
                character dropping.  Pass an explicit value > 0 to override.
                On Linux (xdotool), 0 means no delay.
        """
        ...
