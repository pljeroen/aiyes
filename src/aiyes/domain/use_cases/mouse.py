"""Mouse use case — mouse control commands."""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.ports.input import InputPort
from aiyes.ports.storage import SessionRepositoryPort


_VALID_DIRECTIONS = frozenset(["up", "down", "left", "right"])


@dataclasses.dataclass(frozen=True)
class MouseResult:
    """Result of a mouse operation."""

    status: str = "ok"


class MouseUseCase:
    """Mouse control: move, click, drag, scroll."""

    def __init__(
        self,
        input_port: InputPort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._input = input_port
        self._session_repo = session_repo

    def _get_session(self, session_id: str):
        """Resolve session_id to Session object."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")
        return session

    def execute(
        self,
        session_id: str,
        action: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        x1: Optional[int] = None,
        y1: Optional[int] = None,
        x2: Optional[int] = None,
        y2: Optional[int] = None,
        button: str = "left",
        direction: str = "up",
        amount: int = 3,
    ) -> MouseResult:
        """Dispatch mouse action by name."""
        if action == "move" and x is not None and y is not None:
            return self.move(session_id, x, y)
        elif action == "click":
            return self.click(session_id, x, y, button)
        elif action == "drag":
            return self.drag(
                session_id,
                x1 if x1 is not None else 0,
                y1 if y1 is not None else 0,
                x2 if x2 is not None else 0,
                y2 if y2 is not None else 0,
            )
        elif action == "scroll":
            return self.scroll(session_id, direction, amount)
        raise ValueError(f"Unknown mouse action: {action!r}")

    def move(self, session_id: str, x: int, y: int) -> MouseResult:
        """Move mouse to (x, y)."""
        session = self._get_session(session_id)
        self._input.mouse_move(session, x, y)
        return MouseResult()

    def click(
        self,
        session_id: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
    ) -> MouseResult:
        """Click at (x, y) or current position."""
        session = self._get_session(session_id)
        self._input.mouse_click(session, x, y, button)
        return MouseResult()

    def drag(self, session_id: str, x1: int, y1: int, x2: int, y2: int) -> MouseResult:
        """Drag from (x1, y1) to (x2, y2)."""
        session = self._get_session(session_id)
        self._input.mouse_drag(session, x1, y1, x2, y2)
        return MouseResult()

    def scroll(self, session_id: str, direction: str, amount: int = 3) -> MouseResult:
        """Scroll in direction by amount."""
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"Invalid scroll direction: {direction!r}. "
                f"Must be one of: {sorted(_VALID_DIRECTIONS)}"
            )
        session = self._get_session(session_id)
        self._input.mouse_scroll(session, direction, amount)
        return MouseResult()
