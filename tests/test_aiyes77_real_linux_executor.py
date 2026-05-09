"""AIYES-77: real Linux scenario executor wiring."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from click.testing import CliRunner

from aiyes.cli.main import cli
from aiyes.domain.scenario import ScenarioStep, validate_scenario_document
from aiyes.domain.use_cases.scenario_run import ScenarioRunResult


@dataclasses.dataclass
class RecordingUseCase:
    result: Any
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.result


def test_real_linux_executor_maps_steps_to_existing_use_cases() -> None:
    from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor

    start = RecordingUseCase(SimpleNamespace(session_id="s1", backend="linux"))
    inspect = RecordingUseCase(SimpleNamespace(tree={"roots": []}, screenshot_path=None))
    find = RecordingUseCase(
        [SimpleNamespace(id="node-1", role="button", name="OK", actions=("click",))]
    )
    action = RecordingUseCase(SimpleNamespace(status="ok", action="click", target="node-1"))
    type_text = RecordingUseCase(SimpleNamespace(status="ok"))
    screenshot = RecordingUseCase(SimpleNamespace(path="/tmp/shot.png", data=None))
    stop = RecordingUseCase(SimpleNamespace(status="ok", session_id="s1"))
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=inspect,
        find=find,
        action=action,
        type_text=type_text,
        screenshot=screenshot,
        session_stop=stop,
    )

    steps = [
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "gedit", "name": "fixture", "wait_seconds": 0.1},
        ),
        ScenarioStep(id="inspect", kind="inspect", parameters={"tree_depth": 2}),
        ScenarioStep(id="find", kind="find", parameters={"role": "button", "name": "OK"}),
        ScenarioStep(
            id="click",
            kind="action",
            parameters={"source": "find", "action": "click"},
        ),
        ScenarioStep(id="type", kind="type_text", parameters={"text": "hello"}),
        ScenarioStep(id="shot", kind="screenshot", parameters={}),
        ScenarioStep(id="stop", kind="stop_session", parameters={}),
    ]

    results = [executor.execute(step) for step in steps]

    assert [result.status for result in results] == ["passed"] * len(steps)
    assert start.calls == [
        {
            "app_command": "gedit",
            "app_args": [],
            "resolution": "1280x800",
            "color_depth": 24,
            "wait": 0.1,
            "name": "fixture",
            "backend": "linux",
            "device_serial": None,
        }
    ]
    assert inspect.calls == [
        {
            "session_id": "s1",
            "no_screenshot": False,
            "no_tree": False,
            "tree_depth": 2,
            "no_prune": False,
            "screenshot_base64": False,
            "focus_window": None,
        }
    ]
    assert find.calls == [
        {
            "session_id": "s1",
            "role": "button",
            "name_pattern": "OK",
            "state": None,
            "no_prune": False,
        }
    ]
    assert action.calls == [
        {
            "session_id": "s1",
            "node_id": "node-1",
            "action_name": "click",
            "value": None,
        }
    ]
    assert type_text.calls == [{"session_id": "s1", "text": "hello", "delay_ms": 0}]
    assert screenshot.calls == [
        {
            "session_id": "s1",
            "output_path": None,
            "base64": False,
            "region": None,
            "node_id": None,
        }
    ]
    assert stop.calls == [{"session_id": "s1"}]
    assert results[2].output["nodes"][0]["id"] == "node-1"
    assert results[3].output["status"] == "ok"


def test_real_linux_executor_returns_failed_result_for_missing_action_source() -> None:
    from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor

    executor = ScenarioUseCaseExecutor(
        session_start=RecordingUseCase(SimpleNamespace(session_id="s1")),
        inspect=RecordingUseCase(SimpleNamespace()),
        find=RecordingUseCase([]),
        action=RecordingUseCase(SimpleNamespace()),
        type_text=RecordingUseCase(SimpleNamespace()),
        screenshot=RecordingUseCase(SimpleNamespace()),
        session_stop=RecordingUseCase(SimpleNamespace()),
    )
    executor.execute(ScenarioStep(id="start", kind="start_session", parameters={}))

    result = executor.execute(
        ScenarioStep(
            id="click",
            kind="action",
            parameters={"source": "missing", "action": "click"},
        )
    )

    assert result.status == "failed"
    assert "missing" in result.error


def test_cli_scenario_run_defaults_to_dry_run_with_real_executor_available(
    tmp_path: Path,
) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "dry-default",
                "title": "Dry default",
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
    assert payload["steps"][0]["output"]["dry_run"] is True


def test_cli_scenario_run_uses_real_executor_only_when_requested(
    monkeypatch: Any, tmp_path: Path
) -> None:
    scenario = validate_scenario_document(
        {
            "schema_version": 1,
            "id": "real-opt-in",
            "title": "Real opt in",
            "target": "linux",
            "steps": [{"id": "inspect", "kind": "inspect"}],
            "evidence_policy": {"bundle": True, "redact_environment": True},
        }
    ).scenario
    assert scenario is not None
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text("{}", encoding="utf-8")

    fake_loader = SimpleNamespace(ok=True, issues=(), scenario=scenario)

    class FakeRealRun:
        def __init__(self) -> None:
            self.called = False

        def execute(self, loaded_scenario: object) -> ScenarioRunResult:
            self.called = loaded_scenario is scenario
            return ScenarioRunResult(
                scenario_id="real-opt-in",
                status="passed",
                steps=(),
            )

    fake_real = FakeRealRun()
    monkeypatch.setattr("aiyes.cli.main.load_scenario_file", lambda *_a, **_k: fake_loader)
    monkeypatch.setattr("aiyes.cli.main.scenario_real_run_uc", fake_real)

    result = CliRunner().invoke(cli, ["scenario", "run", "--real", str(scenario_path)])

    assert result.exit_code == 0
    assert fake_real.called is True
