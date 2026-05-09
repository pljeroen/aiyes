"""Tests for real Android adapter implementations.

Tests XML parsing, key mapping, text escaping, bounds parsing, and
adb subprocess interaction via mocked subprocess calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes
from aiyes.domain.types import ActionPortResult


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
    <node index="2" text="" resource-id=""
          class="android.webkit.WebView" package="com.example.app"
          content-desc="Help page" checkable="false" checked="false"
          clickable="false" enabled="true" focusable="false"
          focused="false" scrollable="true" long-clickable="false"
          password="false" selected="false"
          bounds="[0,400][1080,1920]">
    </node>
  </node>
</hierarchy>
"""


# ═══════════════════════════════════════════════════════════════════════
# AndroidUiAutomatorTreeAdapter — XML parsing
# ═══════════════════════════════════════════════════════════════════════


class TestUiAutomatorXmlParsing:
    """Test XML parsing of uiautomator dump output."""

    def test_parse_sample_xml_returns_tree(self) -> None:
        """Parsed XML produces a valid AccessibilityTree."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, registry = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1  # FrameLayout root

    def test_parse_node_roles_from_class(self) -> None:
        """Android class names are stripped to short role names."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        roles = {n.role for n in all_nodes}
        assert "FrameLayout" in roles
        assert "EditText" in roles
        assert "Button" in roles
        assert "WebView" in roles

    def test_parse_node_names_from_text(self) -> None:
        """Text attribute becomes the node name."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        names = {n.name for n in all_nodes}
        assert "Username" in names
        assert "Login" in names

    def test_parse_node_name_falls_back_to_content_desc(self) -> None:
        """When text is empty, content-desc is used as name."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        # The WebView has empty text but content-desc="Help page"
        webview_nodes = [n for n in all_nodes if n.role == "WebView"]
        assert len(webview_nodes) == 1
        assert webview_nodes[0].name == "Help page"

    def test_parse_bounds(self) -> None:
        """Bounds are parsed from [x1,y1][x2,y2] and converted to (x, y, w, h)."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        button_nodes = [n for n in all_nodes if n.role == "Button"]
        assert len(button_nodes) == 1
        # [100,300][500,380] -> (100, 300, 400, 80)
        assert button_nodes[0].bounds == (100, 300, 400, 80)

    def test_parse_states(self) -> None:
        """States are extracted from boolean attributes."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        edittext_nodes = [n for n in all_nodes if n.role == "EditText"]
        assert len(edittext_nodes) == 1
        states = edittext_nodes[0].states
        assert "enabled" in states
        assert "focusable" in states
        assert "focused" in states

    def test_parse_actions(self) -> None:
        """Actions are extracted from clickable/long-clickable/scrollable."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        button_nodes = [n for n in all_nodes if n.role == "Button"]
        assert len(button_nodes) == 1
        actions = button_nodes[0].actions
        assert "click" in actions
        assert "long_click" in actions

    def test_parse_scrollable_action(self) -> None:
        """Scrollable attribute maps to scroll action."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        webview_nodes = [n for n in all_nodes if n.role == "WebView"]
        assert len(webview_nodes) == 1
        assert "scroll" in webview_nodes[0].actions

    def test_parse_children_hierarchy(self) -> None:
        """Root node has children parsed correctly."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, _ = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        root = tree.roots[0]
        assert len(root.children) == 3

    def test_parse_registry_assigns_ids(self) -> None:
        """NodeIdRegistry assigns IDs to all nodes."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        tree, registry = parse_uiautomator_xml(SAMPLE_UIAUTOMATOR_XML)
        all_nodes = flatten_nodes(tree.roots)
        for node in all_nodes:
            assert node.id.startswith("n_")
            assert registry.has_id(node.id)

    def test_parse_empty_hierarchy(self) -> None:
        """Empty hierarchy produces empty tree."""
        from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"></hierarchy>'
        )
        tree, _ = parse_uiautomator_xml(xml)
        assert len(tree.roots) == 0


class TestBoundsParsing:
    """Test the _parse_bounds helper function — converts (x1,y1,x2,y2) to (x,y,w,h)."""

    def test_valid_bounds(self) -> None:
        from aiyes.adapters.android_tree_adapter import _parse_bounds

        # [0,0][1080,1920] -> (0, 0, 1080, 1920)
        assert _parse_bounds("[0,0][1080,1920]") == (0, 0, 1080, 1920)
        # [100,200][300,400] -> (100, 200, 200, 200)
        assert _parse_bounds("[100,200][300,400]") == (100, 200, 200, 200)

    def test_invalid_bounds_returns_zeros(self) -> None:
        from aiyes.adapters.android_tree_adapter import _parse_bounds

        assert _parse_bounds("") == (0, 0, 0, 0)
        assert _parse_bounds("invalid") == (0, 0, 0, 0)
        assert _parse_bounds("[0,0]") == (0, 0, 0, 0)

    def test_none_bounds_returns_zeros(self) -> None:
        from aiyes.adapters.android_tree_adapter import _parse_bounds

        # _parse_bounds handles AttributeError for None input
        assert _parse_bounds(None) == (0, 0, 0, 0)  # type: ignore[arg-type]

    def test_bounds_conversion_x_y_w_h(self) -> None:
        """Verify the x1,y1,x2,y2 to x,y,w,h conversion is correct."""
        from aiyes.adapters.android_tree_adapter import _parse_bounds

        # [50,100][250,400] -> x=50, y=100, w=200, h=300
        assert _parse_bounds("[50,100][250,400]") == (50, 100, 200, 300)


class TestTreeAdapterSubprocess:
    """Test AndroidUiAutomatorTreeAdapter with mocked subprocess."""

    def test_get_tree_calls_adb(self) -> None:
        """get_tree tries pipe, falls back to file-based dump: dump, cat, rm."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Pipe attempt fails
        pipe_result = MagicMock(returncode=1, stdout=b"", stderr=b"")

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

        # 1 pipe + 3 file-based = 4 total
        assert mock_run.call_count == 4
        # Second call (index 1) is the file-based dump
        dump_cmd = mock_run.call_args_list[1][0][0]
        assert "uiautomator" in dump_cmd
        assert "-s" in dump_cmd
        assert "emulator-5554" in dump_cmd
        assert isinstance(tree, AccessibilityTree)

    def test_get_tree_error_raises_runtime_error(self) -> None:
        """adb failure raises RuntimeError."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error: device not found"

        with patch(
            "aiyes.adapters.android_tree_adapter.subprocess.run",
            return_value=mock_result,
        ):
            with pytest.raises(RuntimeError, match="adb uiautomator dump failed"):
                adapter.get_tree(session)

    def test_get_tree_no_serial_raises(self) -> None:
        """Session without device_serial raises RuntimeError."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session(device_serial=None)

        with pytest.raises(RuntimeError, match="no device_serial"):
            adapter.get_tree(session)

    def test_get_tree_stores_registry(self) -> None:
        """After get_tree, last_registry is populated."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Pipe attempt fails
        pipe_result = MagicMock(returncode=1, stdout=b"", stderr=b"")

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
            ):
                adapter.get_tree(session)

        assert adapter.last_registry is not None

    def test_get_tree_strips_status_line(self) -> None:
        """Status lines in cat output are not an issue with file-based dump."""
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapter = AndroidUiAutomatorTreeAdapter()
        session = _make_android_session()

        # Pipe attempt fails
        pipe_result = MagicMock(returncode=1, stdout=b"", stderr=b"")

        dump_result = MagicMock(
            returncode=0,
            stdout="UI hierchary dumped to: /sdcard/window_dump.xml",
            stderr="",
        )
        # Cat returns clean XML — file-based dump doesn't append status lines
        cat_result = MagicMock(returncode=0, stdout=SAMPLE_UIAUTOMATOR_XML, stderr="")
        rm_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch("shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "aiyes.adapters.android_tree_adapter.subprocess.run",
                side_effect=[pipe_result, dump_result, cat_result, rm_result],
            ):
                tree = adapter.get_tree(session)

        assert isinstance(tree, AccessibilityTree)
        assert len(tree.roots) == 1


# ═══════════════════════════════════════════════════════════════════════
# AdbInputAdapter — key mapping and text escaping
# ═══════════════════════════════════════════════════════════════════════


class TestKeyNameToKeycode:
    """Test key name to Android keycode mapping."""

    def test_common_keys_mapped(self) -> None:
        from aiyes.adapters.android_input_adapter import KEY_NAME_TO_KEYCODE

        assert KEY_NAME_TO_KEYCODE["Return"] == 66
        assert KEY_NAME_TO_KEYCODE["Enter"] == 66
        assert KEY_NAME_TO_KEYCODE["Escape"] == 111
        assert KEY_NAME_TO_KEYCODE["Tab"] == 61
        assert KEY_NAME_TO_KEYCODE["BackSpace"] == 67
        assert KEY_NAME_TO_KEYCODE["Delete"] == 112
        assert KEY_NAME_TO_KEYCODE["Home"] == 3
        assert KEY_NAME_TO_KEYCODE["Back"] == 4

    def test_arrow_keys_mapped(self) -> None:
        from aiyes.adapters.android_input_adapter import KEY_NAME_TO_KEYCODE

        assert KEY_NAME_TO_KEYCODE["Up"] == 19
        assert KEY_NAME_TO_KEYCODE["Down"] == 20
        assert KEY_NAME_TO_KEYCODE["Left"] == 21
        assert KEY_NAME_TO_KEYCODE["Right"] == 22

    def test_letter_keys_mapped(self) -> None:
        from aiyes.adapters.android_input_adapter import KEY_NAME_TO_KEYCODE

        assert KEY_NAME_TO_KEYCODE["a"] == 29
        assert KEY_NAME_TO_KEYCODE["z"] == 54

    def test_digit_keys_mapped(self) -> None:
        from aiyes.adapters.android_input_adapter import KEY_NAME_TO_KEYCODE

        assert KEY_NAME_TO_KEYCODE["0"] == 7
        assert KEY_NAME_TO_KEYCODE["9"] == 16


class TestTextEscaping:
    """Test text escaping for adb shell input text."""

    def test_plain_text_unchanged(self) -> None:
        from aiyes.adapters.android_input_adapter import escape_text_for_adb

        assert escape_text_for_adb("hello") == "hello"

    def test_spaces_become_percent_s(self) -> None:
        from aiyes.adapters.android_input_adapter import escape_text_for_adb

        assert escape_text_for_adb("hello world") == "hello%sworld"

    def test_special_chars_escaped(self) -> None:
        from aiyes.adapters.android_input_adapter import escape_text_for_adb

        assert escape_text_for_adb("it's") == "it\\'s"
        assert escape_text_for_adb('say "hi"') == 'say%s\\"hi\\"'

    def test_shell_metacharacters_escaped(self) -> None:
        from aiyes.adapters.android_input_adapter import escape_text_for_adb

        result = escape_text_for_adb("a&b|c;d")
        assert "\\&" in result
        assert "\\|" in result
        assert "\\;" in result

    def test_empty_string(self) -> None:
        from aiyes.adapters.android_input_adapter import escape_text_for_adb

        assert escape_text_for_adb("") == ""


class TestAdbInputAdapterSubprocess:
    """Test AdbInputAdapter with mocked subprocess."""

    def test_mouse_click_calls_adb_tap(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                adapter.mouse_click(session, x=100, y=200)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "adb",
            "-s",
            "emulator-5554",
            "shell",
            "input",
            "tap",
            "100",
            "200",
        ]

    def test_mouse_move_is_noop(self) -> None:
        """mouse_move on Android is a no-op (no hover)."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        with patch("aiyes.adapters.android_input_adapter.subprocess.run") as mock_run:
            adapter.mouse_move(session, 100, 200)

        mock_run.assert_not_called()

    def test_mouse_drag_calls_adb_swipe(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_input_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            adapter.mouse_drag(session, 100, 200, 300, 400)

        cmd = mock_run.call_args[0][0]
        assert "swipe" in cmd
        assert "100" in cmd and "200" in cmd and "300" in cmd and "400" in cmd

    def test_key_sends_keyevent(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                adapter.key(session, ["Return"])

        cmd = mock_run.call_args[0][0]
        assert cmd == ["adb", "-s", "emulator-5554", "shell", "input", "keyevent", "66"]

    def test_key_raw_keycode(self) -> None:
        """Raw numeric keycodes are passed through."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_input_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            adapter.key(session, ["42"])

        cmd = mock_run.call_args[0][0]
        assert "42" in cmd

    def test_key_unknown_raises(self) -> None:
        """Unknown non-numeric key spec raises RuntimeError."""
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        with pytest.raises(RuntimeError, match="Unknown key spec"):
            adapter.key(session, ["SuperHyperKey"])

    def test_type_text_calls_adb_input_text(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_input_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                with patch("aiyes.adapters.android_input_adapter.time.sleep"):
                    adapter.type_text(session, "hello")

        # Per-character mode with default 20ms delay: 5 calls for 5 chars
        assert mock_run.call_count == 5
        # Last char sent is 'o'
        last_cmd = mock_run.call_args[0][0]
        assert last_cmd == ["adb", "-s", "emulator-5554", "shell", "input", "text", "o"]

    def test_type_text_empty_is_noop(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        with patch("aiyes.adapters.android_input_adapter.subprocess.run") as mock_run:
            adapter.type_text(session, "")

        mock_run.assert_not_called()

    def test_no_serial_raises(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session(device_serial=None)

        with pytest.raises(RuntimeError, match="no device_serial"):
            adapter.mouse_click(session, 100, 200)

    def test_mouse_scroll_calls_swipe(self) -> None:
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapter = AdbInputAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_input_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            adapter.mouse_scroll(session, "up", 2)

        cmd = mock_run.call_args[0][0]
        assert "swipe" in cmd


# ═══════════════════════════════════════════════════════════════════════
# AdbScreenshotAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestAdbScreenshotAdapter:
    """Test AdbScreenshotAdapter with mocked subprocess."""

    def test_take_calls_adb_screencap(self) -> None:
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        adapter = AdbScreenshotAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"\x89PNG fake data"
        mock_result.stderr = b""

        with patch("shutil.which", return_value="adb"):
            with patch(
                "aiyes.adapters.android_screenshot_adapter.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                import tempfile
                import os

                fd, path = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                try:
                    result_path = adapter.take(session, output_path=path)
                    assert result_path == path
                    with open(path, "rb") as f:
                        assert f.read() == b"\x89PNG fake data"
                finally:
                    os.unlink(path)

        cmd = mock_run.call_args[0][0]
        assert cmd == ["adb", "-s", "emulator-5554", "exec-out", "screencap", "-p"]

    def test_take_no_serial_raises(self) -> None:
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        adapter = AdbScreenshotAdapter()
        session = _make_android_session(device_serial=None)

        with pytest.raises(RuntimeError, match="no device_serial"):
            adapter.take(session)

    def test_take_error_raises(self) -> None:
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        adapter = AdbScreenshotAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = b""
        mock_result.stderr = b"error: no devices"

        with patch(
            "aiyes.adapters.android_screenshot_adapter.subprocess.run",
            return_value=mock_result,
        ):
            with pytest.raises(RuntimeError, match="adb screencap failed"):
                adapter.take(session)

    def test_take_generates_temp_path_when_none(self) -> None:
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        adapter = AdbScreenshotAdapter()
        session = _make_android_session()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"\x89PNG fake data"
        mock_result.stderr = b""

        with patch(
            "aiyes.adapters.android_screenshot_adapter.subprocess.run",
            return_value=mock_result,
        ):
            import os

            result_path = adapter.take(session, output_path=None)
            try:
                assert result_path.endswith(".png")
                assert os.path.exists(result_path)
            finally:
                os.unlink(result_path)


# ═══════════════════════════════════════════════════════════════════════
# AndroidActionAdapter
# ═══════════════════════════════════════════════════════════════════════


class TestAndroidActionAdapter:
    """Test AndroidActionAdapter with mocked subprocess and tree."""

    def test_click_action_taps_center(self) -> None:
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        # Create a mock tree adapter that returns a tree with known nodes
        mock_tree = MagicMock()
        button = Node(
            id="n_001",
            role="Button",
            name="Login",
            bounds=(100, 300, 400, 80),
            states=("enabled",),
            actions=("click",),
        )
        mock_tree.get_tree.return_value = AccessibilityTree(roots=(button,))
        adapter.set_tree_adapter(mock_tree)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_action_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            result = adapter.do_action(session, "n_001", "click")

        assert result.success is True
        assert isinstance(result, ActionPortResult)
        cmd = mock_run.call_args[0][0]
        # Center of (x=100, y=300, w=400, h=80) is (300, 340)
        assert "300" in cmd
        assert "340" in cmd

    def test_unknown_action_returns_failure(self) -> None:
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        mock_tree = MagicMock()
        node = Node(
            id="n_001",
            role="Button",
            name="Login",
            bounds=(100, 300, 500, 380),
            states=("enabled",),
            actions=("click",),
        )
        mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
        adapter.set_tree_adapter(mock_tree)

        result = adapter.do_action(session, "n_001", "expand")
        assert result.success is False
        assert "click" in result.available_actions

    def test_set_text_action_types_text(self) -> None:
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        mock_tree = MagicMock()
        node = Node(
            id="n_001",
            role="EditText",
            name="Username",
            bounds=(100, 200, 500, 280),
            states=("enabled", "focusable"),
            actions=("click", "set_text"),
        )
        mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
        adapter.set_tree_adapter(mock_tree)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_action_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            result = adapter.do_action(session, "n_001", "set_text", value="hello")

        assert result.success is True
        # Should have been called at least twice: tap to focus + input text
        assert mock_run.call_count >= 2

    def test_no_serial_raises(self) -> None:
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session(device_serial=None)

        with pytest.raises(RuntimeError, match="no device_serial"):
            adapter.do_action(session, "n_001", "click")

    def test_click_without_tree_adapter_returns_failure(self) -> None:
        """Without a tree adapter, node lookup fails gracefully."""
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        result = adapter.do_action(session, "n_001", "click")
        assert result.success is False

    def test_bounds_center_calculation(self) -> None:
        from aiyes.adapters.android_action_adapter import _bounds_center

        # (x, y, w, h) format — center = (x + w//2, y + h//2)
        assert _bounds_center((100, 200, 200, 200)) == (200, 300)
        assert _bounds_center((0, 0, 1080, 1920)) == (540, 960)
        assert _bounds_center((0, 0, 0, 0)) == (0, 0)


# ═══════════════════════════════════════════════════════════════════════
# Composition root wiring — real adapters
# ═══════════════════════════════════════════════════════════════════════


class TestCompositionRootRealAdapters:
    """Verify composition root now uses real Android adapters."""

    def test_android_tree_adapter_is_real(self) -> None:
        from aiyes.cli.composition_root import get_adapters_for_backend
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        adapters = get_adapters_for_backend("android")
        assert isinstance(adapters["tree"], AndroidUiAutomatorTreeAdapter)

    def test_android_action_adapter_is_real(self) -> None:
        from aiyes.cli.composition_root import get_adapters_for_backend
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapters = get_adapters_for_backend("android")
        assert isinstance(adapters["action"], AndroidActionAdapter)

    def test_android_input_adapter_is_real(self) -> None:
        from aiyes.cli.composition_root import get_adapters_for_backend
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        adapters = get_adapters_for_backend("android")
        assert isinstance(adapters["input"], AdbInputAdapter)

    def test_android_screenshot_adapter_is_real(self) -> None:
        from aiyes.cli.composition_root import get_adapters_for_backend
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        adapters = get_adapters_for_backend("android")
        assert isinstance(adapters["screenshot"], AdbScreenshotAdapter)

    def test_dispatching_tree_uses_real_android_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_tree
        from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter

        assert isinstance(
            _dispatching_tree._android.tree, AndroidUiAutomatorTreeAdapter
        )

    def test_dispatching_input_uses_real_android_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_input
        from aiyes.adapters.android_input_adapter import AdbInputAdapter

        assert isinstance(_dispatching_input._android.input, AdbInputAdapter)

    def test_dispatching_screenshot_uses_real_android_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_screenshot
        from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter

        assert isinstance(
            _dispatching_screenshot._android.screenshot, AdbScreenshotAdapter
        )

    def test_dispatching_action_uses_real_android_adapter(self) -> None:
        from aiyes.cli.composition_root import _dispatching_action
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        assert isinstance(_dispatching_action._android.action, AndroidActionAdapter)


class TestAndroidActionSetTextSafety:
    """A10-01: set_text must fail when target node cannot be resolved."""

    def test_set_text_fails_without_node_resolution(self) -> None:
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        # No tree adapter set → target_node will be None
        session = type(
            "S", (), {"device_serial": "emulator-5554", "backend": "android"}
        )()

        result = adapter.do_action(session, "nonexistent", "set_text", value="hello")
        assert result.success is False

    def test_set_text_succeeds_with_resolved_node(self) -> None:
        from unittest.mock import patch, MagicMock
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter
        from aiyes.domain.tree import Node, AccessibilityTree

        adapter = AndroidActionAdapter()

        # Create a mock tree adapter that returns a tree with one node
        mock_tree = MagicMock()
        node = Node(
            id="n_001",
            role="EditText",
            name="Username",
            bounds=(100, 100, 200, 50),
            states=("enabled",),
            actions=("set_text",),
        )
        mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
        adapter.set_tree_adapter(mock_tree)

        session = type(
            "S", (), {"device_serial": "emulator-5554", "backend": "android"}
        )()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_001", "set_text", value="test")
            assert result.success is True


class TestAndroidActionRegistryResolution:
    """A10-02: registry parameter must be used for stable node resolution."""

    def test_registry_fallback_resolves_node(self) -> None:
        from unittest.mock import patch, MagicMock
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter
        from aiyes.domain.tree import Node, AccessibilityTree
        from aiyes.domain.node_id import NodeIdRegistry

        adapter = AndroidActionAdapter()

        # Node with a different ID than what we'll look up
        node = Node(
            id="live_id",
            role="Button",
            name="Login",
            bounds=(100, 200, 300, 250),
            states=("enabled",),
            actions=("click",),
        )
        mock_tree = MagicMock()
        mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
        adapter.set_tree_adapter(mock_tree)

        # Registry maps old_id → (Button, Login, path)
        registry = NodeIdRegistry()
        registry.get_or_assign("Button", "Login", [0])
        old_id = "n_001"  # This is what registry assigns

        session = type(
            "S", (), {"device_serial": "emulator-5554", "backend": "android"}
        )()

        with patch("aiyes.adapters.android_action_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            # Look up by registry ID — node has different live ID but same role/name
            result = adapter.do_action(
                session,
                old_id,
                "click",
                registry=registry,
            )
            assert result.success is True
