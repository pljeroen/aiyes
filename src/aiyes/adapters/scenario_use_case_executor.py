"""Scenario executor backed by existing AIYES use cases."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from aiyes.domain.matching import name_matches, normalize_whitespace
from aiyes.domain.scenario import _VALID_DIRECTIONS, ScenarioStep
from aiyes.domain.scenario_assertions import evaluate_scenario_assertion
from aiyes.domain.tree import AccessibilityTree, flatten_nodes
from aiyes.ports.scenario_executor import ScenarioStepExecutionResult

_NO_PROGRESS_ATTEMPT_LIMIT = 2
_SELECTOR_DIAGNOSTIC_LIMIT = 5

# AIYES-103 stable, non-interpolated classified failure codes.
_FC_EMPTY_SOURCE_NODE_ID = "empty_source_node_id"
_FC_REQUIRED_FIND_NO_NODES = "required_find_no_nodes"

# AIYES-104 stable step-level wait-family timeout classification code.
_FC_STEP_TIMEOUT = "step_timeout"

# AIYES-104 wait-family kinds subject to timeout classification.
_WAIT_FAMILY_KINDS = frozenset(("wait", "wait_stable", "wait_reactive"))

# AIYES-104 terminal failure_code set for wait_reactive unmatched-terminal
# classification (FORMAL_CONSTRAINT_MAP FC-WAIT-03 / domain ReactiveWaitResult
# _VALID_FAILURE_CODES).
_REACTIVE_TERMINAL_FAILURE_CODES = frozenset(
    (
        "timeout",
        "unsupported_condition",
        "observer_error",
        "invalid_pattern",
        "session_not_found",
    )
)

# OD-03: diagnostic_summary is a single line truncated at 200 chars.
_SUMMARY_MAX_LEN = 200

# AIYES-105 near-name token-overlap threshold (CANON_REQ_PKG AIYES-105-CLAR-01).
_NEAR_NAME_OVERLAP_THRESHOLD = 0.5

# AIYES-105 exact summary markers (OD-03 / FC-SUMMARY-02 / FC-SUMMARY-03).
_SUMMARY_AFFIRMATIVE_MARKER = "near-name candidate(s) found"
_SUMMARY_NEGATIVE_PHRASE = "no near-name candidates found"

# AIYES-105 stable classified failure code for role-drift selector failures.
_FC_ROLE_DRIFT = "selector_role_drift"

# AIYES-105 token split on non-alphanumeric boundaries.
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def _bounded_summary(summary: object) -> str:
    """Single line truncated to SUMMARY_MAX_LEN; "" when no text (OD-03)."""
    if not isinstance(summary, str) or not summary:
        return ""
    return summary.splitlines()[0][:_SUMMARY_MAX_LEN]


def near_name(name: str, pattern: str) -> bool:
    """Diagnostic-only near-name predicate (FC-NEARNAME-01).

    Casefold + whitespace-normalize both sides. A near-name match holds iff
    either side contains the other OR the token overlap is at least
    ``_NEAR_NAME_OVERLAP_THRESHOLD`` (denominator = the smaller token set).
    Tokens split on non-alphanumeric boundaries; empty tokens discarded. An
    empty normalized side OR an empty token set yields ``False`` (no
    div-by-zero, no match-all).

    This predicate is DIAGNOSTIC-ONLY and MUST NOT be used for real find /
    name_matches behavior (FC-FINDPURE-01). It reuses
    ``normalize_whitespace`` but does not alter it.
    """
    n = normalize_whitespace(name).casefold()
    p = normalize_whitespace(pattern).casefold()
    if n == "" or p == "":
        return False
    tn = {t for t in _TOKEN_SPLIT.split(n) if t}
    tp = {t for t in _TOKEN_SPLIT.split(p) if t}
    if not tn or not tp:
        return False
    if (p in n) or (n in p):
        return True
    inter = len(tn & tp)
    denom = min(len(tn), len(tp))
    return (inter / denom) >= _NEAR_NAME_OVERLAP_THRESHOLD


class ScenarioUseCaseExecutor:
    """Execute scenario steps through existing use-case boundaries."""

    def __init__(
        self,
        *,
        session_start: Any,
        inspect: Any,
        find: Any,
        action: Any,
        type_text: Any,
        screenshot: Any,
        session_stop: Any,
        navigate: Any = None,
        wait: Any = None,
        wait_stable: Any = None,
        reactive_wait: Any = None,
        key: Any = None,
        sleeper: Any = None,
        mouse: Any = None,
        gesture: Any = None,
        native_scroll: Any = None,
        session_repo: Any = None,
        clock: Any = None,
        diagnostic_log: Any = None,
    ) -> None:
        self._session_start = session_start
        self._inspect = inspect
        self._find = find
        self._action = action
        self._type_text = type_text
        self._screenshot = screenshot
        self._session_stop = session_stop
        self._navigate = navigate
        self._wait = wait
        self._wait_stable = wait_stable
        self._reactive_wait = reactive_wait
        self._key = key
        self._sleeper = sleeper
        self._mouse = mouse
        self._gesture = gesture
        self._native_scroll = native_scroll
        self._session_repo = session_repo
        self._clock = clock
        self._diagnostic_log = diagnostic_log
        self._session_id = ""
        self._outputs: dict[str, Any] = {}
        self._step_kinds: dict[str, str] = {}
        # The requested selector triple of each executed find step, recorded
        # INDEPENDENTLY of selector_diagnostics so a consumed-empty action can
        # always attribute requested_selector for a find source even when the
        # find produced no diagnostics (FC-DIAG-06 / R-01).
        self._find_selectors: dict[str, dict[str, Any]] = {}
        self._viewport_cache: dict[str, tuple[int, int]] = {}

    def execute(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Execute one scenario step and return a normalized result."""
        self._step_kinds[step.id] = step.kind
        if step.kind == "assert":
            return self._execute_assert(step)
        if step.kind == "scroll_into_view":
            return self._execute_scroll_into_view(step)
        if step.kind == "find":
            return self._execute_find(step)
        if step.kind == "action":
            return self._execute_action(step)

        try:
            output, session_id = self._execute(step)
        except Exception as exc:
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="failed",
                output={},
                error=str(exc),
                session_id=self._session_id,
            )

        self._outputs[step.id] = output

        if step.kind in _WAIT_FAMILY_KINDS and self._is_wait_family_failure(
            step, output
        ):
            self._emit_classification(
                step.id,
                _FC_STEP_TIMEOUT,
                self._wait_family_summary(step, output),
                contract_id="AIYES-104",
            )
            # FC-PRESERVE-08 / RISK-4: status changes only; the original jsonable
            # output mapping is returned verbatim (no key added, dropped, or
            # mutated), so the failed step surfaces the same output as a pass.
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="failed",
                output=output,
                error=_FC_STEP_TIMEOUT,
                session_id=session_id,
            )

        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output=output,
            error="",
            session_id=session_id,
        )

    @staticmethod
    def _is_wait_family_failure(step: ScenarioStep, output: Any) -> bool:
        """Classify a wait-family timeout / unmatched-terminal as a failure.

        AIYES-104 (FC-WAIT-01/02/03, FC-POLICY-04/05, FC-WAIT-07): a wait,
        wait_stable, or wait_reactive step whose output is a timeout
        (output["timeout"] is True) — or, for wait_reactive, an unmatched
        terminal failure (matched is False AND failure_code in the closed
        terminal set) — fails by default. The single explicit boolean opt-in
        ``allow_timeout: true`` (OD-02) keeps such an output a passed
        observation. A satisfied/matched result (no timeout, no unmatched
        terminal) always stays passed.
        """
        if not isinstance(output, Mapping):
            return False
        if step.parameters.get("allow_timeout") is True:
            return False
        if output.get("timeout") is True:
            return True
        if step.kind == "wait_reactive":
            return (
                output.get("matched") is False
                and output.get("failure_code") in _REACTIVE_TERMINAL_FAILURE_CODES
            )
        return False

    @staticmethod
    def _wait_family_summary(step: ScenarioStep, output: Mapping[str, Any]) -> str:
        failure_code = output.get("failure_code")
        if failure_code:
            return (
                f"wait-family step {step.id!r} ({step.kind}) classified failed "
                f"(failure_code={failure_code!r})"
            )
        return (
            f"wait-family step {step.id!r} ({step.kind}) classified failed on timeout"
        )

    def _execute_find(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Execute a find step, applying the explicit required/optional policy.

        Optional finds (required absent or False) with zero nodes remain passed
        observations (FC-COMPAT-01/02). A required find with zero nodes fails at
        the find step with a stable failure_code and selector_diagnostics when
        available (FC-FIND-01/02/03), and emits LE-01.
        """
        # Record the requested selector triple for this find BEFORE execution,
        # so a later consumed-empty action can attribute requested_selector for
        # this find source regardless of whether diagnostics were produced.
        self._find_selectors[step.id] = _requested_selector_triple(step.parameters)

        try:
            output, session_id = self._execute(step)
        except Exception as exc:
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="failed",
                output={},
                error=str(exc),
                session_id=self._session_id,
            )

        self._outputs[step.id] = output
        nodes = output.get("nodes") if isinstance(output, Mapping) else None
        required = step.parameters.get("required") is True
        if required and not nodes:
            failure_output = dict(output)
            failure_output["failure_code"] = _FC_REQUIRED_FIND_NO_NODES
            failure_output["source_step_id"] = step.id
            failure_output["source_step_kind"] = "find"
            self._outputs[step.id] = failure_output
            self._emit_classification(
                step.id,
                _FC_REQUIRED_FIND_NO_NODES,
                self._find_summary(step, failure_output),
            )
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="failed",
                output=failure_output,
                error=_FC_REQUIRED_FIND_NO_NODES,
                session_id=session_id,
            )

        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output=output,
            error="",
            session_id=session_id,
        )

    def _execute_action(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Execute an action step, classifying a consumed-empty source failure.

        When the action consumes a source step whose output has no usable node
        id (and no explicit node_id is supplied), the action fails BEFORE
        invoking the underlying action use case (FC-DIAG-01) with structured
        diagnostics (FC-DIAG-02..07) and emits LE-01.
        """
        params = step.parameters
        if not params.get("node_id"):
            source = params.get("source")
            if source:
                source_key = str(source)
                # AIYES-103 classification applies to a source step that ran and
                # produced an output with no usable node id. A genuinely missing
                # source step (never produced) keeps the legacy free-form path.
                if source_key in self._outputs and not _first_node_id(
                    self._outputs[source_key]
                ):
                    return self._consumed_empty_action_failure(
                        step, source_key, self._outputs[source_key]
                    )

        try:
            output, session_id = self._execute(step)
        except Exception as exc:
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="failed",
                output={},
                error=str(exc),
                session_id=self._session_id,
            )

        self._outputs[step.id] = output
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="passed",
            output=output,
            error="",
            session_id=session_id,
        )

    def _consumed_empty_action_failure(
        self,
        step: ScenarioStep,
        source_key: str,
        source_output: Any,
    ) -> ScenarioStepExecutionResult:
        failure_output: dict[str, Any] = {
            "failure_code": _FC_EMPTY_SOURCE_NODE_ID,
            "source_step_id": source_key,
        }
        source_kind = self._step_kinds.get(source_key)
        if source_kind is not None:
            failure_output["source_step_kind"] = source_kind
        # FC-DIAG-06 (R-01): requested_selector is populated for EVERY find
        # source from the selector triple recorded when the find ran —
        # independent of whether the find produced selector_diagnostics.
        if source_kind == "find" and source_key in self._find_selectors:
            failure_output["requested_selector"] = dict(
                self._find_selectors[source_key]
            )
        elif isinstance(source_output, Mapping):
            requested_selector = self._requested_selector_from_source(source_output)
            if requested_selector is not None:
                failure_output["requested_selector"] = requested_selector
        # selector_diagnostics is reused verbatim only when the source genuinely
        # produced it; it is never fabricated (FC-DIAG-07).
        if isinstance(source_output, Mapping):
            diagnostics = source_output.get("selector_diagnostics")
            if diagnostics is not None:
                failure_output["selector_diagnostics"] = diagnostics
        self._outputs[step.id] = failure_output
        self._emit_classification(
            step.id,
            _FC_EMPTY_SOURCE_NODE_ID,
            f"action {step.id!r} consumed empty source {source_key!r}",
        )
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="failed",
            output=failure_output,
            error=_FC_EMPTY_SOURCE_NODE_ID,
            session_id=self._session_id,
        )

    @staticmethod
    def _requested_selector_from_source(
        source_output: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Recover the requested selector triple from a find source output.

        Prefers the selector embedded in selector_diagnostics (the same triple
        the find branch built); falls back to None when absent.
        """
        diagnostics = source_output.get("selector_diagnostics")
        if isinstance(diagnostics, Mapping):
            requested = diagnostics.get("requested_selector")
            if isinstance(requested, Mapping):
                return {
                    "role": requested.get("role"),
                    "name_pattern": requested.get("name_pattern"),
                    "state": requested.get("state"),
                }
        return None

    @staticmethod
    def _find_summary(step: ScenarioStep, output: Mapping[str, Any]) -> str:
        role = step.parameters.get("role", "*")
        name_pattern = step.parameters.get("name_pattern", step.parameters.get("name"))
        return (
            f"required find {step.id!r} matched zero nodes "
            f"(role={role!r}, name_pattern={name_pattern!r})"
        )

    def _emit_classification(
        self,
        step_id: str,
        failure_code: str,
        summary: str,
        contract_id: str = "AIYES-103",
    ) -> None:
        """Emit one LE-01 event, fail-open (FC-OBS-01/02). Never raises.

        Per the DiagnosticEventPort invariant (R-02) the OBSERVABLE emission-
        failure count is owned and self-incremented by the sink. The try/except
        here is defense-in-depth only — it guarantees a non-conforming sink can
        never break the step/run outcome; it deliberately does NOT touch the
        count, which the conforming production sink owns.

        ``contract_id`` identifies the slice that classified the failure
        (AIYES-103 find/action policy, AIYES-104 wait-family timeout policy).
        """
        log = self._diagnostic_log
        if log is None:
            return
        try:
            from aiyes.domain.diagnostic_event import DiagnosticEvent

            log.emit_event(
                DiagnosticEvent(
                    action="scenario.diagnostic.failure_classified",
                    contract_id=contract_id,
                    step_id=step_id,
                    failure_code=failure_code,
                    diagnostic_summary=_bounded_summary(summary),
                )
            )
        except Exception:
            # Fail-open: emission must never affect the step/run outcome.
            pass

    def _execute_assert(self, step: ScenarioStep) -> ScenarioStepExecutionResult:
        """Evaluate an assertion step against accumulated step outputs.

        Assert needs output-on-failure semantics, unlike the other kinds
        whose failure path is exception-only. Implementation note: the
        assertion context is the executor's own _outputs dict, identical
        in shape to scenario_run.py's run-layer assertion path.
        """
        assertion = step.parameters.get("assertion")
        if not isinstance(assertion, Mapping):
            assertion = {}
        assertion_result = evaluate_scenario_assertion(assertion, self._outputs)
        output = {
            "assertion": {
                "assertion_id": assertion_result.assertion_id,
                "kind": assertion_result.kind,
                "status": assertion_result.status,
                "message": assertion_result.message,
            }
        }
        self._outputs[step.id] = output
        if assertion_result.status == "passed":
            return ScenarioStepExecutionResult(
                step_id=step.id,
                status="passed",
                output=output,
                error="",
                session_id="",
            )
        return ScenarioStepExecutionResult(
            step_id=step.id,
            status="failed",
            output=output,
            error=assertion_result.message,
            session_id="",
        )

    def _execute_scroll_into_view(
        self, step: ScenarioStep
    ) -> ScenarioStepExecutionResult:
        """Scroll a list until target node is visible, or fail with diagnostics.

        Two-bound termination: max_scrolls AND max_seconds. Cross-platform
        via gesture.swipe — the dispatching gesture port routes to the
        appropriate adapter (adb input swipe on Android, mouse-drag
        substitute on Linux).
        """
        try:
            session_id = self._require_session()
        except Exception as exc:
            return ScenarioStepExecutionResult(
                step_id=step.id, status="failed", output={}, error=str(exc)
            )

        params = step.parameters
        role = str(params.get("role", "*"))
        name_pattern = params.get("name_pattern")
        state = params.get("state")
        direction = str(params.get("direction", "down"))
        max_scrolls = int(params.get("max_scrolls", 10))
        max_seconds = float(params.get("max_seconds", 30.0))

        viewport = _parse_viewport(
            self._session_repo, session_id, _cache=self._viewport_cache
        )

        clock = self._clock
        start = clock.now() if clock is not None else 0.0
        attempts = 0
        scroll_attempts: list[dict[str, Any]] = []
        unchanged_by_scrollable: dict[str, int] = {}
        latest_selector_diagnostics: Optional[dict[str, Any]] = None

        while True:
            nodes = self._find.execute(
                session_id=session_id,
                role=role,
                name_pattern=name_pattern,
                state=state,
                no_prune=False,
            )
            node_id = _first_node_id(nodes if isinstance(nodes, list) else nodes)
            if node_id:
                if scroll_attempts:
                    scroll_attempts[-1]["progress"] = "target_appeared"
                elapsed = (clock.now() - start) if clock is not None else 0.0
                output = {
                    "found": True,
                    "node_id": node_id,
                    "attempts": attempts,
                    "elapsed": elapsed,
                    "direction": direction,
                    "role_match": "exact",
                    "scroll_attempts": scroll_attempts,
                }
                self._outputs[step.id] = output
                return ScenarioStepExecutionResult(
                    step_id=step.id, status="passed", output=output, error=""
                )

            elapsed_now = (clock.now() - start) if clock is not None else 0.0
            advisory_result = self._resolve_scroll_into_view_role_drift(
                step_id=step.id,
                session_id=session_id,
                requested_role=role,
                name_pattern=name_pattern,
                state=state,
                attempts=attempts,
                elapsed=elapsed_now,
                direction=direction,
                scroll_attempts=scroll_attempts,
            )
            if advisory_result is not None:
                return advisory_result

            if attempts >= max_scrolls:
                latest_selector_diagnostics = self._ensure_selector_diagnostics(
                    latest_selector_diagnostics,
                    session_id=session_id,
                    viewport=viewport,
                    requested_role=role,
                    name_pattern=name_pattern,
                    state=state,
                )
                return self._scroll_into_view_failure(
                    step.id,
                    attempts,
                    elapsed_now,
                    direction,
                    viewport,
                    "scrolls",
                    scroll_attempts,
                    latest_selector_diagnostics,
                )
            if elapsed_now >= max_seconds:
                latest_selector_diagnostics = self._ensure_selector_diagnostics(
                    latest_selector_diagnostics,
                    session_id=session_id,
                    viewport=viewport,
                    requested_role=role,
                    name_pattern=name_pattern,
                    state=state,
                )
                return self._scroll_into_view_failure(
                    step.id,
                    attempts,
                    elapsed_now,
                    direction,
                    viewport,
                    "seconds",
                    scroll_attempts,
                    latest_selector_diagnostics,
                )

            before_tree = _inspect_tree_snapshot(self._inspect, session_id)
            latest_selector_diagnostics = _selector_diagnostics(
                before_tree,
                viewport,
                requested_role=role,
                name_pattern=name_pattern,
                state=state,
            )
            before_fingerprint = _tree_snapshot_fingerprint(before_tree)
            scrollable = _select_scrollable_candidate(
                _scrollable_candidates(before_tree, viewport),
                context_bounds=None,
            )
            if scrollable is None:
                x1, y1, x2, y2 = _swipe_coords_for_direction(direction, viewport)
                method = "viewport_swipe"
                selected_scrollable_id = None
                selected_bounds = None
            else:
                x1, y1, x2, y2 = _swipe_coords_in_bounds(
                    direction, scrollable.bounds, viewport
                )
                method = "scrollable_region_swipe"
                selected_scrollable_id = scrollable.node_id
                selected_bounds = list(scrollable.bounds)
                native_output = self._try_native_scroll(
                    session_id=session_id,
                    node_id=scrollable.node_id,
                    direction=direction,
                    stable_id=scrollable.stable_id,
                    bounds=scrollable.bounds,
                )
                if native_output is not None and native_output.get("success") is True:
                    after_tree = _inspect_tree_snapshot(self._inspect, session_id)
                    after_fingerprint = _tree_snapshot_fingerprint(after_tree)
                    progress = _tree_snapshot_progress(
                        before_fingerprint, after_fingerprint
                    )
                    scroll_attempts.append(
                        {
                            "method": "native_scroll",
                            "direction": direction,
                            "selected_scrollable_id": selected_scrollable_id,
                            "selected_bounds": selected_bounds,
                            "native_scroll": native_output,
                            "tree_changed": progress == "changed",
                            "progress": progress,
                            "tree_fingerprint_before": before_fingerprint,
                            "tree_fingerprint_after": after_fingerprint,
                        }
                    )
                    attempts += 1
                    if _record_no_progress(
                        unchanged_by_scrollable,
                        selected_scrollable_id,
                        progress,
                    ):
                        elapsed_no_progress = (
                            (clock.now() - start) if clock is not None else 0.0
                        )
                        return self._scroll_into_view_failure(
                            step.id,
                            attempts,
                            elapsed_no_progress,
                            direction,
                            viewport,
                            "no_progress",
                            scroll_attempts,
                            latest_selector_diagnostics,
                        )
                    continue
            self._gesture.swipe(session_id, x1, y1, x2, y2, 300)
            after_tree = _inspect_tree_snapshot(self._inspect, session_id)
            after_fingerprint = _tree_snapshot_fingerprint(after_tree)
            progress = _tree_snapshot_progress(before_fingerprint, after_fingerprint)
            attempt = {
                "method": method,
                "direction": direction,
                "coordinates": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "selected_scrollable_id": selected_scrollable_id,
                "selected_bounds": selected_bounds,
                "tree_changed": progress == "changed",
                "progress": progress,
                "tree_fingerprint_before": before_fingerprint,
                "tree_fingerprint_after": after_fingerprint,
            }
            if scrollable is not None and native_output is not None:
                attempt["native_scroll"] = native_output
                attempt["fallback_reason"] = native_output.get(
                    "fallback_reason", "native_scroll_failed"
                )
            scroll_attempts.append(attempt)
            attempts += 1
            if _record_no_progress(
                unchanged_by_scrollable,
                selected_scrollable_id,
                progress,
            ):
                elapsed_no_progress = (
                    (clock.now() - start) if clock is not None else 0.0
                )
                return self._scroll_into_view_failure(
                    step.id,
                    attempts,
                    elapsed_no_progress,
                    direction,
                    viewport,
                    "no_progress",
                    scroll_attempts,
                    latest_selector_diagnostics,
                )

    def _try_native_scroll(
        self,
        *,
        session_id: str,
        node_id: str,
        direction: str,
        stable_id: str,
        bounds: tuple[int, int, int, int],
    ) -> Optional[dict[str, Any]]:
        if self._native_scroll is None or self._session_repo is None:
            return None
        try:
            session = self._session_repo.load(session_id)
        except Exception:
            return None
        if getattr(session, "backend", "") != "android":
            return None
        try:
            return _jsonable_dict(
                self._native_scroll.scroll(
                    session,
                    node_id,
                    direction,
                    stable_id=stable_id,
                    bounds=bounds,
                )
            )
        except Exception as exc:
            return {
                "success": False,
                "method": "android_accessibility_helper",
                "node_id": node_id,
                "direction": direction,
                "stable_id": stable_id,
                "bounds": list(bounds),
                "fallback_reason": str(exc) or "native_scroll_failed",
            }

    def _resolve_scroll_into_view_role_drift(
        self,
        *,
        step_id: str,
        session_id: str,
        requested_role: str,
        name_pattern: Any,
        state: Any,
        attempts: int,
        elapsed: float,
        direction: str,
        scroll_attempts: list[dict[str, Any]],
    ) -> Optional[ScenarioStepExecutionResult]:
        if requested_role == "*" or not _non_empty_pattern(name_pattern):
            return None

        wildcard_nodes = self._find.execute(
            session_id=session_id,
            role="*",
            name_pattern=name_pattern,
            state=state,
            no_prune=False,
        )
        candidates = _candidate_nodes(wildcard_nodes)
        if not candidates:
            return None

        actionable_candidates = [
            node
            for node in candidates
            if _role_drift_compatible(node, requested_role)
            and _scroll_target_actionable(node)
        ]
        if len(actionable_candidates) == 1:
            node = actionable_candidates[0]
            output = {
                "found": True,
                "node_id": _node_id(node),
                "attempts": attempts,
                "elapsed": elapsed,
                "direction": direction,
                "role_match": "advisory",
                "requested_role": requested_role,
                "actual_role": _node_role(node),
                "matched_name": _node_name(node),
                "scroll_attempts": scroll_attempts,
            }
            self._outputs[step_id] = output
            return ScenarioStepExecutionResult(
                step_id=step_id, status="passed", output=output, error=""
            )

        output = _role_drift_failure_output(
            candidates=candidates,
            actionable_candidates=actionable_candidates,
            requested_role=requested_role,
            name_pattern=str(name_pattern),
            attempts=attempts,
            elapsed=elapsed,
            direction=direction,
            viewport=_parse_viewport(
                self._session_repo,
                session_id,
                _cache=self._viewport_cache,
            ),
        )
        output["scroll_attempts"] = scroll_attempts
        output["failure_class"] = "ambiguous_role_drift"
        self._outputs[step_id] = output
        if len(actionable_candidates) > 1:
            error = "scroll_into_view_role_drift_ambiguous"
        else:
            error = "scroll_into_view_role_drift_no_actionable_candidate"
        # AIYES-105 (FC-OBS-01): emit exactly one LE-01 for this classified
        # role-drift selector failure, carrying the bounded near-name summary.
        selector_diagnostics = output.get("selector_diagnostics")
        summary = (
            selector_diagnostics.get("summary", "")
            if isinstance(selector_diagnostics, Mapping)
            else ""
        )
        self._emit_classification(
            step_id,
            _FC_ROLE_DRIFT,
            summary,
            contract_id="AIYES-105",
        )
        return ScenarioStepExecutionResult(
            step_id=step_id,
            status="failed",
            output=output,
            error=error,
        )

    def _ensure_selector_diagnostics(
        self,
        diagnostics: Optional[dict[str, Any]],
        *,
        session_id: str,
        viewport: tuple[int, int],
        requested_role: str,
        name_pattern: Any,
        state: Any,
    ) -> Optional[dict[str, Any]]:
        if diagnostics is not None:
            return diagnostics
        return _selector_diagnostics(
            _inspect_tree_snapshot(self._inspect, session_id),
            viewport,
            requested_role=requested_role,
            name_pattern=name_pattern,
            state=state,
        )

    def _scroll_into_view_failure(
        self,
        step_id: str,
        attempts: int,
        elapsed: float,
        direction: str,
        viewport: tuple[int, int],
        bound_hit: str,
        scroll_attempts: list[dict[str, Any]],
        selector_diagnostics: Optional[dict[str, Any]] = None,
    ) -> ScenarioStepExecutionResult:
        progress = _final_scroll_progress(scroll_attempts)
        failure_class = _scroll_failure_class(bound_hit, progress, scroll_attempts)
        output = {
            "found": False,
            "attempts": attempts,
            "elapsed": elapsed,
            "direction": direction,
            "viewport": list(viewport),
            "bound_hit": bound_hit,
            "progress": progress,
            "failure_class": failure_class,
            "scroll_attempts": scroll_attempts,
        }
        if selector_diagnostics is not None:
            output["selector_diagnostics"] = selector_diagnostics
        if failure_class == "no_scrollable" and progress == "unchanged":
            # AIYES-106: read-only over the already-computed failure evidence —
            # attach app-side accessibility-exposure guidance and emit LE-01.
            output["guidance"] = _no_scrollable_exposure_guidance(
                failure_class=failure_class,
                progress=progress,
                attempts=attempts,
                direction=direction,
                viewport=viewport,
            )
            self._emit_classification(
                step_id,
                "no_scrollable",
                output["guidance"]["summary"],
                contract_id="AIYES-106",
            )
        self._outputs[step_id] = output
        return ScenarioStepExecutionResult(
            step_id=step_id,
            status="failed",
            output=output,
            error=(
                "scroll_into_view_target_not_found "
                f"({failure_class}; bound: {bound_hit}, attempts: {attempts})"
            ),
        )

    def _execute(self, step: ScenarioStep) -> tuple[dict[str, Any], str]:
        params = step.parameters
        if step.kind == "start_session":
            result = self._session_start.execute(**_start_kwargs(params))
            session_id = _get_attr(result, "session_id")
            if not session_id:
                raise RuntimeError("start_session did not return a session_id")
            self._session_id = str(session_id)
            return _jsonable_dict(result), self._session_id

        if step.kind == "inspect":
            result = self._inspect.execute(
                session_id=self._require_session(),
                no_screenshot=bool(params.get("no_screenshot", False)),
                no_tree=bool(params.get("no_tree", False)),
                tree_depth=params.get("tree_depth"),
                no_prune=bool(params.get("no_prune", False)),
                screenshot_base64=bool(params.get("screenshot_base64", False)),
                focus_window=params.get("focus_window"),
            )
            return _jsonable_dict(result), ""

        if step.kind == "find":
            session_id = self._require_session()
            role = str(params.get("role", "*"))
            name_pattern = params.get("name_pattern", params.get("name"))
            result = self._find.execute(
                session_id=session_id,
                role=role,
                name_pattern=name_pattern,
                state=params.get("state"),
                no_prune=bool(params.get("no_prune", False)),
            )
            nodes = [_jsonable_dict(node) for node in result]
            output: dict[str, Any] = {"nodes": nodes}
            if not nodes:
                diagnostics = _selector_diagnostics(
                    _inspect_tree_snapshot(self._inspect, session_id),
                    _parse_viewport(
                        self._session_repo,
                        session_id,
                        _cache=self._viewport_cache,
                    ),
                    requested_role=role,
                    name_pattern=name_pattern,
                    state=params.get("state"),
                )
                if diagnostics is not None:
                    output["selector_diagnostics"] = diagnostics
            return output, ""

        if step.kind == "action":
            result = self._action.execute(
                session_id=self._require_session(),
                node_id=self._resolve_node_id(params),
                action_name=str(params.get("action", "click")),
                value=params.get("value"),
            )
            return _jsonable_dict(result), ""

        if step.kind == "type_text":
            result = self._type_text.execute(
                session_id=self._require_session(),
                text=str(params.get("text", "")),
                delay_ms=int(params.get("delay_ms", 0)),
            )
            return _jsonable_dict(result), ""

        if step.kind == "screenshot":
            result = self._screenshot.execute(
                session_id=self._require_session(),
                output_path=params.get("output_path"),
                base64=bool(params.get("base64", False)),
                region=params.get("region"),
                node_id=params.get("node_id"),
            )
            return _jsonable_dict(result), ""

        if step.kind == "navigate" and self._navigate is not None:
            result = self._navigate.execute(
                session_id=self._require_session(),
                action=str(params.get("action", "")),
            )
            return _jsonable_dict(result), ""

        if step.kind == "stop_session":
            result = self._session_stop.execute(session_id=self._session_id or None)
            output = _jsonable_dict(result)
            self._session_id = ""
            return output, ""

        if step.kind == "wait" and self._wait is not None:
            result = self._wait.execute(
                session_id=self._require_session(),
                role=str(params.get("role", "*")),
                name_pattern=params.get("name_pattern", params.get("name")),
                timeout=params.get("timeout"),
                state=params.get("state"),
                absent=bool(params.get("absent", False)),
                transient=bool(params.get("transient", False)),
            )
            return _jsonable_dict(result), ""

        if step.kind == "wait_reactive" and self._reactive_wait is not None:
            result = self._reactive_wait.execute(
                session_id=self._require_session(),
                condition=str(params.get("condition", "")),
                name_pattern=params.get("name_pattern"),
                timeout=float(params.get("timeout", 10.0)),
                quiet=float(params.get("quiet", 0.0)),
                poll_interval=float(params.get("poll_interval", 0.25)),
            )
            return _jsonable_dict(result), ""

        if step.kind == "key" and self._key is not None:
            keys = params.get("keys", ())
            if not isinstance(keys, (list, tuple)) or not keys:
                raise RuntimeError("key step requires a non-empty keys list")
            result = self._key.execute(
                session_id=self._require_session(),
                key_specs=[str(item) for item in keys],
            )
            return _jsonable_dict(result), ""

        if step.kind == "sleep":
            seconds = float(params.get("seconds", 0.0))
            reason = str(params.get("reason", ""))
            if self._sleeper is not None:
                self._sleeper.sleep(seconds)
            else:
                import time as _time

                _time.sleep(seconds)
            return {"slept": seconds, "reason": reason}, ""

        if step.kind == "mouse_drag" and self._mouse is not None:
            x1, y1, x2, y2 = self._resolve_drag_coords(params)
            self._mouse.drag(self._require_session(), x1, y1, x2, y2)
            return {"moved": True, "from": [x1, y1], "to": [x2, y2]}, ""

        if step.kind == "gesture_pinch" and self._gesture is not None:
            x, y = self._resolve_point_coords(params)
            if "scale_factor" not in params:
                raise RuntimeError("gesture_pinch requires scale_factor")
            scale_factor = float(params["scale_factor"])
            self._gesture.pinch(self._require_session(), x, y, scale_factor)
            return {
                "pinched": True,
                "center": [x, y],
                "scale_factor": scale_factor,
            }, ""

        if step.kind == "gesture_two_finger_scroll" and self._gesture is not None:
            x, y = self._resolve_point_coords(params)
            direction = str(params.get("direction", ""))
            if direction not in _VALID_DIRECTIONS:
                raise RuntimeError(
                    f"direction_invalid: direction must be one of {sorted(_VALID_DIRECTIONS)}"
                )
            amount = int(params.get("amount", 3))
            self._gesture.two_finger_scroll(
                self._require_session(), x, y, direction, amount
            )
            return {
                "scrolled": True,
                "center": [x, y],
                "direction": direction,
                "amount": amount,
            }, ""

        if step.kind == "swipe" and self._gesture is not None:
            x1, y1, x2, y2 = self._resolve_drag_coords(params)
            duration_ms = int(params.get("duration_ms", 300))
            self._gesture.swipe(self._require_session(), x1, y1, x2, y2, duration_ms)
            return {
                "swiped": True,
                "from": [x1, y1],
                "to": [x2, y2],
                "duration_ms": duration_ms,
            }, ""

        if step.kind == "mouse_scroll" and self._mouse is not None:
            direction = str(params.get("direction", ""))
            if direction not in _VALID_DIRECTIONS:
                raise RuntimeError(
                    f"direction_invalid: direction must be one of {sorted(_VALID_DIRECTIONS)}"
                )
            amount = int(params.get("amount", 3))
            sid = self._require_session()
            source = params.get("source")
            if source is not None:
                cx, cy = self._resolve_node_center(source)
                self._mouse.move(sid, cx, cy)
            self._mouse.scroll(sid, direction, amount)
            return {"scrolled": True, "direction": direction, "amount": amount}, ""

        if step.kind == "wait_stable" and self._wait_stable is not None:
            ignore_nodes = params.get("ignore_nodes", ())
            if not isinstance(ignore_nodes, (list, tuple, frozenset, set)):
                ignore_nodes = ()
            result = self._wait_stable.execute(
                session_id=self._require_session(),
                timeout=float(params.get("timeout", 10.0)),
                poll_interval=float(params.get("interval", 0.5)),
                consecutive=int(params.get("consecutive", 3)),
                tolerance=int(params.get("tolerance", 0)),
                ignore_ids=frozenset(str(item) for item in ignore_nodes),
            )
            return _jsonable_dict(result), ""

        raise RuntimeError(f"unsupported real scenario step kind: {step.kind}")

    def _require_session(self) -> str:
        if not self._session_id:
            raise RuntimeError("scenario step requires an active session")
        return self._session_id

    def _resolve_drag_coords(
        self, params: Mapping[str, Any]
    ) -> tuple[int, int, int, int]:
        """Resolve drag endpoints. Exactly one of literal/source mode required."""
        has_literal = all(k in params for k in ("x1", "y1", "x2", "y2"))
        has_source = "source" in params
        if has_literal and has_source:
            raise RuntimeError(
                "coord_mode_ambiguous: supply literal coords OR source, not both"
            )
        if not has_literal and not has_source:
            raise RuntimeError("coord_mode_missing: supply x1/y1/x2/y2 or source/dx/dy")
        if has_literal:
            return (
                int(params["x1"]),
                int(params["y1"]),
                int(params["x2"]),
                int(params["y2"]),
            )
        cx, cy = self._resolve_node_center(params["source"])
        if "dx" not in params or "dy" not in params:
            raise RuntimeError("coord_mode_missing: source mode requires dx and dy")
        dx = int(params["dx"])
        dy = int(params["dy"])
        return (cx, cy, cx + dx, cy + dy)

    def _resolve_point_coords(self, params: Mapping[str, Any]) -> tuple[int, int]:
        """Resolve a single (x, y) point. Either literal x/y or source mode."""
        has_literal = "x" in params and "y" in params
        has_source = "source" in params
        if has_literal and has_source:
            raise RuntimeError("coord_mode_ambiguous: supply x/y OR source, not both")
        if not has_literal and not has_source:
            raise RuntimeError("coord_mode_missing: supply x/y or source")
        if has_literal:
            return (int(params["x"]), int(params["y"]))
        return self._resolve_node_center(params["source"])

    def _resolve_node_center(self, source: Any) -> tuple[int, int]:
        """Resolve a source step id to the center of its first node's bounds."""
        if not isinstance(source, str):
            raise RuntimeError("source_step_unknown: source must be a step id string")
        if source not in self._outputs:
            raise RuntimeError(f"source_step_unknown: no prior step with id {source!r}")
        output = self._outputs[source]
        bounds = _first_node_bounds(output)
        if bounds is None:
            raise RuntimeError(
                f"source_step_no_node: step {source!r} has no node with bounds"
            )
        x, y, w, h = bounds
        return (x + w // 2, y + h // 2)

    def _resolve_node_id(self, params: Mapping[str, Any]) -> str:
        explicit = params.get("node_id")
        if explicit:
            return str(explicit)

        source = params.get("source")
        if not source:
            raise RuntimeError("action step requires node_id or source")

        source_output = self._outputs.get(str(source))
        node_id = _first_node_id(source_output)
        if not node_id:
            raise RuntimeError(f"source step did not provide a node id: {source}")
        return node_id


@dataclasses.dataclass(frozen=True)
class _ScrollableCandidate:
    node_id: str
    bounds: tuple[int, int, int, int]
    area: int
    stable_id: str = ""


def _requested_selector_triple(params: Mapping[str, Any]) -> dict[str, Any]:
    """Recover a find step's requested selector triple from its parameters.

    Mirrors the find branch of _execute: name_pattern falls back to name. The
    triple is recorded for every find so a consumed-empty action can attribute
    requested_selector independent of selector_diagnostics (FC-DIAG-06).
    """
    return {
        "role": str(params.get("role", "*")),
        "name_pattern": params.get("name_pattern", params.get("name")),
        "state": params.get("state"),
    }


def _start_kwargs(params: Mapping[str, Any]) -> dict[str, Any]:
    command = params.get("command", "")
    backend = str(params.get("backend", "linux"))
    app_args: list[str]
    if not command and backend == "android":
        app_command = "adb"
        app_args = ["shell", "am", "start", "-a", "android.settings.SETTINGS"]
    elif isinstance(command, str):
        app_command = command
        app_args = []
    elif isinstance(command, Sequence):
        parts = [str(part) for part in command]
        app_command = parts[0] if parts else ""
        app_args = parts[1:]
    else:
        app_command = ""
        app_args = []

    return {
        "app_command": app_command,
        "app_args": app_args,
        "resolution": str(params.get("resolution", "1280x800")),
        "color_depth": int(params.get("color_depth", 24)),
        "wait": float(params.get("wait_seconds", params.get("wait", 2.0))),
        "name": params.get("name"),
        "backend": backend,
        "device_serial": _device_serial(params, backend),
    }


def _device_serial(params: Mapping[str, Any], backend: str) -> Any:
    serial = params.get("device_serial")
    if backend != "android" or serial != "auto":
        return serial
    return _first_adb_device_serial()


def _first_adb_device_serial() -> str:
    from aiyes.adapters.adb_path import resolve_adb_path

    completed = subprocess.run(
        [resolve_adb_path(), "devices"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("adb devices returned a non-zero exit status")
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    raise RuntimeError("no Android device is available")


def _jsonable_dict(value: Any) -> dict[str, Any]:
    converted = _to_jsonable(value)
    if isinstance(converted, dict):
        return converted
    return {"value": converted}


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            key: _to_jsonable(item)
            for key, item in dataclasses.asdict(value).items()  # type: ignore[arg-type]
            if item is not None
        }
    if isinstance(value, Mapping):
        return {
            str(key): _to_jsonable(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _to_jsonable(item)
            for key, item in vars(value).items()
            if item is not None
        }
    return value


def _get_attr(value: Any, name: str) -> Optional[Any]:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _non_empty_pattern(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _candidate_nodes(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            return list(nodes)
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def _node_role(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("role", "")
    else:
        raw = getattr(value, "role", "")
    return str(raw) if raw else ""


def _node_name(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("name", "")
    else:
        raw = getattr(value, "name", "")
    return str(raw) if raw else ""


def _node_actions(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        raw = value.get("actions", ())
    else:
        raw = getattr(value, "actions", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(action) for action in raw)


def _role_drift_compatible(value: Any, requested_role: str) -> bool:
    return _node_role(value) != requested_role


def _scroll_target_actionable(value: Any) -> bool:
    if _node_role(value).lower() == "button":
        return True
    actionable = {"click", "tap", "press", "activate", "scroll", "focus", "long_click"}
    return any(action.lower() in actionable for action in _node_actions(value))


def _role_drift_failure_output(
    *,
    candidates: list[Any],
    actionable_candidates: list[Any],
    requested_role: str,
    name_pattern: str,
    attempts: int,
    elapsed: float,
    direction: str,
    viewport: tuple[int, int],
) -> dict[str, Any]:
    selector_diagnostics = _selector_diagnostics_from_nodes(
        candidates,
        viewport,
        requested_role=requested_role,
        name_pattern=name_pattern,
        state=None,
    )
    bounded_candidates = (
        selector_diagnostics.get("candidates", [])
        if selector_diagnostics is not None
        else []
    )
    return {
        "found": False,
        "attempts": attempts,
        "elapsed": elapsed,
        "direction": direction,
        "role_match": "advisory",
        "requested_role": requested_role,
        "name_pattern": name_pattern,
        "candidate_count": len(actionable_candidates),
        "observed_roles": sorted({_node_role(node) for node in candidates}),
        "candidates": bounded_candidates,
        "selector_diagnostics": selector_diagnostics,
    }


def _inspect_tree_snapshot(inspect: Any, session_id: str) -> Any:
    if inspect is None:
        return None
    try:
        result = inspect.execute(
            session_id=session_id,
            no_screenshot=True,
            no_tree=False,
            tree_depth=None,
            no_prune=True,
            screenshot_base64=False,
            focus_window=None,
        )
    except Exception:
        return None
    if isinstance(result, Mapping):
        return result.get("tree")
    return getattr(result, "tree", None)


def _scrollable_candidates(
    tree_snapshot: Any, viewport: tuple[int, int]
) -> list[_ScrollableCandidate]:
    candidates: list[_ScrollableCandidate] = []
    for node in _tree_snapshot_nodes(tree_snapshot):
        bounds = _node_bounds(node)
        if bounds is None or not _visible_bounds(bounds, viewport):
            continue
        if not _node_scrollable(node):
            continue
        node_id = _node_id(node)
        if not node_id:
            continue
        _, _, width, height = bounds
        candidates.append(
            _ScrollableCandidate(
                node_id=node_id,
                bounds=bounds,
                area=width * height,
                stable_id=_node_stable_id(node),
            )
        )
    return candidates


def _tree_snapshot_nodes(tree_snapshot: Any) -> list[Any]:
    if tree_snapshot is None:
        return []
    if isinstance(tree_snapshot, AccessibilityTree):
        return list(flatten_nodes(tree_snapshot.roots))
    if isinstance(tree_snapshot, Mapping):
        roots = tree_snapshot.get("roots", tree_snapshot.get("tree"))
        if isinstance(roots, Sequence) and not isinstance(roots, (str, bytes)):
            return _walk_tree_nodes(roots)
        if roots is not None:
            return _walk_tree_nodes([roots])
        return _walk_tree_nodes([tree_snapshot])
    if isinstance(tree_snapshot, Sequence) and not isinstance(
        tree_snapshot, (str, bytes)
    ):
        return _walk_tree_nodes(tree_snapshot)
    roots = getattr(tree_snapshot, "roots", None)
    if isinstance(roots, Sequence) and not isinstance(roots, (str, bytes)):
        return _walk_tree_nodes(roots)
    return _walk_tree_nodes([tree_snapshot])


def _walk_tree_nodes(nodes: Sequence[Any]) -> list[Any]:
    flattened: list[Any] = []
    for node in nodes:
        flattened.append(node)
        if isinstance(node, Mapping):
            children = node.get("children", ())
        else:
            children = getattr(node, "children", ())
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            flattened.extend(_walk_tree_nodes(children))
    return flattened


def _node_scrollable(value: Any) -> bool:
    role = _node_role(value).lower()
    if "scroll" in role or role in {"list", "listview", "recyclerview"}:
        return True
    states = _node_states(value)
    if "scrollable" in states:
        return True
    if isinstance(value, Mapping):
        raw = value.get("scrollable")
        if raw is True or (isinstance(raw, str) and raw.lower() == "true"):
            return True
    return any(action.lower() == "scroll" for action in _node_actions(value))


def _node_states(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        raw = value.get("states", ())
    else:
        raw = getattr(value, "states", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(state).lower() for state in raw)


def _node_stable_id(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("stable_id", "")
    else:
        raw = getattr(value, "stable_id", "")
    return str(raw) if raw else ""


def _visible_bounds(
    bounds: tuple[int, int, int, int], viewport: tuple[int, int]
) -> bool:
    x, y, width, height = bounds
    if width <= 0 or height <= 0:
        return False
    viewport_width, viewport_height = viewport
    if x >= viewport_width or y >= viewport_height:
        return False
    if x + width <= 0 or y + height <= 0:
        return False
    return True


def _select_scrollable_candidate(
    candidates: list[_ScrollableCandidate],
    context_bounds: Optional[tuple[int, int, int, int]],
) -> Optional[_ScrollableCandidate]:
    if not candidates:
        return None
    if context_bounds is not None:
        contextual = [
            candidate
            for candidate in candidates
            if _bounds_contains(candidate.bounds, context_bounds)
            or _bounds_overlap_ratio(candidate.bounds, context_bounds) >= 0.5
        ]
        if contextual:
            return min(contextual, key=lambda candidate: candidate.area)
    return max(candidates, key=lambda candidate: candidate.area)


def _bounds_contains(
    outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]
) -> bool:
    outer_x, outer_y, outer_w, outer_h = outer
    inner_x, inner_y, inner_w, inner_h = inner
    return (
        outer_x <= inner_x
        and outer_y <= inner_y
        and outer_x + outer_w >= inner_x + inner_w
        and outer_y + outer_h >= inner_y + inner_h
    )


def _bounds_overlap_ratio(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> float:
    first_x, first_y, first_w, first_h = first
    second_x, second_y, second_w, second_h = second
    overlap_w = max(
        0,
        min(first_x + first_w, second_x + second_w) - max(first_x, second_x),
    )
    overlap_h = max(
        0,
        min(first_y + first_h, second_y + second_h) - max(first_y, second_y),
    )
    second_area = second_w * second_h
    if second_area <= 0:
        return 0.0
    return (overlap_w * overlap_h) / second_area


def _swipe_coords_in_bounds(
    direction: str,
    bounds: tuple[int, int, int, int],
    viewport: tuple[int, int],
) -> tuple[int, int, int, int]:
    x, y, width, height = bounds
    left = max(0, x)
    top = max(0, y)
    right = min(viewport[0], x + width)
    bottom = min(viewport[1] - _bottom_system_inset(viewport), y + height)

    horizontal_margin = _edge_margin(right - left)
    vertical_margin = _edge_margin(bottom - top)
    safe_left = min(right, left + horizontal_margin)
    safe_right = max(left, right - horizontal_margin)
    safe_top = min(bottom, top + vertical_margin)
    safe_bottom = max(top, bottom - vertical_margin)

    cx = (safe_left + safe_right) // 2
    cy = (safe_top + safe_bottom) // 2
    if direction == "down":
        return (cx, safe_bottom, cx, safe_top)
    if direction == "up":
        return (cx, safe_top, cx, safe_bottom)
    if direction == "left":
        return (safe_left, cy, safe_right, cy)
    if direction == "right":
        return (safe_right, cy, safe_left, cy)
    return (cx, safe_bottom, cx, safe_top)


def _edge_margin(length: int) -> int:
    if length <= 2:
        return 0
    return min(96, max(16, length // 12))


def _bottom_system_inset(viewport: tuple[int, int]) -> int:
    _, height = viewport
    return min(240, max(96, height // 20))


def _tree_snapshot_changed(before: Any, after: Any) -> bool:
    before_fingerprint = _tree_snapshot_fingerprint(before)
    after_fingerprint = _tree_snapshot_fingerprint(after)
    if before_fingerprint is None or after_fingerprint is None:
        return False
    return before_fingerprint != after_fingerprint


def _selector_diagnostics(
    tree_snapshot: Any,
    viewport: tuple[int, int],
    *,
    requested_role: str,
    name_pattern: Any,
    state: Any,
) -> Optional[dict[str, Any]]:
    if not _non_empty_pattern(name_pattern):
        return None
    pattern = str(name_pattern)
    return _selector_diagnostics_from_nodes(
        _tree_snapshot_nodes(tree_snapshot),
        viewport,
        requested_role=requested_role,
        name_pattern=pattern,
        state=state,
    )


def _selector_diagnostics_from_nodes(
    nodes: Sequence[Any],
    viewport: tuple[int, int],
    *,
    requested_role: str,
    name_pattern: str,
    state: Any,
) -> Optional[dict[str, Any]]:
    candidates = []
    for node in nodes:
        candidate = _selector_candidate_diagnostic(
            node,
            viewport,
            requested_role=requested_role,
            name_pattern=name_pattern,
        )
        if candidate is not None:
            candidates.append(candidate)

    # AIYES-105 (FC-RANK-01/02/03): classify the FULL candidate set on the
    # requested name_pattern, then sort with class_rank (exact/near before
    # unrelated) as the primary key BEFORE applying the bound, so a near-name
    # candidate is never hidden behind an unrelated same-role one. The existing
    # tie-breaks (reason_count, actionable_penalty, node_id) are retained as
    # lower-priority keys, terminating in node_id for a deterministic total order.
    candidates.sort(key=lambda c: _selector_candidate_sort_key(c, name_pattern))
    bounded = candidates[:_SELECTOR_DIAGNOSTIC_LIMIT]
    # AIYES-105 (FC-SUMMARY-04): near-name existence sourced from the FULL set.
    has_near_name = any(
        near_name(candidate.get("name", ""), name_pattern) for candidate in candidates
    )
    diagnostics: dict[str, Any] = {
        "requested_selector": {
            "role": requested_role,
            "name_pattern": name_pattern,
            "state": state,
        },
        "candidate_count": len(candidates),
        "max_candidates": _SELECTOR_DIAGNOSTIC_LIMIT,
        # AIYES-105 (FC-SUMMARY-01/05): top-level single-line summary precedes
        # raw candidate detail; bounded to SUMMARY_MAX_LEN.
        "summary": _selector_summary(has_near_name),
        "candidates": bounded,
    }
    hint = _selector_hint(requested_role, name_pattern, candidates)
    if hint:
        diagnostics["hint"] = hint
    return diagnostics


def _selector_summary(has_near_name: bool) -> str:
    """First-line near-name summary (FC-SUMMARY-01/02/03/05).

    Single line, bounded to SUMMARY_MAX_LEN. Contains the EXACT affirmative
    marker when a near-name candidate exists in the full classified set, the
    EXACT negative phrase otherwise.
    """
    if has_near_name:
        return _bounded_summary(f"selector diagnostics: {_SUMMARY_AFFIRMATIVE_MARKER}")
    return _bounded_summary(f"selector diagnostics: {_SUMMARY_NEGATIVE_PHRASE}")


def _selector_candidate_diagnostic(
    node: Any,
    viewport: tuple[int, int],
    *,
    requested_role: str,
    name_pattern: str,
) -> Optional[dict[str, Any]]:
    role = _node_role(node)
    name = _node_name(node)
    role_matches = requested_role == "*" or role == requested_role
    name_matches_requested = name_matches(name, name_pattern)
    if not role_matches and not name_matches_requested:
        return None

    bounds = _node_bounds(node)
    visible = bounds is not None and _visible_bounds(bounds, viewport)
    actionable = _scroll_target_actionable(node)
    reasons = []
    if not role_matches:
        reasons.append("role_mismatch")
    if not name_matches_requested:
        reasons.append("name_mismatch")
    if not visible:
        reasons.append("not_visible")
    if not actionable:
        reasons.append("not_actionable")

    return {
        "node_id": _node_id(node),
        "role": role,
        "requested_role": requested_role,
        "observed_role": role,
        "name": name,
        "visible": visible,
        "actionable": actionable,
        "reasons": reasons,
    }


def _selector_candidate_sort_key(
    candidate: Mapping[str, Any], name_pattern: str = ""
) -> tuple[int, int, int, str]:
    # AIYES-105 (FC-RANK-03): class_rank is the primary key — 0 for an
    # exact-name or near-name candidate (near subsumes exact, FC-NEARNAME-01),
    # 1 for an unrelated same-role candidate. The existing deterministic
    # tie-breaks follow, terminating in node_id for a total order.
    class_rank = 0 if near_name(str(candidate.get("name", "")), name_pattern) else 1
    reasons = candidate.get("reasons", ())
    if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)):
        reasons = ()
    reason_count = len(reasons)
    actionable_penalty = 0 if candidate.get("actionable") is True else 1
    return (
        class_rank,
        reason_count,
        actionable_penalty,
        str(candidate.get("node_id", "")),
    )


def _selector_hint(
    requested_role: str,
    name_pattern: str,
    candidates: list[dict[str, Any]],
) -> str:
    if requested_role != "View":
        return ""
    for candidate in candidates:
        if (
            candidate.get("observed_role") == "Button"
            and candidate.get("name") == name_pattern
            and candidate.get("actionable") is True
        ):
            return (
                f"Requested role View missed actionable Button named {name_pattern}; "
                'use role="*" or role="Button" for this selector.'
            )
    return ""


def _tree_snapshot_progress(
    before_fingerprint: Optional[str],
    after_fingerprint: Optional[str],
) -> str:
    if before_fingerprint is None or after_fingerprint is None:
        return "unknown"
    if before_fingerprint == after_fingerprint:
        return "unchanged"
    return "changed"


def _tree_snapshot_fingerprint(tree_snapshot: Any) -> Optional[str]:
    if tree_snapshot is None:
        return None
    nodes = _tree_snapshot_nodes(tree_snapshot)
    if not nodes:
        return None
    entries = []
    for node in nodes:
        name = _node_name(node)
        name_hash = (
            hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:12]
            if name
            else ""
        )
        entries.append(
            {
                "id": _node_id(node),
                "role": _node_role(node),
                "name_hash": name_hash,
                "bounds": list(_node_bounds(node) or ()),
                "states": sorted(_node_states(node)),
                "actions": sorted(_node_actions(node)),
                "scrollable": _node_scrollable(node),
            }
        )
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _record_no_progress(
    unchanged_by_scrollable: dict[str, int],
    selected_scrollable_id: Optional[str],
    progress: str,
) -> bool:
    if not selected_scrollable_id:
        return False
    if progress == "unchanged":
        unchanged_by_scrollable[selected_scrollable_id] = (
            unchanged_by_scrollable.get(selected_scrollable_id, 0) + 1
        )
        return (
            unchanged_by_scrollable[selected_scrollable_id]
            >= _NO_PROGRESS_ATTEMPT_LIMIT
        )
    if progress in {"changed", "target_appeared"}:
        unchanged_by_scrollable[selected_scrollable_id] = 0
    return False


def _final_scroll_progress(scroll_attempts: list[dict[str, Any]]) -> str:
    progress_values = [
        str(attempt.get("progress", "unknown")) for attempt in scroll_attempts
    ]
    if not progress_values:
        return "unknown"
    if "target_appeared" in progress_values:
        return "target_appeared"
    if "changed" in progress_values:
        return "changed"
    if all(progress == "unchanged" for progress in progress_values):
        return "unchanged"
    if all(progress == "unknown" for progress in progress_values):
        return "unknown"
    return "unknown"


def _no_scrollable_exposure_guidance(
    *,
    failure_class: str,
    progress: str,
    attempts: int,
    direction: str,
    viewport: tuple[int, int],
) -> dict[str, Any]:
    """Build the AIYES-106 app-side accessibility-exposure guidance block.

    Read-only over the already-computed failure evidence (FC-DIAG-07): the
    guidance carries only permitted scalar facts (failure_class, progress,
    attempts, direction, viewport, the null selected_scrollable_id, and the
    tree-fingerprint-equality flag) — never a raw tree, screenshot, or
    coordinate payload (FC-DIAG-06). Causality is hedged and the step stays a
    failure (FC-DIAG-08). Bounded to GUIDANCE_MAX_BYTES (FC-DIAG-06, OD-04).
    """
    return {
        "kind": "no_scrollable_accessibility_exposure",
        "summary": (
            "No accessible scrollable region appears to have been exposed: "
            "every scroll attempt fell back to a raw viewport swipe and the "
            "accessibility tree did not change. This likely points to an "
            "app-side accessibility-exposure gap to investigate."
        ),
        "recommended_action": (
            "Investigate the app/widget tree: expose a scrollable container "
            "(e.g. a Scrollable / SemanticsScrollable) with scroll semantics "
            "so assistive tooling can discover and drive it."
        ),
        "step_outcome": "failed",
        "failure_class": failure_class,
        "progress": progress,
        "attempts": attempts,
        "direction": direction,
        "viewport": list(viewport),
        "selected_scrollable_id": None,
    }


def _scroll_failure_class(
    bound_hit: str,
    progress: str,
    scroll_attempts: list[dict[str, Any]],
) -> str:
    if bound_hit == "no_progress":
        return "target_not_found_no_progress"
    if progress == "unknown":
        return "target_not_found_progress_unknown"
    if scroll_attempts and all(
        attempt.get("selected_scrollable_id") is None for attempt in scroll_attempts
    ):
        return "no_scrollable"
    if progress == "unchanged":
        return "target_not_found_no_progress"
    if progress == "changed":
        return "target_not_found_after_progress"
    return "target_not_found_after_progress"


def _first_node_id(value: Any) -> str:
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            for node in nodes:
                node_id = _node_id(node)
                if node_id:
                    return node_id
        return _node_id(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            node_id = _node_id(item)
            if node_id:
                return node_id
    return _node_id(value)


def _node_id(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("id", value.get("node_id"))
    else:
        raw = getattr(value, "id", getattr(value, "node_id", ""))
    return str(raw) if raw else ""


def _parse_viewport(
    session_repo: Any,
    session_id: str,
    _cache: Optional[dict[str, tuple[int, int]]] = None,
) -> tuple[int, int]:
    """Resolve viewport (width, height) from session.

    On Android sessions with a device_serial, queries the device via
    ``adb shell wm size`` and caches per session_id. On Linux, parses
    ``session.resolution``. Returns (1080, 1920) on any failure with a
    single stderr warning naming the device serial (Android failures
    only).
    """
    default = (1080, 1920)

    if _cache is not None and session_id in _cache:
        return _cache[session_id]

    if session_repo is None:
        return default

    try:
        session = session_repo.load(session_id)
    except Exception:
        return default
    if session is None:
        return default

    backend = getattr(session, "backend", "")
    device_serial = getattr(session, "device_serial", "")

    if backend == "android" and device_serial:
        from aiyes.adapters.android_device_metrics_adapter import (
            query_device_metrics,
        )

        metrics, reason = query_device_metrics(device_serial)
        if metrics is None:
            # Do not cache the fallback — a transient failure (timeout,
            # daemon not yet up) shouldn't poison every subsequent scroll
            # step in the same session.
            print(
                f"aiyes: warning: wm size query failed for device "
                f"{device_serial!r} ({reason}); falling back to "
                f"viewport {default}.",
                file=sys.stderr,
            )
            return default
        if _cache is not None:
            _cache[session_id] = metrics
        return metrics

    # Linux / default path
    resolution = getattr(session, "resolution", "") if session is not None else ""
    if not isinstance(resolution, str) or "x" not in resolution:
        return default
    parts = resolution.lower().split("x")
    if len(parts) != 2:
        return default
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return default


def _swipe_coords_for_direction(
    direction: str, viewport: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Compute swipe endpoints for a scroll in the given direction.

    "down" scroll = swipe from lower screen region upward.
    "up" scroll = swipe from upper region downward.
    Horizontal directions swipe across the center row.
    """
    width, height = viewport
    cx, cy = width // 2, height // 2
    dy = height // 3
    dx = width // 3
    if direction == "down":
        return (cx, cy + dy, cx, cy - dy)
    if direction == "up":
        return (cx, cy - dy, cx, cy + dy)
    if direction == "left":
        return (cx - dx, cy, cx + dx, cy)
    if direction == "right":
        return (cx + dx, cy, cx - dx, cy)
    # Validator catches bad direction; defensive default.
    return (cx, cy + dy, cx, cy - dy)


def _first_node_bounds(value: Any) -> Optional[tuple[int, int, int, int]]:
    """Return bounds [x, y, width, height] of the first node with bounds.

    Searches the same shapes as _first_node_id: a dict with "nodes",
    or a sequence of nodes, or a single node.
    """
    candidates: list[Any] = []
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            candidates.extend(nodes)
        else:
            candidates.append(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        candidates.extend(value)
    else:
        candidates.append(value)

    for node in candidates:
        bounds = _node_bounds(node)
        if bounds is not None:
            return bounds
    return None


def _node_bounds(value: Any) -> Optional[tuple[int, int, int, int]]:
    if isinstance(value, Mapping):
        raw = value.get("bounds")
    else:
        raw = getattr(value, "bounds", None)
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
    except (TypeError, ValueError):
        return None
