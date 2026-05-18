"""AIYES-96: scroll direction convention — name = view direction.

Pins the unified view-direction semantics across the scroll/scroll-like
surfaces:

  * R1 — AdbInputAdapter.mouse_scroll: "up" reveals content above →
    finger swipes DOWN (y2 > y1).
  * R2 — AdbGestureAdapter.two_finger_scroll: "up" → dy positive.
  * R5 — scroll_into_view's _swipe_coords_for_direction stays correct
    (non-regression: it already used view-direction).
  * R6 — XdotoolAdapter.mouse_scroll: button 4 = wheel-up = scroll up =
    reveal above. Was already view-direction; pinned here.

Per AIYES-96 INTEGRATION_MAP. RED before A9; GREEN after the polarity
flip in android_input_adapter and adb_gesture_adapter.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════
# R5 — _swipe_coords_for_direction non-regression (already correct)
# ═══════════════════════════════════════════════════════════════════════


from aiyes.adapters.scenario_use_case_executor import _swipe_coords_for_direction


@pytest.mark.parametrize(
    "direction,viewport,expected",
    [
        # viewport (1080, 2400): cx=540, cy=1200; dx=width//3=360,
        # dy=height//3=800.
        # "up" = reveal above = finger DOWN (y2 > y1)
        ("up", (1080, 2400), (540, 400, 540, 2000)),
        # "down" = reveal below = finger UP (y2 < y1)
        ("down", (1080, 2400), (540, 2000, 540, 400)),
        # "left" = reveal left = finger RIGHT (x2 > x1)
        ("left", (1080, 2400), (180, 1200, 900, 1200)),
        # "right" = reveal right = finger LEFT (x2 < x1)
        ("right", (1080, 2400), (900, 1200, 180, 1200)),
    ],
)
def test_swipe_coords_for_direction_uses_view_direction(
    direction: str, viewport: tuple[int, int], expected: tuple[int, int, int, int]
) -> None:
    """R5: For each named direction, _swipe_coords_for_direction returns a
    swipe whose finger moves OPPOSITE to the named direction (touch
    convention: drag content the opposite way to REVEAL content in the
    named direction). This pins the existing view-direction behaviour so
    future contracts don't accidentally break it while editing the other
    adapters.
    """
    assert _swipe_coords_for_direction(direction, viewport) == expected


# ═══════════════════════════════════════════════════════════════════════
# R6 — xdotool mouse_scroll uses view-direction (button 4 = wheel up)
# ═══════════════════════════════════════════════════════════════════════


def test_xdotool_mouse_scroll_button_mapping_is_view_direction() -> None:
    """R6: Linux xdotool mouse_scroll for direction='up' must issue
    `xdotool click 4` (button 4 = wheel-up event = viewport scrolls up =
    reveal content above). Pins the view-direction convention on Linux
    so AIYES-96's Android flip does not accidentally drift the Linux
    adapter into the opposite polarity.
    """
    if importlib.util.find_spec("aiyes.adapters.xdotool_adapter") is None:
        pytest.skip("xdotool_adapter not importable")

    from aiyes.adapters.xdotool_adapter import XdotoolAdapter

    adapter = XdotoolAdapter()
    session = SimpleNamespace(
        session_id="linux-test",
        app_pid=100,
        app_command="/usr/bin/firefox",
        app_args=(),
        name=None,
        display=":99",
        started_at=1000.0,
        backend="linux",
    )

    with patch("aiyes.adapters.xdotool_adapter.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        adapter.mouse_scroll(session, "up", 1)

    assert mock_run.call_count >= 1, (
        f"expected at least 1 xdotool call; got {mock_run.call_count}"
    )
    # All invocations must be xdotool click <btn>; for direction='up' the
    # button code is "4" (wheel-up event = scroll viewport up = reveal above).
    for call in mock_run.call_args_list:
        cmd = call[0][0]
        assert cmd[0] == "xdotool", (
            f"xdotool mouse_scroll must shell out to xdotool; got cmd[0]={cmd[0]!r}"
        )
        assert "click" in cmd, f"expected 'click' in xdotool command; got {cmd}"
        assert "4" in cmd, (
            f"R6: direction='up' must map to xdotool button '4' (wheel-up); got {cmd}"
        )


# ═══════════════════════════════════════════════════════════════════════
# R1 — mouse_scroll polarity: "up" → finger DOWN (y2 > y1)
# ═══════════════════════════════════════════════════════════════════════


def test_mouse_scroll_view_direction_up_finger_goes_down() -> None:
    """R1: AdbInputAdapter.mouse_scroll with direction='up' must emit an
    adb swipe whose end point is BELOW the start point (y2 > y1), i.e.
    the finger drags content DOWN to reveal content ABOVE.
    """
    from aiyes.adapters.android_input_adapter import AdbInputAdapter

    session = SimpleNamespace(device_serial="emulator-5554")
    adapter = AdbInputAdapter()

    with patch("aiyes.adapters.android_input_adapter.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        adapter.mouse_scroll(session, "up", 3)

    assert mock_run.call_count == 1, (
        f"expected exactly 1 subprocess.run call; got {mock_run.call_count}"
    )
    argv = mock_run.call_args[0][0]
    # argv tail: [..., input, swipe, x1, y1, x2, y2, duration_ms]
    _, _, x1, y1, x2, y2, _dur = argv[-7:]
    assert int(y2) > int(y1), (
        f"R1: direction='up' (view-direction) must produce y2 > y1 "
        f"(finger swipes DOWN to reveal content ABOVE); "
        f"got y1={y1}, y2={y2}"
    )


# ═══════════════════════════════════════════════════════════════════════
# R2 — two_finger_scroll polarity: "up" → dy positive (finger DOWN)
# ═══════════════════════════════════════════════════════════════════════


def test_two_finger_scroll_view_direction_up_finger_goes_down() -> None:
    """R2: AdbGestureAdapter.two_finger_scroll with direction='up' must
    emit an adb swipe whose dy is POSITIVE (finger moves DOWN, viewport
    reveals content above).
    """
    from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

    session = SimpleNamespace(device_serial="emulator-5554")
    adapter = AdbGestureAdapter()

    proc = MagicMock()
    proc.wait.return_value = 0
    proc.returncode = 0

    with patch(
        "aiyes.adapters.adb_gesture_adapter.subprocess.Popen", return_value=proc
    ) as mock_popen:
        adapter.two_finger_scroll(session, x=540, y=1200, direction="up", amount=2)

    assert mock_popen.call_count == 1, (
        f"expected exactly 1 Popen call; got {mock_popen.call_count}"
    )
    argv = mock_popen.call_args[0][0]
    _, _, x1, y1, x2, y2, _dur = argv[-7:]
    assert int(y2) > int(y1), (
        f"R2: two_finger_scroll direction='up' must produce y2 > y1 "
        f"(dy positive, finger DOWN); got y1={y1}, y2={y2}"
    )
