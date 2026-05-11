"""AIYES-25 Group C — Interaction Capabilities: tests.

Tests for GAP-03 (clipboard), GAP-07 (multi-touch gestures),
GAP-08 (unified navigation), GAP-11 (menu traversal).

Traceability:
  REQ-C01..C10: Clipboard read/write (GAP-03)
  REQ-C11..C20: Multi-touch gestures (GAP-07)
  REQ-C21..C30: Unified navigation (GAP-08)
  REQ-C31..C40: Menu traversal (GAP-11)
  REQ-C41..C50: CLI wiring, MCP dispatch, presenter
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, List, Tuple
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree

from tests.conftest import (
    FakeAccessibilityAction,
    FakeAccessibilityTree,
    FakeClock,
    FakeInput,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
    make_domain_tree,
)


# ═══════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="test-s",
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=(),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_android_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="android-test",
        app_pid=200,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# Fake ports for GAP-03, GAP-07, GAP-11
# ═══════════════════════════════════════════════════════════════════════


class FakeClipboard:
    """Fake for ClipboardPort."""

    def __init__(self, text: str = "") -> None:
        self._text = text
        self.calls: List[Tuple[str, Any]] = []

    def read(self, session: Any) -> str:
        self.calls.append(("read", session))
        return self._text

    def write(self, session: Any, text: str) -> None:
        self.calls.append(("write", (session, text)))
        self._text = text


class FakeGesture:
    """Fake for GesturePort."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self.calls: List[Tuple[str, Any]] = []

    def pinch(self, session: Any, x: int, y: int, scale_factor: float) -> None:
        self.calls.append(("pinch", (session, x, y, scale_factor)))
        if self._fail:
            raise RuntimeError("Gestures not supported on Linux backend")

    def two_finger_scroll(
        self, session: Any, x: int, y: int, direction: str, amount: int = 3
    ) -> None:
        self.calls.append(("two_finger_scroll", (session, x, y, direction, amount)))
        if self._fail:
            raise RuntimeError("Gestures not supported on Linux backend")


# ═══════════════════════════════════════════════════════════════════════
# GAP-03: Clipboard Read/Write
# ═══════════════════════════════════════════════════════════════════════


class TestClipboardPort:
    """REQ-C01: ClipboardPort protocol has read and write methods."""

    def test_clipboard_port_is_protocol(self) -> None:
        """REQ-C01: ClipboardPort is a Protocol class."""
        from aiyes.ports.clipboard import ClipboardPort

        assert hasattr(ClipboardPort, "read")
        assert hasattr(ClipboardPort, "write")

    def test_clipboard_port_read_signature(self) -> None:
        """REQ-C01: ClipboardPort.read takes session, returns str."""
        from aiyes.ports.clipboard import ClipboardPort

        import inspect

        sig = inspect.signature(ClipboardPort.read)
        params = list(sig.parameters.keys())
        assert "session" in params

    def test_clipboard_port_write_signature(self) -> None:
        """REQ-C01: ClipboardPort.write takes session and text."""
        from aiyes.ports.clipboard import ClipboardPort

        import inspect

        sig = inspect.signature(ClipboardPort.write)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "text" in params


class TestClipboardUseCase:
    """REQ-C02..C06: ClipboardUseCase delegates to port."""

    def test_read_returns_clipboard_text(self) -> None:
        """REQ-C02: read delegates to clipboard_port.read and returns text."""
        from aiyes.domain.use_cases.clipboard import ClipboardUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)
        clipboard = FakeClipboard(text="hello world")

        uc = ClipboardUseCase(clipboard_port=clipboard, session_repo=repo)
        result = uc.read(session_id="test-s")

        assert result.text == "hello world"

    def test_write_delegates_to_port(self) -> None:
        """REQ-C03: write delegates to clipboard_port.write."""
        from aiyes.domain.use_cases.clipboard import ClipboardUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)
        clipboard = FakeClipboard()

        uc = ClipboardUseCase(clipboard_port=clipboard, session_repo=repo)
        result = uc.write(session_id="test-s", text="copied text")

        assert result.status == "ok"
        assert clipboard.calls[-1] == ("write", (session, "copied text"))

    def test_read_session_not_found_raises(self) -> None:
        """REQ-C04: read with unknown session raises RuntimeError."""
        from aiyes.domain.use_cases.clipboard import ClipboardUseCase

        repo = FakeSessionRepository()
        clipboard = FakeClipboard()

        uc = ClipboardUseCase(clipboard_port=clipboard, session_repo=repo)
        with pytest.raises(RuntimeError, match="Session not found"):
            uc.read(session_id="nonexistent")

    def test_write_session_not_found_raises(self) -> None:
        """REQ-C05: write with unknown session raises RuntimeError."""
        from aiyes.domain.use_cases.clipboard import ClipboardUseCase

        repo = FakeSessionRepository()
        clipboard = FakeClipboard()

        uc = ClipboardUseCase(clipboard_port=clipboard, session_repo=repo)
        with pytest.raises(RuntimeError, match="Session not found"):
            uc.write(session_id="nonexistent", text="x")

    def test_read_result_is_frozen_dataclass(self) -> None:
        """REQ-C06: ClipboardReadResult is a frozen dataclass."""
        from aiyes.domain.use_cases.clipboard import ClipboardReadResult

        assert dataclasses.is_dataclass(ClipboardReadResult)
        r = ClipboardReadResult(text="abc")
        with pytest.raises(AttributeError):
            r.text = "changed"  # type: ignore[misc]

    def test_write_result_is_frozen_dataclass(self) -> None:
        """REQ-C06: ClipboardWriteResult is a frozen dataclass."""
        from aiyes.domain.use_cases.clipboard import ClipboardWriteResult

        assert dataclasses.is_dataclass(ClipboardWriteResult)
        r = ClipboardWriteResult(status="ok")
        with pytest.raises(AttributeError):
            r.status = "changed"  # type: ignore[misc]


class TestClipboardLinuxAdapter:
    """REQ-C07..C08: Linux clipboard adapter uses xclip."""

    def test_linux_clipboard_read_calls_xclip(self) -> None:
        """REQ-C07: read calls xclip with correct DISPLAY."""
        from aiyes.adapters.xclip_adapter import XclipAdapter

        adapter = XclipAdapter()
        session = _make_session()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="clipboard content", stderr=""
            )
            text = adapter.read(session)

        assert text == "clipboard content"
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "xclip" in cmd
        assert "-selection" in cmd
        assert "clipboard" in cmd

    def test_linux_clipboard_write_calls_xclip(self) -> None:
        """REQ-C08: write pipes text to xclip."""
        from aiyes.adapters.xclip_adapter import XclipAdapter

        adapter = XclipAdapter()
        session = _make_session()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.write(session, "hello")

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "xclip" in cmd
        assert "-selection" in cmd


class TestClipboardAndroidAdapter:
    """REQ-C09..C10: Android clipboard adapter uses adb cmd clipboard."""

    def test_android_clipboard_read_calls_adb(self) -> None:
        """REQ-C09: read uses adb shell cmd clipboard get-text."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="android clip", stderr=""
            )
            text = adapter.read(session)

        assert text == "android clip"

    def test_android_clipboard_write_calls_adb(self) -> None:
        """REQ-C10: write uses adb shell cmd clipboard set-text."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.write(session, "hello android")

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "clipboard" in " ".join(cmd)

    def test_android_clipboard_write_passes_raw_text_to_set_text(self) -> None:
        """AIYES-50/R-004: clipboard write preserves raw clipboard text."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()
        raw_text = "hello world 100% 'single' \"double\" $HOME; echo no | cat\ncafe \u20ac \u2615"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.write(session, raw_text)

        cmd = mock_run.call_args[0][0]
        assert cmd[-4:] == ["cmd", "clipboard", "set-text", raw_text]

    def test_android_clipboard_write_does_not_call_input_text_escaper(self) -> None:
        """AIYES-50/R-004: clipboard transport must not use input-text escaping."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()

        with (
            patch(
                "aiyes.adapters.adb_text.escape_text_for_adb",
                side_effect=AssertionError("input text escaper must not be used"),
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.write(session, "space percent% quote' newline\nunicode \u20ac")

    def test_android_clipboard_write_nonzero_adb_raises_runtime_error(self) -> None:
        """AIYES-50/R-004: adb clipboard failures remain actionable."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=23, stdout="", stderr="clipboard service rejected text"
            )
            with pytest.raises(
                RuntimeError,
                match="adb clipboard failed \\(rc=23\\): clipboard service rejected text",
            ):
                adapter.write(session, "text")


# ═══════════════════════════════════════════════════════════════════════
# GAP-07: Multi-touch Gestures (Android only)
# ═══════════════════════════════════════════════════════════════════════


class TestGesturePort:
    """REQ-C11: GesturePort protocol has pinch and two_finger_scroll."""

    def test_gesture_port_is_protocol(self) -> None:
        """REQ-C11: GesturePort is a Protocol class."""
        from aiyes.ports.gesture import GesturePort

        assert hasattr(GesturePort, "pinch")
        assert hasattr(GesturePort, "two_finger_scroll")


class TestGestureUseCase:
    """REQ-C12..C16: GestureUseCase validates and delegates."""

    def test_pinch_delegates_to_port(self) -> None:
        """REQ-C12: pinch delegates to gesture_port.pinch."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        result = uc.pinch(session_id="android-test", x=500, y=800, scale_factor=2.0)

        assert result.status == "ok"
        assert gesture.calls[-1][0] == "pinch"

    def test_two_finger_scroll_delegates_to_port(self) -> None:
        """REQ-C13: two_finger_scroll delegates to gesture_port."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        result = uc.two_finger_scroll(
            session_id="android-test", x=500, y=800, direction="up", amount=5
        )

        assert result.status == "ok"
        assert gesture.calls[-1][0] == "two_finger_scroll"

    def test_pinch_session_not_found_raises(self) -> None:
        """REQ-C14: pinch with unknown session raises RuntimeError."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        with pytest.raises(RuntimeError, match="Session not found"):
            uc.pinch(session_id="nonexistent", x=0, y=0, scale_factor=1.0)

    def test_pinch_on_linux_backend_raises(self) -> None:
        """REQ-C15: pinch on linux session raises RuntimeError."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_session()  # linux backend
        repo.save(session)
        gesture = FakeGesture(fail=True)

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        with pytest.raises(RuntimeError, match="[Gg]esture.*not supported|[Ll]inux"):
            uc.pinch(session_id="test-s", x=0, y=0, scale_factor=1.0)

    def test_two_finger_scroll_invalid_direction_raises(self) -> None:
        """REQ-C16: two_finger_scroll with invalid direction raises ValueError."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        with pytest.raises(ValueError, match="[Ii]nvalid.*direction"):
            uc.two_finger_scroll(
                session_id="android-test", x=0, y=0, direction="diagonal", amount=3
            )

    def test_pinch_result_is_frozen_dataclass(self) -> None:
        """REQ-C16b: GestureResult is frozen dataclass."""
        from aiyes.domain.use_cases.gesture import GestureResult

        assert dataclasses.is_dataclass(GestureResult)
        r = GestureResult(status="ok")
        with pytest.raises(AttributeError):
            r.status = "changed"  # type: ignore[misc]

    def test_pinch_scale_factor_must_be_positive(self) -> None:
        """REQ-C16c: scale_factor must be positive."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        with pytest.raises(ValueError, match="scale_factor"):
            uc.pinch(session_id="android-test", x=0, y=0, scale_factor=0.0)

    def test_two_finger_scroll_on_linux_raises(self) -> None:
        """REQ-C16d: two_finger_scroll on linux session raises."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_session()  # linux backend
        repo.save(session)
        gesture = FakeGesture(fail=True)

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        with pytest.raises(RuntimeError, match="[Gg]esture.*not supported|[Ll]inux"):
            uc.two_finger_scroll(
                session_id="test-s", x=0, y=0, direction="up", amount=3
            )


class TestGestureAndroidAdapter:
    """REQ-C17..C18: Android gesture adapter uses adb shell input."""

    def test_pinch_zoom_in_uses_adb_swipe(self) -> None:
        """REQ-C17: pinch with scale_factor > 1 runs two concurrent swipes (zoom in)."""
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        adapter = AdbGestureAdapter()
        session = _make_android_session()

        with patch("subprocess.Popen") as mock_popen:
            proc1 = MagicMock()
            proc1.wait.return_value = 0
            proc1.returncode = 0
            proc2 = MagicMock()
            proc2.wait.return_value = 0
            proc2.returncode = 0
            mock_popen.side_effect = [proc1, proc2]

            adapter.pinch(session, x=500, y=800, scale_factor=2.0)

        assert mock_popen.call_count == 2

    def test_two_finger_scroll_uses_adb_swipe(self) -> None:
        """REQ-C18: two_finger_scroll runs two concurrent swipes."""
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        adapter = AdbGestureAdapter()
        session = _make_android_session()

        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.wait.return_value = 0
            proc.returncode = 0
            mock_popen.return_value = proc

            adapter.two_finger_scroll(session, x=500, y=800, direction="up", amount=5)

        assert mock_popen.call_count == 2


class TestGestureLinuxAdapter:
    """REQ-C19: Linux gesture adapter raises RuntimeError."""

    def test_linux_gesture_pinch_raises(self) -> None:
        """REQ-C19: Linux pinch raises RuntimeError."""
        from aiyes.adapters.linux_gesture_adapter import LinuxGestureAdapter

        adapter = LinuxGestureAdapter()
        session = _make_session()

        with pytest.raises(RuntimeError, match="[Gg]esture.*not supported.*Linux"):
            adapter.pinch(session, x=0, y=0, scale_factor=1.0)

    def test_linux_gesture_two_finger_scroll_raises(self) -> None:
        """REQ-C19: Linux two_finger_scroll raises RuntimeError."""
        from aiyes.adapters.linux_gesture_adapter import LinuxGestureAdapter

        adapter = LinuxGestureAdapter()
        session = _make_session()

        with pytest.raises(RuntimeError, match="[Gg]esture.*not supported.*Linux"):
            adapter.two_finger_scroll(session, x=0, y=0, direction="up", amount=3)


# ═══════════════════════════════════════════════════════════════════════
# GAP-08: Unified Navigation
# ═══════════════════════════════════════════════════════════════════════


class TestNavigateUseCase:
    """REQ-C21..C26: NavigateUseCase routes platform-specific key commands."""

    def test_navigate_back_linux_sends_alt_left_with_warning(self) -> None:
        """REQ-C21: navigate back on Linux sends Alt+Left with browser-only warning."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="test-s", action="back")

        assert result.status == "ok"
        # Should have sent key event for alt+Left
        key_calls = [c for c in input_port.calls if c[0] == "key"]
        assert len(key_calls) == 1
        assert "alt+Left" in key_calls[0][1][1]
        # A10-C04: Should include warning about browser-only behavior
        assert result.warning is not None
        assert "browser" in result.warning.lower()

    def test_navigate_back_android_sends_keycode_back(self) -> None:
        """REQ-C22: navigate back on Android sends KEYCODE_BACK (4)."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="android-test", action="back")

        assert result.status == "ok"
        key_calls = [c for c in input_port.calls if c[0] == "key"]
        assert len(key_calls) == 1
        assert "Back" in key_calls[0][1][1]

    def test_navigate_home_android_sends_keycode_home(self) -> None:
        """REQ-C23: navigate home on Android sends Home key."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="android-test", action="home")

        assert result.status == "ok"
        key_calls = [c for c in input_port.calls if c[0] == "key"]
        assert len(key_calls) == 1
        assert "Home" in key_calls[0][1][1]

    def test_navigate_home_linux_returns_warning(self) -> None:
        """REQ-C24: navigate home on Linux returns warning (not applicable)."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="test-s", action="home")

        assert result.status == "ok"
        assert result.warning is not None
        assert (
            "not applicable" in result.warning.lower()
            or "linux" in result.warning.lower()
        )

    def test_navigate_recent_android_sends_app_switch(self) -> None:
        """REQ-C25: navigate recent on Android sends KEYCODE_APP_SWITCH (187)."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="android-test", action="recent")

        assert result.status == "ok"
        key_calls = [c for c in input_port.calls if c[0] == "key"]
        assert len(key_calls) == 1
        assert "187" in key_calls[0][1][1]

    def test_navigate_recent_linux_returns_warning(self) -> None:
        """REQ-C26: navigate recent on Linux returns warning."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="test-s", action="recent")

        assert result.status == "ok"
        assert result.warning is not None

    def test_navigate_invalid_action_raises(self) -> None:
        """REQ-C27: invalid navigation action raises ValueError."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        with pytest.raises(ValueError, match="[Uu]nknown.*action|[Ii]nvalid"):
            uc.execute(session_id="test-s", action="forward")

    def test_navigate_session_not_found_raises(self) -> None:
        """REQ-C28: navigate with unknown session raises RuntimeError."""
        from aiyes.domain.use_cases.navigate import NavigateUseCase

        repo = FakeSessionRepository()
        input_port = FakeInput()

        uc = NavigateUseCase(input_port=input_port, session_repo=repo)
        with pytest.raises(RuntimeError, match="Session not found"):
            uc.execute(session_id="nonexistent", action="back")

    def test_navigate_result_is_frozen_dataclass(self) -> None:
        """REQ-C29: NavigateResult is frozen dataclass."""
        from aiyes.domain.use_cases.navigate import NavigateResult

        assert dataclasses.is_dataclass(NavigateResult)
        r = NavigateResult(status="ok")
        with pytest.raises(AttributeError):
            r.status = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# GAP-11: Menu Traversal (Linux only for v1)
# ═══════════════════════════════════════════════════════════════════════


class TestMenuUseCase:
    """REQ-C31..C38: MenuUseCase traverses menu hierarchy."""

    def _make_menu_tree(self) -> AccessibilityTree:
        """Build a tree with menu_bar > menu > menu_item structure."""
        raw = {
            "tree": [
                make_node(
                    "n_root",
                    "frame",
                    "App Window",
                    children=[
                        make_node(
                            "n_menubar",
                            "menu_bar",
                            "",
                            actions=["click"],
                            children=[
                                make_node(
                                    "n_file",
                                    "menu",
                                    "File",
                                    actions=["click"],
                                    children=[
                                        make_node(
                                            "n_save",
                                            "menu_item",
                                            "Save",
                                            actions=["click"],
                                        ),
                                        make_node(
                                            "n_saveas",
                                            "menu_item",
                                            "Save As",
                                            actions=["click"],
                                        ),
                                    ],
                                ),
                                make_node(
                                    "n_edit",
                                    "menu",
                                    "Edit",
                                    actions=["click"],
                                    children=[
                                        make_node(
                                            "n_prefs",
                                            "menu_item",
                                            "Preferences",
                                            actions=["click"],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        make_node("n_content", "panel", "Content"),
                    ],
                ),
            ]
        }
        return make_domain_tree(raw["tree"])

    def test_menu_single_item(self) -> None:
        """REQ-C31: Menu traversal with 'File.Save' clicks File then Save."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        tree = self._make_menu_tree()
        tree_port = FakeAccessibilityTree(tree)
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        result = uc.execute(session_id="test-s", menu_path="File.Save")

        assert result.status == "ok"
        # Should have clicked File menu, then Save item
        action_calls = [c for c in action_port.calls if c[0] == "do_action"]
        assert len(action_calls) >= 2

    def test_menu_two_segments(self) -> None:
        """REQ-C32: Menu traversal 'Edit.Preferences' works."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        tree = self._make_menu_tree()
        tree_port = FakeAccessibilityTree(tree)
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        result = uc.execute(session_id="test-s", menu_path="Edit.Preferences")

        assert result.status == "ok"

    def test_menu_not_found_raises(self) -> None:
        """REQ-C33: Menu item not found raises RuntimeError."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        tree = self._make_menu_tree()
        tree_port = FakeAccessibilityTree(tree)
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        with pytest.raises(RuntimeError, match="[Nn]ot found|[Mm]enu.*[Nn]onexistent"):
            uc.execute(session_id="test-s", menu_path="Nonexistent.Item")

    def test_menu_empty_path_raises(self) -> None:
        """REQ-C34: Empty menu path raises ValueError."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        tree = self._make_menu_tree()
        tree_port = FakeAccessibilityTree(tree)
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        with pytest.raises(ValueError, match="[Mm]enu.*path.*empty|[Aa]t least"):
            uc.execute(session_id="test-s", menu_path="")

    def test_menu_android_raises(self) -> None:
        """REQ-C35: Menu traversal on Android raises RuntimeError."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)

        tree = self._make_menu_tree()
        tree_port = FakeAccessibilityTree(tree)
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("android-test", tree, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        with pytest.raises(RuntimeError, match="[Mm]enu.*not supported.*Android"):
            uc.execute(session_id="android-test", menu_path="File.Save")

    def test_menu_session_not_found_raises(self) -> None:
        """REQ-C36: Menu with unknown session raises RuntimeError."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        tree_port = FakeAccessibilityTree()
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        with pytest.raises(RuntimeError, match="Session not found"):
            uc.execute(session_id="nonexistent", menu_path="File.Save")

    def test_menu_result_is_frozen_dataclass(self) -> None:
        """REQ-C37: MenuResult is frozen dataclass."""
        from aiyes.domain.use_cases.menu import MenuResult

        assert dataclasses.is_dataclass(MenuResult)
        r = MenuResult(status="ok")
        with pytest.raises(AttributeError):
            r.status = "changed"  # type: ignore[misc]

    def test_menu_single_segment_is_valid(self) -> None:
        """REQ-C38: Single-segment menu path 'File' just opens the menu."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        tree = self._make_menu_tree()
        tree_port = FakeAccessibilityTree(tree)
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        result = uc.execute(session_id="test-s", menu_path="File")

        assert result.status == "ok"
        # Should have clicked File menu at minimum
        action_calls = [c for c in action_port.calls if c[0] == "do_action"]
        assert len(action_calls) >= 1


# ═══════════════════════════════════════════════════════════════════════
# CLI Wiring Tests
# ═══════════════════════════════════════════════════════════════════════


class TestClipboardCli:
    """REQ-C41: CLI clipboard read/write commands."""

    def test_clipboard_read_command_exists(self) -> None:
        """REQ-C41: 'aieyes clipboard read' is registered."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["clipboard", "read", "--help"])
        assert result.exit_code == 0
        assert "clipboard" in result.output.lower() or "read" in result.output.lower()

    def test_clipboard_write_command_exists(self) -> None:
        """REQ-C41: 'aieyes clipboard write' is registered."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["clipboard", "write", "--help"])
        assert result.exit_code == 0


class TestGestureCli:
    """REQ-C42: CLI gesture pinch/two-finger-scroll commands."""

    def test_gesture_pinch_command_exists(self) -> None:
        """REQ-C42: 'aieyes gesture pinch' is registered."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["gesture", "pinch", "--help"])
        assert result.exit_code == 0

    def test_gesture_two_finger_scroll_command_exists(self) -> None:
        """REQ-C42: 'aieyes gesture two-finger-scroll' is registered."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["gesture", "two-finger-scroll", "--help"])
        assert result.exit_code == 0

    def test_gesture_help_discloses_restricted_best_effort_status(self) -> None:
        """AIYES-52 R-009: gesture help must not overclaim Android multi-touch."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["gesture", "--help"])

        assert result.exit_code == 0
        output = result.output.lower()
        assert "restricted/best-effort" in output
        assert "multi-touch gesture commands" not in output


class TestNavigateCli:
    """REQ-C43: CLI navigate command."""

    def test_navigate_command_exists(self) -> None:
        """REQ-C43: 'aieyes navigate' is registered."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["navigate", "--help"])
        assert result.exit_code == 0
        assert "back" in result.output.lower() or "navigate" in result.output.lower()


class TestMenuCli:
    """REQ-C44: CLI menu command."""

    def test_menu_command_exists(self) -> None:
        """REQ-C44: 'aieyes menu' is registered."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["menu", "--help"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# Presenter Tests
# ═══════════════════════════════════════════════════════════════════════


class TestClipboardPresenter:
    """REQ-C45: Clipboard presenter functions."""

    def test_format_clipboard_read(self) -> None:
        """REQ-C45: format_clipboard_read returns JSON with text field."""
        from aiyes.cli.presenter import format_clipboard_read

        result = json.loads(format_clipboard_read("hello"))
        assert result["text"] == "hello"

    def test_format_clipboard_write(self) -> None:
        """REQ-C45: format_clipboard_write returns JSON with status=ok."""
        from aiyes.cli.presenter import format_clipboard_write

        result = json.loads(format_clipboard_write())
        assert result["status"] == "ok"


class TestGesturePresenter:
    """REQ-C46: Gesture presenter function."""

    def test_format_gesture_result(self) -> None:
        """REQ-C46: format_gesture_result returns JSON with status=ok."""
        from aiyes.cli.presenter import format_gesture_result

        result = json.loads(format_gesture_result())
        assert result["status"] == "ok"


class TestNavigatePresenter:
    """REQ-C47: Navigate presenter function."""

    def test_format_navigate_result_no_warning(self) -> None:
        """REQ-C47: format_navigate_result without warning."""
        from aiyes.cli.presenter import format_navigate_result

        result = json.loads(format_navigate_result(status="ok"))
        assert result["status"] == "ok"
        assert "warning" not in result

    def test_format_navigate_result_with_warning(self) -> None:
        """REQ-C47: format_navigate_result with warning."""
        from aiyes.cli.presenter import format_navigate_result

        result = json.loads(
            format_navigate_result(status="ok", warning="Not applicable on Linux")
        )
        assert result["status"] == "ok"
        assert result["warning"] == "Not applicable on Linux"


class TestMenuPresenter:
    """REQ-C48: Menu presenter function."""

    def test_format_menu_result(self) -> None:
        """REQ-C48: format_menu_result returns JSON with status."""
        from aiyes.cli.presenter import format_menu_result

        result = json.loads(format_menu_result(status="ok"))
        assert result["status"] == "ok"

    def test_format_menu_result_with_node_id(self) -> None:
        """REQ-C48: format_menu_result with final node info."""
        from aiyes.cli.presenter import format_menu_result

        result = json.loads(format_menu_result(status="ok", node_id="n_save"))
        assert result["node_id"] == "n_save"


# ═══════════════════════════════════════════════════════════════════════
# Schema & MCP count tests — updated counts
# ═══════════════════════════════════════════════════════════════════════


class TestSchemaCountAfterGroupC:
    """REQ-C49: enumerate_commands count updated for Group C commands."""

    def test_enumerate_commands_count(self) -> None:
        """REQ-C49: Count increases by 7 new commands (clipboard read/write,
        gesture pinch/two-finger-scroll, navigate, menu = 2+2+1+1 = 6 new leaf commands;
        but navigate is a single leaf, and clipboard/gesture are groups with 2 each).
        Previous: 25. New: 25 + 7 = 32.
        clipboard read, clipboard write, gesture pinch, gesture two_finger_scroll,
        navigate, menu = 6. Session capabilities adds one command. Total: 33."""
        from aiyes.cli.schema_gen import enumerate_commands
        from aiyes.cli.main import cli

        result = enumerate_commands(cli)
        tool_names = {ci.tool_name for ci in result}

        # Verify new tool names exist
        assert "clipboard_read" in tool_names
        assert "clipboard_write" in tool_names
        assert "gesture_pinch" in tool_names
        assert "gesture_two_finger_scroll" in tool_names
        assert "navigate" in tool_names
        assert "menu" in tool_names
        assert "session_capabilities" in tool_names
        assert "debug_bundle" in tool_names

        # Scenario agent-usability surfaces add three scenario leaf commands;
        # AIYES-47 adds top-level swipe.
        assert len(result) == 38


class TestMcpDispatchGroupC:
    """REQ-C50: MCP dispatch table includes Group C commands."""

    @pytest.mark.asyncio
    async def test_dispatch_table_has_clipboard_read(self) -> None:
        """REQ-C50: dispatch table has clipboard_read."""
        from aiyes.adapters.mcp_server import create_mcp_server

        deps = self._make_deps()
        server = create_mcp_server(deps)
        tools = await server.list_tools()
        tool_names = {t.name for t in tools}
        assert "clipboard_read" in tool_names
        assert "clipboard_write" in tool_names
        assert "gesture_pinch" in tool_names
        assert "gesture_two_finger_scroll" in tool_names
        assert "navigate" in tool_names
        assert "menu" in tool_names

    @pytest.mark.asyncio
    async def test_mcp_list_tools_count_is_38(self) -> None:
        """REQ-C50: MCP list_tools includes capabilities and swipe."""
        from aiyes.adapters.mcp_server import create_mcp_server

        deps = self._make_deps()
        server = create_mcp_server(deps)
        tools = await server.list_tools()
        assert len(tools) == 38

    def _make_deps(self):
        """Build mock ServerDependencies with all required fields."""
        from aiyes.adapters.mcp_server import ServerDependencies
        import dataclasses as dc

        fields = {f.name: MagicMock() for f in dc.fields(ServerDependencies)}
        return ServerDependencies(**fields)


# ═══════════════════════════════════════════════════════════════════════
# MCP manifest capabilities updated
# ═══════════════════════════════════════════════════════════════════════


class TestMcpManifestGroupC:
    """REQ-C50b: MCP manifest capabilities lists updated."""

    def test_manifest_linux_control_includes_new_commands(self) -> None:
        """REQ-C50b: Linux control list includes clipboard, navigate, menu."""
        from aiyes.cli.main import _build_mcp_manifest

        manifest = _build_mcp_manifest()
        linux_control = manifest["capabilities"]["linux"]["control"]
        assert "clipboard" in linux_control
        assert "navigate" in linux_control
        assert "menu" in linux_control

    def test_manifest_android_control_includes_new_commands(self) -> None:
        """REQ-C50b: Android control list includes clipboard, gesture, navigate."""
        from aiyes.cli.main import _build_mcp_manifest

        manifest = _build_mcp_manifest()
        android_control = manifest["capabilities"]["android"]["control"]
        assert "clipboard" in android_control
        assert "gesture" in android_control
        assert "navigate" in android_control

    def test_manifest_android_gestures_are_restricted_or_best_effort(self) -> None:
        """AIYES-52 R-009: manifest must not claim reliable Android multi-touch."""
        from aiyes.cli.main import _build_mcp_manifest

        manifest = _build_mcp_manifest()
        android = manifest["capabilities"]["android"]

        gesture_state = android.get("gesture")
        assert isinstance(gesture_state, dict), (
            "Android manifest must disclose gesture status explicitly, not only "
            "list 'gesture' as a reliable control capability."
        )
        assert gesture_state.get("status") in {"restricted", "best-effort"}
        text = repr(gesture_state).lower()
        assert "reliable multi-touch" not in text
        assert "multi-pointer smoke" in text


# ═══════════════════════════════════════════════════════════════════════
# Composition root wiring tests
# ═══════════════════════════════════════════════════════════════════════


class TestCompositionRootGroupC:
    """REQ-C51: composition_root.py wires Group C use cases."""

    def test_clipboard_uc_exists(self) -> None:
        """REQ-C51: clipboard_uc is wired in composition_root."""
        from aiyes.cli.composition_root import clipboard_uc

        assert clipboard_uc is not None

    def test_gesture_uc_exists(self) -> None:
        """REQ-C51: gesture_uc is wired in composition_root."""
        from aiyes.cli.composition_root import gesture_uc

        assert gesture_uc is not None

    def test_navigate_uc_exists(self) -> None:
        """REQ-C51: navigate_uc is wired in composition_root."""
        from aiyes.cli.composition_root import navigate_uc

        assert navigate_uc is not None

    def test_menu_uc_exists(self) -> None:
        """REQ-C51: menu_uc is wired in composition_root."""
        from aiyes.cli.composition_root import menu_uc

        assert menu_uc is not None


# ═══════════════════════════════════════════════════════════════════════
# A10 Review Fix Tests — AIYES-25 Group C
# ═══════════════════════════════════════════════════════════════════════


class TestA10C01XclipDoctorCheck:
    """A10-C01: xclip must be in doctor dependency checks."""

    def test_xclip_in_all_deps(self) -> None:
        """A10-C01: xclip is listed in _ALL_DEPS."""
        from aiyes.adapters.system_dependency_check import _ALL_DEPS

        assert "xclip" in _ALL_DEPS

    def test_xclip_in_executable_deps(self) -> None:
        """A10-C01: xclip is in _EXECUTABLE_DEPS with correct executable name."""
        from aiyes.adapters.system_dependency_check import _EXECUTABLE_DEPS

        assert "xclip" in _EXECUTABLE_DEPS
        assert _EXECUTABLE_DEPS["xclip"] == "xclip"

    def test_xclip_check_returns_result(self) -> None:
        """A10-C01: SystemDependencyCheck.check('xclip') returns a DependencyResult."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        result = checker.check("xclip")
        assert result.name == "xclip"
        assert result.status in ("pass", "fail")


class TestA10C02ShellInjectionFix:
    """A10-C02/AIYES-50: adb clipboard set-text uses safe argv transport."""

    def test_write_passes_shell_metacharacters_as_single_argv_element(self) -> None:
        """A10-C02: shell metacharacters stay data, not host-shell syntax."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()
        text = "hello; rm -rf /"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.write(session, text)

        cmd = mock_run.call_args[0][0]
        assert cmd[-4:] == ["cmd", "clipboard", "set-text", text]
        assert mock_run.call_args.kwargs["text"] is True

    def test_write_passes_dollar_sign_as_clipboard_data(self) -> None:
        """A10-C02: dollar signs remain clipboard data in argv transport."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()
        text = "price is $100"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.write(session, text)

        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == text

    def test_write_passes_backtick_as_clipboard_data(self) -> None:
        """A10-C02: backticks remain clipboard data in argv transport."""
        from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter

        adapter = AdbClipboardAdapter()
        session = _make_android_session()
        text = "echo `whoami`"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.write(session, text)

        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == text


class TestA10C03ZombieProcessFix:
    """A10-C03: Popen processes are cleaned up on timeout or failure."""

    def test_pinch_timeout_kills_both_processes(self) -> None:
        """A10-C03: both processes are killed on timeout."""
        import subprocess as sp
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        adapter = AdbGestureAdapter()
        session = _make_android_session()

        with patch("subprocess.Popen") as mock_popen:
            proc1 = MagicMock()
            proc1.wait.side_effect = sp.TimeoutExpired(cmd="adb", timeout=10)
            proc1.returncode = None
            proc2 = MagicMock()
            proc2.wait.return_value = 0
            proc2.returncode = 0
            mock_popen.side_effect = [proc1, proc2]

            with pytest.raises(sp.TimeoutExpired):
                adapter.pinch(session, x=500, y=800, scale_factor=2.0)

        proc1.kill.assert_called_once()
        proc2.kill.assert_called_once()

    def test_two_finger_scroll_timeout_kills_both_processes(self) -> None:
        """A10-C03: both scroll processes are killed on timeout."""
        import subprocess as sp
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        adapter = AdbGestureAdapter()
        session = _make_android_session()

        with patch("subprocess.Popen") as mock_popen:
            proc1 = MagicMock()
            proc1.wait.side_effect = sp.TimeoutExpired(cmd="adb", timeout=10)
            proc1.returncode = None
            proc2 = MagicMock()
            proc2.wait.return_value = 0
            proc2.returncode = 0
            mock_popen.side_effect = [proc1, proc2]

            with pytest.raises(sp.TimeoutExpired):
                adapter.two_finger_scroll(
                    session, x=500, y=800, direction="up", amount=3
                )

        proc1.kill.assert_called_once()
        proc2.kill.assert_called_once()


class TestA10C05AmountValidation:
    """A10-C05: two_finger_scroll amount must be positive."""

    def test_amount_zero_raises(self) -> None:
        """A10-C05: amount=0 raises ValueError."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        with pytest.raises(ValueError, match="amount must be positive"):
            uc.two_finger_scroll(
                session_id="android-test", x=0, y=0, direction="up", amount=0
            )

    def test_amount_negative_raises(self) -> None:
        """A10-C05: amount=-5 raises ValueError."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        with pytest.raises(ValueError, match="amount must be positive"):
            uc.two_finger_scroll(
                session_id="android-test", x=0, y=0, direction="up", amount=-5
            )

    def test_amount_positive_succeeds(self) -> None:
        """A10-C05: amount=1 succeeds (boundary)."""
        from aiyes.domain.use_cases.gesture import GestureUseCase

        repo = FakeSessionRepository()
        session = _make_android_session()
        repo.save(session)
        gesture = FakeGesture()

        uc = GestureUseCase(gesture_port=gesture, session_repo=repo)
        result = uc.two_finger_scroll(
            session_id="android-test", x=0, y=0, direction="up", amount=1
        )
        assert result.status == "ok"


class TestA10C06StatefulMenuTree:
    """A10-C06: Menu traversal re-read actually gets different tree content."""

    def test_menu_traversal_uses_reread_tree(self) -> None:
        """A10-C06: FakeAccessibilityTree that returns different trees on
        successive calls verifies the re-read-after-click pattern matters."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        # First tree: menu bar only, no submenu items
        tree_before_click = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "App Window",
                    children=[
                        make_node(
                            "n_menubar",
                            "menu_bar",
                            "",
                            actions=["click"],
                            children=[
                                make_node(
                                    "n_file",
                                    "menu",
                                    "File",
                                    actions=["click"],
                                    children=[],  # no submenu items yet
                                ),
                            ],
                        ),
                    ],
                ),
            ]
        )

        # Second tree: menu bar with expanded submenu (after click)
        tree_after_click = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "App Window",
                    children=[
                        make_node(
                            "n_menubar",
                            "menu_bar",
                            "",
                            actions=["click"],
                            children=[
                                make_node(
                                    "n_file",
                                    "menu",
                                    "File",
                                    actions=["click"],
                                    children=[
                                        make_node(
                                            "n_save",
                                            "menu_item",
                                            "Save",
                                            actions=["click"],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ]
        )

        # Stateful tree port: returns different trees on successive calls
        call_count = [0]
        trees = [tree_before_click, tree_after_click]

        class StatefulTreePort:
            def get_tree(self, session):
                idx = min(call_count[0], len(trees) - 1)
                call_count[0] += 1
                return trees[idx]

        tree_port = StatefulTreePort()
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree_before_click, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        result = uc.execute(session_id="test-s", menu_path="File.Save")

        assert result.status == "ok"
        assert result.node_name == "Save"
        # Verify re-read was called (tree_port was used)
        assert call_count[0] >= 1


class TestA10C07SubmenuRetry:
    """A10-C07: Menu submenu discovery retries up to 3 times."""

    def test_submenu_retry_succeeds_on_second_attempt(self) -> None:
        """A10-C07: submenu appears on second re-read attempt."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        # Menu bar (initial stored tree and first re-read return this)
        tree_no_submenu = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "App",
                    children=[
                        make_node(
                            "n_file", "menu", "File", actions=["click"], children=[]
                        ),
                    ],
                ),
            ]
        )

        # After submenu appears
        tree_with_submenu = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "App",
                    children=[
                        make_node(
                            "n_file",
                            "menu",
                            "File",
                            actions=["click"],
                            children=[
                                make_node(
                                    "n_save",
                                    "menu_item",
                                    "Save",
                                    actions=["click"],
                                ),
                            ],
                        ),
                    ],
                ),
            ]
        )

        call_count = [0]

        class DelayedTreePort:
            def get_tree(self, session):
                call_count[0] += 1
                # First re-read: still no submenu
                if call_count[0] <= 1:
                    return tree_no_submenu
                # Second and subsequent: submenu appears
                return tree_with_submenu

        tree_port = DelayedTreePort()
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree_no_submenu, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        result = uc.execute(session_id="test-s", menu_path="File.Save")

        assert result.status == "ok"
        # At least 2 re-reads (first no submenu, second has submenu)
        assert call_count[0] >= 2

    def test_submenu_retry_exhausted_raises_with_timing_hint(self) -> None:
        """A10-C07: after 3 failed retries, error mentions timing."""
        from aiyes.domain.use_cases.menu import MenuUseCase

        repo = FakeSessionRepository()
        session = _make_session()
        repo.save(session)

        tree_no_submenu = make_domain_tree(
            [
                make_node(
                    "n_root",
                    "frame",
                    "App",
                    children=[
                        make_node(
                            "n_file", "menu", "File", actions=["click"], children=[]
                        ),
                    ],
                ),
            ]
        )

        class NeverExpandsTreePort:
            def get_tree(self, session):
                return tree_no_submenu

        tree_port = NeverExpandsTreePort()
        action_port = FakeAccessibilityAction()
        tree_store = FakeTreeStore()
        tree_store.save_tree("test-s", tree_no_submenu, {})
        clock = FakeClock()

        uc = MenuUseCase(
            tree_port=tree_port,
            action_port=action_port,
            session_repo=repo,
            tree_store=tree_store,
            clock=clock,
        )
        with pytest.raises(RuntimeError, match="submenu may not have appeared"):
            uc.execute(session_id="test-s", menu_path="File.Save")
