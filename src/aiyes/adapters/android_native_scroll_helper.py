"""Host-side bridge for Android native accessibility scroll helper calls."""

from __future__ import annotations

import argparse
import re
import subprocess
from typing import Optional, Sequence

from aiyes.adapters.adb_path import resolve_adb_path

_DEFAULT_BROADCAST_ACTION = "dev.aiyes.helper.NATIVE_SCROLL"
_DEFAULT_RECEIVER_COMPONENT = "dev.aiyes.helper/.NativeScrollReceiver"
_BROADCAST_RESULT_RE = re.compile(r"\bresult=(-?\d+)\b")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aiyes-android-native-scroll-helper")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--stable-id", required=True)
    parser.add_argument("--direction", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--bounds", default="")
    parser.add_argument("--broadcast-action", default=_DEFAULT_BROADCAST_ACTION)
    parser.add_argument("--receiver-component", default=_DEFAULT_RECEIVER_COMPONENT)
    return parser


def _adb_broadcast_args(args: argparse.Namespace) -> list[str]:
    command = [
        resolve_adb_path(),
        "-s",
        args.serial,
        "shell",
        "am",
        "broadcast",
        "-a",
        args.broadcast_action,
        "-n",
        args.receiver_component,
        "--es",
        "node_id",
        args.node_id,
        "--es",
        "stable_id",
        args.stable_id,
        "--es",
        "direction",
        args.direction,
        "--es",
        "action",
        args.action,
        "--ei",
        "action_id",
        args.action_id,
    ]
    if args.bounds:
        command.extend(["--es", "bounds", args.bounds])
    return command


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    completed = subprocess.run(
        _adb_broadcast_args(args),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode
    match = _BROADCAST_RESULT_RE.search(completed.stdout or "")
    if match:
        return int(match.group(1))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
