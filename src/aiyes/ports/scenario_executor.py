"""Port for deterministic release-scenario step execution."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Protocol

from aiyes.domain.scenario import ScenarioStep


@dataclasses.dataclass(frozen=True)
class ScenarioStepExecutionResult:
    """Result returned by a scenario step executor."""

    step_id: str
    status: str
    output: Mapping[str, Any]
    error: str = ""
    session_id: str = ""


class ScenarioStepExecutorPort(Protocol):
    """Execute one already-validated scenario step."""

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Execute a scenario step and return a normalized result."""

