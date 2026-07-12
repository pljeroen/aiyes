"""Eval use case — run operator JavaScript in a Firefox content context.

Pure domain logic: DEC-07 auto-return wrapping (a bare expression is wrapped as
``return (<script>);`` so it yields a value; a script that already contains a
``return`` statement is sent verbatim), the FR-06 backend/marionette guard, and
JS-error -> status='error' mapping. All socket/protocol I/O lives behind
MarionettePort (NFR-02 / C-PURITY).
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Optional

from aiyes.ports.marionette import MarionettePort
from aiyes.ports.storage import SessionRepositoryPort

# DEC-07 / C-EVALWRAP: a script that already carries a `return` statement is sent
# verbatim; otherwise the bare expression is wrapped so ExecuteScript yields a
# value. Word-boundary match so "returnValue"/"document.returned" do not falsely
# count as a return statement.
_RETURN_TOKEN = re.compile(r"\breturn\b")


@dataclasses.dataclass(frozen=True)
class EvalResult:
    """Result of an eval (content-context script execution) operation."""

    status: str
    session_id: str
    action: str = "eval"
    value: Any = None
    reason: Optional[str] = None


class EvalUseCase:
    """Execute operator JS in a linux firefox/marionette session's content context."""

    def __init__(
        self, marionette: MarionettePort, session_repo: SessionRepositoryPort
    ) -> None:
        self._marionette = marionette
        self._session_repo = session_repo

    def execute(self, session_id: str, script: str) -> EvalResult:
        """Run ``script`` and return its JSON value, or a mapped error.

        Guarded paths (non-linux backend or marionette-disabled session) return
        status='error' and perform ZERO MarionettePort I/O; an unknown session
        raises RuntimeError. A JS/webdriver exception maps to status='error'
        with the message in ``reason`` and value None (no crash).
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # FR-06 guard — inlined per A-R1 (no shared helper), mirroring goto.py.
        backend = getattr(session, "backend", "linux")
        if backend != "linux":
            return EvalResult(
                status="error",
                session_id=session_id,
                reason=(
                    "eval is a linux firefox/marionette primitive; "
                    f"not applicable to backend={backend!r}"
                ),
            )
        if getattr(session, "marionette_port", None) is None:
            return EvalResult(
                status="error",
                session_id=session_id,
                reason=(
                    "eval requires a marionette-enabled session; restart the "
                    "session with marionette enabled (session start --marionette)"
                ),
            )

        wrapped = script if _RETURN_TOKEN.search(script) else f"return ({script});"
        outcome = self._marionette.execute_script(session, wrapped)
        if not outcome.ok:
            return EvalResult(
                status="error",
                session_id=session_id,
                value=None,
                reason=outcome.error,
            )
        return EvalResult(
            status="ok",
            session_id=session_id,
            value=outcome.value,
        )
