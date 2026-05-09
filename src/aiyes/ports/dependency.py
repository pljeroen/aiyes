"""Dependency check port — Protocol for checking system dependencies."""

from __future__ import annotations

from typing import List, Protocol

from aiyes.domain.types import DependencyResult


class DependencyCheckPort(Protocol):
    """Port for checking system dependency availability."""

    def check(self, name: str) -> DependencyResult:
        """Check a single dependency. Returns DependencyResult."""
        ...

    def check_all(self) -> List[DependencyResult]:
        """Check all dependencies. Returns list of DependencyResult."""
        ...
