"""AIYES-105: near-name selector diagnostic ranking (RED).

These tests pin the AIYES-105 behavior the implementation (A9) must deliver,
derived directly from FORMAL_CONSTRAINT_MAP.yaml::FC-* and the proof
obligations PO-01..PO-10. Every test asserts an OBSERVABLE post-state — the
returned diagnostics mapping, the produced candidate ORDER, the summary string,
or a captured DiagnosticEvent — never an internal return value alone.

Constraint coverage:

  FC-NEARNAME-01  near_name() predicate: casefold + whitespace-normalized,
                  either-side containment OR >=50% token overlap (denominator
                  min(|tn|,|tp|)); empty side / empty token set => NOT near-name
                  (no div-by-zero, no match-all); exact-name => near-name EXCEPT
                  the empty/empty degenerate row.                       (PO-01)
  FC-RANK-01/02/03  every exact/near candidate ranked AHEAD OF every unrelated
                  same-role candidate; classify+sort BEFORE the
                  _SELECTOR_DIAGNOSTIC_LIMIT bound (near-name beyond position 5
                  survives); deterministic total order terminating in node_id.
                                                                  (PO-02/PO-03)
  FC-SUMMARY-01..05  single-line <=200 summary; EXACT affirmative marker
                  "near-name candidate(s) found" when a near-name exists, EXACT
                  negative phrase "no near-name candidates found" otherwise;
                  near-name existence sourced from the FULL classified set, not
                  the bounded subset; summary precedes / does not embed raw
                  candidate detail.                              (PO-04/PO-05)
  FC-BOUND-01 / FC-NORAWTREE-01  candidates stay <=5; no raw accessibility tree
                  dump leaks into the diagnostics.                      (PO-06)
  FC-FINDPURE-01  domain/matching.py::name_matches is UNCHANGED (behavior +
                  guard).                                               (PO-08)
  FC-OBS-01/02/03  LE-01 emission through the PRODUCTION InMemoryDiagnosticLog:
                  exactly one event on a classified selector-diagnostic failure
                  (contract_id="AIYES-105", action
                  scenario.diagnostic.failure_classified, step_id/failure_code
                  present, diagnostic_summary == diagnostics['summary'] bounded
                  to SUMMARY_MAX_LEN); fail-open + adapter-owned count via an
                  injected internal-store failure; diagnostic_log=None => no
                  emission.                                             (PO-09)
  FC-OBS-NOLE02-01  AIYES-105 emits LE-01 ONLY; NO LE-02
                  (evidence.profile.selected) event is produced by this slice.
                                                                        (PO-10)
"""

from __future__ import annotations

import dataclasses
import json
import re
from types import SimpleNamespace
from typing import Any, List

import pytest

import aiyes.domain.matching as matching_module
import aiyes.adapters.scenario_use_case_executor as executor_module
from aiyes.adapters.diagnostic_log import InMemoryDiagnosticLog
from aiyes.adapters.scenario_use_case_executor import (
    _SELECTOR_DIAGNOSTIC_LIMIT,
    ScenarioUseCaseExecutor,
    _selector_diagnostics_from_nodes,
)
from aiyes.domain.diagnostic_event import DiagnosticEvent
from aiyes.domain.matching import name_matches, normalize_whitespace
from aiyes.domain.scenario import ScenarioStep
from aiyes.ports.diagnostic_event import DiagnosticEventPort


# ─── Authoritative constants (FORMAL_CONSTRAINT_MAP.yaml::constants, OD-03) ────

SUMMARY_MAX_LEN = 200
SUMMARY_AFFIRMATIVE_MARKER = "near-name candidate(s) found"
SUMMARY_NEGATIVE_PHRASE = "no near-name candidates found"
SUMMARY_AFFIRMATIVE_REGEX = r"^.*near-name candidate\(s\) found.*$"
SUMMARY_NEGATIVE_REGEX = r"^.*no near-name candidates found.*$"

_VIEWPORT = (1080, 2400)
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def near_name(name: str, pattern: str) -> bool:
    """Resolve the NEW diagnostic-only predicate from the adapter at call time.

    Imported lazily (not at module top-level) so the predicate tests fail
    individually on a missing symbol — observable per-test RED — rather than a
    whole-module collection error. The implementation (A9) MUST add
    ``near_name`` to ``aiyes.adapters.scenario_use_case_executor``.
    """
    predicate = getattr(executor_module, "near_name", None)
    assert predicate is not None, (
        "near_name predicate not yet implemented in "
        "aiyes.adapters.scenario_use_case_executor (FC-NEARNAME-01)"
    )
    return predicate(name, pattern)


# ─── Diagnostic-input node helper ─────────────────────────────────────────────


def _diag_node(
    node_id: str,
    role: str,
    name: str,
    *,
    actions: list[str] | None = None,
    bounds: list[int] | None = None,
) -> dict[str, Any]:
    """A compact accessibility node mapping accepted by the diagnostic helper."""
    return {
        "id": node_id,
        "role": role,
        "name": name,
        "bounds": bounds if bounds is not None else [0, 0, 100, 50],
        "actions": actions if actions is not None else [],
    }


def _diagnostics(
    nodes: list[dict[str, Any]],
    *,
    requested_role: str,
    name_pattern: str,
) -> dict[str, Any]:
    """Run the production selector-diagnostic helper over a node list."""
    return _selector_diagnostics_from_nodes(
        nodes,
        _VIEWPORT,
        requested_role=requested_role,
        name_pattern=name_pattern,
        state=None,
    )


# ==============================================================================
# PO-01 — FC-NEARNAME-01 near_name() predicate (table-driven)
# ==============================================================================

# (name, pattern, expected_near_name, why)
_NEAR_NAME_ROWS: list[tuple[str, str, bool, str]] = [
    # ----- exact-name => near-name (non-empty guard) -------------------------
    ("Target Markets", "Target Markets", True, "exact => near (non-empty)"),
    ("target markets", "TARGET MARKETS", True, "exact after casefold"),
    ("  Target   Markets ", "Target Markets", True, "exact after whitespace-normalize"),
    # ----- containment branch (either side contains the other) ---------------
    ("Target Markets Overview", "Target Markets", True, "pattern contained in name"),
    ("Markets", "Target Markets Section", True, "name contained in pattern"),
    ("login button", "login", True, "substring containment"),
    # ----- token-overlap branch (>=50%, denominator = min(|tn|,|tp|)) --------
    # tn={save,changes} tp={save,now}; inter={save}=1; denom=min(2,2)=2 => 0.5 OK
    ("Save Changes", "Save Now", True, "token overlap 1/2 == 0.5 boundary"),
    # tn={open,settings,menu} tp={settings}; inter=1; denom=min(3,1)=1 => 1.0
    ("Open Settings Menu", "Settings", True, "single shared token, min denom == 1"),
    # punctuation split: tn={user,name} tp={user,name}; overlap 1.0
    ("user-name", "user_name", True, "non-alphanumeric split yields same tokens"),
    # ----- NEGATIVE: below threshold ----------------------------------------
    # tn={alpha,beta,gamma} tp={alpha,delta,epsilon}; inter=1; denom=min(3,3)=3
    # => 0.333 < 0.5 AND no containment
    ("Alpha Beta Gamma", "Alpha Delta Epsilon", False, "overlap 1/3 < 0.5"),
    ("Settings", "Profile", False, "disjoint single tokens, no containment"),
    ("Cancel", "Submit Order Now", False, "unrelated, no overlap, no containment"),
    # ----- empty-side / empty-token rules (no div-by-zero, no match-all) -----
    ("", "", False, "empty/empty degenerate => NOT near-name (excluded)"),
    ("", "Login", False, "empty name side => never near-name"),
    ("Login", "", False, "empty pattern side => never near-name"),
    ("   ", "Login", False, "whitespace-only name normalizes to empty => False"),
    ("!!!", "Login", False, "punctuation-only name has empty token set => False"),
    ("Login", "###", False, "punctuation-only pattern, no containment, empty tokens"),
    # ----- A10-004 RED rows: punctuation-only same/contained pairs -----------
    # FC-NEARNAME-01: empty token set => False, even when one side contains the
    # other.  The containment branch MUST NOT fire before the token-set guard;
    # these rows are RED against the current impl which returns True for
    # same/contained punctuation strings (containment check precedes guard).
    ("!!!", "!!!", False, "a10_004: punc-only identical => empty token sets => False"),
    ("###", "##", False, "a10_004: punc-only contained => empty token sets => False"),
    ("...", ".", False, "a10_004: dots contained => empty token sets => False"),
    ("--", "--", False, "a10_004: dashes identical => empty token sets => False"),
    # Mixed punc/word pairs: no containment fires (strings differ), token guard
    # catches the empty-token side => correctly False in both old and new impl.
    (
        "!!!",
        "Submit",
        False,
        "a10_004: punc name, word pattern, no containment => False",
    ),
    (
        "Submit",
        "###",
        False,
        "a10_004: word name, punc pattern, no containment => False",
    ),
]


@pytest.mark.parametrize(
    "name, pattern, expected, why",
    _NEAR_NAME_ROWS,
    ids=[row[3] for row in _NEAR_NAME_ROWS],
)
def test_near_name_rule_is_casefold_contains_or_token_overlap(
    name: str, pattern: str, expected: bool, why: str
) -> None:
    """FC-NEARNAME-01 / PO-01: near_name is casefold + whitespace-normalized,
    either-side containment OR >=50% token overlap (denominator min(|tn|,|tp|)),
    with empty side / empty token set => NOT near-name."""
    assert near_name(name, pattern) is expected, why


def test_near_name_empty_inputs_never_raise_and_are_false() -> None:
    """FC-NEARNAME-01 (BR-5 / RISK-3): empty/degenerate inputs return False and
    never raise (no ZeroDivisionError on the min-denominator)."""
    for name, pattern in (
        ("", ""),
        ("", "x"),
        ("x", ""),
        ("   ", "   "),
        ("!!!", "@@@"),
    ):
        assert near_name(name, pattern) is False


def test_near_name_exact_implies_near_for_nonempty_text() -> None:
    """FC-NEARNAME-01 relationship: exact_name AND norm(name) != '' => near_name.

    The empty/empty degenerate case is exact but NOT near (excluded by design).
    """
    for text in ("Target Markets", "login", "Save  Changes"):
        assert normalize_whitespace(text) != ""
        # exact (after normalize+casefold) and non-empty => near-name
        assert near_name(text, text) is True
    # degenerate exclusion: exact-equal empties, but not near-name.
    assert near_name("", "") is False


# ==============================================================================
# PO-02 — FC-RANK-01 / FC-RANK-02 ordering (near before unrelated; survives bound)
# ==============================================================================


def _classify(candidate: dict[str, Any], name_pattern: str) -> str:
    """Test-side classification mirror for ordering assertions."""
    return "NEAR" if near_name(candidate.get("name", ""), name_pattern) else "UNRELATED"


def test_near_name_candidates_rank_before_unrelated_role_matches() -> None:
    """FC-RANK-01 / PO-02: every near-name candidate precedes every unrelated
    same-role candidate in the produced ORDER.

    All nodes share the requested role (Button) so each is included via
    role_matches; some additionally near-name-match the requested name_pattern.
    """
    # The near-name candidates carry a role-drift mismatch (observed role differs
    # from the requested Button), so their reason_count equals the unrelated
    # same-role candidates' (1 each). Node ids are chosen so the EXISTING sort
    # key (reason_count, actionable_penalty, node_id) would place the unrelated
    # candidates FIRST (lexicographically smaller ids). Only the NEW class_rank
    # key can lift the near-name candidates ahead — isolating the AIYES-105
    # behavior from the pre-existing tie-break heuristic.
    name_pattern = "Target Markets"
    nodes = [
        _diag_node("a_unrelated", "Button", "Settings", actions=["click"]),
        _diag_node("y_near", "View", "Target Markets Overview", actions=["click"]),
        _diag_node("b_unrelated", "Button", "Profile", actions=["click"]),
        _diag_node("z_exact", "View", "Target Markets", actions=["click"]),
        _diag_node("c_unrelated", "Button", "Cancel", actions=["click"]),
    ]

    diagnostics = _diagnostics(
        nodes, requested_role="Button", name_pattern=name_pattern
    )
    ordered = diagnostics["candidates"]
    classes = [_classify(c, name_pattern) for c in ordered]

    # No UNRELATED precedes any NEAR.
    last_near = max((i for i, k in enumerate(classes) if k == "NEAR"), default=-1)
    first_unrelated = next(
        (i for i, k in enumerate(classes) if k == "UNRELATED"), len(classes)
    )
    assert last_near < first_unrelated, classes
    # The exact/near candidates are the leading entries despite larger node ids.
    assert classes[0] == "NEAR"
    assert {ordered[0]["node_id"], ordered[1]["node_id"]} == {"y_near", "z_exact"}


def test_near_name_candidate_beyond_bound_is_not_dropped_for_unrelated() -> None:
    """FC-RANK-02 / PO-02: classification + sort happen on the FULL set BEFORE
    the _SELECTOR_DIAGNOSTIC_LIMIT bound, so a near-name that would otherwise land
    beyond position 5 is NOT dropped in favor of unrelated candidates.

    The 5 unrelated same-role (Button) candidates each carry exactly one mismatch
    reason (name_mismatch) and small node ids; the single near-name candidate is
    a role-drift View (one mismatch reason: role_mismatch) with the LARGEST node
    id. Under the EXISTING sort key alone the near-name would tie on reason_count
    and lose the node-id tie-break, landing in 6th place and being dropped by the
    bound. The contract requires the NEW class_rank to lift it into — and to the
    front of — the bounded list.
    """
    name_pattern = "Target Markets"
    nodes = [
        _diag_node(f"a_unrelated_{i}", "Button", f"Filler {i}", actions=["click"])
        for i in range(5)
    ]
    nodes.append(_diag_node("z_near_late", "View", "Target Markets", actions=["click"]))

    diagnostics = _diagnostics(
        nodes, requested_role="Button", name_pattern=name_pattern
    )
    bounded = diagnostics["candidates"]

    assert len(bounded) <= _SELECTOR_DIAGNOSTIC_LIMIT
    bounded_ids = [c["node_id"] for c in bounded]
    # The near-name candidate survives the bound and leads the bounded list.
    assert "z_near_late" in bounded_ids, bounded_ids
    assert bounded[0]["node_id"] == "z_near_late"
    # candidate_count reflects the FULL classified set, not the bounded subset.
    assert diagnostics["candidate_count"] == 6


def test_ranking_is_deterministic_across_identical_runs() -> None:
    """FC-RANK-03 / PO-03: identical diagnostic input yields identical candidate
    order on repeated runs, and the order honors the class_rank primary key
    (near before unrelated) with node_id as the terminal deterministic tie-break.

    The near-name candidates are role-drift Views (reason_count 1, matching the
    unrelated same-role Buttons) so the order cannot be produced by the existing
    reason-count heuristic alone — it must come from the AIYES-105 class_rank.
    """
    name_pattern = "Target Markets"
    nodes = [
        _diag_node("b_unrelated", "Button", "Cancel", actions=["click"]),
        _diag_node("a_near", "View", "Target Markets", actions=["click"]),
        _diag_node("c_near", "View", "Target Markets Overview", actions=["click"]),
        _diag_node("d_unrelated", "Button", "Settings", actions=["click"]),
    ]

    first = _diagnostics(nodes, requested_role="Button", name_pattern=name_pattern)
    second = _diagnostics(nodes, requested_role="Button", name_pattern=name_pattern)

    order_first = [c["node_id"] for c in first["candidates"]]
    order_second = [c["node_id"] for c in second["candidates"]]
    assert order_first == order_second
    # Both near-name candidates precede both unrelated candidates, and the
    # near-name pair is internally ordered by the node_id tie-break (a before c).
    assert order_first == ["a_near", "c_near", "b_unrelated", "d_unrelated"]


# ==============================================================================
# PO-05 — FC-SUMMARY-01/02/05 affirmative branch (near-name exists)
# ==============================================================================


def test_selector_summary_reports_near_name_candidates_exist() -> None:
    """FC-SUMMARY-01/02/05 / PO-05: when >=1 near-name candidate exists, the
    summary contains the EXACT affirmative marker, matches the affirmative regex,
    is single-line and <= SUMMARY_MAX_LEN, and does NOT contain the negative
    phrase."""
    name_pattern = "Target Markets"
    nodes = [
        _diag_node("near_a", "Button", "Target Markets", actions=["click"]),
        _diag_node("unrelated_a", "Button", "Settings", actions=["click"]),
    ]

    diagnostics = _diagnostics(
        nodes, requested_role="Button", name_pattern=name_pattern
    )
    summary = diagnostics["summary"]

    assert isinstance(summary, str) and summary != ""
    assert "\n" not in summary
    assert len(summary) <= SUMMARY_MAX_LEN
    assert summary == summary.splitlines()[0][:SUMMARY_MAX_LEN]
    assert SUMMARY_AFFIRMATIVE_MARKER in summary
    assert re.search(SUMMARY_AFFIRMATIVE_REGEX, summary) is not None
    assert SUMMARY_NEGATIVE_PHRASE not in summary


# ==============================================================================
# PO-04 — FC-SUMMARY-01/03/04/05 negative branch (no near-name) + truthfulness
# ==============================================================================


def test_selector_summary_reports_no_near_name_candidates() -> None:
    """FC-SUMMARY-01/03/05 / PO-04: when NO near-name candidate exists, the
    summary contains the EXACT negative phrase, matches the negative regex, is
    single-line and bounded, and does NOT contain the affirmative marker."""
    name_pattern = "Target Markets"
    # All candidates share the requested role (included via role_matches) but
    # none near-name-matches "Target Markets".
    nodes = [
        _diag_node("unrelated_a", "Button", "Settings", actions=["click"]),
        _diag_node("unrelated_b", "Button", "Profile", actions=["click"]),
        _diag_node("unrelated_c", "Button", "Cancel", actions=["click"]),
    ]

    diagnostics = _diagnostics(
        nodes, requested_role="Button", name_pattern=name_pattern
    )
    summary = diagnostics["summary"]

    assert isinstance(summary, str) and summary != ""
    assert "\n" not in summary
    assert len(summary) <= SUMMARY_MAX_LEN
    assert SUMMARY_NEGATIVE_PHRASE in summary
    assert re.search(SUMMARY_NEGATIVE_REGEX, summary) is not None
    assert SUMMARY_AFFIRMATIVE_MARKER not in summary


def test_summary_truthfulness_sourced_from_full_classified_set() -> None:
    """FC-SUMMARY-04 / PO-04: near-name existence is computed from the FULL
    classified set, NOT the bounded subset — a genuine, non-self-satisfying proof.

    Construction (the discriminator): the SINGLE near-name candidate is engineered
    to sort OUTSIDE candidates[:5] (=_SELECTOR_DIAGNOSTIC_LIMIT) UNDER THE OLD,
    pre-AIYES-105 ``reason_count`` heuristic:

      * 5 unrelated fillers are exact-role Buttons that are visible AND actionable
        and only ``name_mismatch`` the requested pattern => reason_count == 1, and
        carry the SMALLEST node ids.
      * the lone near-name ("Target Markets", exact) is a role-drift View that is
        ALSO not-visible and not-actionable => reasons
        {role_mismatch, not_visible, not_actionable}, reason_count == 3, and the
        LARGEST node id.

    Under the OLD key (reason_count, actionable_penalty, node_id) the near-name
    sorts to 6th place and is dropped by the bound, so a faulty impl that reads
    the affirmative flag off the (old-ordered) BOUNDED list would WRONGLY emit
    "no near-name candidates found". The test asserts the summary is STILL
    affirmative, which can only hold if the existence flag is sourced from the
    FULL classified set BEFORE bounding.

    Falsifiability (verified): against this input the correct impl (full-set flag
    + class_rank lift) yields the affirmative marker and PASSES; a bounded-source
    impl over the old reason_count ordering excludes the near-name from the 5-item
    bounded list and yields the negative phrase, FAILING the assertion below. The
    old fixture (near_one with the requested role + exact name and FEWER reasons
    than the fillers) sorted to the front even under the old heuristic, so it could
    not falsify the bounded-source impl — this construction repairs that gap.
    """
    name_pattern = "Target Markets"
    # 5 unrelated same-role fillers: visible + actionable, name_mismatch only
    # (reason_count == 1), with the smallest node ids.
    nodes = [
        _diag_node(
            f"a_filler_{i}",
            "Button",
            f"Filler {i}",
            actions=["click"],
            bounds=[0, 0, 100, 50],
        )
        for i in range(5)
    ]
    # The lone near-name: a role-drift View that is OFF-SCREEN (not visible) and
    # NON-actionable (no actions), giving it reason_count 3 and the largest node
    # id, so the OLD reason_count heuristic ranks it 6th (outside the bound).
    nodes.append(
        _diag_node(
            "z_near_offbound",
            "View",
            "Target Markets",
            actions=[],
            bounds=[0, 5000, 100, 5050],
        )
    )

    diagnostics = _diagnostics(
        nodes, requested_role="Button", name_pattern=name_pattern
    )
    summary = diagnostics["summary"]

    # The full classified set holds 6 candidates; the near-name is one of them.
    assert diagnostics["candidate_count"] == 6
    # Truthfulness comes from the FULL set: affirmative despite the near-name
    # being engineered outside the OLD-heuristic bounded subset.
    assert SUMMARY_AFFIRMATIVE_MARKER in summary
    assert SUMMARY_NEGATIVE_PHRASE not in summary


def test_summary_does_not_embed_raw_candidate_list() -> None:
    """FC-SUMMARY-05 / PO-04: the summary is a top-level scalar string that does
    NOT embed the raw candidates list (it precedes raw candidate detail)."""
    name_pattern = "Target Markets"
    nodes = [
        _diag_node("near_a", "Button", "Target Markets", actions=["click"]),
        _diag_node("unrelated_a", "Button", "Settings", actions=["click"]),
    ]

    diagnostics = _diagnostics(
        nodes, requested_role="Button", name_pattern=name_pattern
    )
    summary = diagnostics["summary"]

    # The summary must not serialize the candidate node identifiers / dict shape.
    assert "node_id" not in summary
    assert "'reasons'" not in summary and '"reasons"' not in summary
    assert "unrelated_a" not in summary
    # 'summary' is a top-level key alongside (and distinct from) 'candidates'.
    assert "summary" in diagnostics and "candidates" in diagnostics
    assert isinstance(diagnostics["candidates"], list)


# ==============================================================================
# PO-06 — FC-BOUND-01 / FC-NORAWTREE-01 bounded evidence, no raw tree dump
# ==============================================================================


def test_selector_diagnostics_remain_bounded_and_do_not_emit_raw_tree() -> None:
    """FC-BOUND-01 / FC-NORAWTREE-01 / PO-06: candidates stay <= 5, max_candidates
    reports the bound, candidate_count is the full count, and no raw accessibility
    tree dump (nested children / roots / tree payload) leaks into diagnostics."""
    name_pattern = "Target Markets"
    nodes = [
        _diag_node(f"near_{i}", "Button", "Target Markets", actions=["click"])
        for i in range(12)
    ]

    diagnostics = _diagnostics(
        nodes, requested_role="Button", name_pattern=name_pattern
    )

    assert diagnostics["max_candidates"] == _SELECTOR_DIAGNOSTIC_LIMIT == 5
    assert len(diagnostics["candidates"]) <= _SELECTOR_DIAGNOSTIC_LIMIT
    assert diagnostics["candidate_count"] == 12  # full classified count, not bounded

    # No raw-tree payload anywhere in the serialized diagnostics.
    blob = json.dumps(diagnostics)
    assert '"children"' not in blob
    assert '"roots"' not in blob
    assert '"tree"' not in blob
    # Each candidate is a compact dict — no nested node tree under any candidate.
    for candidate in diagnostics["candidates"]:
        assert "children" not in candidate
        assert "tree" not in candidate
        assert "roots" not in candidate
        # The existing mismatch-reason field set is preserved (FC-BACKCOMPAT-01).
        assert set(candidate).issuperset(
            {
                "node_id",
                "role",
                "requested_role",
                "observed_role",
                "name",
                "visible",
                "actionable",
                "reasons",
            }
        )


# ==============================================================================
# PO-08 — FC-FINDPURE-01 domain/matching.py::name_matches unchanged
# ==============================================================================


def test_name_matches_behavior_unchanged_by_near_name_slice() -> None:
    """FC-FINDPURE-01 / PO-08: name_matches keeps its case-insensitive substring
    semantics with empty-pattern match-all. near_name MUST NOT alter find
    behavior; these rows would break if name_matches were widened to near-name."""
    # Substring match (case-insensitive, whitespace-normalized).
    assert name_matches("Target Markets", "target markets") is True
    assert name_matches("Target Markets Overview", "Target Markets") is True
    # Empty / whitespace-only pattern matches everything.
    assert name_matches("anything", "") is True
    assert name_matches("anything", "   ") is True
    # A near-name (token-overlap) that is NOT a substring must still be a NON-match
    # for name_matches — proving name_matches was not replaced by near_name.
    assert name_matches("Save Changes", "Save Now") is False
    assert name_matches("Open Settings Menu", "Settings Menu Open") is False


def test_matching_module_exposes_only_unchanged_public_matchers() -> None:
    """FC-FINDPURE-01 / PO-08 (guard): the domain matching module's public
    surface is exactly normalize_whitespace + name_matches — near_name is NOT
    introduced into the domain matching slice (it is diagnostic-only and lives
    in the adapter)."""
    assert not hasattr(matching_module, "near_name")
    public = {n for n in vars(matching_module) if not n.startswith("_")}
    # The diagnostic predicate must not be added here; the find matchers remain.
    assert "name_matches" in public
    assert "normalize_whitespace" in public
    assert "near_name" not in public


# ==============================================================================
# PO-09 / PO-10 — LE-01 emission through the PRODUCTION adapter; no LE-02
# ==============================================================================

# AIYES-105 emits LE-01 for a classified selector-diagnostic failure. The
# scroll_into_view role-drift failure path produces selector_diagnostics (with
# the new summary) and is the observable trigger used here.


@dataclasses.dataclass
class RoleAwareFakeFindUseCase:
    """Returns exact-role results, then wildcard results, by requested role."""

    exact_role: str
    exact_results: List[list]
    wildcard_results: List[list]
    calls: List[dict] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        role = kwargs.get("role")
        if role == self.exact_role and self.exact_results:
            return self.exact_results.pop(0)
        if role == "*" and self.wildcard_results:
            return self.wildcard_results.pop(0)
        return []


class _StepClock:
    def __init__(self, step: float = 0.1) -> None:
        self._step = step
        self._t = 0.0

    def now(self) -> float:
        self._t += self._step
        return self._t


class _FakeSessionRepo:
    def __init__(self, resolution: str = "1080x2400") -> None:
        self._resolution = resolution

    def load(self, session_id: str) -> Any:
        return SimpleNamespace(
            session_id=session_id,
            resolution=self._resolution,
            backend="android",
            device_serial="",
        )


class _RaisingStore(list):
    """Backing store whose append raises — an INTERNAL adapter failure.

    Injected into the PRODUCTION InMemoryDiagnosticLog so the adapter's OWN
    fail-open swallow-and-count path runs. The store NEVER touches the adapter's
    failure counter, so the count assertion is NOT self-satisfying.
    """

    def append(self, _item: Any) -> None:
        raise RuntimeError("backing store down")


def _role_drift_candidate(node_id: str, name: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "role": "Button",
        "name": name,
        "bounds": [0, 0, 100, 50],
        "actions": ["click"],
    }


def _build_scroll_executor(*, diagnostic_log: Any | None) -> ScenarioUseCaseExecutor:
    """A started executor whose scroll_into_view role-drift fails with >1
    actionable Button candidate (ambiguous role drift) — producing
    selector_diagnostics and triggering the AIYES-105 LE-01 emission."""
    start = SimpleNamespace(
        execute=lambda **kw: SimpleNamespace(session_id="s1", backend="android")
    )
    find = RoleAwareFakeFindUseCase(
        exact_role="View",
        exact_results=[[]],
        wildcard_results=[
            [
                _role_drift_candidate("cand_a", "Target Markets"),
                _role_drift_candidate("cand_b", "Target Markets"),
            ]
        ],
    )
    gesture = SimpleNamespace(
        swipe=lambda *a, **k: SimpleNamespace(status="ok"),
        pinch=lambda *a, **k: SimpleNamespace(status="ok"),
        two_finger_scroll=lambda *a, **k: SimpleNamespace(status="ok"),
    )
    kwargs: dict[str, Any] = dict(
        session_start=start,
        inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace(tree=None)),
        find=find,
        action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        gesture=gesture,
        session_repo=_FakeSessionRepo(),
        clock=_StepClock(),
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


def _run_role_drift_failure(executor: ScenarioUseCaseExecutor) -> Any:
    return executor.execute(
        ScenarioStep(
            id="reach_target",
            kind="scroll_into_view",
            parameters={
                "role": "View",
                "name_pattern": "Target Markets",
                "max_scrolls": 3,
            },
        )
    )


def test_production_adapter_emits_exactly_one_le01_for_classified_selector_failure() -> (
    None
):
    """FC-OBS-01 / PO-09: a classified selector-diagnostic failure emits exactly
    one scenario.diagnostic.failure_classified event through the PRODUCTION
    InMemoryDiagnosticLog, carrying contract_id="AIYES-105", a present step_id +
    failure_code, and diagnostic_summary equal to the diagnostics summary,
    bounded to SUMMARY_MAX_LEN."""
    log = InMemoryDiagnosticLog()
    executor = _build_scroll_executor(diagnostic_log=log)

    result = _run_role_drift_failure(executor)

    assert result.status == "failed"
    diagnostics = result.output["selector_diagnostics"]
    assert "summary" in diagnostics  # AIYES-105 added the summary field

    le01 = [
        e
        for e in log.events
        if e.action == "scenario.diagnostic.failure_classified"
        and e.contract_id == "AIYES-105"
    ]
    assert len(le01) == 1, [dataclasses.asdict(e) for e in log.events]
    event = le01[0]
    assert isinstance(event, DiagnosticEvent)
    assert event.step_id == "reach_target"
    assert isinstance(event.failure_code, str) and event.failure_code != ""
    assert _SNAKE_CASE.match(event.failure_code), event.failure_code
    summary = event.diagnostic_summary
    assert isinstance(summary, str)
    assert summary == diagnostics["summary"]
    assert "\n" not in summary
    assert len(summary) <= SUMMARY_MAX_LEN
    assert log.emission_failure_count() == 0  # all-success path


def test_le01_emission_is_fail_open_and_count_is_adapter_owned() -> None:
    """FC-OBS-02 / FC-OBS-03 / PO-09: an INTERNAL adapter store failure is
    swallowed by the PRODUCTION adapter (the scenario step outcome is UNCHANGED
    versus a healthy sink) AND the adapter's emission_failure_count increments by
    exactly one.

    The injected store NEVER touches the counter, so this passes only if the
    ADAPTER owns the increment — it is not self-satisfying."""
    log = InMemoryDiagnosticLog()
    log._events = _RaisingStore()  # noqa: SLF001 — exercise the adapter fail-open path
    assert log.emission_failure_count() == 0

    executor = _build_scroll_executor(diagnostic_log=log)
    reference = _build_scroll_executor(diagnostic_log=InMemoryDiagnosticLog())

    result = _run_role_drift_failure(executor)
    reference_result = _run_role_drift_failure(reference)

    # The fail-open emission did not change the step outcome.
    assert result.status == reference_result.status == "failed"
    assert dict(result.output) == dict(reference_result.output)
    # The ADAPTER owned the increment on its swallowed internal failure.
    assert log.emission_failure_count() == 1


def test_no_logger_injected_means_no_le01_emission_and_no_crash() -> None:
    """FC-OBS-01 / PO-09: diagnostic_log=None => classification still happens, no
    emission occurs, and nothing crashes (back-compat)."""
    executor = _build_scroll_executor(diagnostic_log=None)

    result = _run_role_drift_failure(executor)

    assert result.status == "failed"
    assert "selector_diagnostics" in result.output
    assert "summary" in result.output["selector_diagnostics"]


def test_aiyes105_emits_only_le01_not_le02() -> None:
    """FC-OBS-NOLE02-01 / PO-10: AIYES-105 emits LE-01 ONLY. No event carrying
    the LE-02 action "evidence.profile.selected" is produced by this slice."""
    log = InMemoryDiagnosticLog()
    executor = _build_scroll_executor(diagnostic_log=log)

    _run_role_drift_failure(executor)

    actions = [e.action for e in log.events]
    assert actions, "expected at least the LE-01 emission"
    assert all(a == "scenario.diagnostic.failure_classified" for a in actions), actions
    assert "evidence.profile.selected" not in actions
    # No event populates the LE-02-only payload fields.
    for event in log.events:
        assert event.profile is None
        assert event.raw_tree_included is None
        assert event.preserved_failure_count is None


def test_diagnostic_event_port_is_the_only_emission_surface() -> None:
    """FC-OBS-NOLE02-01 / PO-10: the production sink conforms to the dedicated
    DiagnosticEventPort (it is NOT the OperationLogPort/OperationRecord command
    log being overloaded to carry LE-01 payloads)."""
    log = InMemoryDiagnosticLog()
    assert isinstance(log, DiagnosticEventPort)
    # The command-log shape (append(OperationRecord)) is not how LE-01 flows.
    executor = _build_scroll_executor(diagnostic_log=log)
    _run_role_drift_failure(executor)
    assert len(log.events) >= 1
    assert all(isinstance(e, DiagnosticEvent) for e in log.events)
