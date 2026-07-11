"""FileTreeStore — implements TreeStorePort via JSON files.

Trees are stored as ~/.aieyes/<session-id>/tree.json.
Deserialization reconstructs domain Node objects from stored dicts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.session import validate_session_id
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import StoredTree
from aiyes.domain.output_formatter import tree_to_dict


def _node_from_dict(data: Dict[str, Any]) -> Node:
    """Reconstruct a domain Node from a dict."""
    children_data = data.get("children", [])
    children: List[Node] = [_node_from_dict(c) for c in children_data]

    return Node(
        id=data.get("id", ""),
        role=data.get("role", ""),
        name=data.get("name", ""),
        bounds=tuple(data.get("bounds", [0, 0, 0, 0])),
        states=tuple(data.get("states", [])),
        actions=tuple(data.get("actions", [])),
        children=tuple(children),
        value=data.get("value"),
        stable_id=data.get("stable_id"),
        resource_id=data.get("resource_id", ""),
    )


class FileTreeStore:
    """File-based accessibility tree persistence."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".aieyes")
        self._base_dir = Path(base_dir)

    def _safe_session_dir(self, session_id: str) -> Path:
        """Validate session_id and return the session directory path.

        Raises ValueError if the session_id contains path traversal characters.
        """
        validate_session_id(session_id)
        return self._base_dir / session_id

    def save_tree(
        self,
        session_id: str,
        tree: AccessibilityTree,
        node_id_registry: Optional[NodeIdRegistry] = None,
    ) -> None:
        """Save tree and optional node ID registry for a session."""
        session_dir = self._safe_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(str(session_dir), 0o700)

        tree_file = session_dir / "tree.json"

        data: Dict[str, Any] = tree_to_dict(tree)

        # Include registry mapping if available
        if node_id_registry is not None:
            data["registry"] = node_id_registry.get_mapping()

        content = json.dumps(data, indent=2)
        fd = os.open(str(tree_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)

    def load_tree(self, session_id: str) -> Optional[StoredTree]:
        """Load tree data for a session. Returns StoredTree or None."""
        tree_file = self._safe_session_dir(session_id) / "tree.json"
        if not tree_file.exists():
            return None

        data = json.loads(tree_file.read_text())

        # Reconstruct domain tree from stored dict
        tree_list = data.get("tree", [])
        roots: List[Node] = [_node_from_dict(item) for item in tree_list]
        tree = AccessibilityTree(roots=tuple(roots))

        # Reconstruct NodeIdRegistry if persisted
        registry: Optional[NodeIdRegistry] = None
        registry_data = data.get("registry")
        if registry_data is not None and isinstance(registry_data, dict):
            registry = NodeIdRegistry.from_mapping(registry_data)

        return StoredTree(tree=tree, registry=registry)
