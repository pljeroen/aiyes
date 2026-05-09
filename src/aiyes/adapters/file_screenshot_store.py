"""FileScreenshotStore — implements ScreenshotStorePort.

Screenshots are stored as ~/.aieyes/<session-id>/screenshot.png.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from aiyes.domain.session import validate_session_id


class FileScreenshotStore:
    """File-based screenshot persistence."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".aieyes")
        self._base_dir = Path(base_dir)

    def _safe_session_dir(self, session_id: str) -> Path:
        """Validate session_id and return the session directory path.

        Raises ValueError if the session_id contains path traversal characters.
        """
        validate_session_id(session_id)
        return self._base_dir / session_id

    def save_screenshot(self, session_id: str, source_path: str) -> str:
        """Copy screenshot to session directory. Returns destination path."""
        session_dir = self._safe_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(str(session_dir), 0o700)

        dest = session_dir / "screenshot.png"
        shutil.copy2(source_path, str(dest))
        os.chmod(str(dest), 0o600)
        return str(dest)

    def get_screenshot_path(self, session_id: str) -> str:
        """Get the screenshot path for a session."""
        return str(self._safe_session_dir(session_id) / "screenshot.png")

    def read_screenshot_bytes(self, session_id: str) -> bytes:
        """Read the saved screenshot file as raw bytes."""
        path = self._safe_session_dir(session_id) / "screenshot.png"
        return path.read_bytes()

    def delete_temp(self, path: str) -> None:
        """Delete a temporary screenshot file."""
        os.remove(path)
