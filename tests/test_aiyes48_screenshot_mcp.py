"""AIYES-48 screenshot output and MCP node_id dispatch regressions."""

from __future__ import annotations

import base64
import dataclasses
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

from aiyes.adapters.mcp_server import ServerDependencies, _build_dispatch_table
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import StoredTree
from aiyes.domain.use_cases.screenshot import ScreenshotResult, ScreenshotUseCase


def _make_session() -> Session:
    return Session(
        session_id="test-s",
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=(),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
    )


class SpySessionRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self, session_id: str) -> Optional[Session]:
        if session_id == self._session.session_id:
            return self._session
        return None


class SpyScreenshot:
    def __init__(self, raw_path: str = "/tmp/raw.png") -> None:
        self.raw_path = raw_path
        self.calls: List[Tuple[str, Any]] = []

    def take(self, session: Session, output_path: Optional[str] = None) -> str:
        self.calls.append(("take", (session.session_id, output_path)))
        return output_path or self.raw_path


class ByteTrackingScreenshotStore:
    def __init__(self, initial_bytes: bytes = b"full-image") -> None:
        self.calls: List[Tuple[str, Any]] = []
        self._paths: Dict[str, str] = {}
        self.bytes_by_path: Dict[str, bytes] = {}
        # Content-scoped dimension registry: maps a byte-blob -> (width, height).
        # read_dimensions resolves the CURRENT bytes at a path through this map,
        # so a crop that rewrites those bytes changes the reported dims
        # (proves the post-crop read for AIYES-111 R2). Unregistered bytes -> None.
        self.dims_by_bytes: Dict[bytes, Tuple[int, int]] = {}
        self.initial_bytes = initial_bytes

    def save_screenshot(self, session_id: str, source_path: str) -> str:
        store_path = f"/store/{session_id}/screenshot.png"
        self.calls.append(("save_screenshot", (session_id, source_path)))
        self._paths[session_id] = store_path
        self.bytes_by_path[store_path] = self.bytes_by_path.get(
            source_path, self.initial_bytes
        )
        return store_path

    def get_screenshot_path(self, session_id: str) -> str:
        self.calls.append(("get_screenshot_path", session_id))
        return self._paths[session_id]

    def read_screenshot_bytes(self, session_id: str) -> bytes:
        self.calls.append(("read_screenshot_bytes", session_id))
        return self.bytes_by_path[self._paths[session_id]]

    def read_dimensions(self, path: str) -> Optional[Tuple[int, int]]:
        """Map the CURRENT bytes at ``path`` to registered dims (content-scoped).

        Returns the dims registered for whatever bytes currently live at
        ``path`` (so a crop rewriting those bytes changes the answer); None
        for absent/unregistered bytes (the AIYES-111 degrade sentinel).
        """
        self.calls.append(("read_dimensions", path))
        data = self.bytes_by_path.get(path)
        return self.dims_by_bytes.get(data)

    def delete_temp(self, path: str) -> None:
        self.calls.append(("delete_temp", path))


class MutatingCrop:
    def __init__(
        self,
        store: ByteTrackingScreenshotStore,
        cropped_bytes: bytes = b"cropped-image",
    ) -> None:
        self.store = store
        self.cropped_bytes = cropped_bytes
        self.calls: List[Tuple[str, int, int, int, int, str]] = []

    def crop(
        self, source_path: str, x: int, y: int, w: int, h: int, dest_path: str
    ) -> str:
        self.calls.append((source_path, x, y, w, h, dest_path))
        self.store.bytes_by_path[dest_path] = self.cropped_bytes
        return dest_path


class StubTreeStore:
    def load_tree(self, session_id: str) -> StoredTree:
        node = Node(
            id="n_002",
            role="push_button",
            name="OK",
            bounds=(7, 8, 30, 40),
            states=(),
            actions=(),
        )
        return StoredTree(tree=AccessibilityTree(roots=(node,)), registry=None)


def _make_use_case(
    *,
    output_path: Optional[str] = None,
    tree_store: Optional[StubTreeStore] = None,
) -> Tuple[ScreenshotUseCase, SpyScreenshot, ByteTrackingScreenshotStore, MutatingCrop]:
    store = ByteTrackingScreenshotStore()
    if output_path is not None:
        store.bytes_by_path[output_path] = store.initial_bytes
    crop = MutatingCrop(store)
    screenshot = SpyScreenshot()
    uc = ScreenshotUseCase(
        screenshot=screenshot,
        session_repo=SpySessionRepo(_make_session()),
        screenshot_store=store,
        crop=crop,
        tree_store=tree_store,
    )
    return uc, screenshot, store, crop


class TestScreenshotOutputCropCorrectness:
    def test_region_crop_with_output_path_crops_output_path(self) -> None:
        output_path = "/tmp/out.png"
        uc, _, _, crop = _make_use_case(output_path=output_path)

        result = uc.execute(
            session_id="test-s",
            output_path=output_path,
            region=(1, 2, 30, 40),
        )

        assert result.path == output_path
        assert any(call[-1] == output_path for call in crop.calls)

    def test_node_crop_with_output_path_crops_output_path(self) -> None:
        output_path = "/tmp/node.png"
        uc, _, _, crop = _make_use_case(
            output_path=output_path,
            tree_store=StubTreeStore(),
        )

        result = uc.execute(
            session_id="test-s",
            output_path=output_path,
            node_id="n_002",
        )

        assert result.path == output_path
        assert any(call[1:5] == (7, 8, 30, 40) for call in crop.calls)
        assert any(call[-1] == output_path for call in crop.calls)

    def test_base64_region_crop_returns_cropped_store_bytes(self) -> None:
        uc, _, _, crop = _make_use_case()

        result = uc.execute(
            session_id="test-s",
            base64=True,
            region=(1, 2, 30, 40),
        )

        assert result.path is None
        assert result.data == base64.b64encode(crop.cropped_bytes).decode("ascii")


class TestMcpScreenshotNodeIdDispatch:
    def test_mcp_screenshot_forwards_node_id(self) -> None:
        fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
        deps = ServerDependencies(**fields)
        deps.screenshot_uc.execute.return_value = ScreenshotResult(path="/tmp/node.png")
        handler = _build_dispatch_table(deps)["screenshot"].use_case_call

        handler({"node_id": "n_002"}, deps, "test-s")

        deps.screenshot_uc.execute.assert_called_once()
        assert deps.screenshot_uc.execute.call_args.kwargs["node_id"] == "n_002"


class TestScreenshotNoCropRegression:
    def test_output_path_without_crop_remains_unchanged(self) -> None:
        output_path = "/tmp/plain.png"
        uc, screenshot, _, crop = _make_use_case(output_path=output_path)

        result = uc.execute(session_id="test-s", output_path=output_path)

        assert result.path == output_path
        assert screenshot.calls == [("take", ("test-s", output_path))]
        assert crop.calls == []
