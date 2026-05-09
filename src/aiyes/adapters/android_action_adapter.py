"""AndroidActionAdapter — implements AccessibilityActionPort via adb input commands.

Performs accessibility actions by looking up node bounds from the tree
and using coordinate-based adb input commands.

Uses only stdlib: subprocess.
"""

from __future__ import annotations

import subprocess
from typing import List, Optional, Tuple

from aiyes.adapters.adb_text import escape_text_for_adb
from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.tree import Node, flatten_nodes
from aiyes.domain.types import ActionPortResult


def _get_serial(session) -> str:
    """Extract device_serial from session."""
    serial = session.device_serial
    if not serial:
        raise RuntimeError(
            "Android session has no device_serial — cannot execute action"
        )
    return serial


def _run_adb(serial: str, args: List[str]) -> None:
    """Run an adb shell command targeting a specific device."""
    from aiyes.adapters.adb_path import resolve_adb_path

    cmd = [resolve_adb_path(), "-s", serial, "shell"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError("adb not found on PATH. Install Android SDK platform-tools.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"adb action command timed out for device {serial}")

    if result.returncode != 0:
        raise RuntimeError(
            f"adb action failed (rc={result.returncode}): {result.stderr.strip()}"
        )


def _bounds_center(bounds: Tuple[int, ...]) -> Tuple[int, int]:
    """Calculate the center point of a bounds tuple (x, y, w, h)."""
    if len(bounds) != 4:
        return (0, 0)
    x, y, w, h = bounds
    return (x + w // 2, y + h // 2)


def _find_node_by_id(nodes: List[Node], node_id: str) -> Optional[Node]:
    """Find a node by its ID in a flat list of nodes."""
    for node in nodes:
        if node.id == node_id:
            return node
    return None


def _get_available_actions(node: Optional[Node]) -> Tuple[str, ...]:
    """Get available actions for a node, or empty tuple if node is None."""
    if node is None:
        return ()
    return node.actions


class AndroidActionAdapter:
    """Executes accessibility actions on Android via adb input commands.

    Uses coordinate-based tapping after resolving node bounds from the tree.
    """

    def __init__(self) -> None:
        self._tree_adapter = None

    def do_action(
        self,
        session,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[NodeIdRegistry] = None,
    ) -> ActionPortResult:
        """Execute an action on a node. Returns ActionPortResult.

        For "click": look up node bounds, then tap the center.
        For "set_text": tap to focus, Ctrl+A to select all, then input text.
        For unknown actions: return failure with available_actions.

        When a tree_adapter is set (via set_tree_adapter), it fetches a fresh
        tree to resolve node coordinates. Otherwise, if registry is provided,
        node_id is validated against the registry.
        """
        serial = _get_serial(session)

        # Try to resolve the node — prefer live tree, fall back to registry
        target_node: Optional[Node] = None
        if self._tree_adapter is not None:
            try:
                tree = self._tree_adapter.get_tree(session)
                all_nodes = flatten_nodes(tree.roots)
                target_node = _find_node_by_id(all_nodes, node_id)
                # If registry provided, use it for stable identity resolution
                if target_node is None and registry is not None:
                    reg_entry = registry.lookup_id(node_id)
                    if reg_entry is not None:
                        reg_role, reg_name, _reg_path = reg_entry
                        for n in all_nodes:
                            if n.role == reg_role and n.name == reg_name:
                                target_node = n
                                break
            except RuntimeError:
                pass
        elif registry is not None:
            # No tree adapter but registry provided — cannot resolve coordinates
            # Registry alone doesn't carry bounds, so we cannot act
            pass

        available = _get_available_actions(target_node)

        if action_name == "click":
            if target_node is not None:
                cx, cy = _bounds_center(target_node.bounds)
                _run_adb(serial, ["input", "tap", str(cx), str(cy)])
                return ActionPortResult(
                    success=True,
                    available_actions=available,
                    action_method="node_bounds_tap",
                )
            # No node found — cannot determine coordinates
            return ActionPortResult(success=False, available_actions=available)

        elif action_name == "set_text":
            if target_node is None:
                # Cannot set_text without knowing which element to target
                return ActionPortResult(success=False, available_actions=available)
            if "set_text" not in available:
                return ActionPortResult(success=False, available_actions=available)
            cx, cy = _bounds_center(target_node.bounds)
            # Clear-before-type sequence:
            # 1. Tap to focus the field
            # 2. Ctrl+A (select all) via input keycombo (API 21+, universal)
            # 3. DEL to delete the selection
            # 4. Type the new value (if non-empty)
            #
            # Note: clear-before-type is best-effort for standard Android widgets.
            # Custom widgets, WebView-based inputs, and Jetpack Compose TextField
            # may not respond to Ctrl+A or may handle key events differently.
            # There is no reliable way to detect whether selection succeeded
            # without re-reading the accessibility tree.
            _run_adb(serial, ["input", "tap", str(cx), str(cy)])
            # KEYCODE_CTRL_LEFT=113, KEYCODE_A=29 → Ctrl+A (select all)
            _run_adb(serial, ["input", "keycombo", "113", "29"])
            # KEYCODE_DEL=67 → delete selection
            _run_adb(serial, ["input", "keyevent", "67"])
            if value:
                escaped = escape_text_for_adb(value)
                _run_adb(serial, ["input", "text", escaped])
            # Re-read tree to get verified node text
            node_value: Optional[str] = None
            if self._tree_adapter is not None:
                try:
                    fresh_tree = self._tree_adapter.get_tree(session)
                    fresh_nodes = flatten_nodes(fresh_tree.roots)
                    fresh_node = _find_node_by_id(fresh_nodes, node_id)
                    if fresh_node is not None:
                        node_value = fresh_node.name
                except (RuntimeError, OSError, ValueError):
                    pass
            return ActionPortResult(
                success=True,
                available_actions=available,
                node_value=node_value,
                action_method="node_bounds_tap",
            )

        elif action_name == "focus":
            if target_node is None:
                return ActionPortResult(success=False, available_actions=available)
            cx, cy = _bounds_center(target_node.bounds)
            _run_adb(serial, ["input", "tap", str(cx), str(cy)])
            # Re-read tree to get node states after focus
            node_states: Optional[Tuple[str, ...]] = None
            if self._tree_adapter is not None:
                try:
                    fresh_tree = self._tree_adapter.get_tree(session)
                    fresh_nodes = flatten_nodes(fresh_tree.roots)
                    fresh_node = _find_node_by_id(fresh_nodes, node_id)
                    if fresh_node is not None:
                        node_states = tuple(fresh_node.states)
                except (RuntimeError, OSError, ValueError):
                    pass
            return ActionPortResult(
                success=True,
                available_actions=available,
                node_states=node_states,
                action_method="node_bounds_tap",
            )

        elif action_name == "long_click":
            if target_node is not None:
                cx, cy = _bounds_center(target_node.bounds)
                # Long press via swipe with same start/end and 1000ms duration
                _run_adb(
                    serial,
                    [
                        "input",
                        "swipe",
                        str(cx),
                        str(cy),
                        str(cx),
                        str(cy),
                        "1000",
                    ],
                )
                return ActionPortResult(
                    success=True,
                    available_actions=available,
                    action_method="node_bounds_tap",
                )
            return ActionPortResult(success=False, available_actions=available)

        elif action_name == "scroll":
            if target_node is not None:
                cx, cy = _bounds_center(target_node.bounds)
                # Scroll down by default
                _run_adb(
                    serial,
                    [
                        "input",
                        "swipe",
                        str(cx),
                        str(cy),
                        str(cx),
                        str(cy - 300),
                    ],
                )
                return ActionPortResult(
                    success=True,
                    available_actions=available,
                    action_method="node_bounds_tap",
                )
            return ActionPortResult(success=False, available_actions=available)

        else:
            # Unknown action
            return ActionPortResult(success=False, available_actions=available)

    def set_tree_adapter(self, tree_adapter) -> None:
        """Set the tree adapter used for node resolution during actions."""
        self._tree_adapter = tree_adapter
