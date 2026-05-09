from __future__ import annotations

from aiyes.adapters.android_reactive_wait_adapter import AndroidReactiveWaitObserver
from aiyes.domain.reactive_wait import ReactiveWaitCondition, ReactiveWaitRequest
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node


class _Clock:
    def __init__(self) -> None:
        self.current = 0.0

    def now(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


class _TreePort:
    def __init__(self, trees: list[AccessibilityTree]) -> None:
        self.trees = trees
        self.index = 0

    def get_tree(self, session: Session) -> AccessibilityTree:
        tree = self.trees[min(self.index, len(self.trees) - 1)]
        self.index += 1
        return tree


class _ActivityPort:
    def __init__(self, activities: list[str | None]) -> None:
        self.activities = activities
        self.index = 0

    def get_resumed_activity(self, serial: str) -> str | None:
        activity = self.activities[min(self.index, len(self.activities) - 1)]
        self.index += 1
        return activity


def _session() -> Session:
    return Session(
        session_id="s1",
        app_pid=0,
        app_command="com.example/.Main",
        app_args=(),
        name=None,
        backend="android",
        device_serial="serial-1",
        package_name="com.example",
        activity_name=".Main",
    )


def _tree(name: str) -> AccessibilityTree:
    return AccessibilityTree(
        roots=(
            Node(
                id=f"id-{name}",
                role="button",
                name=name,
                bounds=(0, 0, 10, 10),
                states=(),
                actions=(),
                stable_id=f"stable-{name}",
            ),
        )
    )


def test_android_app_change_uses_adb_state_poll_source() -> None:
    observer = AndroidReactiveWaitObserver(
        tree_port=_TreePort([_tree("A")]),
        activity_port=_ActivityPort(["com.example/.Main", "com.example/.Next"]),
        clock=_Clock(),
    )

    result = observer.wait(
        _session(),
        ReactiveWaitRequest(
            condition=ReactiveWaitCondition.APP_CHANGE,
            timeout=1.0,
            quiet=0.0,
            poll_interval=0.1,
        ),
    )

    assert result.matched is True
    assert result.source == "adb_state_poll"
    assert result.events[0].type == "app-change"
    assert result.events[0].app == "com.example/.Next"


def test_android_screen_change_uses_snapshot_poll_source() -> None:
    observer = AndroidReactiveWaitObserver(
        tree_port=_TreePort([_tree("A"), _tree("B")]),
        activity_port=_ActivityPort([None]),
        clock=_Clock(),
    )

    result = observer.wait(
        _session(),
        ReactiveWaitRequest(
            condition=ReactiveWaitCondition.SCREEN_CHANGE,
            timeout=1.0,
            quiet=0.0,
            poll_interval=0.1,
        ),
    )

    assert result.matched is True
    assert result.source == "snapshot_poll"
    assert result.polls == 2


def test_android_node_appears_and_disappears_by_name_pattern() -> None:
    appears = AndroidReactiveWaitObserver(
        tree_port=_TreePort([_tree("Old"), _tree("Save")]),
        activity_port=_ActivityPort([None]),
        clock=_Clock(),
    ).wait(
        _session(),
        ReactiveWaitRequest(
            condition=ReactiveWaitCondition.NODE_APPEARS,
            name_pattern="Save",
            timeout=1.0,
            quiet=0.0,
            poll_interval=0.1,
        ),
    )
    disappears = AndroidReactiveWaitObserver(
        tree_port=_TreePort([_tree("Save"), _tree("Old")]),
        activity_port=_ActivityPort([None]),
        clock=_Clock(),
    ).wait(
        _session(),
        ReactiveWaitRequest(
            condition=ReactiveWaitCondition.NODE_DISAPPEARS,
            name_pattern="Save",
            timeout=1.0,
            quiet=0.0,
            poll_interval=0.1,
        ),
    )

    assert appears.matched is True
    assert appears.source == "snapshot_poll"
    assert appears.events[0].name == "Save"
    assert disappears.matched is True
    assert disappears.source == "snapshot_poll"

