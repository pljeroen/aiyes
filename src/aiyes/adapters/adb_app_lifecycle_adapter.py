"""ADB-backed Android app lifecycle adapter."""

from __future__ import annotations

import subprocess


class AdbAppLifecycleAdapter:
    """Query and stop Android packages through adb."""

    def is_app_running(self, serial: str, package_name: str) -> bool:
        """Return True when adb pidof finds at least one package process."""
        from aiyes.adapters.adb_path import resolve_adb_path

        try:
            adb = resolve_adb_path()
            result = subprocess.run(
                [adb, "-s", serial, "shell", "pidof", package_name],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (
            FileNotFoundError,
            OSError,
            subprocess.TimeoutExpired,
            RuntimeError,
        ):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    def stop_app(self, serial: str, package_name: str) -> None:
        """Force-stop a package on-device."""
        from aiyes.adapters.adb_path import resolve_adb_path

        adb = resolve_adb_path()
        subprocess.run(
            [adb, "-s", serial, "shell", "am", "force-stop", package_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
