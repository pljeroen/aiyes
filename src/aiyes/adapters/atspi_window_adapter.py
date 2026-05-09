"""AtSpiWindowAdapter — implements TopLevelWindowPort for Linux.

Enumerates AT-SPI desktop children (no full tree walk) by invoking
the atspi_subprocess_worker with the list-windows subcommand.

When a persistent worker is injected via set_worker(), the adapter will try
the worker first and fall back to one-shot subprocess on any failure.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import TYPE_CHECKING, List, Optional

from aiyes.domain.top_level_window import TopLevelWindow

if TYPE_CHECKING:
    from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

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


class AtSpiWindowAdapter:
    """List top-level windows via AT-SPI desktop children enumeration."""

    def __init__(self) -> None:
        self._worker: Optional[AtSpiWorkerConnection] = None

    def set_worker(self, worker: Optional[AtSpiWorkerConnection]) -> None:
        """Inject the persistent worker connection. Called by composition root."""
        self._worker = worker

    def list_top_level_windows(self, session: object) -> List[TopLevelWindow]:
        """List top-level windows in the session's display.

        Tries the persistent worker first (if available and alive), then
        falls back to one-shot subprocess. Returns empty list on failure
        (no exception propagated).
        """
        display = getattr(session, "display", "")
        bus_address = getattr(session, "atspi_bus_address", "")

        if not display:
            return []

        # Try persistent worker first
        if self._worker is not None and self._worker.is_alive():
            try:
                data = self._worker.send("list_windows")
                windows: List[TopLevelWindow] = []
                for item in data:
                    windows.append(
                        TopLevelWindow(
                            role=item.get("role", ""),
                            name=item.get("name", ""),
                        )
                    )
                return windows
            except (RuntimeError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                _warn_worker_fallback("list_windows", exc)
                # Fall through to one-shot subprocess

        # One-shot fallback (current behavior, unchanged)
        try:
            env = {
                "DISPLAY": display,
                "DBUS_SESSION_BUS_ADDRESS": bus_address,
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
            }

            cmd = [
                sys.executable,
                _WORKER_PATH,
                "list-windows",
                "--display",
                display,
                "--bus",
                bus_address,
            ]

            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return []

            data = json.loads(result.stdout)
            windows_list: List[TopLevelWindow] = []
            for item in data:
                windows_list.append(
                    TopLevelWindow(
                        role=item.get("role", ""),
                        name=item.get("name", ""),
                    )
                )
            return windows_list
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return []
