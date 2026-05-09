"""Persistent AT-SPI subprocess worker — long-running NDJSON protocol.

This script is the persistent counterpart of atspi_subprocess_worker.py.
It runs as a long-lived subprocess, accepting commands via stdin (NDJSON)
and returning responses via stdout (NDJSON).

Startup:
    python atspi_persistent_worker.py --display :1 --bus unix:path=/tmp/dbus-xxx

Protocol:
    1. On startup, writes {"status": "ready"} handshake to stdout.
    2. Reads NDJSON requests from stdin, dispatches, writes NDJSON responses.
    3. Exits on "shutdown" command or stdin EOF.

See CONTRACT.md sections 2 and 3 for full protocol specification.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

from aiyes.adapters.atspi_subprocess_worker import (
    _do_action_impl,
    _do_list_windows_impl,
    _do_tree_impl,
    _setup_env,
)


def _write_response(obj: Dict[str, Any]) -> None:
    """Write a single NDJSON response line and flush."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _dispatch(
    cmd: str,
    request: Dict[str, Any],
    display: str,
    bus_address: str,
) -> Any:
    """Route a command to the appropriate handler. Returns result dict/list."""
    if cmd == "ping":
        return {"status": "alive"}
    elif cmd == "get_tree":
        return _do_tree_impl(display, bus_address)
    elif cmd == "do_action":
        node_id = request.get("node_id", "")
        action_name = request.get("action_name", "")
        value = request.get("value")
        registry = request.get("registry")
        registry_json: Optional[str] = None
        if registry is not None:
            registry_json = json.dumps(registry)
        return _do_action_impl(
            display, bus_address, node_id, action_name, value, registry_json
        )
    elif cmd == "list_windows":
        return _do_list_windows_impl(display, bus_address)
    elif cmd == "shutdown":
        return {"status": "shutting_down"}
    else:
        raise ValueError(f"Unknown command: {cmd}")


def main_loop(display: str, bus_address: str) -> None:
    """Enter the NDJSON request/response main loop."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _write_response(
                {
                    "req_id": None,
                    "ok": False,
                    "error": {
                        "type": "JSONDecodeError",
                        "message": "Invalid JSON",
                    },
                }
            )
            continue

        req_id = request.get("req_id")
        cmd = request.get("cmd", "")

        try:
            result = _dispatch(cmd, request, display, bus_address)
            _write_response({"req_id": req_id, "ok": True, "result": result})
        except Exception as exc:
            _write_response(
                {
                    "req_id": req_id,
                    "ok": False,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )

        if cmd == "shutdown":
            break


def main() -> None:
    """Entry point for persistent worker subprocess."""
    parser = argparse.ArgumentParser(description="Persistent AT-SPI worker")
    parser.add_argument("--display", required=True)
    parser.add_argument("--bus", required=True)
    args = parser.parse_args()

    # Set up AT-SPI environment ONCE
    _setup_env(args.display, args.bus)

    # Write startup handshake
    sys.stdout.write(json.dumps({"status": "ready"}) + "\n")
    sys.stdout.flush()

    # Enter main loop
    main_loop(args.display, args.bus)


if __name__ == "__main__":
    main()
