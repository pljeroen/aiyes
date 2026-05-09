"""XdotoolAdapter — implements InputPort via xdotool subprocess.

All calls set DISPLAY by injecting it into the subprocess environment dict
passed to subprocess.run(env=...). The display value is never passed as a
command-line argument to xdotool.
"""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional


def _extract_display(session_or_display) -> str:
    """Extract display string from Session or plain string."""
    if isinstance(session_or_display, str):
        return session_or_display
    return session_or_display.display


class XdotoolAdapter:
    """Mouse and keyboard input via xdotool subprocess."""

    def _run(self, display: str, args: List[str]) -> None:
        """Run xdotool with DISPLAY set in subprocess env."""
        cmd = ["xdotool"] + args
        env = dict(os.environ)
        env["DISPLAY"] = display
        subprocess.run(cmd, check=True, env=env)

    def mouse_move(self, session, x: int, y: int) -> None:
        """Move mouse to (x, y)."""
        self._run(_extract_display(session), ["mousemove", str(x), str(y)])

    def mouse_click(
        self,
        session,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
    ) -> None:
        """Click at position or current position."""
        display = _extract_display(session)
        button_map = {"left": "1", "middle": "2", "right": "3"}
        btn = button_map.get(button, "1")

        if x is not None and y is not None:
            self._run(display, ["mousemove", str(x), str(y), "click", btn])
        else:
            self._run(display, ["click", btn])

    def mouse_drag(self, session, x1: int, y1: int, x2: int, y2: int) -> None:
        """Drag from (x1, y1) to (x2, y2)."""
        self._run(
            _extract_display(session),
            [
                "mousemove",
                str(x1),
                str(y1),
                "mousedown",
                "1",
                "mousemove",
                str(x2),
                str(y2),
                "mouseup",
                "1",
            ],
        )

    def mouse_scroll(self, session, direction: str, amount: int = 3) -> None:
        """Scroll in direction by amount."""
        display = _extract_display(session)
        # xdotool uses click with button 4/5 for up/down scroll
        direction_map = {"up": "4", "down": "5", "left": "6", "right": "7"}
        btn = direction_map.get(direction, "5")
        for _ in range(amount):
            self._run(display, ["click", btn])

    def _resolve_window_id(self, session, display: str) -> Optional[str]:
        """Resolve the app window ID from session.app_pid via xdotool search.

        Returns window ID string if found, None otherwise.
        Only attempts resolution when session has an app_pid attribute.
        """
        app_pid = getattr(session, "app_pid", None)
        if app_pid is None or app_pid == 0:
            return None

        try:
            env = dict(os.environ)
            env["DISPLAY"] = display
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--pid", str(app_pid)],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            stdout = result.stdout.strip()
            if not stdout:
                return None
            # Take the first window ID
            return stdout.splitlines()[0].strip()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return None

    def key(self, session, key_specs: List[str]) -> None:
        """Send key events, targeting the app window when possible."""
        display = _extract_display(session)
        wid = self._resolve_window_id(session, display)
        if wid:
            # Focus the window first — Xvfb has no window manager,
            # so without explicit focus keys go to the root window
            try:
                self._run(display, ["windowfocus", "--sync", wid])
            except subprocess.CalledProcessError:
                pass  # Best-effort focus
        for spec in key_specs:
            if wid:
                self._run(display, ["key", "--window", wid, spec])
            else:
                self._run(display, ["key", spec])

    def type_text(self, session, text: str, delay_ms: int = 0) -> None:
        """Type text character by character.

        When *delay_ms* > 0, passes ``--delay <ms>`` to xdotool so it
        inserts a pause between each keystroke.
        """
        args = ["type"]
        if delay_ms > 0:
            args += ["--delay", str(delay_ms)]
        args += ["--", text]
        self._run(_extract_display(session), args)
