"""AIYES-59: empty accessibility tree diagnostics."""

from __future__ import annotations

import json

from aiyes.cli.presenter import format_inspect_result
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.use_cases.inspect import InspectDiagnostic, InspectUseCase

from tests.conftest import (
    FakeAccessibilityTree,
    FakeClock,
    FakeScreenshot,
    FakeScreenshotStore,
    FakeSessionRepository,
    FakeTreeStore,
    make_domain_tree,
    make_node,
)


def _session(backend: str = "linux") -> Session:
    return Session(
        session_id=f"{backend}-empty-tree",
        display=":99",
        app_pid=123,
        app_command="app",
        app_args=(),
        atspi_bus_pid=124,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=125,
        name=None,
        backend=backend,
        device_serial="emulator-5554" if backend == "android" else None,
    )


def _use_case(
    session: Session,
    tree: AccessibilityTree,
    screenshot: FakeScreenshot | None = None,
) -> InspectUseCase:
    repo = FakeSessionRepository()
    repo.save(session)
    return InspectUseCase(
        tree=FakeAccessibilityTree(tree),
        screenshot=screenshot or FakeScreenshot(),
        session_repo=repo,
        tree_store=FakeTreeStore(),
        screenshot_store=FakeScreenshotStore(),
        clock=FakeClock(),
    )


class TestInspectEmptyTreeDiagnostics:
    def test_empty_tree_with_screenshot_returns_structured_diagnostics(self) -> None:
        session = _session("linux")
        uc = _use_case(session, AccessibilityTree(roots=()))

        result = uc.execute(session_id=session.session_id)

        assert result.screenshot is not None
        assert result.tree == AccessibilityTree(roots=())
        assert result.diagnostics
        diagnostic = result.diagnostics[0]
        assert diagnostic.code == "empty_accessibility_tree"
        assert diagnostic.severity == "warning"
        assert diagnostic.backend == "linux"
        assert diagnostic.likely_causes
        assert all("is broken" not in cause for cause in diagnostic.likely_causes)

    def test_non_empty_tree_has_no_empty_tree_diagnostics(self) -> None:
        session = _session("linux")
        tree = make_domain_tree([make_node("root", "frame", "Window")])
        uc = _use_case(session, tree)

        result = uc.execute(session_id=session.session_id)

        assert result.diagnostics == ()

    def test_empty_tree_without_screenshot_has_no_empty_tree_diagnostics(self) -> None:
        session = _session("android")
        uc = _use_case(session, AccessibilityTree(roots=()))

        result = uc.execute(session_id=session.session_id, no_screenshot=True)

        assert result.screenshot is None
        assert result.diagnostics == ()


class TestPresenterEmptyTreeDiagnostics:
    def test_inspect_json_includes_structured_diagnostics(self) -> None:
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
                    likely_causes=("UI may still be rendering.",),
                ),
            ),
        )

        data = json.loads(output)

        assert data["diagnostics"][0]["code"] == "empty_accessibility_tree"
        assert data["diagnostics"][0]["backend"] == "android"
        assert data["diagnostics"][0]["likely_causes"] == [
            "UI may still be rendering."
        ]

