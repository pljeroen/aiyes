"""Inspect use case — get accessibility tree and screenshot."""

from __future__ import annotations

import base64
import dataclasses
import datetime
from typing import Dict, Optional, Tuple

from aiyes.domain.session import android_package_name
from aiyes.domain.tree import (
    AccessibilityTree,
    enrich_tree,
    filter_tree_by_window,
    limit_tree_depth,
    prune_tree,
)
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.clock import ClockPort
from aiyes.ports.screenshot import ScreenshotPort
from aiyes.ports.screenshot_store import ScreenshotStorePort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class InspectDiagnostic:
    """Structured diagnostic attached to inspect output."""

    code: str
    severity: str
    backend: str
    message: str
    likely_causes: Tuple[str, ...]
    evidence: Dict[str, str] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.likely_causes, list):
            object.__setattr__(self, "likely_causes", tuple(self.likely_causes))
        if not isinstance(self.evidence, dict):
            object.__setattr__(self, "evidence", dict(self.evidence))


@dataclasses.dataclass(frozen=True)
class InspectResult:
    """Result of an inspect operation."""

    tree: Optional[AccessibilityTree]
    screenshot: Optional[str]
    timestamp: str
    screenshot_base64: bool = False
    screenshot_data: Optional[str] = None
    diagnostics: Tuple[InspectDiagnostic, ...] = ()


class InspectUseCase:
    """Inspect the current state: tree + screenshot + timestamp."""

    def __init__(
        self,
        tree: AccessibilityTreePort,
        screenshot: ScreenshotPort,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
        screenshot_store: ScreenshotStorePort,
        clock: ClockPort,
    ) -> None:
        self._tree = tree
        self._screenshot = screenshot
        self._session_repo = session_repo
        self._tree_store = tree_store
        self._screenshot_store = screenshot_store
        self._clock = clock

    def execute(
        self,
        session_id: str,
        no_screenshot: bool = False,
        no_tree: bool = False,
        tree_depth: Optional[int] = None,
        no_prune: bool = False,
        screenshot_base64: bool = False,
        focus_window: Optional[str] = None,
    ) -> InspectResult:
        """Execute the inspect operation.

        Raises if both no_tree and no_screenshot are True.
        """
        if no_tree and no_screenshot:
            raise ValueError("Cannot use both --no-tree and --no-screenshot")

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # Get tree (port returns AccessibilityTree domain type)
        domain_tree: Optional[AccessibilityTree] = None
        if not no_tree:
            domain_tree = self._tree.get_tree(session)

            # Filter by window title if requested
            domain_tree = filter_tree_by_window(domain_tree, window_title=focus_window)

            # Apply pruning (default: prune=True, unless no_prune is set)
            domain_tree = prune_tree(domain_tree, prune=not no_prune)

            # Apply depth limiting
            domain_tree = limit_tree_depth(domain_tree, max_depth=tree_depth)

            # Enrich tree with context fields
            domain_tree = enrich_tree(domain_tree)

            # Save domain tree to store with registry if available
            registry = getattr(self._tree, "last_registry", None)
            self._tree_store.save_tree(session_id, domain_tree, registry)

        # Take screenshot
        screenshot_path = None
        screenshot_data = None
        if not no_screenshot:
            raw_path = self._screenshot.take(session)
            try:
                screenshot_path = self._screenshot_store.save_screenshot(
                    session_id, raw_path
                )

                # Encode to base64 if requested
                if screenshot_base64:
                    raw_bytes = self._screenshot_store.read_screenshot_bytes(session_id)
                    screenshot_data = base64.b64encode(raw_bytes).decode("ascii")
            finally:
                # Clean up temp file created by screenshot port
                if raw_path != screenshot_path:
                    try:
                        self._screenshot_store.delete_temp(raw_path)
                    except OSError:
                        pass

        diagnostics = self._build_diagnostics(
            session=session,
            tree=domain_tree,
            screenshot_path=screenshot_path,
        )

        # Timestamp via ClockPort (deterministic, testable)
        now_epoch = self._clock.now()

        timestamp = datetime.datetime.fromtimestamp(
            now_epoch, tz=datetime.timezone.utc
        ).isoformat()

        return InspectResult(
            tree=domain_tree,
            screenshot=screenshot_path,
            timestamp=timestamp,
            screenshot_base64=screenshot_base64,
            screenshot_data=screenshot_data,
            diagnostics=diagnostics,
        )

    def _build_diagnostics(
        self,
        session: object,
        tree: Optional[AccessibilityTree],
        screenshot_path: Optional[str],
    ) -> Tuple[InspectDiagnostic, ...]:
        if tree is None or tree.roots or screenshot_path is None:
            return ()
        backend = getattr(session, "backend", "linux")

        return (
            InspectDiagnostic(
                code="empty_accessibility_tree",
                severity="warning",
                backend=backend,
                message="Screenshot exists but no accessibility nodes were found.",
                likely_causes=_empty_tree_likely_causes(backend),
                evidence=_empty_tree_evidence(
                    backend=backend,
                    session=session,
                    tree_port=self._tree,
                    screenshot_path=screenshot_path,
                ),
            ),
        )


def _empty_tree_likely_causes(backend: str) -> Tuple[str, ...]:
    common = (
        "The UI may still be starting or rendering.",
        "The active surface may not expose accessibility semantics.",
        "The rendered content may be custom canvas, WebView, game, or GL content.",
    )
    if backend == "android":
        return common + (
            "Android views may need content-description, text, resource-id, or Compose semantics.",
            "UIAutomator may not expose nodes for the current foreground surface.",
        )
    return common + (
        "The application may not be connected to the desktop accessibility bus yet.",
        "The toolkit may require AT-SPI or AccessKit accessibility support to be enabled.",
    )


def _empty_tree_evidence(
    backend: str,
    session: object,
    tree_port: object,
    screenshot_path: Optional[str],
) -> Dict[str, str]:
    evidence = {
        "screenshot": "available" if screenshot_path is not None else "unavailable",
    }
    if backend != "android":
        return evidence

    package = android_package_name(session)
    if package:
        evidence["foreground_package"] = package
    dump_status = getattr(tree_port, "last_dump_status", None)
    if dump_status is not None:
        evidence["uiautomator_dump"] = str(dump_status)
    named_node_count = getattr(tree_port, "last_named_node_count", None)
    if named_node_count is not None:
        evidence["named_node_count"] = str(named_node_count)
    return evidence
