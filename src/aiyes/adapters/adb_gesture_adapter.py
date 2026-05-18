"""AdbGestureAdapter — implements GesturePort via adb shell input.

Two-finger scroll is emulated as a single-finger drag because
`adb shell input` is a single-pointer API and a single drag is
what Flutter/Android scroll recognizers actually consume. Pinch
still uses two concurrent `adb shell input swipe` processes as a
best-effort approximation; reliability on Flutter is not
guaranteed and is tracked as a follow-up to AIYES-94.
"""

from __future__ import annotations

import subprocess


def _get_serial(session) -> str:
    """Extract device_serial from session."""
    serial = session.device_serial
    if not serial:
        raise RuntimeError(
            "Android session has no device_serial — cannot send gestures"
        )
    return serial


def _adb_swipe(
    serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int
) -> subprocess.Popen:
    """Start an adb shell input swipe as a non-blocking process."""
    from aiyes.adapters.adb_path import resolve_adb_path

    cmd = [
        resolve_adb_path(),
        "-s",
        serial,
        "shell",
        "input",
        "swipe",
        str(x1),
        str(y1),
        str(x2),
        str(y2),
        str(duration_ms),
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class AdbGestureAdapter:
    """Restricted gestures via concurrent adb shell input swipe commands."""

    def pinch(self, session, x: int, y: int, scale_factor: float) -> None:
        """Pinch gesture via two concurrent swipes.

        scale_factor > 1.0 = zoom in (fingers move apart)
        scale_factor < 1.0 = zoom out (fingers move together)
        """
        serial = _get_serial(session)

        # Calculate finger positions
        offset = 100  # starting finger offset from center
        if scale_factor > 1.0:
            # Zoom in: fingers start close, move apart
            factor = min(scale_factor, 5.0)
            end_offset = int(offset * factor)
            start_offset = offset
        else:
            # Zoom out: fingers start far, move together
            factor = max(scale_factor, 0.1)
            start_offset = int(offset / factor)
            end_offset = offset

        duration_ms = 500

        # Finger 1: left side
        p1 = _adb_swipe(
            serial,
            x - start_offset,
            y,
            x - end_offset,
            y,
            duration_ms,
        )
        # Finger 2: right side
        p2 = _adb_swipe(
            serial,
            x + start_offset,
            y,
            x + end_offset,
            y,
            duration_ms,
        )

        # Wait for both to complete, ensuring cleanup on failure
        try:
            p1.wait(timeout=10)
            p2.wait(timeout=10)
        except BaseException:
            p1.kill()
            p2.kill()
            p1.wait()
            p2.wait()
            raise

        if p1.returncode != 0:
            raise RuntimeError(f"Pinch swipe 1 failed (rc={p1.returncode})")
        if p2.returncode != 0:
            raise RuntimeError(f"Pinch swipe 2 failed (rc={p2.returncode})")

    def two_finger_scroll(
        self, session, x: int, y: int, direction: str, amount: int = 3
    ) -> None:
        """Single-finger drag emulating a two-finger scroll; no multitouch event is emitted.

        Two-finger semantics are emulated with a single adb input swipe
        anchored at (x, y) because `adb shell input` is a single-pointer
        API. This produces the same observable viewport motion in apps
        whose scroll recognizers accept single-pointer drags (Flutter,
        most native Android views).

        Direction convention: the named direction is where the *viewport*
        scrolls (which content is revealed). The emitted finger swipe is
        the inverse -- e.g., ``direction='up'`` reveals content above and
        is emitted as a finger swipe downward. This matches mouse-wheel
        UIs, touch-UI naming norms (iOS HIG / Material Design), and the
        project's scroll_into_view step.
        """
        serial = _get_serial(session)
        distance = amount * 400
        duration_ms = 300

        direction_offsets = {
            # view-direction: named "up" reveals content above -> finger DOWN
            "up": (0, distance),
            "down": (0, -distance),
            "left": (distance, 0),
            "right": (-distance, 0),
        }
        dx, dy = direction_offsets.get(direction, (0, distance))

        process = _adb_swipe(serial, x, y, x + dx, y + dy, duration_ms)
        try:
            process.wait(timeout=10)
        except BaseException:
            process.kill()
            process.wait()
            raise

        if process.returncode != 0:
            raise RuntimeError(
                f"two_finger_scroll swipe failed (rc={process.returncode})"
            )

    def swipe(
        self,
        session,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> None:
        """Single-finger swipe from (x1, y1) to (x2, y2) over duration_ms.

        This is the natural Android list-scroll primitive — equivalent to
        UiScrollable.scrollIntoView()'s underlying gesture. Distinct from
        two_finger_scroll above only in API naming: two_finger_scroll is
        also a single-finger adb emulation (the "two-finger" label is
        preserved for caller compatibility; no multitouch event is
        emitted by either method).
        """
        serial = _get_serial(session)
        process = _adb_swipe(serial, x1, y1, x2, y2, duration_ms)
        try:
            process.wait(timeout=10 + duration_ms / 1000.0)
        except BaseException:
            process.kill()
            process.wait()
            raise
        if process.returncode != 0:
            raise RuntimeError(f"swipe failed (rc={process.returncode})")
