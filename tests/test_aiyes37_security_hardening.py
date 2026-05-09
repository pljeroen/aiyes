"""Tests for AIYES-37 — Defense-in-depth hardening from external security review.

Items covered:
  Item 1: SSH_AUTH_SOCK and GPG_AGENT_INFO stripped from child environment
  Item 2: ~/.aieyes/ directory permissions 0o700, file permissions 0o600
  Item 4: Size ceiling on adb capture_output results
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)


# ══════════════════════════════════════════════════════════════════════
# Item 1: SSH_AUTH_SOCK and GPG_AGENT_INFO in credential strip list
# ══════════════════════════════════════════════════════════════════════


class TestSshGpgCredentialStripping:
    """Item 1: SSH_AUTH_SOCK and GPG_AGENT_INFO must be stripped from child env."""

    def _get_app_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_overrides: Dict[str, str],
    ) -> Dict[str, str]:
        """Execute session start and return env passed to process.start()."""
        from aiyes.domain.use_cases.session_start import SessionStartUseCase

        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)

        fp = FakeProcess()
        uc = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(),
            atspi_bus=FakeAccessibilityBus(),
            process=fp,
            session_repo=FakeSessionRepository(),
            clock=FakeClock(),
        )
        uc.execute(app_command="test-app", app_args=[])

        start_calls = [c for c in fp.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        return env

    def test_ssh_auth_sock_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Item 1: SSH_AUTH_SOCK must be stripped from child environment."""
        env = self._get_app_env(
            monkeypatch, {"SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.sock"}
        )
        assert "SSH_AUTH_SOCK" not in env, (
            "SSH_AUTH_SOCK leaked into session environment"
        )

    def test_gpg_agent_info_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Item 1: GPG_AGENT_INFO must be stripped from child environment."""
        env = self._get_app_env(
            monkeypatch, {"GPG_AGENT_INFO": "/run/user/1000/gnupg/S.gpg-agent:0:1"}
        )
        assert "GPG_AGENT_INFO" not in env, (
            "GPG_AGENT_INFO leaked into session environment"
        )

    def test_both_ssh_and_gpg_stripped_together(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Item 1: Both vars stripped when present simultaneously."""
        env = self._get_app_env(
            monkeypatch,
            {
                "SSH_AUTH_SOCK": "/tmp/ssh-XXXXXXabcdef/agent.12345",
                "GPG_AGENT_INFO": "/run/user/1000/gnupg/S.gpg-agent:0:1",
            },
        )
        assert "SSH_AUTH_SOCK" not in env
        assert "GPG_AGENT_INFO" not in env


# ══════════════════════════════════════════════════════════════════════
# Item 2: File and directory permissions
# ══════════════════════════════════════════════════════════════════════


class TestDirectoryPermissions:
    """Item 2: ~/.aieyes/ directories must be created with mode 0o700."""

    def test_session_repo_mkdir_uses_0o700(self, tmp_path: Path) -> None:
        """Item 2: FileSessionRepository.save() creates dirs with mode 0o700."""
        from aiyes.adapters.file_session_repository import FileSessionRepository
        from aiyes.domain.session import Session

        repo = FileSessionRepository(base_dir=str(tmp_path / ".aieyes"))
        session = Session(
            session_id="abc12345",
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
        )
        repo.save(session)

        session_dir = tmp_path / ".aieyes" / "abc12345"
        dir_mode = stat.S_IMODE(session_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Session directory mode is {oct(dir_mode)}, expected 0o700"
        )

    def test_tree_store_mkdir_uses_0o700(self, tmp_path: Path) -> None:
        """Item 2: FileTreeStore.save_tree() creates dirs with mode 0o700."""
        from aiyes.adapters.file_tree_store import FileTreeStore
        from aiyes.domain.tree import AccessibilityTree

        store = FileTreeStore(base_dir=str(tmp_path / ".aieyes"))
        tree = AccessibilityTree(roots=())
        store.save_tree("abc12345", tree)

        session_dir = tmp_path / ".aieyes" / "abc12345"
        dir_mode = stat.S_IMODE(session_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Tree store directory mode is {oct(dir_mode)}, expected 0o700"
        )

    def test_screenshot_store_mkdir_uses_0o700(self, tmp_path: Path) -> None:
        """Item 2: FileScreenshotStore.save_screenshot() creates dirs with mode 0o700."""
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path / ".aieyes"))

        # Create a source file to copy
        src = tmp_path / "source.png"
        src.write_bytes(b"\x89PNG fake")

        store.save_screenshot("abc12345", str(src))

        session_dir = tmp_path / ".aieyes" / "abc12345"
        dir_mode = stat.S_IMODE(session_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Screenshot store directory mode is {oct(dir_mode)}, expected 0o700"
        )

    def test_operation_log_mkdir_uses_0o700(self, tmp_path: Path) -> None:
        """Item 2: FileOperationLog.append() creates dirs with mode 0o700."""
        from aiyes.adapters.file_operation_log import FileOperationLog
        from aiyes.domain.operation_record import OperationRecord

        log = FileOperationLog(base_dir=str(tmp_path / ".aieyes"))
        record = OperationRecord(
            timestamp=1000.0,
            session_id="abc12345",
            command="test",
            duration_ms=10,
            exit_code=0,
            error="",
        )
        log.append(record)

        session_dir = tmp_path / ".aieyes" / "abc12345"
        dir_mode = stat.S_IMODE(session_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Operation log directory mode is {oct(dir_mode)}, expected 0o700"
        )


class TestFilePermissions:
    """Item 2: Files in ~/.aieyes/ must be created with mode 0o600."""

    def test_session_repo_file_uses_0o600(self, tmp_path: Path) -> None:
        """Item 2: session.json created with mode 0o600."""
        from aiyes.adapters.file_session_repository import FileSessionRepository
        from aiyes.domain.session import Session

        repo = FileSessionRepository(base_dir=str(tmp_path / ".aieyes"))
        session = Session(
            session_id="abc12345",
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
        )
        repo.save(session)

        session_file = tmp_path / ".aieyes" / "abc12345" / "session.json"
        file_mode = stat.S_IMODE(session_file.stat().st_mode)
        assert file_mode == 0o600, (
            f"session.json mode is {oct(file_mode)}, expected 0o600"
        )

    def test_tree_store_file_uses_0o600(self, tmp_path: Path) -> None:
        """Item 2: tree.json created with mode 0o600."""
        from aiyes.adapters.file_tree_store import FileTreeStore
        from aiyes.domain.tree import AccessibilityTree

        store = FileTreeStore(base_dir=str(tmp_path / ".aieyes"))
        tree = AccessibilityTree(roots=())
        store.save_tree("abc12345", tree)

        tree_file = tmp_path / ".aieyes" / "abc12345" / "tree.json"
        file_mode = stat.S_IMODE(tree_file.stat().st_mode)
        assert file_mode == 0o600, f"tree.json mode is {oct(file_mode)}, expected 0o600"

    def test_screenshot_store_file_uses_0o600(self, tmp_path: Path) -> None:
        """Item 2: screenshot.png created with mode 0o600."""
        from aiyes.adapters.file_screenshot_store import FileScreenshotStore

        store = FileScreenshotStore(base_dir=str(tmp_path / ".aieyes"))

        src = tmp_path / "source.png"
        src.write_bytes(b"\x89PNG fake")

        store.save_screenshot("abc12345", str(src))

        screenshot_file = tmp_path / ".aieyes" / "abc12345" / "screenshot.png"
        file_mode = stat.S_IMODE(screenshot_file.stat().st_mode)
        assert file_mode == 0o600, (
            f"screenshot.png mode is {oct(file_mode)}, expected 0o600"
        )

    def test_operation_log_file_uses_0o600(self, tmp_path: Path) -> None:
        """Item 2: operations.jsonl created with mode 0o600."""
        from aiyes.adapters.file_operation_log import FileOperationLog
        from aiyes.domain.operation_record import OperationRecord

        log = FileOperationLog(base_dir=str(tmp_path / ".aieyes"))
        record = OperationRecord(
            timestamp=1000.0,
            session_id="abc12345",
            command="test",
            duration_ms=10,
            exit_code=0,
            error="",
        )
        log.append(record)

        log_file = tmp_path / ".aieyes" / "abc12345" / "operations.jsonl"
        file_mode = stat.S_IMODE(log_file.stat().st_mode)
        assert file_mode == 0o600, (
            f"operations.jsonl mode is {oct(file_mode)}, expected 0o600"
        )


# ══════════════════════════════════════════════════════════════════════
# Item 4: Size ceiling on adb capture_output results
# ══════════════════════════════════════════════════════════════════════


class TestAdbScreenshotSizeCeiling:
    """Item 4: AdbScreenshotAdapter must reject oversized capture_output."""

    def test_max_screenshot_bytes_constant_exists(self) -> None:
        """Item 4: MAX_SCREENSHOT_BYTES constant must exist."""
        from aiyes.adapters.android_screenshot_adapter import MAX_SCREENSHOT_BYTES

        assert isinstance(MAX_SCREENSHOT_BYTES, int)
        assert MAX_SCREENSHOT_BYTES > 0

    def test_screenshot_exceeding_ceiling_raises(self) -> None:
        """Item 4: Screenshot larger than MAX_SCREENSHOT_BYTES raises RuntimeError."""
        from aiyes.adapters.android_screenshot_adapter import (
            AdbScreenshotAdapter,
            MAX_SCREENSHOT_BYTES,
        )

        adapter = AdbScreenshotAdapter()

        # Create a fake session with device_serial
        session = MagicMock()
        session.device_serial = "emulator-5554"

        # Simulate oversized screenshot output
        oversized_stdout = b"\x89PNG" + b"\x00" * (MAX_SCREENSHOT_BYTES + 1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = oversized_stdout
        mock_result.stderr = b""

        with (
            patch("subprocess.run", return_value=mock_result),
            patch(
                "aiyes.adapters.adb_path.resolve_adb_path",
                return_value="/usr/bin/adb",
            ),
        ):
            with pytest.raises(
                RuntimeError, match="exceeds.*ceiling|too large|size.*limit"
            ):
                adapter.take(session)

    def test_screenshot_under_ceiling_succeeds(self, tmp_path: Path) -> None:
        """Item 4: Screenshot under MAX_SCREENSHOT_BYTES is accepted normally."""
        from aiyes.adapters.android_screenshot_adapter import (
            AdbScreenshotAdapter,
            MAX_SCREENSHOT_BYTES,
        )

        adapter = AdbScreenshotAdapter()

        session = MagicMock()
        session.device_serial = "emulator-5554"

        # Normal-sized screenshot
        normal_stdout = b"\x89PNG" + b"\x00" * 1000
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = normal_stdout
        mock_result.stderr = b""

        output_path = str(tmp_path / "screenshot.png")

        with (
            patch("subprocess.run", return_value=mock_result),
            patch(
                "aiyes.adapters.adb_path.resolve_adb_path",
                return_value="/usr/bin/adb",
            ),
        ):
            result = adapter.take(session, output_path=output_path)
            assert Path(result).exists()


class TestAdbTreeSizeCeiling:
    """Item 4: AndroidUiAutomatorTreeAdapter must reject oversized XML output."""

    def test_max_xml_bytes_constant_exists(self) -> None:
        """Item 4: MAX_XML_BYTES constant must exist."""
        from aiyes.adapters.android_tree_adapter import MAX_XML_BYTES

        assert isinstance(MAX_XML_BYTES, int)
        assert MAX_XML_BYTES > 0

    def test_xml_exceeding_ceiling_raises_on_pipe_path(self) -> None:
        """Item 4: XML output larger than MAX_XML_BYTES raises RuntimeError (pipe).

        The pipe path raises size-exceeded, which triggers the file fallback.
        The file fallback also gets oversized data and raises size-exceeded.
        Net result: RuntimeError with 'exceeds' in the message.
        """
        from aiyes.adapters.android_tree_adapter import (
            AndroidUiAutomatorTreeAdapter,
            MAX_XML_BYTES,
        )

        adapter = AndroidUiAutomatorTreeAdapter()

        session = MagicMock()
        session.device_serial = "emulator-5554"

        # Oversized XML output (bytes for pipe path)
        oversized_xml_bytes = (
            b"<hierarchy>" + b"<node/>" * (MAX_XML_BYTES // 7 + 1) + b"</hierarchy>"
        )
        assert len(oversized_xml_bytes) > MAX_XML_BYTES

        # Oversized XML as string (for file fallback path with text=True)
        oversized_xml_str = oversized_xml_bytes.decode("utf-8")

        # Pipe path result (capture_output=True, no text=True → bytes)
        pipe_result = MagicMock()
        pipe_result.returncode = 0
        pipe_result.stdout = oversized_xml_bytes
        pipe_result.stderr = b""

        # File fallback: dump result
        dump_result = MagicMock()
        dump_result.returncode = 0
        dump_result.stdout = "dumped"
        dump_result.stderr = ""

        # File fallback: cat result (text=True → string)
        cat_result = MagicMock()
        cat_result.returncode = 0
        cat_result.stdout = oversized_xml_str
        cat_result.stderr = ""

        # File fallback: rm cleanup
        rm_result = MagicMock()
        rm_result.returncode = 0

        call_count = 0

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pipe_result  # pipe: oversized → size error
            elif call_count == 2:
                return dump_result  # file fallback: dump
            elif call_count == 3:
                return cat_result  # file fallback: cat → oversized
            else:
                return rm_result  # file fallback: rm cleanup

        with (
            patch("subprocess.run", side_effect=side_effect),
            patch(
                "aiyes.adapters.adb_path.resolve_adb_path",
                return_value="/usr/bin/adb",
            ),
        ):
            with pytest.raises(
                RuntimeError, match="exceeds.*ceiling|too large|size.*limit"
            ):
                adapter.get_tree(session)

    def test_xml_exceeding_ceiling_raises_on_file_path(self) -> None:
        """Item 4: XML output larger than MAX_XML_BYTES raises RuntimeError (file fallback)."""
        from aiyes.adapters.android_tree_adapter import (
            AndroidUiAutomatorTreeAdapter,
            MAX_XML_BYTES,
        )

        adapter = AndroidUiAutomatorTreeAdapter()

        session = MagicMock()
        session.device_serial = "emulator-5554"

        # Pipe path fails, file fallback also oversized
        oversized_xml_text = (
            "<hierarchy>" + "<node/>" * (MAX_XML_BYTES // 7 + 1) + "</hierarchy>"
        )

        # First call (pipe) fails
        pipe_result = MagicMock()
        pipe_result.returncode = 1
        pipe_result.stdout = b""
        pipe_result.stderr = b"error"

        # Second call (dump) succeeds
        dump_result = MagicMock()
        dump_result.returncode = 0
        dump_result.stdout = "dumped"
        dump_result.stderr = ""

        # Third call (cat) returns oversized content
        cat_result = MagicMock()
        cat_result.returncode = 0
        cat_result.stdout = oversized_xml_text
        cat_result.stderr = ""

        # Fourth call (rm) cleanup
        rm_result = MagicMock()
        rm_result.returncode = 0

        call_count = 0

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Pipe path: return failure to trigger file fallback
                raise RuntimeError("pipe failed")
            elif call_count == 2:
                return dump_result
            elif call_count == 3:
                return cat_result
            else:
                return rm_result

        with (
            patch("subprocess.run", side_effect=side_effect),
            patch(
                "aiyes.adapters.adb_path.resolve_adb_path",
                return_value="/usr/bin/adb",
            ),
        ):
            with pytest.raises(
                RuntimeError, match="exceeds.*ceiling|too large|size.*limit"
            ):
                adapter.get_tree(session)

    def test_xml_under_ceiling_succeeds(self) -> None:
        """Item 4: XML output under MAX_XML_BYTES is parsed normally."""
        from aiyes.adapters.android_tree_adapter import (
            AndroidUiAutomatorTreeAdapter,
            MAX_XML_BYTES,
        )

        adapter = AndroidUiAutomatorTreeAdapter()

        session = MagicMock()
        session.device_serial = "emulator-5554"

        # Valid small XML
        small_xml = b'<?xml version="1.0" ?><hierarchy rotation="0"><node bounds="[0,0][1080,1920]" class="android.widget.FrameLayout" text="" content-desc="" resource-id="" enabled="true" focusable="false" focused="false" selected="false" clickable="false" long-clickable="false" scrollable="false" checked="false" /></hierarchy>'

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = small_xml
        mock_result.stderr = b""

        with (
            patch("subprocess.run", return_value=mock_result),
            patch(
                "aiyes.adapters.adb_path.resolve_adb_path",
                return_value="/usr/bin/adb",
            ),
        ):
            tree = adapter.get_tree(session)
            assert tree is not None
