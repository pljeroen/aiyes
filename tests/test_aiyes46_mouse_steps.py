"""AIYES-46: mouse_drag and mouse_scroll scenario step kinds.

Each step kind supports two coordinate modes: literal (CLI parity) and
source-anchored (resolve a prior step's first node bounds to its center,
optional dx/dy offset). Source-anchored is the durable test-script
primitive; literal is the escape hatch.
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
class RecordingMouseUseCase:
    """Captures drag/scroll/move calls."""

    drag_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    scroll_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    move_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def drag(self, session_id: str, x1: int, y1: int, x2: int, y2: int) -> Any:
        self.drag_calls.append(
            {"session_id": session_id, "x1": x1, "y1": y1, "x2": x2, "y2": y2}
        )
        return SimpleNamespace(status="ok")

    def scroll(self, session_id: str, direction: str, amount: int = 3) -> Any:
        self.scroll_calls.append(
            {"session_id": session_id, "direction": direction, "amount": amount}
        )
        return SimpleNamespace(status="ok")

    def move(self, session_id: str, x: int, y: int) -> Any:
        self.move_calls.append({"session_id": session_id, "x": x, "y": y})
        return SimpleNamespace(status="ok")


def _executor_with_session(*, mouse: Any = None) -> ScenarioUseCaseExecutor:
    start = SimpleNamespace(
        execute=lambda **kwargs: SimpleNamespace(session_id="s1", backend="linux")
    )
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=SimpleNamespace(execute=lambda **kw: []),
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        mouse=mouse,
    )
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "gedit", "wait_seconds": 0.0},
        )
    )
    return executor


# ─── mouse_drag literal ───────────────────────────────────────────────


def test_mouse_drag_literal_dispatches_with_coordinates() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)

    result = executor.execute(
        ScenarioStep(
            id="drag1",
            kind="mouse_drag",
            parameters={"x1": 100, "y1": 200, "x2": 300, "y2": 400},
        )
    )

    assert result.status == "passed"
    assert mouse.drag_calls == [
        {"session_id": "s1", "x1": 100, "y1": 200, "x2": 300, "y2": 400}
    ]


# ─── mouse_drag source-anchored ───────────────────────────────────────


def test_mouse_drag_source_anchored_uses_node_center_plus_offset() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)
    # Seed prior find output. bounds = [x, y, width, height] → center (140, 220)
    executor._outputs["find_handle"] = {
        "nodes": [{"id": "n-1", "bounds": [100, 200, 80, 40]}]
    }

    result = executor.execute(
        ScenarioStep(
            id="drag_anchor",
            kind="mouse_drag",
            parameters={"source": "find_handle", "dx": 0, "dy": -300},
        )
    )

    assert result.status == "passed"
    # center = (100 + 80/2, 200 + 40/2) = (140, 220)
    # endpoint = (140 + 0, 220 + (-300)) = (140, -80)
    assert mouse.drag_calls == [
        {"session_id": "s1", "x1": 140, "y1": 220, "x2": 140, "y2": -80}
    ]


def test_mouse_drag_ambiguous_coord_mode_fails() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)
    executor._outputs["src"] = {"nodes": [{"id": "n", "bounds": [0, 0, 10, 10]}]}

    result = executor.execute(
        ScenarioStep(
            id="bad",
            kind="mouse_drag",
            parameters={
                "x1": 1,
                "y1": 2,
                "x2": 3,
                "y2": 4,
                "source": "src",
                "dx": 0,
                "dy": 0,
            },
        )
    )

    assert result.status == "failed"
    assert (
        "coord_mode_ambiguous" in result.error.lower()
        or "ambiguous" in result.error.lower()
    )
    assert mouse.drag_calls == []


def test_mouse_drag_missing_coord_mode_fails() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)

    result = executor.execute(ScenarioStep(id="bad", kind="mouse_drag", parameters={}))

    assert result.status == "failed"
    assert "coord_mode" in result.error.lower() or "missing" in result.error.lower()
    assert mouse.drag_calls == []


def test_mouse_drag_unknown_source_fails() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)

    result = executor.execute(
        ScenarioStep(
            id="bad",
            kind="mouse_drag",
            parameters={"source": "no_such", "dx": 0, "dy": 0},
        )
    )

    assert result.status == "failed"
    assert "source" in result.error.lower()


def test_mouse_drag_source_with_no_bounds_fails() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)
    executor._outputs["src"] = {"nodes": [{"id": "n"}]}  # no bounds

    result = executor.execute(
        ScenarioStep(
            id="bad",
            kind="mouse_drag",
            parameters={"source": "src", "dx": 5, "dy": 5},
        )
    )

    assert result.status == "failed"


# ─── mouse_scroll literal ─────────────────────────────────────────────


def test_mouse_scroll_literal_dispatches_with_direction_and_amount() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)

    result = executor.execute(
        ScenarioStep(
            id="scroll1",
            kind="mouse_scroll",
            parameters={"direction": "down", "amount": 5},
        )
    )

    assert result.status == "passed"
    assert mouse.scroll_calls == [
        {"session_id": "s1", "direction": "down", "amount": 5}
    ]
    assert mouse.move_calls == []  # no source → no move


def test_mouse_scroll_uses_default_amount() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)

    executor.execute(
        ScenarioStep(id="s", kind="mouse_scroll", parameters={"direction": "up"})
    )

    assert mouse.scroll_calls[0]["amount"] == 3


def test_mouse_scroll_invalid_direction_fails() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)

    result = executor.execute(
        ScenarioStep(
            id="bad",
            kind="mouse_scroll",
            parameters={"direction": "diagonal", "amount": 1},
        )
    )

    assert result.status == "failed"
    assert "direction" in result.error.lower()
    assert mouse.scroll_calls == []


# ─── mouse_scroll source-anchored ─────────────────────────────────────


def test_mouse_scroll_source_anchored_moves_cursor_then_scrolls() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)
    executor._outputs["list_root"] = {
        "nodes": [{"id": "n", "bounds": [0, 100, 400, 600]}]
    }
    # center = (200, 400)

    result = executor.execute(
        ScenarioStep(
            id="scroll_to_target",
            kind="mouse_scroll",
            parameters={"source": "list_root", "direction": "down", "amount": 2},
        )
    )

    assert result.status == "passed"
    assert mouse.move_calls == [{"session_id": "s1", "x": 200, "y": 400}]
    assert mouse.scroll_calls == [
        {"session_id": "s1", "direction": "down", "amount": 2}
    ]


def test_mouse_scroll_source_anchored_unknown_source_fails() -> None:
    mouse = RecordingMouseUseCase()
    executor = _executor_with_session(mouse=mouse)

    result = executor.execute(
        ScenarioStep(
            id="bad",
            kind="mouse_scroll",
            parameters={"source": "no_such", "direction": "down"},
        )
    )

    assert result.status == "failed"


# ─── step kind declared ──────────────────────────────────────────────


def test_mouse_drag_and_mouse_scroll_kinds_validate() -> None:
    for kind, extra in (
        ("mouse_drag", {"x1": 0, "y1": 0, "x2": 10, "y2": 10}),
        ("mouse_scroll", {"direction": "down"}),
    ):
        document = {
            "schema_version": 1,
            "id": "k",
            "title": "k",
            "target": "linux",
            "steps": [{"id": "s", "kind": kind, **extra}],
            "evidence_policy": {"bundle": False, "redact_environment": True},
        }
        result = validate_scenario_document(document)
        assert result.ok, f"{kind} should validate: {result.issues}"


# ─── public fixture ──────────────────────────────────────────────────


def test_aiyes46_public_fixture_validates() -> None:
    fixture_path = Path("examples/scenarios/linux-gedit-mouse.json")
    assert fixture_path.is_file(), "AIYES-46 public fixture must exist"

    document = json.loads(fixture_path.read_text())
    result = validate_scenario_document(document, public_fixture=True)
    assert result.ok, f"AIYES-46 fixture invalid: {result.issues}"
    scenario = result.scenario
    assert scenario is not None
    kinds = {step.kind for step in scenario.steps}
    assert "mouse_scroll" in kinds
