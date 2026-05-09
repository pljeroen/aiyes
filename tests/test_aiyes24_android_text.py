"""AIYES-24: Android text input — editable field detection and set_text clear-before-type.

Tests for two bugs:
  BUG-1: _extract_actions() never reports set_text for editable fields
  BUG-2: set_text doesn't clear existing text before typing

Requirements:
  REQ-1: _extract_actions appends "set_text" when class contains "EditText" OR editable="true"
  REQ-2: set_text with non-empty value: tap + Ctrl+A + DEL before input text
  REQ-3: set_text with None/"": clear field only (tap + Ctrl+A + DEL, no input text)
  REQ-4: Existing click/long_click/scroll untouched
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import MagicMock, patch

from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_android_session(**overrides: Any) -> Any:
    """Construct a minimal Android session object for testing."""
    defaults = dict(device_serial="emulator-5554", backend="android")
    defaults.update(overrides)
    return type("S", (), defaults)()


def _make_adapter_with_node(node: Node):
    """Create an AndroidActionAdapter with a mock tree adapter returning one node."""
    from aiyes.adapters.android_action_adapter import AndroidActionAdapter

    adapter = AndroidActionAdapter()
    mock_tree = MagicMock()
    mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
    adapter.set_tree_adapter(mock_tree)
    return adapter


def _extract_adb_commands(mock_run: MagicMock) -> List[List[str]]:
    """Extract the list of adb command arg lists from mock subprocess.run calls."""
    return [c[0][0] for c in mock_run.call_args_list]


def _adb_shell_args(cmd: List[str]) -> List[str]:
    """Return args after 'shell' in an adb command list, e.g. ['input', 'tap', '300', '240']."""
    try:
        idx = cmd.index("shell")
        return cmd[idx + 1 :]
    except ValueError:
        return cmd


# ═══════════════════════════════════════════════════════════════════════
# BUG-1: _extract_actions — editable field detection (REQ-1)
# ═══════════════════════════════════════════════════════════════════════


class TestExtractActionsEditableDetection:
    """REQ-1: _extract_actions must report set_text for editable text fields."""

    def test_edittext_class_reports_set_text(self) -> None:
        """XML element with class="android.widget.EditText" -> actions includes set_text."""
        import xml.etree.ElementTree as ET

        from aiyes.adapters.android_tree_adapter import _extract_actions

        elem = ET.fromstring(
            '<node class="android.widget.EditText" clickable="false" '
            'long-clickable="false" scrollable="false" />'
        )
        actions = _extract_actions(elem)
        assert "set_text" in actions

    def test_appcompat_edittext_reports_set_text(self) -> None:
        """AppCompatEditText class also triggers set_text detection."""
        import xml.etree.ElementTree as ET

        from aiyes.adapters.android_tree_adapter import _extract_actions

        elem = ET.fromstring(
            '<node class="android.support.v7.widget.AppCompatEditText" '
            'clickable="false" long-clickable="false" scrollable="false" />'
        )
        actions = _extract_actions(elem)
        assert "set_text" in actions

    def test_editable_true_reports_set_text(self) -> None:
        """XML element with editable="true" -> actions includes set_text."""
        import xml.etree.ElementTree as ET

        from aiyes.adapters.android_tree_adapter import _extract_actions

        elem = ET.fromstring(
            '<node class="android.widget.TextView" editable="true" '
            'clickable="false" long-clickable="false" scrollable="false" />'
        )
        actions = _extract_actions(elem)
        assert "set_text" in actions

    def test_edittext_with_editable_false_still_reports_set_text(self) -> None:
        """EditText with editable="false" still reports set_text.

        The class name is the primary signal for text-input capability.
        The editable attribute is supplementary, catching non-EditText widgets
        that are marked editable. An EditText with editable="false" is a rare
        edge case (programmatically disabled field). The enabled/focusable
        states should gate interaction at a higher level, not the action
        detection. Without this test, a future developer might "fix" the OR
        to AND, breaking detection for standard EditText widgets that lack
        the editable attribute entirely.
        """
        import xml.etree.ElementTree as ET

        from aiyes.adapters.android_tree_adapter import _extract_actions

        elem = ET.fromstring(
            '<node class="android.widget.EditText" editable="false" '
            'clickable="false" long-clickable="false" scrollable="false" />'
        )
        actions = _extract_actions(elem)
        assert "set_text" in actions

    def test_non_editable_no_set_text(self) -> None:
        """XML element with class=TextView and no editable=true -> no set_text."""
        import xml.etree.ElementTree as ET

        from aiyes.adapters.android_tree_adapter import _extract_actions

        elem = ET.fromstring(
            '<node class="android.widget.TextView" '
            'clickable="false" long-clickable="false" scrollable="false" />'
        )
        actions = _extract_actions(elem)
        assert "set_text" not in actions

    def test_button_no_set_text(self) -> None:
        """Clickable Button does NOT get set_text."""
        import xml.etree.ElementTree as ET

        from aiyes.adapters.android_tree_adapter import _extract_actions

        elem = ET.fromstring(
            '<node class="android.widget.Button" clickable="true" '
            'long-clickable="false" scrollable="false" />'
        )
        actions = _extract_actions(elem)
        assert "click" in actions
        assert "set_text" not in actions

    def test_edittext_still_reports_click(self) -> None:
        """EditText that is also clickable -> actions includes both click and set_text."""
        import xml.etree.ElementTree as ET

        from aiyes.adapters.android_tree_adapter import _extract_actions

        elem = ET.fromstring(
            '<node class="android.widget.EditText" clickable="true" '
            'long-clickable="false" scrollable="false" />'
        )
        actions = _extract_actions(elem)
        assert "click" in actions
        assert "set_text" in actions

    def test_full_tree_parse_edittext(self) -> None:
        """parse_uiautomator_xml with an EditText in the tree -> node has set_text."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="Search" resource-id="search_field"
        class="android.widget.EditText" package="com.example"
        content-desc="" checkable="false" checked="false"
        clickable="true" enabled="true" focusable="true"
        focused="false" scrollable="false" long-clickable="false"
        password="false" selected="false"
        bounds="[50,100][400,160]">
  </node>
</hierarchy>"""
        tree, _ = parse_uiautomator_xml(xml)
        all_nodes = flatten_nodes(tree.roots)
        edittext_nodes = [n for n in all_nodes if n.role == "EditText"]
        assert len(edittext_nodes) == 1
        assert "set_text" in edittext_nodes[0].actions


# ═══════════════════════════════════════════════════════════════════════
# BUG-2: set_text clear-before-type (REQ-2, REQ-3)
# ═══════════════════════════════════════════════════════════════════════


class TestSetTextClearBeforeType:
    """REQ-2/REQ-3: set_text must clear existing text before typing."""

    def _edittext_node(self) -> Node:
        """Shared EditText node with bounds (100, 200, 500, 280) -> center (300, 240)."""
        return Node(
            id="n_001",
            role="EditText",
            name="Username",
            bounds=(100, 200, 400, 80),
            states=("enabled", "focusable"),
            actions=("click", "set_text"),
        )

    def test_set_text_clears_before_typing(self) -> None:
        """set_text with value="hello": tap + Ctrl+A + DEL before input text."""
        node = self._edittext_node()
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_001", "set_text", value="hello")

        assert result.success is True

        calls = _extract_adb_commands(mock_run)
        shell_args = [_adb_shell_args(c) for c in calls]

        # Expect 4 commands: tap, keycombo (Ctrl+A), keyevent 67 (DEL), input text
        assert len(shell_args) == 4, (
            f"Expected 4 adb calls, got {len(shell_args)}: {shell_args}"
        )

        # Command 0: tap to focus
        assert shell_args[0][0] == "input"
        assert shell_args[0][1] == "tap"

        # Command 1: Ctrl+A (select all) via keycombo
        assert shell_args[1] == ["input", "keycombo", "113", "29"]

        # Command 2: DEL (delete selection)
        assert shell_args[2] == ["input", "keyevent", "67"]

        # Command 3: type the new text
        assert shell_args[3][0] == "input"
        assert shell_args[3][1] == "text"
        assert shell_args[3][2] == "hello"

    def test_set_text_empty_clears_only(self) -> None:
        """set_text with value="" : tap + Ctrl+A + DEL, NO input text."""
        node = self._edittext_node()
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_001", "set_text", value="")

        assert result.success is True

        calls = _extract_adb_commands(mock_run)
        shell_args = [_adb_shell_args(c) for c in calls]

        # Expect 3 commands: tap, keycombo (Ctrl+A), keyevent 67 (DEL) — no text
        assert len(shell_args) == 3, (
            f"Expected 3 adb calls, got {len(shell_args)}: {shell_args}"
        )

        assert shell_args[0][1] == "tap"
        assert shell_args[1] == ["input", "keycombo", "113", "29"]
        assert shell_args[2] == ["input", "keyevent", "67"]

    def test_set_text_none_clears_only(self) -> None:
        """set_text with value=None: tap + Ctrl+A + DEL, NO input text."""
        node = self._edittext_node()
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_001", "set_text", value=None)

        assert result.success is True

        calls = _extract_adb_commands(mock_run)
        shell_args = [_adb_shell_args(c) for c in calls]

        # Expect 3 commands: tap, keycombo (Ctrl+A), keyevent 67 (DEL) — no text
        assert len(shell_args) == 3, (
            f"Expected 3 adb calls, got {len(shell_args)}: {shell_args}"
        )

        assert shell_args[0][1] == "tap"
        assert shell_args[1] == ["input", "keycombo", "113", "29"]
        assert shell_args[2] == ["input", "keyevent", "67"]

    def test_set_text_taps_at_node_center(self) -> None:
        """Tap coordinate matches the node's center (300, 240)."""
        node = self._edittext_node()
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            adapter.do_action(session, "n_001", "set_text", value="x")

        calls = _extract_adb_commands(mock_run)
        shell_args = [_adb_shell_args(c) for c in calls]

        # First command is the tap — verify coordinates
        assert shell_args[0] == ["input", "tap", "300", "240"]

    def test_set_text_unresolvable_node_returns_failure(self) -> None:
        """set_text with unresolvable node_id returns success=False and issues zero adb commands.

        When target_node is None, the clear-before-type sequence must not be
        attempted. No tap, no keycombo, no keyevent, no text command.
        """
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        # Tree adapter that returns an empty tree — node "n_999" won't resolve
        mock_tree = MagicMock()
        mock_tree.get_tree.return_value = AccessibilityTree(roots=())
        adapter.set_tree_adapter(mock_tree)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_999", "set_text", value="hello")

        assert result.success is False
        assert mock_run.call_count == 0, (
            f"Expected zero adb commands for unresolvable node, got {mock_run.call_count}"
        )

    def test_set_text_rejected_when_node_does_not_advertise_set_text(self) -> None:
        """Resolved non-editable nodes must reject set_text without adb input."""
        node = Node(
            id="n_005",
            role="Button",
            name="Submit",
            bounds=(100, 300, 400, 80),
            states=("enabled",),
            actions=("click", "focus"),
        )
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_005", "set_text", value="hello")

        assert result.success is False
        assert result.available_actions == ("click", "focus")
        shell_args = [_adb_shell_args(c) for c in _extract_adb_commands(mock_run)]
        forbidden = (
            ["input", "tap", "300", "340"],
            ["input", "keycombo", "113", "29"],
            ["input", "keyevent", "67"],
            ["input", "text", "hello"],
        )
        for args in forbidden:
            assert args not in shell_args
        assert mock_run.call_count == 0


# ═══════════════════════════════════════════════════════════════════════
# REQ-4: Existing actions unchanged
# ═══════════════════════════════════════════════════════════════════════


class TestExistingActionsUnchanged:
    """REQ-4: click/long_click/scroll must not be affected by set_text changes."""

    def test_click_action_unchanged(self) -> None:
        """Click action still just taps center — no Ctrl+A or DEL sequence."""
        node = Node(
            id="n_002",
            role="Button",
            name="Submit",
            bounds=(100, 300, 400, 80),
            states=("enabled",),
            actions=("click",),
        )
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_002", "click")

        assert result.success is True

        calls = _extract_adb_commands(mock_run)
        shell_args = [_adb_shell_args(c) for c in calls]
        assert len(shell_args) == 1, (
            f"Click should be exactly 1 adb call, got {len(shell_args)}"
        )
        assert shell_args[0][1] == "tap"
        # No keycombo or keyevent calls
        for args in shell_args:
            assert args[1] != "keycombo"
            assert args[1] != "keyevent"

    def test_long_click_action_unchanged(self) -> None:
        """Long click still uses swipe-at-same-point — no clear sequence."""
        node = Node(
            id="n_003",
            role="Button",
            name="Hold",
            bounds=(100, 300, 400, 80),
            states=("enabled",),
            actions=("long_click",),
        )
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_003", "long_click")

        assert result.success is True

        calls = _extract_adb_commands(mock_run)
        shell_args = [_adb_shell_args(c) for c in calls]
        assert len(shell_args) == 1
        assert shell_args[0][1] == "swipe"

    def test_scroll_action_unchanged(self) -> None:
        """Scroll still uses swipe — no clear sequence."""
        node = Node(
            id="n_004",
            role="WebView",
            name="Content",
            bounds=(0, 400, 1080, 1520),
            states=("enabled",),
            actions=("scroll",),
        )
        adapter = _make_adapter_with_node(node)
        session = _make_android_session()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_004", "scroll")

        assert result.success is True

        calls = _extract_adb_commands(mock_run)
        shell_args = [_adb_shell_args(c) for c in calls]
        assert len(shell_args) == 1
        assert shell_args[0][1] == "swipe"
