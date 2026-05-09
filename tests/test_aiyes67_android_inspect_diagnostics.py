"""AIYES-67: Android inspect diagnostics."""

from __future__ import annotations

import json

from aiyes.cli.presenter import format_inspect_result
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.use_cases.inspect import InspectDiagnostic, InspectUseCase

from tests.conftest import (
    FakeClock,
    FakeScreenshot,
    FakeScreenshotStore,
    FakeSessionRepository,
    FakeTreeStore,
)


class AndroidEmptyTree:
    last_dump_status = "empty_hierarchy"
    last_named_node_count = 0

    def get_tree(self, session) -> AccessibilityTree:
        return AccessibilityTree(roots=())


def _android_session() -> Session:
    return Session(
        session_id="android-empty",
        app_pid=123,
        app_command="adb",
        app_args=("-s", "emulator-5554", "shell", "monkey", "-p", "com.app", "1"),
        name=None,
        backend="android",
        device_serial="emulator-5554",
        package_name="com.app",
    )


def _use_case() -> InspectUseCase:
    repo = FakeSessionRepository()
    repo.save(_android_session())
    return InspectUseCase(
        tree=AndroidEmptyTree(),
        screenshot=FakeScreenshot(),
        session_repo=repo,
        tree_store=FakeTreeStore(),
        screenshot_store=FakeScreenshotStore(),
        clock=FakeClock(),
    )


def test_android_empty_tree_diagnostic_includes_evidence_and_guidance() -> None:
    result = _use_case().execute(session_id="android-empty")

    assert result.diagnostics
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "empty_accessibility_tree"
    assert diagnostic.backend == "android"
    assert diagnostic.evidence["foreground_package"] == "com.app"
    assert diagnostic.evidence["uiautomator_dump"] == "empty_hierarchy"
    assert diagnostic.evidence["screenshot"] == "available"
    assert diagnostic.evidence["named_node_count"] == "0"
    assert any("content-description" in cause for cause in diagnostic.likely_causes)
    assert all("is broken" not in cause for cause in diagnostic.likely_causes)


def test_presenter_serializes_diagnostic_evidence() -> None:
    output = format_inspect_result(
        tree=AccessibilityTree(roots=()),
        screenshot="/tmp/screenshot.png",
        timestamp="2026-05-06T00:00:00+00:00",
        diagnostics=(
            InspectDiagnostic(
                code="empty_accessibility_tree",
                severity="warning",
                backend="android",
                message="Screenshot exists but no accessibility nodes were found.",
                likely_causes=("Android views may need content-description.",),
                evidence={
                    "foreground_package": "com.app",
                    "uiautomator_dump": "empty_hierarchy",
                    "screenshot": "available",
                },
            ),
        ),
    )

    data = json.loads(output)
    diagnostic = data["diagnostics"][0]
    assert diagnostic["evidence"]["foreground_package"] == "com.app"
    assert diagnostic["evidence"]["uiautomator_dump"] == "empty_hierarchy"

