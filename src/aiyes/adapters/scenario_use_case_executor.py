"""Scenario executor backed by existing AIYES use cases."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from aiyes.domain.scenario import _VALID_DIRECTIONS, ScenarioStep
from aiyes.domain.scenario_assertions import evaluate_scenario_assertion
from aiyes.domain.tree import AccessibilityTree, flatten_nodes
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
        session_repo: Any = None,
        clock: Any = None,
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
        self._session_repo = session_repo
        self._clock = clock
        self._session_id = ""
        self._outputs: dict[str, Any] = {}
        self._viewport_cache: dict[str, tuple[int, int]] = {}

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Execute one scenario step and return a normalized result."""
        if step.kind == "assert":
            return self._execute_assert(step)
        if step.kind == "scroll_into_view":
            return self._execute_scroll_into_view(step)

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

    def _execute_scroll_into_view(
        self, step: ScenarioStep
    ) -> ScenarioStepExecutionResult:
        """Scroll a list until target node is visible, or fail with diagnostics.

        Two-bound termination: max_scrolls AND max_seconds. Cross-platform
        via gesture.swipe — the dispatching gesture port routes to the
        appropriate adapter (adb input swipe on Android, mouse-drag
        substitute on Linux).
        """
        try:
            session_id = self._require_session()
        except Exception as exc:
            return ScenarioStepExecutionResult(
                step_id=step.id, status="failed", output={}, error=str(exc)
            )

        params = step.parameters
        role = str(params.get("role", "*"))
        name_pattern = params.get("name_pattern")
        state = params.get("state")
        direction = str(params.get("direction", "down"))
        max_scrolls = int(params.get("max_scrolls", 10))
        max_seconds = float(params.get("max_seconds", 30.0))

        viewport = _parse_viewport(
            self._session_repo, session_id, _cache=self._viewport_cache
        )

        clock = self._clock
        start = clock.now() if clock is not None else 0.0
        attempts = 0
        scroll_attempts: list[dict[str, Any]] = []

        while True:
            nodes = self._find.execute(
                session_id=session_id,
                role=role,
                name_pattern=name_pattern,
                state=state,
                no_prune=False,
            )
            node_id = _first_node_id(nodes if isinstance(nodes, list) else nodes)
            if node_id:
                elapsed = (clock.now() - start) if clock is not None else 0.0
                output = {
                    "found": True,
                    "node_id": node_id,
                    "attempts": attempts,
                    "elapsed": elapsed,
                    "direction": direction,
                    "role_match": "exact",
                    "scroll_attempts": scroll_attempts,
                }
                self._outputs[step.id] = output
                return ScenarioStepExecutionResult(
                    step_id=step.id, status="passed", output=output, error=""
                )

            elapsed_now = (clock.now() - start) if clock is not None else 0.0
            advisory_result = self._resolve_scroll_into_view_role_drift(
                step_id=step.id,
                session_id=session_id,
                requested_role=role,
                name_pattern=name_pattern,
                state=state,
                attempts=attempts,
                elapsed=elapsed_now,
                direction=direction,
                scroll_attempts=scroll_attempts,
            )
            if advisory_result is not None:
                return advisory_result

            if attempts >= max_scrolls:
                return self._scroll_into_view_failure(
                    step.id,
                    attempts,
                    elapsed_now,
                    direction,
                    viewport,
                    "scrolls",
                    scroll_attempts,
                )
            if elapsed_now >= max_seconds:
                return self._scroll_into_view_failure(
                    step.id,
                    attempts,
                    elapsed_now,
                    direction,
                    viewport,
                    "seconds",
                    scroll_attempts,
                )

            before_tree = _inspect_tree_snapshot(self._inspect, session_id)
            scrollable = _select_scrollable_candidate(
                _scrollable_candidates(before_tree, viewport),
                context_bounds=None,
            )
            if scrollable is None:
                x1, y1, x2, y2 = _swipe_coords_for_direction(direction, viewport)
                method = "viewport_swipe"
                selected_scrollable_id = None
                selected_bounds = None
            else:
                x1, y1, x2, y2 = _swipe_coords_in_bounds(
                    direction, scrollable.bounds, viewport
                )
                method = "scrollable_region_swipe"
                selected_scrollable_id = scrollable.node_id
                selected_bounds = list(scrollable.bounds)
            self._gesture.swipe(session_id, x1, y1, x2, y2, 300)
            after_tree = _inspect_tree_snapshot(self._inspect, session_id)
            scroll_attempts.append(
                {
                    "method": method,
                    "direction": direction,
                    "coordinates": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    "selected_scrollable_id": selected_scrollable_id,
                    "selected_bounds": selected_bounds,
                    "tree_changed": _tree_snapshot_changed(before_tree, after_tree),
                }
            )
            attempts += 1

    def _resolve_scroll_into_view_role_drift(
        self,
        *,
        step_id: str,
        session_id: str,
        requested_role: str,
        name_pattern: Any,
        state: Any,
        attempts: int,
        elapsed: float,
        direction: str,
        scroll_attempts: list[dict[str, Any]],
    ) -> Optional[ScenarioStepExecutionResult]:
        if requested_role == "*" or not _non_empty_pattern(name_pattern):
            return None

        wildcard_nodes = self._find.execute(
            session_id=session_id,
            role="*",
            name_pattern=name_pattern,
            state=state,
            no_prune=False,
        )
        candidates = _candidate_nodes(wildcard_nodes)
        if not candidates:
            return None

        actionable_candidates = [
            node
            for node in candidates
            if _role_drift_compatible(node, requested_role)
            and _scroll_target_actionable(node)
        ]
        if len(actionable_candidates) == 1:
            node = actionable_candidates[0]
            output = {
                "found": True,
                "node_id": _node_id(node),
                "attempts": attempts,
                "elapsed": elapsed,
                "direction": direction,
                "role_match": "advisory",
                "requested_role": requested_role,
                "actual_role": _node_role(node),
                "matched_name": _node_name(node),
                "scroll_attempts": scroll_attempts,
            }
            self._outputs[step_id] = output
            return ScenarioStepExecutionResult(
                step_id=step_id, status="passed", output=output, error=""
            )

        output = _role_drift_failure_output(
            candidates=candidates,
            actionable_candidates=actionable_candidates,
            requested_role=requested_role,
            name_pattern=str(name_pattern),
            attempts=attempts,
            elapsed=elapsed,
            direction=direction,
        )
        output["scroll_attempts"] = scroll_attempts
        self._outputs[step_id] = output
        if len(actionable_candidates) > 1:
            error = "scroll_into_view_role_drift_ambiguous"
        else:
            error = "scroll_into_view_role_drift_no_actionable_candidate"
        return ScenarioStepExecutionResult(
            step_id=step_id,
            status="failed",
            output=output,
            error=error,
        )

    def _scroll_into_view_failure(
        self,
        step_id: str,
        attempts: int,
        elapsed: float,
        direction: str,
        viewport: tuple[int, int],
        bound_hit: str,
        scroll_attempts: list[dict[str, Any]],
    ) -> ScenarioStepExecutionResult:
        output = {
            "found": False,
            "attempts": attempts,
            "elapsed": elapsed,
            "direction": direction,
            "viewport": list(viewport),
            "bound_hit": bound_hit,
            "scroll_attempts": scroll_attempts,
        }
        self._outputs[step_id] = output
        return ScenarioStepExecutionResult(
            step_id=step_id,
            status="failed",
            output=output,
            error=f"scroll_into_view_target_not_found (bound: {bound_hit}, attempts: {attempts})",
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


@dataclasses.dataclass(frozen=True)
class _ScrollableCandidate:
    node_id: str
    bounds: tuple[int, int, int, int]
    area: int


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


def _non_empty_pattern(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _candidate_nodes(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            return list(nodes)
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def _node_role(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("role", "")
    else:
        raw = getattr(value, "role", "")
    return str(raw) if raw else ""


def _node_name(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("name", "")
    else:
        raw = getattr(value, "name", "")
    return str(raw) if raw else ""


def _node_actions(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        raw = value.get("actions", ())
    else:
        raw = getattr(value, "actions", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(action) for action in raw)


def _role_drift_compatible(value: Any, requested_role: str) -> bool:
    return _node_role(value) != requested_role


def _scroll_target_actionable(value: Any) -> bool:
    if _node_role(value).lower() == "button":
        return True
    actionable = {"click", "tap", "press", "activate", "scroll", "focus", "long_click"}
    return any(action.lower() in actionable for action in _node_actions(value))


def _role_drift_failure_output(
    *,
    candidates: list[Any],
    actionable_candidates: list[Any],
    requested_role: str,
    name_pattern: str,
    attempts: int,
    elapsed: float,
    direction: str,
) -> dict[str, Any]:
    return {
        "found": False,
        "attempts": attempts,
        "elapsed": elapsed,
        "direction": direction,
        "role_match": "advisory",
        "requested_role": requested_role,
        "name_pattern": name_pattern,
        "candidate_count": len(actionable_candidates),
        "observed_roles": sorted({_node_role(node) for node in candidates}),
        "candidates": [
            {
                "node_id": _node_id(node),
                "role": _node_role(node),
                "name": _node_name(node),
            }
            for node in actionable_candidates
        ],
    }


def _inspect_tree_snapshot(inspect: Any, session_id: str) -> Any:
    if inspect is None:
        return None
    try:
        result = inspect.execute(
            session_id=session_id,
            no_screenshot=True,
            no_tree=False,
            tree_depth=None,
            no_prune=True,
            screenshot_base64=False,
            focus_window=None,
        )
    except Exception:
        return None
    if isinstance(result, Mapping):
        return result.get("tree")
    return getattr(result, "tree", None)


def _scrollable_candidates(
    tree_snapshot: Any, viewport: tuple[int, int]
) -> list[_ScrollableCandidate]:
    candidates: list[_ScrollableCandidate] = []
    for node in _tree_snapshot_nodes(tree_snapshot):
        bounds = _node_bounds(node)
        if bounds is None or not _visible_bounds(bounds, viewport):
            continue
        if not _node_scrollable(node):
            continue
        node_id = _node_id(node)
        if not node_id:
            continue
        _, _, width, height = bounds
        candidates.append(
            _ScrollableCandidate(node_id=node_id, bounds=bounds, area=width * height)
        )
    return candidates


def _tree_snapshot_nodes(tree_snapshot: Any) -> list[Any]:
    if tree_snapshot is None:
        return []
    if isinstance(tree_snapshot, AccessibilityTree):
        return list(flatten_nodes(tree_snapshot.roots))
    if isinstance(tree_snapshot, Mapping):
        roots = tree_snapshot.get("roots", tree_snapshot.get("tree"))
        if isinstance(roots, Sequence) and not isinstance(roots, (str, bytes)):
            return _walk_tree_nodes(roots)
        if roots is not None:
            return _walk_tree_nodes([roots])
        return _walk_tree_nodes([tree_snapshot])
    if isinstance(tree_snapshot, Sequence) and not isinstance(tree_snapshot, (str, bytes)):
        return _walk_tree_nodes(tree_snapshot)
    roots = getattr(tree_snapshot, "roots", None)
    if isinstance(roots, Sequence) and not isinstance(roots, (str, bytes)):
        return _walk_tree_nodes(roots)
    return _walk_tree_nodes([tree_snapshot])


def _walk_tree_nodes(nodes: Sequence[Any]) -> list[Any]:
    flattened: list[Any] = []
    for node in nodes:
        flattened.append(node)
        if isinstance(node, Mapping):
            children = node.get("children", ())
        else:
            children = getattr(node, "children", ())
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            flattened.extend(_walk_tree_nodes(children))
    return flattened


def _node_scrollable(value: Any) -> bool:
    role = _node_role(value).lower()
    if "scroll" in role or role in {"list", "listview", "recyclerview"}:
        return True
    states = _node_states(value)
    if "scrollable" in states:
        return True
    if isinstance(value, Mapping):
        raw = value.get("scrollable")
        if raw is True or (isinstance(raw, str) and raw.lower() == "true"):
            return True
    return any(action.lower() == "scroll" for action in _node_actions(value))


def _node_states(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        raw = value.get("states", ())
    else:
        raw = getattr(value, "states", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(state).lower() for state in raw)


def _visible_bounds(bounds: tuple[int, int, int, int], viewport: tuple[int, int]) -> bool:
    x, y, width, height = bounds
    if width <= 0 or height <= 0:
        return False
    viewport_width, viewport_height = viewport
    if x >= viewport_width or y >= viewport_height:
        return False
    if x + width <= 0 or y + height <= 0:
        return False
    return True


def _select_scrollable_candidate(
    candidates: list[_ScrollableCandidate],
    context_bounds: Optional[tuple[int, int, int, int]],
) -> Optional[_ScrollableCandidate]:
    if not candidates:
        return None
    if context_bounds is not None:
        contextual = [
            candidate
            for candidate in candidates
            if _bounds_contains(candidate.bounds, context_bounds)
            or _bounds_overlap_ratio(candidate.bounds, context_bounds) >= 0.5
        ]
        if contextual:
            return min(contextual, key=lambda candidate: candidate.area)
    return max(candidates, key=lambda candidate: candidate.area)


def _bounds_contains(
    outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]
) -> bool:
    outer_x, outer_y, outer_w, outer_h = outer
    inner_x, inner_y, inner_w, inner_h = inner
    return (
        outer_x <= inner_x
        and outer_y <= inner_y
        and outer_x + outer_w >= inner_x + inner_w
        and outer_y + outer_h >= inner_y + inner_h
    )


def _bounds_overlap_ratio(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> float:
    first_x, first_y, first_w, first_h = first
    second_x, second_y, second_w, second_h = second
    overlap_w = max(
        0,
        min(first_x + first_w, second_x + second_w) - max(first_x, second_x),
    )
    overlap_h = max(
        0,
        min(first_y + first_h, second_y + second_h) - max(first_y, second_y),
    )
    second_area = second_w * second_h
    if second_area <= 0:
        return 0.0
    return (overlap_w * overlap_h) / second_area


def _swipe_coords_in_bounds(
    direction: str,
    bounds: tuple[int, int, int, int],
    viewport: tuple[int, int],
) -> tuple[int, int, int, int]:
    x, y, width, height = bounds
    left = max(0, x)
    top = max(0, y)
    right = min(viewport[0], x + width)
    bottom = min(viewport[1] - _bottom_system_inset(viewport), y + height)

    horizontal_margin = _edge_margin(right - left)
    vertical_margin = _edge_margin(bottom - top)
    safe_left = min(right, left + horizontal_margin)
    safe_right = max(left, right - horizontal_margin)
    safe_top = min(bottom, top + vertical_margin)
    safe_bottom = max(top, bottom - vertical_margin)

    cx = (safe_left + safe_right) // 2
    cy = (safe_top + safe_bottom) // 2
    if direction == "down":
        return (cx, safe_bottom, cx, safe_top)
    if direction == "up":
        return (cx, safe_top, cx, safe_bottom)
    if direction == "left":
        return (safe_left, cy, safe_right, cy)
    if direction == "right":
        return (safe_right, cy, safe_left, cy)
    return (cx, safe_bottom, cx, safe_top)


def _edge_margin(length: int) -> int:
    if length <= 2:
        return 0
    return min(96, max(16, length // 12))


def _bottom_system_inset(viewport: tuple[int, int]) -> int:
    _, height = viewport
    return min(240, max(96, height // 20))


def _tree_snapshot_changed(before: Any, after: Any) -> bool:
    if before is None or after is None:
        return False
    return _to_jsonable(before) != _to_jsonable(after)


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


def _parse_viewport(
    session_repo: Any,
    session_id: str,
    _cache: Optional[dict[str, tuple[int, int]]] = None,
) -> tuple[int, int]:
    """Resolve viewport (width, height) from session.

    On Android sessions with a device_serial, queries the device via
    ``adb shell wm size`` and caches per session_id. On Linux, parses
    ``session.resolution``. Returns (1080, 1920) on any failure with a
    single stderr warning naming the device serial (Android failures
    only).
    """
    default = (1080, 1920)

    if _cache is not None and session_id in _cache:
        return _cache[session_id]

    if session_repo is None:
        return default

    try:
        session = session_repo.load(session_id)
    except Exception:
        return default
    if session is None:
        return default

    backend = getattr(session, "backend", "")
    device_serial = getattr(session, "device_serial", "")

    if backend == "android" and device_serial:
        from aiyes.adapters.android_device_metrics_adapter import (
            query_device_metrics,
        )

        metrics, reason = query_device_metrics(device_serial)
        if metrics is None:
            # Do not cache the fallback — a transient failure (timeout,
            # daemon not yet up) shouldn't poison every subsequent scroll
            # step in the same session.
            print(
                f"aiyes: warning: wm size query failed for device "
                f"{device_serial!r} ({reason}); falling back to "
                f"viewport {default}.",
                file=sys.stderr,
            )
            return default
        if _cache is not None:
            _cache[session_id] = metrics
        return metrics

    # Linux / default path
    resolution = getattr(session, "resolution", "") if session is not None else ""
    if not isinstance(resolution, str) or "x" not in resolution:
        return default
    parts = resolution.lower().split("x")
    if len(parts) != 2:
        return default
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return default


def _swipe_coords_for_direction(
    direction: str, viewport: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Compute swipe endpoints for a scroll in the given direction.

    "down" scroll = swipe from lower screen region upward.
    "up" scroll = swipe from upper region downward.
    Horizontal directions swipe across the center row.
    """
    width, height = viewport
    cx, cy = width // 2, height // 2
    dy = height // 3
    dx = width // 3
    if direction == "down":
        return (cx, cy + dy, cx, cy - dy)
    if direction == "up":
        return (cx, cy - dy, cx, cy + dy)
    if direction == "left":
        return (cx - dx, cy, cx + dx, cy)
    if direction == "right":
        return (cx + dx, cy, cx - dx, cy)
    # Validator catches bad direction; defensive default.
    return (cx, cy + dy, cx, cy - dy)


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
