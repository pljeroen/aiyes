"""Tests for Android adb pipe consolidation (P-03).

Tests the fast-path _get_tree_pipe() method that uses
`adb exec-out uiautomator dump /dev/stdout` for single-pipe XML retrieval,
and the fallback to the 3-step file-based approach on failure.

All tests mock subprocess.run — no real adb needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree


# ═════════════════��══════════════════════���══════════════════════════════
# Helpers
# ══════════════���═══════════���════════════════════════════════════════════


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
    <node index="0" text="Hello" resource-id="com.example.app:id/text1"
          class="android.widget.TextView" package="com.example.app"
          content-desc="" checkable="false" checked="false"
          clickable="true" enabled="true" focusable="true"
          focused="false" scrollable="false" long-clickable="false"
          password="false" selected="false"
          bounds="[100,200][500,280]">
    </node>
  </node>
</hierarchy>
"""


# ══════��════════════════════════════════════════════════════════════════
# Pipe approach tests
# ══════════════════��══════════════════════════════��═════════════════════


class TestPipeApproach:
    """Test the fast-path pipe-based tree retrieval."""

    def test_pipe_approach_success(self) -> None:
        """exec-out uiautomator dump /dev/stdout returns valid tree.

        When the pipe approach succeeds with clean XML output,
        get_tree should return a valid AccessibilityTree without
        falling back to the file-based approach.
        """
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Pipe returns clean XML bytes (exec-out returns raw bytes)
        pipe_result = MagicMock(
            returncode=0,
            stdout=SAMPLE_UIAUTOMATOR_XML.encode("utf-8"),
            stderr=b"",
        )

        with patch("shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "aiyes.adapters.android_tree_adapter.subprocess.run",
                return_value=pipe_result,
            ) as mock_run:
                tree = adapter.get_tree(session)

        # Should have called subprocess.run exactly once (pipe only)
        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert "exec-out" in cmd
        assert "uiautomator" in cmd
        assert "/dev/stdout" in cmd

        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1

    def test_pipe_approach_strips_preamble(self) -> None:
        """Preamble line 'UI hierrchy dumped to: /dev/stdout' before XML is handled.

        Note: 'hierrchy' is the real typo in Android source code.
        The adapter must strip this preamble and still parse the XML correctly.
        """
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Real Android output: preamble line + XML
        preamble = "UI hierrchy dumped to: /dev/stdout\n"
        raw_output = (preamble + SAMPLE_UIAUTOMATOR_XML).encode("utf-8")

        pipe_result = MagicMock(
            returncode=0,
            stdout=raw_output,
            stderr=b"",
        )

        with patch("shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "aiyes.adapters.android_tree_adapter.subprocess.run",
                return_value=pipe_result,
            ) as mock_run:
                tree = adapter.get_tree(session)

        # Should succeed via pipe (1 call only, no fallback)
        assert mock_run.call_count == 1
        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1

    def test_pipe_fallback_on_failure(self) -> None:
        """Falls back to 3-step file-based when pipe subprocess fails.

        When the pipe command returns non-zero exit code,
        the adapter should fall back to the file-based approach.
        """
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Pipe fails (non-zero return code)
        pipe_result = MagicMock(
            returncode=1,
            stdout=b"",
            stderr=b"Error: could not dump UI hierarchy",
        )

        # File-based approach succeeds (3 calls: dump, cat, rm)
        dump_result = MagicMock(
            returncode=0,
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml",
            stderr="",
        )
        cat_result = MagicMock(returncode=0, stdout=SAMPLE_UIAUTOMATOR_XML, stderr="")
        rm_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch("shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "aiyes.adapters.android_tree_adapter.subprocess.run",
                side_effect=[pipe_result, dump_result, cat_result, rm_result],
            ) as mock_run:
                tree = adapter.get_tree(session)

        # 1 pipe call + 3 file-based calls = 4 total
        assert mock_run.call_count == 4
        # First call is the pipe attempt
        pipe_cmd = mock_run.call_args_list[0][0][0]
        assert "exec-out" in pipe_cmd
        # Second call is the file-based dump
        dump_cmd = mock_run.call_args_list[1][0][0]
        assert "uiautomator" in dump_cmd
        assert "exec-out" not in dump_cmd

        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1

    def test_pipe_fallback_on_no_xml(self) -> None:
        """Falls back when pipe returns non-XML output.

        Some devices return garbage or error text instead of XML.
        The adapter should detect this and fall back to file-based.
        """
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Pipe returns non-XML content (rc=0 but garbage output)
        pipe_result = MagicMock(
            returncode=0,
            stdout=b"ERROR: could not get idle state.",
            stderr=b"",
        )

        # File-based approach succeeds
        dump_result = MagicMock(
            returncode=0,
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml",
            stderr="",
        )
        cat_result = MagicMock(returncode=0, stdout=SAMPLE_UIAUTOMATOR_XML, stderr="")
        rm_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch("shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "aiyes.adapters.android_tree_adapter.subprocess.run",
                side_effect=[pipe_result, dump_result, cat_result, rm_result],
            ) as mock_run:
                tree = adapter.get_tree(session)

        # 1 pipe call + 3 file-based calls = 4 total
        assert mock_run.call_count == 4
        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1
