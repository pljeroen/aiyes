"""Output formatter — converts domain objects to dicts.

These functions produce plain Python dicts, NOT JSON strings.
JSON serialization belongs in the CLI layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node


def session_to_dict(session: Session) -> Dict[str, Any]:
    """Convert a Session to a plain dict."""
    return {
        "session_id": session.session_id,
        "display": session.display,
        "app_pid": session.app_pid,
        "app_command": session.app_command,
        "app_args": session.app_args,
        "atspi_bus_pid": session.atspi_bus_pid,
        "atspi_bus_address": session.atspi_bus_address,
        "xvfb_pid": session.xvfb_pid,
        "name": session.name,
        "resolution": session.resolution,
        "color_depth": session.color_depth,
        "started_at": session.started_at,
        "backend": session.backend,
        "device_serial": session.device_serial,
    }


def node_to_dict(
    node: Node, max_depth: Optional[int] = None, current_depth: int = 0
) -> Dict[str, Any]:
    """Convert a Node to a plain dict.

    If max_depth is set, children deeper than max_depth are excluded.
    """
    result: Dict[str, Any] = {
        "id": node.id,
        "role": node.role,
        "name": node.name,
        "bounds": list(node.bounds),
        "states": list(node.states),
        "actions": list(node.actions),
    }

    if node.value is not None:
        result["value"] = node.value
    if node.stable_id is not None:
        result["stable_id"] = node.stable_id
    # AIYES-116: OMIT when "" (truthiness guard — a NEW guard, distinct from the
    # is-not-None guards above; resource_id is a non-Optional falsy-but-not-None
    # field, so an empty value must produce byte-identical pre-change output).
    if node.resource_id:
        result["resource_id"] = node.resource_id

    # Context fields — only include when non-None (compactness)
    if node.parent_role is not None:
        result["parent_role"] = node.parent_role
    if node.parent_name is not None:
        result["parent_name"] = node.parent_name
    if node.index_in_parent is not None:
        result["index_in_parent"] = node.index_in_parent
    if node.depth is not None:
        result["depth"] = node.depth
    if node.sibling_count is not None:
        result["sibling_count"] = node.sibling_count

    if node.children:
        if max_depth is not None and current_depth >= max_depth:
            # Truncate: do not include children beyond max_depth
            pass
        else:
            result["children"] = [
                node_to_dict(
                    child, max_depth=max_depth, current_depth=current_depth + 1
                )
                for child in node.children
            ]

    return result


def tree_to_dict(
    tree: AccessibilityTree, max_depth: Optional[int] = None
) -> Dict[str, Any]:
    """Convert an AccessibilityTree to a plain dict.

    Returns {"tree": [list of root node dicts]}.
    """
    roots: List[Dict[str, Any]] = [
        node_to_dict(root, max_depth=max_depth) for root in tree.roots
    ]
    return {"tree": roots}
