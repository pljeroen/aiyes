"""System-backed scenario prerequisite checker."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from shutil import which as shutil_which
from typing import Any, Mapping, Optional, Tuple

from aiyes.ports.scenario_prerequisites import ScenarioPrerequisiteResult


class SystemScenarioPrerequisiteChecker:
    """Check local executables and Android device availability."""

    def __init__(
        self,
        *,
        which: Callable[[str], Optional[str]] = shutil_which,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._which = which
        self._run = run

    def check(
        self, prerequisites: Tuple[Mapping[str, Any], ...]
    ) -> Tuple[ScenarioPrerequisiteResult, ...]:
        results: list[ScenarioPrerequisiteResult] = []
        for prerequisite in prerequisites:
            results.append(self._check_one(prerequisite))
        return tuple(results)

    def _check_one(self, prerequisite: Mapping[str, Any]) -> ScenarioPrerequisiteResult:
        kind = str(prerequisite.get("kind", ""))
        if kind == "executable":
            return self._check_executable(prerequisite)
        if kind == "android_device":
            return self._check_android_device(prerequisite)
        return ScenarioPrerequisiteResult(
            prerequisite_id=f"{kind or 'unknown'}:unsupported",
            status="passed",
            details=dict(prerequisite),
        )

    def _check_executable(
        self, prerequisite: Mapping[str, Any]
    ) -> ScenarioPrerequisiteResult:
        name = str(prerequisite.get("name", ""))
        if not name:
            return ScenarioPrerequisiteResult(
                prerequisite_id="executable:",
                status="skipped",
                reason="executable prerequisite is missing a name",
                details=dict(prerequisite),
            )
        if self._which(name) is None:
            return ScenarioPrerequisiteResult(
                prerequisite_id=f"executable:{name}",
                status="skipped",
                reason=f"required executable not found: {name}",
                details=dict(prerequisite),
            )
        return ScenarioPrerequisiteResult(
            prerequisite_id=f"executable:{name}",
            status="passed",
            details=dict(prerequisite),
        )

    def _check_android_device(
        self, prerequisite: Mapping[str, Any]
    ) -> ScenarioPrerequisiteResult:
        serial = str(prerequisite.get("serial", "auto"))
        if self._which("adb") is None:
            return ScenarioPrerequisiteResult(
                prerequisite_id=f"android_device:{serial}",
                status="skipped",
                reason="required executable not found: adb",
                details=dict(prerequisite),
            )
        try:
            completed = self._run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ScenarioPrerequisiteResult(
                prerequisite_id=f"android_device:{serial}",
                status="skipped",
                reason=f"adb device check failed: {exc}",
                details=dict(prerequisite),
            )

        if completed.returncode != 0:
            return ScenarioPrerequisiteResult(
                prerequisite_id=f"android_device:{serial}",
                status="skipped",
                reason="adb devices returned a non-zero exit status",
                details=dict(prerequisite),
            )
        if _has_matching_device(completed.stdout, serial):
            return ScenarioPrerequisiteResult(
                prerequisite_id=f"android_device:{serial}",
                status="passed",
                details=dict(prerequisite),
            )
        return ScenarioPrerequisiteResult(
            prerequisite_id=f"android_device:{serial}",
            status="skipped",
            reason="no matching Android device is available",
            details=dict(prerequisite),
        )


def _has_matching_device(output: str, serial: str) -> bool:
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        if serial == "auto" or parts[0] == serial:
            return True
    return False
