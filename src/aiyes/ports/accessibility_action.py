"""Accessibility action port — Protocol for executing accessibility actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol

from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.types import ActionPortResult

if TYPE_CHECKING:
    from aiyes.domain.session import Session


class AccessibilityActionPort(Protocol):
    """Port for executing accessibility actions on nodes."""

    def do_action(
        self,
        session: "Session",
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[NodeIdRegistry] = None,
    ) -> ActionPortResult:
        """Execute an action on a node. Returns ActionPortResult.

        When registry is provided, the adapter uses the persisted registry
        to resolve node_id -> (role, name, path) and walks the live tree
        using the stored path for stable node resolution.
        """
        ...
