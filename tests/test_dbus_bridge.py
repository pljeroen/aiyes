"""Tests for AIYES-04: AccessKit/AT-SPI2 bridge fix.

Requirements covered:
  R-ATSPI-01: Isolated D-Bus session bus via dbus-daemon
  R-ATSPI-02: Set ScreenReaderEnabled=true on the isolated session bus
  R-ATSPI-03: Pass isolated session bus address to the application
  R-ATSPI-05: BusStartResult must return real addresses (not sentinel)
  R-ATSPI-06: at-spi2-registryd must be running

Regression guards:
  - Existing a11y env vars still set
  - Bus address is never the sentinel "atspi-via-x11-property"
"""

from __future__ import annotations

import subprocess
from typing import Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from aiyes.adapters.atspi_bus_adapter import AtSpiBusAdapter
from aiyes.domain.types import BusStartResult
from aiyes.domain.use_cases.session_start import SessionStartUseCase

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _get_app_env(
    fake_process: FakeProcess,
    monkeypatch: Optional[pytest.MonkeyPatch] = None,
    env_overrides: Optional[Dict[str, str]] = None,
    bus_address: str = "unix:abstract=/tmp/dbus-test",
) -> Dict[str, str]:
    """Execute session start and return the env dict passed to process.start()."""
    if monkeypatch and env_overrides:
        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)

    uc = SessionStartUseCase(
        display_server=FakeDisplayServer(),
        allocator=FakeDisplayAllocator(),
        atspi_bus=FakeAccessibilityBus(bus_address=bus_address),
        process=fake_process,
        session_repo=FakeSessionRepository(),
        clock=FakeClock(),
    )
    uc.execute(app_command="test-app", app_args=[])

    start_calls = [c for c in fake_process.calls if c[0] == "start"]
    _, _, env = start_calls[0][1]
    assert env is not None
    return env


def _make_mock_dbus_daemon():
    """Create a mock Popen for dbus-daemon that returns a bus address on stdout."""
    mock = MagicMock()
    mock.pid = 7001
    mock.stdout.readline.return_value = b"unix:abstract=/tmp/dbus-aBcDeF\n"
    mock.poll.return_value = None  # still running
    return mock


def _make_mock_launcher():
    """Create a mock Popen for at-spi-bus-launcher."""
    mock = MagicMock()
    mock.pid = 7002
    mock.poll.return_value = None
    return mock


def _make_mock_registryd():
    """Create a mock Popen for at-spi2-registryd."""
    mock = MagicMock()
    mock.pid = 7003
    mock.poll.return_value = None
    return mock


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-01: Isolated D-Bus Session Bus
# ──────────────────────────────────────────────────────────────────────


class TestAtSpiBusAdapterIsolatedBus:
    """R-ATSPI-01: start_bus() must create an isolated dbus-daemon session bus."""

    def test_start_bus_launches_dbus_daemon(self) -> None:
        """dbus-daemon must be started with --session --print-address --nofork."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ) as mock_popen,
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            adapter.start_bus(":99")

            # First Popen call must be dbus-daemon
            first_call = mock_popen.call_args_list[0]
            cmd = first_call[0][0]
            assert cmd[0] == "dbus-daemon"
            assert "--session" in cmd
            assert "--print-address" in cmd
            assert "--nofork" in cmd

    def test_start_bus_starts_launcher_with_session_bus(self) -> None:
        """at-spi-bus-launcher must receive DBUS_SESSION_BUS_ADDRESS from dbus-daemon."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ) as mock_popen,
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            adapter.start_bus(":99")

            # Second Popen call is at-spi-bus-launcher
            launcher_call = mock_popen.call_args_list[1]
            launcher_env = launcher_call[1].get("env", {})
            assert (
                launcher_env.get("DBUS_SESSION_BUS_ADDRESS")
                == "unix:abstract=/tmp/dbus-aBcDeF"
            )
            assert launcher_env.get("DISPLAY") == ":99"

    def test_start_bus_starts_registryd(self) -> None:
        """at-spi2-registryd must be started with correct executable and env."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        def which_side_effect(name: str) -> Optional[str]:
            if name == "at-spi-bus-launcher":
                return "/usr/bin/at-spi-bus-launcher"
            if name == "at-spi2-registryd":
                return "/usr/bin/at-spi2-registryd"
            return None

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ) as mock_popen,
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                side_effect=which_side_effect,
            ),
        ):
            adapter.start_bus(":99")

            # Third Popen call is at-spi2-registryd
            assert len(mock_popen.call_args_list) >= 3
            registryd_call = mock_popen.call_args_list[2]
            # Verify the correct executable was used (not the launcher)
            registryd_cmd = registryd_call[0][0]
            assert registryd_cmd[0] == "/usr/bin/at-spi2-registryd"
            # Verify environment
            registryd_env = registryd_call[1].get("env", {})
            assert (
                registryd_env.get("DBUS_SESSION_BUS_ADDRESS")
                == "unix:abstract=/tmp/dbus-aBcDeF"
            )

    def test_start_bus_returns_real_bus_address(self) -> None:
        """BusStartResult.bus_address must start with 'unix:' (not sentinel)."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            result = adapter.start_bus(":99")

            assert isinstance(result, BusStartResult)
            assert result.bus_address.startswith("unix:")
            assert result.bus_address == "unix:abstract=/tmp/dbus-aBcDeF"

    def test_start_bus_returns_dbus_daemon_pid(self) -> None:
        """BusStartResult.pid must be the dbus-daemon PID (for cleanup)."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            result = adapter.start_bus(":99")

            assert result.pid == 7001  # dbus-daemon PID


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-02: Set ScreenReaderEnabled=true
# ──────────────────────────────────────────────────────────────────────


class TestAtSpiBusScreenReaderEnabled:
    """R-ATSPI-02: start_bus() must set ScreenReaderEnabled=true via dbus-send."""

    def test_start_bus_sets_screen_reader_enabled(self) -> None:
        """dbus-send must be called with ScreenReaderEnabled variant:boolean:true."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run") as mock_run,
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            adapter.start_bus(":99")

            # Find the dbus-send call for ScreenReaderEnabled
            screen_reader_calls = [
                c
                for c in mock_run.call_args_list
                if any("ScreenReaderEnabled" in str(a) for a in c[0][0])
            ]
            assert len(screen_reader_calls) >= 1, (
                "dbus-send for ScreenReaderEnabled not called"
            )

            cmd = screen_reader_calls[0][0][0]
            assert "dbus-send" in cmd[0]
            assert "variant:boolean:true" in cmd

    def test_start_bus_sets_is_enabled(self) -> None:
        """dbus-send must also set IsEnabled=true for GTK/ATK apps."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run") as mock_run,
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            adapter.start_bus(":99")

            is_enabled_calls = [
                c
                for c in mock_run.call_args_list
                if any("IsEnabled" in str(a) for a in c[0][0])
            ]
            assert len(is_enabled_calls) >= 1, "dbus-send for IsEnabled not called"

    def test_start_bus_sends_to_correct_bus_address(self) -> None:
        """dbus-send must target the isolated session bus address."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run") as mock_run,
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            adapter.start_bus(":99")

            # All dbus-send calls must use --bus= with the session bus address
            for c in mock_run.call_args_list:
                cmd = c[0][0]
                bus_args = [a for a in cmd if a.startswith("--bus=")]
                if bus_args:
                    assert bus_args[0] == "--bus=unix:abstract=/tmp/dbus-aBcDeF"


# ──────────────────────────────────────────────────────────────────────
# Process Cleanup (stop_bus)
# ──────────────────────────────────────────────────────────────────────


class TestAtSpiBusAdapterStopBus:
    """stop_bus() must terminate and reap all spawned processes."""

    def test_stop_bus_terminates_all_spawned_processes(self) -> None:
        """stop_bus() must terminate dbus-daemon, launcher, and registryd."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            result = adapter.start_bus(":99")

        adapter.stop_bus(result.pid)

        # All three processes must be terminated and waited on
        mock_dbus.terminate.assert_called_once()
        mock_dbus.wait.assert_called_once()
        mock_launcher.terminate.assert_called_once()
        mock_launcher.wait.assert_called_once()
        mock_registryd.terminate.assert_called_once()
        mock_registryd.wait.assert_called_once()

    def test_stop_bus_tolerates_already_dead_processes(self) -> None:
        """stop_bus() must not raise when processes are already dead."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        # Simulate ProcessLookupError on terminate
        mock_dbus.terminate.side_effect = ProcessLookupError
        mock_launcher.terminate.side_effect = ProcessLookupError
        mock_registryd.terminate.side_effect = ProcessLookupError

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            result = adapter.start_bus(":99")

        # Must not raise
        adapter.stop_bus(result.pid)

    def test_stop_bus_unknown_pid_kills_just_that_pid(self) -> None:
        """stop_bus() with unknown PID falls back to killing just that PID."""
        adapter = AtSpiBusAdapter()

        with patch("aiyes.adapters.atspi_bus_adapter.os.kill") as mock_kill:
            adapter.stop_bus(99999)

            mock_kill.assert_called_once()
            assert mock_kill.call_args[0][0] == 99999


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-03: Session Start Environment
# ──────────────────────────────────────────────────────────────────────


class TestSessionDbusIsolation:
    """R-ATSPI-03: App env must include DBUS_SESSION_BUS_ADDRESS from the isolated bus."""

    def test_app_env_has_dbus_session_bus_address(self) -> None:
        """DBUS_SESSION_BUS_ADDRESS must be set in app env."""
        fp = FakeProcess()
        env = _get_app_env(fp, bus_address="unix:abstract=/tmp/dbus-isolated")
        assert env.get("DBUS_SESSION_BUS_ADDRESS") == "unix:abstract=/tmp/dbus-isolated"

    def test_app_env_dbus_overrides_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """App env DBUS_SESSION_BUS_ADDRESS must override the host value."""
        fp = FakeProcess()
        env = _get_app_env(
            fp,
            monkeypatch,
            {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"},
            bus_address="unix:abstract=/tmp/dbus-isolated",
        )
        assert env.get("DBUS_SESSION_BUS_ADDRESS") == "unix:abstract=/tmp/dbus-isolated"

    def test_app_env_at_spi_bus_address_not_set(self) -> None:
        """AT_SPI_BUS_ADDRESS must NOT be set — AccessKit discovers via org.a11y.Bus."""
        fp = FakeProcess()
        env = _get_app_env(fp, bus_address="unix:abstract=/tmp/dbus-isolated")
        assert "AT_SPI_BUS_ADDRESS" not in env, (
            "AT_SPI_BUS_ADDRESS should not be set in app env — "
            "AccessKit discovers the AT-SPI bus via org.a11y.Bus.GetAddress()"
        )


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-05: Bus Address Not Sentinel
# ──────────────────────────────────────────────────────────────────────


class TestBusAddressNotSentinel:
    """R-ATSPI-05: The sentinel 'atspi-via-x11-property' must never be used."""

    def test_bus_address_not_sentinel(self) -> None:
        """start_bus() must return a real bus address, not the sentinel."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            result = adapter.start_bus(":99")

            assert result.bus_address != "atspi-via-x11-property"
            assert "atspi-via-x11-property" not in result.bus_address

    def test_app_env_at_spi_not_sentinel(self) -> None:
        """AT_SPI_BUS_ADDRESS in app env must not be the sentinel."""
        fp = FakeProcess()
        env = _get_app_env(fp, bus_address="unix:abstract=/tmp/dbus-test-real")
        assert env.get("AT_SPI_BUS_ADDRESS") != "atspi-via-x11-property"


# ──────────────────────────────────────────────────────────────────────
# Regression Guards
# ──────────────────────────────────────────────────────────────────────


class TestAtspiRegressionGuards:
    """Existing a11y env vars must remain set after the AIYES-04 changes."""

    def test_existing_a11y_env_vars_still_set(self) -> None:
        """GTK_MODULES, QT_ACCESSIBILITY, QT_LINUX_ACCESSIBILITY_ALWAYS_ON still present."""
        fp = FakeProcess()
        env = _get_app_env(fp, bus_address="unix:abstract=/tmp/dbus-test")

        assert env.get("GTK_MODULES") == "gail:atk-bridge"
        assert env.get("QT_ACCESSIBILITY") == "1"
        assert env.get("QT_LINUX_ACCESSIBILITY_ALWAYS_ON") == "1"

    def test_display_still_set(self) -> None:
        """DISPLAY must still be set to the Xvfb display."""
        fp = FakeProcess()
        env = _get_app_env(fp, bus_address="unix:abstract=/tmp/dbus-test")
        assert env.get("DISPLAY") == ":99"

    def test_x11_backend_vars_still_set(self) -> None:
        """AIYES-03 X11 backend vars must not be broken by AIYES-04."""
        fp = FakeProcess()
        env = _get_app_env(fp, bus_address="unix:abstract=/tmp/dbus-test")
        assert env.get("WINIT_UNIX_BACKEND") == "x11"
        assert env.get("GDK_BACKEND") == "x11"

    def test_software_rendering_vars_still_set(self) -> None:
        """AIYES-03 software rendering vars must not be broken by AIYES-04."""
        fp = FakeProcess()
        env = _get_app_env(fp, bus_address="unix:abstract=/tmp/dbus-test")
        assert env.get("LIBGL_ALWAYS_SOFTWARE") == "1"
        assert env.get("GALLIUM_DRIVER") == "llvmpipe"


# ──────────────────────────────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────────────────────────────


class TestAtSpiBusAdapterErrorHandling:
    """start_bus() must handle errors gracefully and clean up on partial failure."""

    def test_start_bus_raises_on_empty_bus_address(self) -> None:
        """If dbus-daemon returns an empty address, start_bus() must raise and clean up."""
        adapter = AtSpiBusAdapter()
        mock_dbus = MagicMock()
        mock_dbus.pid = 7001
        mock_dbus.stdout.readline.return_value = b"\n"
        mock_dbus.poll.return_value = None

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="dbus-daemon"):
                adapter.start_bus(":99")

            # dbus-daemon must be terminated and reaped
            mock_dbus.terminate.assert_called_once()
            mock_dbus.wait.assert_called_once()

    def test_start_bus_raises_when_dbus_daemon_exits_immediately(self) -> None:
        """If dbus-daemon exits immediately (poll() not None), must raise."""
        adapter = AtSpiBusAdapter()
        mock_dbus = MagicMock()
        mock_dbus.pid = 7001
        mock_dbus.stdout.readline.return_value = b"unix:abstract=/tmp/dbus-test\n"
        mock_dbus.poll.return_value = 1  # exited with error

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="dbus-daemon"):
                adapter.start_bus(":99")

    def test_launcher_failure_cleans_up_dbus_daemon(self) -> None:
        """If launcher Popen fails, dbus-daemon must be terminated and reaped."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, OSError("cannot spawn launcher")],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            with pytest.raises(OSError, match="cannot spawn launcher"):
                adapter.start_bus(":99")

            # dbus-daemon must be cleaned up
            mock_dbus.terminate.assert_called_once()
            mock_dbus.wait.assert_called_once()

    def test_registryd_failure_cleans_up_dbus_and_launcher(self) -> None:
        """If registryd Popen fails, dbus-daemon and launcher must be cleaned up."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[
                    mock_dbus,
                    mock_launcher,
                    OSError("cannot spawn registryd"),
                ],
            ),
            patch("aiyes.adapters.atspi_bus_adapter.subprocess.run"),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            with pytest.raises(OSError, match="cannot spawn registryd"):
                adapter.start_bus(":99")

            # Both dbus-daemon and launcher must be cleaned up
            mock_dbus.terminate.assert_called_once()
            mock_dbus.wait.assert_called_once()
            mock_launcher.terminate.assert_called_once()
            mock_launcher.wait.assert_called_once()

    def test_dbus_send_failure_cleans_up_all_three(self) -> None:
        """If dbus-send fails, all three processes must be cleaned up."""
        adapter = AtSpiBusAdapter()
        mock_dbus = _make_mock_dbus_daemon()
        mock_launcher = _make_mock_launcher()
        mock_registryd = _make_mock_registryd()

        # First subprocess.run is GetAddress (succeeds),
        # then dbus-send ScreenReaderEnabled (fails)
        get_addr_result = MagicMock()
        get_addr_result.stdout = '   string "unix:path=/tmp/at-spi-test"\n'

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "GetAddress" in str(cmd):
                return get_addr_result
            raise subprocess.CalledProcessError(1, "dbus-send")

        with (
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.Popen",
                side_effect=[mock_dbus, mock_launcher, mock_registryd],
            ),
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=run_side_effect,
            ),
            patch("aiyes.adapters.atspi_bus_adapter.time.sleep"),
            patch(
                "aiyes.adapters.atspi_bus_adapter.shutil.which",
                return_value="/usr/bin/at-spi-bus-launcher",
            ),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                adapter.start_bus(":99")

            # All three processes must be cleaned up
            mock_dbus.terminate.assert_called_once()
            mock_dbus.wait.assert_called_once()
            mock_launcher.terminate.assert_called_once()
            mock_launcher.wait.assert_called_once()
            mock_registryd.terminate.assert_called_once()
            mock_registryd.wait.assert_called_once()
