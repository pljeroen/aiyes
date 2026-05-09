"""AIYES-25 Group D — Screenshot Enhancement: tests.

Tests for GAP-14 (region/node screenshot cropping).

Traceability:
  REQ-D01: --region crops image via CropPort
  REQ-D02: --node looks up bounds from stored tree, then crops
  REQ-D03: --node with unknown node_id raises RuntimeError
  REQ-D04: --region and --node are mutually exclusive (ValueError)
  REQ-D05: Neither --region nor --node preserves backward compat
  REQ-D06: CropPort Protocol structural typing check
  REQ-D07: ImageMagickCropAdapter calls convert subprocess correctly
  REQ-D08: CLI --region flag parsed as 4 comma-separated ints
  REQ-D09: CLI --node flag passed through to use case
  REQ-D10: doctor checks for convert (ImageMagick)
"""

from __future__ import annotations

import subprocess
from typing import Any, List, Tuple
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from aiyes.domain.session import Session
from aiyes.domain.use_cases.screenshot import ScreenshotUseCase, ScreenshotResult
from aiyes.ports.crop import CropPort

from tests.conftest import (
    FakeScreenshot,
    FakeScreenshotStore,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
    make_domain_tree,
)


# ═══════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_session(**overrides: Any) -> Session:
    defaults = dict(
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
    defaults.update(overrides)
    return Session(**defaults)


class FakeCrop:
    """Fake for CropPort."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Any]] = []

    def crop(
        self, source_path: str, x: int, y: int, w: int, h: int, dest_path: str
    ) -> str:
        self.calls.append(("crop", (source_path, x, y, w, h, dest_path)))
        return dest_path


# ═══════════════════════════════════════════════════════════════════════
# REQ-D01: --region crops image via CropPort
# ═══════════════════════════════════════════════════════════════════════


class TestScreenshotRegionCropsImage:
    """When region is specified, use case takes screenshot then crops."""

    def test_screenshot_region_crops_image(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        screenshot_port = FakeScreenshot(path="/tmp/raw.png")
        ss_store = FakeScreenshotStore()
        tree_store = FakeTreeStore()
        crop_port = FakeCrop()

        uc = ScreenshotUseCase(
            screenshot=screenshot_port,
            session_repo=repo,
            screenshot_store=ss_store,
            crop=crop_port,
            tree_store=tree_store,
        )

        result = uc.execute(
            session_id="test-s",
            region=(10, 20, 300, 400),
        )

        # Crop port was called with the correct region
        assert len(crop_port.calls) == 1
        assert crop_port.calls[0][0] == "crop"
        _, (src, x, y, w, h, dest) = crop_port.calls[0]
        assert (x, y, w, h) == (10, 20, 300, 400)


# ═══════════════════════════════════════════════════════════════════════
# REQ-D02: --node looks up bounds from stored tree, then crops
# ═══════════════════════════════════════════════════════════════════════


class TestScreenshotNodeLooksUpBounds:
    """When node_id is given, use case loads stored tree, finds node, crops."""

    def test_screenshot_node_looks_up_bounds(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        screenshot_port = FakeScreenshot(path="/tmp/raw.png")
        ss_store = FakeScreenshotStore()
        tree_store = FakeTreeStore()
        crop_port = FakeCrop()

        # Store a tree with a known node
        tree = make_domain_tree(
            [
                make_node(
                    "n_001",
                    "frame",
                    "Test Window",
                    bounds=[50, 60, 200, 100],
                    children=[
                        make_node(
                            "n_002", "push_button", "OK", bounds=[70, 80, 40, 20]
                        ),
                    ],
                ),
            ]
        )
        tree_store.save_tree("test-s", tree, None)

        uc = ScreenshotUseCase(
            screenshot=screenshot_port,
            session_repo=repo,
            screenshot_store=ss_store,
            crop=crop_port,
            tree_store=tree_store,
        )

        result = uc.execute(
            session_id="test-s",
            node_id="n_002",
        )

        # Crop port was called with the node's bounds
        assert len(crop_port.calls) == 1
        _, (src, x, y, w, h, dest) = crop_port.calls[0]
        assert (x, y, w, h) == (70, 80, 40, 20)


# ═══════════════════════════════════════════════════════════════════════
# REQ-D03: --node with unknown node_id raises RuntimeError
# ═══════════════════════════════════════════════════════════════════════


class TestScreenshotNodeNotFoundRaises:
    """When node_id is not in the stored tree, raises RuntimeError."""

    def test_screenshot_node_not_found_raises(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        screenshot_port = FakeScreenshot(path="/tmp/raw.png")
        ss_store = FakeScreenshotStore()
        tree_store = FakeTreeStore()
        crop_port = FakeCrop()

        # Store a tree without the requested node
        tree = make_domain_tree(
            [
                make_node("n_001", "frame", "Test Window"),
            ]
        )
        tree_store.save_tree("test-s", tree, None)

        uc = ScreenshotUseCase(
            screenshot=screenshot_port,
            session_repo=repo,
            screenshot_store=ss_store,
            crop=crop_port,
            tree_store=tree_store,
        )

        with pytest.raises(RuntimeError, match="Node not found.*n_999"):
            uc.execute(session_id="test-s", node_id="n_999")

    def test_screenshot_node_no_stored_tree_raises(self) -> None:
        """When no tree is stored for the session, raises RuntimeError."""
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        screenshot_port = FakeScreenshot(path="/tmp/raw.png")
        ss_store = FakeScreenshotStore()
        tree_store = FakeTreeStore()
        crop_port = FakeCrop()

        uc = ScreenshotUseCase(
            screenshot=screenshot_port,
            session_repo=repo,
            screenshot_store=ss_store,
            crop=crop_port,
            tree_store=tree_store,
        )

        with pytest.raises(RuntimeError, match="No stored tree"):
            uc.execute(session_id="test-s", node_id="n_001")


# ═══════════════════════════════════════════════════════════════════════
# REQ-D04: --region and --node mutually exclusive
# ═══════════════════════════════════════════════════════════════════════


class TestScreenshotRegionAndNodeMutuallyExclusive:
    """Passing both region and node_id raises ValueError."""

    def test_screenshot_region_and_node_mutually_exclusive(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        screenshot_port = FakeScreenshot()
        ss_store = FakeScreenshotStore()
        tree_store = FakeTreeStore()
        crop_port = FakeCrop()

        uc = ScreenshotUseCase(
            screenshot=screenshot_port,
            session_repo=repo,
            screenshot_store=ss_store,
            crop=crop_port,
            tree_store=tree_store,
        )

        with pytest.raises(ValueError, match="mutually exclusive"):
            uc.execute(
                session_id="test-s",
                region=(10, 20, 100, 100),
                node_id="n_001",
            )


# ═══════════════════════════════════════════════════════════════════════
# REQ-D05: No region, no node — backward compat
# ═══════════════════════════════════════════════════════════════════════


class TestScreenshotNoRegionNoNodeUnchanged:
    """Without region or node_id, behavior is unchanged from before."""

    def test_screenshot_no_region_no_node_unchanged(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        screenshot_port = FakeScreenshot(path="/tmp/raw.png")
        ss_store = FakeScreenshotStore()
        tree_store = FakeTreeStore()
        crop_port = FakeCrop()

        uc = ScreenshotUseCase(
            screenshot=screenshot_port,
            session_repo=repo,
            screenshot_store=ss_store,
            crop=crop_port,
            tree_store=tree_store,
        )

        result = uc.execute(session_id="test-s")

        # No crop calls
        assert len(crop_port.calls) == 0
        # Result has a path
        assert result.path is not None


# ═══════════════════════════════════════════════════════════════════════
# REQ-D06: CropPort Protocol structural typing check
# ═══════════════════════════════════════════════════════════════════════


class TestCropPortProtocol:
    """CropPort is a Protocol and FakeCrop satisfies it."""

    def test_crop_port_protocol(self) -> None:
        """CropPort is a Protocol; FakeCrop structurally matches it."""

        # CropPort should be runtime_checkable
        assert isinstance(FakeCrop(), CropPort)

    def test_crop_port_has_crop_method(self) -> None:
        """CropPort declares a crop() method with correct signature."""
        import inspect

        sig = inspect.signature(CropPort.crop)
        params = list(sig.parameters.keys())
        assert "source_path" in params
        assert "x" in params
        assert "y" in params
        assert "w" in params
        assert "h" in params
        assert "dest_path" in params


# ═══════════════════════════════════════════════════════════════════════
# REQ-D07: ImageMagickCropAdapter calls convert subprocess
# ═══════════════════════════════════════════════════════════════════════


class TestImageMagickCropAdapter:
    """ImageMagickCropAdapter shells out to magick/convert with correct geometry."""

    def test_imagemagick_crop_adapter_uses_magick(self) -> None:
        """When 'magick' is available, adapter uses it."""
        from aiyes.adapters.imagemagick_crop_adapter import ImageMagickCropAdapter

        adapter = ImageMagickCropAdapter()

        with (
            patch("subprocess.run") as mock_run,
            patch(
                "aiyes.adapters.imagemagick_crop_adapter.shutil.which",
                side_effect=lambda cmd: "/usr/bin/magick" if cmd == "magick" else None,
            ),
        ):
            result = adapter.crop(
                source_path="/tmp/input.png",
                x=10,
                y=20,
                w=300,
                h=400,
                dest_path="/tmp/output.png",
            )

        assert result == "/tmp/output.png"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/magick"
        assert "/tmp/input.png" in cmd
        assert "300x400+10+20" in cmd
        assert "/tmp/output.png" in cmd

    def test_imagemagick_crop_adapter_falls_back_to_convert(self) -> None:
        """When 'magick' is not available, adapter falls back to 'convert'."""
        from aiyes.adapters.imagemagick_crop_adapter import ImageMagickCropAdapter

        adapter = ImageMagickCropAdapter()

        with (
            patch("subprocess.run") as mock_run,
            patch(
                "aiyes.adapters.imagemagick_crop_adapter.shutil.which",
                side_effect=lambda cmd: (
                    "/usr/bin/convert" if cmd == "convert" else None
                ),
            ),
        ):
            result = adapter.crop(
                source_path="/tmp/input.png",
                x=10,
                y=20,
                w=300,
                h=400,
                dest_path="/tmp/output.png",
            )

        assert result == "/tmp/output.png"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/convert"
        assert "300x400+10+20" in cmd

    def test_imagemagick_crop_adapter_raises_if_neither_found(self) -> None:
        """When neither 'magick' nor 'convert' is available, raises RuntimeError."""
        from aiyes.adapters.imagemagick_crop_adapter import ImageMagickCropAdapter

        adapter = ImageMagickCropAdapter()

        with (
            patch(
                "aiyes.adapters.imagemagick_crop_adapter.shutil.which",
                return_value=None,
            ),
            pytest.raises(RuntimeError, match="ImageMagick not found"),
        ):
            adapter.crop("/tmp/input.png", 0, 0, 100, 100, "/tmp/output.png")

    def test_imagemagick_crop_adapter_satisfies_protocol(self) -> None:
        from aiyes.adapters.imagemagick_crop_adapter import ImageMagickCropAdapter

        assert isinstance(ImageMagickCropAdapter(), CropPort)

    def test_inplace_crop_uses_temp_file(self, tmp_path) -> None:
        """When source==dest, adapter crops to temp file then replaces."""
        from aiyes.adapters.imagemagick_crop_adapter import ImageMagickCropAdapter

        adapter = ImageMagickCropAdapter()
        src = str(tmp_path / "screenshot.png")
        # Create a dummy file
        with open(src, "wb") as f:
            f.write(b"original-data")

        with (
            patch("subprocess.run") as mock_run,
            patch(
                "aiyes.adapters.imagemagick_crop_adapter.shutil.which",
                side_effect=lambda cmd: "/usr/bin/magick" if cmd == "magick" else None,
            ),
        ):
            result = adapter.crop(src, 10, 20, 100, 100, src)

        assert result == src
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        # Source is the original file
        assert cmd[1] == src
        # Destination in subprocess call should be a temp file (not src)
        subprocess_dest = cmd[-1]
        assert subprocess_dest != src
        assert subprocess_dest.endswith(".png")
        assert str(tmp_path) in subprocess_dest

    def test_inplace_crop_preserves_original_on_failure(self, tmp_path) -> None:
        """When crop fails with source==dest, original file is preserved."""
        from aiyes.adapters.imagemagick_crop_adapter import ImageMagickCropAdapter

        adapter = ImageMagickCropAdapter()
        src = str(tmp_path / "screenshot.png")
        original_data = b"precious-original-data"
        with open(src, "wb") as f:
            f.write(original_data)

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "magick"),
            ),
            patch(
                "aiyes.adapters.imagemagick_crop_adapter.shutil.which",
                side_effect=lambda cmd: "/usr/bin/magick" if cmd == "magick" else None,
            ),
            pytest.raises(subprocess.CalledProcessError),
        ):
            adapter.crop(src, 10, 20, 100, 100, src)

        # Original file must still have its data
        with open(src, "rb") as f:
            assert f.read() == original_data


# ═══════════════════════════════════════════════════════════════════════
# REQ-D08: CLI --region flag
# ═══════════════════════════════════════════════════════════════════════


class TestCliScreenshotRegionFlag:
    """CLI parses --region X,Y,W,H and passes tuple to use case."""

    def test_cli_screenshot_region_flag(self) -> None:
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.screenshot_uc") as mock_uc,
            patch(
                "aiyes.cli.main.resolve_session_id",
                return_value="test-s",
            ),
        ):
            mock_uc.execute.return_value = ScreenshotResult(path="/tmp/screenshot.png")

            from aiyes.cli.main import cli

            result = runner.invoke(
                cli,
                ["screenshot", "--session", "test-s", "--region", "10,20,300,400"],
            )

        assert result.exit_code == 0
        mock_uc.execute.assert_called_once()
        kwargs = mock_uc.execute.call_args[1]
        assert kwargs["region"] == (10, 20, 300, 400)

    def test_cli_screenshot_region_flag_bad_format(self) -> None:
        runner = CliRunner()

        from aiyes.cli.main import cli

        result = runner.invoke(
            cli,
            ["screenshot", "--session", "test-s", "--region", "bad"],
        )
        assert result.exit_code != 0


# ═══════════════════════════════════════════════════════════════════════
# REQ-D09: CLI --node flag
# ═══════════════════════════════════════════════════════════════════════


class TestCliScreenshotNodeFlag:
    """CLI passes --node NODE_ID to use case."""

    def test_cli_screenshot_node_flag(self) -> None:
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.screenshot_uc") as mock_uc,
            patch(
                "aiyes.cli.main.resolve_session_id",
                return_value="test-s",
            ),
        ):
            mock_uc.execute.return_value = ScreenshotResult(path="/tmp/screenshot.png")

            from aiyes.cli.main import cli

            result = runner.invoke(
                cli,
                ["screenshot", "--session", "test-s", "--node", "n_002"],
            )

        assert result.exit_code == 0
        mock_uc.execute.assert_called_once()
        kwargs = mock_uc.execute.call_args[1]
        assert kwargs["node_id"] == "n_002"


# ═══════════════════════════════════════════════════════════════════════
# REQ-D10: doctor checks for convert (ImageMagick)
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorChecksImageMagick:
    """SystemDependencyCheck includes imagemagick in its dependency list."""

    def test_doctor_checks_imagemagick(self) -> None:
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        result = checker.check("imagemagick")
        # Should return a valid DependencyResult (not "Unknown dependency")
        assert result.name == "imagemagick"
        assert result.status in ("pass", "fail")
        # Message should reference a binary, not "Unknown dependency"
        assert "Unknown dependency" not in result.message

    def test_doctor_checks_convert_maps_to_imagemagick(self) -> None:
        """Legacy name 'convert' is mapped to the imagemagick check."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        result = checker.check("convert")
        assert result.name == "imagemagick"

    def test_check_all_includes_imagemagick(self) -> None:
        """check_all() must include imagemagick so 'aieyes doctor' reports it."""
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        checker = SystemDependencyCheck()
        results = checker.check_all()
        names = [r.name for r in results]
        assert "imagemagick" in names


# ═══════════════════════════════════════════════════════════════════════
# A10-D02: Android bounds (x1,y1,x2,y2) normalized to (x,y,w,h)
# ═══════════════════════════════════════════════════════════════════════


class TestAndroidBoundsConversion:
    """Android _parse_bounds converts (x1,y1,x2,y2) to (x,y,w,h)."""

    def test_android_bounds_conversion(self) -> None:
        from aiyes.adapters.android_tree_adapter import _parse_bounds

        # [100,200][350,500] -> x=100, y=200, w=250, h=300
        assert _parse_bounds("[100,200][350,500]") == (100, 200, 250, 300)

    def test_android_bounds_origin(self) -> None:
        from aiyes.adapters.android_tree_adapter import _parse_bounds

        # Full screen [0,0][1080,1920] -> (0, 0, 1080, 1920)
        assert _parse_bounds("[0,0][1080,1920]") == (0, 0, 1080, 1920)


# ═══════════════════════════════════════════════════════════════════════
# A10-D05: Region validation
# ═══════════════════════════════════════════════════════════════════════


class TestRegionValidation:
    """Use case validates region values: x>=0, y>=0, w>0, h>0."""

    def test_negative_x_raises(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(),
            session_repo=repo,
            screenshot_store=FakeScreenshotStore(),
            crop=FakeCrop(),
        )
        with pytest.raises(ValueError, match="region x must be >= 0"):
            uc.execute(session_id="test-s", region=(-1, 0, 100, 100))

    def test_negative_y_raises(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(),
            session_repo=repo,
            screenshot_store=FakeScreenshotStore(),
            crop=FakeCrop(),
        )
        with pytest.raises(ValueError, match="region y must be >= 0"):
            uc.execute(session_id="test-s", region=(0, -1, 100, 100))

    def test_zero_width_raises(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(),
            session_repo=repo,
            screenshot_store=FakeScreenshotStore(),
            crop=FakeCrop(),
        )
        with pytest.raises(ValueError, match="region width must be > 0"):
            uc.execute(session_id="test-s", region=(0, 0, 0, 100))

    def test_zero_height_raises(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(),
            session_repo=repo,
            screenshot_store=FakeScreenshotStore(),
            crop=FakeCrop(),
        )
        with pytest.raises(ValueError, match="region height must be > 0"):
            uc.execute(session_id="test-s", region=(0, 0, 100, 0))

    def test_negative_width_raises(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(),
            session_repo=repo,
            screenshot_store=FakeScreenshotStore(),
            crop=FakeCrop(),
        )
        with pytest.raises(ValueError, match="region width must be > 0"):
            uc.execute(session_id="test-s", region=(0, 0, -10, 100))

    def test_valid_region_accepted(self) -> None:
        session = _make_session()
        repo = FakeSessionRepository()
        repo.save(session)
        crop = FakeCrop()
        uc = ScreenshotUseCase(
            screenshot=FakeScreenshot(path="/tmp/raw.png"),
            session_repo=repo,
            screenshot_store=FakeScreenshotStore(),
            crop=crop,
        )
        result = uc.execute(session_id="test-s", region=(0, 0, 100, 100))
        assert result.path is not None
        assert len(crop.calls) == 1


# ═══════════════════════════════════════════════════════════════════════
# A10-D07: MCP region parsing validates length==4
# ═══════════════════════════════════════════════════════════════════════


class TestMcpRegionValidation:
    """MCP handler validates region has exactly 4 values."""

    def _get_screenshot_handler(self):
        """Build MCP dispatch table and return the screenshot handler."""
        import dataclasses as dc
        from unittest.mock import MagicMock

        from aiyes.adapters.mcp_server import (
            ServerDependencies,
            _build_dispatch_table,
        )

        fields = {f.name: MagicMock() for f in dc.fields(ServerDependencies)}
        deps = ServerDependencies(**fields)
        table = _build_dispatch_table(deps)
        return table["screenshot"].use_case_call, deps

    def test_mcp_region_too_few_values(self) -> None:
        handler, deps = self._get_screenshot_handler()
        with pytest.raises(ValueError, match="exactly 4 values"):
            handler({"region": "10,20"}, deps, "test-s")

    def test_mcp_region_too_many_values(self) -> None:
        handler, deps = self._get_screenshot_handler()
        with pytest.raises(ValueError, match="exactly 4 values"):
            handler({"region": "10,20,30,40,50"}, deps, "test-s")

    def test_mcp_region_list_too_few(self) -> None:
        handler, deps = self._get_screenshot_handler()
        with pytest.raises(ValueError, match="exactly 4 values"):
            handler({"region": [10, 20]}, deps, "test-s")
