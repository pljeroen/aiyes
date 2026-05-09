"""SystemClock adapter — implements ClockPort using time module."""

from __future__ import annotations

import time


class SystemClock:
    """Clock implementation using time.time() and time.sleep()."""

    def now(self) -> float:
        """Return current time as epoch seconds."""
        return time.time()

    def sleep(self, seconds: float) -> None:
        """Sleep for the given number of seconds."""
        time.sleep(seconds)
