"""Profile-aware evidence shaping (AIYES-107).

Centralized compact/deep evidence shaping reused by the scenario CLI presenter,
the smoke harness, the MCP scenario_run handler, and the evidence-bundle writer
so every surface excludes the SAME raw-tree payloads and preserves the SAME
classification fields (FC-EVIDENCE-01..04, FC-CLASS-01..03, LAT-ARCH-03).

The two profiles:

* ``compact`` (default) — excludes full raw accessibility-tree dumps (the
  inspect-step ``output.tree`` node payload, the socialzzz raw scenario
  ``stdout`` blob, and the full ``dataclasses.asdict(step)`` dump) while
  preserving ``step_id``/``kind``/``status``, the upstream classification
  (``failure_code`` / ``failure_class`` copied verbatim, never contradicted),
  a derived bounded ``diagnostic_summary``, bounded ``selector_diagnostics``
  (candidates <= 5), and an ``artifact_refs`` collection.
* ``deep`` — retains the exact pre-AIYES-107 detailed render.

``diagnostic_summary`` is a NEW derived field (FC-CLASS-03): a TOTAL,
deterministic, single-line function bounded to SUMMARY_MAX_LEN characters.

Stdlib only — domain purity. This module performs no I/O and depends on no
infrastructure; it shapes plain dicts/values handed to it by adapters.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from aiyes.domain.diagnostic_event import DiagnosticEvent


# OD-03: diagnostic_summary is the first line truncated at SUMMARY_MAX_LEN.
SUMMARY_MAX_LEN = 200

# Existing upstream bound (scenario_use_case_executor.py _SELECTOR_DIAGNOSTIC_LIMIT).
SELECTOR_DIAGNOSTIC_LIMIT = 5

COMPACT = "compact"
DEEP = "deep"
PROFILES: Tuple[str, str] = (COMPACT, DEEP)

# LE-02 / LE-01 action discriminators (logging.yaml emission_contract).
ACTION_PROFILE_SELECTED = "evidence.profile.selected"

_PASSED = "passed"

# A10-CRIT-003: deterministic ultimate fallback for a failed record carrying no
# error text and no classification token of any kind. Total over failed records.
_ULTIMATE_FAILED_SUMMARY = "scenario step failed"


def is_valid_profile(profile: Any) -> bool:
    """True iff ``profile`` is one of the canonical enum values."""
    return profile in PROFILES


def normalize_profile(profile: Optional[str]) -> str:
    """Resolve a possibly-missing profile to the compact default.

    Raises ValueError for an out-of-enum value (rejected, not coerced) so the
    MCP/CLI surfaces can surface a hard error rather than silently defaulting.
    """
    if profile is None:
        return COMPACT
    if not is_valid_profile(profile):
        raise ValueError(
            f"invalid evidence profile {profile!r}; expected one of {list(PROFILES)}"
        )
    return profile


def first_line(text: object) -> str:
    """First line of ``text`` truncated to SUMMARY_MAX_LEN.

    Non-string / empty input normalizes to "".
    """
    if not isinstance(text, str) or not text:
        return ""
    return text.splitlines()[0][:SUMMARY_MAX_LEN]


def humanize_classification(
    failure_code: Optional[str],
    failure_class: Optional[str],
) -> str:
    """Deterministic human-readable phrase for a stable code/class token.

    Prefers ``failure_class`` (more specific) over ``failure_code``. The result
    contains no free text and no raw tree. Unknown tokens fall back to a
    capitalized, space-joined rendering of the token so the function is total.
    """
    token = failure_class if failure_class else failure_code
    if not token:
        return ""
    fixed = _HUMANIZED.get(token)
    if fixed is not None:
        return fixed
    return token.replace("_", " ").strip().capitalize()


_HUMANIZED: Dict[str, str] = {
    # failure_code tokens (scenario_run.py _failure_code)
    "prerequisite_missing": "Prerequisite missing",
    "assertion_failed": "Assertion failed",
    "executor_error": "Executor error",
    # failure_class tokens (scenario_use_case_executor.py)
    "ambiguous_role_drift": "Ambiguous role drift",
    "target_not_found_no_progress": "Target not found (no progress)",
    "target_not_found_after_progress": "Target not found after progress",
    "target_not_found_progress_unknown": "Target not found (progress unknown)",
    "no_scrollable": "No scrollable region found",
}


def diagnostic_summary(
    status: str,
    error: object,
    failure_code: Optional[str],
    failure_class: Optional[str],
) -> str:
    """Derive the bounded, single-line diagnostic summary (FC-CLASS-03).

    Total deterministic function. For a record r:
      - first_line(error)                       if error is non-empty;
      - first_line(humanize(class|code))        if error empty AND r is failed;
      - ""                                      if r is non-failed with no text.

    For a FAILED record the result is ALWAYS non-empty (A10-CRIT-003 totality):
    when neither an error text nor any classification token is present, a fixed
    deterministic ultimate fallback is returned. The empty string is reachable
    only via the third branch (non-failed, no text). No contradictory branch
    (resolves SV-01).
    """
    text = first_line(error)
    if text:
        return text
    if status != _PASSED:
        humanized = first_line(humanize_classification(failure_code, failure_class))
        return humanized if humanized else _ULTIMATE_FAILED_SUMMARY
    return ""


def _bounded_selector_diagnostics(diagnostics: Any) -> Optional[Dict[str, Any]]:
    """Copy selector_diagnostics verbatim, bounding candidates to <= 5.

    The candidates list is already bounded to 5 upstream; this re-applies the
    bound defensively (FC-EVIDENCE-03) without widening it. requested_selector,
    summary, candidate_count, and max_candidates are copied unchanged
    (FC-CLASS-02 verbatim copy).
    """
    if not isinstance(diagnostics, dict):
        return None
    shaped = dict(diagnostics)
    candidates = shaped.get("candidates")
    if isinstance(candidates, list):
        shaped["candidates"] = list(candidates[:SELECTOR_DIAGNOSTIC_LIMIT])
    return shaped


def _is_raw_tree_value(value: Any) -> bool:
    """True when ``value`` is a raw accessibility-tree payload.

    Mirrors the FC-EVIDENCE-02 exclusion oracle (RD-01): a node-LIST of node
    dicts, OR a mapping carrying ``roots``/``children`` node payloads.
    """
    if isinstance(value, list):
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
        return "roots" in value or "children" in value
    return False


def _compact_output(output: Dict[str, Any]) -> Dict[str, Any]:
    """Strip raw-tree / large-blob payloads from a step output dict (compact).

    Recursively (A10-CRIT-002) excludes a raw ``tree`` payload (node-LIST or
    roots/children object) under ANY nested path and any raw ``stdout`` blob that
    embeds a nested tree, while retaining bounded classification residue
    (failure_code, failure_class, source_step_*, bounded selector_diagnostics,
    guidance, nodes, fingerprints). selector_diagnostics retained inside the
    output is re-bounded to <= 5 candidates (FC-EVIDENCE-03).
    """
    return _compact_mapping(output)


def _compact_mapping(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively shape a mapping, dropping raw-tree / embedded-tree values."""
    compact: Dict[str, Any] = {}
    for key, value in mapping.items():
        if key == "tree" and _is_raw_tree_value(value):
            continue
        if key == "stdout" and _stdout_embeds_tree(value):
            continue
        if key == "selector_diagnostics":
            bounded = _bounded_selector_diagnostics(value)
            if bounded is not None:
                compact[key] = bounded
            continue
        compact[key] = _compact_value(value)
    return compact


def _compact_value(value: Any) -> Any:
    """Recursively shape a nested value (mapping/list) for compact output."""
    if isinstance(value, dict):
        return _compact_mapping(value)
    if isinstance(value, list):
        return [_compact_value(item) for item in value]
    return value


def _stdout_embeds_tree(value: Any) -> bool:
    """True when a stdout string parses to JSON containing a raw tree payload."""
    if not isinstance(value, str) or not value.strip():
        return False

    try:
        parsed = json.loads(value)
    except (ValueError, json.JSONDecodeError):
        return False
    return _payload_contains_raw_tree(parsed)


def _payload_contains_raw_tree(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "tree" and _is_raw_tree_value(value):
                return True
            if _payload_contains_raw_tree(value):
                return True
    elif isinstance(payload, list):
        return any(_payload_contains_raw_tree(item) for item in payload)
    return False


def _artifact_refs(step: Dict[str, Any]) -> List[str]:
    """Extract a bounded artifact_refs collection from a step dict.

    No artifact paths are invented (FC-EVIDENCE-04 / RW-4): an in-memory step
    that carries no artifact references yields an empty list. Recognized
    sources are an existing ``artifact_refs`` list on the step and an
    ``artifacts`` list inside the step output.
    """
    refs: List[str] = []
    existing = step.get("artifact_refs")
    if isinstance(existing, list):
        refs.extend(str(r) for r in existing)
    output = step.get("output")
    if isinstance(output, dict):
        artifacts = output.get("artifacts")
        if isinstance(artifacts, list):
            refs.extend(str(r) for r in artifacts)
    return refs


def shape_step_record(
    step: Dict[str, Any],
    profile: str,
    run_failure_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Shape one serialized step record under the selected profile.

    ``step`` is a plain dict (``dataclasses.asdict(ScenarioRunStepResult)``
    shape): step_id, kind, status, output, error, cleanup. ``run_failure_code``
    is the run-level ScenarioRunResult.failure_code — the TOTAL classifier over
    non-passed steps (scenario_run.py _failure_code) — used as the failure_code
    fallback for a failed step whose own output carries no code/class, so every
    failed record keeps a non-empty classification (FC-CLASS-03 totality).

    DEEP returns the record unchanged (full detail retained, FC-PROFILE-02).
    COMPACT preserves step_id/kind/status/error/cleanup, excludes the raw
    ``output.tree`` payload and any other large blob, attaches the derived
    diagnostic_summary, copies upstream failure_code/failure_class and bounded
    selector_diagnostics verbatim, and attaches an artifact_refs collection.
    """
    if profile == DEEP:
        return step

    output = step.get("output")
    output = output if isinstance(output, dict) else {}

    status = str(step.get("status", ""))
    error = step.get("error", "")
    failure_class = output.get("failure_class")
    failure_code = output.get("failure_code")
    if failure_code is None and failure_class is None and status != _PASSED:
        # Run-level total classifier fallback (FC-CLASS-03): a failed step whose
        # own output carries no code/class still inherits the run failure_code.
        failure_code = run_failure_code or None

    record: Dict[str, Any] = {
        "step_id": step.get("step_id"),
        "kind": step.get("kind"),
        "status": status,
        "cleanup": bool(step.get("cleanup", False)),
        "output": _compact_output(output),
        "artifact_refs": _artifact_refs(step),
        "diagnostic_summary": diagnostic_summary(
            status, error, failure_code, failure_class
        ),
    }
    if isinstance(error, str) and error:
        record["error"] = error
    if failure_code is not None:
        record["failure_code"] = failure_code
    if failure_class is not None:
        record["failure_class"] = failure_class
    selector = _bounded_selector_diagnostics(output.get("selector_diagnostics"))
    if selector is not None:
        record["selector_diagnostics"] = selector
    guidance = output.get("guidance")
    if guidance is not None:
        record["guidance"] = guidance
    return record


def shape_step_records(
    steps: List[Dict[str, Any]],
    profile: str,
    run_failure_code: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Shape a list of serialized step records under the selected profile."""
    return [shape_step_record(step, profile, run_failure_code) for step in steps]


def classified_failure_count(steps: List[Dict[str, Any]]) -> int:
    """Count step records that represent a classified failure.

    A step is a classified failure when its status is not "passed" — every such
    record carries a stable failure_code/failure_class (total over non-passed
    steps), so its compact record preserves a classification (FC-OBS-01
    preserved_failure_count).
    """
    return sum(1 for step in steps if str(step.get("status", "")) != _PASSED)


def build_profile_selection_event(
    profile: str,
    preserved_failure_count: int,
) -> DiagnosticEvent:
    """Build the LE-02 evidence.profile.selected payload (PURE — no emission).

    A10-CRIT-001: the domain shaper constructs the LE-02 DiagnosticEvent value
    object but performs NO I/O and drives NO sink. Emission is owned by the
    adapter/command boundary, which records the returned value through the
    observability port exactly once per scenario/smoke invocation.

    ``raw_tree_included`` is False under compact and True under deep. The event
    carries no raw tree and no large blob (the summary field stays None).
    """
    return DiagnosticEvent(
        action=ACTION_PROFILE_SELECTED,
        profile=profile,
        raw_tree_included=(profile == DEEP),
        preserved_failure_count=preserved_failure_count,
    )
