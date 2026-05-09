"""Clock port — Protocol for time operations."""

from __future__ import annotations

from typing import Protocol


class ClockPort(Protocol):
    """Port for time operations (sleep, now)."""

    def sleep(self, seconds: float) -> None:
        """Sleep for the given number of seconds."""
        ...

    def now(self) -> float:
        """Return current time as a float (epoch seconds)."""
        ...
