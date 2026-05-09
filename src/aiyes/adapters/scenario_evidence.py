"""Evidence bundle writer for deterministic scenario runs."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Mapping, Optional

from aiyes.domain.use_cases.scenario_run import ScenarioRunResult


_SECRET_KEY_FRAGMENTS = ("TOKEN", "SECRET", "PASSWORD", "KEY")
_SAFE_ENV_KEYS = frozenset(
    (
        "ADB_SERIAL",
        "ANDROID_SERIAL",
        "CI",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_SESSION_TYPE",
    )
)


def write_scenario_evidence_bundle(
    bundle_dir: Path,
    run: ScenarioRunResult,
    environment: Optional[Mapping[str, str]] = None,
) -> None:
    """Write a reviewable scenario evidence bundle."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = bundle_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    env = dict(os.environ if environment is None else environment)
    redacted_env, redacted_keys = _redact_environment(env)
    primary_files = ["manifest.json", "run.json", "steps.jsonl"]
    manifest = {
        "schema_version": 1,
        "scenario_id": run.scenario_id,
        "status": run.status,
        "mode": run.mode,
        "primary_files": primary_files,
        "artifacts_dir": "artifacts",
        "step_count": len(run.steps),
        "inspection_order": primary_files + ["redactions.json", "artifacts/"],
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "run.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scenario_id": run.scenario_id,
                "status": run.status,
                "mode": run.mode,
                "failure_code": run.failure_code,
                "next_actions": [dataclasses.asdict(action) for action in run.next_actions],
                "environment": redacted_env,
                "artifacts_dir": "artifacts",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with (bundle_dir / "steps.jsonl").open("w", encoding="utf-8") as f:
        for step in run.steps:
            f.write(json.dumps(dataclasses.asdict(step), sort_keys=True) + "\n")
    (bundle_dir / "redactions.json").write_text(
        json.dumps(
            {
                "redacted_keys": redacted_keys,
                "redaction_count": len(redacted_keys),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _redact_environment(env: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    redacted: dict[str, str] = {}
    redacted_keys: list[str] = []
    for key in sorted(env):
        if _is_secret_key(key):
            redacted[key] = "***"
            redacted_keys.append(key)
        elif key in _SAFE_ENV_KEYS:
            redacted[key] = env[key]
    return redacted, redacted_keys


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(fragment in upper for fragment in _SECRET_KEY_FRAGMENTS)
