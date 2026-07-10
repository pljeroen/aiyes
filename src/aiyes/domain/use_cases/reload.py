"""Reload use case — cache-bypassing hard reload of a linux browser session.

Linux/AT-SPI+xdotool only. Emits exactly one InputPort.key(["ctrl+shift+r"])
(the browser hard-reload / cache-bypass sequence). On a non-linux backend the
use case returns a status="error" result and emits ZERO keystrokes.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.ports.input import InputPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class ReloadResult:
    """Result of a reload (hard / cache-bypass) operation."""

    status: str
    session_id: str
    action: str = "reload"
    reason: Optional[str] = None


class ReloadUseCase:
    """Cache-bypassing hard reload of a linux browser session (ctrl+shift+r)."""

    def __init__(
        self,
        input_port: InputPort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._input = input_port
        self._session_repo = session_repo

    def execute(self, session_id: str) -> ReloadResult:
        """Hard-reload the current page in the resolved session.

        Returns ReloadResult(status="ok") on success, or status="error" with a
        reason for a non-linux backend (zero keystrokes emitted). Raises
        RuntimeError on a system error (session not found), matching every
        sibling use case.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        backend = getattr(session, "backend", "linux")
        if backend != "linux":
            return ReloadResult(
                status="error",
                session_id=session_id,
                action="reload",
                reason=(
                    "reload is a linux browser-session primitive; "
                    f"not applicable to backend={backend!r}"
                ),
            )

        self._input.key(session, ["ctrl+shift+r"])

        return ReloadResult(
            status="ok",
            session_id=session_id,
            action="reload",
        )
