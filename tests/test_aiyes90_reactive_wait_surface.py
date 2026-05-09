from __future__ import annotations

import json

from click.testing import CliRunner

from aiyes.cli.main import cli
from aiyes.cli.presenter import format_reactive_wait
from aiyes.domain.reactive_wait import ReactiveWaitResult


def test_format_reactive_wait_uses_contract_shape() -> None:
    payload = json.loads(
        format_reactive_wait(
            ReactiveWaitResult.unsupported_condition(
                condition="focus-change",
                backend="unknown",
                elapsed_ms=0,
                polls=0,
            )
        )
    )

    assert payload == {
        "condition": "focus-change",
        "matched": False,
        "timeout": False,
        "backend": "unknown",
        "source": "unsupported",
        "elapsed_ms": 0,
        "polls": 0,
        "events": [],
        "failure_code": "unsupported_condition",
        "next_actions": ["Use inspect or wait-stable for this backend/condition."],
    }


def test_help_json_includes_wait_reactive_command() -> None:
    result = CliRunner().invoke(cli, ["help-json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    command = next(
        item for item in payload["commands"] if item["name"] == "wait-reactive"
    )
    assert command["name"] == "wait-reactive"
    properties = command["parameters"]["properties"]
    assert {"session_id", "timeout", "quiet", "poll_interval"} <= set(properties)
    assert "condition" in command["parameters"]["required"]
    assert "name_pattern" in properties
