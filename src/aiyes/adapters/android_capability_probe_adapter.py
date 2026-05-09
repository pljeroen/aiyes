"""Android live capability probe adapter."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Optional

from aiyes.domain.session import android_package_name
from aiyes.domain.use_cases.session_capabilities import (
    CapabilityProbeCheck,
    CapabilityProbeReport,
)


Runner = Callable[..., Any]


class AndroidCapabilityProbeAdapter:
    """Probe Android runtime readiness without mutating the target app."""

    def __init__(
        self,
        runner: Runner = subprocess.run,
        adb_path: Optional[str] = None,
    ) -> None:
        self._runner = runner
        self._adb_path = adb_path

    def probe(self, session: object) -> CapabilityProbeReport:
        adb = self._resolve_adb()
        serial = getattr(session, "device_serial", None)
        checks: Dict[str, CapabilityProbeCheck] = {}

        if not serial:
            unavailable = CapabilityProbeCheck(
                status="unavailable",
                reason="Android session has no device_serial.",
            )
            return CapabilityProbeReport(
                backend="android",
                checks={
                    "device": unavailable,
                    "package": unavailable,
                    "uiautomator_tree": unavailable,
                    "screenshot": unavailable,
                },
            )

        checks["device"] = self._probe_device(adb, serial)
        checks["package"] = self._probe_package(adb, serial, session)
        checks["uiautomator_tree"] = self._probe_tree(adb, serial)
        checks["screenshot"] = self._probe_screenshot(adb, serial)
        return CapabilityProbeReport(backend="android", checks=checks)

    def _resolve_adb(self) -> str:
        if self._adb_path is not None:
            return self._adb_path
        from aiyes.adapters.adb_path import resolve_adb_path

        return resolve_adb_path()

    def _probe_device(self, adb: str, serial: str) -> CapabilityProbeCheck:
        result = self._run_text([adb, "-s", serial, "get-state"])
        if result.returncode == 0 and result.stdout.strip() == "device":
            return CapabilityProbeCheck(
                status="available",
                reason=f"adb reports {serial} as device.",
            )
        return CapabilityProbeCheck(
            status="unavailable",
            reason=_failure_reason(result, "adb device state is not available"),
        )

    def _probe_package(
        self,
        adb: str,
        serial: str,
        session: object,
    ) -> CapabilityProbeCheck:
        package = android_package_name(session)
        if not package:
            return CapabilityProbeCheck(
                status="degraded",
                reason="No Android package identity is stored for this session.",
            )
        result = self._run_text([adb, "-s", serial, "shell", "pidof", package])
        if result.returncode == 0 and result.stdout.strip():
            return CapabilityProbeCheck(
                status="available",
                reason=f"{package} is reachable on {serial}.",
            )
        return CapabilityProbeCheck(
            status="degraded",
            reason=_failure_reason(result, f"{package} is not confirmed running"),
        )

    def _probe_tree(self, adb: str, serial: str) -> CapabilityProbeCheck:
        result = self._run_text(
            [adb, "-s", serial, "exec-out", "uiautomator", "dump", "/dev/stdout"]
        )
        if result.returncode != 0:
            return CapabilityProbeCheck(
                status="unavailable",
                reason=_failure_reason(result, "UIAutomator dump failed"),
            )
        try:
            root = _parse_hierarchy(result.stdout)
        except Exception as exc:
            return CapabilityProbeCheck(
                status="unavailable",
                reason=f"UIAutomator XML could not be parsed: {exc}",
            )
        nodes = list(root.iter("node"))
        named = [
            node
            for node in nodes
            if node.get("text", "") or node.get("content-desc", "")
        ]
        if named:
            return CapabilityProbeCheck(
                status="available",
                reason=f"UIAutomator returned {len(nodes)} nodes with named nodes.",
            )
        if nodes:
            return CapabilityProbeCheck(
                status="degraded",
                reason=f"UIAutomator returned {len(nodes)} nodes but no named nodes.",
            )
        return CapabilityProbeCheck(
            status="degraded",
            reason="UIAutomator returned an empty hierarchy.",
        )

    def _probe_screenshot(self, adb: str, serial: str) -> CapabilityProbeCheck:
        result = self._run_bytes([adb, "-s", serial, "exec-out", "screencap", "-p"])
        if result.returncode == 0 and result.stdout.startswith(b"\x89PNG"):
            return CapabilityProbeCheck(
                status="available",
                reason="screencap returned PNG bytes.",
            )
        return CapabilityProbeCheck(
            status="unavailable",
            reason=_failure_reason(result, "screencap did not return PNG bytes"),
        )

    def _run_text(self, command: list[str]) -> Any:
        return self._runner(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )

    def _run_bytes(self, command: list[str]) -> Any:
        return self._runner(
            command,
            capture_output=True,
            check=False,
            timeout=10,
        )


def _failure_reason(result: Any, fallback: str) -> str:
    stderr = getattr(result, "stderr", "")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    stderr = stderr.strip()
    if stderr:
        return stderr
    return fallback


def _parse_hierarchy(xml_text: str) -> ET.Element:
    marker = "</hierarchy>"
    idx = xml_text.find(marker)
    if idx != -1:
        xml_text = xml_text[: idx + len(marker)]
    return ET.fromstring(xml_text)
