"""Tests for AIYES-39A: Flutter XML parse failure on Android.

Flutter's uiautomator dump produces XML with extra content after the
closing </hierarchy> tag. Python's ET.fromstring() rejects this with
"junk after document element". These tests verify that
parse_uiautomator_xml() strips trailing junk and parses correctly.
"""

from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════════════════
# Sample XML
# ═══════════════════════════════════════════════════════════════════════

CLEAN_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="Hello" resource-id="" class="android.widget.TextView"
        package="com.example" content-desc="" checkable="false"
        checked="false" clickable="true" enabled="true" focusable="true"
        focused="false" scrollable="false" long-clickable="false"
        password="false" selected="false"
        bounds="[100,200][500,280]">
  </node>
</hierarchy>
"""

# Flutter appends extra content after </hierarchy>
XML_WITH_JUNK_AFTER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hierarchy rotation="0">'
    '<node index="0" text="Hello" resource-id="" class="android.widget.TextView"'
    ' package="com.example" content-desc="" checkable="false"'
    ' checked="false" clickable="true" enabled="true" focusable="true"'
    ' focused="false" scrollable="false" long-clickable="false"'
    ' password="false" selected="false"'
    ' bounds="[100,200][500,280]">'
    "</node>"
    "</hierarchy>"
    "\nUI hierrchy dumped to: /dev/tty\n"
)

XML_WITH_TRAILING_WHITESPACE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hierarchy rotation="0">'
    '<node index="0" text="Hello" resource-id="" class="android.widget.TextView"'
    ' package="com.example" content-desc="" checkable="false"'
    ' checked="false" clickable="true" enabled="true" focusable="true"'
    ' focused="false" scrollable="false" long-clickable="false"'
    ' password="false" selected="false"'
    ' bounds="[100,200][500,280]">'
    "</node>"
    "</hierarchy>"
    "\n\n  \n"
)

# Preamble before AND junk after (worst-case real-world scenario)
XML_WITH_PREAMBLE_AND_JUNK = (
    "UI hierrchy dumped to: /dev/stdout\n"
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hierarchy rotation="0">'
    '<node index="0" text="Hello" resource-id="" class="android.widget.TextView"'
    ' package="com.example" content-desc="" checkable="false"'
    ' checked="false" clickable="true" enabled="true" focusable="true"'
    ' focused="false" scrollable="false" long-clickable="false"'
    ' password="false" selected="false"'
    ' bounds="[100,200][500,280]">'
    "</node>"
    "</hierarchy>"
    "\nUI hierrchy dumped to: /dev/tty\n"
)

COMPLETELY_INVALID_XML = "This is not XML at all, no hierarchy tag."


# ═══════════════════════════════════════════════════════════════════════
# Tests for parse_uiautomator_xml()
# ═══════════════════════════════════════════════════════════════════════


class TestFlutterXmlJunkStripping:
    """Verify parse_uiautomator_xml handles junk after </hierarchy>."""

    def test_clean_xml_still_parses(self) -> None:
        """Regression: clean XML without trailing junk still works."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, registry = parse_uiautomator_xml(CLEAN_XML)
        assert len(tree.roots) == 1
        assert tree.roots[0].name == "Hello"

    def test_xml_with_junk_after_hierarchy(self) -> None:
        """Flutter junk after </hierarchy> does not cause parse failure."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, registry = parse_uiautomator_xml(XML_WITH_JUNK_AFTER)
        assert len(tree.roots) == 1
        assert tree.roots[0].name == "Hello"

    def test_xml_with_trailing_whitespace(self) -> None:
        """Trailing whitespace/newlines after </hierarchy> are handled."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, registry = parse_uiautomator_xml(XML_WITH_TRAILING_WHITESPACE)
        assert len(tree.roots) == 1
        assert tree.roots[0].name == "Hello"

    def test_completely_invalid_xml_raises(self) -> None:
        """XML with no hierarchy tag at all still raises an error."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        with pytest.raises(Exception):
            parse_uiautomator_xml(COMPLETELY_INVALID_XML)


# ═══════════════════════════════════════════════════════════════════════
# Tests for _get_tree_pipe() preamble + junk stripping
# ═══════════════════════════════════════════════════════════════════════


class TestPipeJunkStripping:
    """Verify _get_tree_pipe returns clean XML even with preamble AND trailing junk."""

    def test_pipe_strips_preamble_and_trailing_junk(self) -> None:
        """Pipe path handles preamble before + junk after </hierarchy>.

        _get_tree_pipe() already strips preamble lines before XML.
        After AIYES-39A, it must also strip content after </hierarchy>.
        The full round-trip through get_tree must succeed.
        """
        from unittest.mock import MagicMock, patch

        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter
        from aiyes.domain.session import Session
        from aiyes.domain.tree import AccessibilityTree

        adapter = AndroidUiAutomatorTreeAdapter()
        session = Session(
            session_id="android-test",
            app_pid=0,
            app_command="com.example.app/.MainActivity",
            app_args=(),
            name=None,
            started_at=1000.0,
            backend="android",
            device_serial="emulator-5554",
        )

        pipe_result = MagicMock(
            returncode=0,
            stdout=XML_WITH_PREAMBLE_AND_JUNK.encode("utf-8"),
            stderr=b"",
        )

        with patch("shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "aiyes.adapters.android_tree_adapter.subprocess.run",
                return_value=pipe_result,
            ):
                tree = adapter.get_tree(session)

        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1
        assert tree.roots[0].name == "Hello"
