"""Page-text use case — read rendered prose from a Firefox content session.

Pure domain logic (DEC-08): document.body.innerText by default, or the scoped
innerText of a CSS selector with a ``found`` flag (found=False + text='' when the
selector matches nothing — still status='ok'). All socket/protocol I/O lives
behind MarionettePort (NFR-02 / C-PURITY).
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.ports.marionette import MarionettePort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class PageTextResult:
    """Result of a page_text operation."""

    status: str
    session_id: str
    selector: Optional[str] = None
    text: str = ""
    found: bool = False
    reason: Optional[str] = None


class PageTextUseCase:
    """Read document.body.innerText, or a scoped element's innerText."""

    def __init__(
        self, marionette: MarionettePort, session_repo: SessionRepositoryPort
    ) -> None:
        self._marionette = marionette
        self._session_repo = session_repo

    def execute(
        self, session_id: str, selector: Optional[str] = None
    ) -> PageTextResult:
        """Return the page (or scoped) innerText and a found flag.

        Guarded paths return status='error' with ZERO MarionettePort I/O; an
        unknown session raises RuntimeError. A given selector that matches
        nothing yields found=False, text='' (still status='ok').
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # FR-06 guard — inlined per A-R1 (no shared helper).
        backend = getattr(session, "backend", "linux")
        if backend != "linux":
            return PageTextResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=(
                    "page_text is a linux firefox/marionette primitive; "
                    f"not applicable to backend={backend!r}"
                ),
            )
        if getattr(session, "marionette_port", None) is None:
            return PageTextResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=(
                    "page_text requires a marionette-enabled session; restart "
                    "the session with marionette enabled "
                    "(session start --marionette)"
                ),
            )

        outcome = self._marionette.execute_script(
            session, _build_page_text_script(), [selector]
        )
        if not outcome.ok:
            return PageTextResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=outcome.error,
            )

        value = outcome.value if isinstance(outcome.value, dict) else {}
        text = value.get("text")
        return PageTextResult(
            status="ok",
            session_id=session_id,
            selector=selector,
            text=text if isinstance(text, str) else "",
            found=bool(value.get("found")),
        )


def _build_page_text_script() -> str:
    """Build the content-context JS returning {text, found}.

    arguments[0] is the optional CSS selector: null/undefined -> whole-page
    body.innerText (found=true); a selector -> the matched element's innerText
    (found=true) or {text:'', found:false} when nothing matches.
    """
    return (
        "var sel = arguments[0];"
        "if (sel === null || sel === undefined) {"
        "  return {text: (document.body ? document.body.innerText : ''),"
        "          found: true};"
        "}"
        "var el = document.querySelector(sel);"
        "if (!el) { return {text: '', found: false}; }"
        "return {text: (el.innerText || ''), found: true};"
    )
