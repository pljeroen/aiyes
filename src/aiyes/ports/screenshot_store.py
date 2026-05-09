"""Screenshot store port — Protocol for persisting screenshots."""

from __future__ import annotations

from typing import Protocol


class ScreenshotStorePort(Protocol):
    """Port for persisting screenshots in session directories."""

    def save_screenshot(self, session_id: str, source_path: str) -> str:
        """Save screenshot, return final path."""
        ...

    def get_screenshot_path(self, session_id: str) -> str:
        """Get the screenshot path for a session."""
        ...

    def read_screenshot_bytes(self, session_id: str) -> bytes:
        """Read the saved screenshot file as raw bytes."""
        ...

    def delete_temp(self, path: str) -> None:
        """Delete a temporary screenshot file."""
        ...
