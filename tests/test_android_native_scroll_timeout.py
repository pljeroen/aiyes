"""Android native scroll: output a helper wrote before it timed out is reported as text.

When the native scroll helper runs past its time limit, the adapter reports the
output the helper had written so far. These tests pin that the summaries are text
equal to what a normally completing helper would have produced for the same bytes,
that undecodable or cut bytes never raise, and that a scenario whose native scroll
timed out still writes its JSON result.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from aiyes.adapters.android_native_scroll_adapter import AndroidNativeScrollAdapter
from aiyes.cli.presenter import format_scenario_run
from aiyes.domain.scenario import ScenarioStep
from aiyes.domain.types import NativeScrollResult
from aiyes.domain.use_cases.scenario_run import ScenarioRunResult, ScenarioRunStepResult

from tests.test_aiyes49_scroll_into_view import (
    FakeFindUseCase,
    FakeSessionRepo,
    RecordingGesture,
    RecordingInspect,
    StepClock,
    _executor,
    _node,
    _tree,
    _tree_node,
)

_ADAPTER_RUN = "aiyes.adapters.android_native_scroll_adapter.subprocess.run"
_DEVICE_METRICS = "aiyes.adapters.android_device_metrics_adapter.query_device_metrics"
_SESSION = SimpleNamespace(device_serial="emulator-5554")
_FAKE_HELPER = ("native-scroll-helper",)


def _run_that_times_out(
    output: Any, stderr: Any, calls: list[list[str]]
) -> Callable[..., Any]:
    """A subprocess.run stand-in that raises the timeout the real one raises."""

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        calls.append(list(cmd))
        raise subprocess.TimeoutExpired(cmd, 15, output=output, stderr=stderr)

    return fake_run


def _scroll_down(helper_command: tuple[str, ...]) -> NativeScrollResult:
    return AndroidNativeScrollAdapter(helper_command=helper_command).scroll(
        _SESSION, node_id="node-42", direction="down"
    )


def _writer_script(data: bytes) -> str:
    """Python source of a helper that writes `data` to stdout and stderr and exits 0."""
    return (
        "import sys\n"
        f"data = {data!r}\n"
        "sys.stdout.buffer.write(data)\n"
        "sys.stderr.buffer.write(data)\n"
    )


def _hanging_script(pid_file: Path) -> str:
    """Python source of a helper that records its pid, writes one line to each
    stream and then hangs far longer than any test time limit."""
    return (
        "import os, pathlib, sys, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "sys.stdout.write('scrolled half way\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('waiting for the device\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )


def _stop_if_running(pid_file: Path) -> bool:
    """Return whether the process named in `pid_file` was still running; kill it if so."""
    if not pid_file.exists():
        return False
    text = pid_file.read_text().strip()
    if not text:
        return False
    pid = int(text)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    os.kill(pid, signal.SIGKILL)
    return True


def test_timeout_reports_partial_output_as_text_and_keeps_every_other_field(
    monkeypatch: Any,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(_ADAPTER_RUN, _run_that_times_out(b"hi\n", b"err\n", calls))

    result = AndroidNativeScrollAdapter(helper_command=_FAKE_HELPER).scroll(
        _SESSION,
        node_id="node-42",
        direction="down",
        stable_id="android:rid=list",
        bounds=(0, 300, 1080, 1500),
    )

    assert len(calls) == 1
    assert calls[0][0] == "native-scroll-helper"
    assert result == NativeScrollResult(
        success=False,
        method="android_accessibility_helper",
        requested_action="ACTION_SCROLL_FORWARD",
        action_id=4096,
        node_id="node-42",
        direction="down",
        stable_id="android:rid=list",
        bounds=(0, 300, 1080, 1500),
        returncode=None,
        stdout_summary="hi",
        stderr_summary="err",
        fallback_reason="native_scroll_helper_timeout",
    )
    assert type(result.stdout_summary) is str
    assert type(result.stderr_summary) is str
    serialized = json.loads(json.dumps(dataclasses.asdict(result)))
    assert serialized["stdout_summary"] == "hi"
    assert serialized["stderr_summary"] == "err"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"  first\r\nsecond\r\n  ", id="crlf-and-surrounding-blanks"),
        pytest.param(b"one\rtwo\r\nthree", id="lone-carriage-return"),
        pytest.param(
            b"caf\xc3\xa9 \xe2\x82\xac \xe2\x80\x94 end", id="multibyte-characters"
        ),
        pytest.param(
            ("é" * 400 + "x" * 200).encode("utf-8"),
            id="longer-than-the-summary-limit",
        ),
    ],
)
def test_timeout_text_equals_the_text_of_a_normal_completion_with_the_same_bytes(
    monkeypatch: Any, data: bytes
) -> None:
    helper = (sys.executable, "-I", "-c", _writer_script(data))
    try:
        completed = _scroll_down(helper)
    except UnicodeDecodeError:
        pytest.skip(
            "a normally completing helper cannot decode these bytes in this locale"
        )
    assert completed.success is True
    assert completed.stdout_summary
    assert completed.stderr_summary

    calls: list[list[str]] = []
    monkeypatch.setattr(_ADAPTER_RUN, _run_that_times_out(data, data, calls))
    timed_out = _scroll_down(helper)

    assert len(calls) == 1
    assert timed_out.fallback_reason == "native_scroll_helper_timeout"
    assert (timed_out.stdout_summary, timed_out.stderr_summary) == (
        completed.stdout_summary,
        completed.stderr_summary,
    )


def test_timeout_with_undecodable_or_cut_bytes_reports_the_readable_text(
    monkeypatch: Any,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        _ADAPTER_RUN,
        _run_that_times_out(b"ok \xff\xfe partial \xe2\x82", b"\xe2\x82", calls),
    )

    result = _scroll_down(_FAKE_HELPER)

    assert len(calls) == 1
    assert result.fallback_reason == "native_scroll_helper_timeout"
    assert type(result.stdout_summary) is str
    assert type(result.stderr_summary) is str
    assert result.stdout_summary.startswith("ok")
    assert " partial " in result.stdout_summary


@pytest.mark.parametrize(
    ("output", "stderr", "expected"),
    [
        pytest.param("hi\n", "err\n", ("hi", "err"), id="output-already-text"),
        pytest.param(None, None, ("", ""), id="no-output"),
    ],
)
def test_timeout_with_text_or_no_output_is_summarised_as_on_completion(
    monkeypatch: Any, output: Any, stderr: Any, expected: tuple[str, str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(_ADAPTER_RUN, _run_that_times_out(output, stderr, calls))

    result = _scroll_down(_FAKE_HELPER)

    assert len(calls) == 1
    assert result.fallback_reason == "native_scroll_helper_timeout"
    assert (result.stdout_summary, result.stderr_summary) == expected


def test_real_helper_that_hangs_after_writing_reports_what_it_wrote(
    monkeypatch: Any, tmp_path: Path
) -> None:
    pid_file = tmp_path / "helper.pid"
    real_run = subprocess.run
    requested_timeouts: list[Any] = []

    def run_with_a_short_time_limit(cmd: list[str], **kwargs: Any) -> Any:
        requested_timeouts.append(kwargs.get("timeout"))
        kwargs["timeout"] = 2
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(_ADAPTER_RUN, run_with_a_short_time_limit)
    helper = (sys.executable, "-I", "-c", _hanging_script(pid_file))

    try:
        result = _scroll_down(helper)
    finally:
        helper_still_running = _stop_if_running(pid_file)

    assert len(requested_timeouts) == 1
    assert requested_timeouts[0] is not None and requested_timeouts[0] > 2
    assert helper_still_running is False
    assert result.success is False
    assert result.returncode is None
    assert result.fallback_reason == "native_scroll_helper_timeout"
    assert (result.stdout_summary, result.stderr_summary) == (
        "scrolled half way",
        "waiting for the device",
    )


def test_scenario_scroll_whose_native_helper_timed_out_writes_its_json_result(
    monkeypatch: Any,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        _ADAPTER_RUN,
        _run_that_times_out(b"partial out\n", b"partial err\n", calls),
    )
    monkeypatch.setattr(_DEVICE_METRICS, lambda serial: ((1080, 2400), None))
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    gesture = RecordingGesture()
    executor = _executor(
        find=FakeFindUseCase(results=[[], [_node("target")]]),
        gesture=gesture,
        native_scroll=AndroidNativeScrollAdapter(helper_command=_FAKE_HELPER),
        session_repo=FakeSessionRepo(
            resolution="1080x2400", device_serial="emulator-5554"
        ),
        clock=StepClock(step=0.01),
        inspect=RecordingInspect(trees=[_tree(list_node), _tree(list_node)]),
    )

    step = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "direction": "down",
                "max_scrolls": 3,
            },
        )
    )

    assert len(calls) == 1
    assert calls[0][0] == "native-scroll-helper"
    assert step.status == "passed"
    assert len(gesture.swipe_calls) == 1
    attempt = step.output["scroll_attempts"][0]
    assert attempt["method"] == "scrollable_region_swipe"
    assert attempt["fallback_reason"] == "native_scroll_helper_timeout"
    native = attempt["native_scroll"]
    assert (native["stdout_summary"], native["stderr_summary"]) == (
        "partial out",
        "partial err",
    )
    assert type(native["stdout_summary"]) is str
    assert type(native["stderr_summary"]) is str
    step_json = json.loads(json.dumps(step.output))
    assert step_json["scroll_attempts"][0]["native_scroll"]["stdout_summary"] == (
        "partial out"
    )

    run = ScenarioRunResult(
        scenario_id="native-scroll-timeout",
        status="passed",
        steps=(
            ScenarioRunStepResult(
                step_id="reach",
                kind="scroll_into_view",
                status=step.status,
                output=dict(step.output),
            ),
        ),
        mode="live",
    )
    for profile in ("compact", "deep"):
        payload = json.loads(format_scenario_run(run, profile=profile))
        recorded = payload["steps"][0]["output"]["scroll_attempts"][0]["native_scroll"]
        assert (recorded["stdout_summary"], recorded["stderr_summary"]) == (
            "partial out",
            "partial err",
        ), profile
