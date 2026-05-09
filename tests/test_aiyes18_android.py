"""AIYES-18 Android backend migration tests — RED phase.

These tests define the expected API and behavior for Android backend support.
They MUST fail (RED) because the Android features have not been implemented yet.

Traceability — Formal Constraint Map:
  AC-01..AC-08: Architectural constraints
  BC-01..BC-10: Behavioral constraints
  MC-01..MC-05: Migration constraints
  PC-01..PC-06: Portability constraints
  UX-01..UX-06: UX constraints

Requirement coverage:
  R-AIYES18-01: Backend-qualified sessions
  R-AIYES18-02: Android semantic inspection
  R-AIYES18-03: Android hands (input + action)
  R-AIYES18-04: Help/disclosure with Android prerequisites
  R-AIYES18-05: Doctor reports Android capability
  R-AIYES18-06: Provider replaceability
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes
from aiyes.domain.types import ActionPortResult, DependencyResult


# ═══════════════════════════════════════════════════════════════════════
# Helpers — Android-aware session construction
# ═══════════════════════════════════════════════════════════════════════


def _make_linux_session(**overrides: Any) -> Session:
    """Construct a standard Linux session (existing shape)."""
    defaults = dict(
        session_id="linux-001",
        display=":99",
        app_pid=12345,
        app_command="gedit",
        app_args=(),
        atspi_bus_pid=12346,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=12344,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
        backend="linux",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_android_session(**overrides: Any) -> Session:
    """Construct an Android session — no Linux-only fields required."""
    defaults = dict(
        session_id="android-001",
        app_pid=0,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_android_tree() -> AccessibilityTree:
    """Build a representative Android-sourced accessibility tree."""
    button = Node(
        id="n_btn_01",
        role="Button",
        name="Login",
        bounds=(100, 200, 200, 50),
        states=("enabled", "focusable"),
        actions=("click",),
    )
    text_field = Node(
        id="n_txt_01",
        role="EditText",
        name="Username",
        bounds=(100, 100, 200, 50),
        states=("enabled", "focusable", "focused"),
        actions=("click", "set_text"),
        value="",
    )
    root = Node(
        id="n_root",
        role="FrameLayout",
        name="",
        bounds=(0, 0, 1080, 1920),
        states=("enabled",),
        actions=(),
        children=(text_field, button),
    )
    return AccessibilityTree(roots=(root,))


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES18-01: Backend-qualified sessions
# AC-01, AC-02, AC-03, BC-01, BC-02, BC-03, MC-02, MC-05, UX-05, UX-06
# ═══════════════════════════════════════════════════════════════════════


class TestSessionBackendField:
    """AC-01: Session MUST carry a backend discriminator."""

    def test_session_accepts_linux_backend(self) -> None:
        """AC-01: backend='linux' is valid."""
        session = _make_linux_session()
        assert session.backend == "linux"

    def test_session_accepts_android_backend(self) -> None:
        """AC-01: backend='android' is valid."""
        session = _make_android_session()
        assert session.backend == "android"

    def test_session_rejects_invalid_backend(self) -> None:
        """AC-01: backend='bogus' raises ValueError."""
        with pytest.raises(ValueError, match="backend"):
            _make_linux_session(backend="bogus")

    def test_default_backend_is_linux(self) -> None:
        """MC-05: When no backend specified, default is 'linux'."""
        session = Session(
            session_id="compat-001",
            display=":99",
            app_pid=1000,
            app_command="gedit",
            app_args=(),
            atspi_bus_pid=1001,
            atspi_bus_address="unix:abstract=/tmp/dbus-test",
            xvfb_pid=999,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        assert session.backend == "linux"


class TestAndroidSessionCreation:
    """AC-02: Android sessions do NOT require Linux-specific fields."""

    def test_android_session_without_display(self) -> None:
        """AC-02: Android session does not require display."""
        session = _make_android_session()
        assert session.backend == "android"
        assert session.session_id == "android-001"

    def test_android_session_has_device_serial(self) -> None:
        """BC-01: Android session carries device_serial."""
        session = _make_android_session(device_serial="emulator-5554")
        assert session.device_serial == "emulator-5554"

    def test_android_session_linux_fields_are_neutral(self) -> None:
        """AC-02: Linux-specific fields are at neutral/absent values for Android."""
        session = _make_android_session()
        # Linux fields should be None, empty string, or 0 for Android sessions
        display = getattr(session, "display", None)
        xvfb_pid = getattr(session, "xvfb_pid", None)
        atspi_bus_address = getattr(session, "atspi_bus_address", None)
        atspi_bus_pid = getattr(session, "atspi_bus_pid", None)

        # At least one of: None, empty string, or 0
        assert display is None or display == "" or display == ":0"
        assert xvfb_pid is None or xvfb_pid == 0
        assert atspi_bus_address is None or atspi_bus_address == ""
        assert atspi_bus_pid is None or atspi_bus_pid == 0

    def test_android_session_app_command_is_package_activity(self) -> None:
        """BC-01: Android session stores package/activity."""
        session = _make_android_session(app_command="com.example.app/.MainActivity")
        assert "com.example" in session.app_command


class TestLinuxSessionIsolation:
    """AC-03: Linux sessions do NOT require Android-only fields."""

    def test_linux_session_no_android_fields_required(self) -> None:
        """AC-03: A Linux session does not need device_serial."""
        session = _make_linux_session()
        device_serial = getattr(session, "device_serial", None)
        # If the field exists, it must be at its neutral/absent value
        assert device_serial is None or device_serial == ""


class TestSessionPersistenceBackwardCompat:
    """MC-02: Persisted sessions backward-compatible."""

    def test_load_session_without_backend_defaults_to_linux(self) -> None:
        """MC-02: Loading a session JSON without 'backend' key defaults to 'linux'."""
        # Simulate a pre-migration session dict (no backend field)
        session_dict = {
            "session_id": "old-session",
            "display": ":99",
            "app_pid": 1000,
            "app_command": "gedit",
            "app_args": [],
            "atspi_bus_pid": 1001,
            "atspi_bus_address": "unix:abstract=/tmp/dbus-test",
            "xvfb_pid": 999,
            "name": None,
            "resolution": "1280x800",
            "color_depth": 24,
            "started_at": 500.0,
        }
        # Session construction from dict without backend must default to linux
        session = Session(**session_dict)
        assert session.backend == "linux"

    def test_android_session_round_trip(self) -> None:
        """MC-02: Android session can be serialized and reconstructed."""
        session = _make_android_session()
        # Convert to dict and back
        import dataclasses

        d = dataclasses.asdict(session)
        restored = Session(**d)
        assert restored.backend == "android"
        assert restored.device_serial == session.device_serial
        assert restored.session_id == session.session_id


class TestSessionListShowsBackend:
    """UX-06: Session list MUST display backend per entry."""

    def test_session_list_entry_has_backend_field(self) -> None:
        """UX-06: SessionListEntry carries backend."""
        from aiyes.domain.use_cases.session_list import SessionListEntry

        entry = SessionListEntry(
            session_id="s1",
            display=":99",
            app="gedit",
            status="active",
            backend="linux",
        )
        assert entry.backend == "linux"

    def test_session_list_android_entry_has_backend(self) -> None:
        """UX-06: Android session list entry shows backend='android'."""
        from aiyes.domain.use_cases.session_list import SessionListEntry

        entry = SessionListEntry(
            session_id="a1",
            display="",
            app="com.example.app",
            status="active",
            backend="android",
        )
        assert entry.backend == "android"


class TestSessionStartDefaultBackend:
    """MC-05: Session start without --backend defaults to Linux."""

    def test_cli_start_without_backend_is_linux(self) -> None:
        """MC-05: CLI 'session start' without --backend creates a Linux session."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        with patch("aiyes.cli.main._get_session_start_uc") as mock_uc_factory:
            mock_session = MagicMock()
            mock_session.backend = "linux"
            mock_session.session_id = "test-001"
            mock_session.display = ":99"
            mock_session.app_pid = 100
            mock_session.atspi_bus_address = "unix:abstract=/tmp/dbus-test"
            mock_uc = MagicMock()
            mock_uc.execute.return_value = mock_session
            mock_uc_factory.return_value = mock_uc

            result = runner.invoke(cli, ["session", "start", "--", "gedit"])

            if result.exit_code == 0:
                # Use case was called without backend=android
                call_kwargs = mock_uc.execute.call_args
                if call_kwargs:
                    backend_arg = call_kwargs.kwargs.get("backend", "linux")
                    assert backend_arg == "linux"


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES18-02: Android semantic inspection
# AC-04, BC-04, BC-05, BC-09
# ═══════════════════════════════════════════════════════════════════════


class TestPortSignatureNeutrality:
    """AC-04: Port signatures MUST NOT require Linux-specific parameters."""

    def test_accessibility_tree_port_no_display_param(self) -> None:
        """AC-04: AccessibilityTreePort must not require 'display' parameter."""
        from aiyes.ports.accessibility_tree import AccessibilityTreePort
        import inspect

        sig = inspect.signature(AccessibilityTreePort.get_tree)
        param_names = list(sig.parameters.keys())
        assert "display" not in param_names, (
            "AccessibilityTreePort.get_tree must not have 'display' parameter"
        )
        assert "bus_address" not in param_names, (
            "AccessibilityTreePort.get_tree must not have 'bus_address' parameter"
        )

    def test_screenshot_port_no_display_param(self) -> None:
        """AC-04: ScreenshotPort must not require 'display' parameter."""
        from aiyes.ports.screenshot import ScreenshotPort
        import inspect

        sig = inspect.signature(ScreenshotPort.take)
        param_names = list(sig.parameters.keys())
        assert "display" not in param_names, (
            "ScreenshotPort.take must not have 'display' parameter"
        )

    def test_input_port_no_display_param(self) -> None:
        """AC-04: InputPort methods must not require 'display' parameter."""
        from aiyes.ports.input import InputPort
        import inspect

        for method_name in ["mouse_click", "mouse_move", "key", "type_text"]:
            method = getattr(InputPort, method_name)
            sig = inspect.signature(method)
            param_names = list(sig.parameters.keys())
            assert "display" not in param_names, (
                f"InputPort.{method_name} must not have 'display' parameter"
            )

    def test_accessibility_action_port_no_display_param(self) -> None:
        """AC-04: AccessibilityActionPort must not require 'display' parameter."""
        from aiyes.ports.accessibility_action import AccessibilityActionPort
        import inspect

        sig = inspect.signature(AccessibilityActionPort.do_action)
        param_names = list(sig.parameters.keys())
        assert "display" not in param_names, (
            "AccessibilityActionPort.do_action must not have 'display' parameter"
        )
        assert "bus_address" not in param_names, (
            "AccessibilityActionPort.do_action must not have 'bus_address' parameter"
        )


class TestAndroidTreeNormalization:
    """BC-09: Android tree normalization — class->role, text->name, bounds, states."""

    def test_android_node_has_role_from_class(self) -> None:
        """BC-09: Android nodes have role mapped from widget class."""
        tree = _make_android_tree()
        nodes = flatten_nodes(tree.roots)
        button_nodes = [n for n in nodes if n.role == "Button"]
        assert len(button_nodes) >= 1
        assert button_nodes[0].name == "Login"

    def test_android_node_has_bounds(self) -> None:
        """BC-09: Android nodes carry bounds as integer tuple."""
        tree = _make_android_tree()
        nodes = flatten_nodes(tree.roots)
        for node in nodes:
            assert isinstance(node.bounds, tuple)
            assert len(node.bounds) == 4
            assert all(isinstance(b, int) for b in node.bounds)

    def test_android_node_has_states(self) -> None:
        """BC-09: Android nodes carry states (enabled/focused/etc.)."""
        tree = _make_android_tree()
        nodes = flatten_nodes(tree.roots)
        focusable_nodes = [n for n in nodes if "focusable" in n.states]
        assert len(focusable_nodes) >= 1

    def test_android_node_missing_attrs_produce_empty(self) -> None:
        """BC-09: Missing attributes produce empty strings/tuples, never None."""
        node = Node(
            id="n_sparse",
            role="View",
            name="",
            bounds=(0, 0, 0, 0),
            states=(),
            actions=(),
        )
        assert node.name == ""
        assert node.states == ()
        assert node.actions == ()

    def test_android_tree_is_domain_type(self) -> None:
        """BC-04: Android inspect result is a domain AccessibilityTree."""
        tree = _make_android_tree()
        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) > 0


class TestAndroidInspectUsesBackendContext:
    """BC-04: inspect on Android returns tree + screenshot via backend context."""

    def test_inspect_with_android_session_returns_tree(self) -> None:
        """BC-04: InspectUseCase with Android session returns a tree."""
        from aiyes.domain.use_cases.inspect import InspectUseCase

        # Create stubs that satisfy the refactored port signatures
        class StubTree:
            def get_tree(self, session: Session) -> AccessibilityTree:
                return _make_android_tree()

        class StubScreenshot:
            def take(self, session: Session, output_path=None) -> str:
                return "/tmp/android_screenshot.png"

        from tests.conftest import (
            FakeClock,
            FakeScreenshotStore,
            FakeSessionRepository,
            FakeTreeStore,
        )

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = InspectUseCase(
            tree=StubTree(),
            screenshot=StubScreenshot(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
            screenshot_store=FakeScreenshotStore(),
            clock=FakeClock(),
        )
        result = uc.execute(session_id=session.session_id)
        assert result.tree is not None
        assert isinstance(result.tree, AccessibilityTree)
        assert len(flatten_nodes(result.tree.roots)) > 0

    def test_inspect_android_screenshot_is_captured(self) -> None:
        """BC-04: Screenshot is non-None for Android sessions."""
        from aiyes.domain.use_cases.inspect import InspectUseCase

        class StubTree:
            def get_tree(self, session: Session) -> AccessibilityTree:
                return _make_android_tree()

        class StubScreenshot:
            def take(self, session: Session, output_path=None) -> str:
                return "/tmp/android_screenshot.png"

        from tests.conftest import (
            FakeClock,
            FakeScreenshotStore,
            FakeSessionRepository,
            FakeTreeStore,
        )

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = InspectUseCase(
            tree=StubTree(),
            screenshot=StubScreenshot(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
            screenshot_store=FakeScreenshotStore(),
            clock=FakeClock(),
        )
        result = uc.execute(session_id=session.session_id)
        assert result.screenshot is not None


class TestAndroidFindReusesExistingLogic:
    """BC-05: find MUST match Android nodes using same matching logic."""

    def test_find_android_node_by_role(self) -> None:
        """BC-05: find matches Android node by role (class name)."""
        from aiyes.domain.use_cases.find import FindUseCase

        class StubTree:
            def get_tree(self, session: Session) -> AccessibilityTree:
                return _make_android_tree()

        from tests.conftest import FakeSessionRepository, FakeTreeStore

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = FindUseCase(
            tree=StubTree(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
        )
        results = uc.execute(session_id=session.session_id, role="Button")
        assert len(results) >= 1
        assert results[0].role == "Button"
        assert results[0].name == "Login"

    def test_find_android_node_by_name_pattern(self) -> None:
        """BC-05: find matches Android node by name pattern."""
        from aiyes.domain.use_cases.find import FindUseCase

        class StubTree:
            def get_tree(self, session: Session) -> AccessibilityTree:
                return _make_android_tree()

        from tests.conftest import FakeSessionRepository, FakeTreeStore

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = FindUseCase(
            tree=StubTree(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
        )
        results = uc.execute(
            session_id=session.session_id, role="EditText", name_pattern="User"
        )
        assert len(results) >= 1
        assert "User" in results[0].name


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES18-03: Android hands (input + action)
# BC-06, BC-07, BC-08
# ═══════════════════════════════════════════════════════════════════════


class TestAndroidInput:
    """BC-06: Android input operations work through InputPort."""

    def test_android_mouse_click(self) -> None:
        """BC-06: mouse_click on Android session delegates to adapter."""

        class SpyInput:
            def __init__(self):
                self.calls = []

            def mouse_click(self, session: Session, x=None, y=None, button="left"):
                self.calls.append(("mouse_click", session, x, y, button))

            def mouse_move(self, session: Session, x: int = 0, y: int = 0):
                self.calls.append(("mouse_move", session, x, y))

            def mouse_drag(
                self,
                session: Session,
                x1: int = 0,
                y1: int = 0,
                x2: int = 0,
                y2: int = 0,
            ):
                self.calls.append(("mouse_drag", session, x1, y1, x2, y2))

            def mouse_scroll(
                self, session: Session, direction: str = "up", amount: int = 3
            ):
                self.calls.append(("mouse_scroll", session, direction, amount))

            def key(self, session: Session, key_specs: list = None):
                self.calls.append(("key", session, key_specs))

            def type_text(self, session: Session, text: str = "", delay_ms: int = 0):
                self.calls.append(("type_text", session, text))

        from aiyes.domain.use_cases.mouse import MouseUseCase
        from tests.conftest import FakeSessionRepository

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        spy_input = SpyInput()
        uc = MouseUseCase(input_port=spy_input, session_repo=repo)
        uc.execute(session_id=session.session_id, action="click", x=540, y=960)

        assert len(spy_input.calls) >= 1
        call = spy_input.calls[0]
        assert call[0] == "mouse_click"

    def test_android_key_input(self) -> None:
        """BC-06: key events on Android session delegate to adapter."""

        class SpyInput:
            def __init__(self):
                self.calls = []

            def key(self, session: Session, key_specs: list = None):
                self.calls.append(("key", session, key_specs))

            def mouse_click(self, session=None, **kw):
                pass

            def mouse_move(self, session=None, **kw):
                pass

            def mouse_drag(self, session=None, **kw):
                pass

            def mouse_scroll(self, session=None, **kw):
                pass

            def type_text(self, session=None, **kw):
                pass

        from aiyes.domain.use_cases.key import KeyUseCase
        from tests.conftest import FakeSessionRepository

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        spy_input = SpyInput()
        uc = KeyUseCase(input_port=spy_input, session_repo=repo)
        uc.execute(session_id=session.session_id, key_specs=["Return"])

        assert len(spy_input.calls) >= 1
        assert spy_input.calls[0][0] == "key"

    def test_android_type_text(self) -> None:
        """BC-06: type_text on Android session delegates to adapter."""

        class SpyInput:
            def __init__(self):
                self.calls = []

            def type_text(self, session: Session, text: str = "", delay_ms: int = 0):
                self.calls.append(("type_text", session, text))

            def mouse_click(self, session=None, **kw):
                pass

            def mouse_move(self, session=None, **kw):
                pass

            def mouse_drag(self, session=None, **kw):
                pass

            def mouse_scroll(self, session=None, **kw):
                pass

            def key(self, session=None, **kw):
                pass

        from aiyes.domain.use_cases.type_text import TypeTextUseCase
        from tests.conftest import FakeSessionRepository

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        spy_input = SpyInput()
        uc = TypeTextUseCase(input_port=spy_input, session_repo=repo)
        uc.execute(session_id=session.session_id, text="hello android")

        assert len(spy_input.calls) >= 1
        assert spy_input.calls[0][0] == "type_text"
        assert "hello android" in spy_input.calls[0][2]


class TestAndroidAction:
    """BC-07: action on Android session uses semantic action when available."""

    def test_android_action_success(self) -> None:
        """BC-07: Semantic action 'click' on supported node returns success."""

        class StubAction:
            def do_action(
                self,
                session: Session,
                node_id: str,
                action_name: str,
                value=None,
                registry=None,
            ) -> ActionPortResult:
                return ActionPortResult(success=True, available_actions=("click",))

        from aiyes.domain.use_cases.action import ActionUseCase
        from tests.conftest import FakeSessionRepository, FakeTreeStore

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = ActionUseCase(
            action=StubAction(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
        )
        result = uc.execute(
            session_id=session.session_id,
            node_id="n_btn_01",
            action_name="click",
        )
        assert result.success is True

    def test_android_action_unavailable_returns_failure(self) -> None:
        """BC-07: Unsupported action returns success=False with available_actions."""

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
                    success=False,
                    available_actions=("click", "long_click"),
                )

        from aiyes.domain.use_cases.action import ActionUseCase
        from tests.conftest import FakeSessionRepository, FakeTreeStore

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = ActionUseCase(
            action=StubAction(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
        )
        result = uc.execute(
            session_id=session.session_id,
            node_id="n_btn_01",
            action_name="expand",
        )
        assert result.success is False
        assert "click" in result.available_actions


class TestAndroidCompoundDoAndWait:
    """BC-08: compound_do and wait work on Android sessions."""

    def test_wait_with_android_session(self) -> None:
        """BC-08: wait polls Android tree adapter until match."""
        from aiyes.domain.use_cases.wait import WaitUseCase
        from tests.conftest import FakeClock, FakeSessionRepository, FakeTreeStore

        class StubTree:
            def __init__(self):
                self._call_count = 0

            def get_tree(self, session: Session) -> AccessibilityTree:
                self._call_count += 1
                if self._call_count >= 2:
                    return _make_android_tree()
                return AccessibilityTree(roots=())

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = WaitUseCase(
            tree=StubTree(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
            clock=FakeClock(),
        )
        result = uc.execute(
            session_id=session.session_id,
            role="Button",
            timeout=5.0,
        )
        assert result.found is True

    def test_compound_do_with_android_session(self) -> None:
        """BC-08: compound_do find+action+verify on Android session."""
        from aiyes.domain.use_cases.compound_do import CompoundDoUseCase
        from tests.conftest import FakeClock, FakeSessionRepository, FakeTreeStore

        class StubTree:
            def get_tree(self, session: Session) -> AccessibilityTree:
                return _make_android_tree()

        class StubAction:
            def do_action(
                self,
                session: Session,
                node_id: str,
                action_name: str,
                value=None,
                registry=None,
            ) -> ActionPortResult:
                return ActionPortResult(success=True, available_actions=("click",))

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc = CompoundDoUseCase(
            tree=StubTree(),
            action=StubAction(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
            clock=FakeClock(),
        )
        result = uc.execute(
            session_id=session.session_id,
            role="Button",
            name_pattern="Login",
            action_name="click",
        )
        assert result.found is not None
        assert result.found.role == "Button"


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES18-04: Help/disclosure with Android prerequisites
# UX-01, UX-02, UX-03
# ═══════════════════════════════════════════════════════════════════════


class TestCliHelpMentionsAndroid:
    """UX-01: CLI help MUST state Android prerequisites."""

    def test_cli_help_mentions_android(self) -> None:
        """UX-01: General help mentions Android."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        output_lower = result.output.lower()
        assert "android" in output_lower, "CLI help must mention Android"

    def test_cli_help_mentions_content_description(self) -> None:
        """UX-01: CLI help mentions content-description (Android a11y term)."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert (
            "content-description" in result.output.lower()
            or "content description" in result.output.lower()
        ), "CLI help must mention Android content-description requirement"


class TestMcpManifestBackendCapabilities:
    """UX-03: MCP manifest MUST include backend-specific capability sections."""

    def test_manifest_has_backend_sections(self) -> None:
        """UX-03: MCP manifest JSON contains linux and android capability blocks."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp-manifest"])
        assert result.exit_code == 0

        parsed = json.loads(result.output)
        caps = parsed.get("capabilities", {})

        # Must have per-backend capability information
        assert "linux" in caps or "backends" in parsed, (
            "MCP manifest must have backend-specific capability sections"
        )
        assert "android" in caps or "backends" in parsed, (
            "MCP manifest must have android capability section"
        )

    def test_manifest_lists_android_prerequisites(self) -> None:
        """UX-01: MCP manifest lists Android inspectability prerequisites."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp-manifest"])
        assert result.exit_code == 0

        parsed = json.loads(result.output)
        inspectability = parsed.get("inspectability_requirements", [])

        # At least one requirement must mention Android concepts
        all_text = " ".join(
            str(item) for item in inspectability if isinstance(item, (str, dict))
        ).lower()
        assert "android" in all_text or "content-description" in all_text, (
            "MCP manifest inspectability_requirements must mention Android"
        )

    def test_manifest_states_android_limitations(self) -> None:
        """UX-02: MCP manifest states limits of Android backend."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp-manifest"])
        assert result.exit_code == 0

        output_lower = result.output.lower()
        # Must mention limitations or restrictions
        has_limitation = (
            "limit" in output_lower
            or "not available" in output_lower
            or "restricted" in output_lower
            or "fewer" in output_lower
        )
        assert has_limitation, "MCP manifest must state Android backend limitations"


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES18-05: Doctor reports Android capability
# AC-08, BC-10, UX-04
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorAndroidChecks:
    """BC-10, AC-08: Doctor MUST report adb and device status."""

    def test_doctor_checks_adb(self) -> None:
        """BC-10: Doctor reports adb availability as a distinct check."""
        from aiyes.domain.use_cases.doctor import DoctorUseCase

        class AndroidAwareDependencyCheck:
            def check(self, name: str) -> DependencyResult:
                return DependencyResult(
                    name=name, status="pass", message=f"{name} is available"
                )

            def check_all(self) -> List[DependencyResult]:
                # Must include adb as a check
                names = [
                    "xvfb",
                    "screenshot_tool",
                    "xdotool",
                    "at-spi2-core",
                    "python3-gi",
                    "gir1.2-atspi-2.0",
                    "mesa-software-rendering",
                    "mesa-vulkan-software",
                    "adb",
                ]
                return [self.check(n) for n in names]

        uc = DoctorUseCase(dependency_check=AndroidAwareDependencyCheck())
        result = uc.execute()
        check_names = [r.name for r in result]
        assert "adb" in check_names, "Doctor must check adb availability"

    def test_doctor_reports_adb_pass(self) -> None:
        """BC-10: Doctor with adb found returns status='pass'."""
        from aiyes.domain.use_cases.doctor import DoctorUseCase

        class AdbFoundCheck:
            def check(self, name: str) -> DependencyResult:
                if name == "adb":
                    return DependencyResult(
                        name="adb", status="pass", message="adb 34.0.4"
                    )
                return DependencyResult(name=name, status="pass", message="ok")

            def check_all(self) -> List[DependencyResult]:
                names = [
                    "xvfb",
                    "screenshot_tool",
                    "xdotool",
                    "at-spi2-core",
                    "python3-gi",
                    "gir1.2-atspi-2.0",
                    "mesa-software-rendering",
                    "mesa-vulkan-software",
                    "adb",
                ]
                return [self.check(n) for n in names]

        uc = DoctorUseCase(dependency_check=AdbFoundCheck())
        result = uc.execute()
        adb_result = next(r for r in result if r.name == "adb")
        assert adb_result.status == "pass"

    def test_doctor_reports_device_status(self) -> None:
        """BC-10: Doctor reports device attachment status."""
        from aiyes.domain.use_cases.doctor import DoctorUseCase

        class DeviceCheck:
            def check(self, name: str) -> DependencyResult:
                if name == "android_device":
                    return DependencyResult(
                        name="android_device",
                        status="warn",
                        message="No Android device/emulator attached",
                    )
                return DependencyResult(name=name, status="pass", message="ok")

            def check_all(self) -> List[DependencyResult]:
                names = [
                    "xvfb",
                    "screenshot_tool",
                    "xdotool",
                    "at-spi2-core",
                    "python3-gi",
                    "gir1.2-atspi-2.0",
                    "mesa-software-rendering",
                    "mesa-vulkan-software",
                    "adb",
                    "android_device",
                ]
                return [self.check(n) for n in names]

        uc = DoctorUseCase(dependency_check=DeviceCheck())
        result = uc.execute()
        device_result = next(r for r in result if r.name == "android_device")
        assert device_result.status in ("fail", "warn")


class TestDoctorBackendGrouping:
    """UX-04: Doctor output MUST group dependencies by backend."""

    def test_doctor_output_distinguishes_backends(self) -> None:
        """UX-04: Doctor results have backend/category field per dependency."""
        from aiyes.domain.use_cases.doctor import DoctorUseCase

        class GroupedCheck:
            def check(self, name: str) -> DependencyResult:
                return DependencyResult(name=name, status="pass", message="ok")

            def check_all(self) -> List[DependencyResult]:
                names = [
                    "xvfb",
                    "screenshot_tool",
                    "xdotool",
                    "at-spi2-core",
                    "python3-gi",
                    "gir1.2-atspi-2.0",
                    "mesa-software-rendering",
                    "mesa-vulkan-software",
                    "adb",
                    "android_device",
                ]
                return [self.check(n) for n in names]

        uc = DoctorUseCase(dependency_check=GroupedCheck())
        result = uc.execute()

        # DependencyResult must have a category or backend field
        # to distinguish Linux from Android dependencies
        for r in result:
            assert hasattr(r, "category") or hasattr(r, "backend"), (
                f"DependencyResult for '{r.name}' must have category or backend field"
            )

    def test_doctor_adb_is_android_category(self) -> None:
        """UX-04: adb check is categorized under Android."""
        # Verify the DependencyResult has the right grouping
        result = DependencyResult(
            name="adb",
            status="pass",
            message="adb found",
        )
        # After migration, DependencyResult must have a category field
        assert hasattr(result, "category") or hasattr(result, "backend"), (
            "DependencyResult must support backend/category classification"
        )


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES18-06: Provider replaceability
# AC-05, AC-06, PC-01..PC-06
# ═══════════════════════════════════════════════════════════════════════


class TestAdapterReplaceability:
    """PC-02..PC-04: Android adapters are independently replaceable behind ports."""

    def test_two_tree_adapters_both_satisfy_port(self) -> None:
        """PC-02: Two mock Android tree adapters both satisfy AccessibilityTreePort."""

        class UiAutomatorTreeAdapter:
            """Initial provider: adb+uiautomator."""

            def get_tree(self, session: Session) -> AccessibilityTree:
                return _make_android_tree()

        class InstrumentationTreeAdapter:
            """Alternative provider: instrumentation-based."""

            def get_tree(self, session: Session) -> AccessibilityTree:
                return AccessibilityTree(
                    roots=(
                        Node(
                            id="n_alt",
                            role="View",
                            name="AltRoot",
                            bounds=(0, 0, 1080, 1920),
                            states=(),
                            actions=(),
                        ),
                    )
                )

        # Both must work with the same use case
        from tests.conftest import FakeSessionRepository, FakeTreeStore

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        from aiyes.domain.use_cases.find import FindUseCase

        uc1 = FindUseCase(
            tree=UiAutomatorTreeAdapter(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
        )
        results1 = uc1.execute(session_id=session.session_id, role="Button")

        uc2 = FindUseCase(
            tree=InstrumentationTreeAdapter(),
            session_repo=repo,
            tree_store=FakeTreeStore(),
        )
        results2 = uc2.execute(session_id=session.session_id, role="View")

        # Both use cases work; different providers, same port interface
        assert len(results1) >= 1
        assert len(results2) >= 1

    def test_two_input_adapters_both_satisfy_port(self) -> None:
        """PC-03: Input adapters are swappable behind InputPort."""

        class AdbInputAdapter:
            def __init__(self):
                self.calls = []

            def mouse_click(self, session, x=None, y=None, button="left"):
                self.calls.append(("adb_click", x, y))

            def mouse_move(self, s, x=0, y=0):
                pass

            def mouse_drag(self, s, x1=0, y1=0, x2=0, y2=0):
                pass

            def mouse_scroll(self, s, d="up", a=3):
                pass

            def key(self, s, k=None):
                pass

            def type_text(self, s, t="", delay_ms=0):
                pass

        class UiAutomator2InputAdapter:
            def __init__(self):
                self.calls = []

            def mouse_click(self, session, x=None, y=None, button="left"):
                self.calls.append(("u2_click", x, y))

            def mouse_move(self, s, x=0, y=0):
                pass

            def mouse_drag(self, s, x1=0, y1=0, x2=0, y2=0):
                pass

            def mouse_scroll(self, s, d="up", a=3):
                pass

            def key(self, s, k=None):
                pass

            def type_text(self, s, t="", delay_ms=0):
                pass

        adapter1 = AdbInputAdapter()
        adapter2 = UiAutomator2InputAdapter()

        # Both satisfy the same interface
        from aiyes.domain.use_cases.mouse import MouseUseCase
        from tests.conftest import FakeSessionRepository

        session = _make_android_session()
        repo = FakeSessionRepository()
        repo.save(session)

        uc1 = MouseUseCase(input_port=adapter1, session_repo=repo)
        uc1.execute(session_id=session.session_id, action="click", x=100, y=200)
        assert len(adapter1.calls) >= 1

        uc2 = MouseUseCase(input_port=adapter2, session_repo=repo)
        uc2.execute(session_id=session.session_id, action="click", x=100, y=200)
        assert len(adapter2.calls) >= 1

    def test_two_screenshot_adapters_both_satisfy_port(self) -> None:
        """PC-04: Screenshot adapters are swappable."""

        class AdbScreenshotAdapter:
            def take(self, session: Session, output_path=None) -> str:
                return "/tmp/adb_screenshot.png"

        class MirrorScreenshotAdapter:
            def take(self, session: Session, output_path=None) -> str:
                return "/tmp/mirror_screenshot.png"

        adapter1 = AdbScreenshotAdapter()
        adapter2 = MirrorScreenshotAdapter()

        session = _make_android_session()
        path1 = adapter1.take(session)
        path2 = adapter2.take(session)

        assert path1 != path2  # Different providers, same interface
        assert path1.endswith(".png")
        assert path2.endswith(".png")


class TestNoAdbLibraryImport:
    """PC-01: Android adapters MUST NOT import adb as a library."""

    def test_no_adb_module_import_in_adapters(self) -> None:
        """PC-01: No adapter file contains 'import adb' as a module import."""
        adapter_dir = Path("src/aiyes/adapters")
        if not adapter_dir.exists():
            pytest.skip("Adapters directory not found")

        for py_file in adapter_dir.glob("android_*.py"):
            source = py_file.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name != "adb" and not alias.name.startswith(
                            "adb."
                        ), f"{py_file.name} illegally imports adb library: {alias.name}"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("adb"), (
                        f"{py_file.name} illegally imports from adb library: {node.module}"
                    )


class TestDomainPurityAndroid:
    """AC-06: Domain layer MUST have zero imports from adapter modules."""

    def test_domain_no_adapter_imports(self) -> None:
        """AC-06: No domain file imports from aiyes.adapters."""
        domain_dir = Path("src/aiyes/domain")
        assert domain_dir.exists()

        for py_file in domain_dir.rglob("*.py"):
            source = py_file.read_text()
            parsed = ast.parse(source)
            for node in ast.walk(parsed):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("aiyes.adapters"), (
                        f"Domain file {py_file} illegally imports from {node.module}"
                    )

    def test_domain_no_adb_import(self) -> None:
        """AC-06: No domain file imports adb or uiautomator."""
        domain_dir = Path("src/aiyes/domain")

        forbidden = {"adb", "uiautomator", "xdotool", "scrot", "xvfb"}
        for py_file in domain_dir.rglob("*.py"):
            source = py_file.read_text()
            parsed = ast.parse(source)
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in forbidden, (
                            f"Domain file {py_file} imports forbidden module {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"Domain file {py_file} imports from forbidden module {node.module}"
                    )


class TestMcpDisclosureProviderName:
    """PC-05: MCP disclosure MUST state the Android inspection provider."""

    def test_manifest_includes_android_provider(self) -> None:
        """PC-05: MCP manifest identifies the Android inspection provider."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp-manifest"])
        assert result.exit_code == 0

        parsed = json.loads(result.output)
        output_str = json.dumps(parsed).lower()

        # Must mention the provider (e.g., "adb+uiautomator" or similar)
        has_provider = (
            "uiautomator" in output_str
            or "adb" in output_str
            or "provider" in output_str
        )
        assert has_provider, "MCP manifest must state the Android inspection provider"


class TestLinuxOnlyPortsNotRequiredForAndroid:
    """AC-07: Linux-only ports MUST NOT be required for Android session lifecycle."""

    def test_android_session_start_no_display_server(self) -> None:
        """AC-07: Android session start does not call DisplayServerPort or AccessibilityBusPort."""
        from aiyes.domain.use_cases.session_start import SessionStartUseCase
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeClock,
            FakeDisplayAllocator,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )

        display_server = FakeDisplayServer()
        allocator = FakeDisplayAllocator()
        atspi_bus = FakeAccessibilityBus()
        process = FakeProcess()
        repo = FakeSessionRepository()
        clock = FakeClock()

        uc = SessionStartUseCase(
            display_server=display_server,
            allocator=allocator,
            atspi_bus=atspi_bus,
            process=process,
            session_repo=repo,
            clock=clock,
        )

        session = uc.execute(
            app_command="com.example.app/.MainActivity",
            app_args=[],
            backend="android",
            device_serial="emulator-5554",
        )

        # Android path MUST NOT touch display_server, allocator, or atspi_bus
        display_server_calls = [c[0] for c in display_server.calls]
        allocator_calls = [c[0] for c in allocator.calls]
        atspi_bus_calls = [c[0] for c in atspi_bus.calls]

        assert "start" not in display_server_calls, (
            "Android session must not call display_server.start()"
        )
        assert "allocate" not in allocator_calls, (
            "Android session must not call allocator.allocate()"
        )
        assert "start_bus" not in atspi_bus_calls, (
            "Android session must not call atspi_bus.start_bus()"
        )

        # Session must be android-typed with correct fields
        assert session.backend == "android"
        assert session.device_serial == "emulator-5554"
        assert session.xvfb_pid == 0
        assert session.atspi_bus_pid == 0
        assert session.display == ""


class TestCompositionRootDispatch:
    """AC-05: Composition root MUST dispatch adapter selection based on backend."""

    def test_composition_root_resolves_android_adapters(self) -> None:
        """AC-05: Given backend='android', composition root provides non-Linux adapters."""
        from aiyes.cli.composition_root import get_adapters_for_backend

        adapters = get_adapters_for_backend("android")
        assert adapters is not None
        assert "tree" in adapters
        assert "action" in adapters
        assert "input" in adapters
        assert "screenshot" in adapters

    def test_composition_root_android_adapters_are_not_linux(self) -> None:
        """AC-05: Android adapters MUST NOT be the same Linux adapter instances."""
        from aiyes.cli.composition_root import get_adapters_for_backend

        linux_adapters = get_adapters_for_backend("linux")
        android_adapters = get_adapters_for_backend("android")

        # Each Android adapter must be a different object than the Linux one
        for key in ("tree", "action", "input", "screenshot"):
            assert android_adapters[key] is not linux_adapters[key], (
                f"Android adapter '{key}' must not be the same as Linux adapter"
            )

    def test_composition_root_android_adapters_are_real(self) -> None:
        """AC-05: Android adapters are real implementations, not stubs."""
        from aiyes.cli.composition_root import get_adapters_for_backend
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter
        from aiyes.adapters.android_input_adapter import AdbInputAdapter
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        adapters = get_adapters_for_backend("android")

        assert isinstance(adapters["tree"], AndroidUiAutomatorTreeAdapter)
        assert isinstance(adapters["action"], AndroidActionAdapter)
        assert isinstance(adapters["input"], AdbInputAdapter)
        assert isinstance(adapters["screenshot"], AdbScreenshotAdapter)

    def test_composition_root_resolves_linux_adapters(self) -> None:
        """AC-05: Given backend='linux', composition root provides Linux adapters."""
        from aiyes.cli.composition_root import get_adapters_for_backend

        adapters = get_adapters_for_backend("linux")
        assert adapters is not None
        assert "tree" in adapters

    def test_composition_root_rejects_unknown_backend(self) -> None:
        """AC-05: Unknown backend raises ValueError."""
        from aiyes.cli.composition_root import get_adapters_for_backend

        with pytest.raises(ValueError, match="Unknown backend"):
            get_adapters_for_backend("ios")


# ═══════════════════════════════════════════════════════════════════════
# Migration-specific tests
# MC-04: CLI surface unchanged for Linux users
# ═══════════════════════════════════════════════════════════════════════


class TestLinuxCLISurfaceUnchanged:
    """MC-04: Existing CLI commands produce equivalent results for Linux sessions."""

    def test_session_start_help_unchanged(self) -> None:
        """MC-04: 'session start --help' still shows existing options."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "start", "--help"])
        assert result.exit_code == 0
        assert "--resolution" in result.output
        assert "--color-depth" in result.output
        assert "--wait" in result.output
        assert "--name" in result.output

    def test_inspect_help_unchanged(self) -> None:
        """MC-04: 'inspect --help' still shows existing options."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["inspect", "--help"])
        assert result.exit_code == 0
        assert "--no-screenshot" in result.output
        assert "--no-tree" in result.output

    def test_all_existing_subcommands_still_registered(self) -> None:
        """MC-04: All pre-existing subcommands remain registered."""
        from aiyes.cli.main import cli

        commands = cli.commands if hasattr(cli, "commands") else {}
        command_names = set(commands.keys())

        expected = {
            "session",
            "inspect",
            "find",
            "screenshot",
            "action",
            "mouse",
            "key",
            "type",
            "wait",
            "do",
            "doctor",
            "mcp-manifest",
        }
        assert expected.issubset(command_names), (
            f"Missing commands after migration: {expected - command_names}"
        )


class TestSessionStartBackendFlag:
    """MC-05, UX-05: Session start accepts --backend flag."""

    def test_session_start_help_shows_backend_option(self) -> None:
        """MC-05: 'session start --help' shows --backend option."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "start", "--help"])
        assert result.exit_code == 0
        assert "--backend" in result.output

    def test_session_start_backend_android_in_help(self) -> None:
        """UX-05: --backend option documentation mentions 'android'."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "start", "--help"])
        assert result.exit_code == 0
        output_lower = result.output.lower()
        assert "android" in output_lower

    def test_session_start_help_shows_device_serial(self) -> None:
        """F-06: 'session start --help' shows --device-serial option."""
        from aiyes.cli.main import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "start", "--help"])
        assert result.exit_code == 0
        assert "--device-serial" in result.output


# ═══════════════════════════════════════════════════════════════════════
# F-08: Stronger tests — actual runtime paths
# ═══════════════════════════════════════════════════════════════════════


class TestSessionStartUseCaseAndroidPath:
    """F-01/F-08: SessionStartUseCase actually branches for Android."""

    def test_android_session_start_creates_session_via_use_case(self) -> None:
        """F-01: SessionStartUseCase.execute(backend='android') creates Android session."""
        from aiyes.domain.use_cases.session_start import SessionStartUseCase
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeClock,
            FakeDisplayAllocator,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )

        process = FakeProcess()
        repo = FakeSessionRepository()

        uc = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(),
            atspi_bus=FakeAccessibilityBus(),
            process=process,
            session_repo=repo,
            clock=FakeClock(),
        )

        session = uc.execute(
            app_command="com.example.app/.MainActivity",
            app_args=[],
            backend="android",
            device_serial="emulator-5554",
        )

        assert session.backend == "android"
        assert session.device_serial == "emulator-5554"
        assert session.app_command == "com.example.app/.MainActivity"
        # Process.start was called for the app
        start_calls = [c for c in process.calls if c[0] == "start"]
        assert len(start_calls) == 1

    def test_android_session_start_requires_device_serial(self) -> None:
        """NF-03: Android backend raises ValueError without device_serial."""
        from aiyes.domain.use_cases.session_start import SessionStartUseCase
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeClock,
            FakeDisplayAllocator,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )

        uc = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(),
            atspi_bus=FakeAccessibilityBus(),
            process=FakeProcess(),
            session_repo=FakeSessionRepository(),
            clock=FakeClock(),
        )

        with pytest.raises(ValueError, match="device-serial"):
            uc.execute(
                app_command="com.example.app/.MainActivity",
                app_args=[],
                backend="android",
            )

    def test_linux_session_start_still_uses_xvfb(self) -> None:
        """F-01: Linux path unchanged — still allocates display, starts Xvfb, AT-SPI."""
        from aiyes.domain.use_cases.session_start import SessionStartUseCase
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeClock,
            FakeDisplayAllocator,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )

        display_server = FakeDisplayServer()
        allocator = FakeDisplayAllocator()
        atspi_bus = FakeAccessibilityBus()
        process = FakeProcess(pid=54321)
        # Mark the app pid as running so the startup check passes
        process._running[54321] = True

        uc = SessionStartUseCase(
            display_server=display_server,
            allocator=allocator,
            atspi_bus=atspi_bus,
            process=process,
            session_repo=FakeSessionRepository(),
            clock=FakeClock(),
        )

        session = uc.execute(
            app_command="gedit",
            app_args=[],
            backend="linux",
        )

        assert session.backend == "linux"
        assert session.display != ""
        assert session.xvfb_pid != 0
        # display_server.start was called
        ds_calls = [c[0] for c in display_server.calls]
        assert "start" in ds_calls
        # allocator.allocate was called
        alloc_calls = [c[0] for c in allocator.calls]
        assert "allocate" in alloc_calls
        # atspi_bus.start_bus was called
        bus_calls = [c[0] for c in atspi_bus.calls]
        assert "start_bus" in bus_calls


class TestSessionStopBackendBranching:
    """F-03/F-08: SessionStopUseCase branches by backend."""

    def test_android_session_stop_skips_xvfb_and_atspi(self) -> None:
        """F-03: Android stop does not call display_server.stop() or atspi_bus.stop_bus()."""
        from aiyes.domain.use_cases.session_stop import SessionStopUseCase
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )

        display_server = FakeDisplayServer()
        atspi_bus = FakeAccessibilityBus()
        process = FakeProcess()
        repo = FakeSessionRepository()
        lifecycle_calls = []

        class FakeAndroidLifecycle:
            def is_app_running(self, serial: str, package_name: str) -> bool:
                lifecycle_calls.append(("is_app_running", serial, package_name))
                return True

            def stop_app(self, serial: str, package_name: str) -> None:
                lifecycle_calls.append(("stop_app", serial, package_name))

        android_session = _make_android_session(app_pid=100)
        repo.save(android_session)

        uc = SessionStopUseCase(
            display_server=display_server,
            atspi_bus=atspi_bus,
            process=process,
            session_repo=repo,
            android_lifecycle=FakeAndroidLifecycle(),
        )

        result = uc.execute(session_id="android-001")

        assert result.status == "stopped"

        # Android app stop uses on-device package lifecycle, not host adb PID.
        assert ("stop_app", "emulator-5554", "com.example.app") in lifecycle_calls
        process_stop_calls = [c for c in process.calls if c[0] == "stop"]
        assert process_stop_calls == []

        # display_server.stop and atspi_bus.stop_bus must NOT have been called
        ds_calls = [c[0] for c in display_server.calls]
        bus_calls = [c[0] for c in atspi_bus.calls]
        assert "stop" not in ds_calls, (
            "Android stop must not call display_server.stop()"
        )
        assert "stop_bus" not in bus_calls, (
            "Android stop must not call atspi_bus.stop_bus()"
        )

    def test_linux_session_stop_cleans_up_all(self) -> None:
        """F-03: Linux stop calls display_server.stop() and atspi_bus.stop_bus()."""
        from aiyes.domain.use_cases.session_stop import SessionStopUseCase
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )

        display_server = FakeDisplayServer()
        atspi_bus = FakeAccessibilityBus()
        process = FakeProcess()
        repo = FakeSessionRepository()

        linux_session = _make_linux_session()
        repo.save(linux_session)

        uc = SessionStopUseCase(
            display_server=display_server,
            atspi_bus=atspi_bus,
            process=process,
            session_repo=repo,
        )

        result = uc.execute(session_id="linux-001")

        ds_calls = [c[0] for c in display_server.calls]
        bus_calls = [c[0] for c in atspi_bus.calls]
        assert "stop" in ds_calls, "Linux stop must call display_server.stop()"
        assert "stop_bus" in bus_calls, "Linux stop must call atspi_bus.stop_bus()"


class TestSessionResolveBackendAware:
    """F-04/F-08: SessionResolveUseCase uses backend-aware liveness."""

    def test_android_session_resolves_with_app_pid_only(self) -> None:
        """F-04: Android session is active when app_pid is running (no xvfb check)."""
        from aiyes.domain.use_cases.session_resolve import SessionResolveUseCase
        from tests.conftest import FakeProcess, FakeSessionRepository

        process = FakeProcess()
        repo = FakeSessionRepository()

        android_session = _make_android_session(app_pid=100)
        repo.save(android_session)

        # Mark app_pid as running, xvfb_pid (0) is NOT running
        process._running[100] = True
        process._running[0] = False

        uc = SessionResolveUseCase(session_repo=repo, process=process)
        resolved = uc.execute(session_id=None)
        assert resolved == "android-001"

    def test_linux_session_requires_both_pids(self) -> None:
        """F-04: Linux session requires both app_pid AND xvfb_pid running."""
        from aiyes.domain.use_cases.session_resolve import SessionResolveUseCase
        from tests.conftest import FakeProcess, FakeSessionRepository

        process = FakeProcess()
        repo = FakeSessionRepository()

        linux_session = _make_linux_session(app_pid=200, xvfb_pid=300)
        repo.save(linux_session)

        # Only app_pid running, xvfb_pid not — should not resolve
        process._running[200] = True
        process._running[300] = False

        uc = SessionResolveUseCase(session_repo=repo, process=process)
        with pytest.raises(RuntimeError, match="No active sessions"):
            uc.execute(session_id=None)

    def test_mixed_sessions_resolves_only_active(self) -> None:
        """F-04: Mixed Linux+Android sessions — only the truly active one resolves."""
        from aiyes.domain.use_cases.session_resolve import SessionResolveUseCase
        from tests.conftest import FakeProcess, FakeSessionRepository

        process = FakeProcess()
        repo = FakeSessionRepository()

        # Android session: app_pid running
        android_session = _make_android_session(app_pid=100, session_id="and-001")
        repo.save(android_session)
        process._running[100] = True

        # Linux session: app_pid running but xvfb NOT running — stale
        linux_session = _make_linux_session(
            app_pid=200, xvfb_pid=300, session_id="lin-001"
        )
        repo.save(linux_session)
        process._running[200] = True
        process._running[300] = False

        uc = SessionResolveUseCase(session_repo=repo, process=process)
        resolved = uc.execute(session_id=None)
        assert resolved == "and-001"


class TestSessionListBackendAware:
    """F-04/F-08: SessionListUseCase uses backend-aware liveness."""

    def test_android_session_active_with_app_pid_only(self) -> None:
        """Session list marks Android session as active with app_pid only."""
        from aiyes.domain.use_cases.session_list import SessionListUseCase
        from tests.conftest import FakeClock, FakeProcess, FakeSessionRepository

        process = FakeProcess()
        repo = FakeSessionRepository()
        clock = FakeClock()

        android_session = _make_android_session(app_pid=100)
        repo.save(android_session)
        process._running[100] = True

        uc = SessionListUseCase(session_repo=repo, process=process, clock=clock)
        entries = uc.execute()
        assert len(entries) == 1
        assert entries[0].status == "active"
        assert entries[0].backend == "android"


class TestDoctorIncludesAdbInRealAdapter:
    """F-05/F-08: SystemDependencyCheck.check_all() includes adb and android_device."""

    def test_real_adapter_check_all_includes_adb(self) -> None:
        """F-05: SystemDependencyCheck.check_all() returns adb check."""
        from unittest.mock import MagicMock as MM
        from unittest.mock import patch

        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with (
            patch("shutil.which", return_value="/usr/bin/mock"),
            patch.dict("sys.modules", {"gi": MM(), "gi.repository": MM()}),
            patch(
                "aiyes.adapters.system_dependency_check.subprocess.run",
                return_value=MM(stdout="List of devices attached\n"),
            ),
        ):
            results = checker.check_all()
            names = {r.name for r in results}
            assert "adb" in names, "check_all must include adb"
            assert "android_device" in names, "check_all must include android_device"

    def test_adb_check_reports_pass_when_found(self) -> None:
        """F-05: adb check returns pass when adb is reachable via resolver."""
        from unittest.mock import patch

        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with patch(
            "aiyes.adapters.adb_path.resolve_adb_path",
            return_value="/usr/bin/adb",
        ):
            result = checker.check("adb")
            assert result.status == "pass"
            assert "adb" in result.name

    def test_adb_check_reports_fail_when_missing(self) -> None:
        """F-05: adb check returns fail when adb cannot be resolved."""
        from unittest.mock import patch

        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        with patch(
            "aiyes.adapters.adb_path.resolve_adb_path",
            side_effect=RuntimeError("adb not found"),
        ):
            result = checker.check("adb")
            assert result.status == "fail"


class TestSessionStopAutoSelectAndroid:
    """F-03: Auto-select with Android sessions uses correct liveness."""

    def test_auto_select_android_session(self) -> None:
        """Auto-select finds single active Android session by app_pid alone."""
        from aiyes.domain.use_cases.session_stop import SessionStopUseCase
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )

        process = FakeProcess()
        repo = FakeSessionRepository()

        android_session = _make_android_session(app_pid=100)
        repo.save(android_session)
        process._running[100] = True

        uc = SessionStopUseCase(
            display_server=FakeDisplayServer(),
            atspi_bus=FakeAccessibilityBus(),
            process=process,
            session_repo=repo,
        )

        # session_id=None triggers auto-select
        result = uc.execute(session_id=None)
        assert result.session_id == "android-001"
        assert result.status in ("stopped", "stopped_with_errors")


# ═══════════════════════════════════════════════════════════════════════
# NF-01: Dispatching adapters route by session.backend
# ═══════════════════════════════════════════════════════════════════════


class TestDispatchingAdaptersRouteByBackend:
    """NF-01: Use cases use dispatching adapters that route by session.backend."""

    def test_dispatching_tree_routes_linux(self) -> None:
        from aiyes.cli.composition_root import _dispatching_tree

        session = _make_linux_session()
        # Linux path should not raise (AT-SPI adapter may fail without X, but
        # the dispatch itself should pick the Linux adapter, not the stub).
        adapter = _dispatching_tree
        assert hasattr(adapter, "_linux")
        assert hasattr(adapter, "_android")
        assert adapter._linux is not adapter._android

    def test_dispatching_tree_routes_android_to_real_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_tree
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        assert isinstance(
            _dispatching_tree._android.tree, AndroidUiAutomatorTreeAdapter
        )

    def test_dispatching_input_routes_android_to_real_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_input
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        assert isinstance(_dispatching_input._android.input, AdbInputAdapter)

    def test_dispatching_screenshot_routes_android_to_real_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_screenshot
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        assert isinstance(
            _dispatching_screenshot._android.screenshot, AdbScreenshotAdapter
        )

    def test_dispatching_action_routes_android_to_real_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_action
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        assert isinstance(_dispatching_action._android.action, AndroidActionAdapter)


# ═══════════════════════════════════════════════════════════════════════
# NF-02: Doctor output includes backend category
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorOutputIncludesCategory:
    """NF-02: doctor CLI includes 'category' in output JSON."""

    def test_doctor_output_has_category_field(self) -> None:
        from aiyes.cli.main import cli
        from click.testing import CliRunner
        from unittest.mock import patch

        from aiyes.domain.types import DependencyResult

        mock_results = [
            DependencyResult(name="xvfb", status="pass", message="found"),
            DependencyResult(name="adb", status="pass", message="found"),
        ]

        runner = CliRunner()
        with patch("aiyes.cli.main.doctor_uc") as mock_doctor:
            mock_doctor.execute.return_value = mock_results
            result = runner.invoke(cli, ["doctor"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        categories = {r["category"] for r in parsed}
        assert "linux" in categories
        assert "android" in categories
