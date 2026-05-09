"""Tests for AIYES-14: Session Resize Command.

Requirements covered:
  R-TDDV6-01.1: Port/Adapter — DisplayServerPort.resize(), XvfbAdapter RANDR
  R-TDDV6-01.2: SessionResizeUseCase — resize orchestration
  R-TDDV6-01.3: CLI — session resize subcommand

Acceptance criteria:
  AC-01: XvfbAdapter.start() includes +extension RANDR
  AC-02: resize() calls xrandr with correct display and resolution
  AC-03: resize() raises RuntimeError on failure
  AC-04: SessionResizeUseCase happy path — resize called with correct args
  AC-05: After resize, session resolution updated in repository
  AC-06: Settle delay default 0.5s via clock.sleep
  AC-07: Session not found raises RuntimeError
  AC-08: Resize failure preserves session state
  AC-09: ResizeResult has status="ok" and resolution
  AC-10: No ProcessPort injected, no process restart
  AC-11: Custom settle delay passed through
  AC-12: CLI session resize --help shows options
  AC-13: CLI successful resize returns JSON
  AC-14: CLI resize error returns exit 1
  AC-15: resize listed in session subgroup help
  AC-16: CLI --settle option passed to use case
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from aiyes.domain.session import Session

from tests.conftest import (
    FakeClock,
    FakeDisplayServer,
    FakeSessionRepository,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_session(
    session_id: str = "test-resize-001",
    display: str = ":99",
    resolution: str = "1280x800",
    app_pid: int = 12345,
    xvfb_pid: int = 12344,
) -> Session:
    """Create a Session value object for resize tests."""
    return Session(
        session_id=session_id,
        display=display,
        app_pid=app_pid,
        app_command="gedit",
        app_args=(),
        atspi_bus_pid=12346,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=xvfb_pid,
        name=None,
        resolution=resolution,
        color_depth=24,
    )


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-01.1: Port/Adapter — DisplayServerPort + XvfbAdapter
# ══════════════════════════════════════════════════════════════════════


class TestDisplayServerPortResize:
    """TEST-01/TEST-02: DisplayServerPort has resize method."""

    def test_display_server_port_has_resize_method(self) -> None:
        """ARCH-01: DisplayServerPort Protocol defines resize(display, resolution)."""
        source = Path("src/aiyes/ports/display.py").read_text()
        parsed = ast.parse(source)

        method_names = []
        for node in ast.walk(parsed):
            if isinstance(node, ast.FunctionDef):
                method_names.append(node.name)

        assert "resize" in method_names, "DisplayServerPort must have a resize() method"

    def test_display_server_port_resize_signature(self) -> None:
        """IMPL-01: resize(self, display: str, resolution: str) -> None."""
        source = Path("src/aiyes/ports/display.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.FunctionDef) and node.name == "resize":
                arg_names = [a.arg for a in node.args.args]
                assert arg_names == ["self", "display", "resolution"], (
                    f"resize() args must be (self, display, resolution), got {arg_names}"
                )
                return

        pytest.fail("resize() method not found in DisplayServerPort")

    def test_display_server_port_still_has_start_and_stop(self) -> None:
        """ARCH-01: start() and stop() signatures unchanged."""
        source = Path("src/aiyes/ports/display.py").read_text()
        parsed = ast.parse(source)

        method_names = []
        for node in ast.walk(parsed):
            if isinstance(node, ast.FunctionDef):
                method_names.append(node.name)

        assert "start" in method_names, "start() must still exist"
        assert "stop" in method_names, "stop() must still exist"


class TestFakeDisplayServerResize:
    """IMPL-07: FakeDisplayServer has resize method for test doubles."""

    def test_fake_display_server_has_resize(self) -> None:
        """FakeDisplayServer must have resize() method."""
        fake = FakeDisplayServer()
        assert hasattr(fake, "resize"), "FakeDisplayServer must have a resize() method"

    def test_fake_display_server_resize_records_call(self) -> None:
        """IMPL-07: resize() records calls as (resize, (display, resolution))."""
        fake = FakeDisplayServer()
        fake.resize(":99", "800x600")
        assert ("resize", (":99", "800x600")) in fake.calls

    def test_fake_display_server_resize_fail_mode(self) -> None:
        """IMPL-07: fail_resize=True makes resize() raise RuntimeError."""
        fake = FakeDisplayServer(fail_resize=True)
        with pytest.raises(RuntimeError):
            fake.resize(":99", "800x600")


class TestXvfbAdapterRandr:
    """TEST-01: XvfbAdapter.start() includes +extension RANDR."""

    def test_start_includes_randr_extension(self) -> None:
        """AC-01/IMPL-02: Xvfb command must include +extension RANDR."""
        source = Path("src/aiyes/adapters/xvfb_adapter.py").read_text()
        # The command list must contain both "+extension" and "RANDR"
        assert '"+extension"' in source or "'+extension'" in source, (
            "XvfbAdapter.start() must include '+extension' in Xvfb command"
        )
        assert '"RANDR"' in source or "'RANDR'" in source, (
            "XvfbAdapter.start() must include 'RANDR' in Xvfb command"
        )


class TestXvfbAdapterResize:
    """TEST-02/TEST-03: XvfbAdapter.resize() calls xrandr."""

    def test_xvfb_adapter_has_resize_method(self) -> None:
        """ARCH-02: XvfbAdapter must implement resize()."""
        from aiyes.adapters.xvfb_adapter import XvfbAdapter

        adapter = XvfbAdapter()
        assert hasattr(adapter, "resize"), "XvfbAdapter must have a resize() method"

    def test_resize_method_uses_xrandr_fb(self) -> None:
        """IMPL-03: resize() uses xrandr --fb <WxH>."""
        source = Path("src/aiyes/adapters/xvfb_adapter.py").read_text()
        assert "xrandr" in source, (
            "XvfbAdapter must reference xrandr in its resize implementation"
        )
        assert "--fb" in source, (
            "XvfbAdapter.resize() must use --fb flag for framebuffer resize"
        )

    def test_resize_sets_display_env(self) -> None:
        """ARCH-02: resize() sets DISPLAY in subprocess env."""
        source = Path("src/aiyes/adapters/xvfb_adapter.py").read_text()
        # Must follow the subprocess env pattern
        assert "DISPLAY" in source, (
            "XvfbAdapter.resize() must set DISPLAY in subprocess environment"
        )

    def test_resize_wraps_subprocess_error(self) -> None:
        """IMPL-03: CalledProcessError wrapped to RuntimeError."""
        source = Path("src/aiyes/adapters/xvfb_adapter.py").read_text()
        assert "CalledProcessError" in source or "RuntimeError" in source, (
            "XvfbAdapter.resize() must handle subprocess errors"
        )


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-01.2: SessionResizeUseCase
# ══════════════════════════════════════════════════════════════════════


class TestSessionResizeUseCaseImport:
    """The use case module must exist and be importable."""

    def test_module_importable(self) -> None:
        """ARCH-03: session_resize.py exists in domain/use_cases/."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase  # noqa: F401

    def test_resize_result_importable(self) -> None:
        """ARCH-03: ResizeResult is a frozen dataclass in session_resize.py."""
        from aiyes.domain.use_cases.session_resize import ResizeResult

        result = ResizeResult(status="ok", resolution="800x600")
        assert result.status == "ok"
        assert result.resolution == "800x600"

    def test_resize_result_is_frozen(self) -> None:
        """ARCH-03: ResizeResult is immutable."""
        from aiyes.domain.use_cases.session_resize import ResizeResult

        result = ResizeResult(status="ok", resolution="800x600")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.status = "error"  # type: ignore[misc]


class TestSessionResizeUseCaseHappyPath:
    """TEST-04/TEST-05: Successful resize updates session and returns result."""

    def test_resize_succeeds_returns_ok(self) -> None:
        """AC-04/AC-09: Happy path returns ResizeResult(status=ok, resolution=...)."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session()
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-resize-001",
            resolution="800x600",
        )

        assert result.status == "ok"
        assert result.resolution == "800x600"

    def test_resize_calls_display_server_with_correct_args(self) -> None:
        """AC-04: display_server.resize(session.display, resolution) called."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session(display=":42")
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )
        uc.execute(session_id="test-resize-001", resolution="1920x1080")

        assert ("resize", (":42", "1920x1080")) in fake_display.calls

    def test_resize_updates_session_resolution(self) -> None:
        """AC-05: After resize, session in repo has new resolution."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session(resolution="1280x800")
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )
        uc.execute(session_id="test-resize-001", resolution="800x600")

        loaded = fake_repo.load("test-resize-001")
        assert loaded is not None
        assert loaded.resolution == "800x600"

    def test_resize_preserves_other_session_fields(self) -> None:
        """ARCH-05: Only resolution changes; all other fields preserved."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session(
            session_id="test-resize-001",
            display=":42",
            app_pid=99999,
            xvfb_pid=88888,
        )
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )
        uc.execute(session_id="test-resize-001", resolution="800x600")

        loaded = fake_repo.load("test-resize-001")
        assert loaded is not None
        assert loaded.session_id == "test-resize-001"
        assert loaded.display == ":42"
        assert loaded.app_pid == 99999
        assert loaded.xvfb_pid == 88888
        assert loaded.app_command == "gedit"
        assert loaded.color_depth == 24


class TestSessionResizeUseCaseSessionNotFound:
    """TEST-07: Session not found raises RuntimeError."""

    def test_session_not_found_raises(self) -> None:
        """AC-07: RuntimeError with 'Session not found' in message."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )

        with pytest.raises(RuntimeError, match="Session not found"):
            uc.execute(session_id="nonexistent-session", resolution="800x600")


class TestSessionResizeUseCaseResizeFailure:
    """TEST-03/TEST-08: Resize failure propagates error, preserves session."""

    def test_resize_failure_raises_runtime_error(self) -> None:
        """AC-03/AC-08: display_server.resize() raises -> use case propagates."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer(fail_resize=True)
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session()
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )

        with pytest.raises(RuntimeError):
            uc.execute(session_id="test-resize-001", resolution="800x600")

    def test_resize_failure_preserves_session_state(self) -> None:
        """AC-08: On failure, session resolution unchanged in repo."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer(fail_resize=True)
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session(resolution="1280x800")
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )

        with pytest.raises(RuntimeError):
            uc.execute(session_id="test-resize-001", resolution="800x600")

        loaded = fake_repo.load("test-resize-001")
        assert loaded is not None
        assert loaded.resolution == "1280x800"


class TestSessionResizeUseCaseSettleDelay:
    """TEST-06: Settle delay via clock.sleep."""

    def test_default_settle_delay(self) -> None:
        """AC-06: clock.sleep(0.5) called by default."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session()
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )
        uc.execute(session_id="test-resize-001", resolution="800x600")

        assert 0.5 in fake_clock.sleep_calls

    def test_custom_settle_delay(self) -> None:
        """AC-11: settle=1.0 causes clock.sleep(1.0)."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session()
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )
        uc.execute(
            session_id="test-resize-001",
            resolution="800x600",
            settle=1.0,
        )

        assert 1.0 in fake_clock.sleep_calls


class TestSessionResizeUseCaseNoProcessRestart:
    """TEST-09: No process restart during resize."""

    def test_no_process_port_in_constructor(self) -> None:
        """AC-10/ARCH-03: SessionResizeUseCase takes exactly 3 ports."""
        source = Path("src/aiyes/domain/use_cases/session_resize.py").read_text()
        assert "ProcessPort" not in source, (
            "SessionResizeUseCase must not reference ProcessPort"
        )

    def test_app_pid_unchanged_after_resize(self) -> None:
        """AC-10: app_pid stays the same (no restart)."""
        from aiyes.domain.use_cases.session_resize import SessionResizeUseCase

        fake_display = FakeDisplayServer()
        fake_repo = FakeSessionRepository()
        fake_clock = FakeClock()

        session = _make_session(app_pid=12345, xvfb_pid=12344)
        fake_repo.save(session)

        uc = SessionResizeUseCase(
            display_server=fake_display,
            session_repo=fake_repo,
            clock=fake_clock,
        )
        uc.execute(session_id="test-resize-001", resolution="800x600")

        loaded = fake_repo.load("test-resize-001")
        assert loaded is not None
        assert loaded.app_pid == 12345
        assert loaded.xvfb_pid == 12344


# ══════════════════════════════════════════════════════════════════════
# R-TDDV6-01.3: CLI — session resize subcommand
# ══════════════════════════════════════════════════════════════════════


class TestSessionResizeCLIHelp:
    """TEST-10: CLI session resize command structure."""

    def test_resize_in_session_subgroup(self) -> None:
        """AC-15: 'resize' listed in session subgroup help."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "--help"])
        assert result.exit_code == 0
        assert "resize" in result.output, (
            "'resize' must appear in session subgroup help"
        )

    def test_resize_help_shows_resolution_argument(self) -> None:
        """AC-12: session resize --help shows RESOLUTION argument."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "resize", "--help"])
        assert result.exit_code == 0
        assert "RESOLUTION" in result.output or "resolution" in result.output.lower()

    def test_resize_help_shows_session_option(self) -> None:
        """AC-12: session resize --help shows --session option."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "resize", "--help"])
        assert result.exit_code == 0
        assert "--session" in result.output

    def test_resize_help_shows_settle_option(self) -> None:
        """AC-12: session resize --help shows --settle option."""
        from aiyes.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["session", "resize", "--help"])
        assert result.exit_code == 0
        assert "--settle" in result.output


class TestSessionResizeCLISuccess:
    """TEST-10: CLI successful resize returns JSON."""

    def test_resize_success_outputs_json(self) -> None:
        """AC-13: Successful resize returns JSON with status and resolution."""
        from aiyes.cli.main import cli

        runner = CliRunner()

        mock_result = MagicMock()
        mock_result.status = "ok"
        mock_result.resolution = "800x600"

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.session_resize_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(
                cli, ["session", "resize", "--session", "s1", "800x600"]
            )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "ok"
        assert parsed["resolution"] == "800x600"

    def test_resize_json_has_status_and_resolution_keys(self) -> None:
        """AC-13: JSON output contains exactly status and resolution."""
        from aiyes.cli.main import cli

        runner = CliRunner()

        mock_result = MagicMock()
        mock_result.status = "ok"
        mock_result.resolution = "1920x1080"

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.session_resize_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(
                cli, ["session", "resize", "--session", "s1", "1920x1080"]
            )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "status" in parsed
        assert "resolution" in parsed


class TestSessionResizeCLIError:
    """TEST-10: CLI error handling."""

    def test_resize_error_exits_one(self) -> None:
        """AC-14: RuntimeError -> exit 1, error to stderr."""
        import inspect as _insp

        from aiyes.cli.main import cli
        from click.testing import CliRunner

        _kw = (
            {"mix_stderr": False}
            if "mix_stderr" in _insp.signature(CliRunner.__init__).parameters
            else {}
        )
        runner = CliRunner(**_kw)

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="bad-id"),
            patch("aiyes.cli.main.session_resize_uc") as mock_uc,
        ):
            mock_uc.execute.side_effect = RuntimeError("Session not found: bad-id")
            result = runner.invoke(
                cli, ["session", "resize", "--session", "bad-id", "800x600"]
            )

        assert result.exit_code == 1
        all_output = (result.output or "") + (getattr(result, "stderr", "") or "")
        assert "Session not found" in all_output or "Error" in all_output


class TestSessionResizeCLISettlePassthrough:
    """TEST-10: --settle option passed to use case."""

    def test_settle_option_passed_to_use_case(self) -> None:
        """AC-16: --settle 2.0 results in settle=2.0 in execute call."""
        from aiyes.cli.main import cli

        runner = CliRunner()

        mock_result = MagicMock()
        mock_result.status = "ok"
        mock_result.resolution = "800x600"

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.session_resize_uc") as mock_uc,
        ):
            mock_uc.execute.return_value = mock_result
            runner.invoke(
                cli,
                [
                    "session",
                    "resize",
                    "--session",
                    "s1",
                    "--settle",
                    "2.0",
                    "800x600",
                ],
            )

            mock_uc.execute.assert_called_once()
            call_kwargs = mock_uc.execute.call_args
            # settle must be 2.0
            if call_kwargs.kwargs:
                assert call_kwargs.kwargs.get("settle") == 2.0
            else:
                # Positional args: session_id, resolution, settle
                assert 2.0 in call_kwargs.args


# ══════════════════════════════════════════════════════════════════════
# Architecture + Wiring
# ══════════════════════════════════════════════════════════════════════


class TestSessionResizeArchitecture:
    """Architecture compliance tests."""

    def test_session_resize_use_case_imports_only_domain_and_ports(self) -> None:
        """ARCH-03: session_resize.py imports only from domain + ports, not adapters/cli."""
        import sys

        stdlib_modules = set(sys.stdlib_module_names)

        source = Path("src/aiyes/domain/use_cases/session_resize.py").read_text()
        parsed = ast.parse(source)

        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in stdlib_modules or top == "__future__":
                    continue
                assert node.module.startswith(("aiyes.domain", "aiyes.ports")), (
                    f"session_resize.py imports from disallowed module: {node.module}"
                )


class TestSessionResizeCompositionRoot:
    """ARCH-04: session_resize_uc wired in composition root."""

    def test_session_resize_uc_exists_in_composition_root(self) -> None:
        """ARCH-04: composition_root exports session_resize_uc."""
        from aiyes.cli.composition_root import session_resize_uc  # noqa: F401

    def test_format_session_resize_in_composition_root(self) -> None:
        """ARCH-04: composition_root re-exports format_session_resize."""
        from aiyes.cli.composition_root import format_session_resize  # noqa: F401


class TestSessionResizePresenter:
    """IMPL-06: format_session_resize in presenter."""

    def test_format_session_resize_exists(self) -> None:
        """IMPL-06: presenter has format_session_resize function."""
        from aiyes.cli.presenter import format_session_resize

        assert callable(format_session_resize)

    def test_format_session_resize_output(self) -> None:
        """IMPL-06: format_session_resize returns JSON with status and resolution."""
        from aiyes.cli.presenter import format_session_resize

        output = format_session_resize(status="ok", resolution="800x600")
        parsed = json.loads(output)
        assert parsed["status"] == "ok"
        assert parsed["resolution"] == "800x600"

    def test_format_session_resize_indent(self) -> None:
        """IMPL-06: Output is indented JSON (indent=2)."""
        from aiyes.cli.presenter import format_session_resize

        output = format_session_resize(status="ok", resolution="800x600")
        # indent=2 produces newlines and spaces
        assert "\n" in output
        assert "  " in output
