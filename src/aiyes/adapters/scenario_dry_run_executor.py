"""Dry-run scenario executor used until real scenario execution slices land."""

from __future__ import annotations

from aiyes.domain.scenario import ScenarioStep
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult


class ScenarioDryRunExecutor:
    """Record declared steps without performing GUI actions."""

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output={"dry_run": True, "kind": step.kind},
            error="",
            session_id="dry-run-session" if step.kind == "start_session" else "",
        )

