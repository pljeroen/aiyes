"""Backend-neutral reactive wait domain model."""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Optional, Tuple


_VALID_EVENT_SOURCES = frozenset(
    ("native_event", "adb_state_poll", "snapshot_poll", "unsupported")
)
_VALID_FAILURE_CODES = frozenset(
    (
        "timeout",
        "unsupported_condition",
        "observer_error",
        "invalid_pattern",
        "session_not_found",
    )
)


class ReactiveWaitCondition(str, Enum):
    """Supported reactive wait conditions."""

    SCREEN_CHANGE = "screen-change"
    NODE_APPEARS = "node-appears"
    NODE_DISAPPEARS = "node-disappears"
    FOCUS_CHANGE = "focus-change"
    APP_CHANGE = "app-change"


@dataclasses.dataclass(frozen=True)
class GuiEvent:
    """Normalized GUI event shared by Linux and Android observers."""

    type: str
    source: str
    timestamp: float
    node_id: Optional[str] = None
    role: Optional[str] = None
    name: Optional[str] = None
    app: Optional[str] = None

    def __post_init__(self) -> None:
        if self.source not in _VALID_EVENT_SOURCES:
            raise ValueError(f"invalid event source: {self.source}")


@dataclasses.dataclass(frozen=True)
class ReactiveWaitRequest:
    """Observer request for one backend-neutral reactive wait."""

    condition: ReactiveWaitCondition
    name_pattern: Optional[str] = None
    timeout: float = 10.0
    quiet: float = 0.0
    poll_interval: float = 0.25


@dataclasses.dataclass(frozen=True)
class ReactiveWaitResult:
    """Backend-neutral result shape returned by CLI and MCP."""

    condition: str
    matched: bool
    timeout: bool
    backend: str
    source: str
    elapsed_ms: int
    polls: int
    events: Tuple[GuiEvent, ...] = ()
    failure_code: Optional[str] = None
    next_actions: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source not in _VALID_EVENT_SOURCES:
            raise ValueError(f"invalid result source: {self.source}")
        if (
            self.failure_code is not None
            and self.failure_code not in _VALID_FAILURE_CODES
        ):
            raise ValueError(f"invalid failure code: {self.failure_code}")
        if isinstance(self.events, list):
            object.__setattr__(self, "events", tuple(self.events))
        if isinstance(self.next_actions, list):
            object.__setattr__(self, "next_actions", tuple(self.next_actions))

    @classmethod
    def matched_result(
        cls,
        *,
        condition: str,
        backend: str,
        source: str,
        elapsed_ms: int,
        polls: int,
        events: Tuple[GuiEvent, ...],
    ) -> "ReactiveWaitResult":
        return cls(
            condition=condition,
            matched=True,
            timeout=False,
            backend=backend,
            source=source,
            elapsed_ms=elapsed_ms,
            polls=polls,
            events=events,
        )

    @classmethod
    def timeout_result(
        cls,
        *,
        condition: str,
        backend: str,
        source: str,
        elapsed_ms: int,
        polls: int,
    ) -> "ReactiveWaitResult":
        return cls(
            condition=condition,
            matched=False,
            timeout=True,
            backend=backend,
            source=source,
            elapsed_ms=elapsed_ms,
            polls=polls,
            failure_code="timeout",
            next_actions=("Inspect current GUI state or increase timeout.",),
        )

    @classmethod
    def unsupported_condition(
        cls,
        *,
        condition: str,
        backend: str,
        elapsed_ms: int,
        polls: int,
    ) -> "ReactiveWaitResult":
        return cls(
            condition=condition,
            matched=False,
            timeout=False,
            backend=backend,
            source="unsupported",
            elapsed_ms=elapsed_ms,
            polls=polls,
            failure_code="unsupported_condition",
            next_actions=("Use inspect or wait-stable for this backend/condition.",),
        )

    @classmethod
    def observer_error(
        cls,
        *,
        condition: str,
        backend: str,
        elapsed_ms: int,
        polls: int,
    ) -> "ReactiveWaitResult":
        return cls(
            condition=condition,
            matched=False,
            timeout=False,
            backend=backend,
            source="unsupported",
            elapsed_ms=elapsed_ms,
            polls=polls,
            failure_code="observer_error",
            next_actions=("Inspect backend availability and retry.",),
        )

    @classmethod
    def session_not_found(cls, *, session_id: str) -> "ReactiveWaitResult":
        return cls(
            condition="",
            matched=False,
            timeout=False,
            backend="unknown",
            source="unsupported",
            elapsed_ms=0,
            polls=0,
            failure_code="session_not_found",
            next_actions=(f"Start or select a valid session: {session_id}",),
        )


def parse_reactive_condition(value: str) -> Optional[ReactiveWaitCondition]:
    """Parse a public condition string without raising for normal failures."""
    for condition in ReactiveWaitCondition:
        if value == condition.value:
            return condition
    return None

