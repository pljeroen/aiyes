"""Android app lifecycle port."""

from __future__ import annotations

from typing import Protocol


class AndroidAppLifecyclePort(Protocol):
    """Port for Android package liveness and lifecycle control."""

    def is_app_running(self, serial: str, package_name: str) -> bool:
        """Return True when the package has a running on-device process."""
        ...

    def stop_app(self, serial: str, package_name: str) -> None:
        """Stop the package on-device."""
        ...
