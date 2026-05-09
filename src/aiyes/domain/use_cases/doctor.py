"""Doctor use case — check system dependencies."""

from __future__ import annotations

from typing import List

from aiyes.domain.types import DependencyResult
from aiyes.ports.dependency import DependencyCheckPort


class DoctorUseCase:
    """Check system dependencies for aiyes operation."""

    def __init__(self, dependency_check: DependencyCheckPort) -> None:
        self._dependency_check = dependency_check

    def execute(self) -> List[DependencyResult]:
        """Run all dependency checks.

        Returns a list of DependencyResult domain objects.
        """
        return self._dependency_check.check_all()
