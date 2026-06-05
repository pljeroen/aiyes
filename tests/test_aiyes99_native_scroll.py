"""AIYES-99: Android native accessibility scroll helper adapter."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiyes.adapters.android_native_scroll_adapter import AndroidNativeScrollAdapter
from aiyes.domain.tree import flatten_nodes

from tests.test_android_adapters import SAMPLE_UIAUTOMATOR_XML


def test_android_native_scroll_invokes_forward_action_helper(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(returncode=0, stdout="true\n", stderr="")

    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_adapter.subprocess.run", fake_run
    )
    adapter = AndroidNativeScrollAdapter(helper_command=("native-scroll-helper",))

    result = adapter.scroll(
        SimpleNamespace(device_serial="emulator-5554"),
        node_id="node-42",
        direction="down",
        stable_id="android:rid=example;class=ScrollView;name=List;bounds=0,0,1,1;path=0",
        bounds=(0, 0, 1, 1),
    )

    assert result.success is True
    assert result.requested_action == "ACTION_SCROLL_FORWARD"
    assert result.action_id == 4096
    assert calls[0]["cmd"] == [
        "native-scroll-helper",
        "--serial",
        "emulator-5554",
        "--node-id",
        "node-42",
        "--stable-id",
        "android:rid=example;class=ScrollView;name=List;bounds=0,0,1,1;path=0",
        "--direction",
        "down",
        "--action",
        "ACTION_SCROLL_FORWARD",
        "--action-id",
        "4096",
        "--bounds",
        "0,0,1,1",
    ]


def test_android_native_scroll_invokes_backward_action_helper(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(returncode=0, stdout="true\n", stderr="")

    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_adapter.subprocess.run", fake_run
    )
    adapter = AndroidNativeScrollAdapter(helper_command=("native-scroll-helper",))

    result = adapter.scroll(
        SimpleNamespace(device_serial="emulator-5554"),
        node_id="node-42",
        direction="up",
    )

    assert result.success is True
    assert result.requested_action == "ACTION_SCROLL_BACKWARD"
    assert result.action_id == 8192
    assert calls[0]["cmd"] == [
        "native-scroll-helper",
        "--serial",
        "emulator-5554",
        "--node-id",
        "node-42",
        "--stable-id",
        "",
        "--direction",
        "up",
        "--action",
        "ACTION_SCROLL_BACKWARD",
        "--action-id",
        "8192",
    ]


def test_android_native_scroll_unavailable_helper_returns_fallback_reason() -> None:
    adapter = AndroidNativeScrollAdapter(helper_command=())

    result = adapter.scroll(
        SimpleNamespace(device_serial="emulator-5554"),
        node_id="node-42",
        direction="down",
    )

    assert result.success is False
    assert result.fallback_reason == "native_scroll_helper_unavailable"


def test_android_native_scroll_default_helper_command_is_committed_module(
    monkeypatch: Any,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="missing service")

    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_adapter.subprocess.run", fake_run
    )
    adapter = AndroidNativeScrollAdapter()

    result = adapter.scroll(
        SimpleNamespace(device_serial="emulator-5554"),
        node_id="node-42",
        direction="down",
        stable_id="android:rid=example;class=ScrollView;name=List;bounds=0,0,1,1;path=0",
        bounds=(0, 0, 1, 1),
    )

    assert result.success is False
    assert calls[0][:3] == [
        sys.executable,
        "-m",
        "aiyes.adapters.android_native_scroll_helper",
    ]
    assert "--stable-id" in calls[0]
    assert "--bounds" in calls[0]


def test_android_native_scroll_receives_stable_selector_from_parsed_android_tree(
    monkeypatch: Any,
) -> None:
    from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_adapter.subprocess.run", fake_run
    )
    tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
    scrollable = next(node for node in flatten_nodes(tree.roots) if "scroll" in node.actions)
    assert scrollable.stable_id is not None

    result = AndroidNativeScrollAdapter(helper_command=("native-scroll-helper",)).scroll(
        SimpleNamespace(device_serial="emulator-5554"),
        node_id=scrollable.id,
        direction="down",
        stable_id=scrollable.stable_id,
        bounds=scrollable.bounds,
    )

    assert result.success is True
    assert calls[0][calls[0].index("--stable-id") + 1] == scrollable.stable_id
    assert calls[0][calls[0].index("--bounds") + 1] == "0,400,1080,1520"


def test_android_native_scroll_helper_broadcasts_stable_selector(monkeypatch: Any) -> None:
    from aiyes.adapters import android_native_scroll_helper

    calls: list[list[str]] = []

    def fake_adb_path() -> str:
        return "adb"

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="Broadcast completed", stderr="")

    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_helper.resolve_adb_path", fake_adb_path
    )
    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_helper.subprocess.run", fake_run
    )

    rc = android_native_scroll_helper.main(
        [
            "--serial",
            "emulator-5554",
            "--node-id",
            "node-42",
            "--stable-id",
            "android:rid=example;class=ScrollView;name=List;bounds=0,0,1,1;path=0",
            "--direction",
            "down",
            "--action",
            "ACTION_SCROLL_FORWARD",
            "--action-id",
            "4096",
            "--bounds",
            "0,0,1,1",
        ]
    )

    assert rc == 0
    assert calls[0] == [
        "adb",
        "-s",
        "emulator-5554",
        "shell",
        "am",
        "broadcast",
        "-a",
        "dev.aiyes.helper.NATIVE_SCROLL",
        "-n",
        "dev.aiyes.helper/.NativeScrollReceiver",
        "--es",
        "node_id",
        "node-42",
        "--es",
        "stable_id",
        "android:rid=example;class=ScrollView;name=List;bounds=0,0,1,1;path=0",
        "--es",
        "direction",
        "down",
        "--es",
        "action",
        "ACTION_SCROLL_FORWARD",
        "--ei",
        "action_id",
        "4096",
        "--es",
        "bounds",
        "0,0,1,1",
    ]


def test_android_native_scroll_helper_returns_nonzero_for_broadcast_result_failure(
    monkeypatch: Any,
) -> None:
    from aiyes.adapters import android_native_scroll_helper

    def fake_adb_path() -> str:
        return "adb"

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout='Broadcast completed: result=1, data="native scroll target not found"',
            stderr="",
        )

    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_helper.resolve_adb_path", fake_adb_path
    )
    monkeypatch.setattr(
        "aiyes.adapters.android_native_scroll_helper.subprocess.run", fake_run
    )

    rc = android_native_scroll_helper.main(
        [
            "--serial",
            "emulator-5554",
            "--node-id",
            "node-42",
            "--stable-id",
            "stable",
            "--direction",
            "down",
            "--action",
            "ACTION_SCROLL_FORWARD",
            "--action-id",
            "4096",
        ]
    )

    assert rc == 1


def test_committed_android_helper_service_performs_accessibility_node_action() -> None:
    root = Path("android/aiyes-native-scroll-helper")
    service = (
        root
        / "app/src/main/java/dev/aiyes/helper/AiyesNativeScrollService.java"
    ).read_text()
    receiver = (
        root
        / "app/src/main/java/dev/aiyes/helper/NativeScrollReceiver.java"
    ).read_text()
    manifest = (root / "app/src/main/AndroidManifest.xml").read_text()
    config = (
        root / "app/src/main/res/xml/accessibility_service_config.xml"
    ).read_text()

    assert "target.performAction(actionId)" in service
    assert "getRootInActiveWindow()" in service
    assert "findByStableId" in service
    assert service.index("getContentDescription()") < service.index("getText()")
    assert "AiyesNativeScrollService.getActiveService()" in receiver
    assert 'android:name=".NativeScrollReceiver"' in manifest
    assert 'android:canRetrieveWindowContent="true"' in config
