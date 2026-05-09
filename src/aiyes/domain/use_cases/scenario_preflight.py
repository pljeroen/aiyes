"""Scenario preflight use case."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Tuple

from aiyes.domain.scenario import ReleaseScenario, ScenarioValidationIssue
from aiyes.domain.use_cases.scenario_run import ScenarioNextAction
from aiyes.ports.scenario_prerequisites import (
    ScenarioPrerequisiteCheckerPort,
    ScenarioPrerequisiteResult,
)


@dataclasses.dataclass(frozen=True)
class ScenarioEvidencePathCheck:
    """Preflight result for an optional evidence output path."""

    status: str
    path: str = ""
    reason: str = ""


@dataclasses.dataclass(frozen=True)
class ScenarioPreflightResult:
    """Machine-readable scenario readiness result."""

    scenario_id: str
    status: str
    mode: str
    requested_mode: str
    target: str
    prerequisites: Tuple[Mapping[str, Any], ...]
    evidence_dir: ScenarioEvidencePathCheck
    validation_errors: Tuple[ScenarioValidationIssue, ...] = ()
    failure_code: str = ""
    next_actions: Tuple[ScenarioNextAction, ...] = ()
    steps_would_execute: bool = False


class ScenarioPreflightUseCase:
    """Validate whether a scenario can run without executing its steps."""

    def __init__(
        self, prerequisite_checker: ScenarioPrerequisiteCheckerPort | None = None
    ) -> None:
        self._prerequisite_checker = prerequisite_checker

    def execute(
        self,
        scenario: ReleaseScenario,
        *,
        real_execution: bool,
        evidence_dir: ScenarioEvidencePathCheck | None = None,
    ) -> ScenarioPreflightResult:
        requested_mode = "real" if real_execution else "dry_run"
        evidence = evidence_dir or ScenarioEvidencePathCheck(status="not_requested")
        if evidence.status == "failed":
            return ScenarioPreflightResult(
                scenario_id=scenario.id,
                status="failed",
                mode="preflight",
                requested_mode=requested_mode,
                target=scenario.target,
                prerequisites=(),
                evidence_dir=evidence,
                failure_code="evidence_path_rejected",
                next_actions=(
                    ScenarioNextAction(
                        code="choose_safe_evidence_dir",
                        message=evidence.reason,
                    ),
                ),
            )

        prerequisite_results: Tuple[ScenarioPrerequisiteResult, ...] = ()
        if real_execution and self._prerequisite_checker is not None:
            prerequisite_results = self._prerequisite_checker.check(
                scenario.prerequisites
            )
        serialized = tuple(_prerequisite_dict(result) for result in prerequisite_results)
        for result in prerequisite_results:
            if result.status != "passed":
                return ScenarioPreflightResult(
                    scenario_id=scenario.id,
                    status="skipped",
                    mode="preflight",
                    requested_mode=requested_mode,
                    target=scenario.target,
                    prerequisites=serialized,
                    evidence_dir=evidence,
                    failure_code="prerequisite_missing",
                    next_actions=(
                        ScenarioNextAction(
                            code="install_prerequisite",
                            message=result.reason,
                        ),
                    ),
                )

        return ScenarioPreflightResult(
            scenario_id=scenario.id,
            status="passed",
            mode="preflight",
            requested_mode=requested_mode,
            target=scenario.target,
            prerequisites=serialized,
            evidence_dir=evidence,
        )


def scenario_validation_preflight_result(
    errors: Tuple[ScenarioValidationIssue, ...],
) -> ScenarioPreflightResult:
    """Build a failed preflight result from loader validation errors."""
    return ScenarioPreflightResult(
        scenario_id="",
        status="failed",
        mode="preflight",
        requested_mode="unknown",
        target="",
        prerequisites=(),
        evidence_dir=ScenarioEvidencePathCheck(status="not_requested"),
        validation_errors=errors,
        failure_code="validation_error",
        next_actions=(
            ScenarioNextAction(
                code="fix_scenario_file",
                message="Fix scenario validation errors, then rerun preflight.",
            ),
        ),
    )


def _prerequisite_dict(result: ScenarioPrerequisiteResult) -> Mapping[str, Any]:
    return {
        "prerequisite_id": result.prerequisite_id,
        "status": result.status,
        "reason": result.reason,
        "details": dict(result.details),
    }
