"""AIYES-76: release scenario docs and parity planning."""

from __future__ import annotations

from pathlib import Path


def test_release_scenario_docs_describe_secondary_gate_not_e2e_replacement() -> None:
    content = Path("docs/release-scenarios.md").read_text(encoding="utf-8")

    assert "secondary release gate" in content
    assert "not a replacement for deterministic tests" in content
    assert "does not plan or reason" in content


def test_release_scenario_docs_reference_public_fixtures_only() -> None:
    content = Path("docs/release-scenarios.md").read_text(encoding="utf-8")

    assert "examples/scenarios/linux-gedit-text.json" in content
    assert "examples/scenarios/android-settings.json" in content
    assert "android.settings.SETTINGS" in content
    assert "private_egui_marker" not in content.lower()
    assert "private_android_marker" not in content.lower()


def test_readme_links_release_scenario_runner_without_overstating_scope() -> None:
    content = Path("README.md").read_text(encoding="utf-8")

    assert "Release scenarios" in content
    assert "deterministic" in content
    assert "not an LLM planner" in content


def test_mcp_parity_is_documented_as_implemented_without_planner_scope() -> None:
    content = Path("docs/release-scenarios.md").read_text(encoding="utf-8")

    assert "## MCP Parity" in content
    assert "MCP exposes the same release-scenario surfaces as the CLI" in content
    assert "It must not introduce an LLM planner" in content
