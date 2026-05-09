"""XclipAdapter — implements ClipboardPort via xclip subprocess.

Uses xclip with DISPLAY from session for Linux clipboard access.
"""

from __future__ import annotations

import os
import subprocess


class XclipAdapter:
    """Clipboard read/write via xclip subprocess."""

    def read(self, session) -> str:
        """Read clipboard text using xclip."""
        display = session.display
        env = dict(os.environ)
        env["DISPLAY"] = display

        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                env=env,
                timeout=5,
            )
        except FileNotFoundError:
            raise RuntimeError("xclip not found on PATH. Install xclip.")
        except subprocess.TimeoutExpired:
            raise RuntimeError("xclip read timed out")

        if result.returncode != 0:
            raise RuntimeError(
                f"xclip read failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        return result.stdout

    def write(self, session, text: str) -> None:
        """Write text to clipboard using xclip."""
        display = session.display
        env = dict(os.environ)
        env["DISPLAY"] = display

        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                capture_output=True,
                text=True,
                env=env,
                timeout=5,
            )
        except FileNotFoundError:
            raise RuntimeError("xclip not found on PATH. Install xclip.")
        except subprocess.TimeoutExpired:
            raise RuntimeError("xclip write timed out")

        if result.returncode != 0:
            raise RuntimeError(
                f"xclip write failed (rc={result.returncode}): {result.stderr.strip()}"
            )
