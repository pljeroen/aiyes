"""AIYES-55 Linux egui release-smoke contract tests."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
SMOKE = PROJECT_ROOT / "docs" / "release-smoke.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestLinuxEguiReleaseSmoke:
    def test_release_smoke_describes_maintainer_only_egui_target(self) -> None:
        content = _read(SMOKE)

        assert "Maintainer-only egui smoke" in content
        assert "private egui application" in content
        assert "examples/scenarios/linux-gedit-text.json" in content
        assert "egui" in content

    def test_release_smoke_requires_non_empty_accessibility_evidence(self) -> None:
        content = _read(SMOKE)

        assert "non-empty AT-SPI/AccessKit tree" in content
        assert "release blocker" in content
        assert "screenshot" in content

    def test_release_smoke_has_observe_act_verify_shape_for_egui(self) -> None:
        content = _read(SMOKE)

        for phrase in (
            "observe-act-verify",
            "start",
            "inspect",
            "find",
            "action/input",
            "verify",
            "screenshot",
            "cleanup",
        ):
            assert phrase in content
