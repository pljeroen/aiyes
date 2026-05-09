"""Reactive wait use case."""

from __future__ import annotations

from aiyes.domain.reactive_wait import (
    ReactiveWaitRequest,
    ReactiveWaitResult,
    parse_reactive_condition,
)
from aiyes.ports.reactive_observer import ReactiveObserverPort
from aiyes.ports.storage import SessionRepositoryPort


class ReactiveWaitUseCase:
    """Validate reactive wait inputs and delegate observation to a port."""

    def __init__(
        self,
        session_repo: SessionRepositoryPort,
        observer: ReactiveObserverPort,
    ) -> None:
        self._session_repo = session_repo
        self._observer = observer

    def execute(
        self,
        *,
        session_id: str,
        condition: str,
        name_pattern: str | None = None,
        timeout: float = 10.0,
        quiet: float = 0.0,
        poll_interval: float = 0.25,
    ) -> ReactiveWaitResult:
        parsed = parse_reactive_condition(condition)
        if parsed is None:
            return ReactiveWaitResult.unsupported_condition(
                condition=condition,
                backend="unknown",
                elapsed_ms=0,
                polls=0,
            )

        session = self._session_repo.load(session_id)
        if session is None:
            return ReactiveWaitResult.session_not_found(session_id=session_id)

        request = ReactiveWaitRequest(
            condition=parsed,
            name_pattern=name_pattern,
            timeout=timeout,
            quiet=quiet,
            poll_interval=poll_interval,
        )
        try:
            return self._observer.wait(session, request)
        except Exception:
            return ReactiveWaitResult.observer_error(
                condition=parsed.value,
                backend=getattr(session, "backend", "unknown"),
                elapsed_ms=0,
                polls=0,
            )

