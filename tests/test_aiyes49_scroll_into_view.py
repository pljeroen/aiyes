"""AIYES-49: scroll_into_view high-level scenario step.

The socialzzz blocker. Scrolls a list until a target node is visible or
fails with full diagnostics. Two-bound model: max_scrolls AND
max_seconds. Cross-platform dispatch via gesture_uc.swipe.
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
class StepClock:
    """Monotonically advances by `step` seconds per now() call."""

    step: float = 0.1
    t: float = 0.0

    def now(self) -> float:
        self.t += self.step
        return self.t


@dataclasses.dataclass
class FakeFindUseCase:
    """Returns successive find result lists, one per call."""

    results: list[list[Any]]
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.results:
            return self.results.pop(0)
        return []


@dataclasses.dataclass
class RecordingGesture:
    """Records swipe calls."""

    swipe_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

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

    # Other gesture methods unused by scroll_into_view tests.
    def pinch(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("pinch not expected")

    def two_finger_scroll(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("two_finger_scroll not expected")


class FakeSessionRepo:
    """Returns a session with a known resolution."""

    def __init__(self, resolution: str = "1080x1920", backend: str = "android") -> None:
        self._resolution = resolution
        self._backend = backend

    def load(self, session_id: str) -> Any:
        return SimpleNamespace(
            session_id=session_id,
            resolution=self._resolution,
            backend=self._backend,
        )


def _node(node_id: str = "n", bounds: list[int] | None = None) -> dict:
    return {
        "id": node_id,
        "role": "button",
        "name": "Developer",
        "bounds": bounds or [0, 0, 100, 50],
    }


def _executor(
    *,
    find: Any,
    gesture: Any,
    session_repo: Any,
    clock: Any,
) -> ScenarioUseCaseExecutor:
    start = SimpleNamespace(
        execute=lambda **kwargs: SimpleNamespace(session_id="s1", backend="android")
    )
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=find,
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        gesture=gesture,
        session_repo=session_repo,
        clock=clock,
    )
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "adb", "wait_seconds": 0.0, "backend": "android"},
        )
    )
    return executor


# ─── AC-49-02: target found on first attempt ──────────────────────────


def test_scroll_into_view_returns_success_when_already_visible() -> None:
    find = FakeFindUseCase(results=[[_node("dev_node")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(),
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={"role": "button", "name_pattern": "Developer"},
        )
    )

    assert result.status == "passed"
    assert result.output["found"] is True
    assert result.output["node_id"] == "dev_node"
    assert result.output["attempts"] == 0
    assert gesture.swipe_calls == []


# ─── AC-49-03: target appears after K-1 scrolls ───────────────────────


def test_scroll_into_view_succeeds_after_intermediate_scrolls() -> None:
    find = FakeFindUseCase(results=[[], [], [_node("dev_node")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(),
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 5,
            },
        )
    )

    assert result.status == "passed"
    assert result.output["attempts"] == 2
    assert len(gesture.swipe_calls) == 2


# ─── AC-49-04: failure when max_scrolls reached ───────────────────────


def test_scroll_into_view_fails_with_diagnostics_after_max_scrolls() -> None:
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(step=0.01),
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 3,
                "max_seconds": 30.0,
            },
        )
    )

    assert result.status == "failed"
    assert "scroll_into_view_target_not_found" in result.error
    assert result.output["attempts"] == 3
    assert result.output["found"] is False
    assert result.output["bound_hit"] == "scrolls"
    assert len(gesture.swipe_calls) == 3


# ─── AC-49-05: max_seconds bound ─────────────────────────────────────


def test_scroll_into_view_terminates_on_max_seconds_before_max_scrolls() -> None:
    find = FakeFindUseCase(results=[[]] * 50)
    gesture = RecordingGesture()
    # clock advances by 5s per call → timeout hits well before max_scrolls
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(step=5.0),
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 50,
                "max_seconds": 8.0,
            },
        )
    )

    assert result.status == "failed"
    assert result.output["bound_hit"] == "seconds"
    assert result.output["attempts"] < 50


# ─── AC-49-01: cross-platform dispatch via gesture_uc.swipe ───────────


def test_scroll_into_view_uses_gesture_swipe_for_both_platforms() -> None:
    """gesture_uc.swipe routes via _DispatchingGesture which picks adapter."""
    find = FakeFindUseCase(results=[[]] * 5)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1280x800", backend="linux"),
        clock=StepClock(step=0.01),
    )

    executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Settings",
                "max_scrolls": 2,
            },
        )
    )

    # 2 swipes issued at viewport-derived coords for a "down" scroll
    # (default direction). Both should go from lower-region to upper-region.
    assert len(gesture.swipe_calls) == 2
    for call in gesture.swipe_calls:
        # vertical scroll: y1 > y2 ("down" scroll = swipe up)
        assert call["y1"] > call["y2"]
        # x stays at viewport center for vertical scroll
        assert call["x1"] == call["x2"]


# ─── Direction handling ──────────────────────────────────────────────


def test_scroll_into_view_up_scrolls_in_reverse_direction() -> None:
    find = FakeFindUseCase(results=[[], [_node("dev")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(step=0.01),
    )

    executor.execute(
        ScenarioStep(
            id="reach_up",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Top",
                "direction": "up",
                "max_scrolls": 5,
            },
        )
    )

    # "up" scroll = swipe down (content moves down)
    call = gesture.swipe_calls[0]
    assert call["y2"] > call["y1"]


def test_scroll_into_view_horizontal_direction_swipes_horizontally() -> None:
    find = FakeFindUseCase(results=[[], [_node("dev")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(step=0.01),
    )

    executor.execute(
        ScenarioStep(
            id="reach_right",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Right",
                "direction": "right",
                "max_scrolls": 5,
            },
        )
    )

    call = gesture.swipe_calls[0]
    # right scroll = swipe left
    assert call["x1"] > call["x2"]
    # horizontal scroll keeps y constant
    assert call["y1"] == call["y2"]


# ─── Find parameter forwarding ───────────────────────────────────────


def test_scroll_into_view_forwards_role_name_state_to_find() -> None:
    find = FakeFindUseCase(results=[[_node("n")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(),
    )

    executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Submit",
                "state": "enabled",
            },
        )
    )

    call = find.calls[0]
    assert call["role"] == "button"
    assert call["name_pattern"] == "Submit"
    assert call["state"] == "enabled"


# ─── Load-time validation ────────────────────────────────────────────


def _doc(step: dict, target: str = "android") -> dict:
    return {
        "schema_version": 1,
        "id": "t",
        "title": "t",
        "target": target,
        "steps": [step],
        "evidence_policy": {"bundle": False, "redact_environment": True},
    }


def test_scroll_into_view_without_role_rejected_at_load() -> None:
    result = validate_scenario_document(
        _doc({"id": "s", "kind": "scroll_into_view", "name_pattern": "X"})
    )
    assert not result.ok
    codes = [issue.code for issue in result.issues]
    assert "scroll_into_view_role_required" in codes


def test_scroll_into_view_without_name_pattern_rejected_at_load() -> None:
    result = validate_scenario_document(
        _doc({"id": "s", "kind": "scroll_into_view", "role": "button"})
    )
    assert not result.ok
    codes = [issue.code for issue in result.issues]
    assert "scroll_into_view_name_pattern_required" in codes


def test_scroll_into_view_with_invalid_direction_rejected_at_load() -> None:
    result = validate_scenario_document(
        _doc(
            {
                "id": "s",
                "kind": "scroll_into_view",
                "role": "button",
                "name_pattern": "X",
                "direction": "diagonal",
            }
        )
    )
    assert not result.ok
    codes = [issue.code for issue in result.issues]
    assert "direction_invalid" in codes


def test_scroll_into_view_with_max_scrolls_out_of_range_rejected() -> None:
    result = validate_scenario_document(
        _doc(
            {
                "id": "s",
                "kind": "scroll_into_view",
                "role": "button",
                "name_pattern": "X",
                "max_scrolls": 0,
            }
        )
    )
    assert not result.ok


def test_scroll_into_view_valid_inputs_accepted() -> None:
    result = validate_scenario_document(
        _doc(
            {
                "id": "s",
                "kind": "scroll_into_view",
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 15,
                "max_seconds": 45.0,
                "direction": "down",
            }
        )
    )
    assert result.ok, f"Issues: {result.issues}"


# ─── Public fixture ──────────────────────────────────────────────────


def test_aiyes49_public_fixture_validates() -> None:
    fixture_path = Path("examples/scenarios/android-settings-scroll-into-view.json")
    assert fixture_path.is_file(), "AIYES-49 public fixture must exist"

    document = json.loads(fixture_path.read_text())
    result = validate_scenario_document(document, public_fixture=True)
    assert result.ok, f"AIYES-49 fixture invalid: {result.issues}"
    scenario = result.scenario
    assert scenario is not None
    kinds = {step.kind for step in scenario.steps}
    assert "scroll_into_view" in kinds
