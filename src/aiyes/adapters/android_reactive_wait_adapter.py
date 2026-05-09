"""Android reactive wait observer using adb-visible state and UIAutomator trees."""

from __future__ import annotations

from aiyes.domain.matching import name_matches
from aiyes.domain.reactive_wait import (
    GuiEvent,
    ReactiveWaitCondition,
    ReactiveWaitRequest,
    ReactiveWaitResult,
)
from aiyes.domain.session import Session, android_package_name
from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.adb_activity import AdbActivityQueryPort
from aiyes.ports.clock import ClockPort


class AndroidReactiveWaitObserver:
    """Reactive wait observer for Android without helper APK requirements."""

    def __init__(
        self,
        tree_port: AccessibilityTreePort,
        activity_port: AdbActivityQueryPort,
        clock: ClockPort,
    ) -> None:
        self._tree_port = tree_port
        self._activity_port = activity_port
        self._clock = clock

    def wait(
        self,
        session: Session,
        request: ReactiveWaitRequest,
    ) -> ReactiveWaitResult:
        if request.condition == ReactiveWaitCondition.APP_CHANGE:
            return self._wait_app_change(session, request)
        if request.condition in (
            ReactiveWaitCondition.SCREEN_CHANGE,
            ReactiveWaitCondition.NODE_APPEARS,
            ReactiveWaitCondition.NODE_DISAPPEARS,
        ):
            return self._wait_tree_condition(session, request)
        return ReactiveWaitResult.unsupported_condition(
            condition=request.condition.value,
            backend="android",
            elapsed_ms=0,
            polls=0,
        )

    def _wait_app_change(
        self,
        session: Session,
        request: ReactiveWaitRequest,
    ) -> ReactiveWaitResult:
        start = self._clock.now()
        serial = getattr(session, "device_serial", "") or ""
        baseline = self._activity_port.get_resumed_activity(serial)
        polls = 1

        while True:
            elapsed = self._clock.now() - start
            if elapsed >= request.timeout:
                return ReactiveWaitResult.timeout_result(
                    condition=request.condition.value,
                    backend="android",
                    source="adb_state_poll",
                    elapsed_ms=_elapsed_ms(start, self._clock.now()),
                    polls=polls,
                )
            self._sleep(request.poll_interval)
            current = self._activity_port.get_resumed_activity(serial)
            polls += 1
            if current is not None and current != baseline:
                event = GuiEvent(
                    type=request.condition.value,
                    source="adb_state_poll",
                    timestamp=self._clock.now(),
                    app=current,
                )
                return ReactiveWaitResult.matched_result(
                    condition=request.condition.value,
                    backend="android",
                    source="adb_state_poll",
                    elapsed_ms=_elapsed_ms(start, self._clock.now()),
                    polls=polls,
                    events=(event,),
                )

    def _wait_tree_condition(
        self,
        session: Session,
        request: ReactiveWaitRequest,
    ) -> ReactiveWaitResult:
        start = self._clock.now()
        baseline_tree = self._tree_port.get_tree(session)
        baseline_fp = _tree_fingerprint(baseline_tree)
        baseline_matches = _matching_nodes(baseline_tree, request.name_pattern)
        polls = 1

        while True:
            elapsed = self._clock.now() - start
            if elapsed >= request.timeout:
                return ReactiveWaitResult.timeout_result(
                    condition=request.condition.value,
                    backend="android",
                    source="snapshot_poll",
                    elapsed_ms=_elapsed_ms(start, self._clock.now()),
                    polls=polls,
                )
            self._sleep(request.poll_interval)
            tree = self._tree_port.get_tree(session)
            polls += 1
            matches = _matching_nodes(tree, request.name_pattern)

            if request.condition == ReactiveWaitCondition.SCREEN_CHANGE:
                if _tree_fingerprint(tree) != baseline_fp:
                    event = GuiEvent(
                        type=request.condition.value,
                        source="snapshot_poll",
                        timestamp=self._clock.now(),
                        app=android_package_name(session),
                    )
                    return _matched(request, event, start, self._clock.now(), polls)
            elif request.condition == ReactiveWaitCondition.NODE_APPEARS:
                appeared = _first_new_match(baseline_matches, matches)
                if appeared is not None:
                    event = _node_event(request.condition.value, appeared, self._clock.now())
                    return _matched(request, event, start, self._clock.now(), polls)
            elif request.condition == ReactiveWaitCondition.NODE_DISAPPEARS:
                if baseline_matches and not matches:
                    event = GuiEvent(
                        type=request.condition.value,
                        source="snapshot_poll",
                        timestamp=self._clock.now(),
                    )
                    return _matched(request, event, start, self._clock.now(), polls)

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._clock.sleep(seconds)


def _matched(
    request: ReactiveWaitRequest,
    event: GuiEvent,
    start: float,
    now: float,
    polls: int,
) -> ReactiveWaitResult:
    return ReactiveWaitResult.matched_result(
        condition=request.condition.value,
        backend="android",
        source="snapshot_poll",
        elapsed_ms=_elapsed_ms(start, now),
        polls=polls,
        events=(event,),
    )


def _node_event(condition: str, node: Node, timestamp: float) -> GuiEvent:
    return GuiEvent(
        type=condition,
        source="snapshot_poll",
        timestamp=timestamp,
        node_id=node.id,
        role=node.role,
        name=node.name,
    )


def _matching_nodes(tree: AccessibilityTree, name_pattern: str | None) -> tuple[Node, ...]:
    nodes = flatten_nodes(tree.roots)
    if name_pattern is None:
        return tuple(nodes)
    return tuple(node for node in nodes if name_matches(node.name, name_pattern))


def _first_new_match(baseline: tuple[Node, ...], current: tuple[Node, ...]) -> Node | None:
    baseline_keys = {_node_key(node) for node in baseline}
    for node in current:
        if _node_key(node) not in baseline_keys:
            return node
    return None


def _tree_fingerprint(tree: AccessibilityTree) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (_node_key(node), node.role, node.name)
            for node in flatten_nodes(tree.roots)
        )
    )


def _node_key(node: Node) -> str:
    return node.stable_id or node.id


def _elapsed_ms(start: float, now: float) -> int:
    return int(max(0.0, now - start) * 1000)
