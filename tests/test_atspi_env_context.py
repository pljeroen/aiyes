"""Tests for xdotool mouse_click atomicity.

Contract: AIYES-05 (CT-03 BUGFIX)
Requirements covered:
  R-FIX-04: xdotool mouse_click atomicity — single _run call

Note: R-FIX-01/R-FIX-02/R-FIX-03 (_AtspiEnvContext tests) were removed
by AIYES-09. The _AtspiEnvContext class was replaced with subprocess
isolation — see tests/test_aiyes09_subprocess_isolation.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-04: xdotool mouse_click atomicity
# ═══════════════════════════════════════════════════════════════════════


class TestXdotoolMouseClickAtomicity:
    """R-FIX-04: mouse_click must issue a single atomic _run call."""

    def test_mouse_click_with_coords_exactly_one_run_call(self) -> None:
        # R-FIX-04: mouse_click(display, x, y, button): exactly one _run call
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.mouse_click(":99", 100, 200, "left")

            assert mock_run.call_count == 1, (
                f"Expected exactly 1 subprocess.run call, got {mock_run.call_count}"
            )

    def test_mouse_click_with_coords_contains_mousemove_and_click(self) -> None:
        # R-FIX-04: that call's args contain mousemove, str(x), str(y), click, btn
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.mouse_click(":99", 100, 200, "left")

            args = mock_run.call_args[0][0]
            # Expected: ["xdotool", "--display", ":99", "mousemove", "100", "200", "click", "1"]
            assert "mousemove" in args, f"'mousemove' not in args: {args}"
            assert "100" in args, f"'100' not in args: {args}"
            assert "200" in args, f"'200' not in args: {args}"
            assert "click" in args, f"'click' not in args: {args}"
            assert "1" in args, f"'1' (button) not in args: {args}"

            # Verify ordering: mousemove before click in the same call
            mm_idx = args.index("mousemove")
            cl_idx = args.index("click")
            assert mm_idx < cl_idx, (
                f"mousemove (idx={mm_idx}) must precede click (idx={cl_idx}) in args: {args}"
            )

    def test_mouse_click_without_coords_one_run_call(self) -> None:
        # R-FIX-04: mouse_click(display, None, None, button): one _run call
        from aiyes.adapters.xdotool_adapter import XdotoolAdapter

        adapter = XdotoolAdapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.mouse_click(":99", None, None, "right")

            assert mock_run.call_count == 1, (
                f"Expected exactly 1 subprocess.run call, got {mock_run.call_count}"
            )
            args = mock_run.call_args[0][0]
            assert "click" in args, f"'click' not in args: {args}"
            assert "3" in args, f"'3' (right button) not in args: {args}"
            assert "mousemove" not in args, (
                f"'mousemove' should not be in args when coords are None: {args}"
            )
