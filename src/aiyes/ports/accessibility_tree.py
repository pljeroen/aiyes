"""Accessibility tree port — Protocol for reading the accessibility tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aiyes.domain.tree import AccessibilityTree

if TYPE_CHECKING:
    from aiyes.domain.session import Session


class AccessibilityTreePort(Protocol):
    """Port for reading the accessibility tree."""

    def get_tree(self, session: "Session") -> AccessibilityTree:
        """Get the accessibility tree for the given session."""
        ...
