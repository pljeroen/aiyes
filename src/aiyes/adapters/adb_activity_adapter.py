"""AdbActivityQueryAdapter — implements AdbActivityQueryPort via adb subprocess.

Checks the currently resumed Android activity using adb dumpsys.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional


class AdbActivityQueryAdapter:
    """Query resumed Android activity via adb subprocess."""

    _RESUMED_RE = re.compile(r"mResumedActivity.*?(\S+/\S+)")

    def get_resumed_activity(self, serial: str) -> Optional[str]:
        """Get the currently resumed activity package/class string.

        Returns None if the query fails or no activity is resumed.
        """
        from aiyes.adapters.adb_path import resolve_adb_path

        try:
            adb = resolve_adb_path()
            result = subprocess.run(
                [adb, "-s", serial, "shell", "dumpsys", "activity", "activities"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                match = self._RESUMED_RE.search(line)
                if match:
                    return match.group(1)
            return None
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            OSError,
            subprocess.TimeoutExpired,
            RuntimeError,
        ):
            return None
