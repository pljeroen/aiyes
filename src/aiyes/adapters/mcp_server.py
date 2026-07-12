"""MCP stdio server adapter — exposes aieyes as an MCP tool server.

Confined to the adapter layer. All MCP SDK imports are guarded behind
a try/except so the module loads even when mcp is not installed.

CLI layer access uses importlib.import_module to keep AST-level imports clean.
This is an approved architecture exemption (AC-10): mcp_server.py legitimately
needs aiyes.cli.schema_gen, aiyes.cli.presenter, and aiyes.cli.main to generate
tool schemas and format output. importlib is used instead of direct imports so
that the adapter-no-CLI-imports architecture test remains enforceable for all
other adapter modules. The exemption is also recorded in
test_adapters_no_cli_imports.

Cancellation limitation (AC-08): In v1, cancellation is not supported.
Sync use cases run to completion via asyncio.to_thread and do not implement
cooperative cancellation. If a client sends CancelledError while a use case
is running, the sync thread will still complete. This is acceptable because
all current use cases are short-lived operations. Future versions may add
cooperative cancellation if long-running use cases are introduced.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

# Import guard: mcp is an optional dependency
try:
    from mcp.server import Server
    from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


_MCP_MAX_TIMEOUT_SECONDS = 300.0
_MCP_MAX_INTERVAL_SECONDS = 60.0
_MCP_MAX_TEXT_CHARS = 10_000
_MCP_MAX_DELAY_MS = 1_000

_MCP_OBSERVATION_TOOLS = frozenset(
    (
        "session_list",
        "session_capabilities",
        "session_metrics",
        "inspect",
        "diff",
        "find",
        "screenshot",
        "wait",
        "wait_reactive",
        "wait_stable",
        "debug_bundle",
        "doctor",
        "mcp_manifest",
        "help_json",
        "detect_dialog",
        "clipboard_read",
        "scenario_preflight",
        "scenario_fixtures",
        # AIYES-117 DOM-lens read primitives (no page mutation).
        "query_dom",
        "page_text",
        "screenshot_selector",
    )
)

_MCP_CONTROL_TOOLS = frozenset(
    (
        "session_start",
        "session_stop",
        "session_resize",
        "session_prune",
        "action",
        "mouse_move",
        "mouse_click",
        "mouse_drag",
        "mouse_scroll",
        "key",
        "type",
        "do",
        "clipboard_write",
        "gesture_pinch",
        "gesture_two_finger_scroll",
        "navigate",
        "menu",
        "goto",
        "reload",
        "scenario_run",
        # AIYES-117: eval runs arbitrary operator JS — a control surface.
        "eval",
    )
)


def _cli_mod(name: str) -> Any:
    """Dynamically import a CLI module by dotted name."""
    return importlib.import_module(name)


def _reject_above_max(name: str, value: float, maximum: float) -> None:
    if value > maximum:
        raise ValueError(f"{name} must be <= {maximum:g}, got {value:g}")


def _validate_mcp_screenshot_output_path(raw_output: Any) -> Any:
    """Validate explicit MCP screenshot output paths before use-case dispatch."""
    if raw_output is None:
        return None
    if not isinstance(raw_output, str):
        raise ValueError("output must be a string path")
    if raw_output.strip() == "":
        raise ValueError("output must not be empty")

    raw_path = Path(raw_output).expanduser()
    if ".." in Path(raw_output).parts:
        raise ValueError("output must not contain parent traversal")
    if raw_path.exists():
        raise ValueError("output must not target an existing path")
    if raw_path.is_symlink():
        raise ValueError("output must not target a symlink")

    resolved = raw_path.resolve(strict=False)
    cwd = Path.cwd().resolve(strict=False)
    if _path_is_relative_to(resolved, cwd):
        raise ValueError("output must not target a project path")

    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    tmpdir = os.environ.get("TMPDIR")
    allowed_roots = [temp_root]
    if tmpdir:
        allowed_roots.append(Path(tmpdir).expanduser().resolve(strict=False))
    if not any(_path_is_relative_to(resolved, root) for root in allowed_roots):
        raise ValueError("output must be under the system temporary directory")

    return raw_output


def _resolve_evidence_profile(raw_profile: Any) -> str:
    """Resolve the MCP scenario_run evidence profile argument (FC-MCP-01).

    Omitting the argument yields the compact default; an out-of-enum value is
    rejected (ValueError -> isError), never silently coerced.
    """
    from aiyes.domain import evidence_profile

    if raw_profile is None:
        return evidence_profile.COMPACT
    if not isinstance(raw_profile, str):
        raise ValueError("profile must be a string")
    return evidence_profile.normalize_profile(raw_profile)


def _emit_mcp_profile_selection(diagnostic_log: Any, result: Any, profile: str) -> None:
    """Emit exactly one LE-02 evidence.profile.selected event (MCP boundary).

    A10-CRIT-004: the single emission point for the MCP scenario_run handler.
    Fail-open: a None sink is a no-op; any error is swallowed (the sink owns the
    observable failure count). Builds the payload via the pure domain shaper.
    """
    if diagnostic_log is None:
        return
    try:
        import dataclasses as _dc

        from aiyes.domain import evidence_profile

        raw_steps = [_dc.asdict(step) for step in result.steps]
        preserved = evidence_profile.classified_failure_count(raw_steps)
        diagnostic_log.emit_event(
            evidence_profile.build_profile_selection_event(profile, preserved)
        )
    except Exception:
        # Fail-open: emission must never affect the handler result (FC-OBS-03).
        pass


def _validate_mcp_evidence_dir(raw_dir: Any) -> Any:
    """Validate explicit MCP scenario evidence output directories."""
    if raw_dir is None:
        return None
    if not isinstance(raw_dir, str):
        raise ValueError("evidence_dir must be a string path")
    if raw_dir.strip() == "":
        raise ValueError("evidence_dir must not be empty")

    raw_path = Path(raw_dir).expanduser()
    if ".." in Path(raw_dir).parts:
        raise ValueError("evidence_dir must not contain parent traversal")
    if raw_path.is_file():
        raise ValueError("evidence_dir must not target an existing file")
    if raw_path.is_symlink():
        raise ValueError("evidence_dir must not target a symlink")

    resolved = raw_path.resolve(strict=False)
    cwd = Path.cwd().resolve(strict=False)
    if _path_is_relative_to(resolved, cwd):
        raise ValueError("evidence_dir must not target a project path")

    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    tmpdir = os.environ.get("TMPDIR")
    allowed_roots = [temp_root]
    if tmpdir:
        allowed_roots.append(Path(tmpdir).expanduser().resolve(strict=False))
    if not any(_path_is_relative_to(resolved, root) for root in allowed_roots):
        raise ValueError("evidence_dir must be under the system temporary directory")

    return raw_path


def _tool_risk(tool_name: str) -> str:
    if tool_name in _MCP_CONTROL_TOOLS:
        return "gui-control"
    if tool_name in _MCP_OBSERVATION_TOOLS:
        return "gui-observation"
    return "unknown"


def _tool_annotations(tool_name: str) -> Any:
    risk = _tool_risk(tool_name)
    return ToolAnnotations(
        readOnlyHint=risk == "gui-observation",
        destructiveHint=risk == "gui-control",
        idempotentHint=False,
        openWorldHint=True,
    )


def _tool_meta(tool_name: str) -> Dict[str, Any]:
    return {
        "aiyes": {
            "trust_boundary": "trusted-local-stdio",
            "sandbox": False,
            "risk": _tool_risk(tool_name),
            "warning": (
                "AIYES is a trusted local GUI control surface, not a sandbox. "
                "Use only with trusted local clients and targets."
            ),
        }
    }


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclasses.dataclass(frozen=True)
class ServerDependencies:
    """Injectable dependencies for the MCP server.

    26 use cases + clock + operation_log + resolve_session_id = 29 fields.
    """

    session_start_uc: Any
    session_stop_uc: Any
    session_list_uc: Any
    session_capabilities_uc: Any
    session_resize_uc: Any
    metrics_uc: Any
    prune_uc: Any
    inspect_uc: Any
    diff_uc: Any
    find_uc: Any
    screenshot_uc: Any
    action_uc: Any
    mouse_uc: Any
    key_uc: Any
    type_text_uc: Any
    wait_uc: Any
    reactive_wait_uc: Any
    wait_stable_uc: Any
    compound_do_uc: Any
    doctor_uc: Any
    debug_bundle_uc: Any
    session_status_uc: Any
    detect_dialog_uc: Any
    clipboard_uc: Any
    gesture_uc: Any
    navigate_uc: Any
    menu_uc: Any
    goto_uc: Any
    reload_uc: Any
    eval_uc: Any
    query_dom_uc: Any
    page_text_uc: Any
    screenshot_selector_uc: Any
    clock: Any
    operation_log: Any
    resolve_session_id: Any
    scenario_run_uc: Any = None
    scenario_real_run_uc: Any = None
    scenario_preflight_uc: Any = None
    scenario_real_preflight_uc: Any = None
    load_scenario_file: Any = None
    write_scenario_evidence_bundle: Any = None
    list_public_scenario_fixtures: Any = None
    scenario_validation_preflight_result: Any = None
    scenario_evidence_path_check: Any = None
    diagnostic_log: Any = None


@dataclasses.dataclass(frozen=True)
class ToolHandler:
    """Frozen dispatch entry for a single MCP tool.

    Fields:
        tool_name: Underscore-joined name matching MCP tool registration.
        use_case_call: Callable(args, deps, session_id) -> str.
        session_class: One of "bound", "creating", or "less".
            - "bound": requires existing session_id, uses per-session lock.
            - "creating": creates a new session, uses global lock.
            - "less": no session context, uses global lock.
        presenter: Callable that formats the use case result (currently
            resolved dynamically via _presenter(), stored for inspectability).
    """

    tool_name: str
    use_case_call: Callable
    session_class: str  # "bound" | "creating" | "less"
    presenter: Callable


def create_mcp_server(deps: ServerDependencies) -> "McpServerWrapper":
    """Factory: create an MCP server wired with the given dependencies.

    Returns a wrapper that exposes list_tools() and call_tool() as
    async methods suitable for both MCP transport and direct test use.
    """
    schema_gen = _cli_mod("aiyes.cli.schema_gen")
    main_mod = _cli_mod("aiyes.cli.main")

    commands = schema_gen.enumerate_commands(main_mod.cli)
    tool_defs = [
        Tool(
            name=ci.tool_name,
            description=ci.description,
            inputSchema=ci.json_schema,
            annotations=_tool_annotations(ci.tool_name),
            _meta=_tool_meta(ci.tool_name),
        )
        for ci in commands
    ]

    dispatch = _build_dispatch_table(deps)

    # Per-session locks and global lock
    session_locks: Dict[str, asyncio.Lock] = {}
    global_lock = asyncio.Lock()

    async def _get_session_lock(session_id: str) -> asyncio.Lock:
        if session_id not in session_locks:
            session_locks[session_id] = asyncio.Lock()
        return session_locks[session_id]

    async def list_tools_handler() -> List[Any]:
        return tool_defs

    async def call_tool_handler(name: str, arguments: Dict[str, Any]) -> Any:
        op_record_mod = importlib.import_module("aiyes.domain.operation_record")
        OperationRecord = op_record_mod.OperationRecord

        if name not in dispatch:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

        handler = dispatch[name]
        handler_fn = handler.use_case_call
        # Determine lock scope from session_class:
        # "bound" -> per-session lock; "creating" and "less" -> global lock
        uses_global_lock = handler.session_class in ("less", "creating")

        # Determine session_id for locking and logging
        session_id = ""
        t0 = deps.clock.now()
        exit_code = 0
        error_msg = ""
        if not uses_global_lock:
            raw_sid = arguments.get("session_id")
            try:
                session_id = deps.resolve_session_id(raw_sid)
            except (RuntimeError, ValueError) as exc:
                exit_code = 1
                error_msg = str(exc)
                try:
                    t1 = deps.clock.now()
                    duration_ms = (t1 - t0) * 1000.0
                    record = OperationRecord(
                        timestamp=t0,
                        session_id=session_id,
                        command=name,
                        duration_ms=duration_ms,
                        exit_code=exit_code,
                        error=error_msg,
                    )
                    deps.operation_log.append(record)
                except Exception:
                    pass
                return CallToolResult(
                    content=[TextContent(type="text", text=error_msg)],
                    isError=True,
                )

        # Acquire appropriate lock
        if uses_global_lock:
            lock = global_lock
        else:
            lock = await _get_session_lock(session_id)

        # Cancellation note (AC-08): asyncio.to_thread runs the sync use case
        # to completion. Even if the caller cancels, the sync thread finishes.
        # This is a known v1 limitation — sync use cases do not support
        # cooperative cancellation.
        async with lock:
            try:
                result_text = await asyncio.to_thread(
                    handler_fn, arguments, deps, session_id
                )
                return CallToolResult(
                    content=[TextContent(type="text", text=str(result_text))],
                    isError=False,
                )
            except Exception as exc:
                exit_code = 1
                error_msg = str(exc)
                return CallToolResult(
                    content=[TextContent(type="text", text=str(exc))],
                    isError=True,
                )
            finally:
                try:
                    t1 = deps.clock.now()
                    duration_ms = (t1 - t0) * 1000.0
                    # F-01/BC-22/IC-04: MCP logs use tool_name (underscore),
                    # not cli_name. This distinguishes MCP vs CLI origin.
                    record = OperationRecord(
                        timestamp=t0,
                        session_id=session_id,
                        command=name,
                        duration_ms=duration_ms,
                        exit_code=exit_code,
                        error=error_msg,
                    )
                    deps.operation_log.append(record)
                except Exception:
                    pass

    # Also register on an MCP Server instance for transport use
    if _MCP_AVAILABLE:
        mcp_server = Server("aieyes")

        @mcp_server.list_tools()
        async def _list_tools() -> List[Any]:
            return await list_tools_handler()

        @mcp_server.call_tool()
        async def _call_tool(name: str, arguments: Dict[str, Any]) -> Any:
            return await call_tool_handler(name, arguments)
    else:
        mcp_server = None

    return McpServerWrapper(
        list_tools_fn=list_tools_handler,
        call_tool_fn=call_tool_handler,
        mcp_server=mcp_server,
    )


class McpServerWrapper:
    """Wrapper exposing list_tools/call_tool as direct async methods.

    Also holds reference to the underlying MCP Server for stdio transport.
    """

    def __init__(
        self,
        list_tools_fn: Callable,
        call_tool_fn: Callable,
        mcp_server: Any,
    ) -> None:
        self._list_tools_fn = list_tools_fn
        self._call_tool_fn = call_tool_fn
        self._mcp_server = mcp_server

    async def list_tools(self) -> List[Any]:
        return await self._list_tools_fn()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        return await self._call_tool_fn(name, arguments)

    @property
    def mcp_server(self) -> Any:
        return self._mcp_server


def _presenter() -> Any:
    """Dynamically import the presenter module."""
    return _cli_mod("aiyes.cli.presenter")


def _build_dispatch_table(
    deps: ServerDependencies,
) -> Dict[str, ToolHandler]:
    """Build tool_name -> ToolHandler mapping.

    Each ToolHandler contains the handler function, session_class, and presenter.
    The handler takes (arguments, deps, session_id) and returns a string result.
    """
    pres = _presenter

    def _handle_session_start(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        command = args.get("command", [])
        if not command:
            raise ValueError("command is required for session start")
        app_command = command[0] if isinstance(command, (list, tuple)) else command
        app_args = list(command[1:]) if isinstance(command, (list, tuple)) else []
        session = deps.session_start_uc.execute(
            app_command=app_command,
            app_args=app_args,
            resolution=args.get("resolution", "1280x800"),
            color_depth=args.get("color_depth", 24),
            wait=args.get("wait_secs", 2.0),
            name=args.get("name"),
            backend=args.get("backend", "linux"),
            device_serial=args.get("device_serial"),
            marionette=args.get("marionette", False),
        )
        return pres().format_session_start(session)

    def _handle_session_stop(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.session_stop_uc.execute(session_id=session_id)
        errors = list(result.errors) if result.errors else None
        return pres().format_session_stop(
            result.status, result.session_id, errors=errors
        )

    def _handle_session_list(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        import dataclasses as dc

        entries = deps.session_list_uc.execute(
            active_only=args.get("active_only", False)
        )
        result_list = [dc.asdict(e) for e in entries]
        return pres().format_session_list(result_list)

    def _handle_session_capabilities(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.session_capabilities_uc.execute(
            session_id=session_id,
            live=args.get("live", False),
        )
        return pres().format_session_capabilities(result)

    def _handle_session_resize(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.session_resize_uc.execute(
            session_id=session_id,
            resolution=args["resolution"],
            settle=args.get("settle", 0.5),
        )
        return pres().format_session_resize(result.status, result.resolution)

    def _handle_session_metrics(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.metrics_uc.execute(session_id=args.get("session_id"))
        return pres().format_metrics(result)

    def _handle_session_prune(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.prune_uc.execute(
            max_age_hours=args.get("older_than", 72.0),
            dry_run=args.get("dry_run", False),
        )
        return pres().format_prune(result)

    def _handle_inspect(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.inspect_uc.execute(
            session_id=session_id,
            no_screenshot=args.get("no_screenshot", False),
            no_tree=args.get("no_tree", False),
            tree_depth=args.get("tree_depth"),
            no_prune=args.get("no_prune", False),
            screenshot_base64=args.get("screenshot_base64", False),
            focus_window=args.get("focus_window"),
        )
        return pres().format_inspect_result(
            tree=result.tree,
            screenshot=result.screenshot,
            timestamp=result.timestamp,
            screenshot_base64=result.screenshot_base64,
            screenshot_data=result.screenshot_data,
            diagnostics=result.diagnostics,
        )

    def _handle_diff(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.diff_uc.execute(session_id=session_id)
        return pres().format_diff(result)

    def _handle_find(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        find_kwargs: Dict[str, Any] = dict(
            session_id=session_id,
            role=args["role"],
            name_pattern=args.get("name_pattern"),
            state=args.get("state"),
            within_role=args.get("within_role"),
            within_name=args.get("within_name"),
        )
        # AIYES-116: thread resource_id only when the caller supplied it, so an
        # unscoped/legacy find call stays byte-for-byte unchanged (the use case
        # also defaults to None). Mirrors the scenario conditional-inclusion.
        if "resource_id" in args:
            find_kwargs["resource_id"] = args.get("resource_id")
        results = deps.find_uc.execute(**find_kwargs)
        return pres().format_find_nodes(results)

    def _handle_screenshot(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        # Parse region from comma-separated string if provided
        raw_region = args.get("region")
        region = None
        if raw_region is not None:
            if isinstance(raw_region, str):
                parts = [int(p) for p in raw_region.split(",")]
            elif isinstance(raw_region, (list, tuple)):
                parts = [int(p) for p in raw_region]
            else:
                raise ValueError(
                    f"region must be a string 'X,Y,W,H' or list of 4 ints, got {type(raw_region).__name__}"
                )
            if len(parts) != 4:
                raise ValueError(
                    f"region must have exactly 4 values (X,Y,W,H), got {len(parts)}"
                )
            region = tuple(parts)

        result = deps.screenshot_uc.execute(
            session_id=session_id,
            output_path=_validate_mcp_screenshot_output_path(args.get("output")),
            base64=args.get("base64", False),
            region=region,
            node_id=args.get("node_id"),
        )
        return pres().format_screenshot(
            path=result.path,
            data=result.data,
            width=result.width,
            height=result.height,
        )

    def _handle_action(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.action_uc.execute(
            session_id=session_id,
            node_id=args["node_id"],
            action_name=args["action_name"],
            value=args.get("value"),
        )
        avail = list(result.available_actions) if result.available_actions else None
        states = list(result.node_states) if result.node_states else None
        return pres().format_action(
            status=result.status,
            action=result.action,
            target=result.target,
            reason=result.reason,
            available_actions=avail,
            node_value=result.node_value,
            node_states=states,
            action_method=result.action_method,
        )

    def _handle_mouse_move(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        deps.mouse_uc.move(session_id, args["x"], args["y"])
        return pres().format_status_ok()

    def _handle_mouse_click(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        if ("x" in args) != ("y" in args):
            raise ValueError("x and y must be both absent or both present")
        deps.mouse_uc.click(
            session_id,
            args.get("x"),
            args.get("y"),
            args.get("button", "left"),
        )
        return pres().format_status_ok()

    def _handle_mouse_drag(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        deps.mouse_uc.drag(
            session_id,
            args["x1"],
            args["y1"],
            args["x2"],
            args["y2"],
        )
        return pres().format_status_ok()

    def _handle_mouse_scroll(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        deps.mouse_uc.scroll(
            session_id,
            args["direction"],
            args.get("amount", 3),
        )
        return pres().format_status_ok()

    def _handle_key(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        keys = args.get("keys", [])
        if not keys:
            raise ValueError("keys must contain at least one item")
        deps.key_uc.execute(session_id, list(keys))
        return pres().format_status_ok()

    def _handle_type(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        text = args["text"]
        if len(text) > _MCP_MAX_TEXT_CHARS:
            raise ValueError(
                f"text must be <= {_MCP_MAX_TEXT_CHARS} characters, got {len(text)}"
            )
        delay_ms = args.get("delay_ms", 0)
        _reject_above_max("delay_ms", float(delay_ms), float(_MCP_MAX_DELAY_MS))
        deps.type_text_uc.execute(session_id, text, delay_ms=delay_ms)
        return pres().format_status_ok()

    def _handle_wait(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        timeout = args.get("timeout", 30.0)
        _reject_above_max("timeout", float(timeout), _MCP_MAX_TIMEOUT_SECONDS)
        result = deps.wait_uc.execute(
            session_id=session_id,
            role=args["role"],
            name_pattern=args.get("name_pattern"),
            timeout=timeout,
            state=args.get("state"),
            absent=args.get("absent", False),
            transient=args.get("transient", False),
        )
        return pres().format_wait(
            found=result.found,
            timeout=result.timeout,
            node_id=result.id,
            transient=result.transient,
            role_drift=result.role_drift,
        )

    def _handle_wait_stable(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        ignore_raw = args.get("ignore_nodes", [])
        timeout = args.get("timeout", 10.0)
        interval = args.get("interval", 0.5)
        _reject_above_max("timeout", float(timeout), _MCP_MAX_TIMEOUT_SECONDS)
        _reject_above_max("interval", float(interval), _MCP_MAX_INTERVAL_SECONDS)
        result = deps.wait_stable_uc.execute(
            session_id=session_id,
            timeout=timeout,
            poll_interval=interval,
            consecutive=args.get("consecutive", 3),
            tolerance=args.get("tolerance", 0),
            ignore_ids=frozenset(ignore_raw),
        )
        return pres().format_wait_stable(
            stable=result.stable,
            timeout=result.timeout,
            polls=result.polls,
            changes=result.changes,
            comparison_mode=result.comparison_mode,
        )

    def _handle_wait_reactive(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        timeout = args.get("timeout", 10.0)
        poll_interval = args.get("poll_interval", 0.25)
        _reject_above_max("timeout", float(timeout), _MCP_MAX_TIMEOUT_SECONDS)
        _reject_above_max(
            "poll_interval", float(poll_interval), _MCP_MAX_INTERVAL_SECONDS
        )
        result = deps.reactive_wait_uc.execute(
            session_id=session_id,
            condition=args["condition"],
            name_pattern=args.get("name_pattern"),
            timeout=timeout,
            quiet=args.get("quiet", 0.0),
            poll_interval=poll_interval,
        )
        return pres().format_reactive_wait(result)

    def _handle_do(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.compound_do_uc.execute(
            session_id=session_id,
            role=args["role"],
            action_name=args["action_name"],
            name_pattern=args.get("name_pattern"),
            verify=args.get("verify", False),
            value=args.get("value"),
            timeout=args.get("timeout"),
        )
        return pres().format_do(result)

    def _handle_doctor(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        import dataclasses as dc

        results = deps.doctor_uc.execute()
        result_dicts = [{**dc.asdict(r), "category": r.category} for r in results]
        return pres().format_doctor(result_dicts)

    def _handle_debug_bundle(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.debug_bundle_uc.execute(session_id=session_id, environ=os.environ)
        return pres().format_debug_bundle(result)

    def _handle_mcp_manifest(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        main_mod = _cli_mod("aiyes.cli.main")
        return pres().format_mcp_manifest(main_mod._build_mcp_manifest())

    def _handle_session_status(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.session_status_uc.execute(session_id=session_id)
        return pres().format_session_status(
            app_alive=result.app_alive,
            app_foreground=result.app_foreground,
            display_alive=result.display_alive,
            marionette_enabled=result.marionette_enabled,
            marionette_port=result.marionette_port,
        )

    def _handle_detect_dialog(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.detect_dialog_uc.execute(session_id=session_id)
        return pres().format_detect_dialog(
            dialog_detected=result.dialog_detected,
            window_name=result.window_name,
            window_role=result.window_role,
            error=result.error,
        )

    def _handle_clipboard_read(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.clipboard_uc.read(session_id=session_id)
        return pres().format_clipboard_read(result.text)

    def _handle_clipboard_write(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        deps.clipboard_uc.write(session_id=session_id, text=args["text"])
        return pres().format_clipboard_write()

    def _handle_gesture_pinch(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        deps.gesture_uc.pinch(
            session_id=session_id,
            x=args["x"],
            y=args["y"],
            scale_factor=args["scale_factor"],
        )
        return pres().format_gesture_result()

    def _handle_gesture_two_finger_scroll(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        deps.gesture_uc.two_finger_scroll(
            session_id=session_id,
            x=args["x"],
            y=args["y"],
            direction=args["direction"],
            amount=args.get("amount", 3),
        )
        return pres().format_gesture_result()

    def _handle_swipe(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        deps.gesture_uc.swipe(
            session_id=session_id,
            x1=args["x1"],
            y1=args["y1"],
            x2=args["x2"],
            y2=args["y2"],
            duration_ms=args.get("duration_ms", 300),
        )
        return pres().format_gesture_result()

    def _handle_navigate(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.navigate_uc.execute(
            session_id=session_id,
            action=args["action"],
        )
        return pres().format_navigate_result(
            status=result.status,
            warning=result.warning,
        )

    def _handle_menu(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.menu_uc.execute(
            session_id=session_id,
            menu_path=args["menu_path"],
        )
        return pres().format_menu_result(
            status=result.status,
            node_id=result.node_id,
            node_name=result.node_name,
        )

    def _handle_goto(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.goto_uc.execute(
            session_id=session_id,
            url=args["url"],
        )
        return pres().format_goto_result(
            status=result.status,
            session_id=result.session_id,
            action=result.action,
            url=result.url,
            reason=result.reason,
        )

    def _handle_reload(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.reload_uc.execute(session_id=session_id)
        return pres().format_reload_result(
            status=result.status,
            session_id=result.session_id,
            action=result.action,
            reason=result.reason,
        )

    def _handle_eval(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.eval_uc.execute(session_id=session_id, script=args["script"])
        return pres().format_eval_result(
            status=result.status,
            session_id=result.session_id,
            action=result.action,
            value=result.value,
            reason=result.reason,
        )

    def _handle_query_dom(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.query_dom_uc.execute(
            session_id=session_id, selector=args["selector"]
        )
        return pres().format_query_dom_result(
            status=result.status,
            session_id=result.session_id,
            selector=result.selector,
            count=result.count,
            nodes=result.nodes,
            truncated=result.truncated,
            reason=result.reason,
        )

    def _handle_page_text(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.page_text_uc.execute(
            session_id=session_id, selector=args.get("selector")
        )
        return pres().format_page_text_result(
            status=result.status,
            session_id=result.session_id,
            selector=result.selector,
            text=result.text,
            found=result.found,
            reason=result.reason,
        )

    def _handle_screenshot_selector(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        result = deps.screenshot_selector_uc.execute(
            session_id=session_id, selector=args["selector"]
        )
        return pres().format_screenshot_selector_result(
            status=result.status,
            session_id=result.session_id,
            selector=result.selector,
            path=result.path,
            width=result.width,
            height=result.height,
            reason=result.reason,
        )

    def _handle_scenario_run(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        scenario_path = args.get("scenario_path")
        if not isinstance(scenario_path, str) or not scenario_path:
            raise ValueError("scenario_path is required")
        loaded = deps.load_scenario_file(
            Path(scenario_path),
            public_fixture=args.get("public_fixture", False),
        )
        if not loaded.ok or loaded.scenario is None:
            return pres().format_scenario_validation_errors(loaded.issues)

        profile = _resolve_evidence_profile(args.get("profile", "compact"))

        real_execution = bool(args.get("real_execution", args.get("real", False)))
        runner = deps.scenario_real_run_uc if real_execution else deps.scenario_run_uc
        result = runner.execute(loaded.scenario)

        try:
            evidence_dir = _validate_mcp_evidence_dir(args.get("evidence_dir"))
        except ValueError as exc:
            raise ValueError(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_code": "evidence_path_rejected",
                        "error": str(exc),
                    },
                    sort_keys=True,
                )
            ) from exc
        if evidence_dir is not None:
            deps.write_scenario_evidence_bundle(evidence_dir, result, profile=profile)
        rendered = pres().format_scenario_run(result, profile=profile)
        # A10-CRIT-004: emit LE-02 EXACTLY ONCE at this handler boundary through
        # the production diagnostic sink (fail-open, None => no emission).
        _emit_mcp_profile_selection(deps.diagnostic_log, result, profile)
        return rendered

    def _handle_scenario_preflight(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        scenario_path = args.get("scenario_path")
        if not isinstance(scenario_path, str) or not scenario_path:
            raise ValueError("scenario_path is required")
        loaded = deps.load_scenario_file(
            Path(scenario_path),
            public_fixture=args.get("public_fixture", False),
        )
        if not loaded.ok or loaded.scenario is None:
            return pres().format_scenario_preflight(
                deps.scenario_validation_preflight_result(loaded.issues)
            )

        evidence_check = deps.scenario_evidence_path_check(status="not_requested")
        try:
            evidence_dir = _validate_mcp_evidence_dir(args.get("evidence_dir"))
            if evidence_dir is not None:
                evidence_check = deps.scenario_evidence_path_check(
                    status="passed",
                    path=str(evidence_dir),
                )
        except ValueError as exc:
            evidence_check = deps.scenario_evidence_path_check(
                status="failed",
                path=str(args.get("evidence_dir", "")),
                reason=str(exc),
            )

        real_execution = bool(args.get("real_execution", args.get("real", False)))
        runner = (
            deps.scenario_real_preflight_uc
            if real_execution
            else deps.scenario_preflight_uc
        )
        result = runner.execute(
            loaded.scenario,
            real_execution=real_execution,
            evidence_dir=evidence_check,
        )
        return pres().format_scenario_preflight(result)

    def _handle_scenario_fixtures(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        return pres().format_scenario_fixtures(deps.list_public_scenario_fixtures())

    def _handle_help_json(
        args: Dict[str, Any], deps: ServerDependencies, session_id: str
    ) -> str:
        help_json_mod = _cli_mod("aiyes.cli.help_json")
        main_mod = _cli_mod("aiyes.cli.main")
        data = help_json_mod.build_help_json(main_mod.cli)
        return json.dumps(data, indent=2)

    return {
        "session_start": ToolHandler(
            tool_name="session_start",
            use_case_call=_handle_session_start,
            session_class="creating",
            presenter=pres,
        ),
        "session_stop": ToolHandler(
            tool_name="session_stop",
            use_case_call=_handle_session_stop,
            session_class="bound",
            presenter=pres,
        ),
        "session_list": ToolHandler(
            tool_name="session_list",
            use_case_call=_handle_session_list,
            session_class="less",
            presenter=pres,
        ),
        "session_capabilities": ToolHandler(
            tool_name="session_capabilities",
            use_case_call=_handle_session_capabilities,
            session_class="bound",
            presenter=pres,
        ),
        "session_resize": ToolHandler(
            tool_name="session_resize",
            use_case_call=_handle_session_resize,
            session_class="bound",
            presenter=pres,
        ),
        "session_metrics": ToolHandler(
            tool_name="session_metrics",
            use_case_call=_handle_session_metrics,
            session_class="less",
            presenter=pres,
        ),
        "session_prune": ToolHandler(
            tool_name="session_prune",
            use_case_call=_handle_session_prune,
            session_class="less",
            presenter=pres,
        ),
        "session_status": ToolHandler(
            tool_name="session_status",
            use_case_call=_handle_session_status,
            session_class="bound",
            presenter=pres,
        ),
        "inspect": ToolHandler(
            tool_name="inspect",
            use_case_call=_handle_inspect,
            session_class="bound",
            presenter=pres,
        ),
        "diff": ToolHandler(
            tool_name="diff",
            use_case_call=_handle_diff,
            session_class="bound",
            presenter=pres,
        ),
        "find": ToolHandler(
            tool_name="find",
            use_case_call=_handle_find,
            session_class="bound",
            presenter=pres,
        ),
        "screenshot": ToolHandler(
            tool_name="screenshot",
            use_case_call=_handle_screenshot,
            session_class="bound",
            presenter=pres,
        ),
        "action": ToolHandler(
            tool_name="action",
            use_case_call=_handle_action,
            session_class="bound",
            presenter=pres,
        ),
        "mouse_move": ToolHandler(
            tool_name="mouse_move",
            use_case_call=_handle_mouse_move,
            session_class="bound",
            presenter=pres,
        ),
        "mouse_click": ToolHandler(
            tool_name="mouse_click",
            use_case_call=_handle_mouse_click,
            session_class="bound",
            presenter=pres,
        ),
        "mouse_drag": ToolHandler(
            tool_name="mouse_drag",
            use_case_call=_handle_mouse_drag,
            session_class="bound",
            presenter=pres,
        ),
        "mouse_scroll": ToolHandler(
            tool_name="mouse_scroll",
            use_case_call=_handle_mouse_scroll,
            session_class="bound",
            presenter=pres,
        ),
        "key": ToolHandler(
            tool_name="key",
            use_case_call=_handle_key,
            session_class="bound",
            presenter=pres,
        ),
        "type": ToolHandler(
            tool_name="type",
            use_case_call=_handle_type,
            session_class="bound",
            presenter=pres,
        ),
        "wait": ToolHandler(
            tool_name="wait",
            use_case_call=_handle_wait,
            session_class="bound",
            presenter=pres,
        ),
        "wait_reactive": ToolHandler(
            tool_name="wait_reactive",
            use_case_call=_handle_wait_reactive,
            session_class="bound",
            presenter=pres,
        ),
        "wait_stable": ToolHandler(
            tool_name="wait_stable",
            use_case_call=_handle_wait_stable,
            session_class="bound",
            presenter=pres,
        ),
        "do": ToolHandler(
            tool_name="do",
            use_case_call=_handle_do,
            session_class="bound",
            presenter=pres,
        ),
        "doctor": ToolHandler(
            tool_name="doctor",
            use_case_call=_handle_doctor,
            session_class="less",
            presenter=pres,
        ),
        "debug_bundle": ToolHandler(
            tool_name="debug_bundle",
            use_case_call=_handle_debug_bundle,
            session_class="bound",
            presenter=pres,
        ),
        "mcp_manifest": ToolHandler(
            tool_name="mcp_manifest",
            use_case_call=_handle_mcp_manifest,
            session_class="less",
            presenter=pres,
        ),
        "help_json": ToolHandler(
            tool_name="help_json",
            use_case_call=_handle_help_json,
            session_class="less",
            presenter=pres,
        ),
        "detect_dialog": ToolHandler(
            tool_name="detect_dialog",
            use_case_call=_handle_detect_dialog,
            session_class="bound",
            presenter=pres,
        ),
        "clipboard_read": ToolHandler(
            tool_name="clipboard_read",
            use_case_call=_handle_clipboard_read,
            session_class="bound",
            presenter=pres,
        ),
        "clipboard_write": ToolHandler(
            tool_name="clipboard_write",
            use_case_call=_handle_clipboard_write,
            session_class="bound",
            presenter=pres,
        ),
        "gesture_pinch": ToolHandler(
            tool_name="gesture_pinch",
            use_case_call=_handle_gesture_pinch,
            session_class="bound",
            presenter=pres,
        ),
        "gesture_two_finger_scroll": ToolHandler(
            tool_name="gesture_two_finger_scroll",
            use_case_call=_handle_gesture_two_finger_scroll,
            session_class="bound",
            presenter=pres,
        ),
        "swipe": ToolHandler(
            tool_name="swipe",
            use_case_call=_handle_swipe,
            session_class="bound",
            presenter=pres,
        ),
        "navigate": ToolHandler(
            tool_name="navigate",
            use_case_call=_handle_navigate,
            session_class="bound",
            presenter=pres,
        ),
        "menu": ToolHandler(
            tool_name="menu",
            use_case_call=_handle_menu,
            session_class="bound",
            presenter=pres,
        ),
        "goto": ToolHandler(
            tool_name="goto",
            use_case_call=_handle_goto,
            session_class="bound",
            presenter=pres,
        ),
        "reload": ToolHandler(
            tool_name="reload",
            use_case_call=_handle_reload,
            session_class="bound",
            presenter=pres,
        ),
        "eval": ToolHandler(
            tool_name="eval",
            use_case_call=_handle_eval,
            session_class="bound",
            presenter=pres,
        ),
        "query_dom": ToolHandler(
            tool_name="query_dom",
            use_case_call=_handle_query_dom,
            session_class="bound",
            presenter=pres,
        ),
        "page_text": ToolHandler(
            tool_name="page_text",
            use_case_call=_handle_page_text,
            session_class="bound",
            presenter=pres,
        ),
        "screenshot_selector": ToolHandler(
            tool_name="screenshot_selector",
            use_case_call=_handle_screenshot_selector,
            session_class="bound",
            presenter=pres,
        ),
        "scenario_run": ToolHandler(
            tool_name="scenario_run",
            use_case_call=_handle_scenario_run,
            session_class="creating",
            presenter=pres,
        ),
        "scenario_preflight": ToolHandler(
            tool_name="scenario_preflight",
            use_case_call=_handle_scenario_preflight,
            session_class="creating",
            presenter=pres,
        ),
        "scenario_fixtures": ToolHandler(
            tool_name="scenario_fixtures",
            use_case_call=_handle_scenario_fixtures,
            session_class="less",
            presenter=pres,
        ),
    }


def main() -> None:
    """Entry point for the aieyes-mcp command. Wires real deps from composition_root."""
    if not _MCP_AVAILABLE:
        print(
            "Error: MCP server requires the 'mcp' package.\n"
            "Install it with: pip install aiyes[mcp]",
            flush=True,
        )
        raise SystemExit(1)

    # Deferred import via importlib to avoid adapter boundary violations
    comp_root = _cli_mod("aiyes.cli.composition_root")

    deps = ServerDependencies(
        session_start_uc=comp_root.session_start_uc,
        session_stop_uc=comp_root.session_stop_uc,
        session_list_uc=comp_root.session_list_uc,
        session_capabilities_uc=comp_root.session_capabilities_uc,
        session_resize_uc=comp_root.session_resize_uc,
        metrics_uc=comp_root.metrics_uc,
        prune_uc=comp_root.prune_uc,
        inspect_uc=comp_root.inspect_uc,
        diff_uc=comp_root.diff_uc,
        find_uc=comp_root.find_uc,
        screenshot_uc=comp_root.screenshot_uc,
        action_uc=comp_root.action_uc,
        mouse_uc=comp_root.mouse_uc,
        key_uc=comp_root.key_uc,
        type_text_uc=comp_root.type_text_uc,
        wait_uc=comp_root.wait_uc,
        reactive_wait_uc=comp_root.reactive_wait_uc,
        wait_stable_uc=comp_root.wait_stable_uc,
        compound_do_uc=comp_root.compound_do_uc,
        doctor_uc=comp_root.doctor_uc,
        debug_bundle_uc=comp_root.debug_bundle_uc,
        session_status_uc=comp_root.session_status_uc,
        detect_dialog_uc=comp_root.detect_dialog_uc,
        clipboard_uc=comp_root.clipboard_uc,
        gesture_uc=comp_root.gesture_uc,
        navigate_uc=comp_root.navigate_uc,
        menu_uc=comp_root.menu_uc,
        goto_uc=comp_root.goto_uc,
        reload_uc=comp_root.reload_uc,
        eval_uc=comp_root.eval_uc,
        query_dom_uc=comp_root.query_dom_uc,
        page_text_uc=comp_root.page_text_uc,
        screenshot_selector_uc=comp_root.screenshot_selector_uc,
        clock=comp_root.clock,
        operation_log=comp_root.operation_log_adapter,
        resolve_session_id=comp_root.resolve_session_id,
        scenario_run_uc=comp_root.scenario_run_uc,
        scenario_real_run_uc=comp_root.scenario_real_run_uc,
        scenario_preflight_uc=comp_root.scenario_preflight_uc,
        scenario_real_preflight_uc=comp_root.scenario_real_preflight_uc,
        load_scenario_file=comp_root.load_scenario_file,
        write_scenario_evidence_bundle=comp_root.write_scenario_evidence_bundle,
        list_public_scenario_fixtures=comp_root.list_public_scenario_fixtures,
        scenario_validation_preflight_result=comp_root.scenario_validation_preflight_result,
        scenario_evidence_path_check=comp_root.ScenarioEvidencePathCheck,
        diagnostic_log=comp_root._diagnostic_log,
    )

    wrapper = create_mcp_server(deps)
    mcp_stdio = importlib.import_module("mcp.server.stdio")

    async def _run() -> None:
        async with mcp_stdio.stdio_server() as (read_stream, write_stream):
            await wrapper.mcp_server.run(
                read_stream,
                write_stream,
                wrapper.mcp_server.create_initialization_options(),
            )

    asyncio.run(_run())
