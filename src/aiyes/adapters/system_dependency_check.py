"""SystemDependencyCheck — implements DependencyCheckPort.

Checks system dependencies using shutil.which() and import checks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import List

from aiyes.domain.types import DependencyResult


_FALLBACK_LAUNCHER_PATH = "/usr/libexec/at-spi-bus-launcher"

# Mapping of dependency name to the executable or check type
_EXECUTABLE_DEPS = {
    "xvfb": "Xvfb",
    "xdotool": "xdotool",
    "xclip": "xclip",
    "python3-gi": "python3",
    "gir1.2-atspi-2.0": "python3",
    "adb": "adb",
}

_ALL_DEPS = [
    "xvfb",
    "screenshot_tool",
    "xdotool",
    "xclip",
    "at-spi2-core",
    "python3-gi",
    "gir1.2-atspi-2.0",
    "mesa-software-rendering",
    "mesa-vulkan-software",
    "adb",
    "android_device",
    "imagemagick",
]


class SystemDependencyCheck:
    """Checks system dependency availability."""

    def check(self, name: str) -> DependencyResult:
        """Check a single dependency. Returns DependencyResult."""
        if name == "python3-gi":
            return self._check_gi()

        if name == "gir1.2-atspi-2.0":
            return self._check_atspi_gir()

        if name == "at-spi2-core":
            return self._check_atspi_launcher()

        if name == "screenshot_tool":
            return self._check_screenshot_tool()

        if name == "mesa-software-rendering":
            return self._check_mesa_sw()

        if name == "mesa-vulkan-software":
            return self._check_mesa_vulkan()

        if name == "android_device":
            return self._check_android_device()

        if name in ("imagemagick", "convert"):
            return self._check_imagemagick()

        if name in _EXECUTABLE_DEPS:
            executable = _EXECUTABLE_DEPS[name]
            if name == "adb":
                from aiyes.adapters.adb_path import resolve_adb_path

                try:
                    path = resolve_adb_path()
                except RuntimeError:
                    return DependencyResult(
                        name=name,
                        status="fail",
                        message=f"{executable} not found in PATH",
                    )
                return DependencyResult(
                    name=name,
                    status="pass",
                    message=f"found: {path}",
                )
            found_path = shutil.which(executable)
            if found_path is not None:
                return DependencyResult(
                    name=name,
                    status="pass",
                    message=f"found: {found_path}",
                )
            return DependencyResult(
                name=name,
                status="fail",
                message=f"{executable} not found in PATH",
            )

        return DependencyResult(
            name=name,
            status="fail",
            message=f"Unknown dependency: {name}",
        )

    def check_all(self) -> List[DependencyResult]:
        """Check all mandatory dependencies."""
        return [self.check(name) for name in _ALL_DEPS]

    def _check_atspi_launcher(self) -> DependencyResult:
        """Check for at-spi-bus-launcher with fallback to /usr/libexec/.

        Matches the runtime resolution in AtSpiBusAdapter._resolve_launcher().
        """
        found = shutil.which("at-spi-bus-launcher")
        if found is not None:
            return DependencyResult(
                name="at-spi2-core",
                status="pass",
                message=f"found: {found}",
            )
        if os.path.isfile(_FALLBACK_LAUNCHER_PATH) and os.access(
            _FALLBACK_LAUNCHER_PATH, os.X_OK
        ):
            return DependencyResult(
                name="at-spi2-core",
                status="pass",
                message=f"found: {_FALLBACK_LAUNCHER_PATH}",
            )
        return DependencyResult(
            name="at-spi2-core",
            status="fail",
            message="at-spi-bus-launcher not found in PATH or /usr/libexec/",
        )

    def _check_screenshot_tool(self) -> DependencyResult:
        """Check for screenshot capability: scrot or imagemagick import."""
        scrot_path = shutil.which("scrot")
        if scrot_path is not None:
            return DependencyResult(
                name="screenshot_tool",
                status="pass",
                message=f"found: {scrot_path}",
            )
        import_path = shutil.which("import")
        if import_path is not None:
            return DependencyResult(
                name="screenshot_tool",
                status="pass",
                message=f"found: {import_path}",
            )
        return DependencyResult(
            name="screenshot_tool",
            status="fail",
            message="Neither scrot nor imagemagick import found in PATH",
        )

    def _check_gi(self) -> DependencyResult:
        """Check if gi (PyGObject) is importable.

        Uses shutil.which as a prerequisite check for the Python runtime,
        then verifies the gi module is importable.
        """
        if shutil.which("python3") is None:
            return DependencyResult(
                name="python3-gi",
                status="fail",
                message="python3 not found in PATH",
            )
        try:
            import gi  # noqa: F401

            return DependencyResult(
                name="python3-gi",
                status="pass",
                message="gi module importable",
            )
        except ImportError:
            return DependencyResult(
                name="python3-gi",
                status="fail",
                message="gi module not importable; install python3-gi",
            )

    def _check_atspi_gir(self) -> DependencyResult:
        """Check if gi.repository.Atspi is available.

        Uses shutil.which as a prerequisite check for the Python runtime,
        then verifies the Atspi GIR binding.
        """
        if shutil.which("python3") is None:
            return DependencyResult(
                name="gir1.2-atspi-2.0",
                status="fail",
                message="python3 not found in PATH",
            )
        try:
            import gi

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: F401

            return DependencyResult(
                name="gir1.2-atspi-2.0",
                status="pass",
                message="Atspi GIR binding available",
            )
        except (ImportError, ValueError) as exc:
            return DependencyResult(
                name="gir1.2-atspi-2.0",
                status="fail",
                message=f"Atspi GIR binding not available: {exc}",
            )

    def _check_mesa_sw(self) -> DependencyResult:
        """Check Mesa software rendering (llvmpipe) availability via glxinfo.

        R-ISO-04: Uses glxinfo with LIBGL_ALWAYS_SOFTWARE=1 to verify
        that Mesa software rendering is functional.
        """
        name = "mesa-software-rendering"
        glxinfo_path = shutil.which("glxinfo")
        if glxinfo_path is None:
            return DependencyResult(
                name=name,
                status="warn",
                message=("glxinfo not installed, cannot verify; install mesa-demos"),
            )
        try:
            result = subprocess.run(
                [glxinfo_path],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "LIBGL_ALWAYS_SOFTWARE": "1",
                },
            )
            output = result.stdout.lower()
            if "llvmpipe" in output or "softpipe" in output:
                return DependencyResult(
                    name=name,
                    status="pass",
                    message="Mesa software rendering available",
                )
            return DependencyResult(
                name=name,
                status="fail",
                message=(
                    "Mesa software rendering (llvmpipe) not available; "
                    "install mesa-dri-drivers"
                ),
            )
        except (subprocess.TimeoutExpired, OSError):
            return DependencyResult(
                name=name,
                status="fail",
                message=(
                    "Mesa software rendering (llvmpipe) not available; "
                    "install mesa-dri-drivers"
                ),
            )

    def _check_android_device(self) -> DependencyResult:
        """Check for attached Android device/emulator via adb devices.

        Requires adb to be in PATH. If adb is not found, returns fail.
        If adb is found but no device is attached, returns warn.
        """
        from aiyes.adapters.adb_path import resolve_adb_path

        try:
            adb_path = resolve_adb_path()
        except RuntimeError:
            return DependencyResult(
                name="android_device",
                status="fail",
                message="adb not found in PATH; cannot check for devices",
            )
        try:
            result = subprocess.run(
                [adb_path, "devices"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = result.stdout.strip().splitlines()
            # First line is "List of devices attached", subsequent lines are devices
            device_lines = [
                line for line in lines[1:] if line.strip() and "\tdevice" in line
            ]
            if device_lines:
                return DependencyResult(
                    name="android_device",
                    status="pass",
                    message=f"{len(device_lines)} device(s) attached",
                )
            return DependencyResult(
                name="android_device",
                status="warn",
                message="No Android device/emulator attached",
            )
        except (subprocess.TimeoutExpired, OSError):
            return DependencyResult(
                name="android_device",
                status="fail",
                message="Failed to run adb devices",
            )

    def _check_imagemagick(self) -> DependencyResult:
        """Check for ImageMagick: prefer `magick` (IM7), fall back to `convert` (IM6).

        ImageMagick 7 deprecates the standalone `convert` command in favour of
        `magick`.  We report the result under the name 'imagemagick' so the
        doctor output is tool-agnostic.
        """
        magick_path = shutil.which("magick")
        if magick_path is not None:
            return DependencyResult(
                name="imagemagick",
                status="pass",
                message=f"found: {magick_path}",
            )
        convert_path = shutil.which("convert")
        if convert_path is not None:
            return DependencyResult(
                name="imagemagick",
                status="pass",
                message=f"found: {convert_path} (legacy; consider upgrading to ImageMagick 7)",
            )
        return DependencyResult(
            name="imagemagick",
            status="fail",
            message="Neither magick nor convert found in PATH; install ImageMagick",
        )

    def _check_mesa_vulkan(self) -> DependencyResult:
        """Check Mesa Vulkan software rendering (lavapipe) availability.

        R-ISO-04: Runs vulkaninfo with LIBGL_ALWAYS_SOFTWARE=1 and checks
        for llvmpipe/lavapipe in the output. If vulkaninfo is not installed,
        returns a warn status with install hint.
        """
        vulkaninfo_path = shutil.which("vulkaninfo")
        if vulkaninfo_path is None:
            return DependencyResult(
                name="mesa-vulkan-software",
                status="warn",
                message=(
                    "vulkaninfo not installed, cannot verify; install vulkan-tools"
                ),
            )
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "LIBGL_ALWAYS_SOFTWARE": "1",
                },
            )
            output = result.stdout.lower()
            if "llvmpipe" in output or "lavapipe" in output:
                return DependencyResult(
                    name="mesa-vulkan-software",
                    status="pass",
                    message="Mesa Vulkan software rendering (lavapipe) available",
                )
            return DependencyResult(
                name="mesa-vulkan-software",
                status="fail",
                message=(
                    "Mesa Vulkan software rendering (lavapipe) not available; "
                    "install mesa-vulkan-drivers"
                ),
            )
        except (subprocess.TimeoutExpired, OSError):
            return DependencyResult(
                name="mesa-vulkan-software",
                status="fail",
                message=(
                    "Mesa Vulkan software rendering (lavapipe) not available; "
                    "install mesa-vulkan-drivers"
                ),
            )
