"""AIYES-71: release scenario schema and validation."""

from __future__ import annotations

import json
from pathlib import Path

from aiyes.adapters.scenario_loader import load_scenario_file
from aiyes.domain.scenario import validate_scenario_document


def _valid_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "linux-gedit-smoke",
        "title": "Linux gedit smoke",
        "target": "linux",
        "steps": [
            {"id": "start", "kind": "start_session"},
            {"id": "inspect", "kind": "inspect"},
            {"id": "stop", "kind": "stop_session"},
        ],
        "evidence_policy": {
            "bundle": True,
            "redact_environment": True,
        },
    }


def test_valid_minimal_scenario_parses_to_immutable_value_objects() -> None:
    result = validate_scenario_document(_valid_document(), public_fixture=True)

    assert result.ok is True
    assert result.scenario is not None
    assert result.scenario.schema_version == 1
    assert result.scenario.id == "linux-gedit-smoke"
    assert result.scenario.target == "linux"
    assert [step.kind for step in result.scenario.steps] == [
        "start_session",
        "inspect",
        "stop_session",
    ]

    try:
        result.scenario.steps[0] = result.scenario.steps[1]  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("scenario steps must be immutable")


def test_missing_required_fields_return_deterministic_validation_errors() -> None:
    document = _valid_document()
    del document["schema_version"]

    result = validate_scenario_document(document, public_fixture=True)

    assert result.ok is False
    assert result.scenario is None
    assert [(issue.path, issue.code) for issue in result.issues] == [
        ("schema_version", "missing_required")
    ]


def test_unsupported_step_kind_is_rejected_before_execution() -> None:
    document = _valid_document()
    document["steps"] = [{"id": "plan", "kind": "ask_llm"}]

    result = validate_scenario_document(document, public_fixture=True)

    assert result.ok is False
    assert result.issues[0].path == "steps[0].kind"
    assert result.issues[0].code == "unsupported_step_kind"


def test_start_session_requires_cleanup_step_or_cleanup_block() -> None:
    document = _valid_document()
    document["steps"] = [{"id": "start", "kind": "start_session"}]

    result = validate_scenario_document(document, public_fixture=True)

    assert result.ok is False
    assert ("cleanup", "cleanup_required") in [
        (issue.path, issue.code) for issue in result.issues
    ]


def test_public_fixture_rejects_private_references_recursively() -> None:
    document = _valid_document()
    document["steps"] = [
        {
            "id": "start",
            "kind": "start_session",
            "command": ["/home/example/private-app/bin/run"],
        },
        {"id": "stop", "kind": "stop_session"},
    ]

    result = validate_scenario_document(document, public_fixture=True)

    assert result.ok is False
    assert result.issues[0].code == "private_reference"
    assert result.issues[0].path == "steps[0].command[0]"


def test_loader_returns_json_parse_errors_without_raising(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text("{not-json", encoding="utf-8")

    result = load_scenario_file(scenario_path, public_fixture=True)

    assert result.ok is False
    assert result.issues[0].path == "$"
    assert result.issues[0].code == "invalid_json"


def test_loader_validates_json_file(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_valid_document()), encoding="utf-8")

    result = load_scenario_file(scenario_path, public_fixture=True)

    assert result.ok is True
    assert result.scenario is not None
    assert result.scenario.id == "linux-gedit-smoke"

