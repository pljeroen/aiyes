"""AIYES-45: wait_reactive, key, sleep step kinds.

Adds wait family completion (wait_reactive), the Android navigation
primitive (key), and the friction-laden time-delay escape hatch (sleep).
Sleep is validated at load time: seconds in [0.0, 5.0] inclusive, reason
length >= 20 characters.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.domain.scenario import ScenarioStep, validate_scenario_document


@dataclasses.dataclass
class RecordingUseCase:
    result: Any
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.result


@dataclasses.dataclass
class RecordingSleeper:
    """Captures sleep durations instead of actually blocking."""

    slept: list[float] = dataclasses.field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        self.slept.append(float(seconds))


def _executor_with_session(
    *,
    reactive_wait: Any = None,
    key: Any = None,
    sleeper: Any = None,
) -> ScenarioUseCaseExecutor:
    start = RecordingUseCase(SimpleNamespace(session_id="s1", backend="linux"))
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=RecordingUseCase(None),
        find=RecordingUseCase([]),
        action=RecordingUseCase(None),
        type_text=RecordingUseCase(None),
        screenshot=RecordingUseCase(None),
        session_stop=RecordingUseCase(None),
        reactive_wait=reactive_wait,
        key=key,
        sleeper=sleeper,
    )
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "gedit", "wait_seconds": 0.0},
        )
    )
    return executor


# ─── AC-45-01: wait_reactive dispatch + parameter forwarding ──────────


def test_wait_reactive_dispatches_with_all_parameters() -> None:
    reactive = RecordingUseCase(
        SimpleNamespace(
            status="satisfied",
            backend="linux",
            condition="screen-change",
            elapsed_ms=120,
            polls=2,
        )
    )
    executor = _executor_with_session(reactive_wait=reactive)

    result = executor.execute(
        ScenarioStep(
            id="wait_screen",
            kind="wait_reactive",
            parameters={
                "condition": "screen-change",
                "name_pattern": "Save",
                "timeout": 4.0,
                "quiet": 0.5,
                "poll_interval": 0.1,
            },
        )
    )

    assert result.status == "passed"
    assert reactive.calls == [
        {
            "session_id": "s1",
            "condition": "screen-change",
            "name_pattern": "Save",
            "timeout": 4.0,
            "quiet": 0.5,
            "poll_interval": 0.1,
        }
    ]


def test_wait_reactive_uses_defaults_when_optional_parameters_omitted() -> None:
    reactive = RecordingUseCase(
        SimpleNamespace(
            status="satisfied",
            backend="linux",
            condition="focus-change",
            elapsed_ms=5,
            polls=1,
        )
    )
    executor = _executor_with_session(reactive_wait=reactive)

    executor.execute(
        ScenarioStep(
            id="rw",
            kind="wait_reactive",
            parameters={"condition": "focus-change"},
        )
    )

    call = reactive.calls[0]
    assert call["session_id"] == "s1"
    assert call["condition"] == "focus-change"
    assert call["name_pattern"] is None
    assert call["timeout"] == 10.0
    assert call["quiet"] == 0.0
    assert call["poll_interval"] == 0.25


# ─── AC-45-02: key dispatch + parameter forwarding ────────────────────


def test_key_dispatches_with_keys_list() -> None:
    key = RecordingUseCase(SimpleNamespace(status="ok"))
    executor = _executor_with_session(key=key)

    result = executor.execute(
        ScenarioStep(
            id="press_back",
            kind="key",
            parameters={"keys": ["KEYCODE_BACK"]},
        )
    )

    assert result.status == "passed"
    assert key.calls == [{"session_id": "s1", "key_specs": ["KEYCODE_BACK"]}]


def test_key_accepts_multiple_keycodes_in_order() -> None:
    key = RecordingUseCase(SimpleNamespace(status="ok"))
    executor = _executor_with_session(key=key)

    executor.execute(
        ScenarioStep(
            id="chord",
            kind="key",
            parameters={"keys": ["ctrl", "s"]},
        )
    )

    assert key.calls[0]["key_specs"] == ["ctrl", "s"]


def test_key_with_empty_keys_list_fails_loudly() -> None:
    key = RecordingUseCase(SimpleNamespace(status="ok"))
    executor = _executor_with_session(key=key)

    result = executor.execute(
        ScenarioStep(id="no_keys", kind="key", parameters={"keys": []})
    )

    # Empty key list at executor level should fail (key_uc requires non-empty).
    # AIYES-48 will move this to load-time validation; for now executor catches.
    assert result.status == "failed"
    assert key.calls == []


# ─── AC-45-03: sleep dispatches with seconds and records reason ───────


def test_sleep_dispatches_to_sleeper_with_seconds() -> None:
    sleeper = RecordingSleeper()
    executor = _executor_with_session(sleeper=sleeper)

    result = executor.execute(
        ScenarioStep(
            id="brief_settle",
            kind="sleep",
            parameters={
                "seconds": 0.25,
                "reason": "Wait for slide-in animation to finish",
            },
        )
    )

    assert result.status == "passed"
    assert sleeper.slept == [0.25]
    assert result.output["slept"] == 0.25
    assert result.output["reason"] == "Wait for slide-in animation to finish"


def test_sleep_with_zero_seconds_still_dispatches() -> None:
    sleeper = RecordingSleeper()
    executor = _executor_with_session(sleeper=sleeper)

    result = executor.execute(
        ScenarioStep(
            id="no_actual_wait",
            kind="sleep",
            parameters={
                "seconds": 0.0,
                "reason": "Zero-duration marker for review trail visibility",
            },
        )
    )

    assert result.status == "passed"
    assert sleeper.slept == [0.0]


# ─── AC-45-04, AC-45-05: sleep load-time validation ───────────────────


def _scenario_with_sleep(seconds: Any, reason: Any) -> dict:
    return {
        "schema_version": 1,
        "id": "sleep-validation",
        "title": "Sleep validation",
        "target": "linux",
        "steps": [
            {
                "id": "nap",
                "kind": "sleep",
                "seconds": seconds,
                "reason": reason,
            }
        ],
        "evidence_policy": {"bundle": False, "redact_environment": True},
    }


def test_sleep_with_seconds_above_ceiling_rejected_at_load() -> None:
    result = validate_scenario_document(_scenario_with_sleep(5.01, "x" * 25))
    assert not result.ok
    codes = [issue.code for issue in result.issues]
    assert "sleep_ceiling_exceeded" in codes


def test_sleep_with_exactly_five_seconds_is_accepted() -> None:
    result = validate_scenario_document(_scenario_with_sleep(5.0, "x" * 25))
    assert result.ok, f"Issues: {result.issues}"


def test_sleep_with_negative_seconds_rejected_at_load() -> None:
    result = validate_scenario_document(_scenario_with_sleep(-0.1, "x" * 25))
    assert not result.ok
    codes = [issue.code for issue in result.issues]
    assert "sleep_ceiling_exceeded" in codes or "invalid_sleep_seconds" in codes


def test_sleep_with_missing_reason_rejected_at_load() -> None:
    result = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "sleep-no-reason",
            "title": "Sleep no reason",
            "target": "linux",
            "steps": [{"id": "nap", "kind": "sleep", "seconds": 0.5}],
            "evidence_policy": {"bundle": False, "redact_environment": True},
        }
    )
    assert not result.ok
    codes = [issue.code for issue in result.issues]
    assert "sleep_reason_too_short" in codes


def test_sleep_with_short_reason_rejected_at_load() -> None:
    result = validate_scenario_document(_scenario_with_sleep(0.5, "too short"))
    assert not result.ok
    codes = [issue.code for issue in result.issues]
    assert "sleep_reason_too_short" in codes


def test_sleep_with_exactly_twenty_char_reason_is_accepted() -> None:
    result = validate_scenario_document(_scenario_with_sleep(0.5, "x" * 20))
    assert result.ok, f"Issues: {result.issues}"


def test_sleep_with_non_numeric_seconds_rejected_at_load() -> None:
    result = validate_scenario_document(_scenario_with_sleep("a lot", "x" * 25))
    assert not result.ok


# ─── AC-45-06: public fixture validates ───────────────────────────────


def test_aiyes45_public_fixture_validates() -> None:
    fixture_path = Path("examples/scenarios/linux-gedit-reactive-key.json")
    assert fixture_path.is_file(), "AIYES-45 public fixture must exist"

    document = json.loads(fixture_path.read_text())
    result = validate_scenario_document(document, public_fixture=True)
    assert result.ok, f"AIYES-45 fixture invalid: {result.issues}"
    scenario = result.scenario
    assert scenario is not None
    kinds = {step.kind for step in scenario.steps}
    assert "wait_reactive" in kinds
    assert "key" in kinds


# ─── Step kind catalogue updates ──────────────────────────────────────


def test_new_step_kinds_are_declared() -> None:
    """wait_reactive, key, sleep are valid scenario step kinds."""
    base = {
        "schema_version": 1,
        "id": "kind-presence",
        "title": "Kind presence",
        "target": "linux",
        "evidence_policy": {"bundle": False, "redact_environment": True},
    }
    for kind, extra in (
        ("wait_reactive", {"condition": "screen-change"}),
        ("key", {"keys": ["Escape"]}),
        ("sleep", {"seconds": 0.1, "reason": "Document why we sleep here..."}),
    ):
        document = {**base, "steps": [{"id": "s", "kind": kind, **extra}]}
        result = validate_scenario_document(document)
        assert result.ok, f"{kind} should validate: {result.issues}"
