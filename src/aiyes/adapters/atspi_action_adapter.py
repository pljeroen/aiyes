"""AtSpi2ActionAdapter — implements AccessibilityActionPort via subprocess isolation.

AT-SPI action execution runs in a subprocess where DISPLAY and
DBUS_SESSION_BUS_ADDRESS are set BEFORE gi.repository.Atspi is imported.
This bypasses libatspi's per-process D-Bus connection cache.

When a persistent worker is injected via set_worker(), the adapter will try
the worker first and fall back to one-shot subprocess on any failure.

The gi import guard is kept for error reporting only.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import TYPE_CHECKING, Optional

from aiyes.domain.types import ActionPortResult

if TYPE_CHECKING:
    from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

try:
    import gi  # noqa: F401

    _GI_AVAILABLE = True
except ImportError:
    _GI_AVAILABLE = False

# Sentinel — kept only for reference but Atspi is never used in this process
Atspi = None  # type: ignore[assignment]

_WORKER_PATH = str(pathlib.Path(__file__).parent / "atspi_subprocess_worker.py")


def _warn_worker_fallback(context: str, exc: Exception) -> None:
    """Log a structured warning to stderr when falling back from worker."""
    warning = json.dumps(
        {
            "warning": "worker_fallback",
            "context": context,
            "error": str(exc),
        }
    )
    sys.stderr.write(warning + "\n")
    sys.stderr.flush()


class AtSpi2ActionAdapter:
    """Executes AT-SPI2 accessibility actions via subprocess isolation."""

    def __init__(self) -> None:
        self._worker: Optional[AtSpiWorkerConnection] = None

    def set_worker(self, worker: Optional[AtSpiWorkerConnection]) -> None:
        """Inject the persistent worker connection. Called by composition root."""
        self._worker = worker

    @staticmethod
    def _decode_worker_error(stderr: str) -> str:
        """Extract a readable worker error message from stderr JSON if present."""
        if not stderr:
            return "AT-SPI action worker failed without stderr output"
        try:
            data = json.loads(stderr)
        except (json.JSONDecodeError, ValueError):
            return stderr.strip() or "AT-SPI action worker failed"

        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message
        error = data.get("error")
        if isinstance(error, str) and error.strip():
            return error
        return stderr.strip() or "AT-SPI action worker failed"

    def do_action(
        self,
        session: object,
        node_id: str = "",
        action_name: str = "",
        value: Optional[str] = None,
        registry: Optional[object] = None,
    ) -> ActionPortResult:
        """Execute an action on a node. Returns ActionPortResult.

        Tries the persistent worker first (if available and alive), then
        falls back to one-shot subprocess.

        When registry is provided, uses persisted path info for stable
        node resolution instead of rebuilding IDs from a fresh tree walk.
        """
        display = session.display  # type: ignore[union-attr]
        bus_address = session.atspi_bus_address  # type: ignore[union-attr]

        # Try persistent worker first
        if self._worker is not None and self._worker.is_alive():
            try:
                registry_data = registry.get_mapping() if registry else None  # type: ignore[union-attr]
                response = self._worker.send(
                    "do_action",
                    node_id=node_id,
                    action_name=action_name,
                    value=value,
                    registry=registry_data,
                )
                return ActionPortResult(
                    success=response.get("success", False),
                    available_actions=tuple(response.get("available_actions", [])),
                    node_value=response.get("node_value"),
                    node_states=(
                        tuple(response["node_states"])
                        if response.get("node_states")
                        else None
                    ),
                )
            except (RuntimeError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                _warn_worker_fallback("do_action", exc)
                # Fall through to one-shot subprocess

        # One-shot fallback (current behavior, unchanged)
        if not _GI_AVAILABLE:
            raise RuntimeError(
                "gi.repository.Atspi is not available. "
                "Install python3-gi and gir1.2-atspi-2.0."
            )

        env = {
            "DISPLAY": display,
            "DBUS_SESSION_BUS_ADDRESS": bus_address,
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        # AT_SPI_BUS_ADDRESS must NOT be in the subprocess env

        cmd = [
            sys.executable,
            _WORKER_PATH,
            "action",
            "--display",
            display,
            "--bus",
            bus_address,
            "--node-id",
            node_id,
            "--action",
            action_name,
        ]

        if value is not None:
            cmd.extend(["--value", value])

        if registry is not None:
            registry_data = registry.get_mapping()  # type: ignore[union-attr]
            cmd.extend(["--registry", json.dumps(registry_data)])

        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            detail = self._decode_worker_error(result.stderr)
            raise RuntimeError(f"AT-SPI action worker failed: {detail}")

        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError("AT-SPI action worker returned invalid JSON")

        return ActionPortResult(
            success=data.get("success", False),
            available_actions=tuple(data.get("available_actions", [])),
            node_value=data.get("node_value"),
            node_states=tuple(data["node_states"]) if data.get("node_states") else None,
        )
