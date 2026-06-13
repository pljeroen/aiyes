"""AIYES-103: required find consumption and source diagnostics (RED).

These tests pin the AIYES-103 behavior the implementation (A9) must deliver:

  R1  An action step that consumes an empty source find output FAILS (not
      passes) with a stable snake_case failure_code distinct from the legacy
      free-form "source step did not provide a node id", and structured
      diagnostic output carrying source_step_id, source_step_kind (find),
      requested_selector, and the reused selector_diagnostics. requested_selector
      is populated for EVERY find source — independent of whether the source
      find produced selector_diagnostics (FC-DIAG-06).
  R2  A find step with `required: true` and zero matched nodes FAILS at the
      find step; `required: false` / absent with zero nodes stays a passed
      observation. The policy is explicit on the step, not inferred from
      downstream consumption. Load-time validation rejects a non-bool
      `required` with a stable path/code.
  R3  Omitted / explicit-optional empty finds remain passed observations with
      nodes==[] + selector_diagnostics; run-level failure_code stays
      "executor_error". The classified per-step diagnostics survive
      serialization across the CLI JSON, MCP scenario_run response, and the
      evidence steps.jsonl surfaces with equal values (FC-SER-02).
  LE-01  Each AIYES-103 classified failure emits exactly one
      scenario.diagnostic.failure_classified event through the PRODUCTION
      DiagnosticEventPort adapter (src/aiyes/adapters/diagnostic_log.py::
      InMemoryDiagnosticLog); the emission is fail-open with an observable
      emission_failure_count() that the ADAPTER itself owns — a swallowed
      internal storage failure increments the count without raising.

Every test asserts the OBSERVABLE post-state (the result/output mapping, the
captured event, or the serialized surface), not merely a return value. The
observability tests exercise the PRODUCTION adapter (not a hand-rolled double)
so its redaction, retention, and adapter-owned fail-open count are proven for
the concrete sink wired by composition_root.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest

from aiyes.adapters.diagnostic_log import InMemoryDiagnosticLog
from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.cli.presenter import format_scenario_run
from aiyes.domain.diagnostic_event import DiagnosticEvent
from aiyes.domain.scenario import ScenarioStep, validate_scenario_document
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase


# OD-03: diagnostic_summary is a single line truncated at 200 chars.
SUMMARY_MAX_LEN = 200

# A stable snake_case identifier form, no interpolated step id / free text.
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")

_LEGACY_FREE_FORM = "source step did not provide a node id"


# ─── In-memory test doubles ───────────────────────────────────────────────


@dataclasses.dataclass
class RecordingInspect:
    """Returns successive inspect snapshots; an empty default tree."""

    trees: List[Any] = dataclasses.field(default_factory=list)
    calls: List[dict] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.trees:
            return SimpleNamespace(tree=self.trees.pop(0))
        return SimpleNamespace(tree=None)


class FakeSessionRepo:
    """Returns a session with a known resolution."""

    def __init__(self, resolution: str = "1080x2400") -> None:
        self._resolution = resolution

    def load(self, session_id: str) -> Any:
        return SimpleNamespace(
            session_id=session_id,
            resolution=self._resolution,
            backend="android",
            device_serial="",
        )


@dataclasses.dataclass
class RoleAwareFakeFindUseCase:
    """Returns results based on requested role; default empty."""

    results_by_role: dict = dataclasses.field(default_factory=dict)
    calls: List[dict] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        role = kwargs.get("role")
        bucket = self.results_by_role.get(role)
        if bucket:
            return bucket.pop(0)
        return []


@dataclasses.dataclass
class RecordingAction:
    """Records action.execute calls so we can assert it is NOT invoked."""

    calls: List[dict] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(status="ok")


class _RaisingStore(list):
    """A backing store whose append raises — an INTERNAL adapter failure.

    Injected into the PRODUCTION InMemoryDiagnosticLog (replacing its private
    _events list) so the adapter's OWN try/except fail-open path runs. The
    store NEVER touches the adapter's failure counter — the assertion only
    passes if the ADAPTER owns the increment (R-02 / R-03 / FC-OBS-02).
    """

    def append(self, _item: Any) -> None:
        raise RuntimeError("backing store down")


# ─── Executor construction ────────────────────────────────────────────────


def _build_executor(
    *,
    find: Any,
    inspect: Any,
    action: Any | None = None,
    diagnostic_log: Any | None = None,
) -> ScenarioUseCaseExecutor:
    """Build a started executor.

    diagnostic_log is passed via the keyword the implementation must accept.
    When None, the executor must run without emitting and without crashing.
    """
    start = SimpleNamespace(
        execute=lambda **kw: SimpleNamespace(session_id="s1", backend="android")
    )
    kwargs: dict[str, Any] = dict(
        session_start=start,
        inspect=inspect,
        find=find,
        action=action or RecordingAction(),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_repo=FakeSessionRepo(),
        clock=SimpleNamespace(now=lambda: 0.0),
    )
    if diagnostic_log is not None:
        kwargs["diagnostic_log"] = diagnostic_log
    executor = ScenarioUseCaseExecutor(**kwargs)
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "adb", "wait_seconds": 0.0, "backend": "android"},
        )
    )
    return executor


def _find_then_action_outputs(
    executor: ScenarioUseCaseExecutor,
    *,
    role: str = "View",
    name_pattern: str = "Target Markets",
) -> Any:
    """Run a find (empty) then an action consuming it; return action result."""
    executor.execute(
        ScenarioStep(
            id="find_target",
            kind="find",
            parameters={"role": role, "name_pattern": name_pattern},
        )
    )
    return executor.execute(
        ScenarioStep(
            id="tap_target",
            kind="action",
            parameters={"source": "find_target", "action": "click"},
        )
    )


def _required_find_run() -> Any:
    """Run a scenario whose only failing step is a required find with zero nodes.

    Returns the completed ScenarioRunResult; the failing step id is
    "must_find". Grounded against the real ScenarioRunUseCase + executor so the
    classified per-step output flows through run aggregation exactly as in
    production.
    """
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(), inspect=RecordingInspect()
    )
    document = {
        "schema_version": 1,
        "id": "scn",
        "title": "required find run",
        "target": "android",
        "prerequisites": [],
        "steps": [
            {
                "id": "start",
                "kind": "start_session",
                "command": "adb",
                "backend": "android",
            },
            {
                "id": "must_find",
                "kind": "find",
                "role": "View",
                "name_pattern": "Login",
                "required": True,
            },
            {"id": "stop", "kind": "stop_session"},
        ],
        "cleanup": [],
        "evidence_policy": {},
    }
    validated = validate_scenario_document(document)
    assert validated.ok, [dataclasses.asdict(i) for i in validated.issues]
    return ScenarioRunUseCase(executor=executor).execute(validated.scenario)


def _classified_step_output(run: Any) -> dict:
    """The must_find step output from a completed run."""
    failing = next(s for s in run.steps if s.step_id == "must_find")
    return dict(failing.output)


# =====================================================================
# R1 — consumed-empty action → classified failure (FC-DIAG-01..07, SER-01)
# =====================================================================


def test_action_consuming_empty_find_fails_and_does_not_invoke_action() -> None:
    """FC-DIAG-01: failed status; underlying action use case NOT invoked."""
    action = RecordingAction()
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),  # all roles → []
        inspect=RecordingInspect(),
        action=action,
    )

    result = _find_then_action_outputs(executor)

    assert result.status == "failed"
    assert action.calls == []  # action use case never invoked for the consumer


def test_action_failure_carries_nonempty_structured_output() -> None:
    """FC-DIAG-02: output is a non-empty Mapping, not output={}."""
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    result = _find_then_action_outputs(executor)

    from collections.abc import Mapping

    assert isinstance(result.output, Mapping)
    assert len(result.output) > 0


def test_action_failure_uses_stable_snake_case_failure_code() -> None:
    """FC-DIAG-03: stable snake_case code, distinct from legacy free-form."""
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    result = _find_then_action_outputs(executor)

    code = result.output["failure_code"]
    assert isinstance(code, str)
    assert code != ""
    assert _LEGACY_FREE_FORM not in code
    assert _SNAKE_CASE.match(code), code


def test_action_failure_code_is_constant_not_interpolated() -> None:
    """FC-DIAG-03: the same code value for every consumed-empty action."""
    code_a = _find_then_action_outputs(
        _build_executor(find=RoleAwareFakeFindUseCase(), inspect=RecordingInspect()),
        name_pattern="Alpha",
    ).output["failure_code"]
    code_b = _find_then_action_outputs(
        _build_executor(find=RoleAwareFakeFindUseCase(), inspect=RecordingInspect()),
        name_pattern="Bravo Different Selector",
    ).output["failure_code"]

    assert code_a == code_b  # constant, not interpolated with selector/source


def test_action_failure_names_source_step_id() -> None:
    """FC-DIAG-04: output.source_step_id equals action params.source."""
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    result = _find_then_action_outputs(executor)

    assert result.output["source_step_id"] == "find_target"


def test_action_failure_carries_source_step_kind_find() -> None:
    """FC-DIAG-05: source_step_kind recoverable and present for a find source."""
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    result = _find_then_action_outputs(executor)

    assert result.output["source_step_kind"] == "find"


def test_action_failure_propagates_requested_selector_triple() -> None:
    """FC-DIAG-06: requested_selector (role, name_pattern, state) from source find."""
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    result = _find_then_action_outputs(
        executor, role="View", name_pattern="Target Markets"
    )

    assert result.output["requested_selector"] == {
        "role": "View",
        "name_pattern": "Target Markets",
        "state": None,
    }


def test_action_failure_requested_selector_present_for_find_source_without_name_pattern() -> (
    None
):
    """FC-DIAG-06 (R-01 close): requested_selector is populated for EVERY find
    source, independent of selector_diagnostics presence.

    A find with a role but NO name_pattern produces no selector_diagnostics
    (FC-DIAG-07 governs that absence). The rev0 implementation derived
    requested_selector ONLY from the embedded selector_diagnostics, so this
    consumed-empty action omitted requested_selector even though its source
    kind is find — the CRITICAL R-01 gap. requested_selector MUST be present
    and carry the find's selector triple, with selector_diagnostics still
    absent (it was never produced).
    """
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    find_result = executor.execute(
        ScenarioStep(
            id="find_target",
            kind="find",
            parameters={"role": "View"},  # role only → no selector_diagnostics
        )
    )
    # Precondition: the source find genuinely produced no selector_diagnostics.
    assert "selector_diagnostics" not in find_result.output

    action_result = executor.execute(
        ScenarioStep(
            id="tap_target",
            kind="action",
            parameters={"source": "find_target", "action": "click"},
        )
    )

    assert action_result.status == "failed"
    assert action_result.output["source_step_kind"] == "find"
    # requested_selector is present DESPITE the missing selector_diagnostics.
    assert action_result.output["requested_selector"] == {
        "role": "View",
        "name_pattern": None,
        "state": None,
    }
    # selector_diagnostics is NOT fabricated (the source never had one).
    assert "selector_diagnostics" not in action_result.output


def test_action_failure_includes_source_selector_diagnostics() -> None:
    """FC-DIAG-07: source selector_diagnostics reused verbatim into action output.

    The source find produces selector_diagnostics (role drift: a Button exists
    where a View was requested). The consumed-empty action failure must carry
    the SAME diagnostics object (structural equality) — reused, not recomputed.
    """
    inspect = RecordingInspect(
        trees=[
            {
                "roots": [
                    {
                        "id": "target_markets",
                        "role": "Button",
                        "name": "Target Markets",
                        "bounds": [0, 0, 100, 50],
                        "actions": ["click"],
                    }
                ]
            }
        ]
    )
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=inspect,
    )

    # Run the find first so its selector_diagnostics are stored.
    find_result = executor.execute(
        ScenarioStep(
            id="find_target",
            kind="find",
            parameters={"role": "View", "name_pattern": "Target Markets"},
        )
    )
    source_diag = find_result.output["selector_diagnostics"]

    action_result = executor.execute(
        ScenarioStep(
            id="tap_target",
            kind="action",
            parameters={"source": "find_target", "action": "click"},
        )
    )

    assert action_result.status == "failed"
    assert action_result.output["selector_diagnostics"] == source_diag


def test_action_failure_does_not_fabricate_selector_diagnostics() -> None:
    """FC-DIAG-07: when the source find had no selector_diagnostics, the action
    failure must NOT fabricate one (key absent).

    An empty name_pattern produces no selector_diagnostics on the find output.
    """
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    find_result = executor.execute(
        ScenarioStep(
            id="find_target",
            kind="find",
            parameters={"role": "*"},  # no name_pattern → no diagnostics
        )
    )
    assert "selector_diagnostics" not in find_result.output

    action_result = executor.execute(
        ScenarioStep(
            id="tap_target",
            kind="action",
            parameters={"source": "find_target", "action": "click"},
        )
    )

    assert action_result.status == "failed"
    assert "selector_diagnostics" not in action_result.output


# =====================================================================
# R2 — explicit required/optional find policy (FC-FIND-01/02/03/04)
# =====================================================================


def test_required_find_with_zero_nodes_fails_at_find_step() -> None:
    """FC-FIND-01: required find, zero nodes → failed at the find step."""
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    result = executor.execute(
        ScenarioStep(
            id="must_find",
            kind="find",
            parameters={"role": "View", "name_pattern": "Login", "required": True},
        )
    )

    assert result.status == "failed"


def test_required_find_failure_carries_failure_code_and_diagnostics() -> None:
    """FC-FIND-02: required-find failure has a stable code + selector_diagnostics."""
    inspect = RecordingInspect(
        trees=[
            {
                "roots": [
                    {
                        "id": "login_btn",
                        "role": "Button",
                        "name": "Login",
                        "bounds": [0, 0, 100, 50],
                        "actions": ["click"],
                    }
                ]
            }
        ]
    )
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="must_find",
            kind="find",
            parameters={"role": "View", "name_pattern": "Login", "required": True},
        )
    )

    assert result.status == "failed"
    code = result.output["failure_code"]
    assert _SNAKE_CASE.match(code), code
    assert "selector_diagnostics" in result.output


def test_required_policy_decided_in_isolation_not_from_downstream() -> None:
    """FC-FIND-03: the find outcome depends only on its own `required` field.

    Executed in isolation (no downstream action). required:true → failed;
    required absent → passed; required:false → passed.
    """
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
    )

    required = executor.execute(
        ScenarioStep(
            id="req",
            kind="find",
            parameters={"role": "View", "name_pattern": "X", "required": True},
        )
    )
    absent = executor.execute(
        ScenarioStep(
            id="absent",
            kind="find",
            parameters={"role": "View", "name_pattern": "X"},
        )
    )
    optional = executor.execute(
        ScenarioStep(
            id="opt",
            kind="find",
            parameters={"role": "View", "name_pattern": "X", "required": False},
        )
    )

    assert required.status == "failed"
    assert absent.status == "passed"
    assert optional.status == "passed"


def _find_document(required_value: Any, *, include_required: bool = True) -> dict:
    """A minimal valid scenario document with one find step.

    The find step optionally carries a `required` key so load-time validation
    (FC-FIND-04) can be exercised end to end.
    """
    find_step: dict[str, Any] = {
        "id": "find_login",
        "kind": "find",
        "role": "View",
        "name_pattern": "Login",
    }
    if include_required:
        find_step["required"] = required_value
    return {
        "schema_version": 1,
        "id": "scn",
        "title": "find policy scenario",
        "target": "android",
        "prerequisites": [],
        "steps": [
            {
                "id": "start",
                "kind": "start_session",
                "command": "adb",
                "backend": "android",
            },
            find_step,
            {"id": "stop", "kind": "stop_session"},
        ],
        "cleanup": [],
        "evidence_policy": {},
    }


def test_find_required_absent_validates_optional_and_strips_no_policy() -> None:
    """FC-FIND-04 CASE A: absent `required` → ok, key not on parameters."""
    document = _find_document(None, include_required=False)

    result = validate_scenario_document(document)

    assert result.ok, [dataclasses.asdict(i) for i in result.issues]
    find_step = next(s for s in result.scenario.steps if s.kind == "find")
    assert "required" not in find_step.parameters


@pytest.mark.parametrize("value", [True, False])
def test_find_required_bool_validates_and_is_recoverable(value: bool) -> None:
    """FC-FIND-04 CASE B: bool `required` → ok, recoverable on parameters."""
    document = _find_document(value)

    result = validate_scenario_document(document)

    assert result.ok, [dataclasses.asdict(i) for i in result.issues]
    find_step = next(s for s in result.scenario.steps if s.kind == "find")
    assert find_step.parameters["required"] == value


@pytest.mark.parametrize("bad", ["yes", 1, 0, ["true"], {"v": True}])
def test_find_required_non_bool_rejected_with_stable_path_and_code(bad: Any) -> None:
    """FC-FIND-04 CASE C: non-bool `required` → rejected, scenario None.

    Stable path steps[i].required, stable code find_required_not_boolean.
    """
    document = _find_document(bad)

    result = validate_scenario_document(document)

    assert result.ok is False
    assert result.scenario is None
    issue = next(
        (i for i in result.issues if i.code == "find_required_not_boolean"), None
    )
    assert issue is not None, [dataclasses.asdict(i) for i in result.issues]
    # find step is at steps[1] in _find_document.
    assert issue.path == "steps[1].required"
    assert _SNAKE_CASE.match(issue.code), issue.code


# =====================================================================
# R3 — observation-style finds remain passing (FC-COMPAT-01/02/03)
# =====================================================================


def test_omitted_required_empty_find_remains_passed_observation() -> None:
    """FC-COMPAT-01: omitted `required`, zero nodes → passed, nodes==[],
    selector_diagnostics preserved when name_pattern is non-empty."""
    inspect = RecordingInspect(
        trees=[
            {
                "roots": [
                    {
                        "id": "target_markets",
                        "role": "Button",
                        "name": "Target Markets",
                        "bounds": [0, 0, 100, 50],
                        "actions": ["click"],
                    }
                ]
            }
        ]
    )
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="find_target",
            kind="find",
            parameters={"role": "View", "name_pattern": "Target Markets"},
        )
    )

    assert result.status == "passed"
    assert result.output["nodes"] == []
    assert "selector_diagnostics" in result.output
    assert "failure_code" not in result.output


def test_explicit_optional_empty_find_is_passed_without_failure_code() -> None:
    """FC-COMPAT-02: required:false, zero nodes → passed, no failure_code,
    selector_diagnostics preserved."""
    inspect = RecordingInspect(
        trees=[
            {
                "roots": [
                    {
                        "id": "target_markets",
                        "role": "Button",
                        "name": "Target Markets",
                        "bounds": [0, 0, 100, 50],
                        "actions": ["click"],
                    }
                ]
            }
        ]
    )
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=inspect,
    )

    result = executor.execute(
        ScenarioStep(
            id="find_target",
            kind="find",
            parameters={
                "role": "View",
                "name_pattern": "Target Markets",
                "required": False,
            },
        )
    )

    assert result.status == "passed"
    assert result.output["nodes"] == []
    assert "failure_code" not in result.output
    assert "selector_diagnostics" in result.output


def test_run_level_failure_code_stays_executor_error_with_step_classification() -> None:
    """FC-COMPAT-03 / FC-SER-01: run-level failure_code stays "executor_error",
    while the failing step's output carries the AIYES-103 classification."""
    run = _required_find_run()

    assert run.status == "failed"
    assert run.failure_code == "executor_error"
    failing = next(s for s in run.steps if s.step_id == "must_find")
    assert "failure_code" in failing.output
    assert _SNAKE_CASE.match(failing.output["failure_code"])
    assert failing.output["source_step_kind"] == "find" or failing.kind == "find"


# =====================================================================
# FC-SER-02 — cross-surface preservation (R-05 close)
# CLI format_scenario_run / MCP scenario_run / evidence steps.jsonl
# =====================================================================

# The AIYES-103 diagnostic keys that MUST survive each serialization surface
# for the classified required-find failure (it has no requested_selector /
# selector_diagnostics because name_pattern was non-empty but no candidates
# exist; failure_code + source attribution are the load-bearing keys).
_AIYES103_PRESERVED_KEYS = ("failure_code", "source_step_kind")


def test_classified_step_preserved_through_cli_format_scenario_run() -> None:
    """FC-SER-02: the CLI JSON object for a classified AIYES-103 failing step
    carries the failure_code + diagnostic fields with equal values."""
    run = _required_find_run()
    step_output = _classified_step_output(run)

    cli_json = json.loads(format_scenario_run(run))
    cli_step = next(s for s in cli_json["steps"] if s["step_id"] == "must_find")

    for key in _AIYES103_PRESERVED_KEYS:
        assert key in step_output, key
        assert cli_step["output"][key] == step_output[key], key
    assert (
        cli_step["output"]["failure_code"]
        == run.steps[
            next(i for i, s in enumerate(run.steps) if s.step_id == "must_find")
        ].output["failure_code"]
    )


def test_classified_step_preserved_through_mcp_scenario_run_response() -> None:
    """FC-SER-02: the MCP scenario_run response for a classified AIYES-103
    failing step carries the failure_code + diagnostic fields with equal values.

    Drives the real MCP handler (create_mcp_server -> call_tool('scenario_run'))
    end to end: load -> run -> format. The run use case is grounded against the
    real ScenarioRunUseCase + executor."""
    from aiyes.adapters.mcp_server import create_mcp_server, ServerDependencies

    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(), inspect=RecordingInspect()
    )
    document = {
        "schema_version": 1,
        "id": "scn",
        "title": "required find run",
        "target": "android",
        "prerequisites": [],
        "steps": [
            {
                "id": "start",
                "kind": "start_session",
                "command": "adb",
                "backend": "android",
            },
            {
                "id": "must_find",
                "kind": "find",
                "role": "View",
                "name_pattern": "Login",
                "required": True,
            },
            {"id": "stop", "kind": "stop_session"},
        ],
        "cleanup": [],
        "evidence_policy": {},
    }
    validated = validate_scenario_document(document)
    assert validated.ok, [dataclasses.asdict(i) for i in validated.issues]
    run_uc = ScenarioRunUseCase(executor=executor)

    fields = {f.name: None for f in dataclasses.fields(ServerDependencies)}
    fields["clock"] = SimpleNamespace(now=lambda: 0.0)
    fields["operation_log"] = SimpleNamespace(append=lambda _r: None)
    fields["resolve_session_id"] = lambda s: s or "s1"
    fields["scenario_run_uc"] = run_uc
    fields["scenario_real_run_uc"] = run_uc
    fields["load_scenario_file"] = lambda _path, public_fixture=False: validated
    fields["write_scenario_evidence_bundle"] = write_scenario_evidence_bundle
    deps = ServerDependencies(**fields)
    server = create_mcp_server(deps)

    result = asyncio.run(server.call_tool("scenario_run", {"scenario_path": "x.yaml"}))
    text = result.content[0].text
    mcp_payload = json.loads(text)
    mcp_step = next(s for s in mcp_payload["steps"] if s["step_id"] == "must_find")

    # Reference: the same run aggregated directly.
    reference = run_uc.execute(validated.scenario)
    ref_output = _classified_step_output(reference)
    for key in _AIYES103_PRESERVED_KEYS:
        assert key in mcp_step["output"], key
        assert mcp_step["output"][key] == ref_output[key], key


def test_classified_step_preserved_through_evidence_steps_jsonl(tmp_path: Path) -> None:
    """FC-SER-02: the evidence steps.jsonl record for a classified AIYES-103
    failing step carries the failure_code + diagnostic fields with equal values."""
    run = _required_find_run()
    step_output = _classified_step_output(run)

    write_scenario_evidence_bundle(tmp_path, run)
    lines = [
        json.loads(line)
        for line in (tmp_path / "steps.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    jsonl_step = next(s for s in lines if s["step_id"] == "must_find")

    for key in _AIYES103_PRESERVED_KEYS:
        assert key in step_output, key
        assert jsonl_step["output"][key] == step_output[key], key


# =====================================================================
# LE-01 — diagnostic emission through the PRODUCTION adapter (FC-OBS-01/02)
# =====================================================================


def test_production_adapter_emits_exactly_one_le01_event() -> None:
    """FC-OBS-01: the PRODUCTION InMemoryDiagnosticLog records exactly one
    scenario.diagnostic.failure_classified event with the LE-01 payload when
    the executor classifies a required-find failure; diagnostic_summary is the
    adapter-redacted single line truncated to 200 chars."""
    log = InMemoryDiagnosticLog()
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
        diagnostic_log=log,
    )

    result = executor.execute(
        ScenarioStep(
            id="must_find",
            kind="find",
            parameters={"role": "View", "name_pattern": "Login", "required": True},
        )
    )

    assert result.status == "failed"
    assert len(log.events) == 1
    event = log.events[0]
    assert isinstance(event, DiagnosticEvent)
    assert event.action == "scenario.diagnostic.failure_classified"
    assert event.contract_id == "AIYES-103"
    assert event.step_id == "must_find"
    assert event.failure_code == result.output["failure_code"]
    summary = event.diagnostic_summary
    assert isinstance(summary, str)
    if summary:
        assert summary == summary.splitlines()[0][:SUMMARY_MAX_LEN]
    assert len(summary) <= SUMMARY_MAX_LEN


def test_production_adapter_redacts_multiline_oversized_summary() -> None:
    """FC-OBS-01: the PRODUCTION adapter enforces redaction itself — a
    multi-line, oversized diagnostic_summary is stored as a single line
    truncated to SUMMARY_MAX_LEN, regardless of what the caller passed."""
    log = InMemoryDiagnosticLog()
    raw = "first line of detail" + ("x" * 400) + "\nsecond line\nthird line"

    log.emit_event(
        DiagnosticEvent(
            action="scenario.diagnostic.failure_classified",
            contract_id="AIYES-103",
            step_id="s",
            failure_code="required_find_no_nodes",
            diagnostic_summary=raw,
        )
    )

    assert len(log.events) == 1
    stored = log.events[0].diagnostic_summary
    assert "\n" not in stored
    assert len(stored) <= SUMMARY_MAX_LEN
    assert stored == raw.splitlines()[0][:SUMMARY_MAX_LEN]
    assert log.emission_failure_count() == 0  # a normal emit is not a failure


def test_production_adapter_retains_only_bounded_event_history() -> None:
    """FC-OBS-01: the PRODUCTION adapter keeps a bounded event history
    (retention cap), dropping the oldest beyond the cap rather than growing
    unbounded. Exceeding the cap must not raise nor count as a failure."""
    log = InMemoryDiagnosticLog()
    # Emit comfortably past the documented retention cap (1000).
    total = 1200
    for i in range(total):
        log.emit_event(
            DiagnosticEvent(
                action="scenario.diagnostic.failure_classified",
                contract_id="AIYES-103",
                step_id=f"s{i}",
                failure_code="required_find_no_nodes",
                diagnostic_summary="x",
            )
        )

    assert log.emission_failure_count() == 0
    assert 0 < len(log.events) < total  # bounded, oldest dropped
    # The most recent emission is retained.
    assert log.events[-1].step_id == f"s{total - 1}"


def test_production_adapter_fail_open_counts_internal_failure_through_executor() -> (
    None
):
    """FC-OBS-02 (R-02 / R-03 close): the PRODUCTION adapter OWNS the fail-open
    count. An INTERNAL storage failure (the adapter's backing store append
    raises) is swallowed by the adapter — execution outcome is unchanged AND
    the adapter's emission_failure_count increments by exactly one.

    The store NEVER touches the counter, so this assertion passes only if the
    adapter increments its own count. The rev0 executor catches emit_event
    exceptions and does nothing; this test proves the count is adapter-owned,
    not executor-owned, and is NOT self-satisfying."""
    log = InMemoryDiagnosticLog()
    # Inject an internal adapter failure: the private backing store append raises.
    log._events = _RaisingStore()  # noqa: SLF001 — deliberately exercise the adapter path
    assert log.emission_failure_count() == 0

    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
        diagnostic_log=log,
    )

    # Reference outcome with a healthy production adapter (no injected failure).
    reference = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
        diagnostic_log=InMemoryDiagnosticLog(),
    ).execute(
        ScenarioStep(
            id="must_find",
            kind="find",
            parameters={"role": "View", "name_pattern": "Login", "required": True},
        )
    )

    result = executor.execute(
        ScenarioStep(
            id="must_find",
            kind="find",
            parameters={"role": "View", "name_pattern": "Login", "required": True},
        )
    )

    # Same status and output as the healthy sink; no exception propagated.
    assert result.status == reference.status == "failed"
    assert dict(result.output) == dict(reference.output)
    # The ADAPTER owned the increment on its swallowed internal failure.
    assert log.emission_failure_count() == 1


def test_production_adapter_fail_open_counts_internal_failure_direct() -> None:
    """FC-OBS-02: the PRODUCTION adapter's emit_event never raises and counts
    its own internal failure, exercised directly (no executor).

    A swallow-and-count adapter is the canonical fail-open design — this unit
    test pins it on the concrete sink wired by composition_root."""
    log = InMemoryDiagnosticLog()
    log._events = _RaisingStore()  # noqa: SLF001
    assert log.emission_failure_count() == 0

    event = DiagnosticEvent(
        action="scenario.diagnostic.failure_classified",
        contract_id="AIYES-103",
        step_id="s",
        failure_code="required_find_no_nodes",
        diagnostic_summary="detail",
    )

    # Must NOT raise despite the internal store failure.
    log.emit_event(event)

    assert log.emission_failure_count() == 1


def test_no_logger_injected_means_no_emission_and_no_crash() -> None:
    """FC-OBS-01 wiring: when no logger is injected (None), classification still
    happens, no emission occurs, and nothing crashes."""
    executor = _build_executor(
        find=RoleAwareFakeFindUseCase(),
        inspect=RecordingInspect(),
        diagnostic_log=None,
    )

    result = executor.execute(
        ScenarioStep(
            id="must_find",
            kind="find",
            parameters={"role": "View", "name_pattern": "Login", "required": True},
        )
    )

    assert result.status == "failed"
    assert "failure_code" in result.output
