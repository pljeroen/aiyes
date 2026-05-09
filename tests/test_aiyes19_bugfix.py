"""AIYES-19 RED tests — Fix uiautomator dump method and adb path resolution.

Bug 1 (R-AIYES19-01): android_tree_adapter uses /dev/tty dump which is flaky.
    Must use file-based dump: dump to /sdcard/window_dump.xml, cat, then rm.

Bug 2 (R-AIYES19-02): All 4 Android adapters hardcode bare "adb" in subprocess
    calls. Must resolve adb binary path via shutil.which + fallback locations.

These tests are RED against the current (buggy) code.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_android_session(**overrides: Any) -> Session:
    """Construct an Android session for testing."""
    defaults = dict(
        session_id="android-test",
        app_pid=0,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


SAMPLE_UIAUTOMATOR_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example.app" content-desc="" checkable="false"
        checked="false" clickable="false" enabled="true" focusable="false"
        focused="false" scrollable="false" long-clickable="false"
        password="false" selected="false"
        bounds="[0,0][1080,1920]">
    <node index="0" text="Username" resource-id="com.example.app:id/username"
          class="android.widget.EditText" package="com.example.app"
          content-desc="" checkable="false" checked="false"
          clickable="true" enabled="true" focusable="true"
          focused="true" scrollable="false" long-clickable="false"
          password="false" selected="false"
          bounds="[100,200][500,280]">
    </node>
    <node index="1" text="Login" resource-id="com.example.app:id/login_btn"
          class="android.widget.Button" package="com.example.app"
          content-desc="Login button" checkable="false" checked="false"
          clickable="true" enabled="true" focusable="true"
          focused="false" scrollable="false" long-clickable="true"
          password="false" selected="false"
          bounds="[100,300][500,380]">
    </node>
  </node>
</hierarchy>
"""


def _make_subprocess_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    """Create a mock subprocess.CompletedProcess."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _make_pipe_failure() -> MagicMock:
    """Create a mock subprocess result for a failed pipe attempt.

    The pipe path uses raw bytes (no text=True), so stdout/stderr are bytes.
    """
    result = MagicMock()
    result.returncode = 1
    result.stdout = b""
    result.stderr = b""
    return result


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES19-01: File-based uiautomator dump
# ═══════════════════════════════════════════════════════════════════════


class TestTreeAdapterFileBasedDump:
    """The tree adapter must use file-based dump instead of /dev/tty."""

    def test_get_tree_dumps_to_file_on_device(self) -> None:
        """get_tree must run: adb shell uiautomator dump /sdcard/window_dump.xml."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Pipe attempt fails, triggers file-based fallback
        pipe_result = _make_pipe_failure()

        # Mock: dump succeeds, cat returns XML, rm succeeds
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        cat_result = _make_subprocess_result(stdout=SAMPLE_UIAUTOMATOR_XML)
        rm_result = _make_subprocess_result()

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            side_effect=[pipe_result, dump_result, cat_result, rm_result],
        ) as mock_run:
            adapter.get_tree(session)

        # First call is pipe attempt; file-based dump starts at index 1
        calls = mock_run.call_args_list
        assert len(calls) >= 3, (
            f"Expected at least 3 subprocess calls, got {len(calls)}"
        )

        dump_cmd = calls[1][0][0]
        assert "/sdcard/window_dump.xml" in dump_cmd, (
            f"File-based dump must target /sdcard/window_dump.xml, got: {dump_cmd}"
        )
        assert "/dev/tty" not in dump_cmd, f"Must NOT use /dev/tty, got: {dump_cmd}"

    def test_get_tree_reads_file_via_cat(self) -> None:
        """get_tree must run: adb shell cat /sdcard/window_dump.xml."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        pipe_result = _make_pipe_failure()
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        cat_result = _make_subprocess_result(stdout=SAMPLE_UIAUTOMATOR_XML)
        rm_result = _make_subprocess_result()

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            side_effect=[pipe_result, dump_result, cat_result, rm_result],
        ) as mock_run:
            adapter.get_tree(session)

        calls = mock_run.call_args_list
        assert len(calls) >= 3, (
            f"Expected at least 3 subprocess calls, got {len(calls)}"
        )

        cat_cmd = calls[2][0][0]
        assert "cat" in cat_cmd, f"Cat command expected at index 2, got: {cat_cmd}"
        assert "/sdcard/window_dump.xml" in cat_cmd, (
            f"Cat must read /sdcard/window_dump.xml, got: {cat_cmd}"
        )

    def test_get_tree_cleans_up_temp_file(self) -> None:
        """get_tree must run: adb shell rm /sdcard/window_dump.xml."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        pipe_result = _make_pipe_failure()
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        cat_result = _make_subprocess_result(stdout=SAMPLE_UIAUTOMATOR_XML)
        rm_result = _make_subprocess_result()

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            side_effect=[pipe_result, dump_result, cat_result, rm_result],
        ) as mock_run:
            adapter.get_tree(session)

        calls = mock_run.call_args_list
        assert len(calls) >= 4, (
            f"Expected at least 4 subprocess calls, got {len(calls)}"
        )

        rm_cmd = calls[3][0][0]
        assert "rm" in rm_cmd, f"Fourth command must be rm, got: {rm_cmd}"
        assert "/sdcard/window_dump.xml" in rm_cmd, (
            f"Rm must target /sdcard/window_dump.xml, got: {rm_cmd}"
        )

    def test_get_tree_three_command_sequence(self) -> None:
        """get_tree must execute pipe + fallback: dump, cat, rm — in that order."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        pipe_result = _make_pipe_failure()
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        cat_result = _make_subprocess_result(stdout=SAMPLE_UIAUTOMATOR_XML)
        rm_result = _make_subprocess_result()

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            side_effect=[pipe_result, dump_result, cat_result, rm_result],
        ) as mock_run:
            tree = adapter.get_tree(session)

        calls = mock_run.call_args_list
        # 1 pipe + 3 file-based = 4 total
        assert len(calls) == 4, f"Expected exactly 4 subprocess calls, got {len(calls)}"

        # Verify file-based command sequence (indices 1-3)
        dump_cmd = calls[1][0][0]
        cat_cmd = calls[2][0][0]
        rm_cmd = calls[3][0][0]

        assert "uiautomator" in dump_cmd and "dump" in dump_cmd
        assert "cat" in cat_cmd
        assert "rm" in rm_cmd

        # And the result is a valid tree
        assert isinstance(tree, AccessibilityTree)

    def test_get_tree_handles_cat_failure(self) -> None:
        """When dump succeeds but cat fails, RuntimeError is raised with file-read context."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        pipe_result = _make_pipe_failure()
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        cat_result = _make_subprocess_result(returncode=1, stderr="No such file")
        rm_result = _make_subprocess_result()

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            side_effect=[pipe_result, dump_result, cat_result, rm_result],
        ) as mock_run:
            with pytest.raises(RuntimeError):
                adapter.get_tree(session)

        # 1 pipe + at least 2 file-based (dump + cat) = at least 3
        calls = mock_run.call_args_list
        assert len(calls) >= 3, (
            f"Expected at least 3 subprocess calls (pipe + dump + cat), got {len(calls)}"
        )

    def test_cleanup_runs_even_when_parsing_fails(self) -> None:
        """rm must execute even when XML parsing raises an error."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        pipe_result = _make_pipe_failure()
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        # Return garbage XML that will fail to parse
        cat_result = _make_subprocess_result(stdout="NOT VALID XML <<<<>>>")
        rm_result = _make_subprocess_result()

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            side_effect=[pipe_result, dump_result, cat_result, rm_result],
        ) as mock_run:
            with pytest.raises(RuntimeError):
                adapter.get_tree(session)

        # rm must still have been called (index 3: pipe, dump, cat, rm)
        calls = mock_run.call_args_list
        assert len(calls) >= 4, (
            f"Expected rm to be called even after parse failure, got {len(calls)} calls"
        )
        rm_cmd = calls[3][0][0]
        assert "rm" in rm_cmd, f"Fourth call must be rm cleanup, got: {rm_cmd}"

    def test_no_dev_tty_in_dump_command(self) -> None:
        """The adapter must NOT use /dev/tty anywhere in its dump command."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        pipe_result = _make_pipe_failure()
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        cat_result = _make_subprocess_result(stdout=SAMPLE_UIAUTOMATOR_XML)
        rm_result = _make_subprocess_result()

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            side_effect=[pipe_result, dump_result, cat_result, rm_result],
        ) as mock_run:
            adapter.get_tree(session)

        # Check ALL commands — none should reference /dev/tty
        for c in mock_run.call_args_list:
            cmd = c[0][0]
            assert "/dev/tty" not in cmd, f"Found /dev/tty in command: {cmd}"


# ═══════════════════════════════════════════════════════════════════════
# R-AIYES19-02: adb path resolution
# ═══════════════════════════════════════════════════════════════════════


class TestAdbPathResolverExists:
    """A shared adb path resolver function must exist."""

    def test_resolve_adb_path_function_exists(self) -> None:
        """There must be a _resolve_adb_path (or resolve_adb_path) function."""
        # Try the most likely module locations
        found = False
        try:
            from aiyes.adapters.adb_path import resolve_adb_path

            found = True
        except ImportError:
            pass

        if not found:
            try:
                from aiyes.adapters.adb_path import _resolve_adb_path

                found = True
            except ImportError:
                pass

        if not found:
            try:
                from aiyes.adapters.android_tree_adapter import _resolve_adb_path

                found = True
            except ImportError:
                pass

        assert found, (
            "No adb path resolver function found. Expected _resolve_adb_path or "
            "resolve_adb_path in aiyes.adapters.adb_path or aiyes.adapters.android_tree_adapter"
        )


class TestAdbPathResolution:
    """The adb path resolver must check shutil.which then fallback locations."""

    def _get_resolve_func(self):
        """Import the resolve function from wherever it lives."""
        try:
            from aiyes.adapters.adb_path import resolve_adb_path

            return resolve_adb_path
        except ImportError:
            pass
        try:
            from aiyes.adapters.adb_path import _resolve_adb_path

            return _resolve_adb_path
        except ImportError:
            pass
        try:
            from aiyes.adapters.android_tree_adapter import _resolve_adb_path

            return _resolve_adb_path
        except ImportError:
            pass
        pytest.skip("No adb path resolver function found")

    def test_uses_shutil_which_first(self) -> None:
        """Resolver checks shutil.which('adb') first."""
        resolve = self._get_resolve_func()

        with patch("shutil.which", return_value="/usr/bin/adb") as mock_which:
            result = resolve()

        mock_which.assert_called_with("adb")
        assert result == "/usr/bin/adb"

    def test_falls_back_to_android_sdk_platform_tools(self) -> None:
        """When shutil.which returns None, checks ~/android-sdk/platform-tools/adb."""
        resolve = self._get_resolve_func()

        home = os.path.expanduser("~")
        fallback_path = os.path.join(home, "android-sdk", "platform-tools", "adb")

        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile") as mock_isfile:
                # Only the android-sdk path exists
                def isfile_side_effect(path: str) -> bool:
                    return path == fallback_path

                mock_isfile.side_effect = isfile_side_effect
                with patch("os.access", return_value=True):
                    result = resolve()

        assert result == fallback_path

    def test_falls_back_to_android_sdk_capital(self) -> None:
        """When shutil.which returns None, checks ~/Android/Sdk/platform-tools/adb."""
        resolve = self._get_resolve_func()

        home = os.path.expanduser("~")
        fallback_path = os.path.join(home, "Android", "Sdk", "platform-tools", "adb")

        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile") as mock_isfile:

                def isfile_side_effect(path: str) -> bool:
                    return path == fallback_path

                mock_isfile.side_effect = isfile_side_effect
                with patch("os.access", return_value=True):
                    result = resolve()

        assert result == fallback_path

    def test_falls_back_to_usr_local_bin(self) -> None:
        """When shutil.which returns None, checks /usr/local/bin/adb."""
        resolve = self._get_resolve_func()

        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile") as mock_isfile:

                def isfile_side_effect(path: str) -> bool:
                    return path == "/usr/local/bin/adb"

                mock_isfile.side_effect = isfile_side_effect
                with patch("os.access", return_value=True):
                    result = resolve()

        assert result == "/usr/local/bin/adb"

    def test_raises_runtime_error_when_not_found(self) -> None:
        """When adb is not found anywhere, raises RuntimeError with helpful message."""
        resolve = self._get_resolve_func()

        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile", return_value=False):
                with pytest.raises(RuntimeError, match="adb"):
                    resolve()


class TestAllAdaptersUseResolvedPath:
    """All 4 Android adapters must use the resolved adb path, not bare 'adb'."""

    def test_tree_adapter_uses_resolved_path(self) -> None:
        """AndroidUiAutomatorTreeAdapter must NOT have bare 'adb' as first cmd element."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        pipe_result = _make_pipe_failure()
        dump_result = _make_subprocess_result(
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml"
        )
        cat_result = _make_subprocess_result(stdout=SAMPLE_UIAUTOMATOR_XML)
        rm_result = _make_subprocess_result()

        with patch("shutil.which", return_value="/opt/android/platform-tools/adb"):
            with patch(
                "aiyes.adapters.android_tree_adapter.subprocess.run",
                side_effect=[pipe_result, dump_result, cat_result, rm_result],
            ) as mock_run:
                adapter.get_tree(session)

        # The first element of any command must be a full path, not bare "adb"
        for c in mock_run.call_args_list:
            cmd = c[0][0]
            assert cmd[0] != "adb", (
                f"Tree adapter uses bare 'adb' instead of resolved path: {cmd}"
            )

    def test_input_adapter_uses_resolved_path(self) -> None:
        """AdbInputAdapter must NOT have bare 'adb' as first cmd element."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = _make_subprocess_result()

        with patch("shutil.which", return_value="/opt/android/platform-tools/adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                adapter.mouse_click(session, x=100, y=200)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] != "adb", (
            f"Input adapter uses bare 'adb' instead of resolved path: {cmd}"
        )

    def test_screenshot_adapter_uses_resolved_path(self) -> None:
        """AdbScreenshotAdapter must NOT have bare 'adb' as first cmd element."""
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        adapter = AdbScreenshotAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"\x89PNG\r\n"
        mock_result.stderr = b""

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            output_path = f.name

        try:
            with patch("shutil.which", return_value="/opt/android/platform-tools/adb"):
                with patch(
                    "aiyes.adapters.android_screenshot_adapter.subprocess.run",
                    return_value=mock_result,
                ) as mock_run:
                    adapter.take(session, output_path=output_path)

            cmd = mock_run.call_args[0][0]
            assert cmd[0] != "adb", (
                f"Screenshot adapter uses bare 'adb' instead of resolved path: {cmd}"
            )
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_action_adapter_uses_resolved_path(self) -> None:
        """AndroidActionAdapter must NOT have bare 'adb' as first cmd element."""
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        action_adapter = AndroidActionAdapter()
        session = _make_android_session()

        # Set up tree adapter mock so action adapter can resolve a node
        tree_adapter = MagicMock(spec=AndroidUiAutomatorTreeAdapter)
        from aiyes.domain.tree import Node

        mock_tree = AccessibilityTree(
            roots=(
                Node(
                    id="n1",
                    role="Button",
                    name="OK",
                    bounds=(100, 200, 200, 200),
                    states=("enabled",),
                    actions=("click",),
                    children=(),
                ),
            )
        )
        tree_adapter.get_tree.return_value = mock_tree
        action_adapter.set_tree_adapter(tree_adapter)

        mock_result = _make_subprocess_result()

        with patch("shutil.which", return_value="/opt/android/platform-tools/adb"):
            with patch(
                "aiyes.adapters.android_action_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                action_adapter.do_action(session, "n1", "click")

        assert mock_run.called, "Action adapter did not call subprocess.run"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] != "adb", (
            f"Action adapter uses bare 'adb' instead of resolved path: {cmd}"
        )

    def test_all_four_adapters_import_resolver(self) -> None:
        """All 4 Android adapter modules must import from the adb path resolver."""
        import importlib
        import inspect

        adapter_modules = [
            "aiyes.adapters.android_tree_adapter",
            "aiyes.adapters.android_input_adapter",
            "aiyes.adapters.android_screenshot_adapter",
            "aiyes.adapters.android_action_adapter",
        ]

        for mod_name in adapter_modules:
            mod = importlib.import_module(mod_name)
            source = inspect.getsource(mod)

            # The module must reference a resolve function (not just bare "adb")
            has_resolver = "resolve_adb_path" in source or "_resolve_adb_path" in source
            assert has_resolver, (
                f"{mod_name} does not use an adb path resolver function"
            )
