"""Tree store port — Protocol for persisting accessibility trees."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol

from aiyes.domain.types import StoredTree

if TYPE_CHECKING:
    from aiyes.domain.node_id import NodeIdRegistry
    from aiyes.domain.tree import AccessibilityTree


class TreeStorePort(Protocol):
    """Port for persisting accessibility trees and node ID registries."""

    def save_tree(
        self,
        session_id: str,
        tree: AccessibilityTree,
        node_id_registry: Optional[NodeIdRegistry] = None,
    ) -> None:
        """Save tree and node ID registry for a session."""
        ...

    def load_tree(self, session_id: str) -> Optional[StoredTree]:
        """Load tree data for a session. Returns StoredTree or None."""
        ...
