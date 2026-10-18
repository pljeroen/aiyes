"""AIYES-180 regression: Marionette owns reloads for Marionette sessions."""

from __future__ import annotations

from collections import deque
from typing import Any, List, Optional, Tuple

from aiyes.adapters.marionette_adapter import MarionetteAdapter
from aiyes.domain.session import Session
from aiyes.domain.use_cases.reload import ReloadUseCase


def _session(marionette_port: Optional[int]) -> Session:
    return Session(
        session_id="s180",
        app_pid=100,
        app_command="firefox",
        app_args=(),
        name=None,
        display=":99",
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
        backend="linux",
        marionette_port=marionette_port,
    )


class _Repository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self, session_id: str) -> Optional[Session]:
        if session_id == self._session.session_id:
            return self._session
        return None


class _RecordingInput:
    def __init__(self, events: List[Tuple[Any, ...]]) -> None:
        self._events = events

    def key(self, session: Session, key_specs: List[str]) -> None:
        self._events.append(("key", tuple(key_specs)))


class _RecordingMarionette:
    def __init__(self, events: List[Tuple[Any, ...]]) -> None:
        self._events = events

    def refresh(self, session: Session) -> None:
        self._events.append(("refresh", session.session_id))


def test_marionette_enabled_reload_uses_native_refresh_without_keyboard_input() -> None:
    events: List[Tuple[Any, ...]] = []
    result = ReloadUseCase(
        input_port=_RecordingInput(events),
        session_repo=_Repository(_session(2927)),
        marionette=_RecordingMarionette(events),
    ).execute("s180")

    assert result.status == "ok"
    assert events == [("refresh", "s180")]


def test_non_marionette_reload_preserves_cache_bypass_keyboard_fallback() -> None:
    events: List[Tuple[Any, ...]] = []
    result = ReloadUseCase(
        input_port=_RecordingInput(events),
        session_repo=_Repository(_session(None)),
        marionette=_RecordingMarionette(events),
    ).execute("s180")

    assert result.status == "ok"
    assert events == [("key", ("ctrl+shift+r",))]


class _RefreshTransport:
    def __init__(self) -> None:
        self.sent: List[Any] = []
        self._pending: deque = deque(
            [{"applicationType": "gecko", "marionetteProtocol": 3}]
        )

    def send(self, frame_obj: Any) -> None:
        self.sent.append(frame_obj)
        msg_id = frame_obj[1]
        name = frame_obj[2]
        if name == "WebDriver:NewSession":
            result = {"sessionId": "sess-180", "capabilities": {}}
        else:
            result = {"value": None}
        self._pending.append([1, msg_id, None, result])

    def receive(self) -> Any:
        return self._pending.popleft()


def test_marionette_adapter_sends_native_refresh_command() -> None:
    transport = _RefreshTransport()
    adapter = MarionetteAdapter(connect=lambda host, port: transport)

    adapter.refresh(_session(2927))

    assert [frame[2] for frame in transport.sent] == [
        "WebDriver:NewSession",
        "WebDriver:Refresh",
    ]
