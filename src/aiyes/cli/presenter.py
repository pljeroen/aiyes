"""Presenter — domain result to JSON string conversion.

Handles password masking (role=password_text -> value="***") and
two-tier error model (system errors: plain text; semantic failures: JSON).

This module does NOT import from adapters or click.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiyes.domain import evidence_profile
from aiyes.domain.operation_record import MetricsSummary, PruneResult
from aiyes.domain.output_formatter import node_to_dict, session_to_dict, tree_to_dict
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.use_cases.compound_do import CompoundDoResult
from aiyes.domain.use_cases.diff import DiffResult
from aiyes.domain.use_cases.find import FindResult, FoundNode
from aiyes.domain.use_cases.inspect import InspectDiagnostic
from aiyes.domain.reactive_wait import ReactiveWaitResult
from aiyes.domain.use_cases.session_capabilities import SessionCapabilitiesResult

if TYPE_CHECKING:
    from aiyes.domain.use_cases.scenario_run import ScenarioRunResult


def format_session_start(session: Session) -> str:
    """Convert session start result to JSON string."""
    return json.dumps(session_to_dict(session), indent=2)


def format_session_stop(
    status: str,
    session_id: str,
    errors: Optional[List[str]] = None,
) -> str:
    """Convert session stop result to JSON string."""
    result: Dict[str, Any] = {"status": status, "session_id": session_id}
    if errors:
        result["errors"] = errors
    return json.dumps(result, indent=2)


def format_session_resize(status: str, resolution: str) -> str:
    """Convert session resize result to JSON string."""
    return json.dumps({"status": status, "resolution": resolution}, indent=2)


def format_session_list(entries: List[Dict[str, Any]]) -> str:
    """Convert session list entries to JSON string."""
    return json.dumps(entries, indent=2)


def format_session_capabilities(result: SessionCapabilitiesResult) -> str:
    """Convert session capability disclosure to JSON string."""
    capabilities: Dict[str, Any] = {}
    for name, capability in result.capabilities.items():
        entry: Dict[str, Any] = {
            "status": capability.status,
            "reason": capability.reason,
        }
        if capability.operations:
            entry["operations"] = list(capability.operations)
        capabilities[name] = entry

    output: Dict[str, Any] = {
        "session_id": result.session_id,
        "backend": result.backend,
        "capabilities": capabilities,
    }
    if result.live_probe is not None:
        output["live_probe"] = {
            "backend": result.live_probe.backend,
            "checks": {
                name: dataclasses.asdict(check)
                for name, check in result.live_probe.checks.items()
            },
        }
    return json.dumps(output, indent=2)


def format_inspect(
    tree: Optional[Dict[str, Any]] = None,
    screenshot: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """Convert inspect result to JSON string."""
    result: Dict[str, Any] = {}
    if tree is not None:
        result["tree"] = tree
    if screenshot is not None:
        result["screenshot"] = screenshot
    if timestamp is not None:
        result["timestamp"] = timestamp
    return json.dumps(result, indent=2)


def format_inspect_result(
    tree: Optional[AccessibilityTree] = None,
    screenshot: Optional[str] = None,
    timestamp: Optional[str] = None,
    screenshot_base64: bool = False,
    screenshot_data: Optional[str] = None,
    diagnostics: tuple[InspectDiagnostic, ...] = (),
) -> str:
    """Convert inspect result with domain tree to JSON string.

    Converts the domain AccessibilityTree to dict, applies password masking,
    and serializes to JSON. When screenshot_base64 is True and screenshot_data
    is provided, includes the base64-encoded screenshot data in the output.
    """
    tree_output = None
    if tree is not None:
        tree_dict = tree_to_dict(tree)
        tree_dict = mask_node_dict(tree_dict)
        tree_output = tree_dict

    result: Dict[str, Any] = {}
    if tree_output is not None:
        result["tree"] = tree_output
    if screenshot is not None:
        result["screenshot"] = screenshot
    if screenshot_base64 and isinstance(screenshot_data, str):
        result["screenshot_base64"] = screenshot_data
    if diagnostics:
        result["diagnostics"] = [dataclasses.asdict(d) for d in diagnostics]
    if timestamp is not None:
        result["timestamp"] = timestamp
    return json.dumps(result, indent=2)


def format_find(nodes: List[Dict[str, Any]]) -> str:
    """Convert find results to JSON string."""
    return json.dumps(nodes, indent=2)


def format_find_nodes(result: "FindResult | List[FoundNode]") -> str:
    """Convert a find result to masked JSON string.

    Context fields (parent_role, parent_name, etc.) are included when non-None.

    AIYES-113 conditional envelope: when the result is a scoped FindResult
    (``scope_requested`` is True) the output is an envelope object
    ``{"nodes": [...], "scope_matched": bool, "matched_ancestors": [...]}``.
    On the unscoped path (a plain list, or a FindResult with
    ``scope_requested`` False) the output is a BARE JSON array, byte-for-byte
    identical to the pre-AIYES-113 output.
    """
    _CONTEXT_FIELDS = (
        "parent_role",
        "parent_name",
        "index_in_parent",
        "depth",
        "sibling_count",
    )
    node_dicts = []
    for n in result:
        d = dataclasses.asdict(n)
        # Remove context fields that are None for compactness
        for field in _CONTEXT_FIELDS:
            if field in d and d[field] is None:
                del d[field]
        # AIYES-116: OMIT resource_id when "" (separate truthy pop — the context
        # fields above are None-only Optionals; resource_id is a non-Optional
        # falsy-but-not-None field, so an empty value keeps output byte-identical).
        if not d.get("resource_id"):
            d.pop("resource_id", None)
        node_dicts.append(d)
    masked = [mask_node_dict(d) for d in node_dicts]

    scope_requested = getattr(result, "scope_requested", False)
    role_drift = getattr(result, "role_drift", ())

    # AIYES-114 three-way envelope: widen the trigger to `scope_requested OR
    # role_drift`. Unscoped-and-no-drift stays a byte-identical bare array; a
    # scope-only (no drift) call stays the byte-identical AIYES-113 two-key
    # envelope (role_drift key OMITTED, never []); a non-empty role_drift yields
    # an envelope carrying the diagnostic.
    if scope_requested or role_drift:
        envelope: Dict[str, Any] = {"nodes": masked}
        if scope_requested:
            envelope["scope_matched"] = result.scope_matched
            envelope["matched_ancestors"] = [
                {"id": a.id, "role": a.role, "name": a.name}
                for a in result.matched_ancestors
            ]
        if role_drift:
            envelope["role_drift"] = [
                {"id": c.id, "role": c.role, "name": c.name} for c in role_drift
            ]
        return json.dumps(envelope, indent=2)

    return json.dumps(masked, indent=2)


def format_screenshot(
    path: Optional[str] = None,
    data: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> str:
    """Convert screenshot result to JSON string.

    width/height are the returned image's actual pixel dimensions (post-crop
    for a region/node crop). Following the conditional-key-add pattern, the
    "width"/"height" keys are OMITTED entirely when None — never emitted as
    null or 0 — so existing {path, data} callers get byte-identical JSON.
    """
    result: Dict[str, Any] = {}
    if path is not None:
        result["path"] = path
    if data is not None:
        result["data"] = data
    if width is not None:
        result["width"] = width
    if height is not None:
        result["height"] = height
    return json.dumps(result, indent=2)


def format_action(
    status: str,
    action: str,
    target: str,
    reason: Optional[str] = None,
    available_actions: Optional[List[str]] = None,
    node_value: Optional[str] = None,
    node_states: Optional[List[str]] = None,
    action_method: Optional[str] = None,
) -> str:
    """Convert action result to JSON string."""
    result: Dict[str, Any] = {
        "status": status,
        "action": action,
        "target": target,
    }
    if reason is not None:
        result["reason"] = reason
    if available_actions is not None:
        result["available_actions"] = available_actions
    if node_value is not None:
        result["node_value"] = node_value
    if node_states is not None:
        result["node_states"] = node_states
    if action_method is not None:
        result["action_method"] = action_method
    return json.dumps(result, indent=2)


def format_do(do_result: CompoundDoResult) -> str:
    """Convert compound do result to masked JSON string.

    Applies password masking to found node and verify tree.
    """
    output: Dict[str, Any] = {}
    if do_result.found is not None:
        found_dict = node_to_dict(do_result.found)
        output["found"] = mask_node_dict(found_dict)
    if do_result.action_result is not None:
        output["action"] = dataclasses.asdict(do_result.action_result)
    if do_result.verify is not None:
        verify_dict = tree_to_dict(do_result.verify)
        output["verify"] = mask_node_dict(verify_dict)
    if do_result.error is not None:
        output["error"] = do_result.error
    return json.dumps(output, indent=2)


def format_doctor(results: List[Dict[str, Any]]) -> str:
    """Convert doctor results to JSON string."""
    return json.dumps(results, indent=2)


def format_debug_bundle(bundle: Dict[str, Any]) -> str:
    """Convert debug bundle to JSON string."""
    return json.dumps(bundle, indent=2)


_MCP_MANIFEST_KEYS = (
    "identity",
    "non_goals",
    "mcp",
    "trust_boundary",
    "common_loop",
    "capabilities",
    "backends",
    "inspectability_requirements",
)


def format_mcp_manifest(manifest: Dict[str, Any]) -> str:
    """Convert MCP-oriented capability disclosure to canonical-order JSON."""
    ordered = {k: manifest[k] for k in _MCP_MANIFEST_KEYS if k in manifest}
    for k in manifest:
        if k not in ordered:
            ordered[k] = manifest[k]
    return json.dumps(ordered, indent=2)


def format_status_ok() -> str:
    """Generic status=ok JSON for mouse/key/type operations."""
    return json.dumps({"status": "ok"})


def format_scenario_validation_errors(errors: tuple) -> str:
    """Convert scenario validation issues to machine-readable JSON."""
    return json.dumps(
        {
            "status": "error",
            "failure_code": "validation_error",
            "next_actions": [
                {
                    "code": "fix_scenario_file",
                    "message": "Fix scenario validation errors, then rerun.",
                    "command_hint": "",
                }
            ],
            "errors": [dataclasses.asdict(error) for error in errors],
        },
        indent=2,
    )


def format_scenario_preflight(result: Any) -> str:
    """Convert ScenarioPreflightResult to machine-readable JSON."""
    return json.dumps(dataclasses.asdict(result), indent=2)


def format_scenario_fixtures(fixtures: tuple) -> str:
    """Convert public scenario fixture metadata to JSON."""
    return json.dumps(
        {"fixtures": [dataclasses.asdict(fixture) for fixture in fixtures]},
        indent=2,
    )


def format_scenario_run(
    result: "ScenarioRunResult",
    profile: str = "compact",
    diagnostic_log: Any = None,
) -> str:
    """Convert ScenarioRunResult to machine-readable JSON under a profile.

    The default ``compact`` profile excludes raw accessibility-tree payloads
    while preserving classification fields; ``deep`` retains the full pre-change
    detail. Top-level result fields are profile-independent (FC-SERIAL-03).

    A10-CRIT-001/004: the presenter SHAPES only — it does not emit LE-02. The
    single ``evidence.profile.selected`` emission lives at the adapter/command
    boundary (CLI scenario-run command, MCP handler) so a bundle-writing run
    cannot double-emit. The ``diagnostic_log`` parameter is accepted for a
    stable signature but intentionally unused here.
    """
    del diagnostic_log  # emission belongs to the boundary, not the presenter
    selected = evidence_profile.normalize_profile(profile)
    payload = dataclasses.asdict(result)
    raw_steps = [dataclasses.asdict(step) for step in result.steps]
    payload["steps"] = evidence_profile.shape_step_records(
        raw_steps, selected, result.failure_code
    )
    return json.dumps(payload, indent=2)


def format_wait(
    found: bool,
    timeout: bool = False,
    node_id: Optional[str] = None,
    transient: bool = False,
    role_drift: Any = None,
) -> str:
    """Convert wait result to JSON string.

    Includes transient=true in output only when the flag is set. AIYES-114: the
    role_drift diagnostic (same {id, role, name} shape as a find envelope) is
    emitted ONLY when non-empty — the key is OMITTED entirely otherwise (never
    null, never []), so existing wait output stays byte-identical.
    """
    result: Dict[str, Any] = {"found": found}
    if node_id is not None:
        result["id"] = node_id
    result["timeout"] = timeout
    if transient:
        result["transient"] = True
    if role_drift:
        result["role_drift"] = [
            {"id": c.id, "role": c.role, "name": c.name} for c in role_drift
        ]
    return json.dumps(result, indent=2)


def format_wait_stable(
    stable: bool,
    timeout: bool,
    polls: int,
    changes: tuple = (),
    comparison_mode: str | None = None,
) -> str:
    """Convert wait-stable result to JSON string.

    Always includes the timeout key for consistency with format_wait.
    Includes 'changes' field only when stable=False and changes is non-empty.
    """
    result: Dict[str, Any] = {"stable": stable}
    result["timeout"] = timeout
    result["polls"] = polls
    if isinstance(comparison_mode, str):
        result["comparison_mode"] = comparison_mode
    if not stable and changes:
        result["changes"] = list(changes)
    return json.dumps(result, indent=2)


def format_reactive_wait(result: ReactiveWaitResult) -> str:
    """Convert ReactiveWaitResult to the shared CLI/MCP JSON shape."""
    payload = dataclasses.asdict(result)
    payload["events"] = [dataclasses.asdict(event) for event in result.events]
    payload["next_actions"] = list(result.next_actions)
    return json.dumps(payload, indent=2)


def format_system_error(message: str) -> str:
    """Format a system error as plain text (NOT JSON).

    System errors go to stderr; they must not be valid JSON.
    """
    return f"Error: {message}"


def format_diff(diff_result: "DiffResult") -> str:
    """Convert DiffResult to JSON string.

    Added/removed nodes are serialized via node_to_dict with password masking.
    Changed entries are serialized as {id, field, before, after} dicts.
    """

    added = [mask_node_dict(node_to_dict(n)) for n in diff_result.diff.added]
    removed = [mask_node_dict(node_to_dict(n)) for n in diff_result.diff.removed]
    roles = getattr(diff_result, "node_roles", {})
    changed = []
    for c in diff_result.diff.changed:
        entry = dataclasses.asdict(c)
        if entry["field"] == "value" and roles.get(c.id) == "password_text":
            entry["before"] = "***"
            entry["after"] = "***"
        changed.append(entry)

    return json.dumps(
        {"added": added, "removed": removed, "changed": changed},
        indent=2,
    )


def mask_node_dict(node_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Mask password_text nodes: set value to '***'.

    Applies recursively to children.
    """
    result = dict(node_dict)

    if result.get("role") == "password_text" and "value" in result:
        result["value"] = "***"

    if "children" in result:
        result["children"] = [mask_node_dict(c) for c in result["children"]]

    # Also handle tree-level masking (for tree_to_dict output with "tree" key)
    if "tree" in result and isinstance(result["tree"], list):
        result["tree"] = [mask_node_dict(n) for n in result["tree"]]

    return result


def format_metrics(summary: MetricsSummary) -> str:
    """Convert MetricsSummary to JSON string.

    Converts tuple-of-tuples fields to dict for JSON serialization.
    """
    output: Dict[str, Any] = {
        "session_id": summary.session_id,
        "total_commands": summary.total_commands,
        "command_counts": dict(summary.command_counts),
        "latency_p50": dict(summary.latency_p50),
        "latency_p95": dict(summary.latency_p95),
        "failure_rate": dict(summary.failure_rate),
        "session_duration_s": summary.session_duration_s,
        "period_start": summary.period_start,
        "period_end": summary.period_end,
    }
    return json.dumps(output, indent=2)


def format_prune(result: PruneResult) -> str:
    """Convert PruneResult to JSON string."""
    output: Dict[str, Any] = {
        "pruned_count": result.pruned_count,
        "skipped_active": result.skipped_active,
        "dry_run": result.dry_run,
        "sessions_pruned": list(result.sessions_pruned),
    }
    return json.dumps(output, indent=2)


def format_session_status(
    app_alive: bool,
    app_foreground: bool,
    display_alive: bool,
) -> str:
    """Convert session status result to JSON string."""
    result: Dict[str, Any] = {
        "app_alive": app_alive,
        "app_foreground": app_foreground,
        "display_alive": display_alive,
    }
    return json.dumps(result, indent=2)


def format_detect_dialog(
    dialog_detected: bool,
    window_name: Optional[str] = None,
    window_role: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Convert detect-dialog result to JSON string."""
    result: Dict[str, Any] = {"dialog_detected": dialog_detected}
    if window_name is not None:
        result["window_name"] = window_name
    if window_role is not None:
        result["window_role"] = window_role
    if error is not None:
        result["error"] = error
    return json.dumps(result, indent=2)


def format_clipboard_read(text: str) -> str:
    """Convert clipboard read result to JSON string."""
    return json.dumps({"text": text}, indent=2)


def format_clipboard_write() -> str:
    """Convert clipboard write result to JSON string."""
    return json.dumps({"status": "ok"}, indent=2)


def format_gesture_result() -> str:
    """Convert gesture result to JSON string."""
    return json.dumps({"status": "ok"}, indent=2)


def format_navigate_result(
    status: str = "ok",
    warning: Optional[str] = None,
) -> str:
    """Convert navigate result to JSON string."""
    result: Dict[str, Any] = {"status": status}
    if warning is not None:
        result["warning"] = warning
    return json.dumps(result, indent=2)


def format_menu_result(
    status: str = "ok",
    node_id: Optional[str] = None,
    node_name: Optional[str] = None,
) -> str:
    """Convert menu traversal result to JSON string."""
    result: Dict[str, Any] = {"status": status}
    if node_id is not None:
        result["node_id"] = node_id
    if node_name is not None:
        result["node_name"] = node_name
    return json.dumps(result, indent=2)


def format_goto_result(
    status: str,
    session_id: str,
    action: str = "goto",
    url: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """Convert goto result to JSON string (optional fields omitted when None)."""
    result: Dict[str, Any] = {
        "status": status,
        "session_id": session_id,
        "action": action,
    }
    if url is not None:
        result["url"] = url
    if reason is not None:
        result["reason"] = reason
    return json.dumps(result, indent=2)


def format_reload_result(
    status: str,
    session_id: str,
    action: str = "reload",
    reason: Optional[str] = None,
) -> str:
    """Convert reload result to JSON string (optional reason omitted when None)."""
    result: Dict[str, Any] = {
        "status": status,
        "session_id": session_id,
        "action": action,
    }
    if reason is not None:
        result["reason"] = reason
    return json.dumps(result, indent=2)
