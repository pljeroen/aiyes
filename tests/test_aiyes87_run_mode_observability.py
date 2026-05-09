"""AIYES-87: explicit scenario run mode observability."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aiyes.cli.main import cli


def test_cli_scenario_run_reports_dry_run_mode(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "mode-smoke",
                "title": "Mode smoke",
                "target": "linux",
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": True, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["scenario", "run", str(scenario_path)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["mode"] == "dry_run"
