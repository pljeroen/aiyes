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



def _status_reply(command, *, legacy=True, fault=None):
    """Realistic dbus-send replies for either explicit status discovery method."""
    if "org.a11y.Bus.GetAddress" in command:
        body = '   string "unix:path=/tmp/at-spi-test"\n'
    elif "org.freedesktop.DBus.Introspectable.Introspect" in command:
        properties = [("IsEnabled", "b")]
        if legacy:
            properties.append(("ScreenReaderEnabled", "b"))
        if fault == "missing-mandatory":
            properties = [("ScreenReaderEnabled", "b")]
        elif fault == "wrong-type":
            properties[0] = ("IsEnabled", "s")
        xml = '<node><interface name="org.a11y.Status">'
        xml += "".join(
            f'<property name="{name}" type="{kind}" access="readwrite"/>'
            for name, kind in properties
        )
        xml += "</interface></node>"
        body = f'   string "{xml}"\n'
    elif "org.freedesktop.DBus.Properties.GetAll" in command:
        assert "string:org.a11y.Status" in command, command
        properties = [("IsEnabled", "boolean false")]
        if legacy:
            properties.append(("ScreenReaderEnabled", "boolean false"))
        if fault == "missing-mandatory":
            properties = [("ScreenReaderEnabled", "boolean false")]
        elif fault == "wrong-type":
            properties[0] = ("IsEnabled", 'string "false"')
        body = "   array [\n" + "".join(
            f'      dict entry(\n         string "{name}"\n'
            f"         variant             {value}\n      )\n"
            for name, value in properties
        ) + "   ]\n"
    else:
        assert "org.freedesktop.DBus.Properties.Set" in command, command
        body = ""
    return subprocess.CompletedProcess(
        command, 0,
        stdout="method return time=1.0 sender=:1.0 -> destination=:1.1 serial=1 reply_serial=2\n" + body,
        stderr="",
    )


def _legacy_bus_response(command, **kwargs):
    """Supply explicit legacy replies without changing existing expectations."""
    return _status_reply(command, legacy=True)


class _StatusBusHarness:
    """Owned child and D-Bus call observations without real processes or a bus."""

    def __init__(self, *, legacy=True, failure=None):
        self.legacy = legacy
        self.failure = failure
        self.adapter = AtSpiBusAdapter()
        self.children = [
            _make_mock_dbus_daemon(), _make_mock_launcher(), _make_mock_registryd()
        ]
        self.acquired = []
        self.process_calls = []
        self.calls = []
        self.queries = []
        self.writes = []
        self.cleanup = []
        for child in self.children:
            child.terminate.side_effect = lambda child=child: self.cleanup.append(
                ("terminate", child.pid)
            )
            child.wait.side_effect = lambda *, timeout, child=child: self.cleanup.append(
                ("wait", child.pid, timeout)
            )

    def spawn(self, command, **kwargs):
        child = self.children[len(self.acquired)]
        self.acquired.append(child)
        self.process_calls.append((command, kwargs))
        return child

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        is_query = any(
            method in command
            for method in (
                "org.freedesktop.DBus.Introspectable.Introspect",
                "org.freedesktop.DBus.Properties.GetAll",
            )
        )
        reply = _status_reply(command, legacy=self.legacy, fault=self.failure)
        if is_query:
            self.queries.append(command)
            errors = {
                "query-access-denied": "org.freedesktop.DBus.Error.AccessDenied",
                "query-service-lost": "org.freedesktop.DBus.Error.ServiceUnknown",
                "query-invalid-args": "org.freedesktop.DBus.Error.InvalidArgs: ScreenReaderEnabled",
            }
            if self.failure in errors:
                reply = subprocess.CompletedProcess(
                    command, 1, stdout=reply.stdout, stderr=errors[self.failure]
                )
            elif self.failure == "query-timeout":
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 5))
            elif self.failure == "query-malformed":
                reply = subprocess.CompletedProcess(
                    command, 0, stdout="not a valid status reply: ScreenReaderEnabled", stderr=""
                )
        elif "org.freedesktop.DBus.Properties.Set" in command:
            prop = next(
                value.removeprefix("string:") for value in command
                if value in ("string:IsEnabled", "string:ScreenReaderEnabled")
            )
            assert self.legacy or prop != "ScreenReaderEnabled", (
                "Current service has no ScreenReaderEnabled property; do not write it"
            )
            self.writes.append(prop)
            if self.failure == {
                "IsEnabled": "mandatory-write-denied",
                "ScreenReaderEnabled": "legacy-write-denied",
            }[prop]:
                reply = subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="org.freedesktop.DBus.Error.AccessDenied"
                )
        if kwargs.get("check") and reply.returncode:
            raise subprocess.CalledProcessError(
                reply.returncode, command, output=reply.stdout, stderr=reply.stderr
            )
        return reply

    def assert_cleaned(self):
        expected = [
            event for child in reversed(self.acquired)
            for event in (("terminate", child.pid), ("wait", child.pid, 3))
        ]
        assert self.cleanup == expected
        assert self.adapter._managed_procs == {}


@pytest.fixture
def status_bus(monkeypatch):
    """Patch only the adapter boundary and give each case its own observations."""
    def make(**kwargs):
        harness = _StatusBusHarness(**kwargs)
        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.subprocess.Popen", harness.spawn)
        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.subprocess.run", harness.run)
        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.time.sleep", lambda _: None)
        monkeypatch.setattr(
            "aiyes.adapters.atspi_bus_adapter.shutil.which",
            lambda name: "/usr/bin/" + name,
        )
        return harness
    return make


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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ) as mock_run,
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ) as mock_run,
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ) as mock_run,
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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
            patch(
                "aiyes.adapters.atspi_bus_adapter.subprocess.run",
                side_effect=_legacy_bus_response,
            ),
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

        # Discovery succeeds; the advertised legacy property write itself fails.
        def run_side_effect(cmd, **kwargs):
            if (
                "org.freedesktop.DBus.Properties.Set" in cmd
                and "string:ScreenReaderEnabled" in cmd
            ):
                raise subprocess.CalledProcessError(1, cmd)
            return _legacy_bus_response(cmd, **kwargs)

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


@pytest.mark.parallel_unsafe
class TestAtSpiStatusCompatibility:
    """Current and legacy service activation must retain isolation and ownership."""

    @pytest.mark.parametrize("legacy", [False, True], ids=["current", "legacy"])
    def test_supported_status_service_activates_before_success(self, status_bus, legacy):
        harness = status_bus(legacy=legacy)

        result = harness.adapter.start_bus(":99")

        assert harness.process_calls[1][0] == [
            "/usr/bin/at-spi-bus-launcher", "--launch-immediately"
        ]
        assert harness.queries, "Startup must consume an explicit successful status reply"
        assert sorted(harness.writes) == (
            ["IsEnabled", "ScreenReaderEnabled"] if legacy else ["IsEnabled"]
        )
        assert result == BusStartResult(
            pid=7001,
            bus_address="unix:abstract=/tmp/dbus-aBcDeF",
            atspi_bus_address="unix:path=/tmp/at-spi-test",
        )
        assert harness.adapter._managed_procs == {7001: harness.acquired}
        assert harness.process_calls[1][1]["env"]["DISPLAY"] == ":99"
        for _, options in harness.process_calls[1:]:
            assert options["env"]["DBUS_SESSION_BUS_ADDRESS"] == result.bus_address
        for command, options in harness.calls:
            assert "--bus=" + result.bus_address in command
            assert "--dest=org.a11y.Bus" in command
            assert "/org/a11y/bus" in command
            assert options["timeout"] == 5
            if "org.freedesktop.DBus.Properties.Set" in command:
                assert "string:org.a11y.Status" in command
                assert "variant:boolean:true" in command
        assert harness.cleanup == []

        harness.adapter.stop_bus(result.pid)

        harness.assert_cleaned()

    def test_application_launch_receives_only_an_activated_isolated_bus(self, status_bus):
        harness = status_bus(legacy=False)
        process = FakeProcess()
        repository = FakeSessionRepository()
        process_start = process.start
        observed_activation = []

        def start_after_activation(command, args, env=None):
            observed_activation.append(tuple(harness.writes))
            assert harness.queries
            assert harness.writes == ["IsEnabled"]
            assert 7001 in harness.adapter._managed_procs
            return process_start(command, args, env=env)

        process.start = start_after_activation
        use_case = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(),
            atspi_bus=harness.adapter,
            process=process,
            session_repo=repository,
            clock=FakeClock(),
        )

        session = use_case.execute(app_command="test-app", app_args=[], wait=0.0)

        assert observed_activation == [("IsEnabled",)]
        app_calls = [args for name, args in process.calls if name == "start"]
        assert len(app_calls) == 1
        command, arguments, environment = app_calls[0]
        assert (command, arguments) == ("test-app", [])
        assert environment["DBUS_SESSION_BUS_ADDRESS"] == "unix:abstract=/tmp/dbus-aBcDeF"
        assert "AT_SPI_BUS_ADDRESS" not in environment
        assert session.atspi_bus_pid == 7001
        assert session.atspi_bus_address == environment["DBUS_SESSION_BUS_ADDRESS"]
        assert repository.load_all() == [session]

        harness.adapter.stop_bus(session.atspi_bus_pid)
        harness.assert_cleaned()

    @pytest.mark.parametrize(
        "failure",
        [
            "query-access-denied",
            "query-service-lost",
            "query-invalid-args",
            "query-timeout",
            "query-malformed",
            "missing-mandatory",
            "wrong-type",
            "mandatory-write-denied",
            "legacy-write-denied",
        ],
    )
    def test_activation_failure_releases_acquired_children(self, status_bus, failure):
        harness = status_bus(legacy=True, failure=failure)

        with pytest.raises((
            subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError, ValueError
        )):
            harness.adapter.start_bus(":99")

        if failure in ("mandatory-write-denied", "legacy-write-denied"):
            expected_property = (
                "IsEnabled" if failure == "mandatory-write-denied" else "ScreenReaderEnabled"
            )
            assert expected_property in harness.writes
        else:
            assert harness.queries, "The failed capability operation must actually be reached"
            assert harness.writes == [], "Failed discovery cannot authorize activation"
        harness.assert_cleaned()
