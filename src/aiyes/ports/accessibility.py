"""Accessibility bus port — Protocol for AT-SPI2 bus management."""

from __future__ import annotations

from typing import Protocol

from aiyes.domain.types import BusStartResult


class AccessibilityBusPort(Protocol):
    """Port for managing the AT-SPI2 accessibility bus."""

    def start_bus(self, display: str) -> BusStartResult:
        """Start AT-SPI2 bus. Returns BusStartResult with pid and bus_address."""
        ...

    def stop_bus(self, pid: int) -> None:
        """Stop the AT-SPI2 bus by PID."""
        ...
