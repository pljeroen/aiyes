"""Navigate use case — platform-abstracted back/home/recent navigation."""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.ports.input import InputPort
from aiyes.ports.storage import SessionRepositoryPort


_VALID_ACTIONS = frozenset(["back", "home", "recent"])

# Android key mappings
_ANDROID_NAV = {
    "back": ["Back"],  # KEYCODE_BACK = 4
    "home": ["Home"],  # KEYCODE_HOME = 3
    "recent": ["187"],  # KEYCODE_APP_SWITCH = 187
}

# Linux key mappings
_LINUX_NAV = {
    "back": ["alt+Left"],
}


@dataclasses.dataclass(frozen=True)
class NavigateResult:
    """Result of a navigation operation."""

    status: str = "ok"
    warning: Optional[str] = None


class NavigateUseCase:
    """Platform-abstracted navigation: back, home, recent."""

    def __init__(
        self,
        input_port: InputPort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._input = input_port
        self._session_repo = session_repo

    def execute(self, session_id: str, action: str) -> NavigateResult:
        """Execute a navigation action."""
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"Unknown navigation action: {action!r}. "
                f"Must be one of: {sorted(_VALID_ACTIONS)}"
            )

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        backend = getattr(session, "backend", "linux")

        if backend == "android":
            keys = _ANDROID_NAV.get(action)
            if keys:
                self._input.key(session, keys)
            return NavigateResult(status="ok")
        else:
            # Linux backend
            keys = _LINUX_NAV.get(action)
            if keys:
                self._input.key(session, keys)
                return NavigateResult(
                    status="ok",
                    warning=(
                        "Alt+Left sent — works in browsers, "
                        "may not work in all desktop apps"
                    ),
                )
            else:
                # home and recent are not applicable on Linux desktop
                return NavigateResult(
                    status="ok",
                    warning=f"Navigate '{action}' is not applicable on Linux desktop",
                )
