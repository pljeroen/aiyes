"""Reactive observer port."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aiyes.domain.reactive_wait import ReactiveWaitRequest, ReactiveWaitResult

if TYPE_CHECKING:
    from aiyes.domain.session import Session


class ReactiveObserverPort(Protocol):
    """Port implemented by backend-specific reactive wait observers."""

    def wait(
        self, session: "Session", request: ReactiveWaitRequest
    ) -> ReactiveWaitResult:
        """Wait for a backend event or polling-derived state change."""
        ...

