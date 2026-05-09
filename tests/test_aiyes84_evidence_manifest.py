"""AIYES-84: evidence manifest."""

from __future__ import annotations

import json
from pathlib import Path

from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.domain.use_cases.scenario_run import ScenarioRunResult, ScenarioRunStepResult


def test_evidence_bundle_writes_manifest_entry_point(tmp_path: Path) -> None:
    run = ScenarioRunResult(
        scenario_id="manifest-smoke",
        status="passed",
        mode="dry_run",
        steps=(
            ScenarioRunStepResult(
                step_id="inspect",
                kind="inspect",
                status="passed",
                output={},
            ),
        ),
    )

    write_scenario_evidence_bundle(tmp_path, run, environment={})

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scenario_id"] == "manifest-smoke"
    assert manifest["status"] == "passed"
    assert manifest["mode"] == "dry_run"
    assert manifest["primary_files"] == ["manifest.json", "run.json", "steps.jsonl"]
    assert manifest["inspection_order"][0] == "manifest.json"
    assert manifest["step_count"] == 1
