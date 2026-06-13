"""AIYES-106: No-scrollable Flutter accessibility guidance (RED).

When scroll_into_view exhausts its attempts because NO accessible scrollable
region was ever exposed (every attempt fell back to a raw viewport swipe with
selected_scrollable_id null) AND the accessibility tree never changed across
those attempts (before/after fingerprints equal, progress == "unchanged"), the
emitted failure output MUST carry a bounded, app-side accessibility-exposure
guidance block that tells an app author what to investigate.

This file authors the RED tests for the contract. The guidance/emission
behaviour does not exist yet, so these tests are expected to FAIL until the
GREEN implementation lands.

Ground truth (src/aiyes/adapters/scenario_use_case_executor.py):
  * _scroll_into_view_failure assembles the failed output dict and computes
    progress + failure_class BEFORE building it.
  * _scroll_failure_class returns "no_scrollable" only AFTER bound_hit !=
    "no_progress" and progress != "unknown", then iff scroll_attempts is
    non-empty and every attempt.selected_scrollable_id is None.
  * _final_scroll_progress returns "unchanged" iff >=1 attempt and all attempt
    progress values are "unchanged".
  * selected_scrollable_id is None on the viewport_swipe branch (no scrollable
    candidate selected) and scrollable.node_id on the region-swipe branch.

Constraints exercised: FC-DIAG-01/02/03/04/05/06/07/08, FC-SCOPE-01,
FC-LOG-01, FC-LOG-02 (FORMAL_CONSTRAINT_MAP.yaml).
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any

import pytest

from aiyes.adapters.diagnostic_log import InMemoryDiagnosticLog
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.domain.scenario import ScenarioStep
from aiyes.domain.tree import AccessibilityTree, Node

# AIYES-106 normative constants (FORMAL_CONSTRAINT_MAP meta.constants; OD-04/03).
GUIDANCE_MAX_BYTES = 2048
SUMMARY_MAX_LEN = 200

# The contract's dedicated guidance discriminator (FC-DIAG-01).
GUIDANCE_KIND = "no_scrollable_accessibility_exposure"


# ─── Fakes (house style mirrored from tests/test_aiyes49_scroll_into_view.py) ──


@dataclasses.dataclass
class StepClock:
    """Monotonically advances by `step` seconds per now() call."""

    step: float = 0.1
    t: float = 0.0

    def now(self) -> float:
        self.t += self.step
        return self.t


@dataclasses.dataclass
class FakeFindUseCase:
    """Returns successive find result lists, one per call."""

    results: list[list[Any]]
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if kwargs.get("role") == "*":
            return []
        if self.results:
            return self.results.pop(0)
        return []


@dataclasses.dataclass
class RoleAwareFakeFindUseCase:
    """Returns results based on requested role (role-drift exercising)."""

    exact_role: str
    exact_results: list[list[Any]]
    wildcard_results: list[list[Any]]
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        role = kwargs.get("role")
        if role == self.exact_role and self.exact_results:
            return self.exact_results.pop(0)
        if role == "*" and self.wildcard_results:
            return self.wildcard_results.pop(0)
        return []


@dataclasses.dataclass
class RecordingGesture:
    """Records swipe calls."""

    swipe_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    events: list[str] = dataclasses.field(default_factory=list)

    def swipe(
        self,
        session_id: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> Any:
        self.events.append("region_swipe")
        self.swipe_calls.append(
            {
                "session_id": session_id,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "duration_ms": duration_ms,
            }
        )
        return SimpleNamespace(status="ok")

    def pinch(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("pinch not expected")

    def two_finger_scroll(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("two_finger_scroll not expected")


@dataclasses.dataclass
class RecordingInspect:
    """Returns successive inspect snapshots."""

    trees: list[Any]
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.trees:
            return SimpleNamespace(tree=self.trees.pop(0))
        return SimpleNamespace(tree=None)


class FakeSessionRepo:
    """Returns a session with a known resolution."""

    def __init__(
        self,
        resolution: str = "1080x1920",
        backend: str = "android",
        device_serial: str = "",
    ) -> None:
        self._resolution = resolution
        self._backend = backend
        self._device_serial = device_serial

    def load(self, session_id: str) -> Any:
        return SimpleNamespace(
            session_id=session_id,
            resolution=self._resolution,
            backend=self._backend,
            device_serial=self._device_serial,
        )


class RaisingDiagnosticLog:
    """A DiagnosticEventPort whose emit_event self-counts an INTERNAL failure.

    Non-self-satisfying: the failure is injected at the sink's internal store
    (an append that raises), exercised through the PRODUCTION-shaped fail-open
    contract — emit_event swallows and self-increments, never raising to the
    caller (ports/diagnostic_event.py invariant). Used to verify the executor
    leaves the step result byte-identical (OD-05 fail-open).
    """

    def __init__(self) -> None:
        self._failures = 0

    def emit_event(self, event: Any) -> None:
        try:
            raise RuntimeError("internal store failure")
        except Exception:
            self._failures += 1

    def emission_failure_count(self) -> int:
        return self._failures


# ─── Helpers ──────────────────────────────────────────────────────────────


def _node(node_id: str = "n", bounds: list[int] | None = None) -> dict:
    return {
        "id": node_id,
        "role": "button",
        "name": "Developer",
        "bounds": bounds or [0, 0, 100, 50],
    }


def _candidate(node_id: str, role: str, name: str) -> dict:
    return {
        "id": node_id,
        "role": role,
        "name": name,
        "bounds": [0, 0, 100, 50],
        "actions": [],
    }


def _tree(*nodes: Node) -> AccessibilityTree:
    return AccessibilityTree(roots=nodes)


def _tree_node(
    node_id: str,
    role: str,
    bounds: tuple[int, int, int, int],
    *,
    actions: tuple[str, ...] = (),
    states: tuple[str, ...] = (),
    children: tuple[Node, ...] = (),
) -> Node:
    return Node(
        id=node_id,
        role=role,
        name=node_id,
        bounds=bounds,
        states=states,
        actions=actions,
        children=children,
    )


def _executor(
    *,
    find: Any,
    gesture: Any,
    session_repo: Any,
    clock: Any,
    inspect: Any | None = None,
    native_scroll: Any | None = None,
    diagnostic_log: Any | None = None,
) -> ScenarioUseCaseExecutor:
    start = SimpleNamespace(
        execute=lambda **kwargs: SimpleNamespace(session_id="s1", backend="android")
    )
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=inspect or SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=find,
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        gesture=gesture,
        native_scroll=native_scroll,
        session_repo=session_repo,
        clock=clock,
        diagnostic_log=diagnostic_log,
    )
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "adb", "wait_seconds": 0.0, "backend": "android"},
        )
    )
    return executor


def _scroll_step(**params: Any) -> ScenarioStep:
    base: dict[str, Any] = {"role": "button", "name_pattern": "Developer"}
    base.update(params)
    return ScenarioStep(id="reach", kind="scroll_into_view", parameters=base)


def _run_no_scrollable_unchanged(diagnostic_log: Any | None = None):
    """Drive the eligible no_scrollable + unchanged failure (ground-truth).

    Static tree with NO scrollable node => every attempt falls back to a raw
    viewport swipe (selected_scrollable_id None) and the tree fingerprint never
    changes => progress "unchanged", failure_class "no_scrollable".
    """
    static_tree = _tree(_tree_node("root", "frame", (0, 0, 1080, 2400)))
    inspect = RecordingInspect(
        trees=[static_tree, static_tree, static_tree, static_tree]
    )
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
        diagnostic_log=diagnostic_log,
    )
    return executor.execute(_scroll_step(max_scrolls=2))


def _guidance(result: Any) -> Any:
    assert "guidance" in result.output, (
        "FC-DIAG-01: eligible no_scrollable-unchanged failure MUST carry a "
        "guidance block"
    )
    return result.output["guidance"]


# ════════════════════════════════════════════════════════════════════════════
# FC-DIAG-01 / FC-DIAG-02 — guidance presence + app-side lexical shape
# (AIYES-106-T1; PO-01)
# ════════════════════════════════════════════════════════════════════════════


def test_no_scrollable_unchanged_failure_carries_guidance_block() -> None:
    result = _run_no_scrollable_unchanged()

    # Ground the trigger in the real classifier.
    assert result.status == "failed"
    assert result.output["failure_class"] == "no_scrollable"
    assert result.output["progress"] == "unchanged"
    assert result.output["scroll_attempts"]
    assert all(
        attempt["selected_scrollable_id"] is None
        for attempt in result.output["scroll_attempts"]
    )

    guidance = _guidance(result)
    assert isinstance(guidance, dict)
    assert guidance.get("kind") == GUIDANCE_KIND


def test_guidance_names_scrollable_accessibility_and_app_side_remedy() -> None:
    guidance = _guidance(_run_no_scrollable_unchanged())

    summary = guidance.get("summary")
    assert isinstance(summary, str) and summary
    assert "scrollable" in summary.lower()
    assert "accessib" in summary.lower()

    action = guidance.get("recommended_action")
    assert isinstance(action, str) and action
    action_l = action.lower()
    # App/widget-side remedy: expose a Scrollable / scroll semantics — NOT an
    # instruction for aiyes to retry/swipe.
    assert ("scrollable" in action_l) or ("semantic" in action_l)
    assert "retry" not in action_l
    assert "swipe" not in action_l

    # No low-level swipe-coordinate / fingerprint payload inside the guidance.
    blob = json.dumps(guidance)
    assert "coordinates" not in blob
    assert "tree_fingerprint" not in blob


# ════════════════════════════════════════════════════════════════════════════
# FC-DIAG-08 — truthfulness BEYOND lexical markers (AIYES-106-T1b; PO-01)
# ════════════════════════════════════════════════════════════════════════════


def _truthfulness_holds(guidance: Any, *, status: str, found: Any) -> bool:
    """The FC-DIAG-08 truthfulness predicate over emitted guidance.

    Mirrors the FORMAL_CONSTRAINT_MAP FC-DIAG-08 predicate so a counter-example
    test can prove lexical-only is insufficient.
    """
    text = (
        str(guidance.get("summary", ""))
        + " "
        + str(guidance.get("recommended_action", ""))
        + " "
        + str(guidance.get("kind", ""))
    ).lower()
    # (a) failure classification preserved — NOT presented as success.
    if status != "failed":
        return False
    if found is not False:
        return False
    if guidance.get("step_outcome", "failed") != "failed":
        return False
    if any(
        tok in text
        for tok in (
            "succeed",
            "success",
            "passed",
            "works as expected",
            "driver ok",
            "no issue",
        )
    ):
        return False
    # (b) causality hedged — NOT a proven/confirmed defect claim.
    if any(
        phrase in text
        for phrase in (
            "proven flutter",
            "confirmed flutter",
            "flutter bug",
            "flutter defect",
            "definitely a flutter",
            "is a flutter defect",
            "guaranteed",
            "proves a defect",
        )
    ):
        return False
    return any(
        hedge in text
        for hedge in (
            "likely",
            "appears",
            "to investigate",
            "may",
            "possible",
            "suggests",
            "investigate",
        )
    )


def test_guidance_preserves_failure_and_hedges_causality() -> None:
    result = _run_no_scrollable_unchanged()
    guidance = _guidance(result)

    # Status stays a failure; absence is never reframed as a driver success.
    assert result.status == "failed"
    assert result.output.get("found") is False

    assert _truthfulness_holds(
        guidance, status=result.status, found=result.output.get("found")
    ), "FC-DIAG-08: guidance must preserve failure and hedge causality"


def test_truthfulness_predicate_rejects_lexically_valid_but_overstated_guidance() -> (
    None
):
    """Lexical FC-DIAG-02 markers are necessary but NOT sufficient.

    A guidance text that satisfies the lexical markers (scrollable + accessib +
    an app-side action) but overstates causality (proven Flutter defect) or
    frames the step as a success MUST be rejected by the truthfulness predicate.
    This proves FC-DIAG-08 has teeth beyond FC-DIAG-02 lexical shape.
    """
    proven_defect = {
        "kind": GUIDANCE_KIND,
        "summary": (
            "No accessible scrollable region was exposed; this is a proven "
            "flutter defect in the accessibility bridge."
        ),
        "recommended_action": "Expose a Scrollable widget.",
        "step_outcome": "failed",
    }
    success_framed = {
        "kind": GUIDANCE_KIND,
        "summary": (
            "No accessible scrollable region exposed; investigate accessibility."
        ),
        "recommended_action": "Expose a Scrollable; driver ok, works as expected.",
        "step_outcome": "failed",
    }

    # Both carry the FC-DIAG-02 lexical markers but must FAIL FC-DIAG-08.
    for overstated in (proven_defect, success_framed):
        assert "scrollable" in overstated["summary"].lower()
        assert "accessib" in (
            overstated["summary"].lower() + overstated["recommended_action"].lower()
        )
        assert not _truthfulness_holds(overstated, status="failed", found=False), (
            "overstated guidance must be rejected by FC-DIAG-08"
        )


# ════════════════════════════════════════════════════════════════════════════
# FC-DIAG-03 — eligibility precondition (AIYES-106-T1/T2; PO-02)
# ════════════════════════════════════════════════════════════════════════════


def test_guidance_emission_implies_full_eligibility_conjunction() -> None:
    result = _run_no_scrollable_unchanged()
    # Guidance is emitted only when the full conjunction holds.
    if (
        "guidance" in result.output
        and result.output["guidance"].get("kind") == GUIDANCE_KIND
    ):
        assert result.output["failure_class"] == "no_scrollable"
        assert result.output["progress"] == "unchanged"
        attempts = result.output["scroll_attempts"]
        assert len(attempts) >= 1
        assert all(a["selected_scrollable_id"] is None for a in attempts)
        for a in attempts:
            before = a.get("tree_fingerprint_before")
            after = a.get("tree_fingerprint_after")
            if before is not None:
                assert before == after
    else:  # pragma: no cover - RED: guidance not yet implemented
        pytest.fail("FC-DIAG-01/03: eligible failure must emit guidance")


# ════════════════════════════════════════════════════════════════════════════
# FC-DIAG-04 — negative boundary (AIYES-106-T2; PO-02)
# ════════════════════════════════════════════════════════════════════════════


def _assert_no_exposure_guidance(result: Any) -> None:
    assert (
        "guidance" not in result.output
        or result.output["guidance"].get("kind") != GUIDANCE_KIND
    ), "FC-DIAG-04: non-eligible failure must NOT carry no_scrollable guidance"


def test_changed_tree_failure_gets_no_exposure_guidance() -> None:
    inspect = RecordingInspect(
        trees=[
            _tree(
                _tree_node(
                    f"settings_list_{index}",
                    "scroll_view",
                    (0, 300 + index, 1080, 1500),
                    states=("scrollable",),
                )
            )
            for index in range(8)
        ]
    )
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )
    result = executor.execute(_scroll_step(max_scrolls=3))

    assert result.output["failure_class"] == "target_not_found_after_progress"
    assert result.output["progress"] == "changed"
    _assert_no_exposure_guidance(result)


def test_stuck_scrollable_no_progress_failure_gets_no_exposure_guidance() -> None:
    # A scrollable WAS selected on each attempt (selected_scrollable_id non-null)
    # but the tree never moved => target_not_found_no_progress, not absent.
    list_node = _tree_node(
        "settings_list",
        "scroll_view",
        (0, 300, 1080, 1500),
        states=("scrollable",),
    )
    inspect = RecordingInspect(trees=[_tree(list_node)] * 6)
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )
    result = executor.execute(_scroll_step(max_scrolls=8))

    assert result.output["failure_class"] == "target_not_found_no_progress"
    assert any(
        attempt["selected_scrollable_id"] is not None
        for attempt in result.output["scroll_attempts"]
    )
    _assert_no_exposure_guidance(result)


def test_progress_unknown_failure_gets_no_exposure_guidance() -> None:
    # No tree snapshot available => fingerprints unavailable => progress unknown.
    inspect = RecordingInspect(trees=[])
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )
    result = executor.execute(_scroll_step(max_scrolls=4))

    assert result.output["failure_class"] == "target_not_found_progress_unknown"
    assert result.output["progress"] == "unknown"
    _assert_no_exposure_guidance(result)


def test_role_drift_failure_gets_no_exposure_guidance() -> None:
    find = RoleAwareFakeFindUseCase(
        exact_role="View",
        exact_results=[[]],
        wildcard_results=[[_candidate("dev_text", "TextView", "Developer")]],
    )
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(),
        clock=StepClock(),
    )
    result = executor.execute(
        ScenarioStep(
            id="reach",
            kind="scroll_into_view",
            parameters={"role": "View", "name_pattern": "Developer", "max_scrolls": 3},
        )
    )

    assert result.status == "failed"
    assert "role_drift" in result.error
    _assert_no_exposure_guidance(result)


def test_driver_failure_short_circuit_gets_no_exposure_guidance() -> None:
    # A session/driver failure short-circuits at _require_session BEFORE any
    # scroll attempt or failure_class is computed (FCM driver_failure class,
    # scenario_use_case_executor.py session-guard path). The output dict carries
    # no failure_class, so the exposure guidance must never attach.
    start = SimpleNamespace(
        execute=lambda **kwargs: SimpleNamespace(session_id="s1", backend="android")
    )
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        find=FakeFindUseCase(results=[[]] * 10),
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        gesture=RecordingGesture(),
        session_repo=FakeSessionRepo(),
        clock=StepClock(),
    )
    # NOTE: no start_session step executed => _require_session raises and the
    # scroll_into_view branch returns a failed result with an EMPTY output dict.
    result = executor.execute(_scroll_step(max_scrolls=3))

    assert result.status == "failed"
    assert "failure_class" not in result.output
    _assert_no_exposure_guidance(result)


# ════════════════════════════════════════════════════════════════════════════
# FC-DIAG-05 — backward-compat output fields (AIYES-106-T3; PO-03)
# ════════════════════════════════════════════════════════════════════════════


def test_existing_scroll_failure_fields_remain_present_and_stable() -> None:
    result = _run_no_scrollable_unchanged()

    required_keys = {
        "found",
        "attempts",
        "elapsed",
        "direction",
        "viewport",
        "bound_hit",
        "progress",
        "failure_class",
        "scroll_attempts",
    }
    assert required_keys <= set(result.output.keys())
    assert result.output["found"] is False
    assert isinstance(result.output["scroll_attempts"], list)


# ════════════════════════════════════════════════════════════════════════════
# FC-DIAG-06 / FC-DIAG-07 — bounded, evidence-based guidance
# (AIYES-106-T4; PO-04)
# ════════════════════════════════════════════════════════════════════════════


def test_guidance_is_bounded_to_guidance_max_bytes() -> None:
    guidance = _guidance(_run_no_scrollable_unchanged())
    assert len(json.dumps(guidance).encode("utf-8")) <= GUIDANCE_MAX_BYTES


def test_guidance_carries_no_raw_tree_payload_or_tree_keys() -> None:
    guidance = _guidance(_run_no_scrollable_unchanged())

    forbidden_keys = {"roots", "children", "tree", "nodes"}

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in forbidden_keys, (
                    f"FC-DIAG-06: guidance must not contain tree key {key!r}"
                )
                _walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                _walk(child)

    _walk(guidance)

    blob = json.dumps(guidance)
    assert "screenshot" not in blob
    assert "base64" not in blob
    assert "tree_fingerprint" not in blob


def test_guidance_evidence_keys_are_subset_of_permitted_scalars() -> None:
    guidance = _guidance(_run_no_scrollable_unchanged())

    # Permitted scalar evidence (FC-DIAG-07) plus the guidance's own descriptive
    # text fields. Any extra key would indicate a new evidence source.
    permitted = {
        "kind",
        "summary",
        "recommended_action",
        "step_outcome",
        "failure_class",
        "progress",
        "attempts",
        "direction",
        "viewport",
        "selected_scrollable_id",
        "tree_fingerprint_equal",
    }
    extra = set(guidance.keys()) - permitted
    assert not extra, f"FC-DIAG-07: guidance introduced unpermitted keys {extra}"

    # Every value must be a scalar / simple list of scalars — no nested payloads.
    for key, value in guidance.items():
        assert isinstance(value, (str, int, float, bool, type(None), list)), (
            f"FC-DIAG-07: guidance value for {key!r} is not a permitted scalar"
        )
        if isinstance(value, list):
            assert all(
                isinstance(item, (str, int, float, bool, type(None))) for item in value
            )


# ════════════════════════════════════════════════════════════════════════════
# FC-LOG-01 — conditional LE-01 emission (AIYES-106-T5/T5b; PO-05)
# ════════════════════════════════════════════════════════════════════════════


def test_no_scrollable_failure_emits_exactly_one_le01_with_production_sink() -> None:
    log = InMemoryDiagnosticLog()
    result = _run_no_scrollable_unchanged(diagnostic_log=log)

    assert result.status == "failed"
    assert result.output["failure_class"] == "no_scrollable"

    le01 = [
        e
        for e in log.events
        if e.action == "scenario.diagnostic.failure_classified"
        and e.contract_id == "AIYES-106"
    ]
    assert len(le01) == 1, "FC-LOG-01: exactly one LE-01 for AIYES-106"
    event = le01[0]
    assert event.step_id == result.step_id
    assert event.failure_code == "no_scrollable"
    assert isinstance(event.diagnostic_summary, str)
    # Bounded single-line summary (OD-03 / SUMMARY_MAX_LEN).
    assert (
        event.diagnostic_summary
        == event.diagnostic_summary.splitlines()[0][:SUMMARY_MAX_LEN]
    )
    assert len(event.diagnostic_summary) <= SUMMARY_MAX_LEN
    assert log.emission_failure_count() == 0


def test_no_scrollable_emission_is_fail_open_with_raising_sink() -> None:
    raising = RaisingDiagnosticLog()
    noop = InMemoryDiagnosticLog()

    raising_result = _run_no_scrollable_unchanged(diagnostic_log=raising)
    noop_result = _run_no_scrollable_unchanged(diagnostic_log=noop)

    # Fail-open: the raising sink must not alter the scenario step result.
    assert raising_result.status == noop_result.status
    assert raising_result.error == noop_result.error
    assert raising_result.output == noop_result.output
    # The internal store failure is counted exactly once and swallowed.
    assert raising.emission_failure_count() == 1


def test_no_diagnostic_log_means_no_emission_and_no_crash() -> None:
    # FC-LOG-02 / FC-LOG-01 None branch: diagnostic_log default None => the
    # no_scrollable failure is produced normally with no emission, no crash.
    result = _run_no_scrollable_unchanged(diagnostic_log=None)

    assert result.status == "failed"
    assert result.output["failure_class"] == "no_scrollable"


# ════════════════════════════════════════════════════════════════════════════
# FC-LOG-02 — optional constructor dependency / back-compat (AIYES-106-T6; PO-06)
# ════════════════════════════════════════════════════════════════════════════


def test_diagnostic_log_is_optional_constructor_param_default_none() -> None:
    import inspect as _inspect

    sig = _inspect.signature(ScenarioUseCaseExecutor.__init__)
    assert "diagnostic_log" in sig.parameters
    assert sig.parameters["diagnostic_log"].default is None


def test_executor_constructs_without_diagnostic_log_and_log_is_none() -> None:
    # The AIYES-49 _make_executor omit set + dry-run path construct WITHOUT
    # supplying diagnostic_log; the attribute defaults to None (no churn).
    executor = _executor(
        find=FakeFindUseCase(results=[[]]),
        gesture=RecordingGesture(),
        session_repo=FakeSessionRepo(),
        clock=StepClock(),
    )
    assert executor._diagnostic_log is None


# ════════════════════════════════════════════════════════════════════════════
# FC-SCOPE-01 — slice purity (PO-07)
# ════════════════════════════════════════════════════════════════════════════


def test_non_eligible_results_carry_only_legacy_keys_no_guidance() -> None:
    """Slice purity: a non-eligible failure stays guidance-free; the only new
    key the contract may add (guidance) appears ONLY on the eligible class.

    Excluded scroll/selection/matching mechanics are observed read-only — a
    changed-tree result keeps its exact legacy field set with no guidance.
    """
    inspect = RecordingInspect(
        trees=[
            _tree(
                _tree_node(
                    f"settings_list_{index}",
                    "scroll_view",
                    (0, 300 + index, 1080, 1500),
                    states=("scrollable",),
                )
            )
            for index in range(8)
        ]
    )
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
    )
    result = executor.execute(_scroll_step(max_scrolls=3))

    legacy_keys = {
        "found",
        "attempts",
        "elapsed",
        "direction",
        "viewport",
        "bound_hit",
        "progress",
        "failure_class",
        "scroll_attempts",
    }
    # A non-eligible result carries no guidance key at all (only legacy + any
    # pre-existing selector_diagnostics); guidance is exclusive to the eligible
    # class.
    assert "guidance" not in result.output
    assert legacy_keys <= set(result.output.keys())


def test_no_scrollable_path_does_not_invoke_scroll_mechanics_changes() -> None:
    """Slice purity guard: guidance/emission are downstream-only.

    The eligible run still emits exactly the swipe gesture calls the existing
    scroll mechanics produce (one viewport swipe per attempt), proving the
    contract observes the already-computed evidence and does not alter the
    scroll/selection path (FC-SCOPE-01).
    """
    static_tree = _tree(_tree_node("root", "frame", (0, 0, 1080, 2400)))
    inspect = RecordingInspect(trees=[static_tree] * 4)
    find = FakeFindUseCase(results=[[]] * 10)
    gesture = RecordingGesture()
    executor = _executor(
        find=find,
        gesture=gesture,
        session_repo=FakeSessionRepo(resolution="1080x2400"),
        clock=StepClock(step=0.01),
        inspect=inspect,
        diagnostic_log=InMemoryDiagnosticLog(),
    )
    result = executor.execute(_scroll_step(max_scrolls=2))

    # Mechanics unchanged: 2 attempts => 2 viewport swipes, each with null
    # selected_scrollable_id.
    assert len(gesture.swipe_calls) == 2
    assert len(result.output["scroll_attempts"]) == 2
    assert all(
        attempt["selected_scrollable_id"] is None
        for attempt in result.output["scroll_attempts"]
    )
