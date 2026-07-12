"""Marionette adapter — stdlib TCP client for the Firefox Marionette protocol.

Owns ALL socket / protocol / base64 I/O for the DOM-lens (HC-03 / NFR-02): the
`<byte-length>:<utf8-json>` framing (A-M2), the server hello handshake (A-M1/A-M2),
the [0,msgId,name,params] / [1,msgId,error,result] command/response array form
with msgId correlation (A-M3), an idempotent WebDriver session per connection, and
WebDriver:ExecuteScript in the default CONTENT context — it NEVER emits a
Marionette:SetContext('chrome') frame (HC-05 / C-CONTENT least privilege).

A webdriver error slot maps to MarionetteScriptOutcome(ok=False) (recoverable, no
crash); a dead/closed transport propagates its exception (a raised system error,
never swallowed into a status result).

Testability seam: MarionetteAdapter(connect=fn) injects a *connect factory*
``fn(host, port) -> transport`` where a transport exposes ``send(frame_obj)`` /
``receive() -> frame_obj`` (and optionally ``close()``). This unit-tests the pure
protocol logic without a live socket AND exercises the reconnect path — the
adapter calls the factory again to re-establish a fresh transport after a dead
cached socket. In production ``connect`` is None and the adapter opens a real TCP
socket to 127.0.0.1:<session.marionette_port>, caching one transport per port.

Self-healing (A10-AF-005): marionette ports are derived from the display, and
displays are reused across sessions, so a *cached* transport can belong to a
stopped Firefox. When a command against a reused (cached) transport fails at the
connection level, the adapter evicts it, reconnects, re-handshakes, and retries
ONCE — a fresh (just-connected) transport that fails propagates as a real error.
"""

from __future__ import annotations

import base64
import json
import socket
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from aiyes.domain.marionette_outcome import MarionetteScriptOutcome

# W3C WebElement identifier key — FindElement returns {value: {<this>: <uuid>}}.
_WEB_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"

_DEFAULT_HOST = "127.0.0.1"

# Connection-level failures that indicate a dead/closed transport (as opposed to
# a webdriver/JS error carried in the response, or a protocol desync that raises
# RuntimeError). ConnectionError and BrokenPipeError are OSError subclasses;
# EOFError is not, so it is listed explicitly.
_DEAD_SOCKET_ERRORS = (ConnectionError, OSError, EOFError)


# ─────────────────────────────────────────────────────────────────────────
# Framing codec — <byte-length>:<utf8-json> (A-M2). The prefix is the UTF-8
# BYTE length of the body, never a character count.
# ─────────────────────────────────────────────────────────────────────────


def encode_frame(obj: Any) -> bytes:
    """Encode a JSON-serializable object as a length-prefixed Marionette frame."""
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return str(len(body)).encode("ascii") + b":" + body


def decode_frame(frame: bytes) -> Any:
    """Decode a `<byte-length>:<utf8-json>` frame back to its object."""
    prefix, sep, body = frame.partition(b":")
    if sep != b":":
        raise ValueError("malformed marionette frame: missing length prefix")
    expected = int(prefix)
    if len(body) != expected:
        raise ValueError(
            f"marionette frame length mismatch: prefix={expected}, body={len(body)}"
        )
    return json.loads(body.decode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────
# Real socket transport (production). Streams length-prefixed frames off a TCP
# socket; reuses the pure encode_frame/decode_frame codec.
# ─────────────────────────────────────────────────────────────────────────


class _SocketTransport:
    """Frame transport over a live TCP socket (127.0.0.1:<marionette_port>)."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = b""

    def send(self, frame_obj: Any) -> None:
        self._sock.sendall(encode_frame(frame_obj))

    def receive(self) -> Any:
        # Read the ASCII length prefix up to ':'.
        while b":" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("marionette socket closed reading length prefix")
            self._buf += chunk
        prefix, _, rest = self._buf.partition(b":")
        length = int(prefix)
        self._buf = rest
        while len(self._buf) < length:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("marionette socket closed reading frame body")
            self._buf += chunk
        body = self._buf[:length]
        self._buf = self._buf[length:]
        return decode_frame(prefix + b":" + body)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _connect_socket(host: str, port: int, timeout: float = 30.0) -> _SocketTransport:
    """Open a TCP connection to a Marionette server and wrap it as a transport."""
    sock = socket.create_connection((host, port), timeout=timeout)
    return _SocketTransport(sock)


# ─────────────────────────────────────────────────────────────────────────
# MarionetteAdapter
# ─────────────────────────────────────────────────────────────────────────


class MarionetteAdapter:
    """Drive a Firefox content session over the Marionette wire protocol."""

    def __init__(
        self,
        connect: Any = None,
        host: str = _DEFAULT_HOST,
    ) -> None:
        self._connect = connect or _connect_socket
        self._host = host
        self._conns: Dict[int, Any] = {}
        self._ready: set = set()
        self._msg_id = 0

    # ---- MarionettePort surface -----------------------------------------

    def execute_script(
        self, session: Any, script: str, args: Any = None
    ) -> MarionetteScriptOutcome:
        """Run ``script`` in CONTENT context; map its value or webdriver error."""
        params: Dict[str, Any] = {
            "script": script,
            "args": list(args) if args else [],
        }

        def work(transport: Any) -> Tuple[Any, Any]:
            return self._command(transport, "WebDriver:ExecuteScript", params)

        error, result = self._with_reconnect(session, work)
        if error:
            return MarionetteScriptOutcome(ok=False, error=_error_message(error))
        value = result.get("value") if isinstance(result, dict) else result
        return MarionetteScriptOutcome(ok=True, value=value)

    def screenshot_element(self, session: Any, css_selector: str) -> Optional[str]:
        """Find the element, capture its PNG (scroll-into-view), return a temp path."""

        def work(transport: Any) -> Optional[bytes]:
            find_err, find_res = self._command(
                transport,
                "WebDriver:FindElement",
                {"using": "css selector", "value": css_selector},
            )
            if find_err:
                return None
            element_ref = _extract_element_ref(find_res)
            if element_ref is None:
                return None
            shot_err, shot_res = self._command(
                transport,
                "WebDriver:TakeScreenshot",
                {"id": element_ref, "full": False, "hash": False, "scroll": True},
            )
            if shot_err:
                return None
            encoded = shot_res.get("value") if isinstance(shot_res, dict) else None
            if not encoded or not isinstance(encoded, str):
                return None
            return base64.b64decode(encoded)

        png_bytes = self._with_reconnect(session, work)
        if png_bytes is None:
            return None
        with tempfile.NamedTemporaryFile(
            suffix=".png", prefix="aiyes-marionette-", delete=False
        ) as handle:
            handle.write(png_bytes)
            return handle.name

    # ---- protocol plumbing ----------------------------------------------

    def _with_reconnect(self, session: Any, work: Any) -> Any:
        """Run ``work(transport)`` against the session's transport, self-healing a
        stale/dead cached socket ONCE (A10-AF-005).

        The per-port transport is reused across calls (the within-session
        handshake is not repeated). Because derived marionette ports are reused
        across sessions, a *cached* transport can be a stopped Firefox's dead
        socket. When a command against such a reused transport fails at the
        connection level, evict it, reconnect a fresh socket, re-handshake, and
        retry the work ONCE. A fresh (just-connected) transport that fails is a
        genuine dead server: propagate without retry.
        """
        transport, from_cache = self._transport_for(session)
        try:
            self._ensure_ready(transport)
            return work(transport)
        except _DEAD_SOCKET_ERRORS:
            if not from_cache:
                raise
            self._evict(session)
            transport, _ = self._transport_for(session)
            self._ensure_ready(transport)
            return work(transport)

    def _transport_for(self, session: Any) -> Tuple[Any, bool]:
        """Return ``(transport, from_cache)`` for the session's marionette port,
        connecting (and caching) a fresh transport on a cache miss."""
        port = getattr(session, "marionette_port", None)
        if port is None:
            raise RuntimeError("session is not marionette-enabled (no marionette_port)")
        existing = self._conns.get(port)
        if existing is not None:
            return existing, True
        transport = self._connect(self._host, port)
        self._conns[port] = transport
        return transport, False

    def _evict(self, session: Any) -> None:
        """Drop the cached transport for the session's port and close its socket."""
        port = getattr(session, "marionette_port", None)
        transport = self._conns.pop(port, None)
        if transport is None:
            return
        self._ready.discard(id(transport))
        close = getattr(transport, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass

    def _ensure_ready(self, transport: Any) -> None:
        """Read the server hello and establish a WebDriver session, once per conn."""
        if id(transport) in self._ready:
            return
        # A-M1/A-M2: the server pushes a hello frame on connect.
        transport.receive()
        # A-M3: idempotent WebDriver session (default context is CONTENT — we
        # never switch to chrome).
        self._command(transport, "WebDriver:NewSession", {})
        self._ready.add(id(transport))

    def _command(
        self, transport: Any, name: str, params: Dict[str, Any]
    ) -> Tuple[Any, Any]:
        """Send a command array and return (error_slot, result_slot) correlated by msgId."""
        self._msg_id += 1
        msg_id = self._msg_id
        transport.send([0, msg_id, name, params])
        frame = transport.receive()
        if not isinstance(frame, (list, tuple)) or len(frame) < 4:
            raise RuntimeError(f"unexpected marionette response frame: {frame!r}")
        if frame[0] != 1 or frame[1] != msg_id:
            raise RuntimeError(
                f"marionette response desync: expected msgId {msg_id}, got {frame!r}"
            )
        return frame[2], frame[3]


def _error_message(error: Any) -> str:
    """Extract a human-readable message from a webdriver error slot."""
    if isinstance(error, dict):
        return str(
            error.get("message")
            or error.get("error")
            or error.get("stacktrace")
            or "webdriver error"
        )
    return str(error)


def _extract_element_ref(result: Any) -> Optional[str]:
    """Pull the opaque element reference out of a FindElement result."""
    if not isinstance(result, dict):
        return None
    value = result.get("value")
    if isinstance(value, dict):
        ref = value.get(_WEB_ELEMENT_KEY)
        if isinstance(ref, str):
            return ref
        for candidate in value.values():
            if isinstance(candidate, str):
                return candidate
    elif isinstance(value, str):
        return value
    return None


__all__: List[str] = [
    "MarionetteAdapter",
    "encode_frame",
    "decode_frame",
]
