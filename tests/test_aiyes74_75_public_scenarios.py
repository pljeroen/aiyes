"""AIYES-74/75: public release scenario fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from aiyes.adapters.scenario_loader import load_scenario_file


LINUX_GEDIT = Path("examples/scenarios/linux-gedit-text.json")
ANDROID_SETTINGS = Path("examples/scenarios/android-settings.json")
PRIVATE_DENYLIST = (
    "private_egui_marker",
    "private_android_marker",
    "/home/example/private",
    "com.private.marker",
)


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _kinds(document: dict[str, object]) -> list[str]:
    steps = document["steps"]
    assert isinstance(steps, list)
    return [str(step["kind"]) for step in steps if isinstance(step, dict)]


def test_public_linux_gedit_scenario_validates_as_public_fixture() -> None:
    result = load_scenario_file(LINUX_GEDIT, public_fixture=True)

    assert result.ok is True
    assert result.scenario is not None
    assert result.scenario.id == "linux-gedit-text"
    assert result.scenario.target == "linux"


def test_public_linux_gedit_scenario_is_observe_act_verify_cleanup() -> None:
    document = _load(LINUX_GEDIT)
    encoded = json.dumps(document)

    assert '"gedit"' in encoded
    assert "hello from aiyes" in encoded
    for kind in (
        "start_session",
        "inspect",
        "type_text",
        "assert",
        "screenshot",
    ):
        assert kind in _kinds(document)
    cleanup = document["cleanup"]
    assert isinstance(cleanup, list)
    assert cleanup[0]["kind"] == "stop_session"


def test_public_android_settings_scenario_validates_as_public_fixture() -> None:
    result = load_scenario_file(ANDROID_SETTINGS, public_fixture=True)

    assert result.ok is True
    assert result.scenario is not None
    assert result.scenario.id == "android-settings"
    assert result.scenario.target == "android"


def test_public_android_settings_scenario_uses_base_settings_intent() -> None:
    document = _load(ANDROID_SETTINGS)
    encoded = json.dumps(document)

    assert "android.settings.SETTINGS" in encoded
    assert '"device_serial": "auto"' in encoded
    assert "com.private.marker" not in encoded
    for kind in (
        "start_session",
        "inspect",
        "find",
        "action",
        "screenshot",
        "navigate",
    ):
        assert kind in _kinds(document)


def test_public_scenario_fixtures_do_not_reference_private_apps() -> None:
    for path in (LINUX_GEDIT, ANDROID_SETTINGS):
        content = path.read_text(encoding="utf-8").lower()
        for denied in PRIVATE_DENYLIST:
            assert denied not in content
