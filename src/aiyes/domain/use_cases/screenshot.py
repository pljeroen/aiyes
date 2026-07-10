"""Screenshot use case — take a screenshot of the session display."""

from __future__ import annotations

import base64 as base64_mod
import dataclasses
from typing import Optional, Tuple

from aiyes.domain.tree import flatten_nodes
from aiyes.ports.crop import CropPort
from aiyes.ports.screenshot import ScreenshotPort
from aiyes.ports.screenshot_store import ScreenshotStorePort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class ScreenshotResult:
    """Result of a screenshot operation."""

    path: Optional[str]
    data: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


def _validate_region(region: Tuple[int, int, int, int]) -> None:
    """Validate region values: x >= 0, y >= 0, w > 0, h > 0."""
    x, y, w, h = region
    if x < 0:
        raise ValueError(f"region x must be >= 0, got {x}")
    if y < 0:
        raise ValueError(f"region y must be >= 0, got {y}")
    if w <= 0:
        raise ValueError(f"region width must be > 0, got {w}")
    if h <= 0:
        raise ValueError(f"region height must be > 0, got {h}")


class ScreenshotUseCase:
    """Take a screenshot of the session's virtual display."""

    def __init__(
        self,
        screenshot: ScreenshotPort,
        session_repo: SessionRepositoryPort,
        screenshot_store: ScreenshotStorePort,
        crop: Optional[CropPort] = None,
        tree_store: Optional[TreeStorePort] = None,
    ) -> None:
        self._screenshot = screenshot
        self._session_repo = session_repo
        self._screenshot_store = screenshot_store
        self._crop = crop
        self._tree_store = tree_store

    def execute(
        self,
        session_id: str,
        output_path: Optional[str] = None,
        base64: bool = False,
        region: Optional[Tuple[int, int, int, int]] = None,
        node_id: Optional[str] = None,
    ) -> ScreenshotResult:
        """Take a screenshot.

        If output_path is provided, saves to that location.
        If base64 is True, returns base64-encoded data instead of path.
        If region is (x, y, w, h), crops to that rectangle after capture.
        If node_id is given, looks up node bounds from stored tree and crops.
        region and node_id are mutually exclusive.
        """
        if region is not None and node_id is not None:
            raise ValueError(
                "--region and --node are mutually exclusive; specify at most one"
            )

        # Resolve node_id to region if needed
        if node_id is not None:
            region = self._resolve_node_bounds(session_id, node_id)

        # Validate region values if specified
        if region is not None:
            _validate_region(region)

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # Take the screenshot
        raw_path = self._screenshot.take(session, output_path)

        try:
            # Save to screenshot store (always, for session state)
            self._screenshot_store.save_screenshot(session_id, raw_path)

            # Crop if region specified
            if region is not None and self._crop is not None:
                store_path = self._screenshot_store.get_screenshot_path(session_id)
                x, y, w, h = region
                self._crop.crop(store_path, x, y, w, h, store_path)
                if output_path is not None and output_path != store_path:
                    self._crop.crop(output_path, x, y, w, h, output_path)

            if base64:
                # Read the file bytes and encode to base64. Dimensions are
                # bound to the EXACT file that was encoded (C1a): there is no
                # path to cross-check against, so read them from the same file
                # read_screenshot_bytes reads.
                encoded_path = self._screenshot_store.get_screenshot_path(session_id)
                raw_bytes = self._screenshot_store.read_screenshot_bytes(session_id)
                encoded = base64_mod.b64encode(raw_bytes).decode("ascii")
                width, height = self._read_dimensions(encoded_path)
                return ScreenshotResult(
                    path=None,
                    data=encoded,
                    width=width,
                    height=height,
                )

            # If caller requested a specific output path, return that path
            if output_path is not None:
                width, height = self._read_dimensions(output_path)
                return ScreenshotResult(path=output_path, width=width, height=height)

            # Otherwise return the store path
            store_path = self._screenshot_store.get_screenshot_path(session_id)
            width, height = self._read_dimensions(store_path)
            return ScreenshotResult(path=store_path, width=width, height=height)
        finally:
            # Clean up temp file created by screenshot port
            if output_path is None and raw_path is not None:
                try:
                    self._screenshot_store.delete_temp(raw_path)
                except OSError:
                    pass

    def _read_dimensions(self, path: str) -> Tuple[Optional[int], Optional[int]]:
        """Read (width, height) of the returned file via the store port.

        The store returns None when dimensions are unavailable (unrecognized
        format, corrupt/truncated header, missing file); we map that to
        (None, None) so the result carries no fabricated dims. The domain does
        NO raw file I/O — the byte read lives behind ScreenshotStorePort.
        """
        dims = self._screenshot_store.read_dimensions(path)
        if dims is None:
            return (None, None)
        return (dims[0], dims[1])

    def _resolve_node_bounds(
        self, session_id: str, node_id: str
    ) -> Tuple[int, int, int, int]:
        """Look up node bounds from stored accessibility tree.

        Node bounds are always in (x, y, width, height) format — adapters
        normalize to this format on ingestion.
        """
        if self._tree_store is None:
            raise RuntimeError(
                f"No stored tree for session {session_id!r}: tree store not configured"
            )

        stored = self._tree_store.load_tree(session_id)
        if stored is None:
            raise RuntimeError(
                f"No stored tree for session {session_id!r}; run 'aieyes inspect' first"
            )

        tree = stored.tree
        all_nodes = flatten_nodes(tree.roots)
        for node in all_nodes:
            if node.id == node_id:
                bounds = node.bounds
                if len(bounds) != 4:
                    raise RuntimeError(f"Node {node_id!r} has invalid bounds: {bounds}")
                return (bounds[0], bounds[1], bounds[2], bounds[3])

        raise RuntimeError(
            f"Node not found: {node_id!r} in stored tree for session {session_id!r}"
        )
