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
