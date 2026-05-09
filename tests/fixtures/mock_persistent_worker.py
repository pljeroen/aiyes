"""Minimal mock worker that speaks the persistent worker protocol.

Used by tests to verify AtSpiWorkerConnection without requiring AT-SPI.
Supports: ping, get_tree, do_action, list_windows, shutdown.
Configurable delay via --delay CLI arg (seconds, default 0).
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--display", default=":0")
    parser.add_argument("--bus", default="")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--no-handshake", action="store_true")
    parser.add_argument("--slow-handshake", type=float, default=0.0)
    args = parser.parse_args()

    if args.slow_handshake > 0:
        time.sleep(args.slow_handshake)

    if not args.no_handshake:
        sys.stdout.write(json.dumps({"status": "ready"}) + "\n")
        sys.stdout.flush()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        if args.delay > 0:
            time.sleep(args.delay)

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp = {
                "req_id": None,
                "ok": False,
                "error": {"type": "JSONDecodeError", "message": "Invalid JSON"},
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        req_id = req.get("req_id")
        cmd = req.get("cmd", "")

        if cmd == "ping":
            resp = {"req_id": req_id, "ok": True, "result": {"status": "alive"}}
        elif cmd == "get_tree":
            resp = {
                "req_id": req_id,
                "ok": True,
                "result": {
                    "tree": [
                        {
                            "id": "n_001",
                            "role": "application",
                            "name": "MockApp",
                            "bounds": [0, 0, 800, 600],
                            "states": [],
                            "actions": [],
                            "children": [],
                        }
                    ],
                    "registry": {'["application","MockApp",[0]]': "n_001"},
                },
            }
        elif cmd == "do_action":
            resp = {
                "req_id": req_id,
                "ok": True,
                "result": {
                    "success": True,
                    "available_actions": [req.get("action_name", "click")],
                    "node_value": None,
                    "node_states": None,
                },
            }
        elif cmd == "list_windows":
            resp = {
                "req_id": req_id,
                "ok": True,
                "result": [{"role": "application", "name": "MockApp"}],
            }
        elif cmd == "shutdown":
            resp = {
                "req_id": req_id,
                "ok": True,
                "result": {"status": "shutting_down"},
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            break
        else:
            resp = {
                "req_id": req_id,
                "ok": False,
                "error": {
                    "type": "ValueError",
                    "message": f"Unknown command: {cmd}",
                },
            }

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
