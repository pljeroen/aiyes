"""AIYES-38 Group A — Mouse Click Named Arguments.

Tests for dual-mode coordinate input: positional (backward compat) and
named --x/--y options.

Traceability — Acceptance Criteria:
  AC-A01: mouse click 540 960 (positional, regression guard)
  AC-A02: mouse click --x 540 --y 960 (named form)
  AC-A03: mouse click --x 540 --y 960 --button right (named + button)
  AC-A04: mouse click --x 540 (missing --y) produces error
  AC-A05: mouse click (no coords) clicks at current position (regression)
  AC-A06: mouse click 540 960 --x 100 (both forms) produces error
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from aiyes.cli.main import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _patches():
    """Common patches: mock mouse_uc and resolve_session_id."""
    return (
        patch("aiyes.cli.main.mouse_uc"),
        patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
    )


class TestMouseClickNamedArgs:
    """AC-A01 through AC-A06: dual-mode coordinate input."""

    def test_positional_form(self, runner: CliRunner) -> None:
        """AC-A01: mouse click 540 960 works (regression guard)."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse as m_mouse, mock_resolve:
            result = runner.invoke(cli, ["mouse", "click", "540", "960"])

        assert result.exit_code == 0, f"stdout={result.output!r}"
        m_mouse.click.assert_called_once_with("s1", 540, 960, "left")

    def test_named_form(self, runner: CliRunner) -> None:
        """AC-A02: mouse click --x 540 --y 960 works."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse as m_mouse, mock_resolve:
            result = runner.invoke(cli, ["mouse", "click", "--x", "540", "--y", "960"])

        assert result.exit_code == 0, f"stdout={result.output!r}"
        m_mouse.click.assert_called_once_with("s1", 540, 960, "left")

    def test_named_form_with_button(self, runner: CliRunner) -> None:
        """AC-A03: mouse click --x 540 --y 960 --button right works."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse as m_mouse, mock_resolve:
            result = runner.invoke(
                cli,
                ["mouse", "click", "--x", "540", "--y", "960", "--button", "right"],
            )

        assert result.exit_code == 0, f"stdout={result.output!r}"
        m_mouse.click.assert_called_once_with("s1", 540, 960, "right")

    def test_named_missing_y_produces_error(self, runner: CliRunner) -> None:
        """AC-A04: mouse click --x 540 (missing --y) produces error."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse, mock_resolve:
            result = runner.invoke(cli, ["mouse", "click", "--x", "540"])

        assert result.exit_code == 2, f"stdout={result.output!r}"
        assert "x" in result.output.lower() or "y" in result.output.lower()

    def test_named_missing_x_produces_error(self, runner: CliRunner) -> None:
        """AC-A04 (symmetric): mouse click --y 960 (missing --x) produces error."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse, mock_resolve:
            result = runner.invoke(cli, ["mouse", "click", "--y", "960"])

        assert result.exit_code == 2, f"stdout={result.output!r}"
        assert "x" in result.output.lower() or "y" in result.output.lower()

    def test_no_coords_clicks_current_position(self, runner: CliRunner) -> None:
        """AC-A05: mouse click (no coordinates) clicks at current position."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse as m_mouse, mock_resolve:
            result = runner.invoke(cli, ["mouse", "click"])

        assert result.exit_code == 0, f"stdout={result.output!r}"
        m_mouse.click.assert_called_once_with("s1", None, None, "left")

    def test_both_positional_and_named_produces_error(self, runner: CliRunner) -> None:
        """AC-A06: mouse click 540 960 --x 100 (both forms) produces error."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse, mock_resolve:
            result = runner.invoke(cli, ["mouse", "click", "540", "960", "--x", "100"])

        assert result.exit_code == 2, f"stdout={result.output!r}"

    def test_positional_with_button(self, runner: CliRunner) -> None:
        """Regression: positional form combined with --button still works."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse as m_mouse, mock_resolve:
            result = runner.invoke(
                cli,
                ["mouse", "click", "540", "960", "--button", "right"],
            )

        assert result.exit_code == 0, f"stdout={result.output!r}"
        m_mouse.click.assert_called_once_with("s1", 540, 960, "right")

    def test_positional_single_coord_produces_error(self, runner: CliRunner) -> None:
        """Edge case: mouse click 540 (only one positional) produces error."""
        mock_mouse, mock_resolve = _patches()
        with mock_mouse, mock_resolve:
            result = runner.invoke(cli, ["mouse", "click", "540"])

        assert result.exit_code == 2, f"stdout={result.output!r}"
