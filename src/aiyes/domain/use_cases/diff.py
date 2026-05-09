"""Diff use case — compare stored tree against live tree."""

from __future__ import annotations

import dataclasses
import types
from typing import Mapping

from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.tree import flatten_nodes
from aiyes.domain.tree_diff import TreeDiff, diff_trees
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class DiffResult:
    """Result of a diff operation."""

    diff: TreeDiff
    total_changes: int
    node_roles: Mapping[str, str] = dataclasses.field(
        default_factory=lambda: types.MappingProxyType({})
    )

    def __post_init__(self) -> None:
        """Ensure node_roles is immutable."""
        if isinstance(self.node_roles, dict):
            object.__setattr__(
                self, "node_roles", types.MappingProxyType(self.node_roles)
            )


class DiffUseCase:
    """Compare the stored accessibility tree against the current live tree."""

    def __init__(
        self,
        tree: AccessibilityTreePort,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
    ) -> None:
        self._tree = tree
        self._session_repo = session_repo
        self._tree_store = tree_store

    def execute(self, session_id: str) -> DiffResult:
        """Execute the diff operation.

        Loads stored tree, fetches live tree, computes diff, saves live
        tree as the new baseline.

        Raises:
            RuntimeError: If session not found or no stored tree exists.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        stored = self._tree_store.load_tree(session_id)
        if stored is None:
            raise RuntimeError("No stored tree. Run inspect first.")

        live_tree = self._tree.get_tree(session)

        registry = stored.registry or NodeIdRegistry()
        result_diff = diff_trees(
            stored.tree,
            live_tree,
            registry,
            use_stable_ids=session.backend == "android",
        )

        # Save live tree as new baseline with the adapter's last_registry
        # (follows InspectUseCase/FindUseCase pattern)
        live_registry = getattr(self._tree, "last_registry", None)
        save_registry = live_registry or stored.registry or NodeIdRegistry()
        self._tree_store.save_tree(session_id, live_tree, save_registry)

        total = (
            len(result_diff.added) + len(result_diff.removed) + len(result_diff.changed)
        )
        live_nodes = flatten_nodes(live_tree.roots)
        roles = {n.id: n.role for n in live_nodes}
        return DiffResult(diff=result_diff, total_changes=total, node_roles=roles)
