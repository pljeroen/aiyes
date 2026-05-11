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
        "screenshot",
        "navigate",
        "stop_session",
        "assert",
    )
)
_SLEEP_CEILING_SECONDS = 5.0
_SLEEP_REASON_MIN_LENGTH = 20
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
        if kind == "sleep":
            issues.extend(_validate_sleep_step(item, f"{path}[{index}]"))
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
