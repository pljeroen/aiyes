"""Marionette port — Protocol for driving a Firefox/Marionette content session.

A single low-level port (DEC-02): the four DOM-lens use-cases build their own JS
and shape their own results directly against this surface — no facade layer. The
sole implementation is MarionetteAdapter, which owns ALL socket/protocol/base64
I/O (HC-03 / NFR-02). Everything below the port boundary (framing, msgId
correlation, WebDriver session lifecycle) is invisible to the domain.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from aiyes.domain.marionette_outcome import MarionetteScriptOutcome


class MarionettePort(Protocol):
    """Structural port for content-context script execution + element capture."""

    def execute_script(
        self, session: Any, script: str, args: Any = None
    ) -> MarionetteScriptOutcome:
        """Run ``script`` in the session's CONTENT context (never chrome).

        Returns a MarionetteScriptOutcome: ok=True with the JSON value on
        success, or ok=False carrying the mapped webdriver/JS error message.
        Raises (does not swallow) on a dead/closed transport — a system error.
        """
        ...

    def screenshot_element(self, session: Any, css_selector: str) -> Optional[str]:
        """Capture a native screenshot of the first element matching ``css_selector``.

        Scrolls the element into view, writes the PNG bytes to a temp file, and
        returns that file PATH (DEC-A7-01). Returns None when the selector
        matches nothing / capture is unavailable.
        """
        ...
