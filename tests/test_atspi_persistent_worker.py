"""Tests for the persistent AT-SPI worker protocol.

Tests the NDJSON request/response protocol without requiring AT-SPI.
The worker process is tested via subprocess to verify protocol compliance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional

import pytest


def _worker_path() -> str:
    """Return absolute path to the persistent worker script."""
    return os.path.join(
        os.path.dirname(__file__),
        os.pardir,
        "src",
        "aiyes",
        "adapters",
        "atspi_persistent_worker.py",
    )


def _start_worker(
    extra_args: Optional[list] = None,
) -> subprocess.Popen:
    """Start the persistent worker as a subprocess."""
    cmd = [
        sys.executable,
        _worker_path(),
        "--display",
        ":99",
        "--bus",
        "unix:path=/tmp/test-dbus",
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _send(proc: subprocess.Popen, request: Dict[str, Any]) -> Dict[str, Any]:
    """Send a request and read the response."""
    assert proc.stdin is not None
    assert proc.stdout is not None
    line = json.dumps(request) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()
    raw = proc.stdout.readline()
    return json.loads(raw)


def _read_handshake(proc: subprocess.Popen) -> Dict[str, Any]:
    """Read the startup handshake."""
    assert proc.stdout is not None
    raw = proc.stdout.readline()
    return json.loads(raw)


class TestWorkerStartupHandshake:
    """Worker writes {"status": "ready"} on startup."""

    def test_worker_startup_handshake(self) -> None:
        proc = _start_worker()
        try:
            handshake = _read_handshake(proc)
            assert handshake == {"status": "ready"}
        finally:
            proc.kill()
            proc.wait()


class TestWorkerPing:
    """ping command returns {"status": "alive"}."""

    def test_worker_ping(self) -> None:
        proc = _start_worker()
        try:
            _read_handshake(proc)
            resp = _send(proc, {"req_id": "1", "cmd": "ping"})
            assert resp["req_id"] == "1"
            assert resp["ok"] is True
            assert resp["result"] == {"status": "alive"}
        finally:
            proc.kill()
            proc.wait()


class TestWorkerShutdown:
    """shutdown command returns response then worker exits with code 0."""

    def test_worker_shutdown(self) -> None:
        proc = _start_worker()
        try:
            _read_handshake(proc)
            resp = _send(proc, {"req_id": "99", "cmd": "shutdown"})
            assert resp["req_id"] == "99"
            assert resp["ok"] is True
            assert resp["result"] == {"status": "shutting_down"}
            exit_code = proc.wait(timeout=5)
            assert exit_code == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class TestWorkerInvalidJson:
    """Invalid JSON input produces error response, worker continues."""

    def test_worker_invalid_json(self) -> None:
        proc = _start_worker()
        try:
            _read_handshake(proc)

            # Send invalid JSON
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(b"not valid json\n")
            proc.stdin.flush()
            raw = proc.stdout.readline()
            error_resp = json.loads(raw)

            assert error_resp["req_id"] is None
            assert error_resp["ok"] is False
            assert error_resp["error"]["type"] == "JSONDecodeError"

            # Worker should still be alive and handle next command
            resp = _send(proc, {"req_id": "2", "cmd": "ping"})
            assert resp["ok"] is True
        finally:
            proc.kill()
            proc.wait()


class TestWorkerUnknownCommand:
    """Unknown command produces error response, worker continues."""

    def test_worker_unknown_command(self) -> None:
        proc = _start_worker()
        try:
            _read_handshake(proc)
            resp = _send(proc, {"req_id": "1", "cmd": "nonexistent_command"})
            assert resp["req_id"] == "1"
            assert resp["ok"] is False
            assert "error" in resp

            # Worker should continue
            resp2 = _send(proc, {"req_id": "2", "cmd": "ping"})
            assert resp2["ok"] is True
        finally:
            proc.kill()
            proc.wait()


class TestWorkerStdinCloseExits:
    """Worker exits when stdin is closed."""

    def test_worker_stdin_close_exits(self) -> None:
        proc = _start_worker()
        try:
            _read_handshake(proc)
            assert proc.stdin is not None
            proc.stdin.close()
            exit_code = proc.wait(timeout=5)
            assert exit_code == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class TestWorkerMultipleCommands:
    """Multiple commands in sequence all get correct responses."""

    def test_worker_multiple_commands(self) -> None:
        proc = _start_worker()
        try:
            _read_handshake(proc)

            for i in range(5):
                resp = _send(proc, {"req_id": str(i), "cmd": "ping"})
                assert resp["req_id"] == str(i)
                assert resp["ok"] is True
                assert resp["result"]["status"] == "alive"
        finally:
            proc.kill()
            proc.wait()


class TestWorkerReqIdEchoed:
    """Response req_id matches request req_id for various ID values."""

    @pytest.mark.parametrize("req_id", ["1", "abc", "req-42", ""])
    def test_worker_req_id_echoed(self, req_id: str) -> None:
        proc = _start_worker()
        try:
            _read_handshake(proc)
            resp = _send(proc, {"req_id": req_id, "cmd": "ping"})
            assert resp["req_id"] == req_id
        finally:
            proc.kill()
            proc.wait()
