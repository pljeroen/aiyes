"""AIYES-68 — Android normalized diff."""

from __future__ import annotations

from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.tree_diff import diff_trees
from aiyes.domain.use_cases.diff import DiffUseCase

from tests.conftest import FakeAccessibilityTree, FakeSessionRepository, FakeTreeStore


def _node(
    node_id: str,
    name: str,
    stable_id: str | None = None,
    bounds: tuple[int, ...] = (0, 0, 100, 40),
) -> Node:
    return Node(
        id=node_id,
        role="android.widget.Button",
        name=name,
        bounds=bounds,
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


def test_android_diff_matches_nodes_by_stable_id_when_ids_churn() -> None:
    stored = _tree(_node("volatile-1", "Create", "android:rid=com.app:id/create"))
    live = _tree(_node("volatile-9", "Create item", "android:rid=com.app:id/create"))
    repo = FakeSessionRepository()
    repo.save(_session("android"))
    store = FakeTreeStore()
    store.save_tree("s1", stored, NodeIdRegistry())
    use_case = DiffUseCase(
        tree=FakeAccessibilityTree(live),
        session_repo=repo,
        tree_store=store,
    )

    result = use_case.execute("s1")

    assert result.diff.added == ()
    assert result.diff.removed == ()
    assert [(change.id, change.field) for change in result.diff.changed] == [
        ("volatile-1", "name")
    ]


def test_default_diff_keeps_linux_id_based_behavior_even_with_stable_ids() -> None:
    before = _tree(_node("linux-1", "Create", "same-stable-id"))
    after = _tree(_node("linux-9", "Create", "same-stable-id"))

    result = diff_trees(before, after, NodeIdRegistry())

    assert [node.id for node in result.added] == ["linux-9"]
    assert [node.id for node in result.removed] == ["linux-1"]
    assert result.changed == ()
