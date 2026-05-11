"""Scenario executor backed by existing AIYES use cases."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from aiyes.domain.scenario import _VALID_DIRECTIONS, ScenarioStep
from aiyes.domain.scenario_assertions import evaluate_scenario_assertion
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
        wait: Any = None,
        wait_stable: Any = None,
        reactive_wait: Any = None,
        key: Any = None,
        sleeper: Any = None,
        mouse: Any = None,
        gesture: Any = None,
    ) -> None:
        self._session_start = session_start
        self._inspect = inspect
        self._find = find
        self._action = action
        self._type_text = type_text
        self._screenshot = screenshot
        self._session_stop = session_stop
        self._navigate = navigate
        self._wait = wait
        self._wait_stable = wait_stable
        self._reactive_wait = reactive_wait
        self._key = key
        self._sleeper = sleeper
        self._mouse = mouse
        self._gesture = gesture
        self._session_id = ""
        self._outputs: dict[str, Any] = {}

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Execute one scenario step and return a normalized result."""
        if step.kind == "assert":
            return self._execute_assert(step)

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

    def _execute_assert(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Evaluate an assertion step against accumulated step outputs.

        Assert needs output-on-failure semantics, unlike the other kinds
        whose failure path is exception-only. Implementation note: the
        assertion context is the executor's own _outputs dict, identical
        in shape to scenario_run.py's run-layer assertion path.
        """
        assertion = step.parameters.get("assertion")
        if not isinstance(assertion, Mapping):
            assertion = {}
        assertion_result = evaluate_scenario_assertion(assertion, self._outputs)
        output = {
            "assertion": {
                "assertion_id": assertion_result.assertion_id,
                "kind": assertion_result.kind,
                "status": assertion_result.status,
                "message": assertion_result.message,
            }
        }
        self._outputs[step.id] = output
        if assertion_result.status == "passed":
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="passed",
                output=output,
                error="",
                session_id="",
            )
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="failed",
            output=output,
            error=assertion_result.message,
            session_id="",
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

        if step.kind == "wait" and self._wait is not None:
            result = self._wait.execute(
                session_id=self._require_session(),
                role=str(params.get("role", "*")),
                name_pattern=params.get("name_pattern", params.get("name")),
                timeout=params.get("timeout"),
                state=params.get("state"),
                absent=bool(params.get("absent", False)),
                transient=bool(params.get("transient", False)),
            )
            return _jsonable_dict(result), ""

        if step.kind == "wait_reactive" and self._reactive_wait is not None:
            result = self._reactive_wait.execute(
                session_id=self._require_session(),
                condition=str(params.get("condition", "")),
                name_pattern=params.get("name_pattern"),
                timeout=float(params.get("timeout", 10.0)),
                quiet=float(params.get("quiet", 0.0)),
                poll_interval=float(params.get("poll_interval", 0.25)),
            )
            return _jsonable_dict(result), ""

        if step.kind == "key" and self._key is not None:
            keys = params.get("keys", ())
            if not isinstance(keys, (list, tuple)) or not keys:
                raise RuntimeError("key step requires a non-empty keys list")
            result = self._key.execute(
                session_id=self._require_session(),
                key_specs=[str(item) for item in keys],
            )
            return _jsonable_dict(result), ""

        if step.kind == "sleep":
            seconds = float(params.get("seconds", 0.0))
            reason = str(params.get("reason", ""))
            if self._sleeper is not None:
                self._sleeper.sleep(seconds)
            else:
                import time as _time

                _time.sleep(seconds)
            return {"slept": seconds, "reason": reason}, ""

        if step.kind == "mouse_drag" and self._mouse is not None:
            x1, y1, x2, y2 = self._resolve_drag_coords(params)
            self._mouse.drag(self._require_session(), x1, y1, x2, y2)
            return {"moved": True, "from": [x1, y1], "to": [x2, y2]}, ""

        if step.kind == "gesture_pinch" and self._gesture is not None:
            x, y = self._resolve_point_coords(params)
            if "scale_factor" not in params:
                raise RuntimeError("gesture_pinch requires scale_factor")
            scale_factor = float(params["scale_factor"])
            self._gesture.pinch(self._require_session(), x, y, scale_factor)
            return {
                "pinched": True,
                "center": [x, y],
                "scale_factor": scale_factor,
            }, ""

        if step.kind == "gesture_two_finger_scroll" and self._gesture is not None:
            x, y = self._resolve_point_coords(params)
            direction = str(params.get("direction", ""))
            if direction not in _VALID_DIRECTIONS:
                raise RuntimeError(
                    f"direction_invalid: direction must be one of {sorted(_VALID_DIRECTIONS)}"
                )
            amount = int(params.get("amount", 3))
            self._gesture.two_finger_scroll(
                self._require_session(), x, y, direction, amount
            )
            return {
                "scrolled": True,
                "center": [x, y],
                "direction": direction,
                "amount": amount,
            }, ""

        if step.kind == "swipe" and self._gesture is not None:
            x1, y1, x2, y2 = self._resolve_drag_coords(params)
            duration_ms = int(params.get("duration_ms", 300))
            self._gesture.swipe(self._require_session(), x1, y1, x2, y2, duration_ms)
            return {
                "swiped": True,
                "from": [x1, y1],
                "to": [x2, y2],
                "duration_ms": duration_ms,
            }, ""

        if step.kind == "mouse_scroll" and self._mouse is not None:
            direction = str(params.get("direction", ""))
            if direction not in _VALID_DIRECTIONS:
                raise RuntimeError(
                    f"direction_invalid: direction must be one of {sorted(_VALID_DIRECTIONS)}"
                )
            amount = int(params.get("amount", 3))
            sid = self._require_session()
            source = params.get("source")
            if source is not None:
                cx, cy = self._resolve_node_center(source)
                self._mouse.move(sid, cx, cy)
            self._mouse.scroll(sid, direction, amount)
            return {"scrolled": True, "direction": direction, "amount": amount}, ""

        if step.kind == "wait_stable" and self._wait_stable is not None:
            ignore_nodes = params.get("ignore_nodes", ())
            if not isinstance(ignore_nodes, (list, tuple, frozenset, set)):
                ignore_nodes = ()
            result = self._wait_stable.execute(
                session_id=self._require_session(),
                timeout=float(params.get("timeout", 10.0)),
                poll_interval=float(params.get("interval", 0.5)),
                consecutive=int(params.get("consecutive", 3)),
                tolerance=int(params.get("tolerance", 0)),
                ignore_ids=frozenset(str(item) for item in ignore_nodes),
            )
            return _jsonable_dict(result), ""

        raise RuntimeError(f"unsupported real scenario step kind: {step.kind}")

    def _require_session(self) -> str:
        if not self._session_id:
            raise RuntimeError("scenario step requires an active session")
        return self._session_id

    def _resolve_drag_coords(
        self, params: Mapping[str, Any]
    ) -> tuple[int, int, int, int]:
        """Resolve drag endpoints. Exactly one of literal/source mode required."""
        has_literal = all(k in params for k in ("x1", "y1", "x2", "y2"))
        has_source = "source" in params
        if has_literal and has_source:
            raise RuntimeError(
                "coord_mode_ambiguous: supply literal coords OR source, not both"
            )
        if not has_literal and not has_source:
            raise RuntimeError("coord_mode_missing: supply x1/y1/x2/y2 or source/dx/dy")
        if has_literal:
            return (
                int(params["x1"]),
                int(params["y1"]),
                int(params["x2"]),
                int(params["y2"]),
            )
        cx, cy = self._resolve_node_center(params["source"])
        if "dx" not in params or "dy" not in params:
            raise RuntimeError("coord_mode_missing: source mode requires dx and dy")
        dx = int(params["dx"])
        dy = int(params["dy"])
        return (cx, cy, cx + dx, cy + dy)

    def _resolve_point_coords(self, params: Mapping[str, Any]) -> tuple[int, int]:
        """Resolve a single (x, y) point. Either literal x/y or source mode."""
        has_literal = "x" in params and "y" in params
        has_source = "source" in params
        if has_literal and has_source:
            raise RuntimeError("coord_mode_ambiguous: supply x/y OR source, not both")
        if not has_literal and not has_source:
            raise RuntimeError("coord_mode_missing: supply x/y or source")
        if has_literal:
            return (int(params["x"]), int(params["y"]))
        return self._resolve_node_center(params["source"])

    def _resolve_node_center(self, source: Any) -> tuple[int, int]:
        """Resolve a source step id to the center of its first node's bounds."""
        if not isinstance(source, str):
            raise RuntimeError("source_step_unknown: source must be a step id string")
        if source not in self._outputs:
            raise RuntimeError(f"source_step_unknown: no prior step with id {source!r}")
        output = self._outputs[source]
        bounds = _first_node_bounds(output)
        if bounds is None:
            raise RuntimeError(
                f"source_step_no_node: step {source!r} has no node with bounds"
            )
        x, y, w, h = bounds
        return (x + w // 2, y + h // 2)

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


def _first_node_bounds(value: Any) -> Optional[tuple[int, int, int, int]]:
    """Return bounds [x, y, width, height] of the first node with bounds.

    Searches the same shapes as _first_node_id: a dict with "nodes",
    or a sequence of nodes, or a single node.
    """
    candidates: list[Any] = []
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            candidates.extend(nodes)
        else:
            candidates.append(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        candidates.extend(value)
    else:
        candidates.append(value)

    for node in candidates:
        bounds = _node_bounds(node)
        if bounds is not None:
            return bounds
    return None


def _node_bounds(value: Any) -> Optional[tuple[int, int, int, int]]:
    if isinstance(value, Mapping):
        raw = value.get("bounds")
    else:
        raw = getattr(value, "bounds", None)
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
    except (TypeError, ValueError):
        return None
