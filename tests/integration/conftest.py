"""Integration test fixtures — skip conditions for system dependencies.

Traceability:
  IC-01 (amended): Skip fixtures for Xvfb, xdotool, scrot, AT-SPI2
  INTEG-01..03: Skipped when Xvfb unavailable
  INTEG-04..06: Never skipped
"""

from __future__ import annotations

import shutil

import pytest


def _tool_available(name: str) -> bool:
    """Check if a command-line tool is available on PATH."""
    return shutil.which(name) is not None


def _gi_available() -> bool:
    """Check if gi.repository.Atspi is importable."""
    try:
        import gi  # noqa: F401

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # noqa: F401

        return True
    except (ImportError, ValueError):
        return False


# ── Skip conditions ──────────────────────────────────────────────────

HAS_XVFB = _tool_available("Xvfb")
HAS_XDOTOOL = _tool_available("xdotool")
HAS_SCROT = _tool_available("scrot") or _tool_available("import")
HAS_GI = _gi_available()

requires_xvfb = pytest.mark.skipif(
    not HAS_XVFB,
    reason="Xvfb not available on this system",
)

requires_xdotool = pytest.mark.skipif(
    not HAS_XDOTOOL,
    reason="xdotool not available on this system",
)

requires_screenshot_tool = pytest.mark.skipif(
    not HAS_SCROT,
    reason="Neither scrot nor imagemagick import available",
)

requires_gi = pytest.mark.skipif(
    not HAS_GI,
    reason="gi.repository.Atspi not available",
)

requires_xvfb_full = pytest.mark.skipif(
    not (HAS_XVFB and HAS_XDOTOOL),
    reason="Full Xvfb environment (Xvfb + xdotool) not available",
)
