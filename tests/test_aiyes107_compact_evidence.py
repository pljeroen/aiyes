"""AIYES-107 — Compact smoke and scenario evidence profile (RED).

This suite is authored RED (the profile-aware shaping, the MCP ``profile``
argument, and the LE-02 ``evidence.profile.selected`` emission do not exist
yet). It integrates the AIYES-103..106 diagnostic surfaces into a compact
default evidence profile and a deep opt-in profile.

Public surfaces under test (signatures A9 must produce):
  - aiyes.cli.presenter.format_scenario_run(result, profile="compact",
        diagnostic_log=None)
  - aiyes.adapters.scenario_evidence.write_scenario_evidence_bundle(
        bundle_dir, run, environment=None, profile="compact",
        diagnostic_log=None)
  - aiyes.smoke_harness.run_smoke_harness(..., profile="compact",
        diagnostic_log=None) / build_evidence(..., profile="compact")
  - aiyes.adapters.mcp_server._handle_scenario_run honoring args["profile"]
  - aiyes.cli.schema_gen scenario_run schema exposing properties["profile"]

Observability uses the PRODUCTION InMemoryDiagnosticLog (not an inline
double). The fail-open test injects an internal store failure into a real
adapter instance (non-self-satisfying).

Constraint coverage:
  FC-EVIDENCE-01/02/03/04, FC-PROFILE-01/02/03, FC-MCP-01,
  FC-CLASS-01/02/03, FC-SERIAL-01/02/03/04, FC-REGRESS-01,
  FC-OBS-01/02/03. RD-01 (tree-OBJECT rejection) applied in the
  raw-tree-exclusion oracle.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from aiyes import smoke_harness as smoke_harness_module
from aiyes.adapters.diagnostic_log import InMemoryDiagnosticLog
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_dry_run_executor import ScenarioDryRunExecutor
from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.adapters.scenario_loader import load_scenario_file
from aiyes.cli import main as cli_main_module
from aiyes.cli import presenter as presenter_module
from aiyes.cli.main import cli
from aiyes.cli.presenter import format_scenario_run
from aiyes.cli.schema_gen import click_to_json_schema, enumerate_commands
from aiyes.domain import evidence_profile as evidence_profile_module
from aiyes.domain.diagnostic_event import DiagnosticEvent
from aiyes.domain.use_cases.scenario_run import (
    ScenarioRunResult,
    ScenarioRunStepResult,
)
from aiyes.smoke_harness import SOCIALZZZ_TARGET, build_evidence, run_smoke_harness


# --------------------------------------------------------------------------- #
# RD-01 raw-tree-exclusion oracle.
#
# Rejects raw accessibility tree payloads anywhere in a serialized structure.
# Per RD-01 the oracle MUST reject tree OBJECTS carrying roots/children node
# payloads (InspectResult.tree serialized under "tree" with nested "roots"),
# NOT only direct node-LIST values. The oracle walks every dict/list and
# treats a "tree" key as raw when its value is a node-list, OR a mapping that
# carries "roots", OR a mapping/list that carries nested "children" node
# payloads. Bounded residue (12-16 char fingerprints, selector candidates) is
# permitted by construction because it never appears under a "tree" key.
# --------------------------------------------------------------------------- #


def _is_raw_tree_value(value: Any) -> bool:
    """True when ``value`` is a raw accessibility-tree payload (RD-01)."""
    if isinstance(value, list):
        # A node-LIST: list of node dicts (each carrying id/role/children).
        return any(
            isinstance(node, dict)
            and (
                "children" in node
                or "roots" in node
                or "role" in node
                or "node_id" in node
                or "id" in node
            )
            for node in value
        )
    if isinstance(value, dict):
        # A tree OBJECT carrying roots/children node payloads.
        if "roots" in value or "children" in value:
            return True
    return False


def _assert_no_raw_tree(payload: Any, path: str = "$") -> None:
    """Recursively assert no raw accessibility tree payload exists."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{path}.{key}"
            if key == "tree" and _is_raw_tree_value(value):
                raise AssertionError(
                    f"raw accessibility tree payload found at {here}: {value!r}"
                )
            # A raw stdout string that embeds a nested scenario/tree.
            if key == "stdout" and isinstance(value, str) and value.strip():
                _assert_stdout_has_no_embedded_tree(value, here)
            _assert_no_raw_tree(value, here)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_raw_tree(item, f"{path}[{index}]")


def _assert_stdout_has_no_embedded_tree(stdout: str, path: str) -> None:
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return
    _assert_no_raw_tree(parsed, f"{path}<parsed-stdout>")


# --------------------------------------------------------------------------- #
# Fixtures: representative step records carrying the AIYES-103..106 surfaces.
# These mirror the executor's step output shape (plain dicts) so the suite
# exercises the presentation/shaping layer AIYES-107 owns, not execution.
# --------------------------------------------------------------------------- #


def _raw_inspect_tree_object() -> Dict[str, Any]:
    """An inspect step output carrying a raw tree OBJECT with roots/children."""
    return {
        "tree": {
            "roots": [
                {
                    "id": "n_001",
                    "role": "frame",
                    "name": "Editor",
                    "children": [
                        {"id": "n_002", "role": "button", "name": "Save"},
                        {"id": "n_003", "role": "text", "name": "Body"},
                    ],
                }
            ]
        },
        "screenshot_path": "/tmp/shot.png",
    }


def _raw_inspect_tree_nodelist() -> Dict[str, Any]:
    """An inspect step output carrying a raw tree NODE-LIST under 'tree'."""
    return {
        "tree": [
            {
                "id": "n_001",
                "role": "frame",
                "children": [{"id": "n_002", "role": "button"}],
            }
        ],
    }


def _selector_diagnostics_block(candidate_count: int = 9) -> Dict[str, Any]:
    """A bounded selector_diagnostics block (AIYES-105), candidates <= 5."""
    candidates = [
        {"node_id": f"c_{i}", "role": "button", "name": f"Send {i}"}
        for i in range(min(candidate_count, 5))
    ]
    return {
        "requested_selector": {
            "role": "button",
            "name_pattern": "Send",
            "state": None,
        },
        "candidate_count": candidate_count,
        "max_candidates": 5,
        "summary": "selector diagnostics: near-name candidate(s) found",
        "candidates": candidates,
    }


def _step_103_required_find() -> ScenarioRunStepResult:
    """AIYES-103: required-find consumption failure with selector diagnostics."""
    return ScenarioRunStepResult(
        step_id="find-share",
        kind="find",
        status="failed",
        output={
            "nodes": [],
            "failure_class": "target_not_found_no_progress",
            "selector_diagnostics": _selector_diagnostics_block(),
            "tree": _raw_inspect_tree_nodelist()["tree"],
        },
        error="required find matched zero nodes for role=button name=Send",
    )


def _step_104_wait_timeout() -> ScenarioRunStepResult:
    """AIYES-104: wait-timeout-as-failure."""
    return ScenarioRunStepResult(
        step_id="wait-dialog",
        kind="wait",
        status="failed",
        output={"timeout": True, "tree": _raw_inspect_tree_object()["tree"]},
        error="wait timed out after 5000ms without matching the dialog",
    )


def _step_105_selector_ranking() -> ScenarioRunStepResult:
    """AIYES-105: selector diagnostic ranking, bounded candidates."""
    return ScenarioRunStepResult(
        step_id="find-button",
        kind="find",
        status="failed",
        output={
            "nodes": [],
            "selector_diagnostics": _selector_diagnostics_block(candidate_count=12),
            "tree": _raw_inspect_tree_nodelist()["tree"],
        },
        error="",
    )


def _step_106_no_scrollable() -> ScenarioRunStepResult:
    """AIYES-106: no-scrollable guidance, failure_class carried forward."""
    return ScenarioRunStepResult(
        step_id="scroll-feed",
        kind="scroll_into_view",
        status="failed",
        output={
            "failure_class": "no_scrollable",
            "guidance": {"hint": "expose a scrollable region in the app"},
            "tree": _raw_inspect_tree_object()["tree"],
        },
        error="",
    )


def _step_passed_inspect() -> ScenarioRunStepResult:
    """A passed inspect step that carries a raw tree (deep retains it)."""
    return ScenarioRunStepResult(
        step_id="inspect-root",
        kind="inspect",
        status="passed",
        output=_raw_inspect_tree_object(),
    )


def _mixed_run() -> ScenarioRunResult:
    return ScenarioRunResult(
        scenario_id="aiyes107-mixed",
        status="failed",
        mode="real",
        failure_code="assertion_failed",
        steps=(
            _step_passed_inspect(),
            _step_103_required_find(),
            _step_104_wait_timeout(),
            _step_105_selector_ranking(),
            _step_106_no_scrollable(),
        ),
    )


def _compact_payload(run: ScenarioRunResult, **kwargs: Any) -> Dict[str, Any]:
    return json.loads(format_scenario_run(run, profile="compact", **kwargs))


def _deep_payload(run: ScenarioRunResult, **kwargs: Any) -> Dict[str, Any]:
    return json.loads(format_scenario_run(run, profile="deep", **kwargs))


# --------------------------------------------------------------------------- #
# FC-EVIDENCE-01 / FC-EVIDENCE-02 — compact default shape + raw-tree exclusion.
# --------------------------------------------------------------------------- #


def test_compact_default_profile_excludes_all_raw_tree_surfaces() -> None:
    """FC-EVIDENCE-02 / RD-01: no raw tree OBJECT or node-LIST survives."""
    payload = _compact_payload(_mixed_run())
    _assert_no_raw_tree(payload)


def test_format_scenario_run_default_is_compact_without_profile_arg() -> None:
    """FC-PROFILE-01: omitting profile yields compact (no raw tree)."""
    payload = json.loads(format_scenario_run(_mixed_run()))
    _assert_no_raw_tree(payload)


def test_compact_records_preserve_step_id_kind_status_everywhere() -> None:
    """FC-EVIDENCE-01: every record keeps step_id, kind, status."""
    payload = _compact_payload(_mixed_run())
    for step in payload["steps"]:
        assert "step_id" in step
        assert "kind" in step
        assert "status" in step


def test_compact_failed_records_keep_classification_key_set() -> None:
    """FC-EVIDENCE-01: every failed record keeps classification + summary."""
    payload = _compact_payload(_mixed_run())
    for step in payload["steps"]:
        if step["status"] == "passed":
            continue
        has_code_or_class = ("failure_code" in step) or ("failure_class" in step)
        assert has_code_or_class, step
        assert isinstance(step["diagnostic_summary"], str)
        assert "artifact_refs" in step


# --------------------------------------------------------------------------- #
# FC-EVIDENCE-03 — bounded selector diagnostics (<= 5).
# --------------------------------------------------------------------------- #


def test_compact_selector_diagnostics_stay_bounded_to_five() -> None:
    """FC-EVIDENCE-03: candidates <= 5 and max_candidates == 5 retained."""
    payload = _compact_payload(_mixed_run())
    seen = False
    for step in payload["steps"]:
        diags = step.get("selector_diagnostics")
        if diags is None:
            continue
        seen = True
        assert len(diags["candidates"]) <= 5
        assert diags["max_candidates"] == 5
    assert seen, "expected at least one record with selector_diagnostics"


# --------------------------------------------------------------------------- #
# FC-EVIDENCE-04 — artifact_refs collection presence + existence semantics.
# --------------------------------------------------------------------------- #


def test_compact_in_memory_run_has_empty_artifact_refs_per_record() -> None:
    """FC-EVIDENCE-04: in-memory run -> artifact_refs == [] (no invented paths)."""
    payload = _compact_payload(_mixed_run())
    for step in payload["steps"]:
        assert step["artifact_refs"] == []


def test_compact_bundle_artifact_refs_resolve_to_written_files(
    tmp_path: Path,
) -> None:
    """FC-EVIDENCE-04: bundle-writing run -> every artifact_ref points at a file."""
    bundle_dir = tmp_path / "bundle"
    write_scenario_evidence_bundle(bundle_dir, _mixed_run(), profile="compact")
    steps = [
        json.loads(line)
        for line in (bundle_dir / "steps.jsonl").read_text("utf-8").splitlines()
    ]
    for step in steps:
        for ref in step.get("artifact_refs", []):
            assert (bundle_dir / ref).exists() or Path(ref).exists(), ref


# --------------------------------------------------------------------------- #
# FC-PROFILE-02 / FC-PROFILE-03 — deep opt-in retains the pre-change detail.
# --------------------------------------------------------------------------- #


def test_deep_profile_retains_raw_inspect_tree_object() -> None:
    """FC-PROFILE-02: deep keeps the raw inspect tree the pre-change code emits."""
    payload = _deep_payload(_mixed_run())
    inspect_step = next(s for s in payload["steps"] if s["kind"] == "inspect")
    tree = inspect_step["output"]["tree"]
    assert "roots" in tree
    assert tree["roots"][0]["children"]


def test_deep_profile_is_superset_of_compact_detail() -> None:
    """FC-PROFILE-02: deep drops no field the compact render carries."""
    compact = _compact_payload(_mixed_run())
    deep = _deep_payload(_mixed_run())
    assert {s["step_id"] for s in deep["steps"]} == {
        s["step_id"] for s in compact["steps"]
    }
    # The raw tree present in deep is exactly what compact omits.
    with pytest.raises(AssertionError):
        _assert_no_raw_tree(deep)


def test_build_evidence_default_is_compact_and_deep_is_opt_in() -> None:
    """FC-PROFILE-01/03: smoke build_evidence default compact, deep explicit."""
    default = build_evidence(target=SOCIALZZZ_TARGET, enabled=False)
    explicit_compact = build_evidence(
        target=SOCIALZZZ_TARGET, enabled=False, profile="compact"
    )
    assert default["profile"] == "compact"
    assert explicit_compact["profile"] == "compact"
    deep = build_evidence(target=SOCIALZZZ_TARGET, enabled=False, profile="deep")
    assert deep["profile"] == "deep"


# --------------------------------------------------------------------------- #
# FC-CLASS-01 — classifiable from compact without reading a raw tree.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "step_factory, expect_marker",
    [
        (_step_103_required_find, "selector_diagnostics"),
        (_step_104_wait_timeout, "diagnostic_summary"),
        (_step_105_selector_ranking, "selector_diagnostics"),
        (_step_106_no_scrollable, "failure_class"),
    ],
)
def test_each_slice_failure_is_classifiable_from_compact(
    step_factory: Any, expect_marker: str
) -> None:
    """FC-CLASS-01: AIYES-103..106 sample failures classifiable from compact."""
    run = ScenarioRunResult(
        scenario_id="aiyes107-slice",
        status="failed",
        mode="real",
        failure_code="assertion_failed",
        steps=(step_factory(),),
    )
    step = _compact_payload(run)["steps"][0]
    _assert_no_raw_tree(step)
    has_class = ("failure_code" in step) or ("failure_class" in step)
    assert has_class
    assert step["diagnostic_summary"]  # non-empty
    assert expect_marker in step


def test_no_scrollable_class_carried_for_aiyes106() -> None:
    """FC-CLASS-01: AIYES-106 surfaces failure_class == 'no_scrollable'."""
    run = ScenarioRunResult(
        scenario_id="aiyes107-106",
        status="failed",
        mode="real",
        failure_code="executor_error",
        steps=(_step_106_no_scrollable(),),
    )
    step = _compact_payload(run)["steps"][0]
    assert step["failure_class"] == "no_scrollable"


# --------------------------------------------------------------------------- #
# FC-CLASS-02 — upstream classification copied verbatim, never contradicted.
# --------------------------------------------------------------------------- #


def test_compact_copies_upstream_failure_class_and_selector_verbatim() -> None:
    """FC-CLASS-02: upstream failure_class + selector_diagnostics unchanged."""
    upstream = _step_106_no_scrollable()
    upstream_selector = _step_105_selector_ranking()
    run = ScenarioRunResult(
        scenario_id="aiyes107-verbatim",
        status="failed",
        mode="real",
        failure_code="executor_error",
        steps=(upstream, upstream_selector),
    )
    steps = _compact_payload(run)["steps"]
    by_id = {s["step_id"]: s for s in steps}
    assert by_id["scroll-feed"]["failure_class"] == "no_scrollable"
    # selector_diagnostics copied verbatim (requested_selector + summary).
    src_diags = upstream_selector.output["selector_diagnostics"]
    out_diags = by_id["find-button"]["selector_diagnostics"]
    assert out_diags["requested_selector"] == src_diags["requested_selector"]
    assert out_diags["summary"] == src_diags["summary"]
    assert out_diags["candidate_count"] == src_diags["candidate_count"]


# --------------------------------------------------------------------------- #
# FC-CLASS-03 — diagnostic_summary: single-line, <= 200, derived fallback.
# --------------------------------------------------------------------------- #


def test_diagnostic_summary_first_line_truncated_to_200() -> None:
    """FC-CLASS-03 branch 1: non-empty multi-line >200-char error."""
    long_line = "x" * 500
    multiline = f"{long_line}\nsecond line should be dropped"
    run = ScenarioRunResult(
        scenario_id="aiyes107-summary",
        status="failed",
        mode="real",
        failure_code="executor_error",
        steps=(
            ScenarioRunStepResult(
                step_id="boom",
                kind="action",
                status="failed",
                output={},
                error=multiline,
            ),
        ),
    )
    summary = _compact_payload(run)["steps"][0]["diagnostic_summary"]
    assert summary == multiline.splitlines()[0][:200]
    assert "\n" not in summary
    assert len(summary) <= 200


def test_diagnostic_summary_derived_from_code_when_error_empty() -> None:
    """FC-CLASS-03 branch 2 (SV-01): failed record, empty error -> non-empty."""
    run = ScenarioRunResult(
        scenario_id="aiyes107-empty-error",
        status="failed",
        mode="real",
        failure_code="assertion_failed",
        steps=(
            ScenarioRunStepResult(
                step_id="assert-x",
                kind="assert",
                status="failed",
                output={},
                error="",  # the SV-01 case
            ),
        ),
    )
    summary = _compact_payload(run)["steps"][0]["diagnostic_summary"]
    assert summary != ""  # derived from humanized failure_code/class
    assert "\n" not in summary
    assert len(summary) <= 200


def test_diagnostic_summary_empty_only_for_nonfailed_without_text() -> None:
    """FC-CLASS-03 branch 3: non-failed record with no diagnostic text -> ''."""
    run = ScenarioRunResult(
        scenario_id="aiyes107-passed",
        status="passed",
        mode="real",
        steps=(
            ScenarioRunStepResult(
                step_id="ok",
                kind="inspect",
                status="passed",
                output={},
                error="",
            ),
        ),
    )
    summary = _compact_payload(run)["steps"][0].get("diagnostic_summary", "")
    assert summary == ""


def test_diagnostic_summary_is_deterministic_pure_function() -> None:
    """FC-CLASS-03: same inputs -> same output (no embedded tree)."""
    run = _mixed_run()
    first = _compact_payload(run)["steps"]
    second = _compact_payload(run)["steps"]
    first_summaries = [s.get("diagnostic_summary", "") for s in first]
    second_summaries = [s.get("diagnostic_summary", "") for s in second]
    assert first_summaries == second_summaries
    for summary in first_summaries:
        assert "roots" not in summary and "children" not in summary


# --------------------------------------------------------------------------- #
# FC-SERIAL-01 — JSON round-trip under both profiles.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("profile", ["compact", "deep"])
def test_scenario_payload_json_round_trips_under_both_profiles(profile: str) -> None:
    """FC-SERIAL-01: scenario JSON serializes + round-trips for both profiles."""
    rendered = format_scenario_run(_mixed_run(), profile=profile)
    assert json.loads(json.dumps(json.loads(rendered))) == json.loads(rendered)


@pytest.mark.parametrize("profile", ["compact", "deep"])
def test_smoke_harness_json_round_trips_under_both_profiles(profile: str) -> None:
    """FC-SERIAL-01: smoke evidence serializes + round-trips for both profiles."""
    evidence = run_smoke_harness(
        target=SOCIALZZZ_TARGET, enabled=False, environ={}, profile=profile
    )
    assert json.loads(json.dumps(evidence)) == evidence


# --------------------------------------------------------------------------- #
# FC-SERIAL-02 — top-level smoke fields preserved + stable.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("profile", ["compact", "deep"])
def test_smoke_top_level_fields_preserved_for_socialzzz(profile: str) -> None:
    """FC-SERIAL-02: socialzzz top-level metadata fields preserved."""
    evidence = run_smoke_harness(
        target=SOCIALZZZ_TARGET, enabled=False, environ={}, profile=profile
    )
    for key in (
        "schema_version",
        "target",
        "enabled",
        "enable_env",
        "status",
        "started_at",
        "finished_at",
        "aiyes_version",
        "device_serial",
        "app_package",
        "scenario_names",
        "missing_prerequisites",
        "observed_scroll_methods",
        "failure_reason",
    ):
        assert key in evidence, key


@pytest.mark.parametrize("profile", ["compact", "deep"])
def test_smoke_top_level_fields_preserved_for_generic_target(profile: str) -> None:
    """FC-SERIAL-02: generic-target top-level fields preserved."""
    evidence = run_smoke_harness(
        target="linux-gedit", enabled=False, environ={}, profile=profile
    )
    for key in (
        "schema_version",
        "target",
        "enabled",
        "enable_env",
        "status",
        "started_at",
        "finished_at",
    ):
        assert key in evidence, key


# --------------------------------------------------------------------------- #
# FC-SERIAL-03 — top-level scenario fields preserved + stable.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("profile", ["compact", "deep"])
def test_scenario_top_level_fields_preserved(profile: str) -> None:
    """FC-SERIAL-03: scenario top-level keys unchanged across profiles."""
    payload = json.loads(format_scenario_run(_mixed_run(), profile=profile))
    for key in (
        "scenario_id",
        "status",
        "mode",
        "failure_code",
        "steps",
        "next_actions",
    ):
        assert key in payload, key


# --------------------------------------------------------------------------- #
# FC-SERIAL-04 — bundle manifest/run.json keys preserved; steps shaped by profile.
# --------------------------------------------------------------------------- #


def test_bundle_manifest_and_run_keys_preserved_under_both_profiles(
    tmp_path: Path,
) -> None:
    """FC-SERIAL-04: manifest.json + run.json top-level keys profile-independent."""
    manifest_keys = {
        "schema_version",
        "scenario_id",
        "status",
        "mode",
        "primary_files",
        "artifacts_dir",
        "step_count",
        "inspection_order",
    }
    run_keys = {
        "schema_version",
        "scenario_id",
        "status",
        "mode",
        "failure_code",
        "next_actions",
        "environment",
        "artifacts_dir",
    }
    for profile in ("compact", "deep"):
        bundle = tmp_path / profile
        write_scenario_evidence_bundle(bundle, _mixed_run(), profile=profile)
        manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
        run_json = json.loads((bundle / "run.json").read_text("utf-8"))
        assert manifest_keys <= set(manifest), (profile, manifest)
        assert run_keys <= set(run_json), (profile, run_json)


def test_bundle_steps_jsonl_compact_excludes_tree_deep_retains(
    tmp_path: Path,
) -> None:
    """FC-SERIAL-04 + FC-EVIDENCE-02/FC-PROFILE-02: steps.jsonl shaped by profile."""
    compact_dir = tmp_path / "compact"
    deep_dir = tmp_path / "deep"
    write_scenario_evidence_bundle(compact_dir, _mixed_run(), profile="compact")
    write_scenario_evidence_bundle(deep_dir, _mixed_run(), profile="deep")

    compact_steps = [
        json.loads(line)
        for line in (compact_dir / "steps.jsonl").read_text("utf-8").splitlines()
    ]
    for step in compact_steps:
        _assert_no_raw_tree(step)

    deep_steps = [
        json.loads(line)
        for line in (deep_dir / "steps.jsonl").read_text("utf-8").splitlines()
    ]
    deep_has_tree = any(
        isinstance(s.get("output"), dict) and "tree" in s["output"] for s in deep_steps
    )
    assert deep_has_tree, "deep steps.jsonl must retain the raw inspect tree"


# --------------------------------------------------------------------------- #
# FC-MCP-01 — MCP scenario_run honors the profile arg end-to-end.
# --------------------------------------------------------------------------- #


def _mcp_deps() -> ServerDependencies:
    fields = {field: MagicMock() for field in ServerDependencies.__dataclass_fields__}
    fields["clock"].now.side_effect = [1.0, 1.1, 1.2, 1.3]
    fields["scenario_run_uc"] = ScenarioRunUseCaseStub()
    fields["scenario_real_run_uc"] = ScenarioRunUseCaseStub()
    fields["load_scenario_file"] = load_scenario_file
    fields["write_scenario_evidence_bundle"] = write_scenario_evidence_bundle
    return ServerDependencies(**fields)


class ScenarioRunUseCaseStub:
    """Returns a fixed mixed run carrying raw trees, regardless of scenario."""

    def execute(self, scenario: Any) -> ScenarioRunResult:
        return _mixed_run()


def _mcp_scenario_file(tmp_path: Path) -> Path:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "mcp-107",
                "title": "MCP 107",
                "target": "linux",
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": True, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )
    return scenario_path


@pytest.mark.asyncio
async def test_mcp_scenario_run_defaults_to_compact_without_profile(
    tmp_path: Path,
) -> None:
    """FC-MCP-01(a): no profile arg -> compact (FC-EVIDENCE-02 holds)."""
    server = create_mcp_server(_mcp_deps())
    result = await server.call_tool(
        "scenario_run", {"scenario_path": str(_mcp_scenario_file(tmp_path))}
    )
    assert result.isError is False
    payload = json.loads(result.content[0].text)
    _assert_no_raw_tree(payload)


@pytest.mark.asyncio
async def test_mcp_scenario_run_deep_profile_retains_tree(tmp_path: Path) -> None:
    """FC-MCP-01(b): profile='deep' -> deep evidence (raw tree retained)."""
    server = create_mcp_server(_mcp_deps())
    result = await server.call_tool(
        "scenario_run",
        {"scenario_path": str(_mcp_scenario_file(tmp_path)), "profile": "deep"},
    )
    assert result.isError is False
    payload = json.loads(result.content[0].text)
    with pytest.raises(AssertionError):
        _assert_no_raw_tree(payload)


@pytest.mark.asyncio
async def test_mcp_scenario_run_rejects_bogus_profile(tmp_path: Path) -> None:
    """FC-MCP-01(b): out-of-enum profile rejected, not coerced."""
    server = create_mcp_server(_mcp_deps())
    result = await server.call_tool(
        "scenario_run",
        {"scenario_path": str(_mcp_scenario_file(tmp_path)), "profile": "bogus"},
    )
    assert result.isError is True


def test_mcp_schema_exposes_profile_enum_and_default() -> None:
    """FC-MCP-01(d): generated scenario_run schema has profile enum + default."""
    schema = None
    for command in enumerate_commands(cli):
        if command.tool_name == "scenario_run":
            schema = click_to_json_schema(command.click_command)
            break
    assert schema is not None, "scenario_run command not found"
    profile = schema["properties"].get("profile")
    assert profile is not None, "scenario_run schema is missing 'profile'"
    assert profile.get("enum") == ["compact", "deep"]
    assert profile.get("default") == "compact"


# --------------------------------------------------------------------------- #
# FC-OBS-01 / FC-OBS-03 — LE-02 profile-selection emission.
#
# Target design (A10 remediation): the PURE domain shaper BUILDS the LE-02
# payload (a DiagnosticEvent value object) but does NOT emit it; emission
# happens EXACTLY ONCE at the adapter/command boundary (CLI scenario-run, MCP
# scenario_run handler, smoke harness run). These unit tests therefore assert
# the LE-02 payload semantics on the pure builder and the fail-open semantics on
# the production adapter's own emit_event — NOT on format_scenario_run, which no
# longer emits. End-to-end exactly-once cardinality is covered by the CLI/MCP
# tests in the A10 remediation block below.
# --------------------------------------------------------------------------- #


def _profile_events(log: InMemoryDiagnosticLog) -> List[DiagnosticEvent]:
    return [e for e in log.events if e.action == "evidence.profile.selected"]


def test_compact_selection_builds_one_bounded_le02_payload() -> None:
    """FC-OBS-01: the pure builder yields a compact LE-02 payload (no emission).

    Emission-location adjustment (A10-CRIT-001/004): the domain shaper builds
    the LE-02 payload purely; it is emitted once at the boundary. RED until A9
    adds build_profile_selection_event.
    """
    event = evidence_profile_module.build_profile_selection_event(
        "compact", preserved_failure_count=4
    )
    assert event.action == "evidence.profile.selected"
    assert event.profile == "compact"
    assert event.raw_tree_included is False
    assert event.preserved_failure_count == 4
    # bounded/redacted: no tree, no large blob carried on the event object.
    assert event.diagnostic_summary is None


def test_deep_selection_builds_le02_payload_with_raw_tree_included_true() -> None:
    """FC-OBS-01: deep selection payload -> raw_tree_included True."""
    event = evidence_profile_module.build_profile_selection_event(
        "deep", preserved_failure_count=0
    )
    assert event.profile == "deep"
    assert event.raw_tree_included is True


def test_format_scenario_run_does_not_emit_le02_itself() -> None:
    """FC-OBS-01 (emission-location): the presenter no longer emits LE-02.

    Under the once-per-invocation design, format_scenario_run shapes only; the
    boundary emits. Passing a production log to the presenter must NOT add a
    profile-selection event (otherwise the CLI/MCP boundary would double-emit).
    RED until A9 removes emission from the presenter.
    """
    log = InMemoryDiagnosticLog()
    format_scenario_run(_mixed_run(), profile="compact", diagnostic_log=log)
    assert _profile_events(log) == []


def test_le02_preserved_failure_count_matches_classified_failures() -> None:
    """FC-OBS-01: preserved_failure_count == number of classified failures.

    Driven through the pure count + builder the boundary composes, so the
    classified-failure semantics are pinned independent of emission location.
    """
    run = ScenarioRunResult(
        scenario_id="aiyes107-count",
        status="failed",
        mode="real",
        failure_code="assertion_failed",
        steps=(_step_passed_inspect(), _step_103_required_find()),
    )
    raw_steps = [dataclasses.asdict(step) for step in run.steps]
    preserved = evidence_profile_module.classified_failure_count(raw_steps)
    event = evidence_profile_module.build_profile_selection_event(
        "compact", preserved_failure_count=preserved
    )
    assert event.preserved_failure_count == 1


def test_le02_emission_is_fail_open_and_counts_internal_store_failure() -> None:
    """FC-OBS-03: internal store failure -> no raise + failure count == 1.

    Non-self-satisfying: a real InMemoryDiagnosticLog instance is mutated so its
    internal event store raises on append; the production adapter's own
    fail-open path must swallow and self-count when the boundary emits the
    built LE-02 payload. The emit call must not raise.
    """
    log = InMemoryDiagnosticLog()

    class _ExplodingList(list):
        def append(self, item: Any) -> None:  # noqa: D401
            raise RuntimeError("internal store failure")

    # Inject an internal store failure into the production adapter instance.
    log._events = _ExplodingList()  # type: ignore[assignment]

    event = evidence_profile_module.build_profile_selection_event(
        "compact", preserved_failure_count=4
    )
    # The adapter's emit_event owns fail-open: it must not raise to the caller.
    log.emit_event(event)
    # The one LE-02 emission failure is swallowed and counted.
    assert log.emission_failure_count() == 1


# --------------------------------------------------------------------------- #
# FC-OBS-02 — LE-01 classification emission from 103-106 still works.
# --------------------------------------------------------------------------- #


def test_le01_classification_event_payload_is_bounded_and_redacted() -> None:
    """FC-OBS-02: LE-01 event carries the four fields, bounded summary, no tree.

    Drives the production adapter directly with an LE-01 event (as the
    executor emits it) and asserts the adapter-side redaction holds — LE-01
    preservation from AIYES-103..106 is not broken by AIYES-107.
    """
    log = InMemoryDiagnosticLog()
    long_summary = "y" * 500
    log.emit_event(
        DiagnosticEvent(
            action="scenario.diagnostic.failure_classified",
            contract_id="AIYES-106",
            step_id="scroll-feed",
            failure_code="no_scrollable",
            diagnostic_summary=long_summary,
        )
    )
    classified = [
        e for e in log.events if e.action == "scenario.diagnostic.failure_classified"
    ]
    assert len(classified) == 1
    event = classified[0]
    assert event.contract_id == "AIYES-106"
    assert event.step_id == "scroll-feed"
    assert event.failure_code == "no_scrollable"
    # FC-CLASS-03: bounded first-line summary (<= 200, single line).
    assert event.diagnostic_summary is not None
    assert "\n" not in event.diagnostic_summary
    assert len(event.diagnostic_summary) <= 200
    assert "roots" not in event.diagnostic_summary


# ===========================================================================
# A10 rev0 REJECTION remediation — RED tests pinning the CORRECT behavior the
# A10 review found missing (A10-MED-001). Each block below carries a falsifying
# test for one of the six findings; they are RED against the current impl and
# pass once A9 implements the target design (pure domain shaper, recursive
# raw-tree strip, total diagnostic_summary, single LE-02 emission at the
# adapter/command boundary, smoke CLI --profile).
# ===========================================================================


# --------------------------------------------------------------------------- #
# A10-CRIT-002 — NESTED raw-tree leak: compact must recursively strip raw tree
# values under ANY nested path, not just a top-level output['tree'] / stdout.
# --------------------------------------------------------------------------- #


def _step_nested_tree_object() -> ScenarioRunStepResult:
    """A failed step carrying a raw tree OBJECT under output['details']['tree']."""
    return ScenarioRunStepResult(
        step_id="nested-object",
        kind="find",
        status="failed",
        output={
            "failure_class": "target_not_found_no_progress",
            "details": {
                "tree": {
                    "roots": [
                        {
                            "id": "n_010",
                            "role": "frame",
                            "children": [
                                {"id": "n_011", "role": "button", "name": "Go"},
                            ],
                        }
                    ]
                },
            },
        },
        error="required find matched zero nodes",
    )


def _step_deeply_nested_tree_nodelist() -> ScenarioRunStepResult:
    """A failed step carrying a raw tree NODE-LIST under a deep nested path."""
    return ScenarioRunStepResult(
        step_id="nested-list",
        kind="wait",
        status="failed",
        output={
            "timeout": True,
            "diagnostics": {
                "context": {
                    "snapshot": {
                        "tree": [
                            {
                                "id": "n_020",
                                "role": "frame",
                                "children": [{"id": "n_021", "role": "text"}],
                            }
                        ],
                    },
                },
            },
        },
        error="wait timed out",
    )


def test_compact_strips_nested_tree_object_under_details() -> None:
    """A10-CRIT-002 / FC-EVIDENCE-02: nested tree OBJECT removed recursively.

    The raw tree lives at output['details']['tree'] (NOT top-level
    output['tree']); the recursive predicate must reject it. RED today because
    _compact_output only strips a top-level 'tree' key.
    """
    run = ScenarioRunResult(
        scenario_id="aiyes107-nested-object",
        status="failed",
        mode="real",
        failure_code="assertion_failed",
        steps=(_step_nested_tree_object(),),
    )
    payload = _compact_payload(run)
    _assert_no_raw_tree(payload)


def test_compact_strips_deeply_nested_tree_nodelist() -> None:
    """A10-CRIT-002 / FC-EVIDENCE-02: deeply nested tree NODE-LIST removed.

    The raw node-list lives at
    output['diagnostics']['context']['snapshot']['tree']. RED today because the
    compact shaper does not walk nested mappings.
    """
    run = ScenarioRunResult(
        scenario_id="aiyes107-nested-list",
        status="failed",
        mode="real",
        failure_code="assertion_failed",
        steps=(_step_deeply_nested_tree_nodelist(),),
    )
    payload = _compact_payload(run)
    _assert_no_raw_tree(payload)


def test_compact_strips_nested_tree_in_bundle_steps_jsonl(
    tmp_path: Path,
) -> None:
    """A10-CRIT-002: the bundle steps.jsonl is also nested-raw-tree-safe.

    Exercises the file-output adapter path (write_scenario_evidence_bundle), not
    only the in-memory presenter, so the recursive strip is verified at every
    compact surface.
    """
    run = ScenarioRunResult(
        scenario_id="aiyes107-nested-bundle",
        status="failed",
        mode="real",
        failure_code="assertion_failed",
        steps=(_step_nested_tree_object(), _step_deeply_nested_tree_nodelist()),
    )
    bundle_dir = tmp_path / "bundle"
    write_scenario_evidence_bundle(bundle_dir, run, profile="compact")
    steps = [
        json.loads(line)
        for line in (bundle_dir / "steps.jsonl").read_text("utf-8").splitlines()
    ]
    for step in steps:
        _assert_no_raw_tree(step)


# --------------------------------------------------------------------------- #
# A10-CRIT-003 — diagnostic_summary is TOTAL for failed records: an empty error
# AND no failure_code/class AND run_failure_code None must still yield a
# non-empty single-line <= 200 deterministic ultimate fallback.
# --------------------------------------------------------------------------- #


def test_diagnostic_summary_total_for_failed_without_any_classification() -> None:
    """A10-CRIT-003 / FC-CLASS-03: failed + empty error + no code/class -> non-empty.

    diagnostic_summary('failed', '', None, None) currently returns '' (the
    ultimate-fallback branch is missing). RED until A9 adds a deterministic
    non-empty fallback.
    """
    summary = evidence_profile_module.diagnostic_summary("failed", "", None, None)
    assert summary != ""
    assert "\n" not in summary
    assert len(summary) <= 200


def test_compact_failed_record_without_classification_has_nonempty_summary() -> None:
    """A10-CRIT-003 / FC-CLASS-03: end-to-end through the compact shaper.

    A failed step with empty error and no output code/class AND a run with
    failure_code None must still carry a non-empty diagnostic_summary. RED until
    the total fallback exists. Uses ``status='failed'`` on the run to keep the
    run a valid failed run while leaving run_failure_code None.
    """
    run = ScenarioRunResult(
        scenario_id="aiyes107-total-summary",
        status="failed",
        mode="real",
        failure_code=None,  # run-level classifier absent
        steps=(
            ScenarioRunStepResult(
                step_id="unclassified",
                kind="action",
                status="failed",
                output={},  # no failure_code / failure_class
                error="",  # no error text
            ),
        ),
    )
    step = _compact_payload(run)["steps"][0]
    assert step["diagnostic_summary"] != ""
    assert "\n" not in step["diagnostic_summary"]
    assert len(step["diagnostic_summary"]) <= 200


def test_shape_step_record_total_summary_with_run_failure_code_none() -> None:
    """A10-CRIT-003: shape_step_record default run_failure_code=None stays total."""
    step_dict = {
        "step_id": "raw-unclassified",
        "kind": "assert",
        "status": "failed",
        "output": {},
        "error": "",
    }
    record = evidence_profile_module.shape_step_record(step_dict, "compact")
    assert record["diagnostic_summary"] != ""
    assert len(record["diagnostic_summary"]) <= 200


# --------------------------------------------------------------------------- #
# A10-CRIT-004 — LE-02 cardinality EXACTLY ONE end-to-end (production sink).
# --------------------------------------------------------------------------- #


def _le02_count(log: InMemoryDiagnosticLog) -> int:
    return len(_profile_events(log))


def _cli_scenario_file(tmp_path: Path, *, bundle: bool = True) -> Path:
    scenario_path = tmp_path / "cli-scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "cli-le02",
                "title": "CLI LE-02",
                "target": "linux",
                "steps": [{"id": "inspect", "kind": "inspect"}],
                "evidence_policy": {"bundle": bundle, "redact_environment": True},
            }
        ),
        encoding="utf-8",
    )
    return scenario_path


def test_cli_scenario_run_with_evidence_dir_emits_exactly_one_le02(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A10-CRIT-004 / FC-OBS-01: CLI --evidence-dir emits EXACTLY ONE LE-02.

    Both the bundle path (write_scenario_evidence_bundle) and the presenter
    path (format_scenario_run) are active and currently share the same
    production diagnostic log, so the selection emits TWICE today (RED). The
    target design centralizes emission to one per invocation.

    Drives the real CLI command with a fresh production InMemoryDiagnosticLog
    bound in place of the shared singleton (non-self-satisfying).
    """
    log = InMemoryDiagnosticLog()
    monkeypatch.setattr(cli_main_module, "_diagnostic_log", log, raising=True)

    scenario_path = _cli_scenario_file(tmp_path)
    evidence_dir = tmp_path / "evidence"
    result = CliRunner().invoke(
        cli,
        ["scenario", "run", "--evidence-dir", str(evidence_dir), str(scenario_path)],
    )

    assert result.exit_code == 0, result.output
    assert _le02_count(log) == 1, [e for e in log.events]
    assert log.emission_failure_count() == 0


def test_cli_scenario_run_without_evidence_dir_emits_exactly_one_le02(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A10-CRIT-004 / FC-OBS-01: CLI without --evidence-dir still emits one LE-02."""
    log = InMemoryDiagnosticLog()
    monkeypatch.setattr(cli_main_module, "_diagnostic_log", log, raising=True)

    scenario_path = _cli_scenario_file(tmp_path)
    result = CliRunner().invoke(cli, ["scenario", "run", str(scenario_path)])

    assert result.exit_code == 0, result.output
    assert _le02_count(log) == 1, [e for e in log.events]


def test_cli_scenario_run_is_fail_open_when_emit_event_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A10-CRIT-004 / FC-OBS-03: CLI scenario-run is fail-open when emit_event raises.

    The CLI boundary (_emit_profile_selection ~:133) calls
    _diagnostic_log.emit_event directly without any try/except guard.  When the
    sink's emit_event itself raises (as opposed to an internal store failure that
    the adapter swallows), the exception propagates up through
    scenario_run_cmd's outer try-block, is caught by the generic
    ``except Exception`` handler, and causes the command to exit with code 1
    (non-zero) — violating FC-OBS-03 (caller must never be blocked by a
    diagnostic failure).

    RED: monkeypatches _diagnostic_log with a stub whose emit_event raises
    unconditionally; asserts the CLI command still exits 0 (fail-open at the
    CLI boundary).  Currently fails because _emit_profile_selection has no
    try/except around the emit call.

    Note: InMemoryDiagnosticLog.emit_event already has its own internal
    try/except so an exploding-list injection does NOT reach the CLI boundary —
    a stub that raises at the emit_event call-site itself is required.

    Non-self-satisfying: drives the real CLI CliRunner, monkeypatching the
    module-level _diagnostic_log the way existing cardinality tests do.
    """

    class _RaisingSink:
        """Diagnostic sink stub whose emit_event raises unconditionally."""

        def emit_event(self, event: Any) -> None:
            raise RuntimeError("sink failure — CLI must not propagate this")

    monkeypatch.setattr(
        cli_main_module, "_diagnostic_log", _RaisingSink(), raising=True
    )

    scenario_path = _cli_scenario_file(tmp_path)
    result = CliRunner().invoke(cli, ["scenario", "run", str(scenario_path)])

    # FC-OBS-03: a diagnostic sink failure must never block the caller.
    assert result.exit_code == 0, (
        f"CLI exited {result.exit_code} — diagnostic emit_event raise propagated "
        f"to the caller; _emit_profile_selection is not fail-open.\n{result.output}"
    )


def test_cli_scenario_run_is_none_safe_for_diagnostic_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A10-CRIT-004 / FC-OBS-03: CLI scenario-run tolerates diagnostic_log=None.

    The MCP and smoke boundaries are already None-guarded; the CLI
    _emit_profile_selection function calls _diagnostic_log.emit_event directly
    without a None-check.  When _diagnostic_log is None, the call raises
    AttributeError, which propagates through scenario_run_cmd and causes exit 1
    — violating FC-OBS-03.

    RED: monkeypatches _diagnostic_log to None and asserts the command exits 0
    with no emission and no crash.  Currently fails because the CLI boundary
    is not None-safe.
    """
    monkeypatch.setattr(cli_main_module, "_diagnostic_log", None, raising=True)

    scenario_path = _cli_scenario_file(tmp_path)
    result = CliRunner().invoke(cli, ["scenario", "run", str(scenario_path)])

    # FC-OBS-03: None diagnostic_log must not block the caller.
    assert result.exit_code == 0, (
        f"CLI exited {result.exit_code} — None diagnostic_log propagated "
        f"AttributeError to the caller; _emit_profile_selection is not None-safe.\n"
        f"{result.output}"
    )


def _mcp_deps_with_log(log: InMemoryDiagnosticLog) -> ServerDependencies:
    """Build MCP deps wiring a PRODUCTION diagnostic log on the deps.

    The target design adds a ``diagnostic_log`` field to ServerDependencies and
    has _handle_scenario_run emit exactly one LE-02 through it. The field is set
    only when ServerDependencies declares it; before A9 it is absent, the log
    stays untouched, and the cardinality assertion fails cleanly (RED) instead
    of a constructor TypeError.
    """
    fields = {field: MagicMock() for field in ServerDependencies.__dataclass_fields__}
    fields["clock"].now.side_effect = [1.0, 1.1, 1.2, 1.3]
    fields["scenario_run_uc"] = ScenarioRunUseCaseStub()
    fields["scenario_real_run_uc"] = ScenarioRunUseCaseStub()
    fields["load_scenario_file"] = load_scenario_file
    fields["write_scenario_evidence_bundle"] = write_scenario_evidence_bundle
    if "diagnostic_log" in ServerDependencies.__dataclass_fields__:
        fields["diagnostic_log"] = log
    return ServerDependencies(**fields)


@pytest.mark.asyncio
async def test_mcp_scenario_run_emits_exactly_one_le02_via_production_sink(
    tmp_path: Path,
) -> None:
    """A10-CRIT-004 / FC-OBS-01: MCP scenario_run emits EXACTLY ONE LE-02.

    The MCP handler currently passes no diagnostic_log to either the presenter
    or the bundle writer, so it emits ZERO LE-02 events today (RED). The target
    design wires the production sink and emits once. Drives via
    create_mcp_server -> call_tool with a real InMemoryDiagnosticLog.
    """
    log = InMemoryDiagnosticLog()
    server = create_mcp_server(_mcp_deps_with_log(log))
    result = await server.call_tool(
        "scenario_run", {"scenario_path": str(_mcp_scenario_file(tmp_path))}
    )
    assert result.isError is False, result.content
    assert _le02_count(log) == 1, [e for e in log.events]
    assert log.emission_failure_count() == 0


@pytest.mark.asyncio
async def test_mcp_scenario_run_with_evidence_dir_emits_exactly_one_le02(
    tmp_path: Path,
) -> None:
    """A10-CRIT-004: MCP scenario_run with evidence_dir still emits one LE-02.

    Both the bundle writer and presenter are reachable; the centralized
    emission must remain exactly-once even when a bundle is written.
    """
    log = InMemoryDiagnosticLog()
    server = create_mcp_server(_mcp_deps_with_log(log))
    evidence_dir = tmp_path / "mcp-evidence"
    result = await server.call_tool(
        "scenario_run",
        {
            "scenario_path": str(_mcp_scenario_file(tmp_path)),
            "evidence_dir": str(evidence_dir),
        },
    )
    assert result.isError is False, result.content
    assert _le02_count(log) == 1, [e for e in log.events]


# --------------------------------------------------------------------------- #
# A10-HIGH-001 — smoke harness CLI --profile: smoke_harness.main / _parse_args
# accepts --profile {compact,deep} and passes it to run_smoke_harness.
# --------------------------------------------------------------------------- #


def test_smoke_parse_args_accepts_profile_option() -> None:
    """A10-HIGH-001 / FC-PROFILE-03: _parse_args exposes --profile {compact,deep}."""
    args = smoke_harness_module._parse_args(
        ["--target", "linux-gedit", "--profile", "deep"]
    )
    assert args.profile == "deep"


def test_smoke_parse_args_profile_defaults_to_compact() -> None:
    """A10-HIGH-001 / FC-PROFILE-01: omitting --profile yields compact default."""
    args = smoke_harness_module._parse_args(["--target", "linux-gedit"])
    assert getattr(args, "profile", "compact") == "compact"


def test_smoke_parse_args_rejects_bogus_profile() -> None:
    """A10-HIGH-001: out-of-enum --profile is rejected (argparse SystemExit)."""
    with pytest.raises(SystemExit):
        smoke_harness_module._parse_args(
            ["--target", "linux-gedit", "--profile", "bogus"]
        )


def test_smoke_main_passes_deep_profile_to_run_smoke_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A10-HIGH-001 / FC-PROFILE-03: smoke_harness.main honors --profile deep.

    The file-output CLI must forward the selected profile to run_smoke_harness;
    today main() never reads or passes a profile, so the captured profile stays
    the default 'compact' regardless of the flag (RED).
    """
    captured: Dict[str, Any] = {}

    def _fake_run_smoke_harness(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        captured["profile"] = kwargs.get("profile")
        return {"status": "passed", "profile": kwargs.get("profile")}

    monkeypatch.setattr(
        smoke_harness_module, "run_smoke_harness", _fake_run_smoke_harness
    )
    output_path = tmp_path / "evidence.json"
    exit_code = smoke_harness_module.main(
        [
            "--target",
            "linux-gedit",
            "--profile",
            "deep",
            "--output",
            str(output_path),
        ],
        environ={},
    )
    assert exit_code == 0
    assert captured["profile"] == "deep"


# --------------------------------------------------------------------------- #
# A10-CRIT-001 — domain purity: src/aiyes/domain/evidence_profile.py must NOT
# import any port/adapter sink and must expose NO emit/side-effecting function.
# --------------------------------------------------------------------------- #


def _evidence_profile_imported_modules() -> List[str]:
    """Collect every module name imported (incl. function-local) by the shaper."""
    source = inspect.getsource(evidence_profile_module)
    tree = ast.parse(source)
    modules: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def test_evidence_profile_module_imports_no_port_or_adapter() -> None:
    """A10-CRIT-001 / FC-ARCH-01: the shaper imports nothing from ports/adapters.

    Catches the current function-local ``from aiyes.domain.diagnostic_event
    import DiagnosticEvent`` only indirectly — the harder violation is the
    coupling to a sink. This guard forbids any aiyes.ports.* / aiyes.adapters.*
    import anywhere in the module (module-level or function-local). RED until A9
    removes the emission code from the domain shaper.
    """
    modules = _evidence_profile_imported_modules()
    offenders = [
        m
        for m in modules
        if m.startswith("aiyes.ports") or m.startswith("aiyes.adapters")
    ]
    assert offenders == [], f"domain shaper imports port/adapter sinks: {offenders}"


def test_evidence_profile_module_exposes_no_side_effecting_emit() -> None:
    """A10-CRIT-001 / FC-ARCH-01: no emit/side-effecting function on the shaper.

    The pure domain shaper must not own an ``emit_profile_selection`` (or any
    callable that drives a diagnostic_log sink). Emission belongs at the
    adapter/command boundary. RED while emit_profile_selection lives here.
    """
    assert not hasattr(evidence_profile_module, "emit_profile_selection"), (
        "domain shaper still exposes emit_profile_selection; emission must move "
        "to the adapter/command boundary"
    )


def test_evidence_profile_source_does_not_call_a_sink() -> None:
    """A10-CRIT-001: the shaper performs no sink emission (no observable effect).

    The shaper may BUILD the LE-02 payload purely (the target design has it
    "return a plain event payload that adapters emit"; constructing the
    DiagnosticEvent domain value object is pure). What it must NOT do is drive a
    diagnostic_log sink: no emit_event call and no diagnostic_log reference.
    RED while emit_profile_selection calls diagnostic_log.emit_event here.
    """
    source = inspect.getsource(evidence_profile_module)
    assert "emit_event" not in source, (
        "domain shaper calls emit_event; the sink call must move to an adapter"
    )
    assert "diagnostic_log" not in source, (
        "domain shaper references a diagnostic_log sink; emission must move to "
        "the adapter/command boundary"
    )
