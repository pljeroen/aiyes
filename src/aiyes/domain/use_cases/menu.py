"""Menu traversal use case — navigate menu hierarchies (Linux only for v1)."""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.domain.tree import Node
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


# Roles that represent menus and menu items in the accessibility tree
_MENU_ROLES = frozenset(["menu", "menu_bar"])
_ITEM_ROLES = frozenset(["menu_item", "menu", "check_menu_item", "radio_menu_item"])


def _find_node_by_name(
    nodes: tuple,
    name: str,
    roles: frozenset,
) -> Optional[Node]:
    """Recursively find a node matching name and one of the given roles.

    Uses whitespace-normalized exact match: both sides are normalized
    before comparison so that embedded newlines/tabs in menu item names
    match user-supplied names with spaces.
    """
    from aiyes.domain.matching import normalize_whitespace

    normalized_search = normalize_whitespace(name).lower()
    for node in nodes:
        if node.role in roles:
            normalized_node = normalize_whitespace(node.name).lower()
            if normalized_node == normalized_search:
                return node
        # Search children
        found = _find_node_by_name(node.children, name, roles)
        if found is not None:
            return found
    return None


class MenuUseCase:
    """Traverse menu hierarchies by dot-separated path.

    Algorithm:
    1. Split menu_path by '.'
    2. For first segment: find menu/menu_bar node matching name -> click
    3. Re-read tree (menus are dynamic)
    4. For subsequent segments: find menu_item matching name -> click
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
        """Traverse menu path and click the final item."""
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

        last_node = None

        for i, segment in enumerate(segments):
            if i == 0:
                # First segment: find in menu_bar or top-level menu
                search_roles = _MENU_ROLES | _ITEM_ROLES
            else:
                # Subsequent segments: find menu_item in subtree
                search_roles = _ITEM_ROLES

            node = _find_node_by_name(tree.roots, segment, search_roles)
            if node is None:
                raise RuntimeError(
                    f"Menu item not found: {segment!r} in path {menu_path!r}"
                )

            # Click the node
            self._action.do_action(session, node.id, "click", None, registry)
            last_node = node

            # Wait for submenu to appear, then re-read tree with retry
            if i < len(segments) - 1:
                next_segment = segments[i + 1]
                next_roles = _ITEM_ROLES
                found_next = False
                for _attempt in range(3):
                    self._clock.sleep(0.2)
                    tree = self._tree.get_tree(session)
                    registry = None
                    if (
                        _find_node_by_name(tree.roots, next_segment, next_roles)
                        is not None
                    ):
                        found_next = True
                        break
                if not found_next:
                    raise RuntimeError(
                        f"Menu item not found: {next_segment!r} in path "
                        f"{menu_path!r} — the submenu may not have appeared yet"
                    )

        return MenuResult(
            status="ok",
            node_id=last_node.id if last_node else None,
            node_name=last_node.name if last_node else None,
        )
