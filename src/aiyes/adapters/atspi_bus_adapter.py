"""AtSpiBusAdapter — implements AccessibilityBusPort via subprocess.

Creates an isolated D-Bus session bus via dbus-daemon, then starts
at-spi-bus-launcher and at-spi2-registryd on that bus. Sets
ScreenReaderEnabled=true so AccessKit-based apps activate their
AT-SPI2 adapter. Returns the real session bus address.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Dict, List

from aiyes.domain.types import BusStartResult

_FALLBACK_LAUNCHER_PATH = "/usr/libexec/at-spi-bus-launcher"
_FALLBACK_REGISTRYD_PATH = "/usr/libexec/at-spi2-registryd"


def _resolve_launcher() -> str:
    """Resolve the at-spi-bus-launcher executable.

    Uses shutil.which to find it in PATH first, falls back to
    the well-known /usr/libexec/ location.
    """
    found = shutil.which("at-spi-bus-launcher")
    if found is not None:
        return found
    return _FALLBACK_LAUNCHER_PATH


def _resolve_registryd() -> str:
    """Resolve the at-spi2-registryd executable.

    Uses shutil.which to find it in PATH first, falls back to
    the well-known /usr/libexec/ location.
    """
    found = shutil.which("at-spi2-registryd")
    if found is not None:
        return found
    return _FALLBACK_REGISTRYD_PATH


def _terminate_and_reap(proc: subprocess.Popen) -> None:
    """Terminate a process and reap it to prevent zombies.

    Escalates to SIGKILL if the process does not exit within 3 seconds.
    """
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass


class AtSpiBusAdapter:
    """Starts and stops the AT-SPI2 accessibility bus.

    Creates an isolated D-Bus session bus, then launches
    at-spi-bus-launcher and at-spi2-registryd on it. Sets
    ScreenReaderEnabled=true for AccessKit activation.
    """

    def __init__(self) -> None:
        self._managed_procs: Dict[int, List[subprocess.Popen]] = {}

    def start_bus(self, display: str) -> BusStartResult:
        """Start isolated AT-SPI2 infrastructure for a display.

        Process architecture:
          1. dbus-daemon --session (isolated session bus)
          2. at-spi-bus-launcher (registers org.a11y.Bus)
          3. at-spi2-registryd (AT-SPI registry on the AT-SPI bus)
          4. dbus-send ScreenReaderEnabled=true (AccessKit activation gate)
          5. dbus-send IsEnabled=true (GTK/ATK apps)

        Returns BusStartResult with the dbus-daemon PID and the real
        session bus address.

        On any failure after dbus-daemon starts, all already-started
        processes are cleaned up before re-raising.
        """
        base_env = {
            "HOME": os.environ.get("HOME", "/tmp"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        xdg = os.environ.get("XDG_RUNTIME_DIR", "")
        if xdg:
            base_env["XDG_RUNTIME_DIR"] = xdg

        # Step 1: Start isolated dbus-daemon
        dbus_proc = subprocess.Popen(
            ["dbus-daemon", "--session", "--print-address", "--nofork"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=base_env,
        )

        session_bus_address = dbus_proc.stdout.readline().decode().strip()

        # Validate dbus-daemon started correctly
        if dbus_proc.poll() is not None:
            raise RuntimeError(
                f"dbus-daemon exited immediately (exit code {dbus_proc.returncode})"
            )

        if not session_bus_address or not session_bus_address.startswith("unix:"):
            # Clean up the failed dbus-daemon
            _terminate_and_reap(dbus_proc)
            raise RuntimeError(
                "dbus-daemon did not return a valid bus address: "
                f"{session_bus_address!r}"
            )

        # dbus-daemon is running — track for cleanup on partial failure
        started: List[subprocess.Popen] = [dbus_proc]
        try:
            # Step 2: Start at-spi-bus-launcher with the session bus
            launcher = _resolve_launcher()
            launcher_env = dict(base_env)
            launcher_env["DISPLAY"] = display
            launcher_env["DBUS_SESSION_BUS_ADDRESS"] = session_bus_address

            launcher_proc = subprocess.Popen(
                [launcher, "--launch-immediately", "--screen-reader=1", "--a11y=1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=launcher_env,
            )
            started.append(launcher_proc)

            time.sleep(1.0)

            # Step 3: Get the AT-SPI bus address from the launcher
            # The launcher creates a SEPARATE D-Bus instance for AT-SPI.
            # We need this address for the registryd.
            atspi_bus_result = subprocess.run(
                [
                    "dbus-send",
                    f"--bus={session_bus_address}",
                    "--dest=org.a11y.Bus",
                    "--print-reply",
                    "/org/a11y/bus",
                    "org.a11y.Bus.GetAddress",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            atspi_bus_addr = ""
            for line in atspi_bus_result.stdout.splitlines():
                line = line.strip()
                if line.startswith("string"):
                    # Extract address from: string "unix:path=..."
                    atspi_bus_addr = line.split('"')[1] if '"' in line else ""
                    break

            # Step 4: Start at-spi2-registryd with the SESSION bus address.
            # registryd discovers the AT-SPI bus via org.a11y.Bus.GetAddress()
            # on the session bus — it needs the session bus, not the AT-SPI bus.
            registryd = _resolve_registryd()
            registryd_env = dict(base_env)
            registryd_env["DBUS_SESSION_BUS_ADDRESS"] = session_bus_address

            registryd_proc = subprocess.Popen(
                [registryd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=registryd_env,
            )
            started.append(registryd_proc)

            time.sleep(0.5)

            # Step 5: Set ScreenReaderEnabled=true
            subprocess.run(
                [
                    "dbus-send",
                    f"--bus={session_bus_address}",
                    "--dest=org.a11y.Bus",
                    "--print-reply",
                    "/org/a11y/bus",
                    "org.freedesktop.DBus.Properties.Set",
                    "string:org.a11y.Status",
                    "string:ScreenReaderEnabled",
                    "variant:boolean:true",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5,
            )

            # Step 6: Set IsEnabled=true (for GTK/ATK apps)
            subprocess.run(
                [
                    "dbus-send",
                    f"--bus={session_bus_address}",
                    "--dest=org.a11y.Bus",
                    "--print-reply",
                    "/org/a11y/bus",
                    "org.freedesktop.DBus.Properties.Set",
                    "string:org.a11y.Status",
                    "string:IsEnabled",
                    "variant:boolean:true",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5,
            )
        except Exception:
            # Clean up all already-started processes in reverse order
            for proc in reversed(started):
                _terminate_and_reap(proc)
            raise

        # Track all Popen objects for cleanup
        self._managed_procs[dbus_proc.pid] = started

        return BusStartResult(
            pid=dbus_proc.pid,
            bus_address=session_bus_address,
            atspi_bus_address=atspi_bus_addr or None,
        )

    def stop_bus(self, pid: int) -> None:
        """Terminate and reap all AT-SPI2 processes associated with the given PID.

        Terminates all tracked processes (dbus-daemon, at-spi-bus-launcher,
        at-spi2-registryd) in reverse order and waits for them to exit,
        preventing zombie processes. Falls back to killing just the given
        PID if it was not tracked by start_bus().
        """
        procs = self._managed_procs.pop(pid, None)

        if procs is not None:
            for proc in reversed(procs):
                _terminate_and_reap(proc)
        else:
            # Fallback for untracked PIDs — best-effort via os.kill
            try:
                import signal

                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
