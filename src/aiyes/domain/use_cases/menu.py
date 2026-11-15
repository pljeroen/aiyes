"""Menu traversal use case — navigate menu hierarchies (Linux only for v1)."""

from __future__ import annotations

import dataclasses
from typing import Optional, Sequence, Tuple

from aiyes.domain.matching import normalize_whitespace, role_matches
from aiyes.domain.tree import Node
from aiyes.domain.types import ACTION_DECLINED, ACTION_NOT_ENABLED, ActionPortResult
from aiyes.ports.accessibility_action import AccessibilityActionPort
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.clock import ClockPort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class MenuResult:
    """Result of a menu traversal operation."""

    status: str = "ok"
    node_id: Optional[str] = None
    node_name: Optional[str] = None


# Roles that represent menus and menu items in the accessibility tree, in
# canonical spelling. Node roles are compared with role_matches, so the spaced
# spellings AT-SPI reports ("menu bar", "menu item", ...) match as well.
_MENU_ROLES = frozenset(["menu", "menu_bar"])
_ITEM_ROLES = frozenset(["menu_item", "menu", "check_menu_item", "radio_menu_item"])
_FIRST_SEGMENT_ROLES = _MENU_ROLES | _ITEM_ROLES

# Actions that activate a menu node, in order of preference: GTK menus offer
# "click"; Qt Widgets menu items offer "Press" and items that open a submenu
# offer "ShowMenu".
_ACTION_PREFERENCE = ("click", "Press", "ShowMenu")


def _find_node_by_name(
    nodes: tuple,
    name: str,
    roles: frozenset,
) -> Optional[Node]:
    """Recursively find a node matching name and one of the given roles.

    Searches ``nodes`` and their descendants in preorder and returns the first
    match. A node's role matches when role_matches holds for one of ``roles``,
    so spaced and underscore spellings of a role are equivalent.

    Uses whitespace-normalized exact match: both sides are normalized
    before comparison so that embedded newlines/tabs in menu item names
    match user-supplied names with spaces.
    """
    normalized_search = normalize_whitespace(name).lower()
    for node in nodes:
        if any(role_matches(node.role, role) for role in roles):
            normalized_node = normalize_whitespace(node.name).lower()
            if normalized_node == normalized_search:
                return node
        # Search children
        found = _find_node_by_name(node.children, name, roles)
        if found is not None:
            return found
    return None


def _resolve_path(roots: Tuple[Node, ...], segments: Sequence[str]) -> Optional[Node]:
    """Resolve path segments in a tree and return the last segment's node.

    The first segment is looked up anywhere in the tree; every following
    segment only among the descendants of the node the segment before it
    resolved to. Returns None when any segment does not resolve.
    """
    node = _find_node_by_name(roots, segments[0], _FIRST_SEGMENT_ROLES)
    for segment in segments[1:]:
        if node is None:
            return None
        node = _find_node_by_name(node.children, segment, _ITEM_ROLES)
    return node


def _choose_action(node: Node) -> str:
    """Return the first preferred action the node offers, else "click"."""
    for action in _ACTION_PREFERENCE:
        if action in node.actions:
            return action
    return "click"


def _refusal_message(
    segment: str,
    menu_path: str,
    node: Node,
    action: str,
    result: ActionPortResult,
) -> str:
    """Describe a segment action the action port reports did not succeed.

    The message names the cause the port reports: a node that is not enabled
    (the action was not sent) or an action the application declined; for any
    other failure it says that the action did not succeed.
    """
    if result.available_actions:
        available = ", ".join(repr(a) for a in result.available_actions)
    else:
        available = "none were reported"
    prefix = f"Menu item {segment!r} in path {menu_path!r} could not be activated: "
    if result.failure_reason == ACTION_NOT_ENABLED:
        cause = f"node {node.id!r} is not enabled, so action {action!r} was not sent"
    elif result.failure_reason == ACTION_DECLINED:
        cause = f"action {action!r} on node {node.id!r} was declined by the application"
    else:
        cause = f"action {action!r} on node {node.id!r} did not succeed"
    return f"{prefix}{cause} (available actions: {available})"


class MenuUseCase:
    """Traverse menu hierarchies by dot-separated path.

    Algorithm:
    1. Split menu_path by '.'
    2. First segment: find a menu, menu bar or menu item node with that name
       anywhere in the stored tree and activate it
    3. Re-read the tree (menus are dynamic) until the next segment's item
       appears among the descendants of the node the previous segment
       resolved to; segments 1..k-1 are resolved again in each re-read tree
    4. Activate that item; repeat until the last segment has been activated

    Each node receives the first of click, Press or ShowMenu that it offers,
    or click when it offers none of them. When the action port reports that
    the action did not succeed (for example because the node does not offer
    it), the traversal stops with an error.

    The outcome is only what the action port reports. On Linux the port
    refuses a node that is not enabled without sending the action, and
    reports an action the application declines; either stops the traversal
    with an error that names the cause. The effect of an activation is not
    checked: an enabled node that accepts the action but on which the
    application does not act is treated as activated, so the traversal goes
    on with the next segment, and on the last segment the result is status
    "ok".
    """

    def __init__(
        self,
        tree_port: AccessibilityTreePort,
        action_port: AccessibilityActionPort,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
        clock: ClockPort,
    ) -> None:
        self._tree = tree_port
        self._action = action_port
        self._session_repo = session_repo
        self._tree_store = tree_store
        self._clock = clock

    def execute(self, session_id: str, menu_path: str) -> MenuResult:
        """Activate each segment of the menu path in turn, the last one included.

        Returns the last segment's node. Raises RuntimeError when a segment is
        not found in its scope (the whole tree for the first segment, the
        previous segment's node for every later one) or when the action port
        reports that a segment's action did not succeed, which includes a node
        that is not enabled and an action the application declines. An enabled
        node that accepts the action without carrying it out is still treated
        as activated (see the class docstring).
        """
        if not menu_path or not menu_path.strip():
            raise ValueError(
                "Menu path must not be empty — provide at least one segment"
            )

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        backend = getattr(session, "backend", "linux")
        if backend == "android":
            raise RuntimeError(
                "Menu traversal not supported on Android — use find + action instead"
            )

        segments = [s.strip() for s in menu_path.split(".")]

        # Load stored tree for initial menu search
        stored = self._tree_store.load_tree(session_id)
        if stored is not None:
            tree = stored.tree
            registry = stored.registry
        else:
            tree = self._tree.get_tree(session)
            registry = None

        node = _resolve_path(tree.roots, segments[:1])
        if node is None:
            raise RuntimeError(
                f"Menu item not found: {segments[0]!r} in path {menu_path!r}"
            )

        last_index = len(segments) - 1
        for i, segment in enumerate(segments):
            action = _choose_action(node)
            result = self._action.do_action(session, node.id, action, None, registry)
            if not result.success:
                raise RuntimeError(
                    _refusal_message(segment, menu_path, node, action, result)
                )
            if i == last_index:
                break

            # Wait for the next item to appear inside this node, re-reading
            # the tree and resolving the path up to the next segment again
            next_segment = segments[i + 1]
            next_node = None
            for _attempt in range(3):
                self._clock.sleep(0.2)
                tree = self._tree.get_tree(session)
                registry = None
                next_node = _resolve_path(tree.roots, segments[: i + 2])
                if next_node is not None:
                    break
            if next_node is None:
                raise RuntimeError(
                    f"Menu item not found: {next_segment!r} in path "
                    f"{menu_path!r} — the submenu may not have appeared yet"
                )
            node = next_node

        return MenuResult(status="ok", node_id=node.id, node_name=node.name)
