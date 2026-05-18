"""AIYES-95: tests for android_device_metrics_adapter.query_device_metrics.

Thin stdlib-only module-level function that runs
  adb -s <serial> shell wm size
and parses Physical/Override size lines.

Mirrors the android_screenshot_adapter pattern (resolve_adb_path,
subprocess.run with capture_output, no raise on failure).

All tests import the new adapter lazily inside the function so the
import-time error in pre-implementation RED becomes a test failure,
not a collection error.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> Any:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# ─── B-1: Parses Physical size ────────────────────────────────────────


def test_query_device_metrics_parses_physical_size() -> None:
    """rc=0 + 'Physical size: 1080x1920' → (1080, 1920)."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(stdout="Physical size: 1080x1920\n")
        result = query_device_metrics("emulator-5554")

    assert result == ((1080, 1920), None)


# ─── B-2: Override size wins ──────────────────────────────────────────


def test_query_device_metrics_prefers_override_size() -> None:
    """Both lines present → Override wins."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="Physical size: 1080x2400\nOverride size: 720x1280\n"
        )
        result = query_device_metrics("emulator-5554")

    assert result == ((720, 1280), None)


def test_query_device_metrics_prefers_override_regardless_of_line_order() -> None:
    """Override precedence is by label, not line order."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="Override size: 720x1280\nPhysical size: 1080x2400\n"
        )
        result = query_device_metrics("emulator-5554")

    assert result == ((720, 1280), None)


# ─── B-3: Failure modes → None ────────────────────────────────────────


def test_query_device_metrics_returns_none_on_nonzero_rc() -> None:
    """rc=1 → None (no raise)."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="", stderr="device offline", returncode=1
        )
        result = query_device_metrics("broken-serial")

    assert result == (None, "non_zero_rc")


def test_query_device_metrics_returns_none_on_empty_stdout() -> None:
    """rc=0 + empty stdout → None."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(stdout="", returncode=0)
        result = query_device_metrics("emulator-5554")

    assert result == (None, "empty_stdout")


def test_query_device_metrics_returns_none_on_unparseable_stdout() -> None:
    """rc=0 + no size: line → None."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="garbage\nno size here\n", returncode=0
        )
        result = query_device_metrics("emulator-5554")

    assert result == (None, "unparseable_output")


def test_query_device_metrics_returns_none_on_subprocess_exception() -> None:
    """subprocess.run raises → None (no propagation)."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["adb"], timeout=10)
        result = query_device_metrics("emulator-5554")

    assert result == (None, "timeout")


def test_query_device_metrics_returns_none_on_filenotfound() -> None:
    """adb not on PATH (FileNotFoundError) → ('subprocess_error', no raise)."""
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.side_effect = FileNotFoundError("adb not found")
        result = query_device_metrics("emulator-5554")

    assert result == (None, "subprocess_error")


# ─── B-4: argv shape ──────────────────────────────────────────────────


def test_query_device_metrics_invokes_adb_with_correct_argv() -> None:
    """argv == [<adb>, '-s', <serial>, 'shell', 'wm', 'size']."""
    from aiyes.adapters.adb_path import resolve_adb_path
    from aiyes.adapters.android_device_metrics_adapter import query_device_metrics

    expected_adb = resolve_adb_path()
    serial = "emulator-5554"

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(stdout="Physical size: 1080x1920\n")
        query_device_metrics(serial)

    assert mock_run.call_count == 1
    # argv is the first positional arg to subprocess.run.
    args, kwargs = mock_run.call_args
    argv = args[0] if args else kwargs.get("args")
    assert argv == [expected_adb, "-s", serial, "shell", "wm", "size"], (
        f"Unexpected argv: {argv!r}"
    )
