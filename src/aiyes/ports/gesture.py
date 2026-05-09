"""Gesture port — Protocol for restricted/best-effort gestures."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from aiyes.domain.session import Session


class GesturePort(Protocol):
    """Port for pinch and two-finger gesture input."""

    def pinch(self, session: "Session", x: int, y: int, scale_factor: float) -> None:
        """Pinch gesture at (x, y) with given scale factor.

        scale_factor > 1.0 = zoom in, scale_factor < 1.0 = zoom out.
        """
        ...

    def two_finger_scroll(
        self, session: "Session", x: int, y: int, direction: str, amount: int = 3
    ) -> None:
        """Two-finger scroll at (x, y) in given direction."""
        ...
