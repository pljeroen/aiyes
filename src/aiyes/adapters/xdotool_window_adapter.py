"""XdotoolWindowAdapter — implements WindowQueryPort via xdotool subprocess.

Queries active window and window PID using xdotool with DISPLAY set.
Separate from XdotoolAdapter (InputPort) — single responsibility.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional


class XdotoolWindowAdapter:
    """Window query via xdotool subprocess."""

    def get_active_window_id(self, display: str) -> Optional[str]:
        """Get the active window ID for the given display.

        Returns None if no active window or xdotool fails.
        """
        try:
            env = dict(os.environ)
            env["DISPLAY"] = display
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                check=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            stdout = result.stdout.strip()
            return stdout if stdout else None
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            OSError,
            subprocess.TimeoutExpired,
        ):
            return None

    def get_window_pid(self, display: str, window_id: str) -> Optional[int]:
        """Get the PID that owns the given window ID.

        Returns None if the PID cannot be determined or xdotool fails.
        """
        try:
            env = dict(os.environ)
            env["DISPLAY"] = display
            result = subprocess.run(
                ["xdotool", "getwindowpid", window_id],
                check=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            stdout = result.stdout.strip()
            return int(stdout) if stdout else None
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
        ):
            return None
