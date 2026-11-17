"""Compound do use case — find + action + optional verify."""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from aiyes.domain.matching import (
    android_description_refusal,
    description_matches,
    description_selector_given,
    descriptions_containing,
    name_matches,
    role_matches,
)
from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes
from aiyes.domain.use_cases.action import failed_action_reason
from aiyes.ports.accessibility_action import AccessibilityActionPort
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.clock import ClockPort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class CompoundActionResult:
    """Result from the action part of compound do.

    ``reason`` is set when status is "error" and says why the action did not
    succeed, with the same text the action use case gives for that case; it
    is None on success.
    """

    status: str
    action: str
    target: str
    reason: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class CompoundDoResult:
    """Result of a compound do operation.

    ``descriptions_containing`` and ``descriptions_containing_count`` are set
    only when a description query finds no node: up to five whole
    descriptions that contain the given text, among the nodes of the last
    poll that pass the other selectors, and how many such descriptions there
    are. They are None otherwise.
    """

    found: Optional[Node]
    action_result: Optional[CompoundActionResult]
    verify: Optional[AccessibilityTree] = None
    error: Optional[str] = None
    descriptions_containing: Optional[Tuple[str, ...]] = None
    descriptions_containing_count: Optional[int] = None


class CompoundDoUseCase:
    """Compound find + action + optional verify operation.

    Execution order: find -> action -> verify (if requested).
    If find fails, action and verify are skipped.
    """

    POLL_INTERVAL: float = 0.5

    def __init__(
        self,
        tree: AccessibilityTreePort,
        action: AccessibilityActionPort,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
        clock: ClockPort,
    ) -> None:
        self._tree = tree
        self._action = action
        self._session_repo = session_repo
        self._tree_store = tree_store
        self._clock = clock

    def execute(
        self,
        session_id: str,
        role: str,
        action_name: str,
        name_pattern: Optional[str] = None,
        verify: bool = False,
        value: Optional[str] = None,
        timeout: Optional[float] = None,
        description_pattern: Optional[str] = None,
    ) -> CompoundDoResult:
        """Execute compound find + action + optional verify.

        Args:
            session_id: Target session.
            role: Accessibility role to match (e.g. 'View', 'Button', '*').
            action_name: Action to execute on the found node (e.g. 'click').
            name_pattern: Optional substring to match against the node name.
                Matching is case-insensitive with whitespace normalization,
                so 'Home' matches 'Home\\nTab 1 of 4'.
            verify: If True, re-read the tree after the action.
            value: Optional value to pass to the action.
            timeout: Timeout in seconds for the find phase.
            description_pattern: Optional whole accessible description the
                node must have, compared with case and whitespace runs ignored
                (description_matches); None, "" or whitespace only means no
                description filter. When it is given and no node is found,
                the result lists the descriptions that contain it among the
                last poll's nodes that pass the other selectors. Refused on an
                Android session before the tree is read.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")
        refusal = android_description_refusal(session.backend, description_pattern)
        if refusal is not None:
            raise RuntimeError(refusal)
        described = description_selector_given(description_pattern)

        deadline_start = self._clock.now() if timeout is not None else None

        while True:
            # Step 1: Find (port returns AccessibilityTree domain type)
            domain_tree = self._tree.get_tree(session)
            all_nodes = flatten_nodes(domain_tree.roots)

            # Filter by role
            if role != "*":
                matching = [n for n in all_nodes if role_matches(n.role, role)]
            else:
                matching = list(all_nodes)

            # Filter by name pattern (whitespace-normalized)
            if name_pattern is not None:
                matching = [n for n in matching if name_matches(n.name, name_pattern)]

            # The nodes that pass every selector but the description; a given
            # description selector then keeps those whose whole description
            # matches.
            other_selected = matching
            if described and description_pattern is not None:
                wanted = description_pattern
                matching = [
                    n for n in matching if description_matches(n.description, wanted)
                ]

            if matching:
                found_node = matching[0]
                node_id = found_node.id

                # Reject empty/blank node IDs
                if not node_id or not node_id.strip():
                    return CompoundDoResult(
                        found=found_node,
                        action_result=None,
                        verify=None,
                        error="Found node has invalid (empty) ID",
                    )

                # Step 2: Action (pass registry from tree adapter for stable resolution)
                tree_registry = getattr(self._tree, "last_registry", None)
                action_result = self._action.do_action(
                    session, node_id, action_name, value, tree_registry
                )

                if action_result.success:
                    compound_action = CompoundActionResult(
                        status="ok", action=action_name, target=node_id
                    )
                else:
                    compound_action = CompoundActionResult(
                        status="error",
                        action=action_name,
                        target=node_id,
                        reason=failed_action_reason(
                            action_name, node_id, action_result.failure_reason
                        ),
                    )

                # Step 3: Verify (optional, port returns domain type directly)
                verify_result: Optional[AccessibilityTree] = None
                if verify:
                    verify_result = self._tree.get_tree(session)

                return CompoundDoResult(
                    found=found_node,
                    action_result=compound_action,
                    verify=verify_result,
                )

            if timeout is None:
                return _no_match(described, description_pattern, other_selected)

            assert (
                deadline_start is not None
            )  # guarded by timeout is not None check above
            elapsed = self._clock.now() - deadline_start
            if elapsed >= timeout:
                return _no_match(described, description_pattern, other_selected)

            self._clock.sleep(self.POLL_INTERVAL)


def _no_match(
    described: bool,
    description_pattern: Optional[str],
    other_selected: List[Node],
) -> CompoundDoResult:
    """The result when no node is found, listing descriptions for a
    description query whose text some candidate's description contains."""
    listed: Optional[Tuple[str, ...]] = None
    listed_count: Optional[int] = None
    if described and description_pattern is not None:
        texts, count = descriptions_containing(
            (n.description for n in other_selected), description_pattern
        )
        if count:
            listed, listed_count = texts, count
    return CompoundDoResult(
        found=None,
        action_result=None,
        verify=None,
        error="No matching node found",
        descriptions_containing=listed,
        descriptions_containing_count=listed_count,
    )
