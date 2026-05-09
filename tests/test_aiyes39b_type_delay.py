"""AIYES-39B — inter-character delay for Android text input.

Tests for the delay_ms parameter across the full chain:
  - Port signature: type_text(session, text, delay_ms=0)
  - Use case: execute(session_id, text, delay_ms=0)
  - Android adapter: per-character adb calls with delay when delay_ms > 0
  - Xdotool adapter: passes --delay flag to xdotool type
  - Dispatching adapter: passes delay_ms through
  - CLI: --delay option
  - MCP: delay_ms in args
"""

from __future__ import annotations

from typing import Any, List, Tuple
from unittest.mock import MagicMock, call, patch

from aiyes.domain.session import Session
from aiyes.domain.use_cases.type_text import TypeTextUseCase
from aiyes.ports.input import InputPort


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_android_session(**overrides: Any) -> Session:
    defaults = dict(
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


def _make_linux_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="linux-test",
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=[],
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
    )
    defaults.update(overrides)
    return Session(**defaults)


class RecordingInput:
    """Minimal InputPort fake that records type_text calls with delay_ms."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Any]] = []

    def mouse_move(self, session, x: int, y: int) -> None:
        pass

    def mouse_click(self, session, x=None, y=None, button="left") -> None:
        pass

    def mouse_drag(self, session, x1: int, y1: int, x2: int, y2: int) -> None:
        pass

    def mouse_scroll(self, session, direction: str, amount: int = 3) -> None:
        pass

    def key(self, session, key_specs: List[str]) -> None:
        pass

    def type_text(self, session, text: str, delay_ms: int = 0) -> None:
        self.calls.append(("type_text", (session, text, delay_ms)))


class FakeSessionRepo:
    """Minimal session repo for use case tests."""

    def __init__(self) -> None:
        self._sessions: dict = {}

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session

    def load(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list_all(self) -> list:
        return list(self._sessions.values())

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# ═══════════════════════════════════════════════════════════════════════
# 1. Port signature — delay_ms parameter exists
# ═══════════════════════════════════════════════════════════════════════


class TestInputPortSignature:
    """The InputPort.type_text must accept delay_ms parameter."""

    def test_port_type_text_accepts_delay_ms(self) -> None:
        """InputPort.type_text signature includes delay_ms with default 0."""
        import inspect

        sig = inspect.signature(InputPort.type_text)
        params = list(sig.parameters.keys())
        assert "delay_ms" in params, (
            f"InputPort.type_text must have delay_ms parameter, got: {params}"
        )
        # Check default value is 0
        delay_param = sig.parameters["delay_ms"]
        assert delay_param.default == 0


# ═══════════════════════════════════════════════════════════════════════
# 2. Use case — passes delay_ms through
# ═══════════════════════════════════════════════════════════════════════


class TestTypeTextUseCaseDelay:
    """TypeTextUseCase.execute must accept and pass delay_ms."""

    def test_execute_passes_delay_ms_to_port(self) -> None:
        """Use case forwards delay_ms to the input port."""
        recording = RecordingInput()
        repo = FakeSessionRepo()
        session = _make_linux_session()
        repo.save(session)

        uc = TypeTextUseCase(input_port=recording, session_repo=repo)
        uc.execute(session_id="linux-test", text="hello", delay_ms=20)

        assert len(recording.calls) == 1
        _, (_, text, delay_ms) = recording.calls[0]
        assert text == "hello"
        assert delay_ms == 20

    def test_execute_default_delay_is_zero(self) -> None:
        """Use case defaults delay_ms=0 when not specified."""
        recording = RecordingInput()
        repo = FakeSessionRepo()
        session = _make_linux_session()
        repo.save(session)

        uc = TypeTextUseCase(input_port=recording, session_repo=repo)
        uc.execute(session_id="linux-test", text="hello")

        assert len(recording.calls) == 1
        _, (_, text, delay_ms) = recording.calls[0]
        assert delay_ms == 0


# ═══════════════════════════════════════════════════════════════════════
# 3. Android adapter — per-character with delay
# ═══════════════════════════════════════════════════════════════════════


class TestAdbInputAdapterDelay:
    """AdbInputAdapter.type_text with delay_ms sends per-character adb calls."""

    def test_delay_ms_zero_uses_default_per_character(self) -> None:
        """delay_ms=0 uses Android default (20ms) per-character mode."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                with patch(
                    "aiyes.adapters.android_input_adapter.time.sleep"
                ) as mock_sleep:
                    adapter.type_text(session, "hello", delay_ms=0)

        # Per-character mode: 5 adb calls for 5 characters
        assert mock_run.call_count == 5
        # Sleep with 20ms default between characters (not after last)
        assert mock_sleep.call_count == 4

    def test_delay_ms_positive_sends_per_character(self) -> None:
        """delay_ms > 0 sends one adb call per character."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                with patch(
                    "aiyes.adapters.android_input_adapter.time.sleep"
                ) as mock_sleep:
                    adapter.type_text(session, "Hi!", delay_ms=20)

        # 3 characters = 3 adb calls
        assert mock_run.call_count == 3

        # Each call sends one character (escaped)
        from aiyes.adapters.adb_text import escape_text_for_adb

        for i, char in enumerate("Hi!"):
            cmd = mock_run.call_args_list[i][0][0]
            escaped = escape_text_for_adb(char)
            assert cmd == [
                "adb",
                "-s",
                "emulator-5554",
                "shell",
                "input",
                "text",
                escaped,
            ]

    def test_delay_ms_positive_sleeps_between_characters(self) -> None:
        """delay_ms > 0 calls time.sleep(delay_ms/1000) between characters."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ):
                with patch(
                    "aiyes.adapters.android_input_adapter.time.sleep"
                ) as mock_sleep:
                    adapter.type_text(session, "abc", delay_ms=25)

        # Sleep between characters: 2 sleeps for 3 characters (not after last)
        assert mock_sleep.call_count == 2
        for c in mock_sleep.call_args_list:
            assert c == call(0.025)

    def test_delay_ms_single_char_no_sleep(self) -> None:
        """Single character with delay_ms > 0: one adb call, no sleep."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                with patch(
                    "aiyes.adapters.android_input_adapter.time.sleep"
                ) as mock_sleep:
                    adapter.type_text(session, "x", delay_ms=20)

        assert mock_run.call_count == 1
        assert mock_sleep.call_count == 0

    def test_delay_ms_empty_string_is_noop(self) -> None:
        """Empty string with delay_ms is still a no-op."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        with patch("aiyes.adapters.android_input_adapter.subprocess.run") as mock_run:
            adapter.type_text(session, "", delay_ms=20)

        mock_run.assert_not_called()

    def test_delay_ms_with_special_characters(self) -> None:
        """Special characters are properly escaped per-character."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter
        from aiyes.adapters.adb_text import escape_text_for_adb

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                with patch("aiyes.adapters.android_input_adapter.time.sleep"):
                    adapter.type_text(session, "a b", delay_ms=15)

        # 3 chars: 'a', ' ', 'b'
        assert mock_run.call_count == 3
        # Space is escaped as %s
        space_cmd = mock_run.call_args_list[1][0][0]
        assert space_cmd[-1] == "%s"


# ═══════════════════════════════════════════════════════════════════════
# 4. Xdotool adapter — uses --delay flag
# ═══════════════════════════════════════════════════════════════════════


class TestXdotoolAdapterDelay:
    """XdotoolAdapter.type_text with delay_ms passes --delay to xdotool."""

    def test_delay_ms_zero_no_delay_flag(self) -> None:
        """delay_ms=0 does not add --delay to xdotool command."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.type_text(":99", "hello", delay_ms=0)

            cmd = mock_run.call_args[0][0]
            assert "--delay" not in cmd

    def test_delay_ms_positive_adds_delay_flag(self) -> None:
        """delay_ms > 0 adds --delay <ms> to xdotool command."""
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.type_text(":99", "hello", delay_ms=20)

            cmd = mock_run.call_args[0][0]
            assert "--delay" in cmd
            delay_idx = cmd.index("--delay")
            assert cmd[delay_idx + 1] == "20"


# ═══════════════════════════════════════════════════════════════════════
# 5. Dispatching adapter — passes delay_ms through
# ═══════════════════════════════════════════════════════════════════════


class TestDispatchingInputDelay:
    """_DispatchingInput.type_text passes delay_ms to both backends."""

    def test_dispatching_passes_delay_to_android(self) -> None:
        """Dispatching input passes delay_ms to android adapter."""
        from aiyes.cli.composition_root import _DispatchingInput

        mock_linux = MagicMock()
        mock_android = MagicMock()

        dispatcher = _DispatchingInput(mock_linux, mock_android)
        session = _make_android_session()

        dispatcher.type_text(session, "hello", delay_ms=20)

        mock_android.input.type_text.assert_called_once_with(
            session, "hello", delay_ms=20
        )

    def test_dispatching_passes_delay_to_linux(self) -> None:
        """Dispatching input passes delay_ms to linux adapter."""
        from aiyes.cli.composition_root import _DispatchingInput

        mock_linux = MagicMock()
        mock_android = MagicMock()

        dispatcher = _DispatchingInput(mock_linux, mock_android)
        session = _make_linux_session()

        dispatcher.type_text(session, "hello", delay_ms=15)

        mock_linux.type_text.assert_called_once_with(session, "hello", delay_ms=15)

    def test_dispatching_default_delay_zero(self) -> None:
        """Dispatching input defaults delay_ms=0."""
        from aiyes.cli.composition_root import _DispatchingInput

        mock_linux = MagicMock()
        mock_android = MagicMock()

        dispatcher = _DispatchingInput(mock_linux, mock_android)
        session = _make_linux_session()

        dispatcher.type_text(session, "hello")

        mock_linux.type_text.assert_called_once_with(session, "hello", delay_ms=0)


# ═══════════════════════════════════════════════════════════════════════
# 6. CLI --delay option
# ═══════════════════════════════════════════════════════════════════════


class TestCliDelayOption:
    """CLI type command accepts --delay option."""

    def test_cli_type_with_delay(self) -> None:
        """--delay <ms> is passed to type_text_uc.execute."""
        from click.testing import CliRunner
        from aiyes.cli.main import cli

        runner = CliRunner()

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.type_text_uc") as mock_uc,
            patch("aiyes.cli.main._log_operation"),
        ):
            mock_uc.execute.return_value = MagicMock(status="ok")
            result = runner.invoke(cli, ["type", "--delay", "20", "hello"])

        assert result.exit_code == 0, result.output
        mock_uc.execute.assert_called_once_with("s1", "hello", delay_ms=20)

    def test_cli_type_default_delay_zero(self) -> None:
        """Without --delay, delay_ms=0 is passed to use case."""
        from click.testing import CliRunner
        from aiyes.cli.main import cli

        runner = CliRunner()

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.type_text_uc") as mock_uc,
            patch("aiyes.cli.main._log_operation"),
        ):
            mock_uc.execute.return_value = MagicMock(status="ok")
            result = runner.invoke(cli, ["type", "hello"])

        assert result.exit_code == 0, result.output
        mock_uc.execute.assert_called_once_with("s1", "hello", delay_ms=0)


# ═══════════════════════════════════════════════════════════════════════
# 7. MCP handler — passes delay_ms
# ═══════════════════════════════════════════════════════════════════════


class TestMcpTypeDelay:
    """MCP type handler passes delay_ms to use case."""

    def test_mcp_type_with_delay(self) -> None:
        """MCP _handle_type passes delay_ms from args."""
        from aiyes.adapters.mcp_server import _build_dispatch_table

        mock_deps = MagicMock()
        mock_deps.type_text_uc.execute.return_value = MagicMock(status="ok")

        handlers = _build_dispatch_table(mock_deps)
        handler = handlers["type"]

        # Call the handler's use_case_call directly
        handler.use_case_call(
            {"text": "hello", "delay_ms": 20},
            mock_deps,
            "s1",
        )

        mock_deps.type_text_uc.execute.assert_called_once_with(
            "s1", "hello", delay_ms=20
        )

    def test_mcp_type_default_delay_zero(self) -> None:
        """MCP _handle_type defaults delay_ms=0 when not in args."""
        from aiyes.adapters.mcp_server import _build_dispatch_table

        mock_deps = MagicMock()
        mock_deps.type_text_uc.execute.return_value = MagicMock(status="ok")

        handlers = _build_dispatch_table(mock_deps)
        handler = handlers["type"]

        handler.use_case_call(
            {"text": "hello"},
            mock_deps,
            "s1",
        )

        mock_deps.type_text_uc.execute.assert_called_once_with(
            "s1", "hello", delay_ms=0
        )
