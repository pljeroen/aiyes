"""AIYES-56 Android public/maintainer release-smoke contract tests."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
SMOKE = PROJECT_ROOT / "docs" / "release-smoke.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestAndroidReleaseSmoke:
    def test_android_settings_public_scenario_is_documented(self) -> None:
        content = _read(SMOKE)

        for phrase in (
            "Android Settings public scenario",
            "examples/scenarios/android-settings.json",
            "android.settings.SETTINGS",
            "private apps",
            "credentials",
        ):
            assert phrase in content

    def test_android_maintainer_private_smoke_requires_cleanup_success(self) -> None:
        content = _read(SMOKE)

        assert "Maintainers may keep additional private Android app smoke checks locally" in content
        assert "untracked" in content
        assert "public" in content
