"""Release scenario value objects and validation.

The scenario model is deterministic by design: it describes explicit steps for
the runner to execute later. It does not contain planning or LLM behavior.
"""

from __future__ import annotations

import dataclasses
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


_VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_VALID_TARGETS = frozenset(("linux", "android"))
_VALID_STEP_KINDS = frozenset(
    (
        "start_session",
        "inspect",
        "find",
        "action",
        "type_text",
        "wait",
        "wait_stable",
        "wait_reactive",
        "key",
        "sleep",
        "mouse_drag",
        "mouse_scroll",
        "gesture_pinch",
        "gesture_two_finger_scroll",
        "swipe",
        "scroll_into_view",
        "screenshot",
        "navigate",
        "stop_session",
        "assert",
    )
)
_VALID_DIRECTIONS = frozenset(("up", "down", "left", "right"))
_VALID_REACTIVE_CONDITIONS = frozenset(
    (
        "screen-change",
        "node-appears",
        "node-disappears",
        "focus-change",
        "app-change",
    )
)
_SLEEP_CEILING_SECONDS = 5.0
_SLEEP_REASON_MIN_LENGTH = 20
_SWIPE_DURATION_CEILING_MS = 5000
_VALID_FAILURE_POLICIES = frozenset(("fail", "skip", "cleanup_then_fail"))
_PUBLIC_DENIED_FRAGMENTS = frozenset(("/home/", "private", "/dev/"))


@dataclasses.dataclass(frozen=True)
class ScenarioValidationIssue:
    """A deterministic validation issue for a scenario document."""

    path: str
    code: str
    message: str


@dataclasses.dataclass(frozen=True)
class ScenarioStep:
    """A single explicit release-scenario step."""

    id: str
    kind: str
    parameters: Mapping[str, Any]
    timeout_seconds: Optional[float] = None
    on_failure: str = "fail"

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclasses.dataclass(frozen=True)
class ReleaseScenario:
    """Validated release scenario document."""

    schema_version: int
    id: str
    title: str
    target: str
    prerequisites: Tuple[Mapping[str, Any], ...]
    steps: Tuple[ScenarioStep, ...]
    cleanup: Tuple[ScenarioStep, ...]
    evidence_policy: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "prerequisites",
            tuple(MappingProxyType(dict(item)) for item in self.prerequisites),
        )
        object.__setattr__(
            self, "evidence_policy", MappingProxyType(dict(self.evidence_policy))
        )


@dataclasses.dataclass(frozen=True)
class ScenarioValidationResult:
    """Result of validating or loading a release scenario."""

    scenario: Optional[ReleaseScenario]
    issues: Tuple[ScenarioValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues and self.scenario is not None


def validate_scenario_document(
    document: object, public_fixture: bool = False
) -> ScenarioValidationResult:
    """Validate a release-scenario document without executing it."""
    if not isinstance(document, dict):
        return _invalid("$", "invalid_document", "scenario document must be an object")

    issues: list[ScenarioValidationIssue] = []
    _validate_required(document, issues)
    if issues:
        return ScenarioValidationResult(scenario=None, issues=tuple(issues))

    schema_version = document.get("schema_version")
    if schema_version != 1:
        issues.append(
            _issue(
                "schema_version",
                "unsupported_schema_version",
                "schema_version must be 1",
            )
        )

    scenario_id = document.get("id")
    if not isinstance(scenario_id, str) or not _VALID_ID.match(scenario_id):
        issues.append(_issue("id", "invalid_id", "id must be a stable scenario id"))

    title = document.get("title")
    if not isinstance(title, str) or not title.strip():
        issues.append(_issue("title", "invalid_title", "title must be non-empty"))

    target = document.get("target")
    if target not in _VALID_TARGETS:
        issues.append(
            _issue("target", "invalid_target", "target must be linux or android")
        )

    prerequisites = _validate_mapping_list(
        document.get("prerequisites", ()), "prerequisites", issues
    )
    steps = _validate_steps(document.get("steps"), "steps", issues)
    cleanup = _validate_steps(document.get("cleanup", ()), "cleanup", issues)
    evidence_policy = document.get("evidence_policy")
    if not isinstance(evidence_policy, dict):
        issues.append(
            _issue(
                "evidence_policy",
                "invalid_evidence_policy",
                "evidence_policy must be an object",
            )
        )

    if public_fixture:
        issues.extend(_private_reference_issues(document))

    if _has_start_session(steps) and not _has_stop_session(steps + cleanup):
        issues.append(
            _issue(
                "cleanup",
                "cleanup_required",
                "scenarios that start a session must declare stop_session cleanup",
            )
        )

    if issues:
        return ScenarioValidationResult(scenario=None, issues=tuple(issues))

    assert isinstance(schema_version, int)
    assert isinstance(scenario_id, str)
    assert isinstance(title, str)
    assert isinstance(target, str)
    assert isinstance(evidence_policy, dict)
    return ScenarioValidationResult(
        scenario=ReleaseScenario(
            schema_version=schema_version,
            id=scenario_id,
            title=title,
            target=target,
            prerequisites=tuple(MappingProxyType(dict(item)) for item in prerequisites),
            steps=tuple(steps),
            cleanup=tuple(cleanup),
            evidence_policy=MappingProxyType(dict(evidence_policy)),
        ),
        issues=(),
    )


def _validate_required(
    document: Mapping[str, object], issues: list[ScenarioValidationIssue]
) -> None:
    for field in (
        "schema_version",
        "id",
        "title",
        "target",
        "steps",
        "evidence_policy",
    ):
        if field not in document:
            issues.append(_issue(field, "missing_required", f"{field} is required"))


def _validate_mapping_list(
    raw: object, path: str, issues: list[ScenarioValidationIssue]
) -> Tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, (list, tuple)):
        issues.append(_issue(path, "invalid_list", f"{path} must be a list"))
        return ()
    items: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            issues.append(
                _issue(f"{path}[{index}]", "invalid_object", "item must be an object")
            )
            continue
        items.append(item)
    return tuple(items)


def _validate_steps(
    raw: object, path: str, issues: list[ScenarioValidationIssue]
) -> Tuple[ScenarioStep, ...]:
    items = _validate_mapping_list(raw, path, issues)
    steps: list[ScenarioStep] = []
    for index, item in enumerate(items):
        step_id = item.get("id")
        if not isinstance(step_id, str) or not _VALID_ID.match(step_id):
            issues.append(
                _issue(f"{path}[{index}].id", "invalid_id", "invalid step id")
            )
        kind = item.get("kind")
        if kind not in _VALID_STEP_KINDS:
            issues.append(
                _issue(
                    f"{path}[{index}].kind",
                    "unsupported_step_kind",
                    "unsupported step kind",
                )
            )
        timeout = item.get("timeout_seconds")
        timeout_seconds: Optional[float] = None
        if timeout is not None:
            if not isinstance(timeout, (int, float)) or timeout < 0 or timeout > 300:
                issues.append(
                    _issue(
                        f"{path}[{index}].timeout_seconds",
                        "invalid_timeout",
                        "timeout_seconds must be between 0 and 300",
                    )
                )
            else:
                timeout_seconds = float(timeout)
        on_failure = item.get("on_failure", "fail")
        if on_failure not in _VALID_FAILURE_POLICIES:
            issues.append(
                _issue(
                    f"{path}[{index}].on_failure",
                    "invalid_failure_policy",
                    "invalid failure policy",
                )
            )
        if isinstance(kind, str) and kind in _VALID_STEP_KINDS:
            issues.extend(_validate_kind_specific(kind, item, f"{path}[{index}]"))
        if (
            isinstance(step_id, str)
            and _VALID_ID.match(step_id)
            and isinstance(kind, str)
            and kind in _VALID_STEP_KINDS
            and isinstance(on_failure, str)
            and on_failure in _VALID_FAILURE_POLICIES
        ):
            steps.append(
                ScenarioStep(
                    id=step_id,
                    kind=kind,
                    parameters=_step_parameters(item),
                    timeout_seconds=timeout_seconds,
                    on_failure=on_failure,
                )
            )
    return tuple(steps)


def _validate_kind_specific(
    kind: str, item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    """Per-kind load-time parameter checks.

    Implementation note: covers the kinds added in AIYES-44..47. Earlier
    kinds (start_session, inspect, find, action, type_text, screenshot,
    navigate, stop_session, assert) keep their executor-level validation
    pending the wider retrofit (follow-up contract).
    """
    if kind == "sleep":
        return _validate_sleep_step(item, path)
    if kind == "wait":
        return _validate_wait_step(item, path)
    if kind == "wait_reactive":
        return _validate_wait_reactive_step(item, path)
    if kind == "key":
        return _validate_key_step(item, path)
    if kind == "mouse_drag":
        return _validate_drag_coords(item, path)
    if kind == "mouse_scroll":
        return _validate_mouse_scroll_step(item, path)
    if kind == "gesture_pinch":
        return _validate_gesture_pinch_step(item, path)
    if kind == "gesture_two_finger_scroll":
        return _validate_gesture_two_finger_scroll_step(item, path)
    if kind == "swipe":
        return _validate_swipe_step(item, path)
    if kind == "scroll_into_view":
        return _validate_scroll_into_view_step(item, path)
    return ()


def _validate_scroll_into_view_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    issues: list[ScenarioValidationIssue] = []
    role = item.get("role")
    if not isinstance(role, str) or not role:
        issues.append(
            _issue(
                f"{path}.role",
                "scroll_into_view_role_required",
                "scroll_into_view.role must be a non-empty string",
            )
        )
    name_pattern = item.get("name_pattern")
    if not isinstance(name_pattern, str) or not name_pattern:
        issues.append(
            _issue(
                f"{path}.name_pattern",
                "scroll_into_view_name_pattern_required",
                "scroll_into_view.name_pattern must be a non-empty string",
            )
        )
    direction = item.get("direction", "down")
    if not isinstance(direction, str) or direction not in _VALID_DIRECTIONS:
        issues.append(
            _issue(
                f"{path}.direction",
                "direction_invalid",
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}",
            )
        )
    max_scrolls = item.get("max_scrolls", 10)
    if (
        not isinstance(max_scrolls, int)
        or isinstance(max_scrolls, bool)
        or max_scrolls < 1
        or max_scrolls > 50
    ):
        issues.append(
            _issue(
                f"{path}.max_scrolls",
                "scroll_into_view_max_scrolls_out_of_range",
                "max_scrolls must be an integer in [1, 50]",
            )
        )
    max_seconds = item.get("max_seconds", 30.0)
    if (
        not isinstance(max_seconds, (int, float))
        or isinstance(max_seconds, bool)
        or max_seconds <= 0
        or max_seconds > 120
    ):
        issues.append(
            _issue(
                f"{path}.max_seconds",
                "scroll_into_view_max_seconds_out_of_range",
                "max_seconds must be a number in (0, 120]",
            )
        )
    return tuple(issues)


def _validate_wait_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    issues: list[ScenarioValidationIssue] = []
    role = item.get("role")
    if not isinstance(role, str) or not role:
        issues.append(
            _issue(
                f"{path}.role",
                "wait_role_required",
                "wait.role must be a non-empty string",
            )
        )
    timeout = item.get("timeout")
    if timeout is not None:
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout < 0
        ):
            issues.append(
                _issue(
                    f"{path}.timeout",
                    "wait_timeout_invalid",
                    "wait.timeout must be a non-negative number",
                )
            )
    return tuple(issues)


def _validate_wait_reactive_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    issues: list[ScenarioValidationIssue] = []
    condition = item.get("condition")
    if not isinstance(condition, str) or not condition:
        issues.append(
            _issue(
                f"{path}.condition",
                "wait_reactive_condition_required",
                "wait_reactive.condition is required",
            )
        )
    elif condition not in _VALID_REACTIVE_CONDITIONS:
        issues.append(
            _issue(
                f"{path}.condition",
                "wait_reactive_condition_invalid",
                f"wait_reactive.condition must be one of {sorted(_VALID_REACTIVE_CONDITIONS)}",
            )
        )
    return tuple(issues)


def _validate_key_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    keys = item.get("keys")
    if not isinstance(keys, (list, tuple)) or not keys:
        return (
            _issue(
                f"{path}.keys",
                "key_keys_required",
                "key.keys must be a non-empty list of keycode strings",
            ),
        )
    if not all(isinstance(k, str) and k for k in keys):
        return (
            _issue(
                f"{path}.keys",
                "key_keys_required",
                "key.keys entries must be non-empty strings",
            ),
        )
    return ()


def _validate_drag_coords(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    has_literal = all(k in item for k in ("x1", "y1", "x2", "y2"))
    has_source = "source" in item
    if has_literal and has_source:
        return (
            _issue(
                f"{path}",
                "coord_mode_ambiguous",
                "supply literal x1/y1/x2/y2 OR source/dx/dy, not both",
            ),
        )
    if has_source:
        if "dx" not in item or "dy" not in item:
            return (
                _issue(
                    f"{path}",
                    "coord_mode_missing",
                    "source-anchored coords require dx and dy",
                ),
            )
        return ()
    if has_literal:
        return ()
    return (
        _issue(
            f"{path}",
            "coord_mode_missing",
            "supply literal x1/y1/x2/y2 or source/dx/dy",
        ),
    )


def _validate_mouse_scroll_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    direction = item.get("direction")
    if not isinstance(direction, str) or direction not in _VALID_DIRECTIONS:
        return (
            _issue(
                f"{path}.direction",
                "direction_invalid",
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}",
            ),
        )
    return ()


def _validate_gesture_pinch_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    if "scale_factor" not in item:
        return (
            _issue(
                f"{path}.scale_factor",
                "gesture_pinch_scale_factor_required",
                "gesture_pinch.scale_factor is required",
            ),
        )
    return ()


def _validate_gesture_two_finger_scroll_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    direction = item.get("direction")
    if not isinstance(direction, str) or direction not in _VALID_DIRECTIONS:
        return (
            _issue(
                f"{path}.direction",
                "direction_invalid",
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}",
            ),
        )
    return ()


def _validate_swipe_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    issues = list(_validate_drag_coords(item, path))
    duration_ms = item.get("duration_ms")
    if duration_ms is not None:
        if (
            not isinstance(duration_ms, (int, float))
            or isinstance(duration_ms, bool)
            or duration_ms < 0
            or duration_ms > _SWIPE_DURATION_CEILING_MS
        ):
            issues.append(
                _issue(
                    f"{path}.duration_ms",
                    "swipe_duration_exceeded",
                    f"swipe.duration_ms must be in [0, {_SWIPE_DURATION_CEILING_MS}]",
                )
            )
    return tuple(issues)


def _validate_sleep_step(
    item: Mapping[str, Any], path: str
) -> Tuple[ScenarioValidationIssue, ...]:
    """Sleep is the friction-laden time-delay escape hatch.

    Implementation note: special-cased here pending the general per-kind
    parameter validation framework (follow-up contract). Reason field is
    mandatory at length >= 20 to keep sleep usage reviewable; seconds is
    bounded at 5.0 to prevent the kind being misused as a long pause.
    """
    issues: list[ScenarioValidationIssue] = []
    seconds = item.get("seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        issues.append(
            _issue(
                f"{path}.seconds",
                "sleep_ceiling_exceeded",
                "sleep.seconds must be a number in [0.0, 5.0]",
            )
        )
    elif seconds < 0 or seconds > _SLEEP_CEILING_SECONDS:
        issues.append(
            _issue(
                f"{path}.seconds",
                "sleep_ceiling_exceeded",
                f"sleep.seconds must be in [0.0, {_SLEEP_CEILING_SECONDS}]",
            )
        )
    reason = item.get("reason")
    if not isinstance(reason, str) or len(reason) < _SLEEP_REASON_MIN_LENGTH:
        issues.append(
            _issue(
                f"{path}.reason",
                "sleep_reason_too_short",
                f"sleep.reason must be a string of at least {_SLEEP_REASON_MIN_LENGTH} characters",
            )
        )
    return tuple(issues)


def _step_parameters(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if key not in ("id", "kind", "timeout_seconds", "on_failure")
    }


def _private_reference_issues(document: object) -> Tuple[ScenarioValidationIssue, ...]:
    return tuple(
        _issue(path, "private_reference", "public fixture contains a private reference")
        for path, value in _walk_strings(document)
        if _looks_private(value)
    )


def _walk_strings(value: object, path: str = "$") -> Iterable[Tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = str(key) if path == "$" else f"{path}.{key}"
            yield from _walk_strings(child, key_path)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{index}]")


def _looks_private(value: str) -> bool:
    lowered = value.lower()
    return any(fragment in lowered for fragment in _PUBLIC_DENIED_FRAGMENTS)


def _has_start_session(steps: Tuple[ScenarioStep, ...]) -> bool:
    return any(step.kind == "start_session" for step in steps)


def _has_stop_session(steps: Tuple[ScenarioStep, ...]) -> bool:
    return any(step.kind == "stop_session" for step in steps)


def _invalid(path: str, code: str, message: str) -> ScenarioValidationResult:
    return ScenarioValidationResult(
        scenario=None,
        issues=(_issue(path, code, message),),
    )


def _issue(path: str, code: str, message: str) -> ScenarioValidationIssue:
    return ScenarioValidationIssue(path=path, code=code, message=message)
