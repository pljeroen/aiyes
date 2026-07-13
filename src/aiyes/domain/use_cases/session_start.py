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
from aiyes.ports.marionette_profile import MarionetteProfilePort
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


# AIYES-117 / DEC-A7-02: derive the marionette TCP port from the already-unique
# X display number. XDisplayAllocatorAdapter hands out a display_num unique per
# concurrent session, so 2828+display_num is distinct per session for free — no
# second allocator, no TCP-bind probe (SC-04 distinctness, the binding invariant).
_MARIONETTE_BASE_PORT = 2828


def _is_firefox(app_command: str) -> bool:
    """Return True when app_command launches Firefox (DEC-A7-03 / A-D2).

    Basename-normalized and tolerant of full paths and common Firefox binary
    variants (firefox, firefox-esr, firefox-bin, a wrapper path like
    /usr/lib/firefox/firefox) rather than a brittle bare `== "firefox"`.
    """
    base = os.path.basename(app_command).strip().lower()
    return base == "firefox" or base.startswith("firefox-")


def _extract_profile_arg(app_args: List[str]) -> Optional[str]:
    """Return a caller-supplied ``-profile``/``--profile`` path, or None.

    Pure argv inspection (no I/O). Firefox's ``-profile <dir>`` takes the profile
    directory as the following token; a bare flag with no value yields None.
    """
    for index, token in enumerate(app_args):
        if token in ("-profile", "--profile") and index + 1 < len(app_args):
            return app_args[index + 1]
    return None


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
        marionette_profile: Optional[MarionetteProfilePort] = None,
    ) -> None:
        self._display_server = display_server
        self._allocator = allocator
        self._atspi_bus = atspi_bus
        self._process = process
        self._session_repo = session_repo
        self._clock = clock
        self._marionette_profile = marionette_profile

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
        marionette: bool = False,
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
            marionette=marionette,
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

        # AIYES-120: unified failure-atomic cleanup mirroring _execute_linux. The
        # single resource acquired above (the adb-launched app_pid) is released by
        # one best-effort finally when the launch does NOT commit. app_pid is
        # always bound once the try is entered (start raised before the guard if
        # it raised at all), so no sentinel-None gate is needed.
        committed = False
        try:
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

            self._session_repo.save(session)

            committed = True
            return session
        finally:
            # Cleanup runs ONLY when the launch did not commit; the stop is
            # per-call best-effort so a failing release cannot mask the original
            # launch exception (non-masking).
            if not committed:
                try:
                    self._process.stop(app_pid)
                except Exception:
                    pass

    def _execute_linux(
        self,
        session_id: str,
        app_command: str,
        app_args: List[str],
        resolution: str,
        color_depth: int,
        wait: float,
        name: Optional[str],
        marionette: bool = False,
    ) -> Session:
        """Linux session start — Xvfb + AT-SPI + display allocation."""
        # AIYES-117: marionette is a firefox-only launch-time opt-in (DEC-A7-04).
        # Reject a non-firefox app BEFORE any process launch (no side effects,
        # no session persisted) — surfaced as status='error' at the CLI/MCP
        # boundary, matching the existing device_serial ValueError convention.
        if marionette and not _is_firefox(app_command):
            raise ValueError(
                "marionette is only supported for Firefox sessions; "
                f"app_command {app_command!r} is not Firefox"
            )

        display_num = self._allocator.allocate()
        display = f":{display_num}"

        # AIYES-117 / A10-AF-001: derive the distinct marionette port AND make
        # Firefox actually LISTEN on it. Firefox honours the listen port only via
        # the `marionette.port` profile preference — a live probe confirmed the
        # `--marionette-port` CLI arg is ignored (Firefox keeps 2828). So we
        # provision an isolated profile carrying ONLY that pref through the
        # MarionetteProfilePort (filesystem I/O stays in the adapter) and pass it
        # via `-profile`, then splice `-marionette` to enable the server.
        marionette_port: Optional[int] = None
        app_args = list(app_args)
        if marionette:
            marionette_port = _MARIONETTE_BASE_PORT + display_num
            if self._marionette_profile is None:
                raise RuntimeError(
                    "marionette requested but no MarionetteProfilePort is wired; "
                    "Firefox cannot be configured to listen on the derived port "
                    f"{marionette_port}"
                )
            existing_profile = _extract_profile_arg(app_args)
            profile_dir = self._marionette_profile.provision(
                session_id=session_id,
                port=marionette_port,
                existing_profile=existing_profile,
            )
            if existing_profile is None:
                # We created a session-scoped temp profile — point Firefox at it.
                app_args = ["-profile", profile_dir, *app_args]
            if "-marionette" not in app_args:
                app_args = ["-marionette", *app_args]

        def _cleanup_marionette_profile() -> None:
            # Failure-atomic: drop any aiyes-owned temp profile we created.
            if marionette and self._marionette_profile is not None:
                self._marionette_profile.cleanup(session_id)

        # AIYES-119: unified failure-atomic cleanup. Every resource acquired
        # inside the guarded region below (Xvfb, AT-SPI2 bus, app process) is
        # released by a single best-effort finally when the launch does NOT
        # commit. Sentinel locals record what was actually acquired so a partial
        # failure only releases what exists; the marionette temp profile is
        # released via the closure above, whose internal guard suffices.
        xvfb_pid: Optional[int] = None
        atspi_bus_pid: Optional[int] = None
        app_pid: Optional[int] = None
        committed = False
        try:
            # Step 2: Start Xvfb
            xvfb_pid = self._display_server.start(display_num, resolution, color_depth)

            # Step 2b: Configure keyboard layout for XKB extension
            self._display_server.configure_keyboard(display)

            # Step 3: Start AT-SPI2 bus
            bus_result = self._atspi_bus.start_bus(display)
            atspi_bus_pid = bus_result.pid
            atspi_bus_address = bus_result.bus_address

            # Validate bus address is usable
            if not atspi_bus_address or not atspi_bus_address.strip():
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

            app_pid = self._process.start(app_command, app_args, env)

            # Step 5: Wait after app launch
            if wait > 0:
                self._clock.sleep(wait)

            if not self._process.is_running(app_pid):
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
                marionette_port=marionette_port,
            )

            self._session_repo.save(session)

            committed = True
            return session
        finally:
            # Failure-atomic, non-masking release. Runs cleanup ONLY when the
            # launch did not commit (DEC-119-03) — on success the temp profile
            # backing the live Firefox and every running resource must persist.
            # Each release is individually best-effort (DEC-119-02) so a failing
            # release neither masks the original exception nor blocks the others,
            # in reverse-acquisition order process -> bus -> xvfb -> profile
            # (DEC-119-05), each gated on whether the resource was acquired.
            if not committed:
                if app_pid is not None:
                    try:
                        self._process.stop(app_pid)
                    except Exception:
                        pass
                if atspi_bus_pid is not None:
                    try:
                        self._atspi_bus.stop_bus(atspi_bus_pid)
                    except Exception:
                        pass
                if xvfb_pid is not None:
                    try:
                        self._display_server.stop(xvfb_pid)
                    except Exception:
                        pass
                try:
                    _cleanup_marionette_profile()
                except Exception:
                    pass
