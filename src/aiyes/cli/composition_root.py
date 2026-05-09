"""Composition root — creates all adapters and wires use cases.

This is the sole production module that imports from aiyes.adapters.
It re-exports presenter functions so that main.py imports only from here.
It does NOT import from aiyes.cli.main, and does NOT import click.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from aiyes.domain.node_id import NodeIdRegistry
    from aiyes.domain.session import Session
    from aiyes.domain.tree import AccessibilityTree
    from aiyes.domain.types import ActionPortResult

from aiyes import __version__ as __version__  # noqa: F401

from aiyes.cli.presenter import (  # noqa: F401
    format_action,
    format_clipboard_read,
    format_clipboard_write,
    format_debug_bundle,
    format_detect_dialog,
    format_diff,
    format_do,
    format_doctor,
    format_find,
    format_find_nodes,
    format_gesture_result,
    format_inspect,
    format_inspect_result,
    format_mcp_manifest,
    format_menu_result,
    format_metrics,
    format_navigate_result,
    format_prune,
    format_screenshot,
    format_session_capabilities,
    format_session_list,
    format_session_resize,
    format_session_start,
    format_session_status,
    format_session_stop,
    format_scenario_run,
    format_scenario_preflight,
    format_scenario_fixtures,
    format_scenario_validation_errors,
    format_status_ok,
    format_system_error,
    format_wait,
    format_reactive_wait,
    format_wait_stable,
    mask_node_dict,
)

from aiyes.adapters.xvfb_adapter import XvfbAdapter
from aiyes.adapters.display_allocator_adapter import XDisplayAllocatorAdapter
from aiyes.adapters.atspi_bus_adapter import AtSpiBusAdapter
from aiyes.adapters.atspi_tree_adapter import AtSpi2TreeAdapter
from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter
from aiyes.adapters.scrot_adapter import ScrotAdapter
from aiyes.adapters.xdotool_adapter import XdotoolAdapter
from aiyes.adapters.subprocess_adapter import SubprocessAdapter
from aiyes.adapters.file_session_repository import FileSessionRepository
from aiyes.adapters.file_tree_store import FileTreeStore
from aiyes.adapters.file_screenshot_store import FileScreenshotStore
from aiyes.adapters.system_clock import SystemClock
from aiyes.adapters.system_dependency_check import SystemDependencyCheck
from aiyes.adapters.android_action_adapter import AndroidActionAdapter
from aiyes.adapters.adb_app_lifecycle_adapter import AdbAppLifecycleAdapter
from aiyes.adapters.adb_activity_adapter import AdbActivityQueryAdapter
from aiyes.adapters.android_capability_probe_adapter import AndroidCapabilityProbeAdapter
from aiyes.adapters.android_input_adapter import AdbInputAdapter
from aiyes.adapters.android_screenshot_adapter import AdbScreenshotAdapter
from aiyes.adapters.android_tree_adapter import AndroidUiAutomatorTreeAdapter
from aiyes.adapters.android_reactive_wait_adapter import AndroidReactiveWaitObserver
from aiyes.adapters.linux_reactive_wait_adapter import (
    AtSpiEventWorkerClient,
    LinuxReactiveWaitObserver,
)
from aiyes.adapters.file_operation_log import FileOperationLog
from aiyes.adapters.file_session_cleanup import FileSessionCleanup
from aiyes.adapters.scenario_dry_run_executor import ScenarioDryRunExecutor
from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle  # noqa: F401
from aiyes.adapters.scenario_loader import load_scenario_file  # noqa: F401
from aiyes.adapters.scenario_prerequisites import SystemScenarioPrerequisiteChecker
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.adapters.scenario_fixtures import list_public_scenario_fixtures  # noqa: F401

from aiyes.domain.use_cases.session_start import SessionStartUseCase
from aiyes.domain.use_cases.session_stop import SessionStopUseCase
from aiyes.domain.use_cases.session_list import SessionListUseCase
from aiyes.domain.use_cases.inspect import InspectUseCase
from aiyes.domain.use_cases.find import FindUseCase
from aiyes.domain.use_cases.action import ActionUseCase
from aiyes.domain.use_cases.mouse import MouseUseCase
from aiyes.domain.use_cases.key import KeyUseCase
from aiyes.domain.use_cases.type_text import TypeTextUseCase
from aiyes.domain.use_cases.screenshot import ScreenshotUseCase
from aiyes.domain.use_cases.wait import WaitUseCase
from aiyes.domain.use_cases.reactive_wait import ReactiveWaitUseCase
from aiyes.domain.use_cases.compound_do import CompoundDoUseCase
from aiyes.domain.use_cases.diff import DiffUseCase
from aiyes.domain.use_cases.doctor import DoctorUseCase
from aiyes.domain.use_cases.debug_bundle import DebugBundleUseCase
from aiyes.domain.use_cases.session_resize import SessionResizeUseCase
from aiyes.domain.use_cases.session_resolve import SessionResolveUseCase
from aiyes.domain.use_cases.session_capabilities import SessionCapabilitiesUseCase
from aiyes.domain.use_cases.wait_stable import WaitStableUseCase
from aiyes.domain.use_cases.metrics import MetricsUseCase
from aiyes.domain.use_cases.prune import PruneUseCase
from aiyes.domain.use_cases.scenario_run import ScenarioRunUseCase
from aiyes.domain.use_cases.scenario_preflight import (  # noqa: F401
    ScenarioEvidencePathCheck,
    ScenarioPreflightUseCase,
    scenario_validation_preflight_result,
)
from aiyes.domain.operation_record import OperationRecord  # noqa: F401
from aiyes.adapters.imagemagick_crop_adapter import ImageMagickCropAdapter


# ─── Adapter instances (singletons) ──────────────────────────────────

_display_server = XvfbAdapter()
_allocator = XDisplayAllocatorAdapter()
_atspi_bus = AtSpiBusAdapter()
_atspi_tree = AtSpi2TreeAdapter()
_atspi_action = AtSpi2ActionAdapter()
_screenshot = ScrotAdapter()
_input = XdotoolAdapter()
_process = SubprocessAdapter()
_session_repo = FileSessionRepository()
_tree_store = FileTreeStore()
_screenshot_store = FileScreenshotStore()
_clock = SystemClock()
_dep_check = SystemDependencyCheck()
_operation_log = FileOperationLog()
_session_cleanup = FileSessionCleanup()
_crop = ImageMagickCropAdapter()
_android_lifecycle = AdbAppLifecycleAdapter()
_android_capability_probe = AndroidCapabilityProbeAdapter()
_scenario_executor = ScenarioDryRunExecutor()
_scenario_prerequisite_checker = SystemScenarioPrerequisiteChecker()


# ─── Android adapters (lazy — only instantiated when backend="android") ───


class _LazyAndroidAdapters:
    """Lazily instantiates Android adapters on first access."""

    def __init__(self) -> None:
        self._tree: Optional[AndroidUiAutomatorTreeAdapter] = None
        self._action: Optional[AndroidActionAdapter] = None
        self._input: Optional[AdbInputAdapter] = None
        self._screenshot: Optional[AdbScreenshotAdapter] = None

    def _ensure(self) -> None:
        if self._tree is None:
            self._tree = AndroidUiAutomatorTreeAdapter()
            self._action = AndroidActionAdapter()
            self._input = AdbInputAdapter()
            self._screenshot = AdbScreenshotAdapter()
            self._action.set_tree_adapter(self._tree)

    @property
    def tree(self) -> AndroidUiAutomatorTreeAdapter:
        self._ensure()
        assert self._tree is not None
        return self._tree

    @property
    def action(self) -> AndroidActionAdapter:
        self._ensure()
        assert self._action is not None
        return self._action

    @property
    def input(self) -> AdbInputAdapter:
        self._ensure()
        assert self._input is not None
        return self._input

    @property
    def screenshot(self) -> AdbScreenshotAdapter:
        self._ensure()
        assert self._screenshot is not None
        return self._screenshot


_android = _LazyAndroidAdapters()


# ─── Dispatching adapters (route by session.backend) ─────────────────


class _DispatchingTree:
    """Routes tree requests to backend-specific adapter."""

    def __init__(self, linux: AtSpi2TreeAdapter, android: _LazyAndroidAdapters) -> None:
        self._linux = linux
        self._android = android

    def get_tree(self, session: Session) -> AccessibilityTree:
        if session.backend == "android":
            return self._android.tree.get_tree(session)
        return self._linux.get_tree(session)


class _DispatchingAction:
    """Routes action requests to backend-specific adapter."""

    def __init__(
        self, linux: AtSpi2ActionAdapter, android: _LazyAndroidAdapters
    ) -> None:
        self._linux = linux
        self._android = android

    def do_action(
        self,
        session: Session,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[NodeIdRegistry] = None,
    ) -> ActionPortResult:
        if session.backend == "android":
            return self._android.action.do_action(
                session, node_id, action_name, value, registry
            )
        return self._linux.do_action(session, node_id, action_name, value, registry)


class _DispatchingInput:
    """Routes input requests to backend-specific adapter."""

    def __init__(self, linux: XdotoolAdapter, android: _LazyAndroidAdapters) -> None:
        self._linux = linux
        self._android = android

    def mouse_move(self, session: Session, x: int, y: int) -> None:
        if session.backend == "android":
            self._android.input.mouse_move(session, x, y)
        else:
            self._linux.mouse_move(session, x, y)

    def mouse_click(
        self,
        session: Session,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
    ) -> None:
        if session.backend == "android":
            self._android.input.mouse_click(session, x, y, button)
        else:
            self._linux.mouse_click(session, x, y, button)

    def mouse_drag(self, session: Session, x1: int, y1: int, x2: int, y2: int) -> None:
        if session.backend == "android":
            self._android.input.mouse_drag(session, x1, y1, x2, y2)
        else:
            self._linux.mouse_drag(session, x1, y1, x2, y2)

    def mouse_scroll(self, session: Session, direction: str, amount: int = 3) -> None:
        if session.backend == "android":
            self._android.input.mouse_scroll(session, direction, amount)
        else:
            self._linux.mouse_scroll(session, direction, amount)

    def key(self, session: Session, key_specs: List[str]) -> None:
        if session.backend == "android":
            self._android.input.key(session, key_specs)
        else:
            self._linux.key(session, key_specs)

    def type_text(self, session: Session, text: str, delay_ms: int = 0) -> None:
        if session.backend == "android":
            self._android.input.type_text(session, text, delay_ms=delay_ms)
        else:
            self._linux.type_text(session, text, delay_ms=delay_ms)


class _DispatchingScreenshot:
    """Routes screenshot requests to backend-specific adapter."""

    def __init__(self, linux: ScrotAdapter, android: _LazyAndroidAdapters) -> None:
        self._linux = linux
        self._android = android

    def take(self, session: Session, output_path: Optional[str] = None) -> str:
        if session.backend == "android":
            return self._android.screenshot.take(session, output_path)
        return self._linux.take(session, output_path)


_dispatching_tree = _DispatchingTree(_atspi_tree, _android)
_dispatching_action = _DispatchingAction(_atspi_action, _android)
_dispatching_input = _DispatchingInput(_input, _android)
_dispatching_screenshot = _DispatchingScreenshot(_screenshot, _android)


class _DispatchingReactiveObserver:
    """Routes reactive wait requests to backend-specific observers."""

    def __init__(
        self,
        linux: LinuxReactiveWaitObserver,
        android: AndroidReactiveWaitObserver,
    ) -> None:
        self._linux = linux
        self._android = android

    def wait(self, session: Session, request):
        if session.backend == "android":
            return self._android.wait(session, request)
        return self._linux.wait(session, request)


_linux_reactive_observer = LinuxReactiveWaitObserver(
    tree_port=_atspi_tree,
    worker=AtSpiEventWorkerClient(),
    clock=_clock,
)
_android_reactive_observer = AndroidReactiveWaitObserver(
    tree_port=_android.tree,
    activity_port=AdbActivityQueryAdapter(),
    clock=_clock,
)
_dispatching_reactive_observer = _DispatchingReactiveObserver(
    _linux_reactive_observer,
    _android_reactive_observer,
)


# ─── Use case instances ──────────────────────────────────────────────

session_start_uc = SessionStartUseCase(
    display_server=_display_server,
    allocator=_allocator,
    atspi_bus=_atspi_bus,
    process=_process,
    session_repo=_session_repo,
    clock=_clock,
)

session_stop_uc = SessionStopUseCase(
    display_server=_display_server,
    atspi_bus=_atspi_bus,
    process=_process,
    session_repo=_session_repo,
    android_lifecycle=_android_lifecycle,
)

session_list_uc = SessionListUseCase(
    session_repo=_session_repo,
    process=_process,
    clock=_clock,
    android_lifecycle=_android_lifecycle,
)

session_capabilities_uc = SessionCapabilitiesUseCase(
    session_repo=_session_repo,
    android_probe=_android_capability_probe,
)

inspect_uc = InspectUseCase(
    tree=_dispatching_tree,
    screenshot=_dispatching_screenshot,
    session_repo=_session_repo,
    tree_store=_tree_store,
    screenshot_store=_screenshot_store,
    clock=_clock,
)

find_uc = FindUseCase(
    tree=_dispatching_tree,
    session_repo=_session_repo,
    tree_store=_tree_store,
)

action_uc = ActionUseCase(
    action=_dispatching_action,
    session_repo=_session_repo,
    tree_store=_tree_store,
)

mouse_uc = MouseUseCase(
    input_port=_dispatching_input,
    session_repo=_session_repo,
)

key_uc = KeyUseCase(
    input_port=_dispatching_input,
    session_repo=_session_repo,
)

type_text_uc = TypeTextUseCase(
    input_port=_dispatching_input,
    session_repo=_session_repo,
)

screenshot_uc = ScreenshotUseCase(
    screenshot=_dispatching_screenshot,
    session_repo=_session_repo,
    screenshot_store=_screenshot_store,
    crop=_crop,
    tree_store=_tree_store,
)

wait_uc = WaitUseCase(
    tree=_dispatching_tree,
    session_repo=_session_repo,
    tree_store=_tree_store,
    clock=_clock,
)

reactive_wait_uc = ReactiveWaitUseCase(
    session_repo=_session_repo,
    observer=_dispatching_reactive_observer,
)

compound_do_uc = CompoundDoUseCase(
    tree=_dispatching_tree,
    action=_dispatching_action,
    session_repo=_session_repo,
    tree_store=_tree_store,
    clock=_clock,
)

diff_uc = DiffUseCase(
    tree=_dispatching_tree,
    session_repo=_session_repo,
    tree_store=_tree_store,
)

doctor_uc = DoctorUseCase(
    dependency_check=_dep_check,
)

debug_bundle_uc = DebugBundleUseCase(
    session_repo=_session_repo,
    doctor_uc=doctor_uc,
    operation_log=_operation_log,
    tree_store=_tree_store,
    screenshot_store=_screenshot_store,
)

wait_stable_uc = WaitStableUseCase(
    tree=_dispatching_tree,
    session_repo=_session_repo,
    clock=_clock,
)

session_resize_uc = SessionResizeUseCase(
    display_server=_display_server,
    session_repo=_session_repo,
    clock=_clock,
)

metrics_uc = MetricsUseCase(
    op_log=_operation_log,
)

prune_uc = PruneUseCase(
    session_repo=_session_repo,
    cleanup=_session_cleanup,
    process=_process,
    clock=_clock,
)


# ─── GAP-09/10 adapters (session status + dialog detection) ─────────

from aiyes.adapters.xdotool_window_adapter import XdotoolWindowAdapter  # noqa: E402
from aiyes.adapters.atspi_window_adapter import AtSpiWindowAdapter  # noqa: E402
from aiyes.adapters.adb_window_adapter import AdbWindowAdapter  # noqa: E402
from aiyes.domain.use_cases.session_status import SessionStatusUseCase  # noqa: E402
from aiyes.domain.use_cases.detect_dialog import DetectDialogUseCase  # noqa: E402

_xdotool_window = XdotoolWindowAdapter()
_adb_activity = AdbActivityQueryAdapter()
_atspi_window = AtSpiWindowAdapter()
_adb_window = AdbWindowAdapter()

session_status_uc = SessionStatusUseCase(
    session_repo=_session_repo,
    process=_process,
    window_query=_xdotool_window,
    adb_activity=_adb_activity,
    android_lifecycle=_android_lifecycle,
)

detect_dialog_uc = DetectDialogUseCase(
    session_repo=_session_repo,
    tree_store=_tree_store,
    linux_window_port=_atspi_window,
    android_window_port=_adb_window,
)


# ─── GAP-03/07/08/11 adapters (Group C — interaction capabilities) ────

from aiyes.adapters.xclip_adapter import XclipAdapter  # noqa: E402
from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter  # noqa: E402
from aiyes.adapters.adb_gesture_adapter import AdbGestureAdapter  # noqa: E402
from aiyes.adapters.linux_gesture_adapter import LinuxGestureAdapter  # noqa: E402
from aiyes.domain.use_cases.clipboard import ClipboardUseCase  # noqa: E402
from aiyes.domain.use_cases.gesture import GestureUseCase  # noqa: E402
from aiyes.domain.use_cases.navigate import NavigateUseCase  # noqa: E402
from aiyes.domain.use_cases.menu import MenuUseCase  # noqa: E402

_xclip = XclipAdapter()
_adb_clipboard = AdbClipboardAdapter()
_adb_gesture = AdbGestureAdapter()
_linux_gesture = LinuxGestureAdapter()


class _DispatchingClipboard:
    """Routes clipboard requests to backend-specific adapter."""

    def __init__(self, linux: XclipAdapter, android: AdbClipboardAdapter) -> None:
        self._linux = linux
        self._android = android

    def read(self, session: Session) -> str:
        if session.backend == "android":
            return self._android.read(session)
        return self._linux.read(session)

    def write(self, session: Session, text: str) -> None:
        if session.backend == "android":
            self._android.write(session, text)
        else:
            self._linux.write(session, text)


class _DispatchingGesture:
    """Routes gesture requests to backend-specific adapter."""

    def __init__(self, linux: LinuxGestureAdapter, android: AdbGestureAdapter) -> None:
        self._linux = linux
        self._android = android

    def pinch(self, session: Session, x: int, y: int, scale_factor: float) -> None:
        if session.backend == "android":
            self._android.pinch(session, x, y, scale_factor)
        else:
            self._linux.pinch(session, x, y, scale_factor)

    def two_finger_scroll(
        self,
        session: Session,
        x: int,
        y: int,
        direction: str,
        amount: int = 3,
    ) -> None:
        if session.backend == "android":
            self._android.two_finger_scroll(session, x, y, direction, amount)
        else:
            self._linux.two_finger_scroll(session, x, y, direction, amount)


_dispatching_clipboard = _DispatchingClipboard(_xclip, _adb_clipboard)
_dispatching_gesture = _DispatchingGesture(_linux_gesture, _adb_gesture)

clipboard_uc = ClipboardUseCase(
    clipboard_port=_dispatching_clipboard,
    session_repo=_session_repo,
)

gesture_uc = GestureUseCase(
    gesture_port=_dispatching_gesture,
    session_repo=_session_repo,
)

navigate_uc = NavigateUseCase(
    input_port=_dispatching_input,
    session_repo=_session_repo,
)

menu_uc = MenuUseCase(
    tree_port=_dispatching_tree,
    action_port=_dispatching_action,
    session_repo=_session_repo,
    tree_store=_tree_store,
    clock=_clock,
)

scenario_run_uc = ScenarioRunUseCase(
    executor=_scenario_executor,
    evaluate_assertions=False,
    mode="dry_run",
)
_scenario_real_executor = ScenarioUseCaseExecutor(
    session_start=session_start_uc,
    inspect=inspect_uc,
    find=find_uc,
    action=action_uc,
    type_text=type_text_uc,
    screenshot=screenshot_uc,
    session_stop=session_stop_uc,
    navigate=navigate_uc,
)
scenario_real_run_uc = ScenarioRunUseCase(
    executor=_scenario_real_executor,
    prerequisite_checker=_scenario_prerequisite_checker,
    mode="real",
)
scenario_preflight_uc = ScenarioPreflightUseCase()
scenario_real_preflight_uc = ScenarioPreflightUseCase(
    prerequisite_checker=_scenario_prerequisite_checker
)


# Public exports for CLI instrumentation
clock = _clock
operation_log_adapter = _operation_log

_session_resolve_uc = SessionResolveUseCase(
    session_repo=_session_repo,
    process=_process,
    android_lifecycle=_android_lifecycle,
)


def resolve_session_id(session_id: Optional[str] = None) -> str:
    """Resolve a session ID by delegating to SessionResolveUseCase."""
    return _session_resolve_uc.execute(session_id)


def get_adapters_for_backend(backend: str) -> dict:
    """Return backend-specific adapter instances.

    Dispatches adapter selection based on the requested backend.
    For 'linux', returns the standard AT-SPI/xdotool/scrot adapters.
    For 'android', returns adb+uiautomator adapters.

    Note: The dispatching adapters above also route by session.backend
    at call time, so the singleton use cases handle both backends
    transparently.
    """
    if backend == "linux":
        return {
            "tree": _atspi_tree,
            "action": _atspi_action,
            "input": _input,
            "screenshot": _screenshot,
        }
    elif backend == "android":
        return {
            "tree": _android.tree,
            "action": _android.action,
            "input": _android.input,
            "screenshot": _android.screenshot,
        }
    else:
        raise ValueError(f"Unknown backend: {backend!r}")


# ─── Persistent AT-SPI worker lifecycle ───────────────────────────────

from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection  # noqa: E402

_active_worker: Optional[AtSpiWorkerConnection] = None


def start_worker_for_session(session: Session) -> None:
    """Start persistent AT-SPI worker for a Linux session. Adapter-layer only.

    Worker startup failure is caught and logged — the system silently
    degrades to one-shot subprocess per call.
    """
    global _active_worker
    if session.backend != "linux":
        return
    try:
        worker = AtSpiWorkerConnection(session.display, session.atspi_bus_address)
        worker.start()
        _atspi_tree.set_worker(worker)
        _atspi_action.set_worker(worker)
        _atspi_window.set_worker(worker)
        _active_worker = worker
    except Exception:
        # Worker failure must not fail session start
        _atspi_tree.set_worker(None)
        _atspi_action.set_worker(None)
        _atspi_window.set_worker(None)
        _active_worker = None


def stop_worker() -> None:
    """Stop the active persistent AT-SPI worker. Adapter-layer only."""
    global _active_worker
    if _active_worker is None:
        return
    _atspi_tree.set_worker(None)
    _atspi_action.set_worker(None)
    _atspi_window.set_worker(None)
    try:
        _active_worker.stop()
    except Exception:
        pass
    _active_worker = None
