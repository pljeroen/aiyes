"""Tree diff domain logic — compare two accessibility trees.

Pure domain function diff_trees() compares before/after trees by node ID,
producing a TreeDiff with added, removed, and changed entries.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Set, Tuple

from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes


@dataclasses.dataclass(frozen=True)
class NodeChange:
    """A single field change on a node."""

    id: str
    field: str
    before: str
    after: str


@dataclasses.dataclass(frozen=True)
class TreeDiff:
    """Result of comparing two accessibility trees."""

    added: Tuple[Node, ...]
    removed: Tuple[Node, ...]
    changed: Tuple[NodeChange, ...]


# Fields compared between nodes with the same ID.
_COMPARED_FIELDS = ("role", "name", "value", "states", "actions", "bounds")


def _field_to_str(value: object) -> str:
    """Convert a field value to its string representation for NodeChange."""
    if value is None:
        return "null"
    if isinstance(value, tuple):
        return str(value)
    return str(value)


def _compare_nodes(before: Node, after: Node) -> List[NodeChange]:
    """Compare two nodes field-by-field and return changes."""
    changes: List[NodeChange] = []
    for field_name in _COMPARED_FIELDS:
        before_val = getattr(before, field_name)
        after_val = getattr(after, field_name)
        if before_val != after_val:
            changes.append(
                NodeChange(
                    id=before.id,
                    field=field_name,
                    before=_field_to_str(before_val),
                    after=_field_to_str(after_val),
                )
            )
    return changes


def _duplicate_stable_ids(nodes: List[Node]) -> Set[str]:
    """Return stable IDs that cannot safely be used as unique match keys."""
    seen: Set[str] = set()
    duplicates: Set[str] = set()
    for node in nodes:
        if not node.stable_id:
            continue
        if node.stable_id in seen:
            duplicates.add(node.stable_id)
        seen.add(node.stable_id)
    return duplicates


def _node_key(node: Node, duplicate_stable_ids: Set[str]) -> str:
    if node.stable_id and node.stable_id not in duplicate_stable_ids:
        return f"stable:{node.stable_id}"
    return f"id:{node.id}"


def _lookup_by_diff_key(nodes: List[Node], use_stable_ids: bool) -> Dict[str, Node]:
    if not use_stable_ids:
        return {f"id:{node.id}": node for node in nodes}
    duplicate_stable_ids = _duplicate_stable_ids(nodes)
    return {_node_key(node, duplicate_stable_ids): node for node in nodes}


def diff_trees(
    before: AccessibilityTree,
    after: AccessibilityTree,
    registry: NodeIdRegistry,
    use_stable_ids: bool = False,
) -> TreeDiff:
    """Compare two accessibility trees and produce a diff.

    Nodes are identified by their pre-assigned .id field. The registry
    parameter is accepted for API consistency but not used during comparison.

    Args:
        before: The stored/baseline tree.
        after: The live/current tree.
        registry: Node ID registry (carried through, not used for lookup).
        use_stable_ids: When true, use unique Node.stable_id values as match keys.

    Returns:
        TreeDiff with added, removed, and changed entries.
    """
    before_nodes = flatten_nodes(before.roots)
    after_nodes = flatten_nodes(after.roots)

    before_lookup = _lookup_by_diff_key(before_nodes, use_stable_ids)
    after_lookup = _lookup_by_diff_key(after_nodes, use_stable_ids)

    before_ids = set(before_lookup.keys())
    after_ids = set(after_lookup.keys())

    # Added: IDs in after but not in before
    added_ids = after_ids - before_ids
    added = tuple(after_lookup[nid] for nid in sorted(added_ids))

    # Removed: IDs in before but not in after
    removed_ids = before_ids - after_ids
    removed = tuple(before_lookup[nid] for nid in sorted(removed_ids))

    # Changed: IDs in both, with differing field values
    common_ids = before_ids & after_ids
    changes: List[NodeChange] = []
    for nid in sorted(common_ids):
        changes.extend(_compare_nodes(before_lookup[nid], after_lookup[nid]))

    return TreeDiff(
        added=added,
        removed=removed,
        changed=tuple(changes),
    )
