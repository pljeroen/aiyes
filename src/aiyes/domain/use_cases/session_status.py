"""Session status use case — lightweight app-alive + foreground check.

No tree dump: only PID checks and window/activity checks.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.domain.session import android_package_name
from aiyes.ports.adb_activity import AdbActivityQueryPort
from aiyes.ports.android_app_lifecycle import AndroidAppLifecyclePort
from aiyes.ports.process import ProcessPort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.window_query import WindowQueryPort


@dataclasses.dataclass(frozen=True)
class SessionStatusResult:
    """Result of a session status check."""

    app_alive: bool
    app_foreground: bool
    display_alive: bool
    # AIYES-117 (DEC-A7-05 / C-STATESURFACE): per-session marionette runtime state,
    # mirroring session_capabilities. Appended, defaulted (None/False when the
    # session was not marionette-launched).
    marionette_enabled: bool = False
    marionette_port: Optional[int] = None


class SessionStatusUseCase:
    """Check session liveness without performing a full tree dump."""

    def __init__(
        self,
        session_repo: SessionRepositoryPort,
        process: ProcessPort,
        window_query: WindowQueryPort,
        adb_activity: AdbActivityQueryPort,
        android_lifecycle: Optional[AndroidAppLifecyclePort] = None,
    ) -> None:
        self._session_repo = session_repo
        self._process = process
        self._window_query = window_query
        self._adb_activity = adb_activity
        self._android_lifecycle = android_lifecycle

    def execute(self, session_id: str) -> SessionStatusResult:
        """Check app_alive, app_foreground, display_alive for a session."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        backend = getattr(session, "backend", "linux")

        if backend == "android" and self._android_lifecycle is not None:
            serial = getattr(session, "device_serial", None)
            package_name = android_package_name(session)
            app_alive = bool(
                serial
                and package_name
                and self._android_lifecycle.is_app_running(serial, package_name)
            )
        else:
            app_alive = self._process.is_running(session.app_pid)

        # display_alive: Linux checks xvfb_pid, Android always True
        if backend == "android":
            display_alive = True
        else:
            display_alive = self._process.is_running(session.xvfb_pid)

        # app_foreground: platform-specific check
        app_foreground = False
        if backend == "android":
            app_foreground = self._check_android_foreground(session)
        else:
            app_foreground = self._check_linux_foreground(session)

        marionette_port = getattr(session, "marionette_port", None)
        return SessionStatusResult(
            app_alive=app_alive,
            app_foreground=app_foreground,
            display_alive=display_alive,
            marionette_enabled=marionette_port is not None,
            marionette_port=marionette_port,
        )

    def _check_linux_foreground(self, session: object) -> bool:
        """Check if the app is in the foreground on Linux via window query."""
        display = getattr(session, "display", "")
        app_pid = getattr(session, "app_pid", 0)
        if not display or not app_pid:
            return False

        try:
            window_id = self._window_query.get_active_window_id(display)
            if window_id is None:
                return False
            window_pid = self._window_query.get_window_pid(display, window_id)
            if window_pid is None:
                return False
            return window_pid == app_pid
        except (RuntimeError, OSError, ValueError):
            return False

    def _check_android_foreground(self, session: object) -> bool:
        """Check if the app is in the foreground on Android via adb."""
        serial = getattr(session, "device_serial", None)
        app_command = getattr(session, "app_command", "")
        if not serial or not app_command:
            return False

        try:
            resumed = self._adb_activity.get_resumed_activity(serial)
            if resumed is None:
                return False
            # Extract package name from app_command for comparison
            # app_command can be "com.example.app/.MainActivity" or just "com.example.app"
            package = app_command.split("/")[0]
            return resumed.split("/")[0] == package
        except (RuntimeError, OSError, ValueError):
            return False
