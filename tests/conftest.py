"""Shared test fixtures — fakes, spies, and domain factories.

All test doubles implement the expected port Protocols via structural typing.
"""

from __future__ import annotations

import inspect
import shutil
from typing import Any, Dict, List, Optional, Tuple

import pytest
from click.testing import CliRunner


# ──────────────────────────────────────────────────────────────────────
# Markers
# ──────────────────────────────────────────────────────────────────────

_adb_available = shutil.which("adb") is not None

requires_adb = pytest.mark.skipif(
    not _adb_available,
    reason="adb not available",
)


def make_cli_runner(**kwargs: Any) -> CliRunner:
    """Create CliRunner with mix_stderr=False if supported (Click <8.2)."""
    if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
        kwargs.setdefault("mix_stderr", False)
    return CliRunner(**kwargs)


from aiyes.domain.tree import AccessibilityTree, raw_tree_to_domain
from aiyes.domain.types import (
    ActionPortResult,
    BusStartResult,
    DependencyResult,
    StoredTree,
)


# ──────────────────────────────────────────────────────────────────────
# Domain value object factories (define expected shapes)
# ──────────────────────────────────────────────────────────────────────


def make_session(
    session_id: str = "test-session-001",
    display: str = ":99",
    app_pid: int = 12345,
    app_command: str = "gedit",
    app_args: Optional[List[str]] = None,
    atspi_bus_pid: int = 12346,
    atspi_bus_address: str = "unix:abstract=/tmp/dbus-test",
    xvfb_pid: int = 12344,
    name: Optional[str] = None,
    resolution: str = "1280x800",
    color_depth: int = 24,
) -> Dict[str, Any]:
    """Factory for session-like dicts matching Session value object shape."""
    return {
        "session_id": session_id,
        "display": display,
        "app_pid": app_pid,
        "app_command": app_command,
        "app_args": app_args or [],
        "atspi_bus_pid": atspi_bus_pid,
        "atspi_bus_address": atspi_bus_address,
        "xvfb_pid": xvfb_pid,
        "name": name,
        "resolution": resolution,
        "color_depth": color_depth,
    }


def make_node(
    node_id: str = "n_001",
    role: str = "push_button",
    name: str = "OK",
    bounds: Optional[List[int]] = None,
    states: Optional[List[str]] = None,
    actions: Optional[List[str]] = None,
    children: Optional[List[Dict[str, Any]]] = None,
    value: Optional[str] = None,
) -> Dict[str, Any]:
    """Factory for node-like dicts matching Node value object shape."""
    result: Dict[str, Any] = {
        "id": node_id,
        "role": role,
        "name": name,
        "bounds": bounds if bounds is not None else [100, 200, 80, 30],
        "states": states if states is not None else ["enabled", "visible"],
        "actions": actions if actions is not None else ["click"],
    }
    if children is not None:
        result["children"] = children
    if value is not None:
        result["value"] = value
    return result


def make_tree(nodes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Factory for a minimal accessibility tree structure (raw dict format).

    This returns the raw dict format. Use make_domain_tree() for the domain type.
    """
    if nodes is None:
        nodes = [
            make_node(
                "n_001",
                "frame",
                "Test Window",
                children=[
                    make_node("n_002", "push_button", "OK"),
                    make_node("n_003", "push_button", "Cancel"),
                ],
            ),
        ]
    return {"tree": nodes}


def make_domain_tree(
    nodes: Optional[List[Dict[str, Any]]] = None,
) -> AccessibilityTree:
    """Factory that returns a domain AccessibilityTree from raw node dicts.

    This is the adapter-side conversion: raw dict -> domain type.
    """
    return raw_tree_to_domain(make_tree(nodes))


# ──────────────────────────────────────────────────────────────────────
# Fake / Spy port implementations (structural typing — no base class)
# ──────────────────────────────────────────────────────────────────────


class FakeSessionRepository:
    """Fake for SessionRepositoryPort.

    Stores sessions in memory. Records calls for spy assertions.
    """

    def __init__(self, fail_on_save: bool = False) -> None:
        self._sessions: Dict[str, Any] = {}
        self.calls: List[Tuple[str, Any]] = []
        self._fail_on_save = fail_on_save

    def save(self, session: Any) -> None:
        self.calls.append(("save", session))
        if self._fail_on_save:
            raise RuntimeError("disk full")
        self._sessions[session.session_id] = session

    def load(self, session_id: str) -> Optional[Any]:
        self.calls.append(("load", session_id))
        return self._sessions.get(session_id)

    def load_all(self) -> List[Any]:
        self.calls.append(("load_all", None))
        return list(self._sessions.values())

    def delete(self, session_id: str) -> None:
        self.calls.append(("delete", session_id))
        self._sessions.pop(session_id, None)


class FakeTreeStore:
    """Fake for TreeStorePort. Stores/returns StoredTree domain objects."""

    def __init__(self) -> None:
        self._trees: Dict[str, StoredTree] = {}
        self.calls: List[Tuple[str, Any]] = []

    def save_tree(
        self, session_id: str, tree: object, node_id_registry: object
    ) -> None:
        self.calls.append(("save_tree", (session_id, tree, node_id_registry)))
        self._trees[session_id] = StoredTree(tree=tree, registry=node_id_registry)

    def load_tree(self, session_id: str) -> Optional[StoredTree]:
        self.calls.append(("load_tree", session_id))
        return self._trees.get(session_id)


class FakeScreenshotStore:
    """Fake for ScreenshotStorePort."""

    def __init__(self, fake_bytes: bytes = b"fake-png-data") -> None:
        self._paths: Dict[str, str] = {}
        self._fake_bytes = fake_bytes
        self.calls: List[Tuple[str, Any]] = []

    def save_screenshot(self, session_id: str, source_path: str) -> str:
        final = f"/home/test/.aieyes/{session_id}/screenshot.png"
        self.calls.append(("save_screenshot", (session_id, source_path)))
        self._paths[session_id] = final
        return final

    def get_screenshot_path(self, session_id: str) -> str:
        self.calls.append(("get_screenshot_path", session_id))
        return self._paths.get(
            session_id, f"/home/test/.aieyes/{session_id}/screenshot.png"
        )

    def read_screenshot_bytes(self, session_id: str) -> bytes:
        self.calls.append(("read_screenshot_bytes", session_id))
        return self._fake_bytes

    def delete_temp(self, path: str) -> None:
        self.calls.append(("delete_temp", path))


class FakeDisplayServer:
    """Fake for DisplayServerPort."""

    def __init__(
        self, display: str = ":99", pid: int = 9999, fail_resize: bool = False
    ) -> None:
        self._display = display
        self._pid = pid
        self._fail_resize = fail_resize
        self.calls: List[Tuple[str, Any]] = []
        self.stopped: bool = False

    def start(self, display_num: int, resolution: str, color_depth: int) -> int:
        self.calls.append(("start", (display_num, resolution, color_depth)))
        return self._pid

    def stop(self, pid: int) -> None:
        self.calls.append(("stop", pid))
        self.stopped = True

    def resize(self, display: str, resolution: str) -> None:
        self.calls.append(("resize", (display, resolution)))
        if self._fail_resize:
            raise RuntimeError("xrandr resize failed")

    def configure_keyboard(self, display: str) -> None:
        self.calls.append(("configure_keyboard", display))


class FakeDisplayAllocator:
    """Fake for DisplayAllocatorPort."""

    def __init__(self, display_num: int = 99) -> None:
        self._display_num = display_num
        self.calls: List[Tuple[str, Any]] = []

    def allocate(self) -> int:
        self.calls.append(("allocate", None))
        return self._display_num


class FakeAccessibilityBus:
    """Fake for AccessibilityBusPort."""

    def __init__(
        self, pid: int = 8888, bus_address: str = "unix:abstract=/tmp/dbus-test"
    ) -> None:
        self._pid = pid
        self._bus_address = bus_address
        self.calls: List[Tuple[str, Any]] = []
        self.stopped: bool = False

    def start_bus(self, display: str) -> BusStartResult:
        """Returns a BusStartResult domain type."""
        self.calls.append(("start_bus", display))
        return BusStartResult(pid=self._pid, bus_address=self._bus_address)

    def stop_bus(self, pid: int) -> None:
        self.calls.append(("stop_bus", pid))
        self.stopped = True


class FakeAccessibilityTree:
    """Fake for AccessibilityTreePort.

    Accepts either a raw dict tree (and converts to domain type) or
    a domain AccessibilityTree directly.
    """

    def __init__(self, tree: Optional[Any] = None) -> None:
        if isinstance(tree, AccessibilityTree):
            self._tree = tree
        elif tree is not None:
            # Raw dict format — convert to domain type (adapter concern)
            self._tree = raw_tree_to_domain(tree)
        else:
            self._tree = AccessibilityTree(roots=())
        self.calls: List[Tuple[str, Any]] = []

    def get_tree(self, session) -> AccessibilityTree:
        self.calls.append(("get_tree", session))
        return self._tree


class FakeAccessibilityAction:
    """Fake for AccessibilityActionPort."""

    def __init__(
        self, success: bool = True, available_actions: Optional[List[str]] = None
    ) -> None:
        self._success = success
        self._available_actions = available_actions or []
        self.calls: List[Tuple[str, Any]] = []

    def do_action(
        self,
        session,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        self.calls.append(
            ("do_action", (session, node_id, action_name, value, registry))
        )
        return ActionPortResult(
            success=self._success, available_actions=self._available_actions
        )


class FakeScreenshot:
    """Fake for ScreenshotPort."""

    def __init__(self, path: str = "/tmp/test_screenshot.png") -> None:
        self._path = path
        self.calls: List[Tuple[str, Any]] = []

    def take(self, session, output_path: Optional[str] = None) -> str:
        self.calls.append(("take", (session, output_path)))
        return output_path or self._path


class FakeInput:
    """Fake for InputPort."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Any]] = []

    def mouse_move(self, session, x: int, y: int) -> None:
        self.calls.append(("mouse_move", (session, x, y)))

    def mouse_click(
        self,
        session,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
    ) -> None:
        self.calls.append(("mouse_click", (session, x, y, button)))

    def mouse_drag(self, session, x1: int, y1: int, x2: int, y2: int) -> None:
        self.calls.append(("mouse_drag", (session, x1, y1, x2, y2)))

    def mouse_scroll(self, session, direction: str, amount: int = 3) -> None:
        self.calls.append(("mouse_scroll", (session, direction, amount)))

    def key(self, session, key_specs: List[str]) -> None:
        self.calls.append(("key", (session, key_specs)))

    def type_text(self, session, text: str, delay_ms: int = 0) -> None:
        self.calls.append(("type_text", (session, text)))


class FakeProcess:
    """Fake for ProcessPort."""

    def __init__(self, pid: int = 54321) -> None:
        self._pid = pid
        self.calls: List[Tuple[str, Any]] = []
        self._running: Dict[int, bool] = {}

    def start(
        self, command: str, args: List[str], env: Optional[Dict[str, str]] = None
    ) -> int:
        self.calls.append(("start", (command, args, env)))
        self._running[self._pid] = True
        return self._pid

    def stop(self, pid: int) -> None:
        self.calls.append(("stop", pid))
        self._running[pid] = False

    def is_running(self, pid: int) -> bool:
        self.calls.append(("is_running", pid))
        return self._running.get(pid, False)


class FakeClock:
    """Fake for ClockPort."""

    def __init__(self, now_value: float = 1000.0) -> None:
        self._now = now_value
        self._sleep_calls: List[float] = []
        self.calls: List[Tuple[str, Any]] = []

    def sleep(self, seconds: float) -> None:
        self.calls.append(("sleep", seconds))
        self._sleep_calls.append(seconds)
        self._now += seconds

    def now(self) -> float:
        self.calls.append(("now", None))
        return self._now

    @property
    def sleep_calls(self) -> List[float]:
        return self._sleep_calls


class FakeDependencyCheck:
    """Fake for DependencyCheckPort. Returns DependencyResult domain objects."""

    def __init__(self, results: Optional[Dict[str, DependencyResult]] = None) -> None:
        self._results = results or {}
        self.calls: List[Tuple[str, Any]] = []

    def check(self, name: str) -> DependencyResult:
        self.calls.append(("check", name))
        default = DependencyResult(
            name=name, status="pass", message=f"{name} is available"
        )
        return self._results.get(name, default)

    def check_all(self) -> List[DependencyResult]:
        self.calls.append(("check_all", None))
        names = [
            "xvfb",
            "screenshot_tool",
            "xdotool",
            "xclip",
            "at-spi2-core",
            "python3-gi",
            "gir1.2-atspi-2.0",
            "mesa-software-rendering",
            "mesa-vulkan-software",
            "adb",
            "android_device",
            "imagemagick",
        ]
        return [self.check(name) for name in names]


# ──────────────────────────────────────────────────────────────────────
# Pytest fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_session_repo() -> FakeSessionRepository:
    return FakeSessionRepository()


@pytest.fixture
def fake_tree_store() -> FakeTreeStore:
    return FakeTreeStore()


@pytest.fixture
def fake_screenshot_store() -> FakeScreenshotStore:
    return FakeScreenshotStore()


@pytest.fixture
def fake_display_server() -> FakeDisplayServer:
    return FakeDisplayServer()


@pytest.fixture
def fake_display_allocator() -> FakeDisplayAllocator:
    return FakeDisplayAllocator()


@pytest.fixture
def fake_accessibility_bus() -> FakeAccessibilityBus:
    return FakeAccessibilityBus()


@pytest.fixture
def fake_accessibility_tree() -> FakeAccessibilityTree:
    return FakeAccessibilityTree(tree=make_tree())


@pytest.fixture
def fake_accessibility_action() -> FakeAccessibilityAction:
    return FakeAccessibilityAction()


@pytest.fixture
def fake_screenshot() -> FakeScreenshot:
    return FakeScreenshot()


@pytest.fixture
def fake_input() -> FakeInput:
    return FakeInput()


@pytest.fixture
def fake_process() -> FakeProcess:
    return FakeProcess()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_dependency_check() -> FakeDependencyCheck:
    return FakeDependencyCheck()
