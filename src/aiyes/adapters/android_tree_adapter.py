"""AndroidUiAutomatorTreeAdapter — implements AccessibilityTreePort via adb+uiautomator.

Runs `adb exec-out uiautomator dump /dev/stdout` (fast pipe path) to get the
view hierarchy XML, falling back to the 3-step file-based approach (dump/cat/rm)
if the pipe fails.

Parses the XML into domain AccessibilityTree/Node objects.

Uses only stdlib: subprocess, xml.etree.ElementTree.
"""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from aiyes.domain.node_id import NodeIdRegistry
from aiyes.domain.tree import AccessibilityTree, Node

# AIYES-37 Item 4: Size ceiling to prevent unbounded memory allocation
# from malformed/malicious adb output. 10 MB is well above any reasonable
# UI hierarchy XML but catches runaway output.
MAX_XML_BYTES = 10 * 1024 * 1024  # 10 MB


def _parse_bounds(bounds_str: str) -> Tuple[int, int, int, int]:
    """Parse Android bounds string '[x1,y1][x2,y2]' into (x, y, width, height).

    Android UIAutomator reports bounds as absolute coordinates (x1, y1, x2, y2).
    This function converts to (x, y, width, height) to match the domain model
    convention used by AT-SPI and the crop port.

    Returns (0, 0, 0, 0) if the format is invalid.
    """
    try:
        # Format: [x1,y1][x2,y2]
        parts = bounds_str.replace("][", ",").strip("[]")
        values = parts.split(",")
        if len(values) == 4:
            x1, y1, x2, y2 = (
                int(values[0]),
                int(values[1]),
                int(values[2]),
                int(values[3]),
            )
            return (x1, y1, x2 - x1, y2 - y1)
    except (ValueError, IndexError, AttributeError):
        pass
    return (0, 0, 0, 0)


def _extract_states(element: ET.Element) -> Tuple[str, ...]:
    """Extract accessibility states from Android node attributes."""
    states: List[str] = []
    if element.get("enabled", "false") == "true":
        states.append("enabled")
    if element.get("focusable", "false") == "true":
        states.append("focusable")
    if element.get("focused", "false") == "true":
        states.append("focused")
    if element.get("selected", "false") == "true":
        states.append("selected")
    if element.get("checked", "false") == "true":
        states.append("checked")
    return tuple(states)


def _extract_actions(element: ET.Element) -> Tuple[str, ...]:
    """Extract available actions from Android node attributes."""
    actions: List[str] = []
    if element.get("clickable", "false") == "true":
        actions.append("click")
    if element.get("long-clickable", "false") == "true":
        actions.append("long_click")
    if element.get("scrollable", "false") == "true":
        actions.append("scroll")
    # Detect editable text fields:
    #   (a) class contains "EditText" (works all API levels)
    #   (b) editable="true" attribute (API 21+, not always present)
    class_name = element.get("class", "")
    if "EditText" in class_name or element.get("editable", "false") == "true":
        actions.append("set_text")
    return tuple(actions)


def _extract_name(element: ET.Element) -> str:
    """Extract the accessible name from Android node attributes.

    Prefers text, falls back to content-desc.
    """
    text = element.get("text", "")
    if text:
        return text
    return element.get("content-desc", "")


def _extract_stable_name(element: ET.Element) -> str:
    content_desc = element.get("content-desc", "")
    if content_desc:
        return content_desc
    return element.get("text", "")


def _extract_role(element: ET.Element) -> str:
    """Extract the role from Android class attribute.

    Strips the android.widget. / android.view. prefix for brevity.
    """
    class_name = element.get("class", "")
    for prefix in ("android.widget.", "android.view.", "android.webkit."):
        if class_name.startswith(prefix):
            return class_name[len(prefix) :]
    return class_name


def _parse_element(
    element: ET.Element,
    registry: NodeIdRegistry,
    path: List[int],
) -> Optional[Node]:
    """Recursively parse an XML element into a domain Node."""
    role = _extract_role(element)
    name = _extract_name(element)
    bounds = _parse_bounds(element.get("bounds", ""))
    states = _extract_states(element)
    actions = _extract_actions(element)

    node_id = registry.get_or_assign(role, name, path)
    stable_id = _stable_android_id(element, bounds, path)

    children: List[Node] = []
    for idx, child in enumerate(element):
        child_path = path + [idx]
        child_node = _parse_element(child, registry, child_path)
        if child_node is not None:
            children.append(child_node)

    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=bounds,
        states=states,
        actions=actions,
        children=tuple(children),
        stable_id=stable_id,
    )


def _stable_android_id(
    element: ET.Element,
    bounds: Tuple[int, int, int, int],
    path: List[int],
) -> str:
    role = element.get("class", "") or _extract_role(element)
    resource_id = element.get("resource-id", "")
    name = _extract_stable_name(element)
    bounds_text = ",".join(str(part) for part in bounds)
    path_text = ".".join(str(part) for part in path)
    return (
        f"android:rid={resource_id};"
        f"class={role};"
        f"name={name};"
        f"bounds={bounds_text};"
        f"path={path_text}"
    )


def _strip_xml_trailer(xml_text: str) -> str:
    """Strip content after the closing </hierarchy> tag.

    Flutter (and some Android builds) append extra text after </hierarchy>
    (e.g. "UI hierrchy dumped to: /dev/tty") which causes ET.fromstring()
    to fail with "junk after document element".  Truncate at the end of
    the closing tag.  If </hierarchy> is absent, return unchanged and let
    ET.fromstring() surface the real parse error.
    """
    marker = "</hierarchy>"
    idx = xml_text.find(marker)
    if idx != -1:
        return xml_text[: idx + len(marker)]
    return xml_text


def parse_uiautomator_xml(xml_text: str) -> Tuple[AccessibilityTree, NodeIdRegistry]:
    """Parse uiautomator dump XML into domain AccessibilityTree + NodeIdRegistry.

    This function is the core parsing logic, separated for testability.
    """
    registry = NodeIdRegistry()

    # Strip trailing junk after </hierarchy> (AIYES-39A: Flutter compat)
    xml_text = _strip_xml_trailer(xml_text)

    # uiautomator dump wraps everything in a <hierarchy> root element
    root = ET.fromstring(xml_text)

    nodes: List[Node] = []
    for idx, child in enumerate(root):
        node = _parse_element(child, registry, [idx])
        if node is not None:
            nodes.append(node)

    return AccessibilityTree(roots=tuple(nodes)), registry


class AndroidUiAutomatorTreeAdapter:
    """Queries the Android view hierarchy via adb+uiautomator and converts to domain model."""

    def __init__(self) -> None:
        self._registry: Optional[NodeIdRegistry] = None

    @property
    def last_registry(self) -> Optional[NodeIdRegistry]:
        """Return the NodeIdRegistry from the most recent get_tree call."""
        return self._registry

    def get_tree(self, session) -> AccessibilityTree:
        """Get the accessibility tree for the given session via uiautomator dump.

        Tries the fast pipe-based approach first (single adb exec-out call).
        Falls back to the 3-step file-based approach on any failure.
        """
        from aiyes.adapters.adb_path import resolve_adb_path

        serial = session.device_serial
        if not serial:
            raise RuntimeError(
                "Android session has no device_serial — cannot query tree"
            )

        adb = resolve_adb_path()

        # Try pipe-based approach first (fast path)
        try:
            xml_text = self._get_tree_pipe(adb, serial)
        except Exception:
            # Fall back to 3-step file-based approach on any failure
            # (RuntimeError from adb, AttributeError from unexpected output, etc.)
            xml_text = self._get_tree_file(adb, serial)

        try:
            tree, registry = parse_uiautomator_xml(xml_text)
        except ET.ParseError as exc:
            raise RuntimeError(f"Failed to parse uiautomator XML: {exc}")

        self._registry = registry
        return tree

    def _get_tree_pipe(self, adb: str, serial: str) -> str:
        """Fast path: single adb exec-out pipe.

        Uses `adb exec-out uiautomator dump /dev/stdout` to retrieve the XML
        in a single subprocess call without creating a file on the device.

        Raises RuntimeError on any failure (non-zero exit, no XML found,
        timeout, adb not found).
        """
        cmd = [adb, "-s", serial, "exec-out", "uiautomator", "dump", "/dev/stdout"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
        except FileNotFoundError:
            raise RuntimeError("adb not found. Install Android SDK platform-tools.")
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"adb exec-out uiautomator dump timed out for device {serial}"
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"adb exec-out pipe dump failed (rc={result.returncode})"
            )

        if len(result.stdout) > MAX_XML_BYTES:
            raise RuntimeError(
                f"adb uiautomator pipe output ({len(result.stdout)} bytes) "
                f"exceeds size ceiling ({MAX_XML_BYTES} bytes)"
            )

        text = result.stdout.decode("utf-8", errors="replace").strip()
        if not text:
            raise RuntimeError("Pipe dump returned empty output")

        # uiautomator dump /dev/stdout may prefix a preamble line like
        # "UI hierrchy dumped to: /dev/stdout" (note: real typo in Android).
        # Strip preamble by finding the XML start marker.
        if text.startswith("<?xml") or text.startswith("<hierarchy"):
            return text

        lines = text.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("<?xml") or stripped.startswith("<hierarchy"):
                return "\n".join(lines[i:])

        raise RuntimeError("No XML found in pipe output")

    def _get_tree_file(self, adb: str, serial: str) -> str:
        """Fallback: 3-step file-based dump.

        1. adb shell uiautomator dump /sdcard/window_dump.xml
        2. adb shell cat /sdcard/window_dump.xml
        3. adb shell rm /sdcard/window_dump.xml (cleanup, always runs)

        Returns the XML text. Raises RuntimeError on failure.
        """
        dump_path = "/sdcard/window_dump.xml"

        # Step 1: dump to file on device
        dump_cmd = [adb, "-s", serial, "shell", "uiautomator", "dump", dump_path]
        try:
            dump_result = subprocess.run(
                dump_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            raise RuntimeError("adb not found. Install Android SDK platform-tools.")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"adb uiautomator dump timed out for device {serial}")

        if dump_result.returncode != 0:
            raise RuntimeError(
                f"adb uiautomator dump failed (rc={dump_result.returncode}): "
                f"{dump_result.stderr.strip()}"
            )

        # Step 2: read file content via cat; Step 3: cleanup via rm (always)
        try:
            cat_cmd = [adb, "-s", serial, "shell", "cat", dump_path]
            try:
                cat_result = subprocess.run(
                    cat_cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except FileNotFoundError:
                raise RuntimeError("adb not found. Install Android SDK platform-tools.")
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"adb cat dump file timed out for device {serial}")

            if cat_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to read dump file on device "
                    f"(rc={cat_result.returncode}): "
                    f"{cat_result.stderr.strip()}"
                )

            xml_text = cat_result.stdout.strip()

            xml_byte_len = len(xml_text.encode("utf-8"))
            if xml_byte_len > MAX_XML_BYTES:
                raise RuntimeError(
                    f"adb uiautomator file output ({xml_byte_len} bytes) "
                    f"exceeds size ceiling ({MAX_XML_BYTES} bytes)"
                )

            if not xml_text:
                raise RuntimeError(
                    f"adb uiautomator dump returned empty output for device {serial}"
                )

            return xml_text
        finally:
            # Step 3: cleanup — always runs even if parsing fails
            rm_cmd = [adb, "-s", serial, "shell", "rm", dump_path]
            try:
                subprocess.run(
                    rm_cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass  # Best-effort cleanup
