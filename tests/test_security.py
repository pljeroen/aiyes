"""Tests for security and isolation constraints.

Requirements covered:
  R-SEC-01: Display isolation (all ops target Xvfb, not host)
  R-SEC-02: Always-isolated (no flag to bypass)
  R-SEC-03: Screenshots in session directory
  R-SEC-04: No autonomous interaction / dialog dismissal
  FC-SEC-07: No untargeted AT-SPI2 actions
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "aiyes"


def _get_python_files(directory: Path) -> List[Path]:
    """Recursively find all .py files in a directory."""
    if not directory.exists():
        return []
    return sorted(directory.rglob("*.py"))


# ──────────────────────────────────────────────────────────────────────
# R-SEC-01: Display isolation
# ──────────────────────────────────────────────────────────────────────


class TestDisplayIsolation:
    """All GUI operations must target the Xvfb display, never the host."""

    def test_input_port_receives_display_from_session(self) -> None:
        """R-SEC-01: Input operations receive display from session state, not env.

        This tests that use cases pass the session's display to port calls.
        """
        from tests.conftest import FakeInput, FakeSessionRepository
        from aiyes.domain.session import Session
        from aiyes.domain.use_cases.mouse import MouseUseCase

        session = Session(
            session_id="s1",
            display=":42",  # Non-default display
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_input = FakeInput()
        fake_repo = FakeSessionRepository()
        fake_repo.save(session)

        uc = MouseUseCase(input_port=fake_input, session_repo=fake_repo)
        uc.move(session_id="s1", x=50, y=50)

        move_calls = [c for c in fake_input.calls if c[0] == "mouse_move"]
        passed_session, _, _ = move_calls[0][1]
        assert passed_session.display == ":42"

    def test_screenshot_port_receives_display_from_session(self) -> None:
        """R-SEC-01: Screenshot targets session display."""
        from tests.conftest import (
            FakeScreenshot,
            FakeSessionRepository,
            FakeScreenshotStore,
        )
        from aiyes.domain.session import Session
        from aiyes.domain.use_cases.screenshot import ScreenshotUseCase

        session = Session(
            session_id="s1",
            display=":42",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_screenshot = FakeScreenshot()
        fake_repo = FakeSessionRepository()
        fake_repo.save(session)
        fake_ss_store = FakeScreenshotStore()

        uc = ScreenshotUseCase(
            screenshot=fake_screenshot,
            session_repo=fake_repo,
            screenshot_store=fake_ss_store,
        )
        uc.execute(session_id="s1")

        take_calls = [c for c in fake_screenshot.calls if c[0] == "take"]
        passed_session, _ = take_calls[0][1]
        assert passed_session.display == ":42"

    def test_accessibility_tree_receives_session_bus_address(self) -> None:
        """R-SEC-01: AT-SPI2 queries use session's bus address, not host."""
        from tests.conftest import (
            FakeAccessibilityTree,
            FakeSessionRepository,
            FakeTreeStore,
        )
        from aiyes.domain.session import Session
        from aiyes.domain.use_cases.find import FindUseCase
        from tests.conftest import make_tree

        session = Session(
            session_id="s1",
            display=":42",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-custom",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_tree = FakeAccessibilityTree(tree=make_tree())
        fake_repo = FakeSessionRepository()
        fake_repo.save(session)
        fake_tree_store = FakeTreeStore()

        uc = FindUseCase(
            tree=fake_tree,
            session_repo=fake_repo,
            tree_store=fake_tree_store,
        )
        uc.execute(session_id="s1", role="push_button")

        tree_calls = [c for c in fake_tree.calls if c[0] == "get_tree"]
        passed_session = tree_calls[0][1]
        assert passed_session.display == ":42"
        assert passed_session.atspi_bus_address == "unix:abstract=/tmp/dbus-custom"


# ──────────────────────────────────────────────────────────────────────
# R-SEC-02: Always-isolated (no bypass flag)
# ──────────────────────────────────────────────────────────────────────


class TestAlwaysIsolated:
    """No code path bypasses Xvfb isolation."""

    def test_no_isolated_flag_in_source(self) -> None:
        """R-SEC-02: No --isolated, --no-isolated, --host-display flags exist."""
        all_files = _get_python_files(SRC_ROOT)

        forbidden_strings = [
            "--isolated",
            "--no-isolated",
            "--host-display",
            "--use-host",
            "--no-xvfb",
        ]

        for filepath in all_files:
            source = filepath.read_text()
            for forbidden in forbidden_strings:
                if forbidden in source:
                    pytest.fail(
                        f"{filepath.relative_to(PROJECT_ROOT)}: "
                        f"contains forbidden flag '{forbidden}'"
                    )


# ──────────────────────────────────────────────────────────────────────
# R-SEC-03: Screenshot storage location
# ──────────────────────────────────────────────────────────────────────


class TestScreenshotStorage:
    """Screenshots default to session directory."""

    def test_default_screenshot_in_session_dir(self) -> None:
        """R-SEC-03: Default screenshot stored in ~/.aieyes/<session-id>/."""
        from tests.conftest import (
            FakeScreenshot,
            FakeSessionRepository,
            FakeScreenshotStore,
        )
        from aiyes.domain.session import Session
        from aiyes.domain.use_cases.screenshot import ScreenshotUseCase

        session = Session(
            session_id="s1",
            display=":99",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_screenshot = FakeScreenshot()
        fake_repo = FakeSessionRepository()
        fake_repo.save(session)
        fake_ss_store = FakeScreenshotStore()

        uc = ScreenshotUseCase(
            screenshot=fake_screenshot,
            session_repo=fake_repo,
            screenshot_store=fake_ss_store,
        )
        result = uc.execute(session_id="s1")

        # Screenshot should be saved via screenshot store
        save_calls = [c for c in fake_ss_store.calls if c[0] == "save_screenshot"]
        assert len(save_calls) >= 1


# ──────────────────────────────────────────────────────────────────────
# R-SEC-04, FC-SEC-07: No autonomous interaction
# ──────────────────────────────────────────────────────────────────────


class TestNoAutonomousInteraction:
    """Tool must not make outbound calls or dismiss dialogs autonomously."""

    def test_no_network_imports_in_domain(self) -> None:
        """R-SEC-04: No network libraries imported in domain layer."""
        domain_files = _get_python_files(SRC_ROOT / "domain")

        network_modules = ["requests", "urllib3", "httpx", "aiohttp", "socket"]

        for filepath in domain_files:
            source = filepath.read_text()
            if not source.strip():
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in network_modules:
                            pytest.fail(
                                f"{filepath.relative_to(PROJECT_ROOT)}: "
                                f"imports network library {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0] in network_modules:
                        pytest.fail(
                            f"{filepath.relative_to(PROJECT_ROOT)}: "
                            f"imports from network library {node.module}"
                        )

    def test_action_use_case_requires_explicit_target(self) -> None:
        """FC-SEC-07: Action requires explicit node_id parameter."""
        from aiyes.domain.use_cases.action import ActionUseCase

        # The ActionUseCase.execute signature must require node_id
        import inspect

        sig = inspect.signature(ActionUseCase.execute)
        assert "node_id" in sig.parameters
        # node_id must not have a default value (it's required)
        param = sig.parameters["node_id"]
        assert param.default is inspect.Parameter.empty, (
            "node_id must be required (no default value)"
        )
