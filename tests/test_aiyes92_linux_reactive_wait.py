from __future__ import annotations

import json

from aiyes.adapters.linux_reactive_wait_adapter import (
    AtSpiEventWorkerClient,
    LinuxReactiveWaitObserver,
    parse_atspi_event_line,
)
from aiyes.domain.reactive_wait import ReactiveWaitCondition, ReactiveWaitRequest
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node


class _Clock:
    def __init__(self) -> None:
        self.current = 0.0

    def now(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


class _TreePort:
    def get_tree(self, session: Session) -> AccessibilityTree:
        return AccessibilityTree(
            roots=(
                Node(
                    id="focus-1",
                    role="entry",
                    name="Search",
                    bounds=(0, 0, 10, 10),
                    states=(),
                    actions=(),
                ),
            )
        )


class _Worker:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def wait_event(
        self, session: Session, condition: str, timeout: float
    ) -> tuple[str, ...]:
        return tuple(self.lines)


def _session() -> Session:
    return Session(
        session_id="s1",
        app_pid=1,
        app_command="app",
        app_args=(),
        name=None,
        backend="linux",
    )


def test_worker_json_line_parses_to_gui_event() -> None:
    event = parse_atspi_event_line(
        json.dumps(
            {
                "type": "focus-change",
                "source": "native_event",
                "timestamp": 2.0,
                "node_id": "n1",
                "role": "entry",
                "name": "Search",
            }
        )
    )

    assert event is not None
    assert event.type == "focus-change"
    assert event.source == "native_event"
    assert event.name == "Search"


def test_linux_focus_change_returns_native_event_source() -> None:
    observer = LinuxReactiveWaitObserver(
        tree_port=_TreePort(),
        worker=_Worker(
            [
                json.dumps(
                    {
                        "type": "focus-change",
                        "source": "native_event",
                        "timestamp": 2.0,
                        "node_id": "n1",
                        "role": "entry",
                        "name": "Search",
                    }
                )
            ]
        ),
        clock=_Clock(),
    )

    result = observer.wait(
        _session(),
        ReactiveWaitRequest(
            condition=ReactiveWaitCondition.FOCUS_CHANGE,
            timeout=1.0,
            quiet=0.0,
            poll_interval=0.1,
        ),
    )

    assert result.matched is True
    assert result.source == "native_event"
    assert result.events[0].type == "focus-change"


def test_linux_worker_failure_returns_observer_error() -> None:
    class BrokenWorker:
        def wait_event(
            self, session: Session, condition: str, timeout: float
        ) -> tuple[str, ...]:
            raise RuntimeError("worker failed")

    result = LinuxReactiveWaitObserver(
        tree_port=_TreePort(),
        worker=BrokenWorker(),
        clock=_Clock(),
    ).wait(
        _session(),
        ReactiveWaitRequest(
            condition=ReactiveWaitCondition.FOCUS_CHANGE,
            timeout=1.0,
            quiet=0.0,
            poll_interval=0.1,
        ),
    )

    assert result.failure_code == "observer_error"
    assert result.next_actions


def test_atspi_event_worker_client_uses_subprocess_boundary() -> None:
    client = AtSpiEventWorkerClient(worker_module="aiyes.adapters.atspi_subprocess_worker")

    command = client.command_for(_session(), "focus-change", 1.5)

    assert command[:4] == [
        "python",
        "-m",
        "aiyes.adapters.atspi_subprocess_worker",
        "event",
    ]
    assert "--event" in command
    assert "focus-change" in command
