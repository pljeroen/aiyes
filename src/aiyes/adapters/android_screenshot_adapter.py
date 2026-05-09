"""AdbScreenshotAdapter — implements ScreenshotPort via adb screencap.

Takes screenshots using `adb exec-out screencap -p`.
Uses only stdlib: subprocess, tempfile, os.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Optional


# AIYES-37 Item 4: Size ceiling to prevent unbounded memory allocation
# from malformed/malicious adb output. 50 MB is well above any reasonable
# device screenshot (4K RGBA = ~33 MB) but catches runaway output.
MAX_SCREENSHOT_BYTES = 50 * 1024 * 1024  # 50 MB


class AdbScreenshotAdapter:
    """Takes screenshots via adb exec-out screencap."""

    def take(self, session, output_path: Optional[str] = None) -> str:
        """Take a screenshot. Returns path to the saved PNG file.

        Runs: adb -s {serial} exec-out screencap -p > {path}
        """
        serial = session.device_serial
        if not serial:
            raise RuntimeError(
                "Android session has no device_serial — cannot take screenshot"
            )

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)

        from aiyes.adapters.adb_path import resolve_adb_path

        cmd = [resolve_adb_path(), "-s", serial, "exec-out", "screencap", "-p"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=15,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "adb not found on PATH. Install Android SDK platform-tools."
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"adb screencap timed out for device {serial}")

        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"adb screencap failed (rc={result.returncode}): {stderr_text}"
            )

        if len(result.stdout) > MAX_SCREENSHOT_BYTES:
            raise RuntimeError(
                f"adb screencap output ({len(result.stdout)} bytes) exceeds "
                f"size ceiling ({MAX_SCREENSHOT_BYTES} bytes)"
            )

        if not result.stdout:
            raise RuntimeError(
                f"adb screencap returned empty output for device {serial}"
            )

        with open(output_path, "wb") as f:
            f.write(result.stdout)

        return output_path
