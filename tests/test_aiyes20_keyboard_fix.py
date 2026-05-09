"""AIYES-20 RED tests — Fix keyboard events not reaching winit-based apps.

Bug 1 (R-AIYES20-01): Xvfb starts without XKB keyboard extension and no
    keyboard layout is configured. Must add +extension XKEYBOARD to Xvfb args
    and run setxkbmap after Xvfb starts but before app launch.

Bug 2 (R-AIYES20-02): xdotool sends key events without ensuring window focus.
    Must resolve app window ID from session.app_pid and use --window targeting.

These tests are RED against the current (buggy) code.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch


from aiyes.domain.session import Session


# ===================================================================
# Helpers
# ===================================================================


def _make_linux_session(**overrides: Any) -> Session:
    """Construct a Linux session for testing."""
    defaults: Dict[str, Any] = dict(
        session_id="test-session",
        app_pid=12345,
        app_command="/usr/bin/myapp",
        app_args=(),
        name=None,
        display=":99",
        atspi_bus_pid=100,
        atspi_bus_address="unix:abstract=/tmp/test-bus",
        xvfb_pid=200,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
        backend="linux",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_android_session(**overrides: Any) -> Session:
    """Construct an Android session for testing."""
    defaults: Dict[str, Any] = dict(
        session_id="android-test",
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


# ===================================================================
# R-AIYES20-01: XKB keyboard extension in Xvfb sessions
# ===================================================================


class TestXvfbXkbExtension:
    """XvfbAdapter.start() must include +extension XKEYBOARD in command args."""

    def test_xvfb_start_includes_xkeyboard_extension(self) -> None:
        """Xvfb startup command must include +extension XKEYBOARD."""
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()

        mock_process = MagicMock()
        mock_process.pid = 999

        with patch(
            "aiyes.adapters.xvfb_adapter.subprocess.Popen",
            return_value=mock_process,
        ) as mock_popen:
            adapter.start(display_num=99, resolution="1280x800", color_depth=24)

        cmd = mock_popen.call_args[0][0]

        # Must contain +extension XKEYBOARD (in addition to existing RANDR)
        # Find all extensions in the command
        extensions = []
        for i, arg in enumerate(cmd):
            if arg == "+extension" and i + 1 < len(cmd):
                extensions.append(cmd[i + 1])

        assert "XKEYBOARD" in extensions, (
            f"Xvfb command must include +extension XKEYBOARD. "
            f"Found extensions: {extensions}. Full command: {cmd}"
        )

    def test_xvfb_start_still_includes_randr(self) -> None:
        """XKEYBOARD must supplement RANDR, not replace it."""
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()

        mock_process = MagicMock()
        mock_process.pid = 999

        with patch(
            "aiyes.adapters.xvfb_adapter.subprocess.Popen",
            return_value=mock_process,
        ) as mock_popen:
            adapter.start(display_num=99, resolution="1280x800", color_depth=24)

        cmd = mock_popen.call_args[0][0]

        extensions = []
        for i, arg in enumerate(cmd):
            if arg == "+extension" and i + 1 < len(cmd):
                extensions.append(cmd[i + 1])

        assert "RANDR" in extensions, (
            f"Xvfb command must still include +extension RANDR. "
            f"Found extensions: {extensions}. Full command: {cmd}"
        )
        assert "XKEYBOARD" in extensions, (
            f"Xvfb command must also include +extension XKEYBOARD. "
            f"Found extensions: {extensions}. Full command: {cmd}"
        )


class TestSetxkbmapInSessionStart:
    """Session start must run setxkbmap after Xvfb starts but before app launch."""

    def _make_mocks(self):
        """Create mock ports for SessionStartUseCase."""
        from aiyes.ports.display import DisplayServerPort
        from aiyes.ports.display_allocator import DisplayAllocatorPort
        from aiyes.ports.accessibility import AccessibilityBusPort
        from aiyes.ports.process import ProcessPort
        from aiyes.ports.storage import SessionRepositoryPort
        from aiyes.ports.clock import ClockPort

        display_server = MagicMock(spec=DisplayServerPort)
        display_server.start.return_value = 200  # xvfb_pid

        allocator = MagicMock(spec=DisplayAllocatorPort)
        allocator.allocate.return_value = 99

        atspi_bus = MagicMock(spec=AccessibilityBusPort)
        bus_result = MagicMock()
        bus_result.pid = 100
        bus_result.bus_address = "unix:abstract=/tmp/test-bus"
        atspi_bus.start_bus.return_value = bus_result

        process = MagicMock(spec=ProcessPort)
        process.start.return_value = 12345
        process.is_running.return_value = True

        session_repo = MagicMock(spec=SessionRepositoryPort)
        clock = MagicMock(spec=ClockPort)
        clock.now.return_value = 1000.0

        return display_server, allocator, atspi_bus, process, session_repo, clock

    def test_configure_keyboard_called_during_linux_session_start(self) -> None:
        """Session start for linux must call display_server.configure_keyboard."""
        from aiyes.domain.use_cases.session_start import SessionStartUseCase

        mocks = self._make_mocks()
        display_server = mocks[0]
        use_case = SessionStartUseCase(*mocks)

        use_case.execute(
            app_command="/usr/bin/myapp",
            app_args=["--arg1"],
            backend="linux",
        )

        display_server.configure_keyboard.assert_called_once_with(":99")

    def test_setxkbmap_uses_correct_display(self) -> None:
        """XvfbAdapter.configure_keyboard runs setxkbmap with correct DISPLAY."""
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()

        with patch(
            "aiyes.adapters.xvfb_adapter.subprocess.run",
        ) as mock_run:
            adapter.configure_keyboard(":99")

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        cmd = call_kwargs[0][0]
        env = call_kwargs[1].get("env", {})
        assert cmd == ["setxkbmap", "us"]
        assert env.get("DISPLAY") == ":99"

    def test_setxkbmap_skipped_for_android_sessions(self) -> None:
        """Android session start must NOT call configure_keyboard."""
        from aiyes.domain.use_cases.session_start import SessionStartUseCase

        mocks = self._make_mocks()
        display_server = mocks[0]
        use_case = SessionStartUseCase(*mocks)

        use_case.execute(
            app_command="com.example.app/.MainActivity",
            app_args=[],
            backend="android",
            device_serial="emulator-5554",
        )

        display_server.configure_keyboard.assert_not_called()


# ===================================================================
# R-AIYES20-02: Window-targeted key events via xdotool
# ===================================================================


class TestXdotoolWindowTargetedKeys:
    """xdotool key() must resolve and target the app window via session.app_pid."""

    def test_key_resolves_window_from_app_pid(self) -> None:
        """key() must run xdotool search --onlyvisible --pid <app_pid> to find window."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session(app_pid=12345)

        all_calls: List[Any] = []

        def capture_run(cmd, **kwargs):
            all_calls.append({"cmd": cmd, "kwargs": kwargs})
            result = MagicMock()
            result.returncode = 0
            result.stdout = "67108864\n"  # window ID
            return result

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
            side_effect=capture_run,
        ):
            adapter.key(session, ["Return"])

        # There must be a search call that includes --pid and the app_pid
        search_calls = [c for c in all_calls if "search" in c["cmd"]]

        assert len(search_calls) > 0, (
            f"key() must call xdotool search to find app window. "
            f"All subprocess calls: {[c['cmd'] for c in all_calls]}"
        )

        search_cmd = search_calls[0]["cmd"]
        assert "--pid" in search_cmd, (
            f"xdotool search must use --pid flag. Got: {search_cmd}"
        )
        assert "12345" in [str(a) for a in search_cmd], (
            f"xdotool search must include app_pid 12345. Got: {search_cmd}"
        )

    def test_key_uses_window_flag_with_resolved_id(self) -> None:
        """key() must pass --window <id> to xdotool key command."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session(app_pid=12345)

        call_index = [0]

        def capture_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            # First call is search, return window ID
            if call_index[0] == 0 and "search" in cmd:
                result.stdout = "67108864\n"
            call_index[0] += 1
            return result

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
            side_effect=capture_run,
        ) as mock_run:
            adapter.key(session, ["Return"])

        # Find the key command (not the search command)
        key_calls = [c for c in mock_run.call_args_list if "key" in c[0][0]]

        assert len(key_calls) > 0, (
            f"key() must call xdotool key. "
            f"All calls: {[c[0][0] for c in mock_run.call_args_list]}"
        )

        key_cmd = key_calls[0][0][0]
        assert "--window" in key_cmd, (
            f"xdotool key must use --window flag. Got: {key_cmd}"
        )
        assert "67108864" in key_cmd, (
            f"xdotool key must use resolved window ID 67108864. Got: {key_cmd}"
        )

    def test_key_focuses_window_before_sending(self) -> None:
        """key() must call windowfocus before sending key events."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session(app_pid=12345)

        all_cmds: List[Any] = []

        def capture_run(cmd, **kwargs):
            all_cmds.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "67108864\n"
            return result

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
            side_effect=capture_run,
        ):
            adapter.key(session, ["Return"])

        # windowfocus must appear before the key command
        focus_calls = [c for c in all_cmds if "windowfocus" in c]
        assert len(focus_calls) > 0, (
            f"key() must call windowfocus before key events. All commands: {all_cmds}"
        )
        focus_cmd = focus_calls[0]
        assert "67108864" in focus_cmd, (
            f"windowfocus must target the resolved window ID. Got: {focus_cmd}"
        )

    def test_key_falls_back_on_search_failure(self) -> None:
        """If window search fails, key() must fall back to bare xdotool key."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter
        import subprocess

        adapter = XdotoolAdapter()
        session = _make_linux_session(app_pid=12345)

        call_index = [0]

        def capture_run(cmd, **kwargs):
            result = MagicMock()
            # Search call fails
            if call_index[0] == 0 and "search" in cmd:
                call_index[0] += 1
                raise subprocess.CalledProcessError(1, cmd)
            result.returncode = 0
            call_index[0] += 1
            return result

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
            side_effect=capture_run,
        ) as mock_run:
            # Must not raise — graceful degradation
            adapter.key(session, ["Return"])

        # The key command must still have been sent (without --window)
        key_calls = [c for c in mock_run.call_args_list if "key" in c[0][0]]

        assert len(key_calls) > 0, (
            f"key() must still send key event on search failure (graceful degradation). "
            f"All calls: {[c[0][0] for c in mock_run.call_args_list]}"
        )

    def test_key_falls_back_on_empty_search_result(self) -> None:
        """If window search returns empty, key() must fall back to bare xdotool key."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session(app_pid=12345)

        call_index = [0]

        def capture_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            # Search returns empty
            if call_index[0] == 0 and "search" in cmd:
                result.stdout = ""
            call_index[0] += 1
            return result

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
            side_effect=capture_run,
        ) as mock_run:
            adapter.key(session, ["Return"])

        # Key command must have been sent
        key_calls = [c for c in mock_run.call_args_list if "key" in c[0][0]]

        assert len(key_calls) > 0, (
            f"key() must still send key event when search returns empty. "
            f"All calls: {[c[0][0] for c in mock_run.call_args_list]}"
        )


class TestMouseMethodsUnchanged:
    """mouse_click, mouse_move, type_text must NOT use window targeting."""

    def test_mouse_click_no_window_targeting(self) -> None:
        """mouse_click must NOT use --window or xdotool search."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session()

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
        ) as mock_run:
            adapter.mouse_click(session, x=100, y=200)

        cmd = mock_run.call_args[0][0]
        assert "--window" not in cmd, (
            f"mouse_click must NOT use --window targeting. Got: {cmd}"
        )
        assert "search" not in cmd, (
            f"mouse_click must NOT use xdotool search. Got: {cmd}"
        )

    def test_mouse_move_no_window_targeting(self) -> None:
        """mouse_move must NOT use --window or xdotool search."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session()

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
        ) as mock_run:
            adapter.mouse_move(session, x=100, y=200)

        cmd = mock_run.call_args[0][0]
        assert "--window" not in cmd, (
            f"mouse_move must NOT use --window targeting. Got: {cmd}"
        )

    def test_type_text_no_window_targeting(self) -> None:
        """type_text must NOT use --window or xdotool search."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        session = _make_linux_session()

        with patch(
            "aiyes.adapters.xdotool_adapter.subprocess.run",
        ) as mock_run:
            adapter.type_text(session, "hello")

        cmd = mock_run.call_args[0][0]
        assert "--window" not in cmd, (
            f"type_text must NOT use --window targeting. Got: {cmd}"
        )
