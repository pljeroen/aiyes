"""Domain value types for port return values.

These types define the domain contract for port return values,
replacing untyped Any/dict returns with explicit value objects.
All types are stdlib-only, frozen dataclasses.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    pass


@dataclasses.dataclass(frozen=True)
class BusStartResult:
    """Result of starting the AT-SPI2 bus."""

    pid: int
    bus_address: str
    atspi_bus_address: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ActionPortResult:
    """Result of executing an accessibility action via the port."""

    success: bool
    available_actions: Tuple[str, ...]
    node_value: Optional[str] = None
    node_states: Optional[Tuple[str, ...]] = None
    action_method: Optional[str] = None

    def __post_init__(self) -> None:
        """Ensure tuple fields are truly immutable."""
        if isinstance(self.available_actions, list):
            object.__setattr__(self, "available_actions", tuple(self.available_actions))
        if isinstance(self.node_states, list):
            object.__setattr__(self, "node_states", tuple(self.node_states))


_ANDROID_DEPS = frozenset(("adb", "android_device"))


@dataclasses.dataclass(frozen=True)
class DependencyResult:
    """Result of checking a single system dependency."""

    name: str
    status: str
    message: str

    @property
    def category(self) -> str:
        """Backend category for this dependency (linux or android)."""
        if self.name in _ANDROID_DEPS:
            return "android"
        return "linux"


@dataclasses.dataclass(frozen=True)
class StoredTree:
    """Stored accessibility tree with optional node ID registry."""

    tree: "AccessibilityTree"  # type: ignore[name-defined]  # noqa: F821
    registry: Optional["NodeIdRegistry"] = None  # type: ignore[name-defined]  # noqa: F821
