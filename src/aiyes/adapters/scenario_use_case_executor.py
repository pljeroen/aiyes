"""Scenario executor backed by existing AIYES use cases."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from aiyes.domain.scenario import ScenarioStep
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult


class ScenarioUseCaseExecutor:
    """Execute scenario steps through existing use-case boundaries."""

    def __init__(
        self,
        *,
        session_start: Any,
        inspect: Any,
        find: Any,
        action: Any,
        type_text: Any,
        screenshot: Any,
        session_stop: Any,
        navigate: Any = None,
    ) -> None:
        self._session_start = session_start
        self._inspect = inspect
        self._find = find
        self._action = action
        self._type_text = type_text
        self._screenshot = screenshot
        self._session_stop = session_stop
        self._navigate = navigate
        self._session_id = ""
        self._outputs: dict[str, Any] = {}

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Execute one scenario step and return a normalized result."""
        try:
            output, session_id = self._execute(step)
        except Exception as exc:
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="failed",
                output={},
                error=str(exc),
                session_id=self._session_id,
            )

        self._outputs[step.id] = output
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output=output,
            error="",
            session_id=session_id,
        )

    def _execute(self, step: ScenarioStep) -> tuple[dict[str, Any], str]:
        params = step.parameters
        if step.kind == "start_session":
            result = self._session_start.execute(**_start_kwargs(params))
            session_id = _get_attr(result, "session_id")
            if not session_id:
                raise RuntimeError("start_session did not return a session_id")
            self._session_id = str(session_id)
            return _jsonable_dict(result), self._session_id

        if step.kind == "inspect":
            result = self._inspect.execute(
                session_id=self._require_session(),
                no_screenshot=bool(params.get("no_screenshot", False)),
                no_tree=bool(params.get("no_tree", False)),
                tree_depth=params.get("tree_depth"),
                no_prune=bool(params.get("no_prune", False)),
                screenshot_base64=bool(params.get("screenshot_base64", False)),
                focus_window=params.get("focus_window"),
            )
            return _jsonable_dict(result), ""

        if step.kind == "find":
            result = self._find.execute(
                session_id=self._require_session(),
                role=str(params.get("role", "*")),
                name_pattern=params.get("name_pattern", params.get("name")),
                state=params.get("state"),
                no_prune=bool(params.get("no_prune", False)),
            )
            nodes = [_jsonable_dict(node) for node in result]
            return {"nodes": nodes}, ""

        if step.kind == "action":
            result = self._action.execute(
                session_id=self._require_session(),
                node_id=self._resolve_node_id(params),
                action_name=str(params.get("action", "click")),
                value=params.get("value"),
            )
            return _jsonable_dict(result), ""

        if step.kind == "type_text":
            result = self._type_text.execute(
                session_id=self._require_session(),
                text=str(params.get("text", "")),
                delay_ms=int(params.get("delay_ms", 0)),
            )
            return _jsonable_dict(result), ""

        if step.kind == "screenshot":
            result = self._screenshot.execute(
                session_id=self._require_session(),
                output_path=params.get("output_path"),
                base64=bool(params.get("base64", False)),
                region=params.get("region"),
                node_id=params.get("node_id"),
            )
            return _jsonable_dict(result), ""

        if step.kind == "navigate" and self._navigate is not None:
            result = self._navigate.execute(
                session_id=self._require_session(),
                action=str(params.get("action", "")),
            )
            return _jsonable_dict(result), ""

        if step.kind == "stop_session":
            result = self._session_stop.execute(session_id=self._session_id or None)
            output = _jsonable_dict(result)
            self._session_id = ""
            return output, ""

        raise RuntimeError(f"unsupported real scenario step kind: {step.kind}")

    def _require_session(self) -> str:
        if not self._session_id:
            raise RuntimeError("scenario step requires an active session")
        return self._session_id

    def _resolve_node_id(self, params: Mapping[str, Any]) -> str:
        explicit = params.get("node_id")
        if explicit:
            return str(explicit)

        source = params.get("source")
        if not source:
            raise RuntimeError("action step requires node_id or source")

        source_output = self._outputs.get(str(source))
        node_id = _first_node_id(source_output)
        if not node_id:
            raise RuntimeError(f"source step did not provide a node id: {source}")
        return node_id


def _start_kwargs(params: Mapping[str, Any]) -> dict[str, Any]:
    command = params.get("command", "")
    backend = str(params.get("backend", "linux"))
    app_args: list[str]
    if not command and backend == "android":
        app_command = "adb"
        app_args = ["shell", "am", "start", "-a", "android.settings.SETTINGS"]
    elif isinstance(command, str):
        app_command = command
        app_args = []
    elif isinstance(command, Sequence):
        parts = [str(part) for part in command]
        app_command = parts[0] if parts else ""
        app_args = parts[1:]
    else:
        app_command = ""
        app_args = []

    return {
        "app_command": app_command,
        "app_args": app_args,
        "resolution": str(params.get("resolution", "1280x800")),
        "color_depth": int(params.get("color_depth", 24)),
        "wait": float(params.get("wait_seconds", params.get("wait", 2.0))),
        "name": params.get("name"),
        "backend": backend,
        "device_serial": _device_serial(params, backend),
    }


def _device_serial(params: Mapping[str, Any], backend: str) -> Any:
    serial = params.get("device_serial")
    if backend != "android" or serial != "auto":
        return serial
    return _first_adb_device_serial()


def _first_adb_device_serial() -> str:
    from aiyes.adapters.adb_path import resolve_adb_path

    completed = subprocess.run(
        [resolve_adb_path(), "devices"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("adb devices returned a non-zero exit status")
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    raise RuntimeError("no Android device is available")


def _jsonable_dict(value: Any) -> dict[str, Any]:
    converted = _to_jsonable(value)
    if isinstance(converted, dict):
        return converted
    return {"value": converted}


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            key: _to_jsonable(item)
            for key, item in dataclasses.asdict(value).items()  # type: ignore[arg-type]
            if item is not None
        }
    if isinstance(value, Mapping):
        return {
            str(key): _to_jsonable(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _to_jsonable(item)
            for key, item in vars(value).items()
            if item is not None
        }
    return value


def _get_attr(value: Any, name: str) -> Optional[Any]:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _first_node_id(value: Any) -> str:
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            for node in nodes:
                node_id = _node_id(node)
                if node_id:
                    return node_id
        return _node_id(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            node_id = _node_id(item)
            if node_id:
                return node_id
    return _node_id(value)


def _node_id(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("id", value.get("node_id"))
    else:
        raw = getattr(value, "id", getattr(value, "node_id", ""))
    return str(raw) if raw else ""
