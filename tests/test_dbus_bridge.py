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

import builtins
from collections import Counter
import io
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
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

    @pytest.mark.parallel_unsafe
    def test_stop_bus_unknown_pid_refuses_without_signal(self, unowned_bus_stop) -> None:
        """Missing ownership proof is a visible refusal without any signal."""
        adapter = AtSpiBusAdapter()

        with pytest.raises(RuntimeError):
            adapter.stop_bus(99999)

        for signal_route in unowned_bus_stop:
            signal_route.assert_not_called()


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


_PROBE_CODE = """
import os, pathlib, signal, subprocess, sys, time
role, directory, address, stubborn = sys.argv[1:]
root = pathlib.Path(directory)
if role == 'dbus-daemon':
    print(address, flush=True)
if role == 'launcher':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', 'accessibility-broker'])
    (root / 'descendant.pid').write_text(str(child.pid))
if role == stubborn:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / (role + '.ready')).write_text(str(os.getpid()))
while True:
    time.sleep(1)
"""


class _PrivateBusProbe:
    """Inert private children supply real Linux identities; D-Bus replies stay fake."""

    def __init__(self, monkeypatch, root, *, legacy=True, stubborn=""):
        self.root = root
        self.root.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.receipts = self.home / ".aieyes" / "_global" / "atspi-ownership"
        self.address = "unix:path=" + str(root / "session-bus")
        self.legacy = legacy
        self.stubborn = stubborn
        self.children = []
        self.pinned = {}
        self.protected = set()
        self.environments = []
        self.numeric_signals = []
        self.descriptor_signals = []
        self.signal_times = []
        self.opened = []
        self.closed = []
        self.descriptors = {}
        self.drop_kill = False
        self.writes = []
        self.real_popen = subprocess.Popen
        self.real_pidfd_open = os.pidfd_open
        self.real_send = signal.pidfd_send_signal
        self.real_close = os.close
        self.real_kill = os.kill
        self.real_sleep = time.sleep
        self.real_monotonic = time.monotonic
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("GSETTINGS_BACKEND", "dconf")
        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.subprocess.Popen", self.spawn)
        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.subprocess.run", self.run)
        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.time.sleep", lambda delay: None)
        monkeypatch.setattr("os.pidfd_open", self.pin)
        monkeypatch.setattr("signal.pidfd_send_signal", self.send)
        monkeypatch.setattr("os.kill", self.numeric_signal)
        monkeypatch.setattr("os.close", self.close_descriptor)

    def wait_for(self, predicate):
        deadline = self.real_monotonic() + 4
        while self.real_monotonic() < deadline:
            if predicate():
                return True
            self.real_sleep(0.005)
        return False

    def remember(self, pid):
        if pid not in self.pinned:
            self.pinned[pid] = self.real_pidfd_open(pid)

    def spawn(self, command, **kwargs):
        name = Path(command[0]).name
        role = {"dbus-daemon": "dbus-daemon", "at-spi-bus-launcher": "launcher", "at-spi2-registryd": "registryd"}[name]
        ready = self.root / (role + ".ready")
        ready.unlink(missing_ok=True)
        process = self.real_popen(
            [sys.executable, "-B", "-c", _PROBE_CODE, role, str(self.root), self.address, self.stubborn],
            **kwargs,
        )
        self.children.append(process)
        self.remember(process.pid)
        self.environments.append((role, dict(kwargs["env"])))
        if not self.wait_for(lambda: (self.root / (role + ".ready")).exists()):
            raise RuntimeError("Private process probe did not become ready")
        if role == "launcher":
            self.remember(int((self.root / "descendant.pid").read_text()))
        return process

    def run(self, command, **kwargs):
        if "org.freedesktop.DBus.Properties.Set" in command:
            self.writes.append(next(arg for arg in command if arg in ("string:IsEnabled", "string:ScreenReaderEnabled")))
        return _status_reply(command, legacy=self.legacy)

    def pin(self, pid, flags=0):
        descriptor = self.real_pidfd_open(pid, flags)
        self.opened.append(descriptor)
        self.descriptors[descriptor] = pid
        return descriptor

    def send(self, descriptor, signum, siginfo=None, flags=0):
        pid = self.descriptors.get(descriptor)
        self.descriptor_signals.append((pid, signum))
        self.signal_times.append((pid, signum, self.real_monotonic()))
        # Every real delivery is restricted to a descriptor pinned by this probe.
        if pid in self.pinned and pid not in self.protected:
            if signum != signal.SIGKILL or not self.drop_kill:
                return self.real_send(descriptor, signum, siginfo, flags)
        return None

    def numeric_signal(self, pid, signum):
        self.numeric_signals.append((pid, signum))
        if pid in self.pinned and pid not in self.protected:
            return self.real_kill(pid, signum)
        return None

    def close_descriptor(self, descriptor):
        if descriptor in self.descriptors:
            self.closed.append(descriptor)
        return self.real_close(descriptor)

    def records(self):
        records = {path: path.read_bytes() for path in self.receipts.rglob("*") if path.is_file() and not path.is_symlink()}
        assert records, "Successful startup must publish recoverable private ownership metadata"
        return records

    def assert_private_record(self, bus_pid):
        records = self.records()
        combined = b"\n".join(records.values())
        for pid in [child.pid for child in self.children]:
            assert str(pid).encode() in combined, "Receipt must cover every direct root"
            stat_fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            assert stat_fields[19].encode() in combined, "Receipt must bind Linux process birth identity"
        assert Path("/proc/sys/kernel/random/boot_id").read_text().strip().encode() in (
            combined + str(next(iter(records))).encode()
        )
        assert str(os.getuid()).encode() in combined
        assert os.readlink(f"/proc/{bus_pid}/exe").encode() in combined
        assert self.address.encode() in combined
        assert b":99" in combined
        for directory in [self.receipts] + [p for p in self.receipts.rglob("*") if p.is_dir()]:
            assert directory.stat().st_mode & 0o777 == 0o700
        for path in records:
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.stat().st_uid == os.getuid()
        return records

    def running(self, pid):
        path = Path(f"/proc/{pid}/stat")
        if not path.exists():
            return False
        return path.read_text().rsplit(")", 1)[1].split()[0] != "Z"

    def assert_stopped(self):
        assert self.wait_for(lambda: all(not self.running(pid) for pid in self.pinned if pid not in self.protected)), (
            "Every recorded root and owned descendant must exit; a stopped response is insufficient"
        )
        assert not self.numeric_signals, "Recovered targets must use pinned descriptors, never numeric signals"
        assert Counter(self.opened) == Counter(self.closed), "Every production-opened pidfd must close"
        for pid in self.protected:
            assert self.running(pid)
            assert all(target != pid for target, _ in self.descriptor_signals)

    def finish(self):
        for descriptor in self.pinned.values():
            try:
                self.real_send(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                self.real_close(descriptor)
        # Python 3.15 wait(timeout) also uses os.pidfd_open; teardown must retain
        # its real capability while the product's unavailable-capability case runs.
        with patch("os.pidfd_open", self.real_pidfd_open):
            for process in self.children:
                process.wait(timeout=4)
                if process.stdout is not None:
                    process.stdout.close()
        for descriptor in set(self.opened) - set(self.closed):
            try:
                self.real_close(descriptor)
            except OSError:
                pass


@pytest.fixture
def private_bus(monkeypatch, tmp_path):
    probes = []

    def make(**kwargs):
        probe = _PrivateBusProbe(monkeypatch, tmp_path / str(len(probes)), **kwargs)
        probes.append(probe)
        return probe

    yield make

    for probe in reversed(probes):
        probe.finish()


@pytest.mark.parallel_unsafe
class TestAtSpiSettingsIsolation:
    @pytest.mark.parametrize("legacy", [False, True], ids=["current", "legacy"])
    def test_launcher_settings_are_memory_only_without_changing_parent_or_app(self, private_bus, legacy):
        probe = private_bus(legacy=legacy)
        parent_before = dict(os.environ)
        process = FakeProcess()
        repository = FakeSessionRepository()
        use_case = SessionStartUseCase(
            display_server=FakeDisplayServer(), allocator=FakeDisplayAllocator(),
            atspi_bus=AtSpiBusAdapter(), process=process,
            session_repo=repository, clock=FakeClock(),
        )

        session = use_case.execute(app_command="test-app", app_args=[], wait=0.0)

        assert dict(os.environ) == parent_before
        environments = dict(probe.environments)
        assert environments["launcher"].get("GSETTINGS_BACKEND") == "memory"
        assert "GSETTINGS_BACKEND" not in environments["dbus-daemon"]
        assert "GSETTINGS_BACKEND" not in environments["registryd"]
        app_environment = next(args[2] for name, args in process.calls if name == "start")
        assert app_environment["GSETTINGS_BACKEND"] == "dconf"
        assert app_environment["DBUS_SESSION_BUS_ADDRESS"] == session.atspi_bus_address
        assert repository.load_all() == [session]
        assert sorted(probe.writes) == (
            ["string:IsEnabled", "string:ScreenReaderEnabled"] if legacy else ["string:IsEnabled"]
        )


@pytest.mark.parallel_unsafe
class TestAtSpiOwnershipRecovery:
    def test_fresh_adapter_cleans_recorded_roots_and_owned_descendant_only(self, private_bus):
        probe = private_bus()
        starter = AtSpiBusAdapter()
        result = starter.start_bus(":99")
        probe.assert_private_record(result.pid)
        unrelated = probe.real_popen(
            [sys.executable, "-c", "import time; time.sleep(120)", "accessibility-broker"],
            env={"HOME": str(probe.home), "PATH": os.environ["PATH"], "DBUS_SESSION_BUS_ADDRESS": "unix:path=/unrelated"},
        )
        probe.children.append(unrelated)
        probe.remember(unrelated.pid)
        probe.protected.add(unrelated.pid)
        recovered = AtSpiBusAdapter()
        assert recovered._managed_procs == {}

        recovered.stop_bus(result.pid)

        probe.assert_stopped()
        before = (list(probe.numeric_signals), list(probe.descriptor_signals))
        recovered.stop_bus(result.pid)
        assert before == (probe.numeric_signals, probe.descriptor_signals)
        assert probe.records(), "Successful stop must keep a current-boot retired reservation"

    def test_receipt_publication_failure_cleans_acquired_infrastructure(self, private_bus, monkeypatch):
        probe = private_bus()
        original_open = builtins.open
        original_io_open = io.open
        original_os_open = os.open
        original_replace = os.replace

        def is_owned_path(path):
            return not isinstance(path, int) and str(probe.receipts) in os.fspath(path)

        def deny_open(path, mode="r", *args, **kwargs):
            if is_owned_path(path) and len(probe.children) >= 3 and any(flag in mode for flag in "wax+"):
                raise PermissionError("private receipt publication denied")
            return original_open(path, mode, *args, **kwargs)

        def deny_io_open(path, mode="r", *args, **kwargs):
            if is_owned_path(path) and len(probe.children) >= 3 and any(flag in mode for flag in "wax+"):
                raise PermissionError("private receipt publication denied")
            return original_io_open(path, mode, *args, **kwargs)

        def deny_os_open(path, flags, *args, **kwargs):
            if is_owned_path(path) and len(probe.children) >= 3 and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                raise PermissionError("private receipt publication denied")
            return original_os_open(path, flags, *args, **kwargs)

        def deny_replace(source, target, *args, **kwargs):
            if is_owned_path(target) and len(probe.children) >= 3:
                raise PermissionError("private receipt publication denied")
            return original_replace(source, target, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", deny_open)
        monkeypatch.setattr(io, "open", deny_io_open)
        monkeypatch.setattr(os, "open", deny_os_open)
        monkeypatch.setattr(os, "replace", deny_replace)
        adapter = AtSpiBusAdapter()

        with pytest.raises((RuntimeError, OSError)):
            adapter.start_bus(":99")

        assert len(probe.children) >= 3, "The injected publication failure follows acquisition"
        assert adapter._managed_procs == {}
        assert probe.wait_for(lambda: all(not probe.running(pid) for pid in probe.pinned))

    @pytest.mark.parametrize("fault", ["corrupt", "symlink", "wrong-boot", "reused-birth", "foreign-uid", "foreign-context"])
    def test_untrusted_receipt_is_refused_without_signalling(self, private_bus, monkeypatch, fault):
        probe = private_bus()
        result = AtSpiBusAdapter().start_bus(":99")
        records = probe.assert_private_record(result.pid)
        meaningful = {path: data for path, data in records.items() if data}
        assert meaningful
        if fault == "corrupt":
            for path in meaningful:
                path.write_bytes(b"{not a valid ownership receipt")
        elif fault == "symlink":
            target = probe.root / "unrelated-metadata"
            target.write_text("unrelated, preserve me")
            path = next(iter(meaningful))
            path.unlink()
            path.symlink_to(target)
        elif fault == "foreign-uid":
            real_stat = os.stat

            def foreign_stat(path, *args, **kwargs):
                observed = real_stat(path, *args, **kwargs)
                if not isinstance(path, int) and Path(path) in records:
                    fields = list(observed)
                    fields[4] = os.getuid() + 1
                    return os.stat_result(fields)
                return observed

            monkeypatch.setattr(os, "stat", foreign_stat)
        else:
            if fault == "wrong-boot":
                old = Path("/proc/sys/kernel/random/boot_id").read_text().strip().encode()
                new = b"00000000-0000-0000-0000-000000000000"
            elif fault == "reused-birth":
                old = Path(f"/proc/{result.pid}/stat").read_text().rsplit(")", 1)[1].split()[19].encode()
                new = str(int(old) + 10000).encode()
            else:
                old, new = probe.address.encode(), b"unix:path=/unrelated-generation"
            changed = False
            for path, data in meaningful.items():
                if old in data:
                    path.write_bytes(data.replace(old, new))
                    changed = True
            assert changed, "The ownership field being challenged must exist in the published record"
        probe.numeric_signals.clear()
        probe.descriptor_signals.clear()

        with pytest.raises(RuntimeError):
            AtSpiBusAdapter().stop_bus(result.pid)

        assert probe.numeric_signals == []
        assert probe.descriptor_signals == []
        if fault == "symlink":
            assert target.read_text() == "unrelated, preserve me"

    @pytest.mark.parametrize("fault", ["unavailable", "denied"])
    def test_unavailable_safe_process_identity_prevents_success(self, private_bus, monkeypatch, fault):
        probe = private_bus()
        if fault == "unavailable":
            monkeypatch.setattr(os, "pidfd_open", None)
        else:
            def denied(pid, flags=0):
                raise PermissionError("pidfd permission denied")
            monkeypatch.setattr(os, "pidfd_open", denied)
        adapter = AtSpiBusAdapter()

        with pytest.raises(RuntimeError):
            adapter.start_bus(":99")

        assert adapter._managed_procs == {}
        assert probe.wait_for(lambda: all(not probe.running(pid) for pid in probe.pinned))

    def test_process_is_pinned_before_live_identity_validation(self, private_bus, monkeypatch):
        probe = private_bus()
        result = AtSpiBusAdapter().start_bus(":99")
        probe.assert_private_record(result.pid)
        events = []
        real_pin = os.pidfd_open
        real_io_open = io.open

        def pin(pid, flags=0):
            events.append(("pin", pid))
            return real_pin(pid, flags)

        def changed_identity(path, mode="r", *args, **kwargs):
            if not isinstance(path, int) and os.fspath(path) == f"/proc/{result.pid}/stat" and "r" in mode:
                events.append(("identity", result.pid))
                assert ("pin", result.pid) in events, "Pin the target before verifying its live identity"
                with real_io_open(path, "r") as handle:
                    original = handle.read()
                prefix, tail = original.rsplit(")", 1)
                fields = tail.split()
                fields[19] = str(int(fields[19]) + 1)
                changed = prefix + ") " + " ".join(fields)
                return io.BytesIO(changed.encode()) if "b" in mode else io.StringIO(changed)
            return real_io_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "pidfd_open", pin)
        monkeypatch.setattr(io, "open", changed_identity)
        probe.numeric_signals.clear()
        probe.descriptor_signals.clear()

        with pytest.raises(RuntimeError):
            AtSpiBusAdapter().stop_bus(result.pid)

        assert ("identity", result.pid) in events
        assert probe.numeric_signals == []
        assert probe.descriptor_signals == []

    @pytest.mark.parametrize("stubborn", ["", "registryd"], ids=["already-exited", "term-timeout"])
    def test_stop_checks_exit_and_closes_descriptors_after_escalation(self, private_bus, stubborn):
        probe = private_bus(stubborn=stubborn)
        result = AtSpiBusAdapter().start_bus(":99")
        probe.assert_private_record(result.pid)
        if not stubborn:
            for descriptor in probe.pinned.values():
                probe.real_send(descriptor, signal.SIGTERM)
            assert probe.wait_for(lambda: all(not probe.running(pid) for pid in probe.pinned))
        started = probe.real_monotonic()

        AtSpiBusAdapter().stop_bus(result.pid)

        assert probe.real_monotonic() - started < 20
        probe.assert_stopped()
        if stubborn:
            registry_pid = probe.children[2].pid
            assert (registry_pid, signal.SIGTERM) in probe.descriptor_signals
            assert (registry_pid, signal.SIGKILL) in probe.descriptor_signals
            times = {signum: timestamp for pid, signum, timestamp in probe.signal_times if pid == registry_pid}
            assert 0 <= times[signal.SIGKILL] - times[signal.SIGTERM] <= 3.5

    def test_failed_cleanup_retains_retry_evidence_and_does_not_claim_success(self, private_bus):
        probe = private_bus(stubborn="registryd")
        result = AtSpiBusAdapter().start_bus(":99")
        probe.assert_private_record(result.pid)
        probe.drop_kill = True

        with pytest.raises(RuntimeError):
            AtSpiBusAdapter().stop_bus(result.pid)

        assert probe.records(), "An uncertain survivor requires retained retry evidence"
        assert probe.running(probe.children[2].pid)
        probe.drop_kill = False
        AtSpiBusAdapter().stop_bus(result.pid)
        probe.assert_stopped()


    @pytest.mark.parametrize("retired", [False, True], ids=["active", "retired"])
    def test_same_boot_receipt_collision_preserves_previous_generation(self, private_bus, monkeypatch, retired):
        probe = private_bus()
        original = AtSpiBusAdapter().start_bus(":99")
        original_records = probe.assert_private_record(original.pid)
        if retired:
            AtSpiBusAdapter().stop_bus(original.pid)
            probe.assert_stopped()
            original_records = probe.records()
        original_children = list(probe.children)
        spawn = probe.spawn
        collisions = {}

        def spawn_with_collision(command, **kwargs):
            process = spawn(command, **kwargs)
            if command[0] == "dbus-daemon":
                indexed = [(path, data) for path, data in original_records.items() if str(original.pid) in str(path.relative_to(probe.receipts))]
                assert indexed, "Private ownership must be indexed by its public bus PID"
                for path, data in indexed:
                    relative = str(path.relative_to(probe.receipts)).replace(str(original.pid), str(process.pid))
                    target = probe.receipts / relative
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    content = data.replace(str(original.pid).encode(), str(process.pid).encode())
                    target.write_bytes(content)
                    target.chmod(0o600)
                    collisions[target] = content
            return process

        monkeypatch.setattr("aiyes.adapters.atspi_bus_adapter.subprocess.Popen", spawn_with_collision)
        adapter = AtSpiBusAdapter()

        with pytest.raises(RuntimeError):
            adapter.start_bus(":99")

        assert collisions
        assert adapter._managed_procs == {}
        for path, content in collisions.items():
            assert path.read_bytes() == content, "Collision must not overwrite existing ownership evidence"
        for path, content in original_records.items():
            assert path.read_bytes() == content
        assert probe.wait_for(lambda: all(not probe.running(child.pid) for child in probe.children[len(original_children):]))
        if not retired:
            assert all(probe.running(child.pid) for child in original_children)

    def test_contended_ownership_update_has_finite_wait_and_preserves_state(self, private_bus):
        import fcntl

        probe = private_bus()
        result = AtSpiBusAdapter().start_bus(":99")
        records = probe.assert_private_record(result.pid)
        handles = [path.open("rb") for path in records]
        for handle in handles:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = probe.real_monotonic()
        probe.numeric_signals.clear()
        probe.descriptor_signals.clear()
        try:
            with pytest.raises(RuntimeError):
                AtSpiBusAdapter().stop_bus(result.pid)
            assert probe.real_monotonic() - started <= 3.5
            assert probe.numeric_signals == []
            assert probe.descriptor_signals == []
            assert all(path.read_bytes() == data for path, data in records.items())
        finally:
            for handle in handles:
                fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()


@pytest.mark.parallel_unsafe
class TestAtSpiOwnershipPruneCompatibility:
    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real"])
    @pytest.mark.parametrize("established_global", [False, True], ids=["fresh-home", "existing-global"])
    def test_public_prune_preserves_ownership_and_active_session(
        self, monkeypatch, tmp_path, dry_run, established_global
    ):
        from aiyes.adapters.atspi_bus_adapter import _private_directory
        from aiyes.adapters.file_session_cleanup import FileSessionCleanup
        from aiyes.adapters.file_session_repository import FileSessionRepository
        from aiyes.domain.session import Session
        from aiyes.domain.use_cases.prune import PruneUseCase

        home = tmp_path / "private-home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        session_root = home / ".aieyes"
        global_root = session_root / "_global"
        global_bytes = b'{"action":"existing-global-record"}\n'
        if established_global:
            global_root.mkdir(mode=0o755, parents=True)
            global_root.chmod(0o755)
            (global_root / "operations.jsonl").write_bytes(global_bytes)
            (global_root / "existing-metadata").write_bytes(b"preserve global metadata")

        # Exercise production store creation. The public prune call below, before
        # path postconditions, must expose a misplaced ownership namespace.
        ownership_directory = _private_directory(create=True)
        sentinel = ownership_directory / "namespace-preservation.sentinel"
        sentinel.write_bytes(b"preserve private ownership metadata")
        sentinel.chmod(0o600)
        ownership_before = {
            path: path.read_bytes() for path in ownership_directory.rglob("*") if path.is_file()
        }
        repository = FileSessionRepository()
        active = Session(
            session_id="active-session", app_pid=101, app_command="private-test-app",
            app_args=(), name=None, display=":99", xvfb_pid=102,
            atspi_bus_pid=103, started_at=1.0,
        )
        stale = Session(
            session_id="deadbeef", app_pid=201, app_command="private-stale-app",
            app_args=(), name=None, display=":98", xvfb_pid=202,
            atspi_bus_pid=203, started_at=1.0,
        )
        repository.save(active)
        repository.save(stale)
        active_file = session_root / active.session_id / "session.json"
        active_before = active_file.read_bytes()
        process = FakeProcess()
        process._running.update({101: True, 102: True, 201: False, 202: False})
        prune = PruneUseCase(
            session_repo=repository, cleanup=FileSessionCleanup(),
            process=process, clock=FakeClock(now_value=500000.0),
        )

        result = prune.execute(max_age_hours=72.0, dry_run=dry_run)

        assert result.dry_run is dry_run
        assert result.pruned_count == 1
        assert result.sessions_pruned == (stale.session_id,)
        assert result.skipped_active == 1
        assert (session_root / stale.session_id).exists() is dry_run
        assert active_file.read_bytes() == active_before
        assert repository.load(active.session_id) == active
        assert all(path.read_bytes() == data for path, data in ownership_before.items())
        assert ownership_directory.parent == global_root / "atspi-ownership"
        assert global_root.is_dir()
        assert ownership_directory.stat().st_mode & 0o777 == 0o700
        assert ownership_directory.parent.stat().st_mode & 0o777 == 0o700
        if established_global:
            assert global_root.stat().st_mode & 0o777 == 0o755
            assert (global_root / "operations.jsonl").read_bytes() == global_bytes
            assert (global_root / "existing-metadata").read_bytes() == b"preserve global metadata"
