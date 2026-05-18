"""AIYES-95: Android viewport sourced from device, not scenario resolution.

Tests _parse_viewport's new Android branch which queries `adb shell wm size`
via android_device_metrics_adapter.query_device_metrics when the session's
backend is "android" and device_serial is set.

R1: Android + serial → parses Physical size from adb stdout
R2: Override size takes precedence over Physical size
R3: adb failure (rc!=0, empty, unparseable, exception) → (1080, 1920) + stderr warning
R4: Linux backend → resolution-parse path unchanged, no adb call
R5: per-session cache → one adb call for repeated _parse_viewport calls
R6: signature preserved — (session_repo, session_id) call form still works

All tests import the new adapter lazily inside the function so the import-time
error in pre-implementation RED becomes a test failure, not a collection error.
"""

from __future__ import annotations

import inspect
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch


# ─── Fakes ────────────────────────────────────────────────────────────


class FakeSessionRepo:
    """Returns a session with configurable backend/serial/resolution."""

    def __init__(
        self,
        *,
        backend: str = "android",
        device_serial: str = "emulator-5554",
        resolution: str = "",
    ) -> None:
        self._backend = backend
        self._device_serial = device_serial
        self._resolution = resolution
        self.load_calls: list[str] = []

    def load(self, session_id: str) -> Any:
        self.load_calls.append(session_id)
        return SimpleNamespace(
            session_id=session_id,
            backend=self._backend,
            device_serial=self._device_serial,
            resolution=self._resolution,
        )


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> Any:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# ─── R1: Android Physical size parsing ────────────────────────────────


def test_parse_viewport_android_returns_device_size(capsys: Any) -> None:
    """R1: Physical size line from adb stdout → (width, height) tuple."""
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    repo = FakeSessionRepo(backend="android", device_serial="emulator-5554")
    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(stdout="Physical size: 1080x2400\n")
        result = _parse_viewport(repo, "sess-1")

    assert result == (1080, 2400)
    assert mock_run.call_count == 1


# ─── R2: Override size precedence ─────────────────────────────────────


def test_parse_viewport_android_override_size_takes_precedence() -> None:
    """R2: When both Physical and Override are present, Override wins."""
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    repo = FakeSessionRepo(backend="android", device_serial="EMU-1")
    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="Physical size: 1080x2400\nOverride size: 720x1280\n"
        )
        result = _parse_viewport(repo, "sess-2")

    assert result == (720, 1280)


def test_parse_viewport_android_override_size_precedence_regardless_of_line_order() -> (
    None
):
    """R2: Override precedence is by semantic label, not line order."""
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    repo = FakeSessionRepo(backend="android", device_serial="EMU-1")
    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="Override size: 720x1280\nPhysical size: 1080x2400\n"
        )
        result = _parse_viewport(repo, "sess-2b")

    assert result == (720, 1280)


# ─── R3: Failure → fallback + stderr warning ──────────────────────────


def test_parse_viewport_android_subprocess_failure_falls_back_with_stderr_warning(
    capsys: Any,
) -> None:
    """R3a: rc != 0 → fallback (1080, 1920), single stderr warning naming serial."""
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    serial = "broken-serial-42"
    repo = FakeSessionRepo(backend="android", device_serial=serial)
    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="", stderr="device offline", returncode=1
        )
        result = _parse_viewport(repo, "sess-fail")

    assert result == (1080, 1920)
    captured = capsys.readouterr()
    assert serial in captured.err
    # Some recognisable warning indication.
    err_lower = captured.err.lower()
    assert any(
        token in err_lower for token in ("warning", "wm size", "viewport", "fallback")
    ), f"Expected warning-like phrase in stderr, got: {captured.err!r}"


def test_parse_viewport_android_empty_stdout_falls_back(capsys: Any) -> None:
    """R3b: rc=0 but empty stdout → fallback + stderr warning."""
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    serial = "EMPTY-1"
    repo = FakeSessionRepo(backend="android", device_serial=serial)
    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(stdout="", returncode=0)
        result = _parse_viewport(repo, "sess-empty")

    assert result == (1080, 1920)
    captured = capsys.readouterr()
    assert serial in captured.err
    assert captured.err.strip() != ""


def test_parse_viewport_android_unparseable_stdout_falls_back(capsys: Any) -> None:
    """R3c: rc=0 but no size: line → fallback + stderr warning."""
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    serial = "UNP-1"
    repo = FakeSessionRepo(backend="android", device_serial=serial)
    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(
            stdout="garbage line\nno size here\n", returncode=0
        )
        result = _parse_viewport(repo, "sess-unp")

    assert result == (1080, 1920)
    captured = capsys.readouterr()
    assert serial in captured.err


def test_parse_viewport_android_subprocess_exception_falls_back(capsys: Any) -> None:
    """R3d: subprocess.run raises → fallback + stderr warning, no propagation."""
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    serial = "TIMEOUT-1"
    repo = FakeSessionRepo(backend="android", device_serial=serial)
    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["adb"], timeout=10)
        # Must not raise.
        result = _parse_viewport(repo, "sess-timeout")

    assert result == (1080, 1920)
    captured = capsys.readouterr()
    assert serial in captured.err


# ─── R4: Linux path unchanged ─────────────────────────────────────────


def test_parse_viewport_linux_unchanged_no_adb_call() -> None:
    """R4: backend=linux → resolution parsed; new adb adapter NOT called."""
    from aiyes.adapters import android_device_metrics_adapter as adapter_mod
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    repo = FakeSessionRepo(backend="linux", device_serial="", resolution="1920x1080")
    spy = MagicMock(side_effect=AssertionError("adb adapter must not be called"))
    with patch.object(adapter_mod, "query_device_metrics", spy):
        result = _parse_viewport(repo, "sess-linux")

    assert result == (1920, 1080)
    assert spy.call_count == 0


def test_parse_viewport_linux_invalid_resolution_falls_back() -> None:
    """R4: backend=linux + garbage resolution → fallback (1080,1920), no adb."""
    from aiyes.adapters import android_device_metrics_adapter as adapter_mod
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    repo = FakeSessionRepo(backend="linux", device_serial="", resolution="garbage")
    spy = MagicMock(side_effect=AssertionError("adb adapter must not be called"))
    with patch.object(adapter_mod, "query_device_metrics", spy):
        result = _parse_viewport(repo, "sess-linux-bad")

    assert result == (1080, 1920)
    assert spy.call_count == 0


# ─── R5: Cache — one adb call per session_id ─────────────────────────


def test_parse_viewport_caches_per_session() -> None:
    """R5: Two calls for same session_id → exactly one adb invocation."""
    from aiyes.adapters.scenario_use_case_executor import (
        ScenarioUseCaseExecutor,
        _parse_viewport,
    )

    repo = FakeSessionRepo(backend="android", device_serial="CACHE-1")
    # The cache lives on the ScenarioUseCaseExecutor instance per SD-2.
    # Per TS-04 Option A, _parse_viewport accepts the cache as an optional
    # keyword arg; the executor passes self._viewport_cache.
    executor = ScenarioUseCaseExecutor(
        session_start=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_repo=repo,
    )
    cache = getattr(executor, "_viewport_cache", None)
    assert cache is not None, (
        "ScenarioUseCaseExecutor must initialise self._viewport_cache (SD-2)"
    )

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(stdout="Physical size: 1080x2400\n")
        first = _parse_viewport(repo, "sess-cache", _cache=cache)
        second = _parse_viewport(repo, "sess-cache", _cache=cache)

    assert first == (1080, 2400)
    assert second == (1080, 2400)
    assert mock_run.call_count == 1, (
        f"Expected exactly 1 adb call across 2 _parse_viewport calls for the "
        f"same session_id; got {mock_run.call_count}"
    )


def test_parse_viewport_cache_distinct_session_ids_invokes_adb_twice() -> None:
    """R5: Two distinct session_ids → two adb calls (no cross-session leak)."""
    from aiyes.adapters.scenario_use_case_executor import (
        ScenarioUseCaseExecutor,
        _parse_viewport,
    )

    repo = FakeSessionRepo(backend="android", device_serial="CACHE-2")
    executor = ScenarioUseCaseExecutor(
        session_start=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_repo=repo,
    )
    cache = executor._viewport_cache

    with patch(
        "aiyes.adapters.android_device_metrics_adapter.subprocess.run"
    ) as mock_run:
        mock_run.return_value = _completed(stdout="Physical size: 1080x2400\n")
        _parse_viewport(repo, "sess-A", _cache=cache)
        _parse_viewport(repo, "sess-B", _cache=cache)

    assert mock_run.call_count == 2


# ─── R6: Signature preservation ──────────────────────────────────────


def test_parse_viewport_signature_preserved() -> None:
    """R6: positional (session_repo, session_id) call form still works.

    Per SD-3 / TS-04 Option A, an optional keyword-only `_cache=None`
    parameter is allowed; the existing two-positional call form must
    continue to work and return a valid tuple.
    """
    from aiyes.adapters.scenario_use_case_executor import _parse_viewport

    sig = inspect.signature(_parse_viewport)
    params = list(sig.parameters.values())
    # First two positional names preserved.
    assert params[0].name == "session_repo"
    assert params[1].name == "session_id"
    # Any extra params must have defaults (i.e., be optional).
    for extra in params[2:]:
        assert extra.default is not inspect.Parameter.empty, (
            f"Extra parameter {extra.name!r} must be optional (have a default)"
        )

    # The two-positional call form still works on the Linux path.
    repo = FakeSessionRepo(backend="linux", device_serial="", resolution="1920x1080")
    result = _parse_viewport(repo, "sess-sig")
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result == (1920, 1080)
