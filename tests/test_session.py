"""Tests for session lifecycle: start, stop, list.

Requirements covered:
  R-SESSION-01: session start (launch Xvfb, AT-SPI2 bus, app; return JSON)
  R-SESSION-02: session start options (resolution, color-depth, wait, name)
  R-SESSION-03: session stop (kill processes, return JSON, preserve state dir)
  R-SESSION-04: session list (JSON array of all sessions)
  R-SESSION-05: accessibility env vars (GTK_MODULES, QT_ACCESSIBILITY, etc.)
  R-SESSION-06: zombie/stale session detection
  R-ARCH-03:    session state on disk (~/.aieyes/<session-id>/)
"""

from __future__ import annotations

from typing import Any, List

import pytest

# These imports will fail (RED) — they define the expected public API.
from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_start import SessionStartUseCase
from aiyes.domain.use_cases.session_stop import SessionStopUseCase
from aiyes.domain.use_cases.session_list import SessionListUseCase

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)


# ──────────────────────────────────────────────────────────────────────
# R-SESSION-01: Session value object
# ──────────────────────────────────────────────────────────────────────


class TestSessionValueObject:
    """Session value object holds session state fields."""

    def test_session_has_required_fields(self) -> None:
        """R-SESSION-01, R-ARCH-03: Session carries all required fields."""
        session = Session(
            session_id="abc-123",
            display=":99",
            app_pid=1000,
            app_command="gedit",
            app_args=[],
            atspi_bus_pid=1001,
            atspi_bus_address="unix:abstract=/tmp/dbus-abc",
            xvfb_pid=999,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        assert session.session_id == "abc-123"
        assert session.display == ":99"
        assert session.app_pid == 1000
        assert session.app_command == "gedit"
        assert session.atspi_bus_pid == 1001
        assert session.atspi_bus_address == "unix:abstract=/tmp/dbus-abc"
        assert session.xvfb_pid == 999
        assert session.resolution == "1280x800"
        assert session.color_depth == 24

    def test_session_has_atspi_bus_address(self) -> None:
        """R-SESSION-01, PC-05: atspi_bus_address is a required field."""
        session = Session(
            session_id="x",
            display=":1",
            app_pid=1,
            app_command="app",
            app_args=[],
            atspi_bus_pid=2,
            atspi_bus_address="unix:abstract=/tmp/dbus-x",
            xvfb_pid=3,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        assert hasattr(session, "atspi_bus_address")
        assert isinstance(session.atspi_bus_address, str)
        assert len(session.atspi_bus_address) > 0

    def test_session_is_immutable(self) -> None:
        """R-SESSION-01: Session should be an immutable value object."""
        session = Session(
            session_id="x",
            display=":1",
            app_pid=1,
            app_command="app",
            app_args=[],
            atspi_bus_pid=2,
            atspi_bus_address="unix:abstract=/tmp/dbus-x",
            xvfb_pid=3,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        with pytest.raises((AttributeError, TypeError)):
            session.session_id = "changed"  # type: ignore[misc]

    def test_session_has_started_at(self) -> None:
        """R-SESSION-04: Session carries started_at for uptime computation."""
        session = Session(
            session_id="x",
            display=":1",
            app_pid=1,
            app_command="app",
            app_args=[],
            atspi_bus_pid=2,
            atspi_bus_address="unix:abstract=/tmp/dbus-x",
            xvfb_pid=3,
            name=None,
            resolution="1280x800",
            color_depth=24,
            started_at=1000.0,
        )
        assert session.started_at == 1000.0

    def test_session_app_args_is_tuple(self) -> None:
        """F-18: Session.app_args is a tuple, not a mutable list."""
        session = Session(
            session_id="x",
            display=":1",
            app_pid=1,
            app_command="app",
            app_args=["--flag", "value"],
            atspi_bus_pid=2,
            atspi_bus_address="unix:abstract=/tmp/dbus-x",
            xvfb_pid=3,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        assert isinstance(session.app_args, tuple)
        assert session.app_args == ("--flag", "value")
        with pytest.raises((TypeError, AttributeError)):
            session.app_args.append("new")  # type: ignore[attr-error]


# ──────────────────────────────────────────────────────────────────────
# R-SESSION-01: Session start use case
# ──────────────────────────────────────────────────────────────────────


class TestSessionStart:
    """Use case: start a new session."""

    def test_start_returns_session(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-01: Start returns a Session with all required fields."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="gedit", app_args=[])

        assert hasattr(result, "session_id")
        assert hasattr(result, "display")
        assert hasattr(result, "app_pid")
        assert hasattr(result, "atspi_bus_address")
        assert result.display.startswith(":")

    def test_start_launches_xvfb(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-01: Start launches an Xvfb process."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_display_server.calls if c[0] == "start"]
        assert len(start_calls) == 1

    def test_start_launches_atspi_bus(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-01: Start launches the AT-SPI2 bus."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=[])

        bus_calls = [c for c in fake_accessibility_bus.calls if c[0] == "start_bus"]
        assert len(bus_calls) == 1

    def test_start_launches_target_app(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-01: Start launches the target application."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=["test.txt"])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        assert len(start_calls) == 1
        cmd, args, env = start_calls[0][1]
        assert cmd == "gedit"
        assert args == ["test.txt"]

    def test_start_saves_session_to_repo(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-ARCH-03: Start persists session state via SessionRepositoryPort."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="gedit", app_args=[])

        save_calls = [c for c in fake_session_repo.calls if c[0] == "save"]
        assert len(save_calls) == 1
        saved_session = save_calls[0][1]
        assert saved_session.session_id == result.session_id

    def test_start_records_started_at(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-04: Start records started_at from ClockPort for uptime."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="app", app_args=[])

        assert result.started_at > 0


# ──────────────────────────────────────────────────────────────────────
# R-SESSION-02: Session start options
# ──────────────────────────────────────────────────────────────────────


class TestSessionStartOptions:
    """Session start with custom options."""

    def test_custom_resolution(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-02: --resolution sets Xvfb geometry."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="app", app_args=[], resolution="1920x1080")

        start_calls = [c for c in fake_display_server.calls if c[0] == "start"]
        _, resolution, _ = start_calls[0][1]
        assert resolution == "1920x1080"
        assert result.resolution == "1920x1080"

    def test_default_resolution(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-02: Default resolution is 1280x800."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="app", app_args=[])

        assert result.resolution == "1280x800"

    def test_custom_color_depth(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-02: --color-depth sets Xvfb color depth."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="app", app_args=[], color_depth=16)

        start_calls = [c for c in fake_display_server.calls if c[0] == "start"]
        _, _, depth = start_calls[0][1]
        assert depth == 16
        assert result.color_depth == 16

    def test_session_name(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-02: --name stores a human-readable label."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="app", app_args=[], name="my-session")

        assert result.name == "my-session"

    def test_wait_sleeps_after_app_launch(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-02, F-12: --wait causes sleep after app launch."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="app", app_args=[], wait=5.0)

        sleep_calls = [c for c in fake_clock.calls if c[0] == "sleep"]
        assert len(sleep_calls) >= 1
        total_sleep = sum(c[1] for c in sleep_calls)
        assert total_sleep == 5.0

    def test_wait_zero_does_not_sleep(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-02: wait=0 does not sleep."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="app", app_args=[], wait=0)

        sleep_calls = [c for c in fake_clock.calls if c[0] == "sleep"]
        assert len(sleep_calls) == 0

    def test_start_rejects_app_that_exits_during_wait(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """Session start must fail if the launched app dies before startup completes."""

        class EarlyExitProcess(FakeProcess):
            def start(self, command: str, args: List[str], env: Any = None) -> int:
                pid = super().start(command, args, env)
                self._running[pid] = False
                return pid

        fake_process = EarlyExitProcess()

        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )

        with pytest.raises(RuntimeError, match="exited during startup"):
            uc.execute(app_command="app", app_args=[], wait=2.0)

        bus_stop_calls = [c for c in fake_accessibility_bus.calls if c[0] == "stop_bus"]
        assert len(bus_stop_calls) == 1
        xvfb_stop_calls = [c for c in fake_display_server.calls if c[0] == "stop"]
        assert len(xvfb_stop_calls) == 1
        assert fake_session_repo.load_all() == []

    def test_default_wait_is_two_seconds(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-02: Default wait is 2.0 seconds."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="app", app_args=[])

        sleep_calls = [c for c in fake_clock.calls if c[0] == "sleep"]
        assert len(sleep_calls) == 1
        assert sleep_calls[0][1] == 2.0


# ──────────────────────────────────────────────────────────────────────
# R-SESSION-05: Accessibility environment variables
# ──────────────────────────────────────────────────────────────────────


class TestSessionAccessibilityEnv:
    """Target app must get accessibility env vars."""

    def test_gtk_modules_set(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-05: GTK_MODULES=gail:atk-bridge is set."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        assert env.get("GTK_MODULES") == "gail:atk-bridge"

    def test_qt_accessibility_set(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-05: QT_ACCESSIBILITY=1 is set."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        assert env.get("QT_ACCESSIBILITY") == "1"
        assert env.get("QT_LINUX_ACCESSIBILITY_ALWAYS_ON") == "1"

    def test_display_env_set(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-01, R-SEC-01: DISPLAY env var set to Xvfb display."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        assert env.get("DISPLAY") == result.display


# ──────────────────────────────────────────────────────────────────────
# R-SESSION-03: Session stop
# ──────────────────────────────────────────────────────────────────────


class TestSessionStop:
    """Use case: stop a session."""

    def test_stop_returns_result(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03: Stop returns {status: stopped, session_id}."""
        # Pre-populate a session
        from aiyes.domain.session import Session

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
        fake_session_repo.save(session)

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id="s1")

        assert result.status == "stopped"
        assert result.session_id == "s1"

    def test_stop_kills_app(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03: Stop terminates the application process."""
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
        fake_session_repo.save(session)

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        uc.execute(session_id="s1")

        stop_calls = [c for c in fake_process.calls if c[0] == "stop"]
        stopped_pids = [c[1] for c in stop_calls]
        assert 100 in stopped_pids

    def test_stop_kills_xvfb(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03: Stop terminates the Xvfb process."""
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
        fake_session_repo.save(session)

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        uc.execute(session_id="s1")

        stop_calls = [c for c in fake_display_server.calls if c[0] == "stop"]
        assert len(stop_calls) >= 1

    def test_stop_kills_atspi_bus(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03: Stop terminates the AT-SPI2 bus."""
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
        fake_session_repo.save(session)

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        uc.execute(session_id="s1")

        bus_stop_calls = [c for c in fake_accessibility_bus.calls if c[0] == "stop_bus"]
        assert len(bus_stop_calls) >= 1

    def test_stop_preserves_state_dir(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03: Session state directory is preserved on disk after stop."""
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
        fake_session_repo.save(session)

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        uc.execute(session_id="s1")

        # Session repo should NOT have had delete called
        delete_calls = [c for c in fake_session_repo.calls if c[0] == "delete"]
        assert len(delete_calls) == 0

    def test_stop_no_session_raises(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03: Stop with non-existent session raises a system error."""
        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        with pytest.raises(Exception):
            uc.execute(session_id="nonexistent")

    def test_stop_auto_select_single_active_session(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03, OQ-12: When one active session, stop it without --session."""
        session = Session(
            session_id="only-one",
            display=":99",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-only",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_session_repo.save(session)
        # Mark processes as running so session is active
        fake_process._running[100] = True
        fake_process._running[99] = True

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id=None)

        assert result.session_id == "only-one"
        assert result.status == "stopped"

    def test_stop_no_session_id_no_sessions_raises(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03, OQ-12: No session_id and zero sessions raises error."""
        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        with pytest.raises(Exception):
            uc.execute(session_id=None)

    def test_stop_no_session_id_multiple_active_sessions_raises(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-03, OQ-12: No session_id + multiple active sessions raises error."""
        for sid in ["s1", "s2"]:
            session = Session(
                session_id=sid,
                display=":99",
                app_pid=100 if sid == "s1" else 200,
                app_command="app",
                app_args=[],
                atspi_bus_pid=101,
                atspi_bus_address=f"unix:abstract=/tmp/dbus-{sid}",
                xvfb_pid=99 if sid == "s1" else 199,
                name=None,
                resolution="1280x800",
                color_depth=24,
            )
            fake_session_repo.save(session)
            # Mark both as running
            fake_process._running[session.app_pid] = True
            fake_process._running[session.xvfb_pid] = True

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        with pytest.raises(Exception):
            uc.execute(session_id=None)

    def test_stop_auto_select_ignores_stale_sessions(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """F-07: Auto-selection filters by is_running, ignoring stale sessions."""
        # Create one active and one stale session
        active = Session(
            session_id="active-1",
            display=":99",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-active",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        stale = Session(
            session_id="stale-1",
            display=":98",
            app_pid=200,
            app_command="app",
            app_args=[],
            atspi_bus_pid=201,
            atspi_bus_address="unix:abstract=/tmp/dbus-stale",
            xvfb_pid=198,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_session_repo.save(active)
        fake_session_repo.save(stale)
        # Only mark active session's processes as running
        fake_process._running[100] = True
        fake_process._running[99] = True
        # stale processes are not running (default False)

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id=None)

        # Should auto-select the active session, not the stale one
        assert result.session_id == "active-1"

    def test_stop_surfaces_partial_failure_errors(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """F-08: When cleanup steps fail, errors are surfaced in result."""
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
        fake_session_repo.save(session)

        # Create a process port that raises on stop
        failing_process = FakeProcess()
        original_stop = failing_process.stop

        def raising_stop(pid: int) -> None:
            raise RuntimeError(f"Process {pid} already dead")

        failing_process.stop = raising_stop  # type: ignore[assignment]

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=failing_process,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id="s1")

        assert result.session_id == "s1"
        assert result.status == "stopped_with_errors"
        assert len(result.errors) > 0


# ──────────────────────────────────────────────────────────────────────
# R-SESSION-04: Session list
# ──────────────────────────────────────────────────────────────────────


class TestSessionList:
    """Use case: list all sessions."""

    def test_list_empty(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_process: FakeProcess,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-04: Empty repo returns empty list."""
        uc = SessionListUseCase(
            session_repo=fake_session_repo,
            process=fake_process,
            clock=fake_clock,
        )
        result = uc.execute()

        assert result == []

    def test_list_returns_sessions(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_process: FakeProcess,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-04: List returns all sessions with required fields."""
        session = Session(
            session_id="s1",
            display=":99",
            app_pid=100,
            app_command="gedit",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_session_repo.save(session)

        uc = SessionListUseCase(
            session_repo=fake_session_repo,
            process=fake_process,
            clock=fake_clock,
        )
        result = uc.execute()

        assert len(result) == 1
        entry = result[0]
        assert entry.session_id == "s1"
        assert entry.display == ":99"
        assert entry.app == "gedit"
        assert hasattr(entry, "status")
        assert hasattr(entry, "uptime")

    def test_list_reports_active_status(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_process: FakeProcess,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-04: Active sessions have status='active'."""
        session = Session(
            session_id="s1",
            display=":99",
            app_pid=100,
            app_command="gedit",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
            started_at=900.0,
        )
        fake_session_repo.save(session)
        fake_process._running[100] = True
        fake_process._running[99] = True

        uc = SessionListUseCase(
            session_repo=fake_session_repo,
            process=fake_process,
            clock=fake_clock,
        )
        result = uc.execute()

        assert result[0].status == "active"

    def test_list_reports_stale_status(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_process: FakeProcess,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-04, R-SESSION-06: Dead processes -> status='stale'."""
        session = Session(
            session_id="s1",
            display=":99",
            app_pid=100,
            app_command="gedit",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_session_repo.save(session)
        # All processes dead (default is_running = False)

        uc = SessionListUseCase(
            session_repo=fake_session_repo,
            process=fake_process,
            clock=fake_clock,
        )
        result = uc.execute()

        assert result[0].status == "stale"

    def test_list_reports_uptime_for_active(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_process: FakeProcess,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-04, F-05: Active sessions include computed uptime."""
        session = Session(
            session_id="s1",
            display=":99",
            app_pid=100,
            app_command="gedit",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
            started_at=900.0,
        )
        fake_session_repo.save(session)
        fake_process._running[100] = True
        fake_process._running[99] = True

        # Clock returns 1000.0, started_at=900.0, so uptime=100.0
        uc = SessionListUseCase(
            session_repo=fake_session_repo,
            process=fake_process,
            clock=fake_clock,
        )
        result = uc.execute()

        assert result[0].uptime == 100.0

    def test_list_uptime_none_for_stale(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_process: FakeProcess,
        fake_clock: FakeClock,
    ) -> None:
        """R-SESSION-04: Stale sessions have uptime=None."""
        session = Session(
            session_id="s1",
            display=":99",
            app_pid=100,
            app_command="gedit",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-s1",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
            started_at=900.0,
        )
        fake_session_repo.save(session)
        # processes not running

        uc = SessionListUseCase(
            session_repo=fake_session_repo,
            process=fake_process,
            clock=fake_clock,
        )
        result = uc.execute()

        assert result[0].uptime is None


# ──────────────────────────────────────────────────────────────────────
# R-SESSION-06: Stale session handling
# ──────────────────────────────────────────────────────────────────────


class TestStaleSessionHandling:
    """Stale sessions can be stopped cleanly."""

    def test_stop_stale_session_exits_ok(
        self,
        fake_display_server: FakeDisplayServer,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-SESSION-06: Stop on a stale session succeeds (no error)."""
        session = Session(
            session_id="stale-1",
            display=":99",
            app_pid=100,
            app_command="app",
            app_args=[],
            atspi_bus_pid=101,
            atspi_bus_address="unix:abstract=/tmp/dbus-stale",
            xvfb_pid=99,
            name=None,
            resolution="1280x800",
            color_depth=24,
        )
        fake_session_repo.save(session)
        # Processes are not running (stale)

        uc = SessionStopUseCase(
            display_server=fake_display_server,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id="stale-1")

        assert result.status == "stopped"


# ──────────────────────────────────────────────────────────────────────
# FC-SESSION-12: App launch failure cleanup
# ──────────────────────────────────────────────────────────────────────


class TestSessionStartFailure:
    """When target app fails, Xvfb and AT-SPI2 must be cleaned up."""

    def test_app_launch_failure_cleans_up(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """FC-SESSION-12: App start failure cleans up Xvfb and AT-SPI2."""
        failing_process = FakeProcess()
        # Simulate app start failure by making start raise
        original_start = failing_process.start

        def failing_start(command: str, args: List[str], env: Any = None) -> int:
            raise RuntimeError("App failed to launch")

        failing_process.start = failing_start  # type: ignore[assignment]

        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=failing_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )

        with pytest.raises(Exception):
            uc.execute(app_command="broken-app", app_args=[])

        # Xvfb and AT-SPI2 bus should have been cleaned up
        assert fake_display_server.stopped
        assert fake_accessibility_bus.stopped

    def test_start_repo_save_failure_cleans_up_all_processes(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_clock: FakeClock,
    ) -> None:
        """F-15: If repo.save() fails, all launched processes must be cleaned up."""
        failing_repo = FakeSessionRepository(fail_on_save=True)

        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=failing_repo,
            clock=fake_clock,
        )

        with pytest.raises(RuntimeError, match="disk full"):
            uc.execute(app_command="app", app_args=[])

        # All processes must be stopped: app, AT-SPI2 bus, and Xvfb
        process_stop_calls = [c for c in fake_process.calls if c[0] == "stop"]
        assert len(process_stop_calls) >= 1, "App process should be stopped"
        assert fake_display_server.stopped, "Xvfb should be stopped"
        assert fake_accessibility_bus.stopped, "AT-SPI2 bus should be stopped"


# ──────────────────────────────────────────────────────────────────────
# A10-C5-001: App launch env inherits host environment
# ──────────────────────────────────────────────────────────────────────


class TestSessionStartHostEnv:
    """Target app must receive full host env with a11y overrides on top."""

    def test_app_env_contains_host_path(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A10-C5-001: App env includes PATH from host environment."""
        monkeypatch.setenv("PATH", "/usr/bin:/usr/local/bin")

        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        assert env.get("PATH") == "/usr/bin:/usr/local/bin"

    def test_app_env_contains_host_home(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A10-C5-001: App env includes HOME from host environment."""
        monkeypatch.setenv("HOME", "/home/testuser")

        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        assert env.get("HOME") == "/home/testuser"

    def test_a11y_vars_override_host_display(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A10-C5-001: A11y overrides (DISPLAY) take precedence over host env."""
        monkeypatch.setenv("DISPLAY", ":0")

        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        result = uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        # Must be the Xvfb display, not the host :0
        assert env.get("DISPLAY") == result.display
        assert env.get("DISPLAY") != ":0"

    def test_app_env_has_more_than_a11y_vars(
        self,
        fake_display_server: FakeDisplayServer,
        fake_display_allocator: FakeDisplayAllocator,
        fake_accessibility_bus: FakeAccessibilityBus,
        fake_process: FakeProcess,
        fake_session_repo: FakeSessionRepository,
        fake_clock: FakeClock,
    ) -> None:
        """A10-C5-001: App env must contain more than just the 5 a11y vars."""
        uc = SessionStartUseCase(
            display_server=fake_display_server,
            allocator=fake_display_allocator,
            atspi_bus=fake_accessibility_bus,
            process=fake_process,
            session_repo=fake_session_repo,
            clock=fake_clock,
        )
        uc.execute(app_command="gedit", app_args=[])

        start_calls = [c for c in fake_process.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        # Must have inherited host env, so more than just the 5 a11y vars
        assert len(env) > 5


# ──────────────────────────────────────────────────────────────────────
# A10-C5-002: Session ID validation (path traversal prevention)
# ──────────────────────────────────────────────────────────────────────


class TestSessionIdValidation:
    """Session ID must be validated to prevent path traversal attacks."""

    def test_valid_session_id_alphanumeric(self) -> None:
        """A10-C5-002: Alphanumeric session IDs are accepted."""
        session = Session(
            session_id="abc123",
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
        assert session.session_id == "abc123"

    def test_valid_session_id_with_hyphens(self) -> None:
        """A10-C5-002: Hyphens are allowed in session IDs."""
        session = Session(
            session_id="abc-123-def",
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
        assert session.session_id == "abc-123-def"

    def test_valid_session_id_with_underscores(self) -> None:
        """A10-C5-002: Underscores are allowed in session IDs."""
        session = Session(
            session_id="abc_123_def",
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
        assert session.session_id == "abc_123_def"

    def test_rejects_path_traversal_dotdot(self) -> None:
        """A10-C5-002: session_id with '..' is rejected (path traversal)."""
        with pytest.raises(ValueError, match="invalid characters"):
            Session(
                session_id="../../etc",
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

    def test_rejects_forward_slash(self) -> None:
        """A10-C5-002: session_id with '/' is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            Session(
                session_id="abc/def",
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

    def test_rejects_backslash(self) -> None:
        """A10-C5-002: session_id with '\\' is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            Session(
                session_id="abc\\def",
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

    def test_rejects_empty_session_id(self) -> None:
        """A10-C5-002: Empty session_id is rejected."""
        with pytest.raises(ValueError, match="must not be empty"):
            Session(
                session_id="",
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

    def test_rejects_dot_only(self) -> None:
        """A10-C5-002: session_id of '.' or '..' is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            Session(
                session_id=".",
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

    def test_rejects_spaces(self) -> None:
        """A10-C5-002: session_id with spaces is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            Session(
                session_id="abc def",
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

    def test_rejects_null_byte(self) -> None:
        """A10-C5-002: session_id with null byte is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            Session(
                session_id="abc\x00def",
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
