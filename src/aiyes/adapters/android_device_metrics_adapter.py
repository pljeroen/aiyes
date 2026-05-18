"""AIYES-95: Android device metrics adapter — queries `adb shell wm size`.

Thin stdlib-only module-level function that runs

    adb -s <serial> shell wm size

and parses Physical/Override size lines from the output. Mirrors the
inline-resolve-adb pattern used by android_screenshot_adapter.py.

Override size takes precedence over Physical size when both are present
(Android `wm` shell semantics: Override is the active size when set).

Returns None on subprocess failure, empty stdout, or unparseable output —
callers are expected to fall back to a default and emit a warning.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional


_PHYSICAL_RE = re.compile(r"Physical size:\s*(\d+)x(\d+)", re.IGNORECASE)
_OVERRIDE_RE = re.compile(r"Override size:\s*(\d+)x(\d+)", re.IGNORECASE)


DeviceMetricsResult = tuple[Optional[tuple[int, int]], Optional[str]]
"""(metrics, reason): on success metrics is (width, height) and reason is None;
on failure metrics is None and reason is a short kebab-case categorical tag
naming which failure class occurred (for caller-side diagnostic messages)."""


_ADB_TIMEOUT_S = 5.0


def query_device_metrics(serial: str) -> DeviceMetricsResult:
    """Return (metrics, reason) by running ``adb -s <serial> shell wm size``.

    On success: ``((width, height), None)``. On failure: ``(None, reason)``
    where reason is one of the categorical tags below. Prefers
    "Override size: WxH" over "Physical size: WxH" when both are present
    (per Android ``wm`` shell semantics). Subprocess call carries a 5-second
    timeout — a hung adb yields ``(None, "timeout")``, never a hang.

    Failure reasons:
      - "no_adb_binary"      — resolve_adb_path() raised.
      - "subprocess_error"   — OSError / FileNotFoundError / SubprocessError.
      - "timeout"            — adb exceeded the configured timeout.
      - "non_zero_rc"        — adb returned non-zero exit.
      - "empty_stdout"       — adb returned rc=0 but no usable stdout.
      - "unparseable_output" — stdout had no Physical / Override size line.
    """
    from aiyes.adapters.adb_path import resolve_adb_path

    try:
        adb = resolve_adb_path()
    except Exception:
        return (None, "no_adb_binary")

    argv = [adb, "-s", serial, "shell", "wm", "size"]

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_ADB_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return (None, "timeout")
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return (None, "subprocess_error")

    if result.returncode != 0:
        return (None, "non_zero_rc")

    stdout = result.stdout or ""
    if not stdout.strip():
        return (None, "empty_stdout")

    override_match = _OVERRIDE_RE.search(stdout)
    if override_match is not None:
        try:
            return ((int(override_match.group(1)), int(override_match.group(2))), None)
        except (TypeError, ValueError):
            return (None, "unparseable_output")

    physical_match = _PHYSICAL_RE.search(stdout)
    if physical_match is not None:
        try:
            return ((int(physical_match.group(1)), int(physical_match.group(2))), None)
        except (TypeError, ValueError):
            return (None, "unparseable_output")

    return (None, "unparseable_output")
