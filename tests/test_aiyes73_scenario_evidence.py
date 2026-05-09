"""AIYES-73: scenario assertions and evidence bundle."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.cli.main import cli
from aiyes.domain.scenario_assertions import evaluate_scenario_assertion
from aiyes.domain.use_cases.scenario_run import ScenarioRunResult, ScenarioRunStepResult


def test_tree_non_empty_assertion_passes_for_children() -> None:
    result = evaluate_scenario_assertion(
        {"id": "tree", "kind": "tree_non_empty", "source": "inspect"},
        {"inspect": {"tree": {"children": [{"id": "n_001"}]}}},
    )

    assert result.status == "passed"


def test_node_exists_assertion_finds_recursive_node_id() -> None:
    result = evaluate_scenario_assertion(
        {"id": "node", "kind": "node_exists", "node_id": "n_002", "source": "inspect"},
        {
            "inspect": {
                "tree": {
                    "id": "n_001",
                    "children": [{"id": "n_002", "name": "Submit"}],
                }
            }
        },
    )

    assert result.status == "passed"


def test_action_ok_assertion_rejects_error_status() -> None:
    result = evaluate_scenario_assertion(
        {"id": "action", "kind": "action_ok", "source": "click"},
        {"click": {"status": "error", "reason": "not actionable"}},
    )

    assert result.status == "failed"
    assert "not ok" in result.message


def test_screenshot_exists_assertion_checks_filesystem(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")

    result = evaluate_scenario_assertion(
        {"id": "shot", "kind": "screenshot_exists", "path": str(screenshot)},
        {},
    )

    assert result.status == "passed"


def test_text_or_name_matches_searches_tree_text() -> None:
    result = evaluate_scenario_assertion(
        {"id": "text", "kind": "text_or_name_matches", "pattern": "hello"},
        {"inspect": {"tree": {"name": "Editor", "children": [{"text": "hello"}]}}},
    )

    assert result.status == "passed"


def test_tree_changed_assertion_uses_diff_payload() -> None:
    result = evaluate_scenario_assertion(
        {"id": "diff", "kind": "tree_changed", "source": "diff"},
        {"diff": {"added": [], "removed": [], "changed": [{"id": "n_001"}]}},
    )

    assert result.status == "passed"


def test_prerequisite_skip_assertion_requires_skip_status() -> None:
    result = evaluate_scenario_assertion(
        {"id": "skip", "kind": "prerequisite_skip", "source": "gedit"},
        {"gedit": {"status": "skipped", "reason": "gedit not found"}},
    )

    assert result.status == "passed"


def test_evidence_bundle_writes_run_steps_artifacts_and_redactions(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    run = ScenarioRunResult(
        scenario_id="evidence-smoke",
        status="passed",
        steps=(
            ScenarioRunStepResult(
                step_id="inspect",
                kind="inspect",
                status="passed",
                output={"token": "safe"},
            ),
        ),
    )

    write_scenario_evidence_bundle(
        bundle_dir,
        run,
        environment={"API_TOKEN": "secret", "DISPLAY": ":99", "HOME": "/home/example"},
    )

    assert (bundle_dir / "artifacts").is_dir()
    run_json = json.loads((bundle_dir / "run.json").read_text(encoding="utf-8"))
    assert run_json["scenario_id"] == "evidence-smoke"
    assert run_json["environment"]["API_TOKEN"] == "***"
    assert run_json["environment"]["DISPLAY"] == ":99"
    assert "HOME" not in run_json["environment"]
    steps = (bundle_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(steps[0])["step_id"] == "inspect"
    redactions = json.loads((bundle_dir / "redactions.json").read_text(encoding="utf-8"))
    assert redactions["redacted_keys"] == ["API_TOKEN"]


def test_cli_scenario_run_writes_evidence_bundle(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    evidence_dir = tmp_path / "evidence"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "cli-evidence",
                "title": "CLI evidence",
                "target": "linux",
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": True, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        ["scenario", "run", str(scenario_path), "--evidence-dir", str(evidence_dir)],
    )

    assert result.exit_code == 0
    assert (evidence_dir / "run.json").exists()
    assert (evidence_dir / "steps.jsonl").exists()
