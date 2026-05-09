"""Clipboard port — Protocol for clipboard read/write."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from aiyes.domain.session import Session


class ClipboardPort(Protocol):
    """Port for reading and writing clipboard contents."""

    def read(self, session: "Session") -> str:
        """Read current clipboard text."""
        ...

    def write(self, session: "Session", text: str) -> None:
        """Write text to clipboard."""
        ...
