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
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from aiyes import __version__ as AIYES_VERSION


SMOKE_ENABLE_ENV = "AIYES_RUN_REAL_SMOKE"
SOCIALZZZ_TARGET = "socialzzz-android"
SOCIALZZZ_DEVICE_SERIAL_ENV = "AIYES_SOCIALZZZ_DEVICE_SERIAL"
SOCIALZZZ_APP_PACKAGE_ENV = "AIYES_SOCIALZZZ_APP_PACKAGE"
SOCIALZZZ_SCENARIO_DIR_ENV = "AIYES_SOCIALZZZ_SCENARIO_DIR"
SOCIALZZZ_SCENARIOS_ENV = "AIYES_SOCIALZZZ_SCENARIOS"
ANDROID_ADB_ENV = "AIYES_ANDROID_ADB"
SOCIALZZZ_DEFAULT_SCENARIOS = [
    "compositor-share-image-happy-path.json",
    "compositor-share-video-happy-path.json",
    "demo-seed-smoke.json",
    "edit-target-market-regression.json",
    "delete-everything-dialog-smoke.json",
]

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


def _target_choices() -> List[str]:
    return sorted([*_TARGETS, SOCIALZZZ_TARGET])


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


def _non_empty_env(env: Mapping[str, str], key: str) -> Optional[str]:
    value = env.get(key)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _socialzzz_scenario_inputs(env: Mapping[str, str]) -> List[Tuple[str, str]]:
    raw = _non_empty_env(env, SOCIALZZZ_SCENARIOS_ENV)
    filenames = (
        [item.strip() for item in raw.split(",") if item.strip()]
        if raw is not None
        else SOCIALZZZ_DEFAULT_SCENARIOS
    )
    return [(Path(filename).stem, filename) for filename in filenames]


def _empty_observed_scroll_methods() -> Dict[str, Any]:
    return {
        "native_scroll": False,
        "region_swipe": False,
        "advisory_role": False,
        "strict_role": False,
        "scenarios": [],
    }


def _missing_env(name: str, reason: str) -> Dict[str, str]:
    return {"code": "missing_env", "name": name, "reason": reason}


def _missing_prerequisite(code: str, name: str, reason: str) -> Dict[str, str]:
    return {"code": code, "name": name, "reason": reason}


def _skip_for_missing(
    evidence: Dict[str, Any],
    missing: List[Dict[str, str]],
    now: Clock,
) -> Dict[str, Any]:
    evidence["status"] = "skipped"
    evidence["failure_reason"] = "missing_prerequisites"
    evidence["missing_prerequisites"] = missing
    evidence["finished_at"] = now()
    return evidence


def _socialzzz_missing_env(env: Mapping[str, str]) -> List[Dict[str, str]]:
    missing: List[Dict[str, str]] = []
    if _non_empty_env(env, SOCIALZZZ_DEVICE_SERIAL_ENV) is None:
        missing.append(
            _missing_env(
                SOCIALZZZ_DEVICE_SERIAL_ENV,
                "set AIYES_SOCIALZZZ_DEVICE_SERIAL to the adb device serial",
            )
        )
    if _non_empty_env(env, SOCIALZZZ_APP_PACKAGE_ENV) is None:
        missing.append(
            _missing_env(
                SOCIALZZZ_APP_PACKAGE_ENV,
                "set AIYES_SOCIALZZZ_APP_PACKAGE to the installed Socialzzz package",
            )
        )
    if _non_empty_env(env, SOCIALZZZ_SCENARIO_DIR_ENV) is None:
        missing.append(
            _missing_env(
                SOCIALZZZ_SCENARIO_DIR_ENV,
                "set AIYES_SOCIALZZZ_SCENARIO_DIR to the Socialzzz test/scenarios directory",
            )
        )
    return missing


def _socialzzz_missing_files(env: Mapping[str, str]) -> List[Dict[str, str]]:
    scenario_dir = _non_empty_env(env, SOCIALZZZ_SCENARIO_DIR_ENV)
    if scenario_dir is None:
        return []
    root = Path(scenario_dir)
    if not root.is_dir():
        return [
            _missing_prerequisite(
                "missing_scenario_dir",
                SOCIALZZZ_SCENARIO_DIR_ENV,
                f"scenario directory does not exist: {root}",
            )
        ]
    missing = []
    for _scenario_name, filename in _socialzzz_scenario_inputs(env):
        path = root / filename
        if not path.is_file():
            missing.append(
                _missing_prerequisite(
                    "missing_scenario_file",
                    filename,
                    f"scenario file does not exist: {path}",
                )
            )
    return missing


def _run_socialzzz_prerequisite(
    runner: CommandRunner,
    command: List[str],
    missing_code: str,
    missing_name: str,
    missing_reason: str,
    executable_missing_code: Optional[str] = None,
    executable_missing_name: Optional[str] = None,
    executable_missing_reason: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, str]]]:
    try:
        completed = runner(command)
    except FileNotFoundError:
        code = executable_missing_code or missing_code
        name = executable_missing_name or missing_name
        reason = executable_missing_reason or missing_reason
        step = {
            "name": missing_name,
            "command": command,
            "status": "skipped",
            "failure_reason": reason,
        }
        return step, _missing_prerequisite(code, name, reason)

    step_status = "passed" if completed.returncode == 0 else "skipped"
    step = {
        "name": missing_name,
        "command": command,
        "status": step_status,
        "returncode": completed.returncode,
        "stdout": getattr(completed, "stdout", ""),
        "stderr": getattr(completed, "stderr", ""),
        "failure_reason": "" if step_status == "passed" else missing_reason,
    }
    if step_status == "passed":
        return step, None
    return step, _missing_prerequisite(missing_code, missing_name, missing_reason)


def _scenario_failure_reason(completed: Any, payload: Optional[Dict[str, Any]]) -> str:
    if payload is not None:
        failure_code = payload.get("failure_code")
        if isinstance(failure_code, str) and failure_code:
            return failure_code
        status = payload.get("status")
        if isinstance(status, str) and status not in ("", "passed"):
            return status
    stderr = getattr(completed, "stderr", "")
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()
    if completed.returncode != 0:
        return f"returncode_{completed.returncode}"
    return ""


def _parse_json_stdout(stdout: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(stdout, str) or stdout.strip() == "":
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _socialzzz_android_launch_command(
    adb: str,
    device_serial: str,
    app_package: str,
) -> List[str]:
    return [
        adb,
        "-s",
        device_serial,
        "shell",
        "monkey",
        "-p",
        app_package,
        "1",
    ]


def _bind_socialzzz_android_scenario(
    scenario_path: Path,
    output_dir: Path,
    adb: str,
    device_serial: str,
    app_package: str,
) -> Path:
    document = json.loads(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"scenario root must be an object: {scenario_path}")

    bound = dict(document)
    launch_command = _socialzzz_android_launch_command(
        adb=adb,
        device_serial=device_serial,
        app_package=app_package,
    )

    prerequisites = bound.get("prerequisites")
    if isinstance(prerequisites, list):
        bound_prerequisites = []
        for prerequisite in prerequisites:
            if not isinstance(prerequisite, dict):
                bound_prerequisites.append(prerequisite)
                continue
            item = dict(prerequisite)
            if item.get("kind") == "android_device":
                item["serial"] = device_serial
            bound_prerequisites.append(item)
        bound["prerequisites"] = bound_prerequisites

    steps = bound.get("steps")
    if isinstance(steps, list):
        bound_steps = []
        for step in steps:
            if not isinstance(step, dict):
                bound_steps.append(step)
                continue
            item = dict(step)
            if item.get("kind") == "start_session":
                item["backend"] = "android"
                item["device_serial"] = device_serial
                item["command"] = list(launch_command)
            bound_steps.append(item)
        bound["steps"] = bound_steps

    bound_path = output_dir / scenario_path.name
    bound_path.write_text(json.dumps(bound, indent=2), encoding="utf-8")
    return bound_path


def _observed_methods_for_scenario(
    scenario_name: str,
    payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    observed: Dict[str, Any] = {
        "name": scenario_name,
        "native_scroll": False,
        "region_swipe": False,
        "advisory_role": False,
        "strict_role": False,
        "steps": [],
    }
    if payload is None:
        return observed
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return observed
    for step in steps:
        if not isinstance(step, dict):
            continue
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        step_observed = {
            "step_id": step.get("step_id", ""),
            "kind": step.get("kind", ""),
            "status": step.get("status", ""),
            "native_scroll": False,
            "region_swipe": False,
            "advisory_role": False,
            "strict_role": False,
        }
        role_match = output.get("role_match")
        if role_match == "advisory":
            observed["advisory_role"] = True
            step_observed["advisory_role"] = True
        if role_match == "exact":
            observed["strict_role"] = True
            step_observed["strict_role"] = True
        scroll_attempts = output.get("scroll_attempts")
        if isinstance(scroll_attempts, list):
            for attempt in scroll_attempts:
                if not isinstance(attempt, dict):
                    continue
                method = attempt.get("method")
                if method == "native_scroll":
                    observed["native_scroll"] = True
                    step_observed["native_scroll"] = True
                if method in ("region_swipe", "scrollable_region_swipe"):
                    observed["region_swipe"] = True
                    step_observed["region_swipe"] = True
        if any(
            step_observed[key]
            for key in ("native_scroll", "region_swipe", "advisory_role", "strict_role")
        ):
            observed["steps"].append(step_observed)
    return observed


def _merge_observed_methods(
    aggregate: Dict[str, Any],
    scenario_observed: Dict[str, Any],
) -> None:
    aggregate["scenarios"].append(scenario_observed)
    for key in ("native_scroll", "region_swipe", "advisory_role", "strict_role"):
        aggregate[key] = bool(aggregate[key] or scenario_observed[key])


def build_evidence(
    target: str,
    enabled: bool,
    environ: Optional[Mapping[str, str]] = None,
    now: Clock = _utc_now,
) -> Dict[str, Any]:
    """Build a skipped evidence bundle without executing commands."""
    if target not in _target_choices():
        raise ValueError(f"unknown smoke target: {target}")
    timestamp = now()
    env = os.environ if environ is None else environ
    evidence: Dict[str, Any] = {
        "schema_version": 1,
        "target": target,
        "enabled": enabled,
        "enable_env": SMOKE_ENABLE_ENV,
        "status": "skipped",
        "started_at": timestamp,
        "finished_at": timestamp,
    }
    if target == SOCIALZZZ_TARGET:
        evidence.update(
            {
                "failure_reason": "not_enabled" if not enabled else "",
                "aiyes_version": AIYES_VERSION,
                "device_serial": _non_empty_env(env, SOCIALZZZ_DEVICE_SERIAL_ENV),
                "app_package": _non_empty_env(env, SOCIALZZZ_APP_PACKAGE_ENV),
                "scenario_names": [
                    scenario_name
                    for scenario_name, _filename in _socialzzz_scenario_inputs(env)
                ],
                "missing_prerequisites": [],
                "observed_scroll_methods": _empty_observed_scroll_methods(),
                "steps": [],
            }
        )
    else:
        evidence["steps"] = [_step_not_run(step) for step in _TARGETS[target]]
    return evidence


def _run_socialzzz_android_harness(
    enabled: bool,
    runner: CommandRunner,
    environ: Mapping[str, str],
    now: Clock,
) -> Dict[str, Any]:
    evidence = build_evidence(
        target=SOCIALZZZ_TARGET,
        enabled=enabled,
        environ=environ,
        now=now,
    )
    if not enabled:
        return evidence

    missing = _socialzzz_missing_env(environ)
    if missing:
        return _skip_for_missing(evidence, missing, now)

    missing = _socialzzz_missing_files(environ)
    if missing:
        return _skip_for_missing(evidence, missing, now)

    adb = _non_empty_env(environ, ANDROID_ADB_ENV) or "adb"
    device_serial = str(evidence["device_serial"])
    app_package = str(evidence["app_package"])
    steps: List[Dict[str, Any]] = []

    step, missing_adb = _run_socialzzz_prerequisite(
        runner,
        [adb, "-s", device_serial, "get-state"],
        "missing_android_device",
        "adb-device",
        f"adb device is not available in device state: {device_serial}",
        "missing_adb",
        ANDROID_ADB_ENV,
        "adb executable not found; set AIYES_ANDROID_ADB or add adb to PATH",
    )
    steps.append(step)
    if missing_adb is not None:
        evidence["steps"] = steps
        return _skip_for_missing(evidence, [missing_adb], now)

    package_missing_reason = (
        f"Socialzzz package is not installed on {device_serial}: {app_package}"
    )
    step, missing_package = _run_socialzzz_prerequisite(
        runner,
        [adb, "-s", device_serial, "shell", "pm", "path", app_package],
        "missing_android_package",
        "socialzzz-package",
        package_missing_reason,
    )
    package_stdout = str(step.get("stdout", ""))
    if missing_package is None and not package_stdout.strip().startswith("package:"):
        step["status"] = "skipped"
        step["failure_reason"] = package_missing_reason
        missing_package = _missing_prerequisite(
            "missing_android_package",
            "socialzzz-package",
            package_missing_reason,
        )
    steps.append(step)
    if missing_package is not None:
        evidence["steps"] = steps
        return _skip_for_missing(evidence, [missing_package], now)

    scenario_dir_env = _non_empty_env(environ, SOCIALZZZ_SCENARIO_DIR_ENV)
    scenario_dir = Path(str(scenario_dir_env))
    observed = _empty_observed_scroll_methods()
    status = "passed"
    with tempfile.TemporaryDirectory(prefix="aiyes-socialzzz-") as temp_dir:
        bound_dir = Path(temp_dir)
        for scenario_name, filename in _socialzzz_scenario_inputs(environ):
            scenario_path = scenario_dir / filename
            bound_scenario_path = _bind_socialzzz_android_scenario(
                scenario_path=scenario_path,
                output_dir=bound_dir,
                adb=adb,
                device_serial=device_serial,
                app_package=app_package,
            )
            command = ["aieyes", "scenario", "run", "--real", str(bound_scenario_path)]
            completed = runner(command)
            payload = _parse_json_stdout(getattr(completed, "stdout", ""))
            failure_reason = _scenario_failure_reason(completed, payload)
            scenario_status = (
                "failed" if completed.returncode != 0 or failure_reason else "passed"
            )
            if scenario_status == "failed":
                status = "failed"
            _merge_observed_methods(
                observed,
                _observed_methods_for_scenario(scenario_name, payload),
            )
            steps.append(
                {
                    "name": scenario_name,
                    "command": command,
                    "status": scenario_status,
                    "returncode": completed.returncode,
                    "stdout": getattr(completed, "stdout", ""),
                    "stderr": getattr(completed, "stderr", ""),
                    "failure_reason": failure_reason,
                }
            )
            if scenario_status == "failed":
                break

    evidence["status"] = status
    evidence["failure_reason"] = "" if status == "passed" else "scenario_failed"
    evidence["finished_at"] = now()
    evidence["steps"] = steps
    evidence["observed_scroll_methods"] = observed
    return evidence


def run_smoke_harness(
    target: str,
    enabled: bool,
    runner: CommandRunner = _default_runner,
    environ: Optional[Mapping[str, str]] = None,
    now: Clock = _utc_now,
) -> Dict[str, Any]:
    """Run an opt-in smoke target and return a JSON-serializable evidence dict."""
    env = os.environ if environ is None else environ
    if target == SOCIALZZZ_TARGET:
        return _run_socialzzz_android_harness(enabled, runner, env, now)

    evidence = build_evidence(target=target, enabled=enabled, environ=env, now=now)
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
    parser.add_argument("--target", choices=_target_choices(), required=True)
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
        environ=env,
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
