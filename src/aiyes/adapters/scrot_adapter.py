"""ScrotAdapter — implements ScreenshotPort via scrot/imagemagick subprocess."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional


class ScrotAdapter:
    """Takes screenshots using scrot (primary) or imagemagick import (fallback)."""

    def take(self, session, output_path: Optional[str] = None) -> str:
        """Take a screenshot. Returns path to the screenshot file.

        Accepts a Session object or a display string for backward compatibility.
        """
        if isinstance(session, str):
            display = session
        else:
            display = session.display

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)

        # Try scrot first
        if shutil.which("scrot") is not None:
            env = dict(os.environ)
            env["DISPLAY"] = display
            subprocess.run(
                ["scrot", "--display", display, output_path],
                check=True,
                env=env,
            )
            return output_path

        # Fallback to imagemagick import
        if shutil.which("import") is not None:
            subprocess.run(
                ["import", "-display", display, "-window", "root", output_path],
                check=True,
            )
            return output_path

        raise RuntimeError(
            "No screenshot tool available. Install scrot or imagemagick."
        )
