"""Native semantic scroll port."""

from __future__ import annotations

from typing import Optional, Protocol

from aiyes.domain.types import NativeScrollResult


class NativeScrollPort(Protocol):
    """Port for platform semantic scroll actions."""

    def scroll(
        self,
        session: object,
        node_id: str,
        direction: str,
        *,
        stable_id: str = "",
        bounds: Optional[tuple[int, int, int, int]] = None,
    ) -> NativeScrollResult:
        """Attempt a native semantic scroll on a selected node."""
        ...
