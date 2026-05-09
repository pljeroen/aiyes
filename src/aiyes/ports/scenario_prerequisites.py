"""Port for release-scenario prerequisite checks."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Protocol, Tuple


@dataclasses.dataclass(frozen=True)
class ScenarioPrerequisiteResult:
    """Result for one declared scenario prerequisite."""

    prerequisite_id: str
    status: str
    reason: str = ""
    details: Mapping[str, Any] = dataclasses.field(default_factory=dict)


class ScenarioPrerequisiteCheckerPort(Protocol):
    """Check scenario prerequisites without executing scenario steps."""

    def check(
        self, prerequisites: Tuple[Mapping[str, Any], ...]
    ) -> Tuple[ScenarioPrerequisiteResult, ...]:
        """Return one result per prerequisite."""
