"""JSON release-scenario loader adapter."""

from __future__ import annotations

import json
from pathlib import Path

from aiyes.domain.scenario import (
    ScenarioValidationIssue,
    ScenarioValidationResult,
    validate_scenario_document,
)


def load_scenario_file(
    path: Path, public_fixture: bool = False
) -> ScenarioValidationResult:
    """Load and validate a JSON scenario file without executing it."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ScenarioValidationResult(
            scenario=None,
            issues=(
                ScenarioValidationIssue(
                    path="$",
                    code="invalid_json",
                    message=str(exc),
                ),
            ),
        )
    except OSError as exc:
        return ScenarioValidationResult(
            scenario=None,
            issues=(
                ScenarioValidationIssue(
                    path=str(path),
                    code="read_error",
                    message=str(exc),
                ),
            ),
        )
    return validate_scenario_document(document, public_fixture=public_fixture)

