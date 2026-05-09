"""Opt-in real smoke evidence harness.

The harness is inert by default. It only runs real GUI/device commands when
explicitly enabled by argument or by AIYES_RUN_REAL_SMOKE=1.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional


SMOKE_ENABLE_ENV = "AIYES_RUN_REAL_SMOKE"

CommandRunner = Callable[[List[str]], Any]
Clock = Callable[[], str]


_TARGETS: Dict[str, List[Dict[str, Any]]] = {
    "linux-gedit": [
        {"name": "doctor", "command": ["aieyes", "doctor"]},
        {
            "name": "scenario-run",
            "command": [
                "aieyes",
                "scenario",
                "run",
                "--real",
                "--public-fixture",
                "examples/scenarios/linux-gedit-text.json",
            ],
        },
    ],
    "android-settings": [
        {"name": "doctor", "command": ["aieyes", "doctor"]},
        {
            "name": "scenario-run",
            "command": [
                "aieyes",
                "scenario",
                "run",
                "--real",
                "--public-fixture",
                "examples/scenarios/android-settings.json",
            ],
        },
    ],
}


def _utc_now() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _default_runner(command: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True)


def _step_not_run(step: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "name": step["name"],
        "command": list(step["command"]),
        "status": "not_run",
    }


def build_evidence(
    target: str,
    enabled: bool,
    now: Clock = _utc_now,
) -> Dict[str, Any]:
    """Build a skipped evidence bundle without executing commands."""
    if target not in _TARGETS:
        raise ValueError(f"unknown smoke target: {target}")
    timestamp = now()
    return {
        "schema_version": 1,
        "target": target,
        "enabled": enabled,
        "enable_env": SMOKE_ENABLE_ENV,
        "status": "skipped",
        "started_at": timestamp,
        "finished_at": timestamp,
        "steps": [_step_not_run(step) for step in _TARGETS[target]],
    }


def run_smoke_harness(
    target: str,
    enabled: bool,
    runner: CommandRunner = _default_runner,
    now: Clock = _utc_now,
) -> Dict[str, Any]:
    """Run an opt-in smoke target and return a JSON-serializable evidence dict."""
    evidence = build_evidence(target=target, enabled=enabled, now=now)
    if not enabled:
        return evidence

    status = "passed"
    steps: List[Dict[str, Any]] = []
    for index, step in enumerate(_TARGETS[target]):
        command = list(step["command"])
        completed = runner(command)
        step_status = "passed" if completed.returncode == 0 else "failed"
        steps.append(
            {
                "name": step["name"],
                "command": command,
                "status": step_status,
                "returncode": completed.returncode,
                "stdout": getattr(completed, "stdout", ""),
                "stderr": getattr(completed, "stderr", ""),
            }
        )
        if step_status == "failed":
            status = "failed"
            steps.extend(_step_not_run(s) for s in _TARGETS[target][index + 1 :])
            break

    evidence["status"] = status
    evidence["finished_at"] = now()
    evidence["steps"] = steps
    return evidence


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit opt-in real smoke evidence as JSON."
    )
    parser.add_argument("--target", choices=sorted(_TARGETS), required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--run-real", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Optional[List[str]] = None,
    environ: Optional[Mapping[str, str]] = None,
    runner: CommandRunner = _default_runner,
    now: Clock = _utc_now,
) -> int:
    args = _parse_args(argv)
    env = os.environ if environ is None else environ
    enabled = args.run_real or env.get(SMOKE_ENABLE_ENV) == "1"
    evidence = run_smoke_harness(
        target=args.target,
        enabled=enabled,
        runner=runner,
        now=now,
    )
    output = json.dumps(evidence, indent=2)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 1 if evidence["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
