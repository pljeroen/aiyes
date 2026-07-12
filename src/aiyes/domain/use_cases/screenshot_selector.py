"""Screenshot-selector use case — native element screenshot via Marionette.

Pure domain logic (DEC-09 / DEC-A7-01): MarionettePort.screenshot_element returns
a temp-file PATH; this use case then reuses the EXISTING ScreenshotStorePort
(save_screenshot + read_dimensions, the AIYES-111 path) exactly as ScreenshotUseCase
does — zero port change. A no-match selector (port returns None) -> status='error'
with no store write. All socket/protocol I/O lives behind MarionettePort
(NFR-02 / C-PURITY).
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.ports.marionette import MarionettePort
from aiyes.ports.screenshot_store import ScreenshotStorePort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class ScreenshotSelectorResult:
    """Result of a screenshot_selector operation."""

    status: str
    session_id: str
    selector: Optional[str] = None
    path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    reason: Optional[str] = None


class ScreenshotSelectorUseCase:
    """Capture and store a native screenshot of a CSS-selected element."""

    def __init__(
        self,
        marionette: MarionettePort,
        session_repo: SessionRepositoryPort,
        screenshot_store: ScreenshotStorePort,
    ) -> None:
        self._marionette = marionette
        self._session_repo = session_repo
        self._screenshot_store = screenshot_store

    def execute(self, session_id: str, selector: str) -> ScreenshotSelectorResult:
        """Capture the element, store the PNG, return path + width/height.

        Guarded paths return status='error' with ZERO MarionettePort I/O; an
        unknown session raises RuntimeError. A selector that matches nothing
        (port returns None) yields status='error' with NO store write (DEC-09).
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # FR-06 guard — inlined per A-R1 (no shared helper).
        backend = getattr(session, "backend", "linux")
        if backend != "linux":
            return ScreenshotSelectorResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=(
                    "screenshot_selector is a linux firefox/marionette primitive; "
                    f"not applicable to backend={backend!r}"
                ),
            )
        if getattr(session, "marionette_port", None) is None:
            return ScreenshotSelectorResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=(
                    "screenshot_selector requires a marionette-enabled session; "
                    "restart the session with marionette enabled "
                    "(session start --marionette)"
                ),
            )

        raw_path = self._marionette.screenshot_element(session, selector)
        if raw_path is None:
            return ScreenshotSelectorResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=f"No element matched selector {selector!r} (nothing to capture)",
            )

        stored_path = self._screenshot_store.save_screenshot(session_id, raw_path)
        dims = self._screenshot_store.read_dimensions(stored_path)
        width, height = (dims[0], dims[1]) if dims is not None else (None, None)
        return ScreenshotSelectorResult(
            status="ok",
            session_id=session_id,
            selector=selector,
            path=stored_path,
            width=width,
            height=height,
        )
