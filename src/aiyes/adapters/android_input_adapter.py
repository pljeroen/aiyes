"""AdbInputAdapter — implements InputPort via adb shell input commands.

All input operations use `adb -s {serial} shell input ...`.
Uses only stdlib: subprocess.
"""

from __future__ import annotations

import subprocess
import time
from typing import Dict, List, Optional

# Common key name to Android keycode mapping.
# https://developer.android.com/reference/android/view/KeyEvent
KEY_NAME_TO_KEYCODE: Dict[str, int] = {
    "Return": 66,
    "Enter": 66,
    "Escape": 111,
    "Tab": 61,
    "BackSpace": 67,
    "Delete": 112,
    "space": 62,
    "Home": 3,
    "End": 123,
    "Page_Up": 92,
    "Page_Down": 93,
    "Up": 19,
    "Down": 20,
    "Left": 21,
    "Right": 22,
    "Back": 4,
    "Menu": 82,
    "Search": 84,
    "VolumeUp": 24,
    "VolumeDown": 25,
    "Power": 26,
    "F1": 131,
    "F2": 132,
    "F3": 133,
    "F4": 134,
    "F5": 135,
    "F6": 136,
    "F7": 137,
    "F8": 138,
    "F9": 139,
    "F10": 140,
    "F11": 141,
    "F12": 142,
    "a": 29,
    "b": 30,
    "c": 31,
    "d": 32,
    "e": 33,
    "f": 34,
    "g": 35,
    "h": 36,
    "i": 37,
    "j": 38,
    "k": 39,
    "l": 40,
    "m": 41,
    "n": 42,
    "o": 43,
    "p": 44,
    "q": 45,
    "r": 46,
    "s": 47,
    "t": 48,
    "u": 49,
    "v": 50,
    "w": 51,
    "x": 52,
    "y": 53,
    "z": 54,
    "0": 7,
    "1": 8,
    "2": 9,
    "3": 10,
    "4": 11,
    "5": 12,
    "6": 13,
    "7": 14,
    "8": 15,
    "9": 16,
}


# Re-export from shared module for backward compatibility.
from aiyes.adapters.adb_text import escape_text_for_adb as escape_text_for_adb  # noqa: F401


def _get_serial(session) -> str:
    """Extract device_serial from session."""
    serial = session.device_serial
    if not serial:
        raise RuntimeError("Android session has no device_serial — cannot send input")
    return serial


def _run_adb(serial: str, args: List[str]) -> None:
    """Run an adb command targeting a specific device."""
    from aiyes.adapters.adb_path import resolve_adb_path

    cmd = [resolve_adb_path(), "-s", serial, "shell"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError("adb not found on PATH. Install Android SDK platform-tools.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"adb input command timed out for device {serial}")

    if result.returncode != 0:
        raise RuntimeError(
            f"adb input failed (rc={result.returncode}): {result.stderr.strip()}"
        )


class AdbInputAdapter:
    """Mouse and keyboard input via adb shell input commands."""

    def mouse_move(self, session, x: int, y: int) -> None:
        """Move mouse to (x, y) — no-op on Android (no hover support)."""
        # Android touch screens have no cursor; move is a no-op.
        pass

    def mouse_click(
        self,
        session,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
    ) -> None:
        """Tap at position (x, y) via adb shell input tap."""
        serial = _get_serial(session)
        if x is not None and y is not None:
            _run_adb(serial, ["input", "tap", str(x), str(y)])
        else:
            # No coordinates: tap center of screen as fallback
            _run_adb(serial, ["input", "tap", "540", "960"])

    def mouse_drag(self, session, x1: int, y1: int, x2: int, y2: int) -> None:
        """Swipe from (x1,y1) to (x2,y2) via adb shell input swipe."""
        serial = _get_serial(session)
        _run_adb(
            serial,
            ["input", "swipe", str(x1), str(y1), str(x2), str(y2)],
        )

    def mouse_scroll(self, session, direction: str, amount: int = 3) -> None:
        """Scroll via a single-finger adb input swipe; no multitouch event is emitted.

        Issues ``adb shell input swipe x1 y1 x2 y2 300`` from screen center
        with distance proportional to ``amount`` (400 px per unit). The
        explicit 300 ms duration keeps Flutter's Scrollable from
        classifying the gesture as a fling.

        Direction convention: the named direction is where the *viewport*
        scrolls (which content is revealed). The emitted finger swipe is
        the inverse -- e.g., ``direction='up'`` reveals content above and
        is emitted as a finger swipe downward. This matches mouse-wheel
        UIs, touch-UI naming norms (iOS HIG / Material Design), and the
        project's scroll_into_view step.
        """
        duration_ms = 300
        serial = _get_serial(session)
        # Swipe from center; distance proportional to amount
        cx, cy = 540, 960
        distance = amount * 400

        direction_offsets = {
            # "up" = reveal content ABOVE -> finger swipes DOWN (y2 > y1)
            "up": (cx, cy, cx, cy + distance),
            # "down" = reveal content BELOW -> finger swipes UP (y2 < y1)
            "down": (cx, cy, cx, cy - distance),
            # "left" = reveal content LEFT -> finger swipes RIGHT (x2 > x1)
            "left": (cx, cy, cx + distance, cy),
            # "right" = reveal content RIGHT -> finger swipes LEFT (x2 < x1)
            "right": (cx, cy, cx - distance, cy),
        }

        coords = direction_offsets.get(direction, direction_offsets["down"])
        _run_adb(
            serial,
            [
                "input",
                "swipe",
                str(coords[0]),
                str(coords[1]),
                str(coords[2]),
                str(coords[3]),
                str(duration_ms),
            ],
        )

    def key(self, session, key_specs: List[str]) -> None:
        """Send key events via adb shell input keyevent."""
        serial = _get_serial(session)
        for spec in key_specs:
            keycode = KEY_NAME_TO_KEYCODE.get(spec)
            if keycode is not None:
                _run_adb(serial, ["input", "keyevent", str(keycode)])
            else:
                # Try as raw keycode number
                try:
                    code = int(spec)
                    _run_adb(serial, ["input", "keyevent", str(code)])
                except ValueError:
                    raise RuntimeError(
                        f"Unknown key spec for Android: {spec!r}. "
                        f"Known keys: {sorted(KEY_NAME_TO_KEYCODE.keys())}"
                    )

    # Default inter-character delay (ms) when caller does not specify one.
    # Android's IME drops characters when they arrive faster than the input
    # queue can process.  20 ms is enough for emulators and most devices.
    _DEFAULT_DELAY_MS: int = 20

    def type_text(self, session, text: str, delay_ms: int = 0) -> None:
        """Type text via adb shell input text.

        Text is always sent one character at a time with an inter-character
        delay to prevent Android's input system from dropping characters.

        When *delay_ms* is 0 (the default) a built-in default of 20 ms is
        used.  Pass an explicit *delay_ms* > 0 to override.
        """

        serial = _get_serial(session)
        if not text:
            return

        effective_delay = delay_ms if delay_ms > 0 else self._DEFAULT_DELAY_MS
        delay_sec = effective_delay / 1000
        for i, char in enumerate(text):
            escaped = escape_text_for_adb(char)
            _run_adb(serial, ["input", "text", escaped])
            if i < len(text) - 1:
                time.sleep(delay_sec)
