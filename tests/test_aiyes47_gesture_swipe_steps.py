"""AIYES-47: gesture_pinch, gesture_two_finger_scroll, swipe step kinds.

Wires the gesture family as scenario step kinds (existing GestureUseCase)
and adds the single-finger swipe primitive (use case + adapter + CLI
command). All four positional kinds support literal and source-anchored
coord modes.

The swipe primitive is the natural Android list-scroll primitive.
gesture_two_finger_scroll is also a single-finger adb emulation after
AIYES-94 — the "two-finger" label is preserved for caller compatibility
but no multitouch event is emitted. scroll_into_view (AIYES-49)
dispatches to swipe on Android and mouse_scroll on Linux.
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
class RecordingGestureUseCase:
    """Capture pinch/two_finger_scroll/swipe calls."""

    pinch_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    scroll_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    swipe_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def pinch(self, session_id: str, x: int, y: int, scale_factor: float) -> Any:
        self.pinch_calls.append(
            {"session_id": session_id, "x": x, "y": y, "scale_factor": scale_factor}
        )
        return SimpleNamespace(status="ok")

    def two_finger_scroll(
        self,
        session_id: str,
        x: int,
        y: int,
        direction: str,
        amount: int = 3,
    ) -> Any:
        self.scroll_calls.append(
            {
                "session_id": session_id,
                "x": x,
                "y": y,
                "direction": direction,
                "amount": amount,
            }
        )
        return SimpleNamespace(status="ok")

    def swipe(
        self,
        session_id: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> Any:
        self.swipe_calls.append(
            {
                "session_id": session_id,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "duration_ms": duration_ms,
            }
        )
        return SimpleNamespace(status="ok")


def _executor_with_session(*, gesture: Any = None) -> ScenarioUseCaseExecutor:
    start = SimpleNamespace(
        execute=lambda **kwargs: SimpleNamespace(session_id="s1", backend="android")
    )
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=SimpleNamespace(execute=lambda **kw: []),
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        gesture=gesture,
    )
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "adb", "wait_seconds": 0.0, "backend": "android"},
        )
    )
    return executor


# ─── gesture_pinch ────────────────────────────────────────────────────


def test_gesture_pinch_literal_dispatches() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)

    result = executor.execute(
        ScenarioStep(
            id="pinch1",
            kind="gesture_pinch",
            parameters={"x": 540, "y": 960, "scale_factor": 0.5},
        )
    )

    assert result.status == "passed"
    assert gesture.pinch_calls == [
        {"session_id": "s1", "x": 540, "y": 960, "scale_factor": 0.5}
    ]


def test_gesture_pinch_source_anchored_uses_node_center() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)
    executor._outputs["target"] = {"nodes": [{"id": "n", "bounds": [100, 200, 80, 40]}]}

    executor.execute(
        ScenarioStep(
            id="pinch_anchor",
            kind="gesture_pinch",
            parameters={"source": "target", "scale_factor": 2.0},
        )
    )

    # center = (140, 220)
    assert gesture.pinch_calls == [
        {"session_id": "s1", "x": 140, "y": 220, "scale_factor": 2.0}
    ]


def test_gesture_pinch_requires_scale_factor() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)

    result = executor.execute(
        ScenarioStep(id="no_scale", kind="gesture_pinch", parameters={"x": 0, "y": 0})
    )

    assert result.status == "failed"


# ─── gesture_two_finger_scroll ────────────────────────────────────────


def test_gesture_two_finger_scroll_literal_dispatches() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)

    result = executor.execute(
        ScenarioStep(
            id="tfs",
            kind="gesture_two_finger_scroll",
            parameters={"x": 540, "y": 1200, "direction": "down", "amount": 4},
        )
    )

    assert result.status == "passed"
    assert gesture.scroll_calls == [
        {
            "session_id": "s1",
            "x": 540,
            "y": 1200,
            "direction": "down",
            "amount": 4,
        }
    ]


def test_gesture_two_finger_scroll_source_anchored() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)
    executor._outputs["list_root"] = {
        "nodes": [{"id": "n", "bounds": [0, 100, 1080, 1800]}]
    }

    executor.execute(
        ScenarioStep(
            id="tfs_anchor",
            kind="gesture_two_finger_scroll",
            parameters={"source": "list_root", "direction": "down", "amount": 2},
        )
    )

    # center = (540, 1000)
    assert gesture.scroll_calls == [
        {
            "session_id": "s1",
            "x": 540,
            "y": 1000,
            "direction": "down",
            "amount": 2,
        }
    ]


def test_gesture_two_finger_scroll_invalid_direction_fails() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)

    result = executor.execute(
        ScenarioStep(
            id="bad",
            kind="gesture_two_finger_scroll",
            parameters={"x": 0, "y": 0, "direction": "diagonal"},
        )
    )

    assert result.status == "failed"
    assert "direction" in result.error.lower()


# ─── swipe ────────────────────────────────────────────────────────────


def test_swipe_literal_dispatches_with_default_duration() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)

    result = executor.execute(
        ScenarioStep(
            id="swipe1",
            kind="swipe",
            parameters={"x1": 540, "y1": 1500, "x2": 540, "y2": 500},
        )
    )

    assert result.status == "passed"
    assert gesture.swipe_calls == [
        {
            "session_id": "s1",
            "x1": 540,
            "y1": 1500,
            "x2": 540,
            "y2": 500,
            "duration_ms": 300,
        }
    ]


def test_swipe_literal_with_duration_override() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)

    executor.execute(
        ScenarioStep(
            id="slow_swipe",
            kind="swipe",
            parameters={
                "x1": 0,
                "y1": 0,
                "x2": 100,
                "y2": 100,
                "duration_ms": 800,
            },
        )
    )

    assert gesture.swipe_calls[0]["duration_ms"] == 800


def test_swipe_source_anchored_uses_center_plus_offset() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)
    executor._outputs["scroll_root"] = {
        "nodes": [{"id": "root", "bounds": [0, 0, 1080, 2000]}]
    }

    executor.execute(
        ScenarioStep(
            id="swipe_up_from_root",
            kind="swipe",
            parameters={"source": "scroll_root", "dx": 0, "dy": -800},
        )
    )

    # center = (540, 1000) → endpoint (540, 200)
    assert gesture.swipe_calls == [
        {
            "session_id": "s1",
            "x1": 540,
            "y1": 1000,
            "x2": 540,
            "y2": 200,
            "duration_ms": 300,
        }
    ]


def test_swipe_ambiguous_coord_mode_fails() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)
    executor._outputs["src"] = {"nodes": [{"id": "n", "bounds": [0, 0, 10, 10]}]}

    result = executor.execute(
        ScenarioStep(
            id="bad",
            kind="swipe",
            parameters={
                "x1": 0,
                "y1": 0,
                "x2": 10,
                "y2": 10,
                "source": "src",
                "dx": 0,
                "dy": 0,
            },
        )
    )

    assert result.status == "failed"
    assert "ambiguous" in result.error.lower() or "coord_mode" in result.error.lower()


def test_swipe_missing_coord_mode_fails() -> None:
    gesture = RecordingGestureUseCase()
    executor = _executor_with_session(gesture=gesture)

    result = executor.execute(ScenarioStep(id="bad", kind="swipe", parameters={}))

    assert result.status == "failed"


# ─── New CLI command ──────────────────────────────────────────────────


def test_aieyes_swipe_cli_command_exists() -> None:
    """The new aieyes swipe top-level command is registered in the CLI."""
    from click.testing import CliRunner

    from aiyes.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["swipe", "--help"])
    assert result.exit_code == 0, result.output
    assert "swipe" in result.output.lower()


# ─── Step kind validation ─────────────────────────────────────────────


def test_new_gesture_and_swipe_kinds_validate() -> None:
    for kind, extra in (
        ("gesture_pinch", {"x": 0, "y": 0, "scale_factor": 1.5}),
        (
            "gesture_two_finger_scroll",
            {"x": 0, "y": 0, "direction": "down", "amount": 2},
        ),
        ("swipe", {"x1": 0, "y1": 0, "x2": 100, "y2": 100}),
    ):
        document = {
            "schema_version": 1,
            "id": "k",
            "title": "k",
            "target": "android",
            "steps": [{"id": "s", "kind": kind, **extra}],
            "evidence_policy": {"bundle": False, "redact_environment": True},
        }
        result = validate_scenario_document(document)
        assert result.ok, f"{kind} should validate: {result.issues}"
