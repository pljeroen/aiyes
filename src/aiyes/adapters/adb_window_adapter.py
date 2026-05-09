"""AdbWindowAdapter — implements TopLevelWindowPort for Android.

Enumerates visible windows via adb dumpsys window windows.
"""

from __future__ import annotations

import re
import subprocess
from typing import List

from aiyes.domain.top_level_window import TopLevelWindow


class AdbWindowAdapter:
    """List top-level windows on Android via adb dumpsys."""

    # Match window titles from dumpsys output.
    # Format varies by API level; match both mSurface and Window # patterns.
    _WINDOW_RE = re.compile(
        r"(?:Window #\d+|mSurface).*?(?:Surface\(name=|title=)([^)]+)\)"
    )

    def list_top_level_windows(self, session: object) -> List[TopLevelWindow]:
        """List visible top-level windows on the Android device.

        Returns empty list on failure (no exception propagated).
        """
        serial = getattr(session, "device_serial", None)
        if not serial:
            return []

        from aiyes.adapters.adb_path import resolve_adb_path

        try:
            adb = resolve_adb_path()
            result = subprocess.run(
                [adb, "-s", serial, "shell", "dumpsys", "window", "windows"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )

            windows: List[TopLevelWindow] = []
            for line in result.stdout.splitlines():
                match = self._WINDOW_RE.search(line)
                if match:
                    title = match.group(1).strip()
                    if title:
                        windows.append(TopLevelWindow(role="window", name=title))
            return windows
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            OSError,
            subprocess.TimeoutExpired,
            RuntimeError,
        ):
            return []
