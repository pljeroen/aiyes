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
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.use_cases.find import FindUseCase


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
        if kwargs.get("role") == "*":
            return []
        if self.results:
            return self.results.pop(0)
        return []


@dataclasses.dataclass
class RoleAwareFakeFindUseCase:
    """Returns results based on requested role."""

    exact_role: str
    exact_results: list[list[Any]]
    wildcard_results: list[list[Any]]
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        role = kwargs.get("role")
        if role == self.exact_role and self.exact_results:
            return self.exact_results.pop(0)
        if role == "*" and self.wildcard_results:
            return self.wildcard_results.pop(0)
        return []


@dataclasses.dataclass
class RecordingGesture:
    """Records swipe calls."""

    swipe_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    events: list[str] = dataclasses.field(default_factory=list)

    def swipe(
        self,
        session_id: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> Any:
        self.events.append("region_swipe")
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


@dataclasses.dataclass
class RecordingNativeScroll:
    """Records semantic native scroll calls."""

    results: list[Any]
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    events: list[str] = dataclasses.field(default_factory=list)

    def scroll(
        self,
        session: Any,
        node_id: str,
        direction: str,
        *,
        stable_id: str = "",
        bounds: tuple[int, int, int, int] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "session": session,
                "node_id": node_id,
                "direction": direction,
                "stable_id": stable_id,
                "bounds": bounds,
            }
        )
        self.events.append("native_scroll")
        if self.results:
            return self.results.pop(0)
        raise AssertionError("unexpected native scroll call")


@dataclasses.dataclass
class RecordingInspect:
    """Returns successive inspect snapshots."""

    trees: list[Any]
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.trees:
            return SimpleNamespace(tree=self.trees.pop(0))
        return SimpleNamespace(tree=None)


class FakeSessionRepo:
    """Returns a session with a known resolution."""

    def __init__(
        self,
        resolution: str = "1080x1920",
        backend: str = "android",
        device_serial: str = "",
    ) -> None:
        self._resolution = resolution
        self._backend = backend
        self._device_serial = device_serial

    def load(self, session_id: str) -> Any:
        return SimpleNamespace(
            session_id=session_id,
            resolution=self._resolution,
            backend=self._backend,
            device_serial=self._device_serial,
        )


def _node(node_id: str = "n", bounds: list[int] | None = None) -> dict:
    return {
        "id": node_id,
        "role": "button",
        "name": "Developer",
        "bounds": bounds or [0, 0, 100, 50],
    }


def _candidate(
    node_id: str,
    role: str,
    name: str,
    actions: list[str] | None = None,
) -> dict:
    return {
        "id": node_id,
        "role": role,
        "name": name,
        "bounds": [0, 0, 100, 50],
        "actions": actions if actions is not None else [],
    }


def _executor(
    *,
    find: Any,
    gesture: Any,
    session_repo: Any,
    clock: Any,
    inspect: Any | None = None,
    native_scroll: Any | None = None,
) -> ScenarioUseCaseExecutor:
    start = SimpleNamespace(
        execute=lambda **kwargs: SimpleNamespace(session_id="s1", backend="android")
    )
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=inspect or SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=find,
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        gesture=gesture,
        native_scroll=native_scroll,
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


def _tree(*nodes: Node) -> AccessibilityTree:
    return AccessibilityTree(roots=nodes)


def _tree_node(
    node_id: str,
    role: str,
    bounds: tuple[int, int, int, int],
    *,
    actions: tuple[str, ...] = (),
    states: tuple[str, ...] = (),
    children: tuple[Node, ...] = (),
) -> Node:
    return Node(
        id=node_id,
        role=role,
        name=node_id,
        bounds=bounds,
        states=states,
        actions=actions,
        children=children,
    )


def _native_result(success: bool, fallback_reason: str | None = None) -> Any:
    return SimpleNamespace(
        success=success,
        method="android_accessibility_helper",
        requested_action="ACTION_SCROLL_FORWARD",
        action_id=4096,
        node_id="settings_list",
        direction="down",
        returncode=0 if success else 1,
        stdout_summary="ok" if success else "",
        stderr_summary="" if success else "denied",
        fallback_reason=fallback_reason,
    )


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


def test_scroll_into_view_accepts_unique_actionable_role_drift_candidate() -> None:
    find = RoleAwareFakeFindUseCase(
        exact_role="View",
        exact_results=[[]],
        wildcard_results=[[_candidate("dev_button", "Button", "Developer")]],
    )
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
                "role": "View",
                "name_pattern": "Developer",
                "max_scrolls": 3,
            },
        )
    )

    assert result.status == "passed"
    assert result.output["found"] is True
    assert result.output["node_id"] == "dev_button"
    assert result.output["role_match"] == "advisory"
    assert result.output["requested_role"] == "View"
    assert result.output["actual_role"] == "Button"
    assert result.output["matched_name"] == "Developer"
    assert gesture.swipe_calls == []
    assert [call["role"] for call in find.calls] == ["View", "*"]


def test_scroll_into_view_exact_role_match_wins_before_advisory_lookup() -> None:
    find = RoleAwareFakeFindUseCase(
        exact_role="View",
        exact_results=[[_candidate("dev_view", "View", "Developer", ["focus"])]],
        wildcard_results=[[_candidate("dev_button", "Button", "Developer")]],
    )
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
            parameters={"role": "View", "name_pattern": "Developer"},
        )
    )

    assert result.status == "passed"
    assert result.output["node_id"] == "dev_view"
    assert result.output["role_match"] == "exact"
    assert "actual_role" not in result.output
    assert [call["role"] for call in find.calls] == ["View"]


def test_scroll_into_view_ambiguous_role_drift_candidates_fail_before_swipe() -> None:
    find = RoleAwareFakeFindUseCase(
        exact_role="View",
        exact_results=[[]],
        wildcard_results=[
            [
                _candidate("dev_button", "Button", "Developer"),
                _candidate("dev_link", "Link", "Developer", ["click"]),
            ]
        ],
    )
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
                "role": "View",
                "name_pattern": "Developer",
                "max_scrolls": 3,
            },
        )
    )

    assert result.status == "failed"
    assert "scroll_into_view_role_drift_ambiguous" in result.error
    assert result.output["requested_role"] == "View"
    assert result.output["name_pattern"] == "Developer"
    assert result.output["candidate_count"] == 2
    assert result.output["observed_roles"] == ["Button", "Link"]
    assert result.output["candidates"] == [
        {"node_id": "dev_button", "role": "Button", "name": "Developer"},
        {"node_id": "dev_link", "role": "Link", "name": "Developer"},
    ]
    assert gesture.swipe_calls == []


def test_scroll_into_view_role_drift_without_actionable_candidate_fails_with_diagnostics() -> None:
    find = RoleAwareFakeFindUseCase(
        exact_role="View",
        exact_results=[[]],
        wildcard_results=[[_candidate("dev_text", "TextView", "Developer")]],
    )
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
                "role": "View",
                "name_pattern": "Developer",
                "max_scrolls": 3,
            },
        )
    )

    assert result.status == "failed"
    assert "scroll_into_view_role_drift_no_actionable_candidate" in result.error
    assert result.output["requested_role"] == "View"
    assert result.output["name_pattern"] == "Developer"
    assert result.output["candidate_count"] == 0
    assert result.output["observed_roles"] == ["TextView"]
    assert gesture.swipe_calls == []


def test_find_use_case_role_view_does_not_return_button() -> None:
    class StaticTree:
        last_registry = None

        def get_tree(self, session: Any) -> AccessibilityTree:
            return AccessibilityTree(
                roots=(
                    Node(
                        id="dev_button",
                        role="Button",
                        name="Developer",
                        bounds=(0, 0, 100, 50),
                        states=("enabled",),
                        actions=("click",),
                    ),
                )
            )

    class RecordingTreeStore:
        def save_tree(
            self,
            session_id: str,
            tree: AccessibilityTree,
            registry: Any,
        ) -> None:
            return None

    uc = FindUseCase(
        tree=StaticTree(),
        session_repo=FakeSessionRepo(),
        tree_store=RecordingTreeStore(),
    )

    result = uc.execute(session_id="s1", role="View", name_pattern="Developer")

    assert result == []


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


def test_scroll_into_view_uses_scrollable_region_above_bottom_nav() -> None:
    list_node = _tree_node(
        "settings_list",
        "list",
        (0, 200, 1080, 1900),
        actions=("scroll",),
    )
    bottom_nav = _tree_node("bottom_nav", "navigation_bar", (0, 2200, 1080, 200))
    inspect = RecordingInspect(trees=[_tree(list_node, bottom_nav), _tree(list_node)])
    find = FakeFindUseCase(results=[[], [_node("target")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
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

    assert result.status == "passed"
    call = gesture.swipe_calls[0]
    assert call["x1"] == call["x2"] == 540
    assert 200 < call["y2"] < call["y1"] < 2100
    assert call["y1"] < 2200
    attempt = result.output["scroll_attempts"][0]
    assert attempt["method"] == "scrollable_region_swipe"
    assert attempt["direction"] == "down"
    assert attempt["selected_scrollable_id"] == "settings_list"
    assert attempt["selected_bounds"] == [0, 200, 1080, 1900]
    assert attempt["coordinates"] == {
        "x1": call["x1"],
        "y1": call["y1"],
        "x2": call["x2"],
        "y2": call["y2"],
    }
    assert attempt["tree_changed"] is True


def test_scroll_into_view_without_scrollable_keeps_viewport_swipe() -> None:
    inspect = RecordingInspect(trees=[_tree(_tree_node("root", "frame", (0, 0, 1080, 2400)))])
    find = FakeFindUseCase(results=[[]] * 5)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "direction": "down",
                "max_scrolls": 1,
            },
        )
    )

    assert result.status == "failed"
    assert gesture.swipe_calls == [
        {
            "session_id": "s1",
            "x1": 540,
            "y1": 2000,
            "x2": 540,
            "y2": 400,
            "duration_ms": 300,
        }
    ]
    assert result.output["scroll_attempts"][0]["method"] == "viewport_swipe"
    assert result.output["scroll_attempts"][0]["selected_scrollable_id"] is None
    assert result.output["scroll_attempts"][0]["selected_bounds"] is None


def test_scroll_into_view_records_required_diagnostics_for_every_scroll() -> None:
    list_node_1 = _tree_node(
        "settings_list",
        "list",
        (10, 100, 1000, 1800),
        actions=("scroll",),
    )
    list_node_2 = _tree_node(
        "settings_list",
        "list",
        (10, 80, 1000, 1800),
        actions=("scroll",),
    )
    list_node_3 = _tree_node(
        "settings_list",
        "list",
        (10, 60, 1000, 1800),
        actions=("scroll",),
    )
    inspect = RecordingInspect(
        trees=[
            _tree(list_node_1),
            _tree(list_node_2),
            _tree(list_node_2),
            _tree(list_node_3),
        ]
    )
    find = FakeFindUseCase(results=[[], [], [_node("target")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 3,
            },
        )
    )

    assert result.status == "passed"
    assert len(result.output["scroll_attempts"]) == 2
    for attempt in result.output["scroll_attempts"]:
        assert set(attempt) >= {
            "method",
            "direction",
            "coordinates",
            "selected_scrollable_id",
            "selected_bounds",
            "tree_changed",
        }
        assert set(attempt["coordinates"]) == {"x1", "y1", "x2", "y2"}
        assert isinstance(attempt["tree_changed"], bool)


def test_scroll_into_view_tree_changed_includes_bounds_changes() -> None:
    before_list = _tree_node(
        "settings_list",
        "list",
        (10, 100, 1000, 1800),
        actions=("scroll",),
    )
    after_list = _tree_node(
        "settings_list",
        "list",
        (10, 80, 1000, 1800),
        actions=("scroll",),
    )
    inspect = RecordingInspect(trees=[_tree(before_list), _tree(after_list)])
    find = FakeFindUseCase(results=[[], [_node("target")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 3,
            },
        )
    )

    assert result.status == "passed"
    assert result.output["scroll_attempts"][0]["tree_changed"] is True


def test_scroll_into_view_region_swipe_preserves_down_view_direction() -> None:
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    inspect = RecordingInspect(trees=[_tree(list_node), _tree(list_node)])
    find = FakeFindUseCase(results=[[], [_node("target")]])
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
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

    assert result.status == "passed"
    call = gesture.swipe_calls[0]
    assert call["y1"] > call["y2"]
    assert result.output["scroll_attempts"][0]["direction"] == "down"


def test_scroll_into_view_attempts_native_scroll_before_region_swipe_on_android() -> None:
    events: list[str] = []
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    inspect = RecordingInspect(trees=[_tree(list_node), _tree(list_node)])
    find = FakeFindUseCase(results=[[], [_node("target")]])
    gesture = RecordingGesture(events=events)
    native_scroll = RecordingNativeScroll(
        results=[_native_result(False, "native_scroll_helper_failed")],
        events=events,
    )
    executor = _executor(
        find=find,
        gesture=gesture,
        native_scroll=native_scroll,
        session_repo=FakeSessionRepo(
            resolution="1080x2400", device_serial="emulator-5554"
        ),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
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

    assert result.status == "passed"
    assert events == ["native_scroll", "region_swipe"]
    assert native_scroll.calls[0]["node_id"] == "settings_list"
    assert native_scroll.calls[0]["direction"] == "down"
    assert native_scroll.calls[0]["stable_id"] == ""
    assert native_scroll.calls[0]["bounds"] == (0, 300, 1080, 1500)
    assert native_scroll.calls[0]["session"].device_serial == "emulator-5554"
    attempt = result.output["scroll_attempts"][0]
    assert attempt["method"] == "scrollable_region_swipe"
    assert attempt["native_scroll"]["requested_action"] == "ACTION_SCROLL_FORWARD"
    assert attempt["fallback_reason"] == "native_scroll_helper_failed"


def test_scroll_into_view_native_success_suppresses_region_swipe() -> None:
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    inspect = RecordingInspect(trees=[_tree(list_node), _tree(list_node)])
    find = FakeFindUseCase(results=[[], [_node("target")]])
    gesture = RecordingGesture()
    native_scroll = RecordingNativeScroll(results=[_native_result(True)])
    executor = _executor(
        find=find,
        gesture=gesture,
        native_scroll=native_scroll,
        session_repo=FakeSessionRepo(
            resolution="1080x2400", device_serial="emulator-5554"
        ),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
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

    assert result.status == "passed"
    assert gesture.swipe_calls == []
    attempt = result.output["scroll_attempts"][0]
    assert attempt["method"] == "native_scroll"
    assert attempt["native_scroll"]["success"] is True
    assert "fallback_reason" not in attempt


def test_scroll_into_view_failed_native_scroll_falls_back_same_attempt() -> None:
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    inspect = RecordingInspect(trees=[_tree(list_node), _tree(list_node)])
    find = FakeFindUseCase(results=[[], [_node("target")]])
    gesture = RecordingGesture()
    native_scroll = RecordingNativeScroll(
        results=[_native_result(False, "native_scroll_helper_failed")]
    )
    executor = _executor(
        find=find,
        gesture=gesture,
        native_scroll=native_scroll,
        session_repo=FakeSessionRepo(
            resolution="1080x2400", device_serial="emulator-5554"
        ),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
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

    assert result.status == "passed"
    assert len(gesture.swipe_calls) == 1
    assert result.output["attempts"] == 1
    attempt = result.output["scroll_attempts"][0]
    assert attempt["method"] == "scrollable_region_swipe"
    assert attempt["fallback_reason"] == "native_scroll_helper_failed"
    assert attempt["native_scroll"]["returncode"] == 1


def test_scroll_into_view_non_android_session_does_not_attempt_native_scroll() -> None:
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    inspect = RecordingInspect(trees=[_tree(list_node), _tree(list_node)])
    find = FakeFindUseCase(results=[[], [_node("target")]])
    gesture = RecordingGesture()
    native_scroll = RecordingNativeScroll(results=[_native_result(True)])
    executor = _executor(
        find=find,
        gesture=gesture,
        native_scroll=native_scroll,
        session_repo=FakeSessionRepo(resolution="1080x2400", backend="linux"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
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

    assert result.status == "passed"
    assert native_scroll.calls == []
    assert len(gesture.swipe_calls) == 1
    assert "native_scroll" not in result.output["scroll_attempts"][0]


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


def test_scroll_into_view_stops_early_after_repeated_unchanged_scrollable() -> None:
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    inspect = RecordingInspect(trees=[_tree(list_node)] * 6)
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 8,
            },
        )
    )

    assert result.status == "failed"
    assert "target_not_found_no_progress" in result.error
    assert result.output["failure_class"] == "target_not_found_no_progress"
    assert result.output["progress"] == "unchanged"
    assert result.output["attempts"] == 2
    assert len(gesture.swipe_calls) == 2
    assert [attempt["progress"] for attempt in result.output["scroll_attempts"]] == [
        "unchanged",
        "unchanged",
    ]
    for attempt in result.output["scroll_attempts"]:
        assert isinstance(attempt["tree_fingerprint_before"], str)
        assert isinstance(attempt["tree_fingerprint_after"], str)
        assert "Developer" not in json.dumps(attempt)


def test_scroll_into_view_changing_tree_until_max_scrolls_reports_after_progress() -> None:
    inspect = RecordingInspect(
        trees=[
            _tree(
                _tree_node(
                    f"settings_list_{index}",
                    "scroll_view",
                    (0, 300 + index, 1080, 1500),
                    states=("scrollable",),
                )
            )
            for index in range(8)
        ]
    )
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 3,
            },
        )
    )

    assert result.status == "failed"
    assert result.output["failure_class"] == "target_not_found_after_progress"
    assert result.output["progress"] == "changed"
    assert result.output["attempts"] == 3
    assert [attempt["progress"] for attempt in result.output["scroll_attempts"]] == [
        "changed",
        "changed",
        "changed",
    ]


def test_scroll_into_view_unknown_progress_preserves_max_scrolls() -> None:
    inspect = RecordingInspect(trees=[])
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 4,
            },
        )
    )

    assert result.status == "failed"
    assert result.output["attempts"] == 4
    assert len(gesture.swipe_calls) == 4
    assert result.output["progress"] == "unknown"
    assert result.output["failure_class"] == "target_not_found_progress_unknown"
    assert all(
        attempt["progress"] == "unknown"
        for attempt in result.output["scroll_attempts"]
    )


def test_scroll_into_view_static_tree_without_scrollable_reports_no_scrollable() -> None:
    static_tree = _tree(_tree_node("root", "frame", (0, 0, 1080, 2400)))
    inspect = RecordingInspect(trees=[static_tree, static_tree, static_tree, static_tree])
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={
                "role": "button",
                "name_pattern": "Developer",
                "max_scrolls": 2,
            },
        )
    )

    assert result.status == "failed"
    assert result.output["progress"] == "unchanged"
    assert result.output["failure_class"] == "no_scrollable"
    assert all(
        attempt["selected_scrollable_id"] is None
        for attempt in result.output["scroll_attempts"]
    )


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
