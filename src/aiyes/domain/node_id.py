"""Node ID registry for session-stable, deterministic node IDs.

IDs are assigned based on node path (position in tree), role, and name.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple


class NodeIdRegistry:
    """Assigns and tracks session-stable node IDs.

    The registry maps (role, name, path_tuple) -> node_id.
    Same inputs always produce the same ID within a registry instance.
    """

    def __init__(self) -> None:
        self._mapping: Dict[Tuple[str, str, Tuple[int, ...]], str] = {}
        self._reverse: Dict[str, Tuple[str, str, Tuple[int, ...]]] = {}
        self._counter: int = 0

    def get_or_assign(self, role: str, name: str, path: List[int]) -> str:
        """Get existing ID or assign a new one for the given node identity."""
        key = (role, name, tuple(path))
        if key not in self._mapping:
            self._counter += 1
            node_id = f"n_{self._counter:03d}"
            self._mapping[key] = node_id
            self._reverse[node_id] = key
        return self._mapping[key]

    def get_mapping(self) -> Dict[str, str]:
        """Return the current path-to-ID mapping as a dict.

        Keys are JSON-encoded arrays: [role, name, path_list].
        This avoids ast.literal_eval for deserialization (S-01 security fix).
        """
        result: Dict[str, str] = {}
        for key, node_id in self._mapping.items():
            role, name, path = key
            str_key = json.dumps([role, name, list(path)])
            result[str_key] = node_id
        return result

    def has_id(self, node_id: str) -> bool:
        """Check if a node_id exists in this registry. O(1) via reverse map."""
        return node_id in self._reverse

    def lookup_id(self, node_id: str) -> Optional[Tuple[str, str, Tuple[int, ...]]]:
        """Look up a node_id and return its (role, name, path) tuple. O(1).

        Returns None if the node_id is not found in the registry.
        """
        return self._reverse.get(node_id)

    @classmethod
    def from_mapping(cls, mapping: Dict[str, str]) -> "NodeIdRegistry":
        """Reconstruct a NodeIdRegistry from a persisted mapping dict.

        Keys are JSON-encoded arrays: [role, name, path_list].
        Values are the assigned node IDs.

        Old Python tuple string format (e.g., "('role', 'name', (0, 1))")
        is intentionally rejected (S-01 security fix: no ast.literal_eval).
        """
        registry = cls()
        max_counter = 0
        for str_key, node_id in mapping.items():
            try:
                parsed = json.loads(str_key)
                if isinstance(parsed, list) and len(parsed) == 3:
                    role, name, path = parsed
                    if (
                        isinstance(role, str)
                        and isinstance(name, str)
                        and isinstance(path, list)
                        and all(isinstance(p, int) for p in path)
                    ):
                        key = (role, name, tuple(path))
                        registry._mapping[key] = node_id
                        registry._reverse[node_id] = key
                        # Track highest counter for future assignments
                        if node_id.startswith("n_"):
                            try:
                                num = int(node_id[2:])
                                if num > max_counter:
                                    max_counter = num
                            except ValueError:
                                pass
            except (ValueError, json.JSONDecodeError):
                continue
        registry._counter = max_counter
        return registry
