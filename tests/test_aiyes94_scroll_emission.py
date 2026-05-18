"""AIYES-94 — scroll emission contract tests.

Focused unit-test module for the AIYES-94 BUGFIX contract. Verifies that
both AndroidInputAdapter.mouse_scroll and AdbGestureAdapter.two_finger_scroll
emit the corrected adb invocation:

  * single adb shell input swipe (one Popen / one subprocess.run)
  * distance = amount * 400 in the requested direction
  * explicit duration_ms = 300 (controlled drag, not fling)
  * truthful docstring disclosing single-finger emulation + no multitouch
  * preserved public method signatures

These tests are RED before A9's implementation and must go GREEN after.

Requirements covered: AIYES-94 R1..R5. See
.tddv6/contracts/AIYES-94/VALIDATED_INTENT_PKG.yaml for the canonical text.
"""

from __future__ import annotations

import inspect
from typing import Any, Tuple
from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_android_session(**overrides: Any) -> Session:
    """Construct an Android session for testing."""
    defaults = dict(
        session_id="android-test",
        app_pid=0,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_linux_session(**overrides: Any) -> Session:
    """Construct a Linux session for the regression-guard test."""
    defaults = dict(
        session_id="linux-test",
        app_pid=100,
        app_command="/usr/bin/firefox",
        app_args=(),
        name=None,
        display=":99",
        started_at=1000.0,
        backend="linux",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _extract_swipe_tail(cmd: list) -> Tuple[str, str, str, str, str]:
    """Extract the 5 positional swipe args (x1, y1, x2, y2, duration_ms)
    from an adb command list of the form
    [adb, -s, <serial>, shell, input, swipe, x1, y1, x2, y2, duration_ms].
    """
    assert "swipe" in cmd, f"cmd missing 'swipe': {cmd}"
    swipe_idx = cmd.index("swipe")
    assert cmd[swipe_idx - 1] == "input", (
        f"'input' must immediately precede 'swipe': {cmd}"
    )
    tail = cmd[swipe_idx + 1 :]
    assert len(tail) == 5, (
        f"expected 5 positional args after 'swipe'; got {len(tail)}: {tail}"
    )
    return tuple(tail)  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════════
# R1 — mouse_scroll: distance = amount * 400, duration_ms = 300
# ═══════════════════════════════════════════════════════════════════════


class TestMouseScrollEmission:
    """AIYES-94 R1: mouse_scroll emits a single adb swipe with the correct
    distance-per-amount and explicit duration_ms."""

    @pytest.mark.parametrize("amount", [1, 3, 5])
    def test_mouse_scroll_uses_duration_300_and_distance_400_per_amount(
        self, amount: int
    ) -> None:
        """For each tested amount, mouse_scroll must:
        * issue exactly one subprocess.run invocation;
        * produce |y2 - y1| (or |x2 - x1|) == amount * 400;
        * pass duration_ms == '300' as the final argv element.
        """
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_input_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            adapter.mouse_scroll(session, "down", amount)

        assert mock_run.call_count == 1, (
            f"expected 1 subprocess.run call for amount={amount}; "
            f"got {mock_run.call_count}"
        )

        cmd = mock_run.call_args[0][0]
        x1, y1, x2, y2, duration_ms = _extract_swipe_tail(cmd)

        # Vertical scroll: x1 == x2; |y2 - y1| == amount * 400.
        assert x1 == x2, f"vertical scroll expected x1 == x2; got x1={x1!r}, x2={x2!r}"
        expected_distance = amount * 400
        actual_distance = abs(int(y2) - int(y1))
        assert actual_distance == expected_distance, (
            f"for amount={amount}: expected |y2-y1|={expected_distance}; "
            f"got {actual_distance} (y1={y1}, y2={y2})"
        )
        assert duration_ms == "300", f"duration_ms expected '300', got {duration_ms!r}"

    @pytest.mark.parametrize(
        "direction,amount,expected_dx,expected_dy",
        [
            ("up", 1, 0, -400),
            ("up", 3, 0, -1200),
            ("down", 1, 0, 400),
            ("down", 4, 0, 1600),
            ("left", 2, -800, 0),
            ("right", 2, 800, 0),
        ],
    )
    def test_mouse_scroll_directions(
        self,
        direction: str,
        amount: int,
        expected_dx: int,
        expected_dy: int,
    ) -> None:
        """For each direction, mouse_scroll emits a swipe from screen center
        (540, 960) to (540 + dx, 960 + dy) with dx/dy = amount * 400 in the
        requested axis. Catches sign and axis regressions (F-4)."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_input_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            adapter.mouse_scroll(session, direction, amount)

        cmd = mock_run.call_args[0][0]
        x1, y1, x2, y2, duration_ms = _extract_swipe_tail(cmd)

        assert x1 == "540" and y1 == "960", (
            f"start anchor expected (540, 960); got ({x1}, {y1})"
        )
        assert int(x2) - int(x1) == expected_dx, (
            f"for direction={direction!r} amount={amount}: "
            f"expected dx={expected_dx}; got {int(x2) - int(x1)}"
        )
        assert int(y2) - int(y1) == expected_dy, (
            f"for direction={direction!r} amount={amount}: "
            f"expected dy={expected_dy}; got {int(y2) - int(y1)}"
        )
        assert duration_ms == "300"


# ═══════════════════════════════════════════════════════════════════════
# R2 — two_finger_scroll: single anchored swipe, distance = amount * 400
# ═══════════════════════════════════════════════════════════════════════


class TestTwoFingerScrollEmission:
    """AIYES-94 R2: two_finger_scroll emits exactly ONE adb swipe anchored
    at (x, y), with distance = amount * 400 and duration_ms = 300."""

    def test_two_finger_scroll_invokes_single_swipe_with_anchor(self) -> None:
        """One Popen, anchor (540, 1200), amount=2 → (540,1200)→(540,400),
        duration_ms == '300'."""
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        adapter = AdbGestureAdapter()
        session = _make_android_session()

        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.wait.return_value = 0
            proc.returncode = 0
            mock_popen.return_value = proc

            adapter.two_finger_scroll(session, x=540, y=1200, direction="up", amount=2)

        # R2: exactly one swipe.
        assert mock_popen.call_count == 1, (
            f"expected 1 Popen call; got {mock_popen.call_count}"
        )

        argv = mock_popen.call_args[0][0]
        x1, y1, x2, y2, duration_ms = _extract_swipe_tail(argv)

        # Anchor at (540, 1200); direction=up → y decreases by amount*400=800.
        assert x1 == "540", f"x1 expected '540', got {x1!r}"
        assert y1 == "1200", f"y1 expected '1200', got {y1!r}"
        assert x2 == "540", f"x2 expected '540', got {x2!r}"
        assert y2 == "400", (
            f"y2 expected '400' (1200 - amount*400 = 1200 - 800); got {y2!r}"
        )
        assert duration_ms == "300", f"duration_ms expected '300', got {duration_ms!r}"

    @pytest.mark.parametrize(
        "direction,amount,expected_dx,expected_dy",
        [
            ("up", 1, 0, -400),
            ("up", 3, 0, -1200),
            ("down", 1, 0, 400),
            ("down", 4, 0, 1600),
            ("left", 2, -800, 0),
            ("right", 2, 800, 0),
        ],
    )
    def test_two_finger_scroll_directions(
        self,
        direction: str,
        amount: int,
        expected_dx: int,
        expected_dy: int,
    ) -> None:
        """For each direction/amount, dx and dy must match amount*400 in the
        requested axis."""
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        adapter = AdbGestureAdapter()
        session = _make_android_session()

        # Anchor far enough from edges that no axis goes negative.
        anchor_x, anchor_y = 2000, 2000

        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.wait.return_value = 0
            proc.returncode = 0
            mock_popen.return_value = proc

            adapter.two_finger_scroll(
                session, x=anchor_x, y=anchor_y, direction=direction, amount=amount
            )

        assert mock_popen.call_count == 1, (
            f"expected 1 Popen call for direction={direction!r}, "
            f"amount={amount}; got {mock_popen.call_count}"
        )

        argv = mock_popen.call_args[0][0]
        x1, y1, x2, y2, duration_ms = _extract_swipe_tail(argv)
        ix1, iy1, ix2, iy2 = int(x1), int(y1), int(x2), int(y2)

        # Anchor preserved as start point.
        assert (ix1, iy1) == (anchor_x, anchor_y), (
            f"start point expected ({anchor_x}, {anchor_y}); got ({ix1}, {iy1})"
        )
        # Delta matches expected_dx / expected_dy.
        actual_dx, actual_dy = ix2 - ix1, iy2 - iy1
        assert (actual_dx, actual_dy) == (expected_dx, expected_dy), (
            f"for direction={direction!r}, amount={amount}: expected "
            f"(dx, dy) = ({expected_dx}, {expected_dy}); got "
            f"({actual_dx}, {actual_dy})"
        )
        assert duration_ms == "300", f"duration_ms expected '300', got {duration_ms!r}"


# ═══════════════════════════════════════════════════════════════════════
# R3 — Honest docstrings: single-finger + no multitouch
# ═══════════════════════════════════════════════════════════════════════


def _docstring_discloses_single_finger_no_multitouch(doc: str) -> Tuple[bool, bool]:
    """Return (mentions_single, discloses_no_multitouch) for case-insensitive
    substring matching against the docstring."""
    low = doc.lower()
    mentions_single = "single" in low
    no_multitouch_variants = ("no multitouch", "not multitouch", "no multi-touch")
    discloses_no_multitouch = any(v in low for v in no_multitouch_variants)
    return mentions_single, discloses_no_multitouch


class TestDocstringDisclosure:
    """AIYES-94 R3: docstrings must truthfully describe single-finger
    emulation and explicitly disclose that no multitouch event is emitted."""

    def test_mouse_scroll_docstring_discloses_single_finger_no_multitouch(
        self,
    ) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        doc = inspect.getdoc(AdbInputAdapter.mouse_scroll)
        assert doc, "AdbInputAdapter.mouse_scroll docstring must be non-empty"
        mentions_single, discloses = _docstring_discloses_single_finger_no_multitouch(
            doc
        )
        assert mentions_single, (
            f"mouse_scroll docstring must mention 'single' (case-insensitive); "
            f"got: {doc!r}"
        )
        assert discloses, (
            f"mouse_scroll docstring must disclose 'no multitouch' / "
            f"'not multitouch' / 'no multi-touch'; got: {doc!r}"
        )

    def test_two_finger_scroll_docstring_discloses_single_finger_no_multitouch(
        self,
    ) -> None:
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        doc = inspect.getdoc(AdbGestureAdapter.two_finger_scroll)
        assert doc, "AdbGestureAdapter.two_finger_scroll docstring must be non-empty"
        mentions_single, discloses = _docstring_discloses_single_finger_no_multitouch(
            doc
        )
        assert mentions_single, (
            f"two_finger_scroll docstring must mention 'single' "
            f"(case-insensitive); got: {doc!r}"
        )
        assert discloses, (
            f"two_finger_scroll docstring must disclose 'no multitouch' / "
            f"'not multitouch' / 'no multi-touch'; got: {doc!r}"
        )


# ═══════════════════════════════════════════════════════════════════════
# R5 — Public method signatures preserved
# ═══════════════════════════════════════════════════════════════════════


class TestSignaturePreservation:
    """AIYES-94 R5: no caller may need to change."""

    def test_mouse_scroll_signature_preserved(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        sig = inspect.signature(AdbInputAdapter.mouse_scroll)
        params = list(sig.parameters)
        assert params == ["self", "session", "direction", "amount"], (
            f"AdbInputAdapter.mouse_scroll parameters must be "
            f"(self, session, direction, amount); got {params}"
        )

    def test_two_finger_scroll_signature_preserved(self) -> None:
        from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter

        sig = inspect.signature(AdbGestureAdapter.two_finger_scroll)
        params = list(sig.parameters)
        assert params == ["self", "session", "x", "y", "direction", "amount"], (
            f"AdbGestureAdapter.two_finger_scroll parameters must be "
            f"(self, session, x, y, direction, amount); got {params}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Part D — Regression guard: Linux mouse_scroll still uses xdotool
# ═══════════════════════════════════════════════════════════════════════


class TestLinuxMouseScrollRegressionGuard:
    """AIYES-94 cross-cutting: the Linux mouse_scroll path is unaffected by
    this contract — it must continue to use xdotool, not adb."""

    def test_linux_mouse_scroll_uses_xdotool(self) -> None:
        try:
            from aiyes.adapters.xdotool_adapter import XdotoolAdapter
        except ImportError:
            pytest.skip("XdotoolAdapter not importable in this environment")

        adapter = XdotoolAdapter()
        session = _make_linux_session()

        with patch("aiyes.adapters.xdotool_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.mouse_scroll(session, "down", 3)

        # At least one xdotool invocation, and every invocation must be
        # xdotool (NOT adb).
        assert mock_run.call_count >= 1, (
            f"expected at least 1 xdotool call; got {mock_run.call_count}"
        )
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert cmd[0] == "xdotool", (
                f"Linux mouse_scroll must shell out to xdotool, not "
                f"{cmd[0]!r}; full cmd: {cmd}"
            )
            # xdotool scroll uses 'click' with button 4/5/6/7.
            assert "click" in cmd, f"expected 'click' in xdotool command; got {cmd}"
            # button 5 = down scroll
            assert "5" in cmd, (
                f"expected button '5' (down scroll) in command; got {cmd}"
            )
