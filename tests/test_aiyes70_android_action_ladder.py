"""AIYES-70 — Android action ladder disclosure."""

from __future__ import annotations

import json
from unittest.mock import patch

from aiyes.adapters.android_action_adapter import AndroidActionAdapter
from aiyes.cli.presenter import format_action
from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import ActionPortResult
from aiyes.domain.use_cases.action import ActionUseCase

from tests.conftest import FakeSessionRepository, FakeTreeStore


def _android_session() -> Session:
    return Session(
        session_id="android-action",
        display=":99",
        app_pid=100,
        app_command="adb",
        app_args=("-p", "com.app"),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
        backend="android",
        device_serial="emulator-5554",
        package_name="com.app",
    )


def _node() -> Node:
    return Node(
        id="n_001",
        role="android.widget.Button",
        name="Create",
        bounds=(10, 20, 100, 60),
        states=("enabled",),
        actions=("click",),
    )


def test_android_bounds_tap_discloses_action_method() -> None:
    adapter = AndroidActionAdapter()
    tree = AccessibilityTree(roots=(_node(),))

    class TreeAdapter:
        def get_tree(self, session: Session) -> AccessibilityTree:
            return tree

    adapter.set_tree_adapter(TreeAdapter())

    with patch("aiyes.adapters.android_action_adapter._run_adb") as run_adb:
        result = adapter.do_action(
            _android_session(),
            "n_001",
            "click",
            registry=NodeIdRegistry(),
        )

    assert result.success is True
    assert result.action_method == "node_bounds_tap"
    assert run_adb.call_args[0][1] == ["input", "tap", "60", "50"]


def test_action_result_and_presenter_keep_existing_fields_with_method() -> None:
    class StubAction:
        def do_action(
            self,
            session: Session,
            node_id: str,
            action_name: str,
            value=None,
            registry=None,
        ) -> ActionPortResult:
            return ActionPortResult(
                success=True,
                available_actions=("click",),
                action_method="node_bounds_tap",
            )

    session = _android_session()
    repo = FakeSessionRepository()
    repo.save(session)
    store = FakeTreeStore()
    store.save_tree(session.session_id, AccessibilityTree(roots=(_node(),)), None)
    use_case = ActionUseCase(
        action=StubAction(),
        session_repo=repo,
        tree_store=store,
    )

    result = use_case.execute(session.session_id, "n_001", "click")
    output = format_action(
        status=result.status,
        action=result.action,
        target=result.target,
        action_method=result.action_method,
    )
    parsed = json.loads(output)

    assert parsed["status"] == "ok"
    assert parsed["action"] == "click"
    assert parsed["target"] == "n_001"
    assert parsed["action_method"] == "node_bounds_tap"
