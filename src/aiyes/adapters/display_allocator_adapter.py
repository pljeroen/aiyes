"""XDisplayAllocatorAdapter — implements DisplayAllocatorPort via socket probe.

No subprocess usage. Display availability is determined by checking for
X socket files and lock files.
"""

from __future__ import annotations

import os


class XDisplayAllocatorAdapter:
    """Allocates an available X display number by probing sockets/lock files."""

    def allocate(self) -> int:
        """Find and return an available display number (>= 1)."""
        for display_num in range(1, 100):
            socket_path = f"/tmp/.X11-unix/X{display_num}"
            lock_path = f"/tmp/.X{display_num}-lock"
            if not os.path.exists(socket_path) and not os.path.exists(lock_path):
                return display_num
        raise RuntimeError("No available display numbers found (1-99)")
