"""Detect dialog use case — quick check for new windows since last inspect.

Compares current top-level windows against stored tree roots.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.top_level_window import TopLevelWindowPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class DetectDialogResult:
    """Result of dialog detection."""

    dialog_detected: bool
    window_name: Optional[str] = None
    window_role: Optional[str] = None
    error: Optional[str] = None


class DetectDialogUseCase:
    """Detect new top-level windows that were not in the last stored tree."""

    def __init__(
        self,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
        linux_window_port: TopLevelWindowPort,
        android_window_port: TopLevelWindowPort,
    ) -> None:
        self._session_repo = session_repo
        self._tree_store = tree_store
        self._linux_window_port = linux_window_port
        self._android_window_port = android_window_port

    def execute(self, session_id: str) -> DetectDialogResult:
        """Detect if a new dialog/window has appeared since last inspect."""
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # Load stored tree roots
        stored = self._tree_store.load_tree(session_id)
        if stored is None:
            return DetectDialogResult(dialog_detected=False)

        # Extract stored root names as set of (role, name) tuples
        stored_roots = set()
        for root in stored.tree.roots:
            stored_roots.add((root.role, root.name))

        # Get current top-level windows via platform-specific port
        backend = getattr(session, "backend", "linux")
        if backend == "android":
            window_port = self._android_window_port
        else:
            window_port = self._linux_window_port

        try:
            current_windows = window_port.list_top_level_windows(session)
        except Exception as exc:
            return DetectDialogResult(
                dialog_detected=False,
                error=f"Window enumeration failed: {exc}",
            )

        # Compare: find windows not in stored roots
        for window in current_windows:
            key = (window.role, window.name)
            if key not in stored_roots:
                return DetectDialogResult(
                    dialog_detected=True,
                    window_name=window.name,
                    window_role=window.role,
                )

        return DetectDialogResult(dialog_detected=False)
