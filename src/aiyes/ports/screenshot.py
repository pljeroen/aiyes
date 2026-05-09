"""Screenshot port — Protocol for taking screenshots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol

if TYPE_CHECKING:
    from aiyes.domain.session import Session


class ScreenshotPort(Protocol):
    """Port for taking screenshots of a session."""

    def take(self, session: "Session", output_path: Optional[str] = None) -> str:
        """Take a screenshot. Returns path to the screenshot file."""
        ...
