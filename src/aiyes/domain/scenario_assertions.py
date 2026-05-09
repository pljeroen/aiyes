"""Assertion evaluation for release scenario evidence."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclasses.dataclass(frozen=True)
class ScenarioAssertionResult:
    """Result of evaluating one scenario assertion."""

    assertion_id: str
    kind: str
    status: str
    message: str


def evaluate_scenario_assertion(
    assertion: Mapping[str, Any], context: Mapping[str, Any]
) -> ScenarioAssertionResult:
    """Evaluate a supported scenario assertion against collected context."""
    assertion_id = str(assertion.get("id", "assertion"))
    kind = str(assertion.get("kind", ""))
    if kind == "tree_non_empty":
        return _result(assertion_id, kind, _tree_non_empty(_source(assertion, context)))
    if kind == "node_exists":
        node_id = str(assertion.get("node_id", ""))
        return _result(assertion_id, kind, _node_exists(_source(assertion, context), node_id))
    if kind == "action_ok":
        payload = _source(assertion, context)
        ok = isinstance(payload, Mapping) and payload.get("status") in ("ok", "passed")
        return _result(assertion_id, kind, ok, "action result is not ok")
    if kind == "screenshot_exists":
        path = assertion.get("path")
        ok = isinstance(path, str) and Path(path).is_file()
        return _result(assertion_id, kind, ok, "screenshot file does not exist")
    if kind == "text_or_name_matches":
        pattern = str(assertion.get("pattern", "")).lower()
        ok = bool(pattern) and any(pattern in value.lower() for value in _tree_strings(context))
        return _result(assertion_id, kind, ok, "pattern not found in tree text/name")
    if kind == "tree_changed":
        payload = _source(assertion, context)
        ok = _diff_has_changes(payload)
        return _result(assertion_id, kind, ok, "tree diff has no changes")
    if kind == "prerequisite_skip":
        payload = _source(assertion, context)
        ok = isinstance(payload, Mapping) and payload.get("status") == "skipped"
        return _result(assertion_id, kind, ok, "prerequisite did not skip")
    return _result(assertion_id, kind, False, f"unsupported assertion kind: {kind}")


def _source(assertion: Mapping[str, Any], context: Mapping[str, Any]) -> Any:
    source = assertion.get("source")
    if isinstance(source, str):
        return context.get(source, {})
    return context


def _tree_non_empty(payload: Any) -> bool:
    tree = payload.get("tree") if isinstance(payload, Mapping) else payload
    if not isinstance(tree, Mapping):
        return False
    children = tree.get("children")
    return bool(tree.get("id") or (isinstance(children, list) and children))


def _node_exists(payload: Any, node_id: str) -> bool:
    if not node_id:
        return False
    tree = payload.get("tree") if isinstance(payload, Mapping) else payload
    return any(node.get("id") == node_id for node in _walk_nodes(tree))


def _walk_nodes(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        children = value.get("children")
        if isinstance(children, list):
            for child in children:
                yield from _walk_nodes(child)


def _tree_strings(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key in ("name", "text", "value"):
            found = value.get(key)
            if isinstance(found, str):
                yield found
        for child in value.values():
            yield from _tree_strings(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _tree_strings(child)


def _diff_has_changes(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    for key in ("added", "removed", "changed"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _result(
    assertion_id: str, kind: str, passed: bool, failure_message: str = "assertion failed"
) -> ScenarioAssertionResult:
    return ScenarioAssertionResult(
        assertion_id=assertion_id,
        kind=kind,
        status="passed" if passed else "failed",
        message="ok" if passed else failure_message,
    )
