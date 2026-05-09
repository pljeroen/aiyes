"""Public release scenario fixture discovery."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Mapping, Tuple

from aiyes.domain.scenario import validate_scenario_document


_PUBLIC_FIXTURE_PATHS = (
    Path("examples/scenarios/linux-gedit-text.json"),
    Path("examples/scenarios/android-settings.json"),
)


@dataclasses.dataclass(frozen=True)
class ScenarioFixtureInfo:
    """Metadata for one public scenario fixture."""

    scenario_id: str
    title: str
    target: str
    path: str
    prerequisites: Tuple[Mapping[str, Any], ...]
    real_execution_supported: bool


def list_public_scenario_fixtures() -> Tuple[ScenarioFixtureInfo, ...]:
    """List tracked public release scenario fixtures."""
    fixtures: list[ScenarioFixtureInfo] = []
    for path in _PUBLIC_FIXTURE_PATHS:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        loaded = validate_scenario_document(document, public_fixture=True)
        if loaded.scenario is None:
            continue
        scenario = loaded.scenario
        fixtures.append(
            ScenarioFixtureInfo(
                scenario_id=scenario.id,
                title=scenario.title,
                target=scenario.target,
                path=str(path),
                prerequisites=tuple(dict(item) for item in scenario.prerequisites),
                real_execution_supported=True,
            )
        )
    return tuple(fixtures)
