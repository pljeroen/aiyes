"""AIYES-117 — MarionetteAdapter pure logic (RED).

Pins FR-07 (C-CONTENT) and FR-08 (C-FRAMING) WITHOUT a live Firefox, by
injecting a frame-level transport seam:

  * framing codec round-trips `<byte-length>:<utf8-json>` (A-M2) — the length
    prefix is the BYTE length of the body, so a multi-byte payload does not
    miscount;
  * execute_script runs in the default CONTENT context — the adapter NEVER emits
    a Marionette:SetContext('chrome') frame (HC-05 least privilege);
  * a webdriver error slot in the response maps to MarionetteScriptOutcome(
    ok=False) (recoverable, no crash);
  * a dead/closed transport RAISES a system error (not swallowed into a
    status-bearing result).

Testability seam (the adapter contract these tests define):
  - module-level ``encode_frame(obj) -> bytes`` / ``decode_frame(bytes) -> obj``;
  - ``MarionetteAdapter(connect=fn)`` where ``fn(host, port) -> T`` yields a
    transport T exposing ``send(frame_obj)`` and
    ``receive() -> frame_obj``; the adapter reads the server hello, establishes an
    (idempotent) WebDriver session, and drives WebDriver:ExecuteScript over T,
    using the A-M3 command/response array form ([0,msgId,name,params] /
    [1,msgId,error_or_null,result_or_null]). Live socket bring-up (A-M1..A-M3) is
    A9's, not a unit here.

RED discipline: the module collects cleanly; the net-new
``aiyes.adapters.marionette_adapter`` symbols are imported INSIDE the tests, so
each fails at CALL time (ImportError on the absent module / the framing / mapping
assertion), never as a collection error.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, List

import pytest


# ═══════════════════════════════════════════════════════════════════════
# Frame-level fake transport (protocol-shaped, reactive — adapts to whatever
# the adapter sends; A-M3 array command/response form).
# ═══════════════════════════════════════════════════════════════════════

_HELLO = {"applicationType": "gecko", "marionetteProtocol": 3}


def _cmd_name(obj: Any) -> str:
    if isinstance(obj, (list, tuple)) and len(obj) >= 3:
        return str(obj[2])
    if isinstance(obj, dict):
        return str(obj.get("name") or obj.get("command") or "")
    return ""


def _cmd_msgid(obj: Any) -> Any:
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        return obj[1]
    if isinstance(obj, dict):
        return obj.get("msgId", obj.get("id", 0))
    return 0


def _cmd_params(obj: Any) -> Any:
    if isinstance(obj, (list, tuple)) and len(obj) >= 4:
        return obj[3]
    if isinstance(obj, dict):
        return obj.get("parameters", obj.get("params", {}))
    return {}


class FakeMarionetteTransport:
    """A reactive frame transport: pre-queues the hello, then answers each
    command as the real Marionette server would (A-M3 response array).

    Configure ``script_error`` to make the ExecuteScript response carry a
    webdriver error slot; otherwise it returns ``script_value``.
    """

    def __init__(
        self,
        script_value: Any = "AIYES Demo",
        script_error: str = "",
    ) -> None:
        self._script_value = script_value
        self._script_error = script_error
        self.sent: List[Any] = []
        self._pending: deque = deque([_HELLO])

    def send(self, frame_obj: Any) -> None:
        self.sent.append(frame_obj)
        name = _cmd_name(frame_obj).lower()
        msgid = _cmd_msgid(frame_obj)
        if "executescript" in name:
            if self._script_error:
                error = {
                    "error": "javascript error",
                    "message": self._script_error,
                    "stacktrace": "",
                }
                self._pending.append([1, msgid, error, None])
            else:
                self._pending.append([1, msgid, None, {"value": self._script_value}])
        elif "newsession" in name:
            self._pending.append(
                [1, msgid, None, {"sessionId": "sess-abc", "capabilities": {}}]
            )
        else:
            # SetContext / FindElement / anything else -> generic ok result.
            self._pending.append([1, msgid, None, {"value": None}])

    def receive(self) -> Any:
        if not self._pending:
            raise AssertionError("transport.receive() with no pending frame")
        return self._pending.popleft()


class DeadTransport:
    """A transport whose I/O always fails (dead/closed socket)."""

    def __init__(self) -> None:
        self.sent: List[Any] = []

    def send(self, frame_obj: Any) -> None:
        raise OSError("transport is dead: broken pipe")

    def receive(self) -> Any:
        raise OSError("transport is dead: connection reset")


def _adapter(transport: Any) -> Any:
    from aiyes.adapters.marionette_adapter import MarionetteAdapter

    # The single connect-factory seam: every (re)connect hands back this transport.
    return MarionetteAdapter(connect=lambda host, port: transport)


def _session() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(session_id="s117", backend="linux", marionette_port=2927)


def _sent_chrome_setcontext(transport: FakeMarionetteTransport) -> bool:
    for obj in transport.sent:
        if "setcontext" not in _cmd_name(obj).lower():
            continue
        params = _cmd_params(obj)
        blob = ""
        if isinstance(params, dict):
            blob = str(params.get("value", "")).lower()
        elif isinstance(params, (list, tuple)):
            blob = " ".join(str(x).lower() for x in params)
        else:
            blob = str(params).lower()
        if "chrome" in blob:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# FR-08 [C-FRAMING] clause 1 — framing codec round-trips (byte-length prefix)
# ═══════════════════════════════════════════════════════════════════════


class TestFramingRoundTripFR08:
    def test_encode_decode_round_trip_ascii(self) -> None:
        from aiyes.adapters.marionette_adapter import decode_frame, encode_frame

        payload = [0, 1, "WebDriver:ExecuteScript", {"script": "return 1;"}]
        frame = encode_frame(payload)
        prefix, sep, body = frame.partition(b":")
        assert sep == b":"
        # The length prefix is the BYTE count of the body (A-M2, not char count).
        assert int(prefix) == len(body)
        assert decode_frame(frame) == payload

    def test_encode_decode_round_trip_multibyte_utf8(self) -> None:
        from aiyes.adapters.marionette_adapter import decode_frame, encode_frame

        # A multi-byte UTF-8 payload — the byte-length must not be miscounted as
        # a character count (the A-M2 dominant framing failure mode).
        payload = {"text": "café ☕ 日本語", "n": 42}
        frame = encode_frame(payload)
        prefix, sep, body = frame.partition(b":")
        assert sep == b":"
        assert int(prefix) == len(body)  # byte length
        assert decode_frame(frame) == payload


# ═══════════════════════════════════════════════════════════════════════
# FR-07 [C-CONTENT] — execute_script uses CONTENT context, never chrome
# ═══════════════════════════════════════════════════════════════════════


class TestContentContextFR07:
    def test_execute_script_uses_content_context_never_chrome(self) -> None:
        transport = FakeMarionetteTransport(script_value="AIYES Demo")
        outcome = _adapter(transport).execute_script(
            _session(), "return document.title;"
        )
        # The script actually ran (content-context default), not a no-op.
        assert outcome.ok is True
        # NO chrome-context switch was ever emitted.
        assert _sent_chrome_setcontext(transport) is False
        # And it did drive an ExecuteScript command.
        assert any("executescript" in _cmd_name(o).lower() for o in transport.sent), (
            f"no ExecuteScript command was sent: {transport.sent!r}"
        )


# ═══════════════════════════════════════════════════════════════════════
# FR-08 [C-FRAMING] clauses 2/3 — webdriver-error maps; dead transport raises
# ═══════════════════════════════════════════════════════════════════════


class TestErrorMappingAndTransportFailureFR08:
    def test_webdriver_error_maps_to_ok_false_outcome(self) -> None:
        transport = FakeMarionetteTransport(
            script_error="ReferenceError: boom is not defined"
        )
        outcome = _adapter(transport).execute_script(_session(), "boom()")
        # Recoverable: mapped to ok=False carrying the message; no crash.
        assert outcome.ok is False
        assert "ReferenceError" in (outcome.error or "")

    def test_dead_transport_raises_system_error(self) -> None:
        adapter = _adapter(DeadTransport())
        # A dead/closed socket is a RAISED system error, NOT a status result.
        with pytest.raises((OSError, ConnectionError, RuntimeError)):
            adapter.execute_script(_session(), "return 1;")


# ═══════════════════════════════════════════════════════════════════════
# A10-AF-005 [C-FRAMING/FR-08] — stale cached transport self-heals on port reuse
# ═══════════════════════════════════════════════════════════════════════


class ControllableTransport:
    """A reactive transport (like FakeMarionetteTransport) with a kill switch.

    After ``kill()``, ``send``/``receive`` raise ConnectionError — modelling a
    Firefox instance that was stopped while its transport stayed in the adapter's
    per-port cache. A fresh instance is what a real reconnect would hand back.
    """

    def __init__(self, script_value: Any = None) -> None:
        self._script_value = script_value
        self.sent: List[Any] = []
        self.closed = False
        self._dead = False
        self._pending: deque = deque([_HELLO])

    def kill(self) -> None:
        self._dead = True

    def send(self, frame_obj: Any) -> None:
        if self._dead:
            raise ConnectionError("marionette socket closed reading length prefix")
        self.sent.append(frame_obj)
        name = _cmd_name(frame_obj).lower()
        msgid = _cmd_msgid(frame_obj)
        if "executescript" in name:
            self._pending.append([1, msgid, None, {"value": self._script_value}])
        elif "newsession" in name:
            self._pending.append(
                [1, msgid, None, {"sessionId": "sess-abc", "capabilities": {}}]
            )
        else:
            self._pending.append([1, msgid, None, {"value": None}])

    def receive(self) -> Any:
        if self._dead:
            raise ConnectionError("marionette socket closed reading length prefix")
        if not self._pending:
            raise AssertionError("transport.receive() with no pending frame")
        return self._pending.popleft()

    def close(self) -> None:
        self.closed = True


class TestStaleTransportReconnectA10AF005:
    """A10-AF-005: marionette_port = 2828 + display_num, and displays are reused
    across sessions, so a new session can land on the SAME port whose cached
    transport belongs to a now-stopped Firefox. The adapter must EVICT the dead
    cached transport, reconnect a fresh one, re-handshake, and retry — NOT hand
    the caller the stale socket (which fails with ConnectionError)."""

    def test_reused_port_dead_cached_transport_reconnects(self) -> None:
        from types import SimpleNamespace

        from aiyes.adapters.marionette_adapter import MarionetteAdapter

        first = ControllableTransport(script_value=100)  # round 0 (later dies)
        second = ControllableTransport(script_value=200)  # round 1 (fresh Firefox)
        handed: List[Any] = []
        queue = deque([first, second])

        def connect(host: str, port: int) -> Any:
            transport = queue.popleft()
            handed.append(transport)
            return transport

        adapter = MarionetteAdapter(connect=connect)
        session = SimpleNamespace(
            session_id="s005", backend="linux", marionette_port=2833
        )

        # Round 0 — fresh session on port 2833: connect, handshake, run.
        out0 = adapter.execute_script(session, "return 100;")
        assert out0.ok is True and out0.value == 100
        assert handed == [first]  # exactly one connect
        assert any("executescript" in _cmd_name(o).lower() for o in first.sent)

        # Firefox stops; the cached transport is now a dead socket. A new session
        # reuses the same display -> same derived port 2833 (same cache key).
        first.kill()

        # Round 1 — pre-fix this reuses the dead cached transport and raises
        # ConnectionError; the fix must evict + reconnect to `second` and succeed.
        out1 = adapter.execute_script(session, "return 200;")
        assert out1.ok is True and out1.value == 200

        # It self-healed: reconnected exactly once to a fresh transport, closed
        # the dead one, and drove ExecuteScript on the live transport.
        assert handed == [first, second]
        assert first.closed is True
        assert any("executescript" in _cmd_name(o).lower() for o in second.sent)
