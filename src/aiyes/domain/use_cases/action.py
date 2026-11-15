"""Action use case — execute AT-SPI2 actions on accessibility nodes."""

from __future__ import annotations

import dataclasses
from typing import Optional, Tuple

from aiyes.domain.tree import AccessibilityTree, flatten_nodes
from aiyes.domain.types import ACTION_DECLINED, ACTION_NOT_ENABLED
from aiyes.ports.accessibility_action import AccessibilityActionPort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


def failed_action_reason(
    action_name: str, node_id: str, failure_reason: Optional[str]
) -> str:
    """Describe why an action on a node did not succeed.

    ``failure_reason`` is the action port's reason: a node that is not
    enabled (the action was not sent), an action the application declined,
    or no known reason, which is reported as the action not being available.
    """
    if failure_reason == ACTION_NOT_ENABLED:
        return f"Action '{action_name}' not sent: node '{node_id}' is not enabled"
    if failure_reason == ACTION_DECLINED:
        return (
            f"Action '{action_name}' on node '{node_id}' was declined by the "
            "application"
        )
    return f"Action '{action_name}' not available on node '{node_id}'"


@dataclasses.dataclass(frozen=True)
class ActionResult:
    """Result of an action execution."""

    status: str
    action: str
    target: str
    reason: Optional[str] = None
    available_actions: Optional[Tuple[str, ...]] = None
    node_value: Optional[str] = None
    node_states: Optional[Tuple[str, ...]] = None
    action_method: Optional[str] = None

    @property
    def success(self) -> bool:
        """Whether the action succeeded (status == 'ok')."""
        return self.status == "ok"


class ActionUseCase:
    """Execute an AT-SPI2 accessibility action on a node."""

    def __init__(
        self,
        action: AccessibilityActionPort,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
    ) -> None:
        self._action = action
        self._session_repo = session_repo
        self._tree_store = tree_store

    def execute(
        self,
        session_id: str,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
    ) -> ActionResult:
        """Execute an action on a node.

        Returns ActionResult with status="ok" on success,
        or status="error" with reason and available_actions on semantic failure.
        The reason says whether the node is not enabled, the application
        declined the action, or the action is not available (see
        failed_action_reason).
        Raises on system errors (no session, invalid node_id, empty node_id).
        """
        # Reject empty/blank node_id before any lookup
        if not node_id or not node_id.strip():
            raise RuntimeError("Invalid node_id: must be non-empty")

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # Validate node_id against stored tree when available (fail closed)
        stored = self._tree_store.load_tree(session_id)
        registry = None
        if stored is not None:
            # StoredTree.tree is an AccessibilityTree domain object
            tree: AccessibilityTree = stored.tree  # type: ignore[assignment]
            all_nodes = flatten_nodes(tree.roots)
            known_ids = {n.id for n in all_nodes}
            if node_id not in known_ids:
                raise RuntimeError(
                    f"Unknown node_id: {node_id!r} not found in stored tree"
                )
            # Pass persisted registry for stable node resolution
            registry = stored.registry

        result = self._action.do_action(session, node_id, action_name, value, registry)

        if result.success:
            return ActionResult(
                status="ok",
                action=action_name,
                target=node_id,
                node_value=result.node_value,
                node_states=result.node_states,
                action_method=result.action_method,
            )
        else:
            return ActionResult(
                status="error",
                action=action_name,
                target=node_id,
                reason=failed_action_reason(
                    action_name, node_id, result.failure_reason
                ),
                available_actions=tuple(result.available_actions),
                action_method=result.action_method,
            )
