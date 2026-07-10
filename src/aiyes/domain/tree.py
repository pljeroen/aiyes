"""Accessibility tree domain model — Node and AccessibilityTree.

Includes pruning logic for noise node removal and depth limiting.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Set, Tuple


# Roles that are always excluded during pruning.
ALWAYS_EXCLUDED_ROLES = frozenset(
    [
        "filler",
        "redundant_object",
    ]
)

# Roles that are pruned when unnamed (children promoted to parent).
_PRUNE_UNNAMED_PROMOTE_CHILDREN = frozenset(
    [
        "section",
    ]
)

# Roles that are pruned when unnamed AND single-child (child promoted).
_PRUNE_UNNAMED_SINGLE_CHILD = frozenset(
    [
        "panel",
    ]
)


@dataclasses.dataclass(frozen=True)
class Node:
    """A single accessibility tree node."""

    id: str
    role: str
    name: str
    bounds: Tuple[int, ...]
    states: Tuple[str, ...]
    actions: Tuple[str, ...]
    children: Tuple[Node, ...] = ()
    value: Optional[str] = None
    # Context fields — populated by enrich_tree(), default None for backward compat
    parent_role: Optional[str] = None
    parent_name: Optional[str] = None
    index_in_parent: Optional[int] = None
    depth: Optional[int] = None
    sibling_count: Optional[int] = None
    stable_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Ensure collection fields are truly immutable."""
        if isinstance(self.bounds, list):
            object.__setattr__(self, "bounds", tuple(self.bounds))
        if isinstance(self.states, list):
            object.__setattr__(self, "states", tuple(self.states))
        if isinstance(self.actions, list):
            object.__setattr__(self, "actions", tuple(self.actions))
        if isinstance(self.children, list):
            object.__setattr__(self, "children", tuple(self.children))


@dataclasses.dataclass(frozen=True)
class AccessibilityTree:
    """Container for the accessibility tree roots."""

    roots: Tuple[Node, ...]

    def __post_init__(self) -> None:
        """Ensure roots is truly immutable."""
        if isinstance(self.roots, list):
            object.__setattr__(self, "roots", tuple(self.roots))


def _prune_node(node: Node) -> List[Node]:
    """Prune a single node according to pruning rules.

    Returns a list of nodes to replace the input node:
    - Empty list: node removed entirely (ALWAYS_EXCLUDED_ROLES).
    - List of children: node removed, children promoted (unnamed section/panel).
    - [node with pruned children]: node kept with recursively pruned children.
    """
    # Always exclude these roles entirely
    if node.role in ALWAYS_EXCLUDED_ROLES:
        return []

    # Recursively prune children first
    pruned_children: List[Node] = []
    for child in node.children:
        pruned_children.extend(_prune_node(child))

    # Unnamed sections: remove node, promote children
    if node.role in _PRUNE_UNNAMED_PROMOTE_CHILDREN and not node.name:
        return pruned_children

    # Unnamed single-child panels: remove node, promote child
    if (
        node.role in _PRUNE_UNNAMED_SINGLE_CHILD
        and not node.name
        and len(pruned_children) == 1
    ):
        return pruned_children

    # Keep node with pruned children
    return [dataclasses.replace(node, children=tuple(pruned_children))]


def prune_tree(tree: AccessibilityTree, prune: bool = True) -> AccessibilityTree:
    """Apply pruning rules to the tree.

    If prune is False, returns the tree unchanged.
    """
    if not prune:
        return tree

    new_roots: List[Node] = []
    for root in tree.roots:
        new_roots.extend(_prune_node(root))

    return AccessibilityTree(roots=tuple(new_roots))


def _limit_node_depth(node: Node, max_depth: int, current_depth: int) -> Node:
    """Recursively limit a node's children to max_depth.

    At max_depth, children are truncated to an empty list.
    """
    if current_depth >= max_depth:
        return dataclasses.replace(node, children=())

    limited_children = [
        _limit_node_depth(child, max_depth, current_depth + 1)
        for child in node.children
    ]
    return dataclasses.replace(node, children=tuple(limited_children))


def limit_tree_depth(
    tree: AccessibilityTree, max_depth: Optional[int] = None
) -> AccessibilityTree:
    """Apply depth limiting to the tree.

    If max_depth is None, returns the tree unchanged.
    Depth 0 means only root nodes with no children.
    Depth 1 means root nodes and their direct children, etc.
    """
    if max_depth is None:
        return tree

    new_roots = tuple(_limit_node_depth(root, max_depth, 0) for root in tree.roots)
    return AccessibilityTree(roots=new_roots)


def flatten_nodes(nodes: Tuple[Node, ...]) -> List[Node]:
    """Recursively flatten a tuple of Nodes into a flat list."""
    result: List[Node] = []
    for node in nodes:
        result.append(node)
        if node.children:
            result.extend(flatten_nodes(node.children))
    return result


def locate_ancestor_nodes(
    nodes: List[Node],
    within_role: Optional[str],
    within_name: Optional[str],
) -> List[Node]:
    """Return the nodes matching an optional ancestor/section scope spec.

    Filters an already-flattened node list (e.g. flatten_nodes(tree.roots)) by
    two independent optional predicates:

    - ``within_role``: exact ``node.role == within_role`` match, when given.
    - ``within_name``: substring/case-insensitive/whitespace-normalized match
      via the existing ``name_matches`` matcher, when given.

    When both are given the ancestor must satisfy BOTH (AND-ed). A ``None``
    predicate does not constrain. Returns matches in the input (pre-order,
    document) order; ``[]`` when nothing matches. Pure — stdlib + domain only.
    """
    from aiyes.domain.matching import name_matches

    matches: List[Node] = []
    for node in nodes:
        if within_role is not None and node.role != within_role:
            continue
        if within_name is not None and not name_matches(node.name, within_name):
            continue
        matches.append(node)
    return matches


def flatten_scoped_subtrees(ancestors: List[Node]) -> List[Node]:
    """Flatten the DESCENDANTS of each matched ancestor, deduped by node id.

    For every ancestor (in input order) this flattens ``ancestor.children``
    (the ancestor node itself is the scope boundary and is EXCLUDED from the
    candidate pool). Nodes are deduplicated by ``node.id`` preserving first-seen
    order, so nested/overlapping ancestor matches (one matched ancestor inside
    another) yield each descendant exactly once in stable pre-order. Pure — no
    I/O, stdlib + domain only.
    """
    result: List[Node] = []
    seen: Set[str] = set()
    for ancestor in ancestors:
        for descendant in flatten_nodes(ancestor.children):
            if descendant.id in seen:
                continue
            seen.add(descendant.id)
            result.append(descendant)
    return result


@dataclasses.dataclass(frozen=True)
class TreeDiff:
    """A single difference between two trees."""

    type: str  # "added", "removed", "changed"
    node_id: str
    field: Optional[str] = None  # "role" or "name", only for "changed"
    old: Optional[str] = None
    new: Optional[str] = None


def _filter_ignored_subtrees(
    nodes: List[Node],
    ignore_ids: frozenset,
) -> List[Node]:
    """Filter out nodes whose ID or any ancestor ID is in ignore_ids.

    Walk the original tree structure to identify all descendant IDs,
    then filter the flat list.
    """
    if not ignore_ids:
        return nodes

    # Build a set of all IDs to exclude (ignore_ids + their descendants).
    # We need the original tree structure for this, but we have a flat list
    # with parent-child relationships encoded via children fields.
    # Instead, rebuild from the flat list: collect IDs to exclude by
    # checking if any node's ID is in ignore_ids, then recursively
    # collect children IDs.
    excluded: set = set()

    def _collect_excluded(node: Node) -> None:
        """Recursively collect node ID and all descendant IDs."""
        excluded.add(node.id)
        for child in node.children:
            _collect_excluded(child)

    # We need the original tree nodes (with children intact) to walk subtrees.
    # The flat list has the children still attached to each node.
    for node in nodes:
        if node.id in ignore_ids:
            _collect_excluded(node)

    return [n for n in nodes if n.id not in excluded]


def _duplicate_stable_ids(nodes_a: List[Node], nodes_b: List[Node]) -> Set[str]:
    """Return stable IDs that repeat within either comparison tree."""
    duplicates: Set[str] = set()
    for nodes in (nodes_a, nodes_b):
        seen: Set[str] = set()
        for node in nodes:
            if not node.stable_id:
                continue
            if node.stable_id in seen:
                duplicates.add(node.stable_id)
            seen.add(node.stable_id)
    return duplicates


def _node_comparison_key(node: Node, duplicate_stable_ids: Set[str]) -> str:
    if node.stable_id and node.stable_id not in duplicate_stable_ids:
        return f"stable:{node.stable_id}"
    return f"id:{node.id}"


def _comparison_lookup(
    nodes_a: List[Node],
    nodes_b: List[Node],
    use_stable_ids: bool,
) -> Tuple[Dict[str, Node], Dict[str, Node]]:
    if not use_stable_ids:
        return (
            {f"id:{node.id}": node for node in nodes_a},
            {f"id:{node.id}": node for node in nodes_b},
        )
    duplicate_stable_ids = _duplicate_stable_ids(nodes_a, nodes_b)
    return (
        {_node_comparison_key(node, duplicate_stable_ids): node for node in nodes_a},
        {_node_comparison_key(node, duplicate_stable_ids): node for node in nodes_b},
    )


def trees_structurally_equal(
    a: AccessibilityTree,
    b: AccessibilityTree,
    tolerance: int = 0,
    ignore_ids: frozenset = frozenset(),
    use_stable_ids: bool = False,
) -> bool:
    """Check structural equality of two accessibility trees.

    Compares node ID sets and per-ID role+name.
    Ignores value, states, actions, and bounds fields.

    When tolerance > 0, allows up to that many node-level differences
    (added + removed + changed role/name) while still returning True.

    When ignore_ids is non-empty, nodes with those IDs and their entire
    subtrees are excluded from comparison in both trees.
    """
    nodes_a = _filter_ignored_subtrees(flatten_nodes(a.roots), ignore_ids)
    nodes_b = _filter_ignored_subtrees(flatten_nodes(b.roots), ignore_ids)

    lookup_a, lookup_b = _comparison_lookup(nodes_a, nodes_b, use_stable_ids)
    ids_a = set(lookup_a.keys())
    ids_b = set(lookup_b.keys())

    # Count differences
    added = ids_b - ids_a
    removed = ids_a - ids_b
    common = ids_a & ids_b

    changed_count = 0
    for node_id in common:
        na = lookup_a[node_id]
        nb = lookup_b[node_id]
        if na.role != nb.role:
            changed_count += 1
        if na.name != nb.name:
            changed_count += 1

    total_diffs = len(added) + len(removed) + changed_count
    return total_diffs <= tolerance


def compute_tree_diff(
    a: AccessibilityTree,
    b: AccessibilityTree,
    ignore_ids: frozenset = frozenset(),
    use_stable_ids: bool = False,
) -> Tuple[TreeDiff, ...]:
    """Compute specific differences between two trees.

    Returns a tuple of TreeDiff entries describing added, removed, and
    changed nodes. Respects ignore_ids for subtree exclusion.
    """
    nodes_a = _filter_ignored_subtrees(flatten_nodes(a.roots), ignore_ids)
    nodes_b = _filter_ignored_subtrees(flatten_nodes(b.roots), ignore_ids)

    lookup_a, lookup_b = _comparison_lookup(nodes_a, nodes_b, use_stable_ids)
    ids_a = set(lookup_a.keys())
    ids_b = set(lookup_b.keys())

    diffs: List[TreeDiff] = []

    # Removed nodes (in A but not in B)
    for node_id in sorted(ids_a - ids_b):
        diffs.append(TreeDiff(type="removed", node_id=lookup_a[node_id].id))

    # Added nodes (in B but not in A)
    for node_id in sorted(ids_b - ids_a):
        diffs.append(TreeDiff(type="added", node_id=lookup_b[node_id].id))

    # Changed nodes (in both but with different role or name)
    for node_id in sorted(ids_a & ids_b):
        na = lookup_a[node_id]
        nb = lookup_b[node_id]
        if na.role != nb.role:
            diffs.append(
                TreeDiff(
                    type="changed",
                    node_id=na.id,
                    field="role",
                    old=na.role,
                    new=nb.role,
                )
            )
        if na.name != nb.name:
            diffs.append(
                TreeDiff(
                    type="changed",
                    node_id=na.id,
                    field="name",
                    old=na.name,
                    new=nb.name,
                )
            )

    return tuple(diffs)


def filter_tree_by_window(
    tree: AccessibilityTree, window_title: Optional[str] = None
) -> AccessibilityTree:
    """Filter tree roots to only include windows matching the given title.

    If window_title is None, returns the tree unchanged.
    Matching is case-insensitive substring with whitespace normalization.
    """
    if window_title is None:
        return tree

    from aiyes.domain.matching import name_matches

    matching = tuple(
        root for root in tree.roots if name_matches(root.name, window_title)
    )
    return AccessibilityTree(roots=matching)


def raw_tree_to_domain(raw_data: object) -> AccessibilityTree:
    """Convert raw tree data (dict/list of dicts) to domain AccessibilityTree.

    Handles the standard format: {"tree": [node_dicts]} or [node_dicts].
    Each node_dict has keys: id, role, name, bounds, states, actions, children, value.
    """
    if raw_data is None:
        return AccessibilityTree(roots=())

    if isinstance(raw_data, dict):
        tree_list = raw_data.get("tree", [])
    elif isinstance(raw_data, list):
        tree_list = raw_data
    else:
        return AccessibilityTree(roots=())

    roots = tuple(
        n
        for n in (
            _raw_node_to_domain(item) for item in tree_list if isinstance(item, dict)
        )
        if n is not None
    )
    return AccessibilityTree(roots=roots)


def _raw_node_to_domain(raw: dict) -> Optional[Node]:
    """Convert a raw node dict to a domain Node.

    Returns None if the node has an empty or blank ID (malformed input).
    """
    node_id = raw.get("id", "")
    if not isinstance(node_id, str) or not node_id.strip():
        return None

    raw_children = raw.get("children", [])
    children = tuple(
        c
        for c in (
            _raw_node_to_domain(rc) for rc in raw_children if isinstance(rc, dict)
        )
        if c is not None
    )
    return Node(
        id=node_id,
        role=raw.get("role") or "",
        name=raw.get("name") or "",
        bounds=raw.get("bounds", [0, 0, 0, 0]),
        states=raw.get("states", []),
        actions=raw.get("actions", []),
        children=children,
        value=raw.get("value"),
        stable_id=raw.get("stable_id"),
    )


def _enrich_node(
    node: Node,
    parent_role: Optional[str],
    parent_name: Optional[str],
    index: int,
    sibling_count: int,
    depth: int,
) -> Node:
    """Enrich a single node with context fields, recurse into children."""
    enriched_children = tuple(
        _enrich_node(
            child,
            parent_role=node.role,
            parent_name=node.name,
            index=i,
            sibling_count=len(node.children),
            depth=depth + 1,
        )
        for i, child in enumerate(node.children)
    )
    return dataclasses.replace(
        node,
        parent_role=parent_role,
        parent_name=parent_name,
        index_in_parent=index,
        depth=depth,
        sibling_count=sibling_count,
        children=enriched_children,
    )


def enrich_tree(tree: AccessibilityTree) -> AccessibilityTree:
    """Compute context fields for all nodes in a single recursive pass.

    Root nodes get depth=0, parent_role=None, parent_name=None.
    Child nodes inherit parent info. index_in_parent is 0-based among siblings.
    sibling_count is len(parent.children).

    Idempotent: calling twice yields the same result.
    """
    root_count = len(tree.roots)
    enriched_roots = tuple(
        _enrich_node(
            root,
            parent_role=None,
            parent_name=None,
            index=i,
            sibling_count=root_count,
            depth=0,
        )
        for i, root in enumerate(tree.roots)
    )
    return AccessibilityTree(roots=enriched_roots)
