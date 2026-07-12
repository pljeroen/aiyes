"""Session capabilities use case.

Reports backend-level capability truth so callers can choose semantic or
coordinate interaction strategies before acting.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional, Tuple

from aiyes.ports.storage import SessionRepositoryPort

_VALID_STATUSES = frozenset(("available", "degraded", "unavailable"))


@dataclasses.dataclass(frozen=True)
class Capability:
    """A single backend capability declaration."""

    status: str
    reason: str
    operations: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate status vocabulary and immutability."""
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid capability status: {self.status}")
        if isinstance(self.operations, list):
            object.__setattr__(self, "operations", tuple(self.operations))


@dataclasses.dataclass(frozen=True)
class SessionCapabilitiesResult:
    """Capabilities for a resolved session."""

    session_id: str
    backend: str
    capabilities: Dict[str, Capability]
    live_probe: Optional["CapabilityProbeReport"] = None
    # AIYES-117 (DEC-A7-05 / C-STATESURFACE): top-level marionette state — enabled
    # iff the session was launched with a marionette port. Appended, defaulted.
    marionette_enabled: bool = False
    marionette_port: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class CapabilityProbeCheck:
    """A live backend capability probe check."""

    status: str
    reason: str

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid probe status: {self.status}")


@dataclasses.dataclass(frozen=True)
class CapabilityProbeReport:
    """Live capability probe metadata for a backend."""

    backend: str
    checks: Dict[str, CapabilityProbeCheck]


class SessionCapabilitiesUseCase:
    """Return backend-level capabilities for a stored session."""

    def __init__(
        self,
        session_repo: SessionRepositoryPort,
        android_probe: Optional[Any] = None,
    ) -> None:
        self._session_repo = session_repo
        self._android_probe = android_probe

    def execute(
        self,
        session_id: str,
        live: bool = False,
    ) -> SessionCapabilitiesResult:
        """Return deterministic capability metadata for a session backend."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        backend = getattr(session, "backend", "linux")
        if backend == "android":
            capabilities = _android_capabilities()
        else:
            capabilities = _linux_capabilities()

        live_probe = None
        if live and backend == "android" and self._android_probe is not None:
            live_probe = self._android_probe.probe(session)

        marionette_port = getattr(session, "marionette_port", None)
        return SessionCapabilitiesResult(
            session_id=session_id,
            backend=backend,
            capabilities=capabilities,
            live_probe=live_probe,
            marionette_enabled=marionette_port is not None,
            marionette_port=marionette_port,
        )


def _linux_capabilities() -> Dict[str, Capability]:
    return {
        "semantic_tree": Capability(
            status="available",
            reason="Linux sessions use AT-SPI tree inspection.",
            operations=("inspect", "find", "wait", "diff", "wait-stable"),
        ),
        "screenshot": Capability(
            status="available",
            reason="Linux sessions support screenshot capture through screenshot tools.",
            operations=("inspect", "screenshot"),
        ),
        "semantic_action": Capability(
            status="available",
            reason="Linux sessions can invoke AT-SPI actions exposed by the app.",
            operations=("action", "do"),
        ),
        "coordinate_input": Capability(
            status="available",
            reason="Linux sessions support coordinate input through xdotool.",
            operations=("mouse", "key", "type"),
        ),
        "clipboard": Capability(
            status="available",
            reason="Linux clipboard support is available when xclip is installed.",
            operations=("clipboard",),
        ),
        "resize": Capability(
            status="available",
            reason="Linux Xvfb sessions can be resized.",
            operations=("session resize",),
        ),
        "gesture": Capability(
            status="unavailable",
            reason="Linux backend does not implement mobile multi-touch gestures.",
        ),
        "diff": Capability(
            status="available",
            reason="Linux AT-SPI trees support normal diff semantics.",
            operations=("diff",),
        ),
        "wait_stable": Capability(
            status="available",
            reason="Linux AT-SPI trees support stability polling.",
            operations=("wait-stable",),
        ),
        "reactive_wait": Capability(
            status="available",
            reason=(
                "Linux reactive waits use normalized AT-SPI native events when "
                "available and disclose source=native_event."
            ),
            operations=("wait-reactive",),
        ),
    }


def _android_capabilities() -> Dict[str, Capability]:
    return {
        "semantic_tree": Capability(
            status="available",
            reason="Android sessions use UIAutomator hierarchy inspection.",
            operations=("inspect", "find", "wait"),
        ),
        "screenshot": Capability(
            status="available",
            reason="Android sessions support adb screenshot capture.",
            operations=("inspect", "screenshot"),
        ),
        "semantic_action": Capability(
            status="degraded",
            reason="Android action support depends on UIAutomator node actions and app semantics.",
            operations=("action", "do"),
        ),
        "coordinate_input": Capability(
            status="available",
            reason="Android sessions support adb coordinate and key input.",
            operations=("mouse", "key", "type", "navigate"),
        ),
        "clipboard": Capability(
            status="degraded",
            reason="Android clipboard behavior is platform and focus dependent.",
            operations=("clipboard",),
        ),
        "resize": Capability(
            status="unavailable",
            reason="Android devices and emulators are not resized through AIYES sessions.",
        ),
        "gesture": Capability(
            status="degraded",
            reason="Gestures are best-effort adb input sequences without full multi-pointer truth.",
            operations=("gesture pinch", "gesture two-finger-scroll"),
        ),
        "diff": Capability(
            status="degraded",
            reason="Android UIAutomator trees have fewer states and less stable identity than AT-SPI.",
            operations=("diff",),
        ),
        "wait_stable": Capability(
            status="degraded",
            reason="Android tree stability is approximate because UIAutomator state is lower fidelity.",
            operations=("wait-stable",),
        ),
        "reactive_wait": Capability(
            status="degraded",
            reason=(
                "Android reactive waits use adb foreground state and "
                "UIAutomator snapshot polling; no helper APK is required."
            ),
            operations=("wait-reactive",),
        ),
    }
