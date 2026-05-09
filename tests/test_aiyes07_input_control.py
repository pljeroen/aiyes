"""Tests for AIYES-07 (CT-03 BUGFIX) — xdotool --display flag broken.

Verified root cause: xdotool 3.20211022.1 does NOT support --display flag.
Fix: use DISPLAY env var in subprocess.run(env=...) instead.

Requirements covered:
  R-FIX-01: _run() must use DISPLAY env var, not --display flag
  R-FIX-02: All 6 public methods work with env-based display
  R-FIX-03: Regression guards (existing --display tests inverted)
  R-FIX-04: ScrotAdapter subprocess env must include PATH
  R-FIX-05: Mouse/keyboard edge case coverage
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aiyes.adapters.xdotool_adapter import XdotoolAdapter


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_adapter() -> XdotoolAdapter:
    return XdotoolAdapter()


def _mock_run_ok() -> MagicMock:
    m = MagicMock(returncode=0)
    return m


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-01: _run() must use DISPLAY env var, not --display flag
# ═══════════════════════════════════════════════════════════════════════


class TestRunUsesDisplayEnvVar:
    """R-FIX-01: subprocess.run must receive env with DISPLAY, no --display in cmd."""

    def test_run_does_not_use_display_flag(self) -> None:
        """Command list must NOT contain '--display'."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_move(":99", 100, 200)

            cmd = mock_run.call_args[0][0]
            assert "--display" not in cmd, (
                f"'--display' must not appear in command args: {cmd}"
            )

    def test_run_passes_display_as_env(self) -> None:
        """subprocess.run must receive env dict with DISPLAY == display param."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_move(":99", 100, 200)

            kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
            # Also check positional keyword-style
            if "env" not in kwargs:
                kwargs = mock_run.call_args.kwargs
            assert "env" in kwargs, (
                f"subprocess.run must be called with env= kwarg, got: {mock_run.call_args}"
            )
            env = kwargs["env"]
            assert env.get("DISPLAY") == ":99", (
                f"env['DISPLAY'] must be ':99', got: {env.get('DISPLAY')!r}"
            )

    def test_run_env_includes_path(self) -> None:
        """subprocess.run env must propagate PATH with exact value from os.environ."""
        adapter = _make_adapter()
        fake_env = {"PATH": "/custom/bin:/usr/bin", "HOME": "/home/test"}
        with (
            patch("subprocess.run") as mock_run,
            patch("os.environ", fake_env),
        ):
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_move(":99", 100, 200)

            kwargs = mock_run.call_args.kwargs
            assert "env" in kwargs, "subprocess.run must be called with env="
            env = kwargs["env"]
            assert env.get("PATH") == "/custom/bin:/usr/bin", (
                f"env['PATH'] must be '/custom/bin:/usr/bin', got: {env.get('PATH')!r}"
            )

    def test_run_env_display_matches_parameter(self) -> None:
        """DISPLAY in env must match the display parameter exactly."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_move(":55", 0, 0)

            env = mock_run.call_args.kwargs["env"]
            assert env["DISPLAY"] == ":55"


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-02: All 6 public methods must work after fix
# ═══════════════════════════════════════════════════════════════════════


class TestAllMethodsUseEnvDisplay:
    """R-FIX-02: parametrized test across all 6 methods — no --display, env DISPLAY set."""

    @pytest.mark.parametrize(
        "method_name, args",
        [
            ("mouse_move", (":99", 100, 200)),
            ("mouse_click", (":99", 50, 60, "left")),
            ("mouse_drag", (":99", 10, 20, 100, 200)),
            ("mouse_scroll", (":99", "down", 1)),
            ("key", (":99", ["Return"])),
            ("type_text", (":99", "hello")),
        ],
        ids=[
            "mouse_move",
            "mouse_click",
            "mouse_drag",
            "mouse_scroll",
            "key",
            "type_text",
        ],
    )
    def test_method_does_not_use_display_flag(
        self, method_name: str, args: tuple
    ) -> None:
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            getattr(adapter, method_name)(*args)

            for call in mock_run.call_args_list:
                cmd = call[0][0]
                assert "--display" not in cmd, (
                    f"{method_name}: '--display' must not appear in cmd: {cmd}"
                )

    @pytest.mark.parametrize(
        "method_name, args",
        [
            ("mouse_move", (":99", 100, 200)),
            ("mouse_click", (":99", 50, 60, "left")),
            ("mouse_drag", (":99", 10, 20, 100, 200)),
            ("mouse_scroll", (":99", "down", 1)),
            ("key", (":99", ["Return"])),
            ("type_text", (":99", "hello")),
        ],
        ids=[
            "mouse_move",
            "mouse_click",
            "mouse_drag",
            "mouse_scroll",
            "key",
            "type_text",
        ],
    )
    def test_method_passes_display_env(self, method_name: str, args: tuple) -> None:
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            getattr(adapter, method_name)(*args)

            for call in mock_run.call_args_list:
                kwargs = call.kwargs
                assert "env" in kwargs, (
                    f"{method_name}: subprocess.run must receive env= kwarg"
                )
                assert kwargs["env"].get("DISPLAY") == ":99", (
                    f"{method_name}: env['DISPLAY'] must be ':99', "
                    f"got: {kwargs['env'].get('DISPLAY')!r}"
                )

    @pytest.mark.parametrize(
        "method_name, args",
        [
            ("mouse_move", (":99", 100, 200)),
            ("mouse_click", (":99", 50, 60, "left")),
            ("mouse_drag", (":99", 10, 20, 100, 200)),
            ("mouse_scroll", (":99", "down", 1)),
            ("key", (":99", ["Return"])),
            ("type_text", (":99", "hello")),
        ],
        ids=[
            "mouse_move",
            "mouse_click",
            "mouse_drag",
            "mouse_scroll",
            "key",
            "type_text",
        ],
    )
    def test_method_env_includes_path(self, method_name: str, args: tuple) -> None:
        adapter = _make_adapter()
        fake_env = {"PATH": "/custom/bin:/usr/bin", "HOME": "/home/test"}
        with (
            patch("subprocess.run") as mock_run,
            patch("os.environ", fake_env),
        ):
            mock_run.return_value = _mock_run_ok()
            getattr(adapter, method_name)(*args)

            for call in mock_run.call_args_list:
                env = call.kwargs["env"]
                assert env.get("PATH") == "/custom/bin:/usr/bin", (
                    f"{method_name}: env['PATH'] must be '/custom/bin:/usr/bin', "
                    f"got: {env.get('PATH')!r}"
                )


class TestMouseClickAtomicityPreserved:
    """R-FIX-02: mouse_click atomicity from AIYES-05 must survive the fix."""

    def test_mouse_click_with_coords_exactly_one_subprocess_call(self) -> None:
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_click(":99", 100, 200, "left")

            assert mock_run.call_count == 1, (
                f"mouse_click with coords must issue exactly 1 subprocess.run call, "
                f"got {mock_run.call_count}"
            )

    def test_mouse_click_with_coords_single_call_contains_move_and_click(self) -> None:
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_click(":99", 100, 200, "left")

            cmd = mock_run.call_args[0][0]
            assert "mousemove" in cmd
            assert "click" in cmd
            mm_idx = cmd.index("mousemove")
            cl_idx = cmd.index("click")
            assert mm_idx < cl_idx, (
                f"mousemove (idx={mm_idx}) must precede click (idx={cl_idx}) in: {cmd}"
            )


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-04: ScrotAdapter subprocess env must include PATH
# ═══════════════════════════════════════════════════════════════════════


class TestScrotAdapterEnv:
    """R-FIX-04: ScrotAdapter.take() must pass env with both DISPLAY and PATH."""

    def test_scrot_subprocess_env_includes_path(self) -> None:
        from aiyes.adapters.scrot_adapter import ScrotAdapter

        adapter = ScrotAdapter()
        fake_environ = {"PATH": "/custom/scrot/bin:/usr/bin", "HOME": "/home/test"}
        with (
            patch("subprocess.run") as mock_run,
            patch("shutil.which", return_value="/usr/bin/scrot"),
            patch.dict("os.environ", fake_environ, clear=True),
        ):
            mock_run.return_value = _mock_run_ok()
            adapter.take(":99", "/tmp/test.png")

            kwargs = mock_run.call_args.kwargs
            assert "env" in kwargs, "subprocess.run must be called with env="
            env = kwargs["env"]
            assert env.get("PATH") == "/custom/scrot/bin:/usr/bin", (
                f"ScrotAdapter env PATH must be preserved from os.environ, got: {env.get('PATH')!r}"
            )

    def test_scrot_subprocess_env_includes_display(self) -> None:
        from aiyes.adapters.scrot_adapter import ScrotAdapter

        adapter = ScrotAdapter()
        with (
            patch("subprocess.run") as mock_run,
            patch("shutil.which", return_value="/usr/bin/scrot"),
        ):
            mock_run.return_value = _mock_run_ok()
            adapter.take(":99", "/tmp/test.png")

            kwargs = mock_run.call_args.kwargs
            env = kwargs["env"]
            assert env.get("DISPLAY") == ":99", (
                f"ScrotAdapter env['DISPLAY'] must be ':99', got: {env.get('DISPLAY')!r}"
            )


# ═══════════════════════════════════════════════════════════════════════
# R-FIX-05: Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestMouseMoveEdgeCases:
    """R-FIX-05: mouse_move edge cases."""

    def test_mouse_move_zero_coords(self) -> None:
        """(0, 0) must be accepted — '0' must appear in args."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_move(":99", 0, 0)

            cmd = mock_run.call_args[0][0]
            assert "0" in cmd, f"'0' must appear in args for zero coords: {cmd}"

    def test_mouse_move_large_coords(self) -> None:
        """Large coordinates (99999, 99999) must not raise."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            # Must not raise
            adapter.mouse_move(":99", 99999, 99999)

            cmd = mock_run.call_args[0][0]
            assert "99999" in cmd


class TestMouseClickEdgeCases:
    """R-FIX-05: mouse_click edge cases."""

    def test_mouse_click_negative_coords(self) -> None:
        """Negative coordinates should still be passed to xdotool (it may reject)."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_click(":99", -1, -1, "left")

            cmd = mock_run.call_args[0][0]
            assert "-1" in cmd, f"'-1' must appear in args for negative coords: {cmd}"


class TestKeyEdgeCases:
    """R-FIX-05: key() edge cases."""

    def test_key_empty_list_no_subprocess_call(self) -> None:
        """key([]) must not invoke subprocess.run at all."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.key(":99", [])

            assert mock_run.call_count == 0, (
                f"key([]) must not call subprocess.run, got {mock_run.call_count} calls"
            )


class TestTypeTextEdgeCases:
    """R-FIX-05: type_text() edge cases."""

    def test_type_empty_string(self) -> None:
        """type_text('') behavior — xdotool is invoked (no guard in current code)."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            # Current code calls _run unconditionally; empty string is passed through
            adapter.type_text(":99", "")

            # We just verify it doesn't raise. subprocess.run is called.
            cmd = mock_run.call_args[0][0]
            assert "type" in cmd


class TestScrollEdgeCases:
    """R-FIX-05: mouse_scroll() edge cases."""

    @pytest.mark.parametrize(
        "direction, expected_button",
        [
            ("up", "4"),
            ("down", "5"),
            ("left", "6"),
            ("right", "7"),
        ],
    )
    def test_scroll_all_directions(self, direction: str, expected_button: str) -> None:
        """All four scroll directions must map to correct xdotool click buttons."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_scroll(":99", direction, 1)

            cmd = mock_run.call_args[0][0]
            assert expected_button in cmd, (
                f"scroll('{direction}') must use button {expected_button}, got cmd: {cmd}"
            )

    def test_scroll_amount_zero_no_subprocess_call(self) -> None:
        """mouse_scroll with amount=0 must not invoke subprocess.run."""
        adapter = _make_adapter()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _mock_run_ok()
            adapter.mouse_scroll(":99", "down", 0)

            assert mock_run.call_count == 0, (
                f"scroll(amount=0) must not call subprocess.run, got {mock_run.call_count} calls"
            )
