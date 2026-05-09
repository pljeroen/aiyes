"""Display allocator port — Protocol for allocating display numbers."""

from __future__ import annotations

from typing import Protocol


class DisplayAllocatorPort(Protocol):
    """Port for allocating available display numbers."""

    def allocate(self) -> int:
        """Allocate and return an available display number."""
        ...
