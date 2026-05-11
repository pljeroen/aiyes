"""Gesture use case — restricted/best-effort gestures (Android only)."""

from __future__ import annotations

import dataclasses

from aiyes.ports.gesture import GesturePort
from aiyes.ports.storage import SessionRepositoryPort


_VALID_DIRECTIONS = frozenset(["up", "down", "left", "right"])


@dataclasses.dataclass(frozen=True)
class GestureResult:
    """Result of a gesture operation."""

    status: str = "ok"


class GestureUseCase:
    """Gesture control: pinch and two-finger scroll."""

    def __init__(
        self,
        gesture_port: GesturePort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._gesture = gesture_port
        self._session_repo = session_repo

    def pinch(
        self, session_id: str, x: int, y: int, scale_factor: float
    ) -> GestureResult:
        """Pinch gesture at (x, y) with given scale factor."""
        if scale_factor <= 0.0:
            raise ValueError(f"scale_factor must be positive, got {scale_factor}")

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # Gestures are Android only — the port will raise for Linux
        self._gesture.pinch(session, x, y, scale_factor)
        return GestureResult()

    def two_finger_scroll(
        self,
        session_id: str,
        x: int,
        y: int,
        direction: str,
        amount: int = 3,
    ) -> GestureResult:
        """Two-finger scroll at (x, y) in the given direction."""
        if amount <= 0:
            raise ValueError(f"amount must be positive, got {amount}")

        if direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"Invalid scroll direction: {direction!r}. "
                f"Must be one of: {sorted(_VALID_DIRECTIONS)}"
            )

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # Gestures are Android only — the port will raise for Linux
        self._gesture.two_finger_scroll(session, x, y, direction, amount)
        return GestureResult()

    def swipe(
        self,
        session_id: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> GestureResult:
        """Single-finger swipe from (x1, y1) to (x2, y2) over duration_ms.

        Distinct from two_finger_scroll — this is the natural list-scroll
        primitive on Android, equivalent to UiScrollable.scrollIntoView()'s
        underlying gesture. On Linux the port routes to a mouse-drag
        substitute so scroll_into_view can dispatch swipe cross-platform.
        """
        if duration_ms < 0:
            raise ValueError(f"duration_ms must be non-negative, got {duration_ms}")
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")
        self._gesture.swipe(session, x1, y1, x2, y2, duration_ms)
        return GestureResult()
