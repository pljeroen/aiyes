"""AIYES-64: golden smoke matrix documentation."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX = PROJECT_ROOT / "docs" / "golden-smoke-matrix.md"
RELEASE_SMOKE = PROJECT_ROOT / "docs" / "release-smoke.md"


def test_golden_smoke_matrix_defines_maintained_targets() -> None:
    content = MATRIX.read_text(encoding="utf-8")

    required = [
        "Golden Smoke Matrix",
        "linux-gedit",
        "android-settings",
        "AT-SPI",
        "UIAutomator",
        "semantic capability",
        "observe-act-verify",
    ]
    for text in required:
        assert text in content


def test_golden_smoke_matrix_classifies_toolkit_failures() -> None:
    content = MATRIX.read_text(encoding="utf-8")

    required = [
        "control app failure",
        "AIYES-wide release blocker",
        "toolkit/app evidence",
        "not an AIYES-wide failure",
        "unless a control app fails",
    ]
    for text in required:
        assert text in content


def test_release_smoke_links_to_golden_matrix() -> None:
    content = RELEASE_SMOKE.read_text(encoding="utf-8")

    assert "golden-smoke-matrix.md" in content
    assert "Golden Smoke Matrix" in content
