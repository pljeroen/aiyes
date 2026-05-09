"""Session start use case — orchestrates launching a new session."""

from __future__ import annotations

import os
import uuid
from typing import List, Optional

from aiyes.domain.session import Session, parse_android_package_identity
from aiyes.ports.clock import ClockPort
from aiyes.ports.display import DisplayServerPort
from aiyes.ports.display_allocator import DisplayAllocatorPort
from aiyes.ports.accessibility import AccessibilityBusPort
from aiyes.ports.process import ProcessPort
from aiyes.ports.storage import SessionRepositoryPort

_CREDENTIAL_STRIP_VARS = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NPM_TOKEN",
    "PYPI_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "MYSQL_PASSWORD",
    "SECRET_KEY",
    "DJANGO_SECRET_KEY",
    "SSH_AUTH_SOCK",
    "GPG_AGENT_INFO",
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "no_proxy",
    "NO_PROXY",
]

# S-03: Suffix patterns for credential detection (belt and suspenders).
# Any env var whose name ends with one of these suffixes will be stripped,
# in addition to the explicit list above.
_CREDENTIAL_STRIP_SUFFIXES = (
    "_TOKEN",
    "_SECRET",
    "_KEY",
    "_PASSWORD",
    "_CREDENTIAL",
    "_API_KEY",
)


def _is_credential_var(name: str) -> bool:
    """Return True if the env var name matches explicit list or suffix pattern."""
    if name in _CREDENTIAL_STRIP_VARS:
        return True
    upper = name.upper()
    return any(upper.endswith(suffix) for suffix in _CREDENTIAL_STRIP_SUFFIXES)


class SessionStartUseCase:
    """Start a new session: allocate display, launch Xvfb, AT-SPI2 bus, and app."""

    def __init__(
        self,
        display_server: DisplayServerPort,
        allocator: DisplayAllocatorPort,
        atspi_bus: AccessibilityBusPort,
        process: ProcessPort,
        session_repo: SessionRepositoryPort,
        clock: ClockPort,
    ) -> None:
        self._display_server = display_server
        self._allocator = allocator
        self._atspi_bus = atspi_bus
        self._process = process
        self._session_repo = session_repo
        self._clock = clock

    def execute(
        self,
        app_command: str,
        app_args: List[str],
        resolution: str = "1280x800",
        color_depth: int = 24,
        wait: float = 2.0,
        name: Optional[str] = None,
        backend: str = "linux",
        device_serial: Optional[str] = None,
    ) -> Session:
        """Execute the session start sequence.

        For Linux backend:
        1. Allocate a display number.
        2. Start Xvfb on that display.
        3. Start the AT-SPI2 bus.
        4. Start the target application with accessibility env vars.
        5. Wait for the configured duration after app launch.
        6. Save the session to the repository.

        For Android backend:
        1. Start the target app via process port (package/activity).
        2. Wait for the configured duration after app launch.
        3. Save the session to the repository.
        Android MUST NOT use display_server, allocator, or atspi_bus.
        """
        session_id = str(uuid.uuid4())[:8]

        if backend == "android":
            return self._execute_android(
                session_id=session_id,
                app_command=app_command,
                app_args=app_args,
                wait=wait,
                name=name,
                device_serial=device_serial,
            )

        return self._execute_linux(
            session_id=session_id,
            app_command=app_command,
            app_args=app_args,
            resolution=resolution,
            color_depth=color_depth,
            wait=wait,
            name=name,
        )

    def _execute_android(
        self,
        session_id: str,
        app_command: str,
        app_args: List[str],
        wait: float,
        name: Optional[str],
        device_serial: Optional[str],
    ) -> Session:
        """Android session start — no Xvfb, no AT-SPI, no display allocator."""
        if not device_serial:
            raise ValueError(
                "Android backend requires --device-serial "
                "(e.g., emulator-5554 or device serial from 'adb devices')"
            )

        env = os.environ.copy()
        credential_vars = [name for name in env if _is_credential_var(name)]
        for var in credential_vars:
            env.pop(var, None)

        # Start target app via process port (e.g., adb shell am start)
        app_pid = self._process.start(app_command, app_args, env)

        if wait > 0:
            self._clock.sleep(wait)

        started_at = self._clock.now()
        package_name, activity_name = parse_android_package_identity(
            app_command, app_args
        )
        session = Session(
            session_id=session_id,
            app_pid=app_pid,
            app_command=app_command,
            app_args=tuple(app_args),
            name=name,
            started_at=started_at,
            backend="android",
            device_serial=device_serial,
            package_name=package_name,
            activity_name=activity_name,
        )

        try:
            self._session_repo.save(session)
        except Exception:
            self._process.stop(app_pid)
            raise

        return session

    def _execute_linux(
        self,
        session_id: str,
        app_command: str,
        app_args: List[str],
        resolution: str,
        color_depth: int,
        wait: float,
        name: Optional[str],
    ) -> Session:
        """Linux session start — Xvfb + AT-SPI + display allocation."""
        display_num = self._allocator.allocate()
        display = f":{display_num}"

        # Step 2: Start Xvfb
        xvfb_pid = self._display_server.start(display_num, resolution, color_depth)

        # Step 2b: Configure keyboard layout for XKB extension
        self._display_server.configure_keyboard(display)

        # Step 3: Start AT-SPI2 bus
        try:
            bus_result = self._atspi_bus.start_bus(display)
        except Exception:
            self._display_server.stop(xvfb_pid)
            raise

        atspi_bus_pid = bus_result.pid
        atspi_bus_address = bus_result.bus_address

        # Validate bus address is usable
        if not atspi_bus_address or not atspi_bus_address.strip():
            self._atspi_bus.stop_bus(atspi_bus_pid)
            self._display_server.stop(xvfb_pid)
            raise RuntimeError("AT-SPI2 bus started but returned empty bus address")

        # Step 4: Start target application with full host env + a11y overrides.
        # The app needs PATH, HOME, XDG_*, etc. to function correctly.
        # Accessibility env vars are added/overridden on top of the host env.

        env = os.environ.copy()

        # R-ISO-01: Strip Wayland variables to prevent display leakage.
        # On Wayland hosts, these cause apps to connect to the real
        # compositor instead of the isolated Xvfb session.
        _wayland_strip_vars = [
            "WAYLAND_DISPLAY",
            "WAYLAND_SOCKET",
            "XDG_SESSION_TYPE",
            "GDK_BACKEND",
            "QT_QPA_PLATFORM",
            "CLUTTER_BACKEND",
            "SDL_VIDEODRIVER",
            "MOZ_ENABLE_WAYLAND",
            "ELM_DISPLAY",
            "CHROME_HEADLESS",  # R-ATSPI-03: Chromium a11y kill-switch
            "AT_SPI_BUS_ADDRESS",  # Must not leak host AT-SPI bus
        ]

        for var in _wayland_strip_vars:
            env.pop(var, None)

        # R-REM-03 + S-03: Strip credential/secret env vars to prevent leakage
        # into isolated sessions. Uses both explicit list and suffix patterns.
        credential_vars = [name for name in env if _is_credential_var(name)]
        for var in credential_vars:
            env.pop(var, None)

        # R-ISO-02 + R-ISO-03 + existing a11y overrides:
        # Force X11 backends, enable software rendering, set a11y vars.
        env.update(
            {
                # Existing: Display + Accessibility
                "DISPLAY": display,
                "GTK_MODULES": "gail:atk-bridge",
                # Qt5/6: AT-SPI bridge may fail to register under Xvfb (known upstream issue)
                "QT_ACCESSIBILITY": "1",
                "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
                # R-ATSPI-01: Firefox/GTK a11y activation
                "GNOME_ACCESSIBILITY": "1",
                # R-ATSPI-02: Chromium/Electron a11y activation
                "ACCESSIBILITY_ENABLED": "1",
                # R-ATSPI-04: LibreOffice AT-SPI via gtk3 VCL plugin
                "SAL_USE_VCLPLUGIN": "gtk3",
                # R-ATSPI-05: Java AWT non-reparenting WM fix for Xvfb
                "_JAVA_AWT_WM_NONREPARENTING": "1",
                # R-ATSPI-03: Isolated session bus — AccessKit discovers
                # the AT-SPI bus via org.a11y.Bus.GetAddress() on this bus.
                # Do NOT set AT_SPI_BUS_ADDRESS — let discovery work normally.
                "DBUS_SESSION_BUS_ADDRESS": atspi_bus_address,
                # R-ISO-02: Force X11 backend per toolkit
                "GDK_BACKEND": "x11",
                "QT_QPA_PLATFORM": "xcb",
                "SDL_VIDEODRIVER": "x11",
                "CLUTTER_BACKEND": "x11",
                "WINIT_UNIX_BACKEND": "x11",
                "ELM_DISPLAY": "x11",
                "XDG_SESSION_TYPE": "x11",
                # R-ISO-03: Software rendering for GPU-less Xvfb
                "LIBGL_ALWAYS_SOFTWARE": "1",
                "GALLIUM_DRIVER": "llvmpipe",
                "MESA_VK_WSI_PRESENT_MODE": "immediate",
                "WLR_RENDERER": "pixman",
            }
        )

        try:
            app_pid = self._process.start(app_command, app_args, env)
        except Exception:
            # Clean up Xvfb and AT-SPI2 bus on app launch failure
            self._atspi_bus.stop_bus(atspi_bus_pid)
            self._display_server.stop(xvfb_pid)
            raise

        # Step 5: Wait after app launch
        if wait > 0:
            self._clock.sleep(wait)

        if not self._process.is_running(app_pid):
            self._atspi_bus.stop_bus(atspi_bus_pid)
            self._display_server.stop(xvfb_pid)
            raise RuntimeError(
                "Application exited during startup wait; session was not created"
            )

        # Step 6: Create and save session
        started_at = self._clock.now()
        session = Session(
            session_id=session_id,
            display=display,
            app_pid=app_pid,
            app_command=app_command,
            app_args=tuple(app_args),
            atspi_bus_pid=atspi_bus_pid,
            atspi_bus_address=atspi_bus_address,
            xvfb_pid=xvfb_pid,
            name=name,
            resolution=resolution,
            color_depth=color_depth,
            started_at=started_at,
            backend="linux",
        )

        try:
            self._session_repo.save(session)
        except Exception:
            # Failure-atomic: clean up all launched processes on save failure
            self._process.stop(app_pid)
            self._atspi_bus.stop_bus(atspi_bus_pid)
            self._display_server.stop(xvfb_pid)
            raise

        return session
