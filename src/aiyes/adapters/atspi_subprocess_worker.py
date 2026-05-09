"""AT-SPI subprocess worker — runs AT-SPI queries in an isolated process.

This script is invoked as a subprocess by AtSpi2TreeAdapter and
AtSpi2ActionAdapter. It sets DISPLAY and DBUS_SESSION_BUS_ADDRESS
BEFORE importing gi.repository.Atspi, ensuring a fresh D-Bus connection
to the correct bus (bypassing libatspi's per-process connection cache).

Modes:
  tree   — Walk the AT-SPI tree and serialize to JSON on stdout.
  action — Find a node by ID and execute an action, return result as JSON.
  event  — Wait briefly for one AT-SPI event and serialize it as JSONL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple


def _warn(context: str, exc: Exception) -> None:
    """Log a suppressed exception to stderr as structured JSON.

    AT-SPI GLib calls can throw arbitrary C-level exceptions. These are
    suppressed to maintain tree walk stability, but logged here so they
    are visible in debug output.
    """
    print(
        json.dumps(
            {
                "warning": context,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        ),
        file=sys.stderr,
    )


def _setup_env(display: str, bus_address: str) -> None:
    """Set environment variables BEFORE importing gi.repository.Atspi."""
    os.environ["DISPLAY"] = display
    if bus_address and bus_address.startswith("unix:"):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus_address
    else:
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
    # Always strip AT_SPI_BUS_ADDRESS to avoid stale cached connections
    os.environ.pop("AT_SPI_BUS_ADDRESS", None)


def _node_to_dict(
    accessible: object,
    path: List[int],
    registry: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Convert an AT-SPI accessible to a serializable dict."""
    try:
        role_name = accessible.get_role_name()  # type: ignore[union-attr]
        name = accessible.get_name() or ""  # type: ignore[union-attr]

        # Bounds
        bounds: Tuple[int, ...] = (0, 0, 0, 0)
        component = accessible.get_component()  # type: ignore[union-attr]
        if component is not None:
            try:
                rect = component.get_extents(0)  # CoordType.SCREEN = 0
                bounds = (rect.x, rect.y, rect.width, rect.height)
            except Exception as exc:
                _warn("get_extents", exc)

        # States
        states: List[str] = []
        try:
            state_set = accessible.get_state_set()  # type: ignore[union-attr]
            raw_states = state_set.get_states()
            states = [str(s) for s in raw_states]
        except Exception as exc:
            _warn("get_state_set", exc)

        # Actions
        actions: List[str] = []
        try:
            n_actions = accessible.get_n_actions()  # type: ignore[union-attr]
            for j in range(n_actions):
                action_name = accessible.get_action_name(j)  # type: ignore[union-attr]
                if action_name:
                    actions.append(action_name)
        except Exception as exc:
            _warn("get_actions", exc)

        # Node ID via registry key (JSON array format, S-01 security fix)
        key = json.dumps([role_name, name, list(path)])
        if key not in registry:
            counter = len(registry) + 1
            registry[key] = f"n_{counter:03d}"
        node_id = registry[key]

        # Children
        children: List[Dict[str, Any]] = []
        try:
            child_count = accessible.get_child_count()  # type: ignore[union-attr]
            for i in range(child_count):
                child = accessible.get_child_at_index(i)  # type: ignore[union-attr]
                if child is not None:
                    child_dict = _node_to_dict(child, path + [i], registry)
                    if child_dict is not None:
                        children.append(child_dict)
        except Exception as exc:
            _warn("get_children", exc)

        return {
            "id": node_id,
            "role": role_name,
            "name": name,
            "bounds": list(bounds),
            "states": states,
            "actions": actions,
            "children": children,
        }
    except Exception as exc:
        _warn("node_to_dict", exc)
        return None


def _do_tree_impl(display: str, bus_address: str) -> Dict[str, Any]:
    """Walk the AT-SPI tree and return result as dict."""
    _setup_env(display, bus_address)

    # Import AFTER env is set
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    desktop = Atspi.get_desktop(0)
    registry: Dict[str, str] = {}
    roots: List[Dict[str, Any]] = []

    child_count = desktop.get_child_count()
    for i in range(child_count):
        child = desktop.get_child_at_index(i)
        if child is not None:
            node_dict = _node_to_dict(child, [i], registry)
            if node_dict is not None:
                roots.append(node_dict)

    return {
        "tree": roots,
        "registry": registry,
    }


def _do_tree(display: str, bus_address: str) -> None:
    """Walk the AT-SPI tree and output JSON to stdout."""
    result = _do_tree_impl(display, bus_address)
    json.dump(result, sys.stdout)


def _walk_path(root: object, path: Tuple[int, ...]) -> Optional[object]:
    """Walk the AT-SPI tree using stored path indices."""
    current = root
    for idx in path:
        try:
            child_count = current.get_child_count()  # type: ignore[union-attr]
            if idx >= child_count:
                return None
            child = current.get_child_at_index(idx)  # type: ignore[union-attr]
            if child is None:
                return None
            current = child
        except Exception as exc:
            _warn("walk_path", exc)
            return None
    return current


def _search_tree(
    accessible: object,
    target_id: str,
    path: List[int],
    registry_mapping: Dict[str, str],
) -> Optional[object]:
    """Recursively search for a node by ID using registry key derivation."""
    try:
        child_count = accessible.get_child_count()  # type: ignore[union-attr]
        for i in range(child_count):
            child = accessible.get_child_at_index(i)  # type: ignore[union-attr]
            if child is None:
                continue
            child_path = path + [i]
            try:
                role_name = child.get_role_name()  # type: ignore[union-attr]
                child_name = child.get_name() or ""  # type: ignore[union-attr]
            except Exception as exc:
                _warn("search_tree.get_role_name", exc)
                role_name = ""
                child_name = ""
            key = str((role_name, child_name, tuple(child_path)))
            if key not in registry_mapping:
                counter = len(registry_mapping) + 1
                registry_mapping[key] = f"n_{counter:03d}"
            child_id = registry_mapping[key]
            if child_id == target_id:
                return child
            found = _search_tree(child, target_id, child_path, registry_mapping)
            if found is not None:
                return found
    except Exception as exc:
        _warn("search_tree", exc)
    return None


def _do_action_impl(
    display: str,
    bus_address: str,
    node_id: str,
    action_name: str,
    value: Optional[str] = None,
    registry_json: Optional[str] = None,
) -> Dict[str, Any]:
    """Find a node and execute an action, return result as dict."""
    _setup_env(display, bus_address)

    # Import AFTER env is set
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    desktop = Atspi.get_desktop(0)

    # Reconstruct registry if provided (S-01: JSON array keys, no ast.literal_eval)
    node = None
    if registry_json:
        registry_mapping = json.loads(registry_json)
        # Try path-based resolution first
        for str_key, nid in registry_mapping.items():
            if nid == node_id:
                try:
                    parsed = json.loads(str_key)
                    if isinstance(parsed, list) and len(parsed) == 3:
                        _role, _name, path = parsed
                        resolved = _walk_path(desktop, tuple(path))
                        if resolved is not None:
                            try:
                                live_role = resolved.get_role_name()  # type: ignore[union-attr]
                                live_name = resolved.get_name() or ""  # type: ignore[union-attr]
                                if live_role == _role and live_name == _name:
                                    node = resolved
                            except Exception as exc:
                                _warn("action.verify_node", exc)
                except (ValueError, json.JSONDecodeError):
                    pass
                break

    # Fallback: full tree scan
    if node is None:
        fresh_registry: Dict[str, str] = {}
        node = _search_tree(desktop, node_id, [], fresh_registry)

    if node is None:
        return {"success": False, "available_actions": []}

    # Handle set_text action
    if action_name == "set_text" and value is not None:
        success = False
        try:
            editable = node.get_editable_text()  # type: ignore[union-attr]
            if editable is not None:
                editable.set_text_contents(value)
                success = True
        except Exception as exc:
            _warn("set_text.editable", exc)
        if not success:
            try:
                node.set_text_contents(value)  # type: ignore[union-attr]
                success = True
            except Exception as exc:
                _warn("set_text.fallback", exc)
        # Read back node value after successful set_text
        node_value = None
        if success:
            try:
                node_value = node.get_text(0, -1)  # type: ignore[union-attr]
            except Exception as exc:
                _warn("set_text.readback", exc)
        return {
            "success": success,
            "available_actions": ["set_text"],
            "node_value": node_value,
            "node_states": None,
        }

    # Handle focus action (before generic action resolution)
    if action_name == "focus":
        focus_success = False
        # Get available actions
        focus_available: List[str] = []
        try:
            n_actions = node.get_n_actions()  # type: ignore[union-attr]
            for i in range(n_actions):
                aname = node.get_action_name(i)  # type: ignore[union-attr]
                if aname:
                    focus_available.append(aname)
        except Exception as exc:
            _warn("focus.get_actions", exc)

        if "focus" in focus_available:
            # Standard path: focus is a listed action
            try:
                focus_index = focus_available.index("focus")
                node.do_action(focus_index)  # type: ignore[union-attr]
                focus_success = True
            except Exception as exc:
                _warn("focus.do_action", exc)
        else:
            # Fallback: grab_focus via Component interface
            try:
                component = node.get_component()  # type: ignore[union-attr]
                if component is not None:
                    component.grab_focus()
                    focus_success = True
            except Exception as exc:
                _warn("focus.grab_focus", exc)

        # Read node states after focus attempt
        node_states = None
        if focus_success:
            try:
                state_set = node.get_state_set()  # type: ignore[union-attr]
                raw_states = state_set.get_states()
                node_states = [str(s) for s in raw_states]
            except Exception as exc:
                _warn("focus.get_states", exc)

        return {
            "success": focus_success,
            "available_actions": focus_available,
            "node_value": None,
            "node_states": node_states,
        }

    # Get available actions
    available: List[str] = []
    try:
        n_actions = node.get_n_actions()  # type: ignore[union-attr]
        for i in range(n_actions):
            name = node.get_action_name(i)  # type: ignore[union-attr]
            if name:
                available.append(name)
    except Exception as exc:
        _warn("action.get_actions", exc)

    # Check if requested action is available
    if action_name not in available:
        return {
            "success": False,
            "available_actions": available,
            "node_value": None,
            "node_states": None,
        }

    # Execute the action
    try:
        action_index = available.index(action_name)
        node.do_action(action_index)  # type: ignore[union-attr]
        return {
            "success": True,
            "available_actions": available,
            "node_value": None,
            "node_states": None,
        }
    except Exception as exc:
        _warn("action.do_action", exc)
        return {
            "success": False,
            "available_actions": available,
            "node_value": None,
            "node_states": None,
        }


def _do_action(
    display: str,
    bus_address: str,
    node_id: str,
    action_name: str,
    value: Optional[str] = None,
    registry_json: Optional[str] = None,
) -> None:
    """Find a node and execute an action, output JSON result to stdout."""
    result = _do_action_impl(
        display, bus_address, node_id, action_name, value, registry_json
    )
    json.dump(result, sys.stdout)


def _do_list_windows_impl(display: str, bus_address: str) -> List[Dict[str, str]]:
    """List top-level windows (AT-SPI desktop children) as list of dicts.

    Enumerates only direct children of the desktop — no recursive walk.
    Each entry has {role, name}.
    """
    _setup_env(display, bus_address)

    # Import AFTER env is set
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    desktop = Atspi.get_desktop(0)
    windows: List[Dict[str, str]] = []

    child_count = desktop.get_child_count()
    for i in range(child_count):
        child = desktop.get_child_at_index(i)
        if child is not None:
            try:
                role_name = child.get_role_name()
                name = child.get_name() or ""
                windows.append({"role": role_name, "name": name})
            except Exception as exc:
                _warn("list_windows.get_child_info", exc)

    return windows


def _do_list_windows(display: str, bus_address: str) -> None:
    """List top-level windows (AT-SPI desktop children) as JSON to stdout."""
    result = _do_list_windows_impl(display, bus_address)
    json.dump(result, sys.stdout)


def _event_name_for_condition(condition: str) -> str:
    if condition == "focus-change":
        return "object:state-changed:focused"
    if condition == "screen-change":
        return "window"
    return condition


def _do_event(display: str, bus_address: str, event: str, timeout: float) -> None:
    """Wait for one native AT-SPI event and print normalized JSONL."""
    _setup_env(display, bus_address)

    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib

    loop = GLib.MainLoop()
    registered_event = _event_name_for_condition(event)

    def _callback(raw_event: object) -> None:
        source = getattr(raw_event, "source", None)
        payload: Dict[str, Any] = {
            "type": event,
            "source": "native_event",
            "timestamp": GLib.get_monotonic_time() / 1_000_000.0,
        }
        if source is not None:
            try:
                payload["name"] = source.get_name() or ""  # type: ignore[union-attr]
            except Exception as exc:
                _warn("event_get_name", exc)
            try:
                payload["role"] = source.get_role_name()  # type: ignore[union-attr]
            except Exception as exc:
                _warn("event_get_role", exc)
        print(json.dumps(payload), flush=True)
        loop.quit()

    listener = Atspi.EventListener.new(_callback)
    listener.register(registered_event)

    def _timeout() -> bool:
        loop.quit()
        return False

    GLib.timeout_add(int(max(0.0, timeout) * 1000), _timeout)
    loop.run()
    try:
        listener.deregister(registered_event)
    except Exception as exc:
        _warn("event_deregister", exc)


def _error_json(error: Exception) -> str:
    """Format an exception as structured JSON for stderr."""
    return json.dumps(
        {
            "error": type(error).__name__,
            "message": str(error),
        }
    )


def main() -> None:
    """Entry point for subprocess worker."""
    try:
        parser = argparse.ArgumentParser(description="AT-SPI subprocess worker")
        parser.add_argument("mode", choices=["tree", "action", "list-windows", "event"])
        parser.add_argument("--display", required=True)
        parser.add_argument("--bus", required=True)
        parser.add_argument("--node-id")
        parser.add_argument("--action")
        parser.add_argument("--event")
        parser.add_argument("--timeout", type=float, default=10.0)
        parser.add_argument("--value")
        parser.add_argument("--registry")

        args = parser.parse_args()

        if args.mode == "list-windows":
            _do_list_windows(args.display, args.bus)
        elif args.mode == "tree":
            _do_tree(args.display, args.bus)
        elif args.mode == "event":
            if not args.event:
                print(
                    json.dumps(
                        {
                            "error": "ValueError",
                            "message": "event mode requires --event",
                        }
                    ),
                    file=sys.stderr,
                )
                sys.exit(1)
            _do_event(args.display, args.bus, args.event, args.timeout)
        elif args.mode == "action":
            if not args.node_id or not args.action:
                print(
                    json.dumps(
                        {
                            "error": "ValueError",
                            "message": "action mode requires --node-id and --action",
                        }
                    ),
                    file=sys.stderr,
                )
                sys.exit(1)
            _do_action(
                args.display,
                args.bus,
                args.node_id,
                args.action,
                value=args.value,
                registry_json=args.registry,
            )
    except SystemExit as exc:
        if exc.code != 0:
            print(
                _error_json(RuntimeError(f"worker exited with code {exc.code}")),
                file=sys.stderr,
            )
        raise
    except Exception as exc:
        print(_error_json(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
