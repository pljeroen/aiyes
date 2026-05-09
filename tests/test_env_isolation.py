"""Tests for environment isolation and AT-SPI toolkit support.

Requirements covered:
  R-ISO-01: Strip Wayland display variables from app environment
  R-ISO-02: Force X11 backend for all toolkits
  R-ISO-03: Software rendering environment variables
  R-ISO-04: Doctor checks for Mesa software rendering
  R-ATSPI-01: GNOME_ACCESSIBILITY=1 for Firefox/GTK a11y activation
  R-ATSPI-02: ACCESSIBILITY_ENABLED=1 for Chromium/Electron a11y activation
  R-ATSPI-03: CHROME_HEADLESS stripped to prevent Chromium a11y kill-switch
  R-ATSPI-04: SAL_USE_VCLPLUGIN=gtk3 for LibreOffice AT-SPI support
  R-ATSPI-05: _JAVA_AWT_WM_NONREPARENTING=1 (set, not stripped)
  Regression: Existing env inheritance (PATH, HOME, a11y) unaffected
"""

from __future__ import annotations

from typing import Dict, Optional

import pytest

from aiyes.domain.use_cases.session_start import SessionStartUseCase
from aiyes.domain.types import DependencyResult
from aiyes.adapters.system_dependency_check import SystemDependencyCheck

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)


def _get_app_env(
    fake_process: FakeProcess,
    monkeypatch: Optional[pytest.MonkeyPatch] = None,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Helper: execute session start and return the env dict passed to process.start()."""
    if monkeypatch and env_overrides:
        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)

    uc = SessionStartUseCase(
        display_server=FakeDisplayServer(),
        allocator=FakeDisplayAllocator(),
        atspi_bus=FakeAccessibilityBus(),
        process=fake_process,
        session_repo=FakeSessionRepository(),
        clock=FakeClock(),
    )
    uc.execute(app_command="test-app", app_args=[])

    start_calls = [c for c in fake_process.calls if c[0] == "start"]
    _, _, env = start_calls[0][1]
    assert env is not None
    return env


# ──────────────────────────────────────────────────────────────────────
# R-ISO-01: Wayland Variable Stripping
# ──────────────────────────────────────────────────────────────────────


class TestWaylandVariableStripping:
    """R-ISO-01: Wayland env vars must be stripped from app launch environment."""

    def test_wayland_display_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WAYLAND_DISPLAY must not leak into app env."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"WAYLAND_DISPLAY": "wayland-0"})
        assert "WAYLAND_DISPLAY" not in env

    def test_wayland_socket_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WAYLAND_SOCKET must not leak into app env."""
        fp = FakeProcess()
        env = _get_app_env(
            fp, monkeypatch, {"WAYLAND_SOCKET": "/run/user/1000/wayland-0"}
        )
        assert "WAYLAND_SOCKET" not in env

    def test_xdg_session_type_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """XDG_SESSION_TYPE=wayland must be replaced with x11."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"XDG_SESSION_TYPE": "wayland"})
        assert env.get("XDG_SESSION_TYPE") == "x11"

    def test_gdk_backend_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GDK_BACKEND=wayland must be replaced with x11."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"GDK_BACKEND": "wayland"})
        assert env.get("GDK_BACKEND") == "x11"

    def test_qt_qpa_platform_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """QT_QPA_PLATFORM=wayland must be replaced with xcb."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"QT_QPA_PLATFORM": "wayland"})
        assert env.get("QT_QPA_PLATFORM") == "xcb"

    def test_sdl_videodriver_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDL_VIDEODRIVER=wayland must be replaced with x11."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"SDL_VIDEODRIVER": "wayland"})
        assert env.get("SDL_VIDEODRIVER") == "x11"

    def test_clutter_backend_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLUTTER_BACKEND=wayland must be replaced with x11."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"CLUTTER_BACKEND": "wayland"})
        assert env.get("CLUTTER_BACKEND") == "x11"

    def test_moz_enable_wayland_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MOZ_ENABLE_WAYLAND must be stripped (not re-set)."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"MOZ_ENABLE_WAYLAND": "1"})
        assert "MOZ_ENABLE_WAYLAND" not in env

    def test_elm_display_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ELM_DISPLAY=wl must be replaced with x11."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"ELM_DISPLAY": "wl"})
        assert env.get("ELM_DISPLAY") == "x11"

    def test_java_awt_set_to_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_JAVA_AWT_WM_NONREPARENTING must be set to '1', not stripped.

        R-ATSPI-05: The variable fixes Java AWT window manager interaction
        in non-reparenting WMs. Xvfb has no WM at all, so this is needed
        for java-atk-wrapper to function correctly.
        """
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"_JAVA_AWT_WM_NONREPARENTING": "1"})
        assert env.get("_JAVA_AWT_WM_NONREPARENTING") == "1"

    def test_all_wayland_vars_stripped_simultaneously(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All 9 Wayland strip-list variables stripped; X11 overrides correctly set.

        R-ATSPI-05: _JAVA_AWT_WM_NONREPARENTING is no longer a strip-list
        variable — it is set to '1' via env.update() instead.
        """
        wayland_vars = {
            "WAYLAND_DISPLAY": "wayland-0",
            "WAYLAND_SOCKET": "/run/user/1000/wayland-0",
            "XDG_SESSION_TYPE": "wayland",
            "GDK_BACKEND": "wayland",
            "QT_QPA_PLATFORM": "wayland",
            "CLUTTER_BACKEND": "wayland",
            "SDL_VIDEODRIVER": "wayland",
            "MOZ_ENABLE_WAYLAND": "1",
            "ELM_DISPLAY": "wl",
            "_JAVA_AWT_WM_NONREPARENTING": "0",
        }
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, wayland_vars)

        # Pure-strip vars must be absent
        assert "WAYLAND_DISPLAY" not in env
        assert "WAYLAND_SOCKET" not in env
        assert "MOZ_ENABLE_WAYLAND" not in env

        # R-ATSPI-05: _JAVA_AWT_WM_NONREPARENTING must be PRESENT with value "1"
        # (overrides the host value of "0" via env.update())
        assert env.get("_JAVA_AWT_WM_NONREPARENTING") == "1"

        # Strip-then-set vars must have X11 values
        assert env.get("XDG_SESSION_TYPE") == "x11"
        assert env.get("GDK_BACKEND") == "x11"
        assert env.get("QT_QPA_PLATFORM") == "xcb"
        assert env.get("CLUTTER_BACKEND") == "x11"
        assert env.get("SDL_VIDEODRIVER") == "x11"
        assert env.get("ELM_DISPLAY") == "x11"

    def test_strip_is_noop_when_vars_absent(self) -> None:
        """Stripping absent vars must not raise or crash."""
        fp = FakeProcess()
        # No monkeypatch: no Wayland vars in env.
        env = _get_app_env(fp)
        # Session started normally — env has at least the a11y vars
        assert env.get("DISPLAY") == ":99"


# ──────────────────────────────────────────────────────────────────────
# R-ISO-02: Force X11 Backend for All Toolkits
# ──────────────────────────────────────────────────────────────────────


class TestX11BackendForcing:
    """R-ISO-02: X11 backend vars must be set regardless of host env."""

    def test_winit_backend_set(self) -> None:
        """WINIT_UNIX_BACKEND=x11 must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("WINIT_UNIX_BACKEND") == "x11"

    def test_gdk_backend_set(self) -> None:
        """GDK_BACKEND=x11 must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("GDK_BACKEND") == "x11"

    def test_qt_qpa_platform_set(self) -> None:
        """QT_QPA_PLATFORM=xcb must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("QT_QPA_PLATFORM") == "xcb"

    def test_sdl_videodriver_set(self) -> None:
        """SDL_VIDEODRIVER=x11 must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("SDL_VIDEODRIVER") == "x11"

    def test_clutter_backend_set(self) -> None:
        """CLUTTER_BACKEND=x11 must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("CLUTTER_BACKEND") == "x11"

    def test_xdg_session_type_set(self) -> None:
        """XDG_SESSION_TYPE=x11 must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("XDG_SESSION_TYPE") == "x11"

    def test_elm_display_set(self) -> None:
        """ELM_DISPLAY=x11 must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("ELM_DISPLAY") == "x11"


# ──────────────────────────────────────────────────────────────────────
# R-ISO-03: Software Rendering Variables
# ──────────────────────────────────────────────────────────────────────


class TestSoftwareRendering:
    """R-ISO-03: Software rendering vars must be set for GPU-less Xvfb."""

    def test_libgl_always_software_set(self) -> None:
        """LIBGL_ALWAYS_SOFTWARE=1 must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("LIBGL_ALWAYS_SOFTWARE") == "1"

    def test_gallium_driver_set(self) -> None:
        """GALLIUM_DRIVER=llvmpipe must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("GALLIUM_DRIVER") == "llvmpipe"

    def test_mesa_vk_wsi_present_mode_set(self) -> None:
        """MESA_VK_WSI_PRESENT_MODE=immediate must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("MESA_VK_WSI_PRESENT_MODE") == "immediate"

    def test_wlr_renderer_set(self) -> None:
        """WLR_RENDERER=pixman must be set."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("WLR_RENDERER") == "pixman"


# ──────────────────────────────────────────────────────────────────────
# R-ISO-04: Doctor Checks for Mesa Software Rendering
# ──────────────────────────────────────────────────────────────────────


class TestDoctorMesaChecks:
    """R-ISO-04: Doctor must include Mesa software rendering checks."""

    def test_mesa_sw_rendering_in_check_all(self) -> None:
        """mesa-software-rendering must appear in check_all() results."""
        checker = SystemDependencyCheck()
        results = checker.check_all()
        names = [r.name for r in results]
        assert "mesa-software-rendering" in names

    def test_mesa_vulkan_sw_in_check_all(self) -> None:
        """mesa-vulkan-software must appear in check_all() results."""
        checker = SystemDependencyCheck()
        results = checker.check_all()
        names = [r.name for r in results]
        assert "mesa-vulkan-software" in names

    def test_mesa_sw_check_returns_dependency_result(self) -> None:
        """mesa-software-rendering check returns a DependencyResult."""
        checker = SystemDependencyCheck()
        result = checker.check("mesa-software-rendering")
        assert isinstance(result, DependencyResult)
        assert result.name == "mesa-software-rendering"
        assert result.status in ("pass", "warn", "fail")

    def test_mesa_vulkan_check_returns_dependency_result(self) -> None:
        """mesa-vulkan-software check returns a DependencyResult."""
        checker = SystemDependencyCheck()
        result = checker.check("mesa-vulkan-software")
        assert isinstance(result, DependencyResult)
        assert result.name == "mesa-vulkan-software"
        assert result.status in ("pass", "warn", "fail")


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-01: Firefox / GTK Accessibility Activation
# ──────────────────────────────────────────────────────────────────────


class TestFirefoxA11yEnv:
    """R-ATSPI-01: GNOME_ACCESSIBILITY=1 must be set in app launch environment.

    Firefox uses a three-layer fallback for accessibility activation.
    In an isolated Xvfb+dbus-daemon session, GSettings/dconf is unavailable,
    so Firefox falls to the env var check. If GNOME_ACCESSIBILITY is not set,
    Firefox may permanently disable accessibility at startup.
    """

    def test_gnome_accessibility_set(self) -> None:
        """GNOME_ACCESSIBILITY='1' must be present in app env."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("GNOME_ACCESSIBILITY") == "1"

    def test_gnome_accessibility_set_overrides_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GNOME_ACCESSIBILITY='1' even if host had GNOME_ACCESSIBILITY='0'."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"GNOME_ACCESSIBILITY": "0"})
        assert env.get("GNOME_ACCESSIBILITY") == "1"


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-02: Chromium / Electron Accessibility Activation
# ──────────────────────────────────────────────────────────────────────


class TestChromiumA11yEnv:
    """R-ATSPI-02: ACCESSIBILITY_ENABLED=1 must be set in app launch environment.

    Chromium checks env vars first (before D-Bus) in atk_util_auralinux.cc.
    ACCESSIBILITY_ENABLED=1 is the fastest and most reliable activation path
    for Chromium and all Electron-based apps.
    """

    def test_accessibility_enabled_set(self) -> None:
        """ACCESSIBILITY_ENABLED='1' must be present in app env."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("ACCESSIBILITY_ENABLED") == "1"


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-03: Chromium Headless Kill-Switch Stripping
# ──────────────────────────────────────────────────────────────────────


class TestChromiumHeadlessStrip:
    """R-ATSPI-03: CHROME_HEADLESS must be stripped from app launch environment.

    Chromium checks CHROME_HEADLESS=='1' and immediately disables accessibility
    if found, bypassing all other activation checks. In CI environments,
    CHROME_HEADLESS=1 is commonly set in the host environment and would leak
    into isolated sessions via os.environ.copy().
    """

    def test_chrome_headless_stripped_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CHROME_HEADLESS must not leak into app env when host has it set."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"CHROME_HEADLESS": "1"})
        assert "CHROME_HEADLESS" not in env

    def test_chrome_headless_absent_when_not_set(self) -> None:
        """CHROME_HEADLESS must not be in app env when host does not have it."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert "CHROME_HEADLESS" not in env

    def test_chrome_headless_strip_no_raise_when_absent(self) -> None:
        """Session start must not raise when CHROME_HEADLESS is absent from host."""
        fp = FakeProcess()
        # No exception means success — env.pop(var, None) handles absence
        env = _get_app_env(fp)
        assert env.get("DISPLAY") == ":99"


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-04: LibreOffice VCL Plugin Selection
# ──────────────────────────────────────────────────────────────────────


class TestLibreOfficeA11yEnv:
    """R-ATSPI-04: SAL_USE_VCLPLUGIN=gtk3 must be set in app launch environment.

    LibreOffice selects its VCL plugin at startup. The gtk3 plugin loads
    the GTK3 backend with ATK + atk-adaptor for full AT-SPI2 support.
    """

    def test_sal_use_vclplugin_set(self) -> None:
        """SAL_USE_VCLPLUGIN='gtk3' must be present in app env."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("SAL_USE_VCLPLUGIN") == "gtk3"


# ──────────────────────────────────────────────────────────────────────
# R-ATSPI-05: Java AWT WM Non-Reparenting
# ──────────────────────────────────────────────────────────────────────


class TestJavaAwtEnv:
    """R-ATSPI-05: _JAVA_AWT_WM_NONREPARENTING=1 must be set (not stripped).

    The variable fixes Java AWT window manager interaction issues in
    non-reparenting window managers. Xvfb has no window manager at all,
    making this fix necessary for Java Swing apps and java-atk-wrapper.
    """

    def test_java_awt_wm_nonreparenting_set(self) -> None:
        """_JAVA_AWT_WM_NONREPARENTING='1' must be present in app env."""
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("_JAVA_AWT_WM_NONREPARENTING") == "1"

    def test_java_awt_wm_nonreparenting_set_overrides_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_JAVA_AWT_WM_NONREPARENTING='1' even if host had value '0'."""
        fp = FakeProcess()
        env = _get_app_env(fp, monkeypatch, {"_JAVA_AWT_WM_NONREPARENTING": "0"})
        assert env.get("_JAVA_AWT_WM_NONREPARENTING") == "1"


# ──────────────────────────────────────────────────────────────────────
# Regression Guards
# ──────────────────────────────────────────────────────────────────────


class TestEnvIsolationRegression:
    """Regression: existing env behaviour must be preserved."""

    def test_existing_a11y_vars_still_present(self) -> None:
        """Regression: DISPLAY, GTK_MODULES, QT_ACCESSIBILITY etc. still set."""
        fp = FakeProcess()
        env = _get_app_env(fp)

        assert env.get("DISPLAY") == ":99"
        assert env.get("GTK_MODULES") == "gail:atk-bridge"
        assert env.get("QT_ACCESSIBILITY") == "1"
        assert env.get("QT_LINUX_ACCESSIBILITY_ALWAYS_ON") == "1"
        # AT_SPI_BUS_ADDRESS must NOT be set — AccessKit discovers via D-Bus
        assert "AT_SPI_BUS_ADDRESS" not in env

    def test_host_path_still_inherited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: PATH from host env passes through."""
        monkeypatch.setenv("PATH", "/usr/bin:/usr/local/bin")
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("PATH") == "/usr/bin:/usr/local/bin"

    def test_host_home_still_inherited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: HOME from host env passes through."""
        monkeypatch.setenv("HOME", "/home/testuser")
        fp = FakeProcess()
        env = _get_app_env(fp)
        assert env.get("HOME") == "/home/testuser"
