"""Linux reactive wait observer using an isolated AT-SPI worker boundary."""

from __future__ import annotations

import json
import subprocess
from typing import Optional, Protocol

from aiyes.domain.reactive_wait import (
    GuiEvent,
    ReactiveWaitCondition,
    ReactiveWaitRequest,
    ReactiveWaitResult,
)
from aiyes.domain.session import Session
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.clock import ClockPort


class _EventWorker(Protocol):
    def wait_event(
        self,
        session: Session,
        condition: str,
        timeout: float,
    ) -> tuple[str, ...]:
        ...


class AtSpiEventWorkerClient:
    """Small subprocess client for native AT-SPI event waits."""

    def __init__(self, worker_module: str = "aiyes.adapters.atspi_subprocess_worker") -> None:
        self._worker_module = worker_module

    def command_for(self, session: Session, condition: str, timeout: float) -> list[str]:
        command = ["python", "-m", self._worker_module, "event"]
        display = getattr(session, "display", "")
        bus = getattr(session, "atspi_bus_address", "")
        if display:
            command.extend(["--display", display])
        if bus:
            command.extend(["--bus", bus])
        command.extend(["--event", condition, "--timeout", str(timeout)])
        return command

    def wait_event(
        self,
        session: Session,
        condition: str,
        timeout: float,
    ) -> tuple[str, ...]:
        result = subprocess.run(
            self.command_for(session, condition, timeout),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 2.0,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "AT-SPI event worker failed")
        return tuple(line for line in result.stdout.splitlines() if line.strip())


class LinuxReactiveWaitObserver:
    """Reactive wait observer for Linux sessions."""

    def __init__(
        self,
        tree_port: AccessibilityTreePort,
        worker: _EventWorker,
        clock: ClockPort,
    ) -> None:
        self._tree_port = tree_port
        self._worker = worker
        self._clock = clock

    def wait(
        self,
        session: Session,
        request: ReactiveWaitRequest,
    ) -> ReactiveWaitResult:
        if request.condition in (
            ReactiveWaitCondition.FOCUS_CHANGE,
            ReactiveWaitCondition.SCREEN_CHANGE,
        ):
            return self._wait_native_event(session, request)
        return ReactiveWaitResult.unsupported_condition(
            condition=request.condition.value,
            backend="linux",
            elapsed_ms=0,
            polls=0,
        )

    def _wait_native_event(
        self,
        session: Session,
        request: ReactiveWaitRequest,
    ) -> ReactiveWaitResult:
        start = self._clock.now()
        try:
            lines = self._worker.wait_event(
                session,
                request.condition.value,
                request.timeout,
            )
        except Exception:
            return ReactiveWaitResult.observer_error(
                condition=request.condition.value,
                backend="linux",
                elapsed_ms=_elapsed_ms(start, self._clock.now()),
                polls=0,
            )

        events = tuple(
            event
            for event in (parse_atspi_event_line(line) for line in lines)
            if event is not None
        )
        if events:
            return ReactiveWaitResult.matched_result(
                condition=request.condition.value,
                backend="linux",
                source="native_event",
                elapsed_ms=_elapsed_ms(start, self._clock.now()),
                polls=1,
                events=events,
            )
        return ReactiveWaitResult.timeout_result(
            condition=request.condition.value,
            backend="linux",
            source="native_event",
            elapsed_ms=_elapsed_ms(start, self._clock.now()),
            polls=1,
        )


def parse_atspi_event_line(line: str) -> Optional[GuiEvent]:
    """Parse one worker JSON line into a normalized GUI event."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    source = payload.get("source", "native_event")
    if source != "native_event":
        source = "native_event"
    return GuiEvent(
        type=str(payload.get("type", "")),
        source=source,
        timestamp=float(payload.get("timestamp", 0.0)),
        node_id=_optional_str(payload.get("node_id")),
        role=_optional_str(payload.get("role")),
        name=_optional_str(payload.get("name")),
        app=_optional_str(payload.get("app")),
    )


def _optional_str(value: object) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _elapsed_ms(start: float, now: float) -> int:
    return int(max(0.0, now - start) * 1000)
