from __future__ import annotations

from aiyes.domain.reactive_wait import (
    GuiEvent,
    ReactiveWaitCondition,
    ReactiveWaitRequest,
    ReactiveWaitResult,
)
from aiyes.domain.session import Session
from aiyes.domain.use_cases.reactive_wait import ReactiveWaitUseCase


class _Repo:
    def __init__(self, session: Session | None) -> None:
        self.session = session

    def load(self, session_id: str) -> Session | None:
        return self.session if session_id == "s1" else None


class _Observer:
    def __init__(self, result: ReactiveWaitResult) -> None:
        self.result = result
        self.requests: list[ReactiveWaitRequest] = []

    def wait(self, session: Session, request: ReactiveWaitRequest) -> ReactiveWaitResult:
        self.requests.append(request)
        return self.result


def _session(backend: str = "linux") -> Session:
    return Session(
        session_id="s1",
        app_pid=1,
        app_command="app",
        app_args=(),
        name=None,
        backend=backend,
    )


def test_reactive_wait_use_case_returns_matched_result() -> None:
    event = GuiEvent(
        type="node-appears",
        source="native_event",
        timestamp=1.0,
        node_id="n1",
        role="button",
        name="Save",
    )
    observer = _Observer(
        ReactiveWaitResult.matched_result(
            condition="node-appears",
            backend="linux",
            source="native_event",
            elapsed_ms=12,
            polls=1,
            events=(event,),
        )
    )

    result = ReactiveWaitUseCase(_Repo(_session()), observer).execute(
        session_id="s1",
        condition="node-appears",
        name_pattern="Save",
        timeout=1.0,
        quiet=0.0,
        poll_interval=0.1,
    )

    assert result.matched is True
    assert result.timeout is False
    assert result.failure_code is None
    assert result.events == (event,)
    assert observer.requests[0].condition == ReactiveWaitCondition.NODE_APPEARS


def test_reactive_wait_use_case_returns_timeout_semantic_result() -> None:
    observer = _Observer(
        ReactiveWaitResult.timeout_result(
            condition="screen-change",
            backend="android",
            source="snapshot_poll",
            elapsed_ms=1000,
            polls=4,
        )
    )

    result = ReactiveWaitUseCase(_Repo(_session("android")), observer).execute(
        session_id="s1",
        condition="screen-change",
        timeout=1.0,
        quiet=0.0,
        poll_interval=0.25,
    )

    assert result.matched is False
    assert result.timeout is True
    assert result.failure_code == "timeout"
    assert result.polls == 4


def test_invalid_condition_returns_unsupported_condition() -> None:
    observer = _Observer(
        ReactiveWaitResult.timeout_result(
            condition="unused",
            backend="linux",
            source="unsupported",
            elapsed_ms=0,
            polls=0,
        )
    )

    result = ReactiveWaitUseCase(_Repo(_session()), observer).execute(
        session_id="s1",
        condition="not-a-condition",
        timeout=1.0,
        quiet=0.0,
        poll_interval=0.1,
    )

    assert result.failure_code == "unsupported_condition"
    assert result.source == "unsupported"
    assert result.next_actions
    assert observer.requests == []


def test_observer_exception_returns_observer_error() -> None:
    class BrokenObserver:
        def wait(
            self, session: Session, request: ReactiveWaitRequest
        ) -> ReactiveWaitResult:
            raise RuntimeError("backend failed")

    result = ReactiveWaitUseCase(_Repo(_session()), BrokenObserver()).execute(
        session_id="s1",
        condition="focus-change",
        timeout=1.0,
        quiet=0.0,
        poll_interval=0.1,
    )

    assert result.failure_code == "observer_error"
    assert result.matched is False
    assert any("Inspect backend availability" in item for item in result.next_actions)
