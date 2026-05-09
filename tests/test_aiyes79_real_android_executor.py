"""AIYES-79: real Android scenario executor wiring."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

from aiyes.domain.scenario import ScenarioStep


@dataclasses.dataclass
class RecordingUseCase:
    result: Any
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.result


def test_android_executor_defaults_start_session_to_settings_intent() -> None:
    from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor

    start = RecordingUseCase(SimpleNamespace(session_id="a1", backend="android"))
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=RecordingUseCase(SimpleNamespace(tree={"roots": []})),
        find=RecordingUseCase([]),
        action=RecordingUseCase(SimpleNamespace(status="ok")),
        type_text=RecordingUseCase(SimpleNamespace(status="ok")),
        screenshot=RecordingUseCase(SimpleNamespace(path="/tmp/android.png")),
        session_stop=RecordingUseCase(SimpleNamespace(status="ok")),
    )

    result = executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={
                "backend": "android",
                "name": "settings-smoke",
                "device_serial": "emulator-5554",
            },
        )
    )

    assert result.status == "passed"
    assert start.calls == [
        {
            "app_command": "adb",
            "app_args": [
                "shell",
                "am",
                "start",
                "-a",
                "android.settings.SETTINGS",
            ],
            "resolution": "1280x800",
            "color_depth": 24,
            "wait": 2.0,
            "name": "settings-smoke",
            "backend": "android",
            "device_serial": "emulator-5554",
        }
    ]


def test_android_executor_resolves_auto_device_serial(monkeypatch: Any) -> None:
    from aiyes.adapters import scenario_use_case_executor
    from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor

    def fake_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout="List of devices attached\nemulator-5554\tdevice\n",
        )

    monkeypatch.setattr(scenario_use_case_executor.subprocess, "run", fake_run)
    start = RecordingUseCase(SimpleNamespace(session_id="a1", backend="android"))
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=RecordingUseCase(SimpleNamespace(tree={"roots": []})),
        find=RecordingUseCase([]),
        action=RecordingUseCase(SimpleNamespace(status="ok")),
        type_text=RecordingUseCase(SimpleNamespace(status="ok")),
        screenshot=RecordingUseCase(SimpleNamespace(path="/tmp/android.png")),
        session_stop=RecordingUseCase(SimpleNamespace(status="ok")),
    )

    result = executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={
                "backend": "android",
                "name": "settings-smoke",
                "device_serial": "auto",
            },
        )
    )

    assert result.status == "passed"
    assert start.calls[0]["device_serial"] == "emulator-5554"


def test_android_executor_maps_find_action_navigate_screenshot_and_stop() -> None:
    from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor

    navigate = RecordingUseCase(SimpleNamespace(status="ok"))
    action = RecordingUseCase(SimpleNamespace(status="ok", action="click", target="n1"))
    screenshot = RecordingUseCase(SimpleNamespace(path="/tmp/android.png"))
    stop = RecordingUseCase(SimpleNamespace(status="ok", session_id="a1"))
    executor = ScenarioUseCaseExecutor(
        session_start=RecordingUseCase(SimpleNamespace(session_id="a1", backend="android")),
        inspect=RecordingUseCase(SimpleNamespace(tree={"roots": []})),
        find=RecordingUseCase([SimpleNamespace(id="n1", role="button", name="Display")]),
        action=action,
        type_text=RecordingUseCase(SimpleNamespace(status="ok")),
        screenshot=screenshot,
        session_stop=stop,
        navigate=navigate,
    )

    for step in (
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={
                "backend": "android",
                "command": ["adb", "shell", "am", "start", "-a", "android.settings.SETTINGS"],
            },
        ),
        ScenarioStep(id="find", kind="find", parameters={"role": "button"}),
        ScenarioStep(id="open", kind="action", parameters={"source": "find", "action": "click"}),
        ScenarioStep(id="back", kind="navigate", parameters={"action": "back"}),
        ScenarioStep(id="shot", kind="screenshot", parameters={}),
        ScenarioStep(id="stop", kind="stop_session", parameters={}),
    ):
        result = executor.execute(step)
        assert result.status == "passed"

    assert action.calls == [
        {
            "session_id": "a1",
            "node_id": "n1",
            "action_name": "click",
            "value": None,
        }
    ]
    assert navigate.calls == [{"session_id": "a1", "action": "back"}]
    assert screenshot.calls == [
        {
            "session_id": "a1",
            "output_path": None,
            "base64": False,
            "region": None,
            "node_id": None,
        }
    ]
    assert stop.calls == [{"session_id": "a1"}]
