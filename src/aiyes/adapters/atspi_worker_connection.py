"""Parent-side connection manager for the persistent AT-SPI worker.

Manages the lifecycle of a persistent AT-SPI worker subprocess:
- Launches the worker with Popen
- Waits for the "ready" handshake
- Sends commands via stdin, reads responses via stdout (NDJSON)
- Thread-safe via lock
- Graceful shutdown with fallback to SIGTERM/SIGKILL

See CONTRACT.md sections 3.5-3.8 for specification.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional


class AtSpiWorkerConnection:
    """Manages a persistent AT-SPI2 worker subprocess."""

    def __init__(
        self,
        display: str,
        bus_address: str,
        worker_script: Optional[str] = None,
        startup_timeout: float = 10.0,
        command_timeout: float = 30.0,
        worker_extra_args: Optional[List[str]] = None,
    ) -> None:
        self._display = display
        self._bus_address = bus_address
        self._worker_script = worker_script or self._default_worker_script()
        self._startup_timeout = startup_timeout
        self._command_timeout = command_timeout
        self._worker_extra_args = worker_extra_args or []
        self._proc: Optional[subprocess.Popen] = None
        self._req_counter: int = 0
        self._lock = threading.Lock()

    @staticmethod
    def _default_worker_script() -> str:
        """Return path to the default persistent worker script."""
        return os.path.join(
            os.path.dirname(__file__),
            "atspi_persistent_worker.py",
        )

    def start(self) -> None:
        """Launch the worker subprocess and wait for the 'ready' handshake.

        Raises RuntimeError if the handshake is not received within the
        startup timeout.
        """
        cmd = [
            sys.executable,
            self._worker_script,
            "--display",
            self._display,
            "--bus",
            self._bus_address,
        ] + self._worker_extra_args

        env = {
            "DISPLAY": self._display,
            "DBUS_SESSION_BUS_ADDRESS": self._bus_address,
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # Wait for handshake
        raw = self._readline_with_timeout(self._startup_timeout)
        if raw is None:
            self._proc.kill()
            self._proc.wait()
            self._proc = None
            raise RuntimeError("AT-SPI worker failed to start: handshake timeout")

        try:
            handshake = json.loads(raw)
        except json.JSONDecodeError:
            self._proc.kill()
            self._proc.wait()
            self._proc = None
            raise RuntimeError(
                f"AT-SPI worker failed to start: invalid handshake: {raw!r}"
            )

        if handshake.get("status") != "ready":
            self._proc.kill()
            self._proc.wait()
            self._proc = None
            raise RuntimeError(
                f"AT-SPI worker failed to start: unexpected handshake: {handshake}"
            )

    def stop(self) -> None:
        """Send shutdown command, wait for exit, fallback to SIGTERM/SIGKILL."""
        if self._proc is None:
            return

        try:
            # Try graceful shutdown
            self._req_counter += 1
            req_id = str(self._req_counter)
            request = {"req_id": req_id, "cmd": "shutdown"}
            self._proc.stdin.write(json.dumps(request).encode() + b"\n")  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]

            # Read shutdown response (5s timeout)
            self._readline_with_timeout(5.0)

            # Wait for process to exit
            self._proc.wait(timeout=5)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            # Fallback: terminate then kill
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        finally:
            self._proc = None

    def send(self, cmd: str, **kwargs: Any) -> Dict[str, Any]:
        """Send a command and return the parsed response. Thread-safe.

        Raises RuntimeError if the worker is dead or returns an error.
        Raises TimeoutError if the response is not received within timeout.
        """
        with self._lock:
            self._ensure_alive()

            self._req_counter += 1
            req_id = str(self._req_counter)
            request: Dict[str, Any] = {"req_id": req_id, "cmd": cmd, **kwargs}

            try:
                assert self._proc is not None
                assert self._proc.stdin is not None
                self._proc.stdin.write(json.dumps(request).encode() + b"\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError(f"Worker process died: {exc}") from exc

            raw = self._readline_with_timeout(self._command_timeout)
            if raw is None:
                raise TimeoutError(
                    f"Worker response timeout after {self._command_timeout}s"
                )

            try:
                response = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Worker returned invalid JSON: {raw!r}") from exc

            if response.get("req_id") != req_id:
                raise RuntimeError(
                    f"Response req_id mismatch: expected {req_id}, "
                    f"got {response.get('req_id')}"
                )

            if not response.get("ok"):
                error = response.get("error", {})
                raise RuntimeError(f"Worker error: {error.get('message', 'unknown')}")

            return response["result"]

    def is_alive(self) -> bool:
        """Check if the worker process is running (poll() is None)."""
        return self._proc is not None and self._proc.poll() is None

    def _ensure_alive(self) -> None:
        """Raise RuntimeError if worker has died."""
        if self._proc is None:
            raise RuntimeError("Worker not started")
        if self._proc.poll() is not None:
            raise RuntimeError(
                f"Worker process died with exit code {self._proc.returncode}"
            )

    def _readline_with_timeout(self, timeout: float) -> Optional[bytes]:
        """Read a line from stdout with timeout using selectors.

        Returns None if timeout expires. Returns the raw bytes line otherwise.
        """
        if self._proc is None or self._proc.stdout is None:
            return None

        sel = selectors.DefaultSelector()
        try:
            sel.register(self._proc.stdout, selectors.EVENT_READ)
            events = sel.select(timeout=timeout)
            if not events:
                return None
            return self._proc.stdout.readline()
        finally:
            sel.close()
