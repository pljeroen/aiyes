"""AtSpi2TreeAdapter — implements AccessibilityTreePort via subprocess isolation.

AT-SPI queries run in a subprocess where DISPLAY and DBUS_SESSION_BUS_ADDRESS
are set BEFORE gi.repository.Atspi is imported. This bypasses libatspi's
per-process D-Bus connection cache.

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

from aiyes.domain.node_id import NodeIdRegistry  # noqa: E501
from aiyes.domain.tree import AccessibilityTree, raw_tree_to_domain

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


class AtSpi2TreeAdapter:
    """Queries the AT-SPI2 tree via subprocess and converts to domain AccessibilityTree."""

    def __init__(self) -> None:
        self._registry: Optional[NodeIdRegistry] = None
        self._worker: Optional[AtSpiWorkerConnection] = None

    def set_worker(self, worker: Optional[AtSpiWorkerConnection]) -> None:
        """Inject the persistent worker connection. Called by composition root."""
        self._worker = worker

    @property
    def last_registry(self) -> Optional[NodeIdRegistry]:
        """Return the NodeIdRegistry from the most recent get_tree call."""
        return self._registry

    def get_tree(self, session, bus_address: str = "") -> AccessibilityTree:
        """Get the accessibility tree for the given session.

        Accepts a Session object or (display_str, bus_address) for backward
        compatibility during migration.

        Tries the persistent worker first (if available and alive), then
        falls back to one-shot subprocess.
        """
        if isinstance(session, str):
            # Legacy call: get_tree(display, bus_address)
            display = session
        else:
            display = session.display
            bus_address = session.atspi_bus_address

        if not _GI_AVAILABLE:
            raise RuntimeError(
                "gi.repository.Atspi is not available. "
                "Install python3-gi and gir1.2-atspi-2.0."
            )

        # Try persistent worker first
        if self._worker is not None and self._worker.is_alive():
            try:
                data = self._worker.send("get_tree")
                registry_mapping = data.get("registry", {})
                registry = NodeIdRegistry.from_mapping(registry_mapping)
                self._registry = registry
                return raw_tree_to_domain(data)
            except (RuntimeError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                _warn_worker_fallback("get_tree", exc)
                # Fall through to one-shot subprocess

        # One-shot fallback (current behavior, unchanged)
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
            "tree",
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
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"AT-SPI subprocess worker failed (rc={result.returncode}): "
                f"{result.stderr}"
            )

        data = json.loads(result.stdout)

        # Reconstruct registry from worker output
        registry_mapping = data.get("registry", {})
        registry = NodeIdRegistry.from_mapping(registry_mapping)
        self._registry = registry

        # Convert tree data to domain model
        tree = raw_tree_to_domain(data)
        return tree
