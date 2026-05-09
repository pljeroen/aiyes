"""Debug bundle use case — collect diagnostic summaries without copying files."""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Mapping

from aiyes.domain.tree import flatten_nodes


_SENSITIVE_MARKERS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "CREDENTIAL",
    "PRIVATE",
    "AUTH",
)


class DebugBundleUseCase:
    """Collect diagnostic summaries for a session."""

    def __init__(
        self,
        session_repo: Any,
        doctor_uc: Any,
        operation_log: Any,
        tree_store: Any,
        screenshot_store: Any,
    ) -> None:
        self._session_repo = session_repo
        self._doctor_uc = doctor_uc
        self._operation_log = operation_log
        self._tree_store = tree_store
        self._screenshot_store = screenshot_store

    def execute(
        self,
        session_id: str,
        environ: Mapping[str, str],
    ) -> Dict[str, Any]:
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        return {
            "schema_version": 1,
            "session": _session_summary(session),
            "doctor": [dataclasses.asdict(result) for result in self._doctor_uc.execute()],
            "operations": _operation_summary(self._operation_log.read(session_id)),
            "tree": _tree_summary(self._tree_store.load_tree(session_id)),
            "screenshot": _screenshot_summary(self._screenshot_store, session_id),
            "environment": redact_environment(environ),
        }


def redact_environment(environ: Mapping[str, str]) -> Dict[str, str]:
    """Return environment values with sensitive entries redacted."""
    redacted: Dict[str, str] = {}
    for key, value in sorted(environ.items()):
        if _is_sensitive_key(key):
            redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


def _is_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in _SENSITIVE_MARKERS)


def _session_summary(session: Any) -> Dict[str, Any]:
    return {
        "session_id": session.session_id,
        "backend": getattr(session, "backend", "linux"),
        "display": getattr(session, "display", ""),
        "app": getattr(session, "app_command", ""),
        "app_args_count": len(getattr(session, "app_args", ())),
        "name": getattr(session, "name", None),
        "device_serial": getattr(session, "device_serial", None),
        "package_name": getattr(session, "package_name", ""),
        "started_at": getattr(session, "started_at", 0.0),
    }


def _operation_summary(records: Any) -> Dict[str, Any]:
    command_counts: Dict[str, int] = {}
    failures = 0
    for record in records:
        command_counts[record.command] = command_counts.get(record.command, 0) + 1
        if record.exit_code != 0:
            failures += 1
    return {
        "total": len(records),
        "failures": failures,
        "command_counts": command_counts,
    }


def _tree_summary(stored_tree: Any) -> Dict[str, Any]:
    if stored_tree is None:
        return {"available": False, "root_count": 0, "node_count": 0}
    tree = stored_tree.tree
    return {
        "available": True,
        "root_count": len(tree.roots),
        "node_count": len(flatten_nodes(tree.roots)),
    }


def _screenshot_summary(screenshot_store: Any, session_id: str) -> Dict[str, Any]:
    try:
        path = screenshot_store.get_screenshot_path(session_id)
        screenshot_store.read_screenshot_bytes(session_id)
    except Exception:
        return {"available": False, "copied": False}
    return {
        "available": True,
        "copied": False,
        "path_basename": str(path).rsplit("/", maxsplit=1)[-1],
    }

