"""Find use case — search accessibility tree for matching nodes."""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from aiyes.domain.matching import name_matches, role_matches
from aiyes.domain.tree import (
    RoleDriftCandidate,
    enrich_tree,
    find_role_drift,
    flatten_nodes,
    flatten_scoped_subtrees,
    locate_ancestor_nodes,
    prune_tree,
)
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
    # Android view resource-id surfaced from the source Node; "" otherwise
    # (AIYES-116/C2). Appended AFTER sibling_count.
    resource_id: str = ""

    def __post_init__(self) -> None:
        """Ensure collection fields are truly immutable."""
        if isinstance(self.bounds, list):
            object.__setattr__(self, "bounds", tuple(self.bounds))
        if isinstance(self.states, list):
            object.__setattr__(self, "states", tuple(self.states))
        if isinstance(self.actions, list):
            object.__setattr__(self, "actions", tuple(self.actions))


@dataclasses.dataclass(frozen=True)
class AncestorRef:
    """Identity of a scoping ancestor echoed by a scoped find (AIYES-113/R4).

    Names the ancestor node the scope matched, which may sit ABOVE a
    FoundNode's immediate parent_role/parent_name.
    """

    id: str
    role: str
    name: str


class FindResult(list):
    """Result of a find operation (AIYES-113).

    Subclasses ``list`` (a ``collections.abc.Sequence``), so it satisfies the
    widened Sequence contract AND every pre-AIYES-113 back-compat idiom that
    treated a find result as a concrete list: ``isinstance(result, list)``,
    ``result == []``, ``len``, indexing (int / slice / negative), iteration and
    truthiness — exactly as the old ``List[FoundNode]`` return value did. This
    is the load-bearing backward-compatibility constraint C3 (T0), whose
    falsifier is "any existing find test needing an edit"; because the prior
    return type WAS a ``list``, several existing tests assert
    ``isinstance(result, list)`` / ``result == []`` — a ``list`` subclass keeps
    them green with zero edits while adding the scope fields. (Deviates from the
    contract's recommended "frozen dataclass" vehicle; see the RUN_LEDGER /
    README note. Stdlib-only, domain-pure — no external import, no I/O.)

    Scope fields are inert on the unscoped path (``scope_requested=False``,
    ``scope_matched=True`` vacuously, ``matched_ancestors=()``). On a scoped
    call they distinguish a scope-matched result (``scope_matched=True``,
    ``matched_ancestors`` non-empty) from a structured scoped-miss
    (``scope_matched=False``, ``matched_ancestors=()``), per R2/C2.
    """

    def __init__(
        self,
        nodes: Tuple[FoundNode, ...] = (),
        scope_requested: bool = False,
        scope_matched: bool = True,
        matched_ancestors: Tuple[AncestorRef, ...] = (),
        role_drift: Tuple[RoleDriftCandidate, ...] = (),
    ) -> None:
        super().__init__(nodes)
        self.scope_requested = scope_requested
        self.scope_matched = scope_matched
        self.matched_ancestors: Tuple[AncestorRef, ...] = tuple(matched_ancestors)
        # AIYES-114 diagnostic: same-name-different-role candidates surfaced on a
        # zero-match exact-role find. Empty on every match / non-drift path. A
        # side-channel only — never changes the list contents (C3).
        self.role_drift: Tuple[RoleDriftCandidate, ...] = tuple(role_drift)

    @property
    def nodes(self) -> Tuple[FoundNode, ...]:
        """The found nodes as a tuple (the list contents; read-only view)."""
        return tuple(self)


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
        within_role: Optional[str] = None,
        within_name: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> FindResult:
        """Find nodes matching role, optional name pattern, and optional state.

        When ``within_role`` and/or ``within_name`` are given (AIYES-113), the
        search is restricted to the descendants of the matched ancestor
        section(s): the ancestor(s) are located first, and ONLY their subtree(s)
        feed the existing role/name_pattern/state filter chain. An absent
        ancestor is a structured scoped-miss (``scope_matched=False``), NEVER a
        silent whole-tree fallback. Unscoped calls are byte-for-byte unchanged.
        """
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

        # AIYES-113 ancestor scoping. None-check (not truthiness): an explicit
        # empty within_name is still a scope request (C1/C3 scope_requested).
        scope_requested = within_role is not None or within_name is not None
        matched_ancestors: Tuple[AncestorRef, ...] = ()
        if scope_requested:
            ancestors = locate_ancestor_nodes(nodes, within_role, within_name)
            if not ancestors:
                # Structured scoped-miss (R2/C2): return immediately WITHOUT
                # running the filter chain over the whole tree — no fallback.
                return FindResult(
                    nodes=(),
                    scope_requested=True,
                    scope_matched=False,
                    matched_ancestors=(),
                )
            matched_ancestors = tuple(
                AncestorRef(id=a.id, role=a.role, name=a.name) for a in ancestors
            )
            # Candidate pool = the matched ancestors' descendants only.
            nodes = flatten_scoped_subtrees(ancestors)

        # AIYES-114: capture the pre-role-filter pool (scope-respecting) so the
        # role-drift diagnostic sees nodes of every role. On a scoped call this
        # is the ancestor subtree, so drift never points outside the scope.
        pre_role_nodes = nodes

        # Filter by role (wildcard '*' matches all)
        if role != "*":
            nodes = [n for n in nodes if role_matches(n.role, role)]

        # Filter by name pattern (case-insensitive substring, whitespace-normalized)
        if name_pattern is not None:
            nodes = [n for n in nodes if name_matches(n.name, name_pattern)]

        # Filter by state
        if state is not None:
            nodes = [n for n in nodes if state in n.states]

        # AIYES-116: filter by EXACT resource_id (full-string ==, never
        # substring/regex — DISTINCT from the name_matches substring matcher).
        # TRUTHY guard: both None and "" mean "no resource_id filter" (R2). Do
        # NOT normalize to is-not-None — an explicit "" would then exclude every
        # non-empty node, violating "empty/absent means no filter".
        if resource_id:
            nodes = [n for n in nodes if n.resource_id == resource_id]

        # Convert domain Nodes to FoundNode result objects
        found: List[FoundNode] = []
        for n in nodes:
            found.append(
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
                    resource_id=n.resource_id,
                )
            )

        # AIYES-114 diagnostic-only role drift: computed ONLY on the zero-match
        # path, purely to populate the additive side-channel. It never feeds back
        # into the matched set above (C3/FC-FINDPURE-02). The detector self-guards
        # to () for role=='*' / falsy name_pattern.
        role_drift: Tuple[RoleDriftCandidate, ...] = ()
        if not found:
            role_drift = find_role_drift(pre_role_nodes, role, name_pattern)

        return FindResult(
            nodes=tuple(found),
            scope_requested=scope_requested,
            scope_matched=True,
            matched_ancestors=matched_ancestors,
            role_drift=role_drift,
        )
