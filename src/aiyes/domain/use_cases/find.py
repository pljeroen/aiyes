"""Find use case — search accessibility tree for matching nodes."""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from aiyes.domain.matching import name_matches
from aiyes.domain.tree import enrich_tree, flatten_nodes, prune_tree
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class FoundNode:
    """A node found by the find operation."""

    id: str
    role: str
    name: str
    bounds: Tuple[int, ...]
    states: Tuple[str, ...]
    actions: Tuple[str, ...]
    value: Optional[str] = None
    # Context fields — populated from enriched Node
    parent_role: Optional[str] = None
    parent_name: Optional[str] = None
    index_in_parent: Optional[int] = None
    depth: Optional[int] = None
    sibling_count: Optional[int] = None

    def __post_init__(self) -> None:
        """Ensure collection fields are truly immutable."""
        if isinstance(self.bounds, list):
            object.__setattr__(self, "bounds", tuple(self.bounds))
        if isinstance(self.states, list):
            object.__setattr__(self, "states", tuple(self.states))
        if isinstance(self.actions, list):
            object.__setattr__(self, "actions", tuple(self.actions))


class FindUseCase:
    """Find nodes matching role and optional name pattern."""

    def __init__(
        self,
        tree: AccessibilityTreePort,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
    ) -> None:
        self._tree = tree
        self._session_repo = session_repo
        self._tree_store = tree_store

    def execute(
        self,
        session_id: str,
        role: str,
        name_pattern: Optional[str] = None,
        state: Optional[str] = None,
        no_prune: bool = False,
    ) -> List[FoundNode]:
        """Find nodes matching role, optional name pattern, and optional state."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(
                f"Session not found: {session_id}. "
                "Run 'aieyes session list' to see available sessions."
            )

        # Port returns AccessibilityTree domain type
        domain_tree = self._tree.get_tree(session)

        # Apply pruning (default: prune=True, unless no_prune is set)
        domain_tree = prune_tree(domain_tree, prune=not no_prune)

        # Enrich tree with context fields
        domain_tree = enrich_tree(domain_tree)

        # Save tree to store with registry if available
        registry = getattr(self._tree, "last_registry", None)
        self._tree_store.save_tree(session_id, domain_tree, registry)

        # Flatten domain tree
        nodes = flatten_nodes(domain_tree.roots)

        # Filter by role (wildcard '*' matches all)
        if role != "*":
            nodes = [n for n in nodes if n.role == role]

        # Filter by name pattern (case-insensitive substring, whitespace-normalized)
        if name_pattern is not None:
            nodes = [n for n in nodes if name_matches(n.name, name_pattern)]

        # Filter by state
        if state is not None:
            nodes = [n for n in nodes if state in n.states]

        # Convert domain Nodes to FoundNode result objects
        result: List[FoundNode] = []
        for n in nodes:
            result.append(
                FoundNode(
                    id=n.id,
                    role=n.role,
                    name=n.name,
                    bounds=n.bounds,
                    states=n.states,
                    actions=n.actions,
                    value=n.value,
                    parent_role=n.parent_role,
                    parent_name=n.parent_name,
                    index_in_parent=n.index_in_parent,
                    depth=n.depth,
                    sibling_count=n.sibling_count,
                )
            )

        return result
