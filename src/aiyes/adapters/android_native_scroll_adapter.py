"""Android native accessibility scroll helper adapter."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from typing import Optional

from aiyes.domain.types import NativeScrollResult

_ACTION_BY_DIRECTION = {
    "down": ("ACTION_SCROLL_FORWARD", 4096),
    "up": ("ACTION_SCROLL_BACKWARD", 8192),
}
_SUMMARY_LIMIT = 500
_DEFAULT_HELPER_COMMAND = (
    sys.executable,
    "-m",
    "aiyes.adapters.android_native_scroll_helper",
)


def _summary(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.strip()[:_SUMMARY_LIMIT]


class AndroidNativeScrollAdapter:
    """Invokes a configured helper for Android accessibility scroll actions."""

    def __init__(self, helper_command: Optional[Sequence[str]] = None) -> None:
        if helper_command is None:
            configured = os.environ.get("AIYES_ANDROID_NATIVE_SCROLL_HELPER", "")
            self._helper_command = (
                tuple(shlex.split(configured)) if configured else _DEFAULT_HELPER_COMMAND
            )
        else:
            self._helper_command = tuple(helper_command)

    def scroll(
        self,
        session: object,
        node_id: str,
        direction: str,
        *,
        stable_id: str = "",
        bounds: Optional[tuple[int, int, int, int]] = None,
    ) -> NativeScrollResult:
        action = _ACTION_BY_DIRECTION.get(direction)
        if action is None:
            return NativeScrollResult(
                success=False,
                method="android_accessibility_helper",
                requested_action="",
                action_id=0,
                node_id=node_id,
                direction=direction,
                stable_id=stable_id or None,
                bounds=bounds,
                fallback_reason="native_scroll_direction_unsupported",
            )

        action_name, action_id = action
        if not self._helper_command:
            return NativeScrollResult(
                success=False,
                method="android_accessibility_helper",
                requested_action=action_name,
                action_id=action_id,
                node_id=node_id,
                direction=direction,
                stable_id=stable_id or None,
                bounds=bounds,
                fallback_reason="native_scroll_helper_unavailable",
            )

        serial = str(getattr(session, "device_serial", "") or "")
        if not serial:
            return NativeScrollResult(
                success=False,
                method="android_accessibility_helper",
                requested_action=action_name,
                action_id=action_id,
                node_id=node_id,
                direction=direction,
                stable_id=stable_id or None,
                bounds=bounds,
                fallback_reason="android_session_missing_serial",
            )

        cmd = [
            *self._helper_command,
            "--serial",
            serial,
            "--node-id",
            node_id,
            "--stable-id",
            stable_id,
            "--direction",
            direction,
            "--action",
            action_name,
            "--action-id",
            str(action_id),
        ]
        if bounds is not None:
            cmd.extend(["--bounds", ",".join(str(part) for part in bounds)])
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            return NativeScrollResult(
                success=False,
                method="android_accessibility_helper",
                requested_action=action_name,
                action_id=action_id,
                node_id=node_id,
                direction=direction,
                stable_id=stable_id or None,
                bounds=bounds,
                fallback_reason="native_scroll_helper_unavailable",
            )
        except subprocess.TimeoutExpired as exc:
            return NativeScrollResult(
                success=False,
                method="android_accessibility_helper",
                requested_action=action_name,
                action_id=action_id,
                node_id=node_id,
                direction=direction,
                stable_id=stable_id or None,
                bounds=bounds,
                stdout_summary=_summary(exc.stdout),
                stderr_summary=_summary(exc.stderr),
                fallback_reason="native_scroll_helper_timeout",
            )

        success = result.returncode == 0
        return NativeScrollResult(
            success=success,
            method="android_accessibility_helper",
            requested_action=action_name,
            action_id=action_id,
            node_id=node_id,
            direction=direction,
            stable_id=stable_id or None,
            bounds=bounds,
            returncode=result.returncode,
            stdout_summary=_summary(result.stdout),
            stderr_summary=_summary(result.stderr),
            fallback_reason=None if success else "native_scroll_helper_failed",
        )
