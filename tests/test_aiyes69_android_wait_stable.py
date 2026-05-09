"""AIYES-69 — Android normalized wait-stable."""

from __future__ import annotations

import json

from aiyes.cli.presenter import format_wait_stable
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node, trees_structurally_equal
from aiyes.domain.use_cases.wait_stable import WaitStableUseCase

from tests.conftest import FakeClock, FakeSessionRepository
from tests.test_aiyes11_wait_stable import FakeAccessibilityTreeSequence


def _node(node_id: str, stable_id: str | None = None) -> Node:
    return Node(
        id=node_id,
        role="android.widget.Button",
        name="Create",
        bounds=(0, 0, 100, 40),
        states=("enabled",),
        actions=("click",),
        stable_id=stable_id,
    )


def _tree(*nodes: Node) -> AccessibilityTree:
    return AccessibilityTree(roots=nodes)


def _session(backend: str) -> Session:
    return Session(
        session_id="s1",
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
        backend=backend,
        device_serial="emulator-5554" if backend == "android" else None,
        package_name="com.app" if backend == "android" else None,
    )


def test_android_wait_stable_uses_stable_ids_before_volatile_ids() -> None:
    first = _tree(_node("volatile-1", "android:rid=com.app:id/create"))
    second = _tree(_node("volatile-9", "android:rid=com.app:id/create"))
    repo = FakeSessionRepository()
    repo.save(_session("android"))
    use_case = WaitStableUseCase(
        tree=FakeAccessibilityTreeSequence([first, second]),
        session_repo=repo,
        clock=FakeClock(),
    )

    result = use_case.execute(
        session_id="s1",
        timeout=10.0,
        poll_interval=0.1,
        consecutive=1,
    )

    assert result.stable is True
    assert result.polls == 2
    assert result.comparison_mode == "normalized_stable_id"


def test_default_wait_stable_comparison_keeps_id_based_behavior() -> None:
    first = _tree(_node("linux-1", "same-stable-id"))
    second = _tree(_node("linux-9", "same-stable-id"))

    assert trees_structurally_equal(first, second) is False


def test_wait_stable_presenter_discloses_comparison_mode() -> None:
    output = format_wait_stable(
        stable=True,
        timeout=False,
        polls=2,
        comparison_mode="normalized_stable_id",
    )

    assert json.loads(output)["comparison_mode"] == "normalized_stable_id"
