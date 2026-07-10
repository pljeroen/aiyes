"""Screenshot store port — Protocol for persisting screenshots."""

from __future__ import annotations

from typing import Optional, Protocol, Tuple


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

    def read_dimensions(self, path: str) -> Optional[Tuple[int, int]]:
        """Read the image's (width, height) in pixels from its encoded bytes.

        Returns None when dimensions are unavailable — unrecognized magic
        bytes, a truncated/corrupt header, or a missing file. None is the
        degrade sentinel; expected degradation MUST NOT raise.
        """
        ...

    def delete_temp(self, path: str) -> None:
        """Delete a temporary screenshot file."""
        ...
