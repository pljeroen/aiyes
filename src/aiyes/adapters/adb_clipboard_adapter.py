"""AdbClipboardAdapter — implements ClipboardPort via adb shell cmd clipboard.

Uses `adb shell cmd clipboard get-text` (read) and
`adb shell cmd clipboard set-text` (write). Requires API 29+.
"""

from __future__ import annotations

import subprocess
from typing import List


def _get_serial(session) -> str:
    """Extract device_serial from session."""
    serial = session.device_serial
    if not serial:
        raise RuntimeError(
            "Android session has no device_serial — cannot access clipboard"
        )
    return serial


def _run_adb(serial: str, args: List[str]) -> subprocess.CompletedProcess:
    """Run an adb command targeting a specific device, returning result."""
    from aiyes.adapters.adb_path import resolve_adb_path

    cmd = [resolve_adb_path(), "-s", serial, "shell"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError("adb not found on PATH. Install Android SDK platform-tools.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"adb clipboard command timed out for device {serial}")

    if result.returncode != 0:
        raise RuntimeError(
            f"adb clipboard failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result


class AdbClipboardAdapter:
    """Clipboard read/write via adb shell cmd clipboard commands."""

    def read(self, session) -> str:
        """Read clipboard text via adb shell cmd clipboard get-text."""
        serial = _get_serial(session)
        result = _run_adb(serial, ["cmd", "clipboard", "get-text"])
        return result.stdout.strip()

    def write(self, session, text: str) -> None:
        """Write text to clipboard via adb shell cmd clipboard set-text."""
        serial = _get_serial(session)
        _run_adb(serial, ["cmd", "clipboard", "set-text", text])
