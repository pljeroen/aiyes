"""Session stop use case — stops a session and cleans up processes."""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from aiyes.domain.session import android_package_name
from aiyes.domain.use_cases.session_liveness import is_session_active
from aiyes.ports.android_app_lifecycle import AndroidAppLifecyclePort
from aiyes.ports.accessibility import AccessibilityBusPort
from aiyes.ports.display import DisplayServerPort
from aiyes.ports.marionette_profile import MarionetteProfilePort
from aiyes.ports.process import ProcessPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class StopResult:
    """Result of stopping a session."""

    status: str
    session_id: str
    errors: Tuple[str, ...] = ()


class SessionStopUseCase:
    """Stop a session: kill app, AT-SPI2 bus, and Xvfb."""

    def __init__(
        self,
        display_server: DisplayServerPort,
        atspi_bus: AccessibilityBusPort,
        process: ProcessPort,
        session_repo: SessionRepositoryPort,
        android_lifecycle: Optional[AndroidAppLifecyclePort] = None,
        marionette_profile: Optional[MarionetteProfilePort] = None,
    ) -> None:
        self._display_server = display_server
        self._atspi_bus = atspi_bus
        self._process = process
        self._session_repo = session_repo
        self._android_lifecycle = android_lifecycle
        self._marionette_profile = marionette_profile

    def _is_session_active(self, session: object) -> bool:
        """Check if a session is active based on its backend."""
        return is_session_active(session, self._process, self._android_lifecycle)

    def execute(self, session_id: Optional[str] = None) -> StopResult:
        """Execute the session stop sequence.

        If session_id is None, auto-selects if exactly one active session exists.
        Raises on zero or multiple active sessions without explicit session_id.
        Stop logic branches by backend:
        - Linux: kill app, AT-SPI bus, Xvfb
        - Android: kill app only (no AT-SPI bus or Xvfb to manage)
        """
        if session_id is None:
            sessions = self._session_repo.load_all()

            # Filter to only active sessions using backend-aware liveness
            active_sessions = [s for s in sessions if self._is_session_active(s)]

            if len(active_sessions) == 0:
                raise RuntimeError(
                    "No active sessions found. Run 'aieyes session start' to start one."
                )
            if len(active_sessions) > 1:
                ids = [s.session_id for s in active_sessions]
                raise RuntimeError(
                    f"Multiple sessions found, specify one: {ids}. "
                    "Run 'aieyes session list' to see available sessions."
                )
            session = active_sessions[0]
            session_id = session.session_id
        else:
            loaded = self._session_repo.load(session_id)
            if loaded is None:
                raise RuntimeError(
                    f"Session not found: {session_id}. "
                    "Run 'aieyes session list' to see available sessions."
                )
            session = loaded

        backend = getattr(session, "backend", "linux")

        # Kill processes, collecting errors
        errors: List[str] = []

        if backend == "android":
            serial = getattr(session, "device_serial", None)
            package_name = android_package_name(session)
            if self._android_lifecycle is None:
                errors.append(
                    "android force-stop failed: lifecycle adapter unavailable"
                )
            elif not serial or not package_name:
                errors.append(
                    "android force-stop failed: missing device/package identity"
                )
            else:
                try:
                    self._android_lifecycle.stop_app(serial, package_name)
                except Exception as exc:
                    errors.append(f"android force-stop failed: {exc}")
        else:
            try:
                self._process.stop(session.app_pid)
            except Exception as exc:
                errors.append(f"app stop failed: {exc}")

        # Linux-only cleanup: AT-SPI bus and Xvfb
        if backend != "android":
            try:
                self._atspi_bus.stop_bus(session.atspi_bus_pid)
            except Exception as exc:
                errors.append(f"atspi_bus stop failed: {exc}")

            try:
                self._display_server.stop(session.xvfb_pid)
            except Exception as exc:
                errors.append(f"display_server stop failed: {exc}")

        # AIYES-117: remove the aiyes-owned temp Marionette profile, if any.
        # No-op for non-marionette sessions and for caller-supplied -profile dirs.
        if (
            getattr(session, "marionette_port", None) is not None
            and self._marionette_profile is not None
        ):
            try:
                self._marionette_profile.cleanup(session.session_id)
            except Exception as exc:
                errors.append(f"marionette profile cleanup failed: {exc}")

        # Do NOT delete the session directory (preserved for forensics)

        status = "stopped" if not errors else "stopped_with_errors"
        return StopResult(status=status, session_id=session_id, errors=tuple(errors))
