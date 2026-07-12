"""CLI entry point — Click-based command group.

Imports from composition_root only (never adapters, presenter, or domain directly).
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import click

from aiyes.cli.composition_root import (  # noqa: F401
    __version__,
    action_uc,
    build_profile_selection_event,
    classified_failure_count,
    clipboard_uc,
    clock,
    compound_do_uc,
    diff_uc,
    doctor_uc,
    debug_bundle_uc,
    _diagnostic_log,
    eval_uc,
    find_uc,
    format_action,
    format_clipboard_read,
    format_clipboard_write,
    format_debug_bundle,
    format_diff,
    format_do,
    format_doctor,
    format_eval_result,
    format_gesture_result,
    format_goto_result,
    format_mcp_manifest,
    format_menu_result,
    format_page_text_result,
    format_query_dom_result,
    format_reload_result,
    format_screenshot_selector_result,
    format_inspect_result,
    format_metrics,
    format_navigate_result,
    format_prune,
    format_screenshot,
    format_session_capabilities,
    format_session_list,
    format_session_resize,
    format_session_start,
    format_session_stop,
    format_scenario_fixtures,
    format_scenario_preflight,
    format_scenario_run,
    format_scenario_validation_errors,
    format_status_ok,
    format_system_error,
    format_find_nodes,
    format_wait,
    format_reactive_wait,
    format_wait_stable,
    gesture_uc,
    goto_uc,
    inspect_uc,
    key_uc,
    load_scenario_file,
    list_public_scenario_fixtures,
    menu_uc,
    metrics_uc,
    mouse_uc,
    navigate_uc,
    page_text_uc,
    query_dom_uc,
    reload_uc,
    operation_log_adapter,
    prune_uc,
    resolve_session_id,
    screenshot_uc,
    screenshot_selector_uc,
    ScenarioEvidencePathCheck,
    scenario_preflight_uc,
    scenario_real_preflight_uc,
    scenario_real_run_uc,
    scenario_run_uc,
    scenario_validation_preflight_result,
    session_list_uc,
    session_capabilities_uc,
    session_resize_uc,
    session_start_uc,
    session_stop_uc,
    type_text_uc,
    OperationRecord,
    start_worker_for_session,
    stop_worker,
    wait_stable_uc,
    wait_uc,
    reactive_wait_uc,
    write_scenario_evidence_bundle,
)


# ─── Instrumentation helper ────────────────────────────────────────


def _log_operation(
    cmd: str,
    session_id: str,
    t0: float,
    exit_code: int,
    error: str = "",
) -> None:
    """Construct and append an OperationRecord. Swallows all exceptions."""
    try:
        t1 = clock.now()
        duration_ms = (t1 - t0) * 1000.0
        record = OperationRecord(
            timestamp=t0,
            session_id=session_id,
            command=cmd,
            duration_ms=duration_ms,
            exit_code=exit_code,
            error=error,
        )
        operation_log_adapter.append(record)
    except Exception:
        pass


def _emit_profile_selection(result: Any, profile: str) -> None:
    """Emit exactly one LE-02 evidence.profile.selected event (boundary).

    A10-CRIT-004: the sole emission point for the scenario-run command. Builds
    the LE-02 payload via the pure domain shaper and drives the production
    diagnostic sink once, even when --evidence-dir also wrote a bundle. The sink
    owns fail-open; references to the module-level ``_diagnostic_log`` so tests
    can rebind a fresh production sink.

    Fail-open (FC-OBS-03): a None sink is a no-op; any exception from emit_event
    is swallowed so the CLI command is never blocked by a diagnostic failure.
    """
    if _diagnostic_log is None:
        return
    try:
        raw_steps = [dataclasses.asdict(step) for step in result.steps]
        preserved = classified_failure_count(raw_steps)
        selected = profile if profile in ("compact", "deep") else "compact"
        _diagnostic_log.emit_event(build_profile_selection_event(selected, preserved))
    except Exception:
        # Fail-open: emission must never affect the CLI command result (FC-OBS-03).
        pass


CLI_HELP = """
aieyes is a local deterministic tool that gives AI tools eyes and hands for Linux and Android GUI inspection and control.

It does not reason.
It does not plan workflows.
It reads GUI state and drives input reliably.
""".strip()

CLI_EPILOG = """
\b
Common loop:
  session start -- <command>
  inspect -> find/action/mouse/key/type -> verify

\b
App-side inspectability rules:
  expose accessible names
  expose roles, states, values, and actions through AT-SPI or AccessKit
  keep focus and state changes observable
  do not hide important UI outside the accessibility tree

\b
Android inspectability rules:
  set content-description on all interactive views
  provide text labels for ImageButton and ImageView widgets
  keep accessibility events firing for state changes
  Compose: use Modifier.semantics / Modifier.testTag for stable IDs
  View: assign resource-id and contentDescription for stable identification
""".strip()


def _get_session_start_uc():
    """Get the session start use case. Indirection for test mocking."""
    return session_start_uc


# ─── Top-level group ─────────────────────────────────────────────────


@click.group("aieyes", help=CLI_HELP, epilog=CLI_EPILOG)
@click.version_option(version=__version__, prog_name="aieyes")
def cli() -> None:
    """Top-level CLI group."""
    pass


_MCP_MANIFEST_KEYS = (
    "identity",
    "non_goals",
    "mcp",
    "common_loop",
    "capabilities",
    "backends",
    "inspectability_requirements",
)


def _build_mcp_manifest() -> Dict[str, Any]:
    """Build machine-readable capability disclosure for AI tools."""
    return {
        "identity": {
            "name": "aieyes",
            "runtime_model": "local-cli",
            "reasoning": "external",
            "description": (
                "Local deterministic eyes-and-hands tool for Linux and Android "
                "GUI inspection and control."
            ),
        },
        "non_goals": [
            "No built-in reasoning",
            "No workflow orchestration",
            "MCP server is optional (install with [mcp] extra)",
        ],
        "mcp": {
            "server": True,
            "transport": "stdio",
            "command": "aieyes-mcp",
        },
        "trust_boundary": {
            "scope": "trusted-local-stdio",
            "transport": "stdio",
            "sandbox": False,
            "operator_model": "Trusted local user and trusted local agent client.",
            "network_exposure": "Do not expose through remote servers, sockets, bridges, or untrusted automation gateways.",
            "warning": (
                "AIYES is a trusted local GUI control tool, not a sandbox. "
                "Tools can observe and control applications running as your user."
            ),
        },
        "common_loop": [
            "session start -- <command>",
            "inspect",
            "find/action/mouse/key/type",
            "verify with inspect or wait",
        ],
        "capabilities": {
            "linux": {
                "session": ["start", "stop", "list", "resize", "status"],
                "inspect": [
                    "inspect",
                    "find",
                    "diff",
                    "wait",
                    "wait-reactive",
                    "wait-stable",
                    "detect-dialog",
                ],
                "control": [
                    "action",
                    "mouse",
                    "key",
                    "type",
                    "do",
                    "screenshot",
                    "clipboard",
                    "navigate",
                    "menu",
                ],
                "diagnostics": ["doctor"],
            },
            "android": {
                "session": ["start", "stop", "list", "status"],
                "inspect": [
                    "inspect",
                    "find",
                    "wait",
                    "wait-reactive",
                    "detect-dialog",
                ],
                "control": [
                    "action",
                    "mouse",
                    "key",
                    "type",
                    "do",
                    "screenshot",
                    "clipboard",
                    "gesture",
                    "navigate",
                ],
                "diagnostics": ["doctor"],
                "provider": "adb+uiautomator",
                "gesture": {
                    "status": "restricted",
                    "operations": ["pinch", "two-finger-scroll"],
                    "evidence": "No real multi-pointer smoke evidence is recorded.",
                    "limitation": (
                        "Implemented as best-effort concurrent adb shell input "
                        "swipe commands, without verified multi-pointer injection."
                    ),
                },
                "limitations": [
                    "Resize not available on Android.",
                    "Fewer states reported than Linux AT-SPI.",
                    "wait-reactive, wait-stable, and diff have restricted accuracy.",
                    "Pinch and two-finger gestures are restricted/best-effort.",
                ],
            },
        },
        "backends": {
            "linux": {
                "provider": "AT-SPI2 + xdotool + scrot",
                "description": "Full Linux desktop accessibility inspection.",
            },
            "android": {
                "provider": "adb+uiautomator",
                "description": (
                    "Android device inspection via adb. "
                    "Fewer capabilities than Linux. "
                    "Requires content-description on interactive views."
                ),
            },
        },
        "inspectability_requirements": [
            "Expose accessible names for important UI elements.",
            "Expose roles, states, values, and actions through AT-SPI or AccessKit.",
            "Keep focus and state changes observable in the accessibility tree.",
            "Do not hide important functionality outside the accessibility tree.",
            {
                "android": (
                    "Set content-description on all interactive Android views. "
                    "Provide text labels for ImageButton and ImageView widgets. "
                    "For Compose UI: use Modifier.semantics and Modifier.testTag "
                    "for stable node identification. "
                    "For View-based UI: assign android:contentDescription and "
                    "resource-id for stable element identification across sessions."
                ),
            },
        ],
    }


# ─── Session subgroup ────────────────────────────────────────────────


@cli.group("session")
def session_group() -> None:
    """Manage sessions."""
    pass


@session_group.command("start")
@click.option(
    "--resolution",
    default="1280x800",
    show_default=True,
    help="Display resolution (WxH).",
)
@click.option("--color-depth", default=24, type=int, help="Color depth.")
@click.option(
    "--wait",
    "wait_secs",
    default=2.0,
    type=float,
    help="Seconds to wait after app launch.",
)
@click.option("--name", default=None, help="Session name.")
@click.option(
    "--backend",
    default="linux",
    type=click.Choice(["linux", "android"]),
    show_default=True,
    help="Backend platform (linux or android).",
)
@click.option(
    "--device-serial",
    default=None,
    help="Android device serial (from adb devices). Required for android backend.",
)
@click.option(
    "--marionette",
    is_flag=True,
    default=False,
    help="Enable the Firefox Marionette DOM lens (firefox/linux only).",
)
@click.argument("command", nargs=-1, required=True)
def session_start(
    resolution: str,
    color_depth: int,
    wait_secs: float,
    name: Optional[str],
    backend: str,
    device_serial: Optional[str],
    marionette: bool,
    command: Tuple[str, ...],
) -> None:
    """Start a new session: -- <command> [args...]."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        uc = _get_session_start_uc()
        app_command = command[0]
        app_args = list(command[1:])
        session = uc.execute(
            app_command=app_command,
            app_args=app_args,
            resolution=resolution,
            color_depth=color_depth,
            wait=wait_secs,
            name=name,
            backend=backend,
            device_serial=device_serial,
            marionette=marionette,
        )
        sid = session.session_id
        # Start persistent worker (failure is non-fatal)
        try:
            start_worker_for_session(session)
        except Exception:
            pass  # Worker failure must not fail session start
        click.echo(format_session_start(session))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session start", sid, t0, exit_code, error)


@session_group.command("stop")
@click.option("--session", "session_id", default=None, help="Session ID to stop.")
def session_stop(session_id: Optional[str]) -> None:
    """Stop a session."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        stop_worker()  # Graceful worker shutdown before session stop
        result = session_stop_uc.execute(session_id=session_id)
        sid = result.session_id
        if session_id is not None:
            sid = session_id
        errors = list(result.errors) if result.errors else None
        click.echo(format_session_stop(result.status, result.session_id, errors=errors))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        if session_id is not None:
            sid = session_id
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session stop", sid, t0, exit_code, error)


@session_group.command("list")
@click.option(
    "--active-only",
    is_flag=True,
    default=False,
    help="Show only sessions that are currently active.",
)
def session_list(active_only: bool) -> None:
    """List all sessions."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        entries = session_list_uc.execute(active_only=active_only)
        result_list = [dataclasses.asdict(e) for e in entries]
        click.echo(format_session_list(result_list))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session list", "", t0, exit_code, error)


@session_group.command("capabilities")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Run live backend probes when supported.",
)
def session_capabilities_cmd(session_id: Optional[str], live: bool) -> None:
    """Report backend capabilities for a session."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = session_capabilities_uc.execute(session_id=sid, live=live)
        click.echo(format_session_capabilities(result))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session capabilities", sid, t0, exit_code, error)


@session_group.command("resize")
@click.argument("resolution")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--settle", default=0.5, type=float, help="Settle delay after resize.")
def session_resize_cmd(
    resolution: str,
    session_id: Optional[str],
    settle: float,
) -> None:
    """Resize session display to RESOLUTION (WxH)."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = session_resize_uc.execute(
            session_id=sid,
            resolution=resolution,
            settle=settle,
        )
        click.echo(format_session_resize(result.status, result.resolution))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session resize", sid, t0, exit_code, error)


# ─── Inspect ──────────────────────────────────────────────────────────


@cli.command("inspect")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--no-screenshot", is_flag=True, default=False, help="Skip screenshot.")
@click.option("--no-tree", is_flag=True, default=False, help="Skip tree.")
@click.option("--tree-depth", default=None, type=int, help="Max tree depth.")
@click.option("--no-prune", is_flag=True, default=False, help="Disable tree pruning.")
@click.option(
    "--screenshot-base64",
    is_flag=True,
    default=False,
    help="Return screenshot as base64 in output.",
)
@click.option(
    "--focus-window",
    default=None,
    help="Focus a specific window by title before inspecting.",
)
def inspect_cmd(
    session_id: Optional[str],
    no_screenshot: bool,
    no_tree: bool,
    tree_depth: Optional[int],
    no_prune: bool,
    screenshot_base64: bool,
    focus_window: Optional[str],
) -> None:
    """Inspect the current state: tree + screenshot."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = inspect_uc.execute(
            session_id=sid,
            no_screenshot=no_screenshot,
            no_tree=no_tree,
            tree_depth=tree_depth,
            no_prune=no_prune,
            screenshot_base64=screenshot_base64,
            focus_window=focus_window,
        )

        click.echo(
            format_inspect_result(
                tree=result.tree,
                screenshot=result.screenshot,
                timestamp=result.timestamp,
                screenshot_base64=result.screenshot_base64,
                screenshot_data=result.screenshot_data,
                diagnostics=result.diagnostics,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("inspect", sid, t0, exit_code, error)


# ─── Diff ────────────────────────────────────────────────────────────


@cli.command("diff")
@click.option("--session", "session_id", default=None, help="Session ID.")
def diff_cmd(session_id: Optional[str]) -> None:
    """Compare stored tree against live state."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = diff_uc.execute(session_id=sid)
        click.echo(format_diff(result))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("diff", sid, t0, exit_code, error)


# ─── Find ─────────────────────────────────────────────────────────────


@cli.command("find")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--state", default=None, help="Filter by state.")
@click.option(
    "--within-name",
    default=None,
    help="Restrict search to descendants of an ancestor matching this name pattern.",
)
@click.option(
    "--within-role",
    default=None,
    help="Restrict search to descendants of an ancestor matching this role "
    "(used with --within-name).",
)
@click.option(
    "--resource-id",
    "resource_id",
    default=None,
    help="Match nodes by EXACT Android resource-id (full-string, not substring "
    "or regex). Android-only; ignored on Linux/AT-SPI nodes (which have none).",
)
@click.argument("role")
@click.argument("name_pattern", default=None, required=False)
def find_cmd(
    session_id: Optional[str],
    state: Optional[str],
    within_name: Optional[str],
    within_role: Optional[str],
    resource_id: Optional[str],
    role: str,
    name_pattern: Optional[str],
) -> None:
    """Find nodes matching role [name_pattern]."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        results = find_uc.execute(
            session_id=sid,
            role=role,
            name_pattern=name_pattern,
            state=state,
            within_role=within_role,
            within_name=within_name,
            resource_id=resource_id,
        )
        click.echo(format_find_nodes(results))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("find", sid, t0, exit_code, error)


# ─── Screenshot ───────────────────────────────────────────────────────


def _parse_region(ctx, param, value):
    """Parse --region X,Y,W,H into a tuple of 4 ints."""
    if value is None:
        return None
    try:
        parts = [int(p) for p in value.split(",")]
        if len(parts) != 4:
            raise ValueError("need exactly 4 values")
        return tuple(parts)
    except (ValueError, AttributeError) as exc:
        raise click.BadParameter(
            f"Must be X,Y,W,H (4 comma-separated integers): {exc}"
        ) from exc


@cli.command("screenshot")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--output", default=None, help="Output file path.")
@click.option("--base64", is_flag=True, default=False, help="Return base64 encoded.")
@click.option(
    "--region",
    default=None,
    callback=_parse_region,
    expose_value=True,
    is_eager=False,
    help="Crop to X,Y,W,H rectangle.",
)
@click.option("--node", "node_id", default=None, help="Crop to node bounding box.")
def screenshot_cmd(
    session_id: Optional[str],
    output: Optional[str],
    base64: bool,
    region: Optional[Tuple],
    node_id: Optional[str],
) -> None:
    """Take a screenshot."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = screenshot_uc.execute(
            session_id=sid,
            output_path=output,
            base64=base64,
            region=region,
            node_id=node_id,
        )
        click.echo(
            format_screenshot(
                path=result.path,
                data=result.data,
                width=result.width,
                height=result.height,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("screenshot", sid, t0, exit_code, error)


# ─── Action ───────────────────────────────────────────────────────────


@cli.command("action")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("node_id")
@click.argument("action_name")
@click.argument("value", default=None, required=False)
def action_cmd(
    session_id: Optional[str],
    node_id: str,
    action_name: str,
    value: Optional[str],
) -> None:
    """Execute an action on a node."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = action_uc.execute(
            session_id=sid,
            node_id=node_id,
            action_name=action_name,
            value=value,
        )
        avail = list(result.available_actions) if result.available_actions else None
        states = list(result.node_states) if result.node_states else None
        click.echo(
            format_action(
                status=result.status,
                action=result.action,
                target=result.target,
                reason=result.reason,
                available_actions=avail,
                node_value=result.node_value,
                node_states=states,
                action_method=result.action_method,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("action", sid, t0, exit_code, error)


# ─── Mouse subgroup ──────────────────────────────────────────────────


@cli.group("mouse")
def mouse_group() -> None:
    """Mouse control commands."""
    pass


@mouse_group.command("move")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("x", type=int)
@click.argument("y", type=int)
def mouse_move(session_id: Optional[str], x: int, y: int) -> None:
    """Move mouse to (x, y)."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        mouse_uc.move(sid, x, y)
        click.echo(format_status_ok())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("mouse move", sid, t0, exit_code, error)


@mouse_group.command(
    "click",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--button", default="left", help="Mouse button (left/middle/right).")
@click.option("--x", type=int, default=None, help="X coordinate.")
@click.option("--y", type=int, default=None, help="Y coordinate.")
@click.pass_context
def mouse_click_cmd(
    ctx: click.Context,
    session_id: Optional[str],
    button: str,
    x: Optional[int],
    y: Optional[int],
) -> None:
    """Click at (x, y) or current position.

    Coordinates can be given as positional arguments (mouse click 540 960)
    or as named options (mouse click --x 540 --y 960).
    """
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        # Resolve dual-mode coordinates: positional (ctx.args) vs named (--x/--y)
        pos_args = ctx.args
        if pos_args and (x is not None or y is not None):
            raise click.UsageError(
                "Cannot combine positional coordinates with --x/--y options."
            )
        if pos_args:
            if len(pos_args) == 2:
                try:
                    x, y = int(pos_args[0]), int(pos_args[1])
                except ValueError:
                    raise click.UsageError("Coordinates must be integers.")
            else:
                raise click.UsageError(
                    f"Expected 0 or 2 positional coordinates, got {len(pos_args)}."
                )
        # Validate: if one named coord given, both must be given
        if (x is None) != (y is None):
            raise click.UsageError(
                "Both --x and --y are required when using named coordinates."
            )

        sid = resolve_session_id(session_id)
        mouse_uc.click(sid, x, y, button)
        click.echo(format_status_ok())
    except click.UsageError:
        raise
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("mouse click", sid, t0, exit_code, error)


@mouse_group.command("drag")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("x1", type=int)
@click.argument("y1", type=int)
@click.argument("x2", type=int)
@click.argument("y2", type=int)
def mouse_drag_cmd(
    session_id: Optional[str],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> None:
    """Drag from (x1, y1) to (x2, y2)."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        mouse_uc.drag(sid, x1, y1, x2, y2)
        click.echo(format_status_ok())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("mouse drag", sid, t0, exit_code, error)


@cli.command("swipe")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option(
    "--duration-ms",
    "duration_ms",
    default=300,
    type=int,
    help="Swipe duration in milliseconds.",
)
@click.argument("x1", type=int)
@click.argument("y1", type=int)
@click.argument("x2", type=int)
@click.argument("y2", type=int)
def swipe_cmd(
    session_id: Optional[str],
    duration_ms: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> None:
    """Single-finger swipe from (x1, y1) to (x2, y2) over duration_ms.

    On Android this is the natural list-scroll gesture. On Linux the
    swipe routes through a mouse-drag substitute because Xvfb has no
    touch input.
    """
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        gesture_uc.swipe(sid, x1, y1, x2, y2, duration_ms)
        click.echo(format_status_ok())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("swipe", sid, t0, exit_code, error)


@mouse_group.command("scroll")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("direction")
@click.argument("amount", type=int, default=3, required=False)
def mouse_scroll_cmd(
    session_id: Optional[str],
    direction: str,
    amount: int,
) -> None:
    """Scroll in direction by amount."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        mouse_uc.scroll(sid, direction, amount)
        click.echo(format_status_ok())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("mouse scroll", sid, t0, exit_code, error)


# ─── Key ──────────────────────────────────────────────────────────────


@cli.command("key")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("keys", nargs=-1, required=True)
def key_cmd(session_id: Optional[str], keys: Tuple[str, ...]) -> None:
    """Send key events."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        key_uc.execute(sid, list(keys))
        click.echo(format_status_ok())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("key", sid, t0, exit_code, error)


# ─── Type ─────────────────────────────────────────────────────────────


@cli.command("type")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option(
    "--delay",
    "delay_ms",
    default=0,
    type=int,
    help="Inter-character delay in milliseconds. Android uses 20 ms by default to prevent character dropping. Pass 0 for no override (Linux has no delay by default).",
)
@click.argument("text")
def type_cmd(session_id: Optional[str], delay_ms: int, text: str) -> None:
    """Type text character by character."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        type_text_uc.execute(sid, text, delay_ms=delay_ms)
        click.echo(format_status_ok())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("type", sid, t0, exit_code, error)


# ─── Wait ─────────────────────────────────────────────────────────────


@cli.command("wait")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--timeout", default=30.0, type=float, help="Max wait time in seconds.")
@click.option("--state", default=None, help="Required state.")
@click.option(
    "--absent", is_flag=True, default=False, help="Wait for node to disappear."
)
@click.option(
    "--transient",
    is_flag=True,
    default=False,
    help="Detect transient elements (toasts/snackbars) that appear then disappear.",
)
@click.argument("role")
@click.argument("name_pattern", default=None, required=False)
def wait_cmd(
    session_id: Optional[str],
    timeout: float,
    state: Optional[str],
    absent: bool,
    transient: bool,
    role: str,
    name_pattern: Optional[str],
) -> None:
    """Wait for a node matching role [name_pattern]."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = wait_uc.execute(
            session_id=sid,
            role=role,
            name_pattern=name_pattern,
            timeout=timeout,
            state=state,
            absent=absent,
            transient=transient,
        )
        click.echo(
            format_wait(
                found=result.found,
                timeout=result.timeout,
                node_id=result.id,
                transient=result.transient,
                role_drift=result.role_drift,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("wait", sid, t0, exit_code, error)


@cli.command("wait-reactive")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--timeout", default=10.0, type=float, help="Max wait time in seconds.")
@click.option(
    "--quiet",
    default=0.0,
    type=float,
    help="Reserved quiet period in seconds for future stability gating.",
)
@click.option(
    "--poll-interval",
    default=0.25,
    type=float,
    help="Polling interval for non-native reactive sources.",
)
@click.argument(
    "condition",
    type=click.Choice(
        [
            "screen-change",
            "node-appears",
            "node-disappears",
            "focus-change",
            "app-change",
        ]
    ),
)
@click.argument("name_pattern", default=None, required=False)
def wait_reactive_cmd(
    session_id: Optional[str],
    timeout: float,
    quiet: float,
    poll_interval: float,
    condition: str,
    name_pattern: Optional[str],
) -> None:
    """Wait for a backend-neutral GUI condition."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = reactive_wait_uc.execute(
            session_id=sid,
            condition=condition,
            name_pattern=name_pattern,
            timeout=timeout,
            quiet=quiet,
            poll_interval=poll_interval,
        )
        click.echo(format_reactive_wait(result))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("wait-reactive", sid, t0, exit_code, error)


# ─── Wait-Stable ─────────────────────────────────────────────────────


@cli.command("wait-stable")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option("--timeout", default=10.0, type=float, help="Max wait time in seconds.")
@click.option("--interval", default=0.5, type=float, help="Poll interval in seconds.")
@click.option(
    "--consecutive", default=3, type=int, help="Consecutive stable polls required."
)
@click.option(
    "--tolerance",
    default=0,
    type=int,
    help="Max node differences allowed while still considering tree stable.",
)
@click.option(
    "--ignore-node",
    "ignore_nodes",
    multiple=True,
    help="Node ID to exclude from stability check (repeatable).",
)
def wait_stable_cmd(
    session_id: Optional[str],
    timeout: float,
    interval: float,
    consecutive: int,
    tolerance: int,
    ignore_nodes: tuple,
) -> None:
    """Wait for the accessibility tree to become structurally stable."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = wait_stable_uc.execute(
            session_id=sid,
            timeout=timeout,
            poll_interval=interval,
            consecutive=consecutive,
            tolerance=tolerance,
            ignore_ids=frozenset(ignore_nodes),
        )
        click.echo(
            format_wait_stable(
                stable=result.stable,
                timeout=result.timeout,
                polls=result.polls,
                changes=result.changes,
                comparison_mode=result.comparison_mode,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("wait-stable", sid, t0, exit_code, error)


# ─── Do (compound find+action+verify) ────────────────────────────────


@cli.command("do")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.option(
    "--role",
    "role",
    required=True,
    help="Accessibility role to match (e.g. View, Button, push_button). Use '*' for any role.",
)
@click.option(
    "--name",
    "name_pattern",
    default=None,
    help="Substring to match in node name (case-insensitive, whitespace-normalized). "
    "E.g. 'Home' matches 'Home\\nTab 1 of 4'.",
)
@click.option("--action", "action_name", required=True, help="Action to execute.")
@click.option("--verify", is_flag=True, default=False, help="Verify tree after action.")
@click.option("--value", default=None, help="Value to pass to action.")
@click.option(
    "--timeout",
    default=None,
    type=float,
    help="Timeout in seconds for find phase.",
)
def do_cmd(
    session_id: Optional[str],
    role: str,
    name_pattern: Optional[str],
    action_name: str,
    verify: bool,
    value: Optional[str],
    timeout: Optional[float],
) -> None:
    """Compound find + action + optional verify."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = compound_do_uc.execute(
            session_id=sid,
            role=role,
            name_pattern=name_pattern,
            action_name=action_name,
            verify=verify,
            value=value,
            timeout=timeout,
        )
        click.echo(format_do(result))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("do", sid, t0, exit_code, error)


# ─── Doctor ───────────────────────────────────────────────────────────


@cli.command("doctor")
def doctor_cmd() -> None:
    """Check system dependencies."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        results = doctor_uc.execute()
        result_dicts = [
            {**dataclasses.asdict(r), "category": r.category} for r in results
        ]
        click.echo(format_doctor(result_dicts))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("doctor", "", t0, exit_code, error)


@cli.command("debug-bundle")
@click.option("--session", "session_id", default=None, help="Session ID.")
def debug_bundle_cmd(session_id: Optional[str]) -> None:
    """Collect a redacted diagnostic debug bundle."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        bundle = debug_bundle_uc.execute(session_id=sid, environ=os.environ)
        click.echo(format_debug_bundle(bundle))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("debug-bundle", sid, t0, exit_code, error)


@cli.command("mcp-manifest")
def mcp_manifest_cmd() -> None:
    """Return machine-readable capability disclosure for AI tools."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        click.echo(format_mcp_manifest(_build_mcp_manifest()))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("mcp-manifest", "", t0, exit_code, error)


# ─── Session Metrics ──────────────────────────────────────────────────


@session_group.command("metrics")
@click.option("--session", "session_id", default=None, help="Session ID.")
def session_metrics(session_id: Optional[str]) -> None:
    """Show operation metrics."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        result = metrics_uc.execute(session_id=session_id)
        click.echo(format_metrics(result))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session metrics", "", t0, exit_code, error)


# ─── Session Prune ────────────────────────────────────────────────────


@session_group.command("prune")
@click.option(
    "--older-than",
    "older_than",
    default=72.0,
    type=float,
    show_default=True,
    help="Max age in hours for sessions to keep.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be pruned without deleting.",
)
def session_prune(older_than: float, dry_run: bool) -> None:
    """Prune stale session directories."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        result = prune_uc.execute(max_age_hours=older_than, dry_run=dry_run)
        click.echo(format_prune(result))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session prune", "", t0, exit_code, error)


# ─── Session Status ──────────────────────────────────────────────────


@session_group.command("status")
@click.option("--session", "session_id", default=None, help="Session ID.")
def session_status_cmd(session_id: Optional[str]) -> None:
    """Check session liveness (app alive, foreground, display)."""
    from aiyes.cli.composition_root import (
        format_session_status as _fmt_ss,
        session_status_uc as _ss_uc,
    )

    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = _ss_uc.execute(session_id=sid)
        click.echo(
            _fmt_ss(
                app_alive=result.app_alive,
                app_foreground=result.app_foreground,
                display_alive=result.display_alive,
                marionette_enabled=result.marionette_enabled,
                marionette_port=result.marionette_port,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("session status", sid, t0, exit_code, error)


# ─── Detect Dialog ───────────────────────────────────────────────────


@cli.command("detect-dialog")
@click.option("--session", "session_id", default=None, help="Session ID.")
def detect_dialog_cmd(session_id: Optional[str]) -> None:
    """Check if a new dialog/window appeared since last inspect."""
    from aiyes.cli.composition_root import (
        format_detect_dialog as _fmt_dd,
        detect_dialog_uc as _dd_uc,
    )

    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = _dd_uc.execute(session_id=sid)
        click.echo(
            _fmt_dd(
                dialog_detected=result.dialog_detected,
                window_name=result.window_name,
                window_role=result.window_role,
                error=result.error,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("detect-dialog", sid, t0, exit_code, error)


# ─── Clipboard subgroup (GAP-03) ─────────────────────────────────────


@cli.group("clipboard")
def clipboard_group() -> None:
    """Clipboard read/write commands."""
    pass


@clipboard_group.command("read")
@click.option("--session", "session_id", default=None, help="Session ID.")
def clipboard_read_cmd(session_id: Optional[str]) -> None:
    """Read clipboard contents."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = clipboard_uc.read(session_id=sid)
        click.echo(format_clipboard_read(result.text))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("clipboard read", sid, t0, exit_code, error)


@clipboard_group.command("write")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("text")
def clipboard_write_cmd(session_id: Optional[str], text: str) -> None:
    """Write text to clipboard."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        clipboard_uc.write(session_id=sid, text=text)
        click.echo(format_clipboard_write())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("clipboard write", sid, t0, exit_code, error)


# ─── Gesture subgroup (GAP-07) ──────────────────────────────────────


@cli.group("gesture")
def gesture_group() -> None:
    """Restricted/best-effort gesture commands (Android only)."""
    pass


@gesture_group.command("pinch")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("x", type=int)
@click.argument("y", type=int)
@click.argument("scale_factor", type=float)
def gesture_pinch_cmd(
    session_id: Optional[str], x: int, y: int, scale_factor: float
) -> None:
    """Pinch gesture at (x, y) with scale factor."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        gesture_uc.pinch(session_id=sid, x=x, y=y, scale_factor=scale_factor)
        click.echo(format_gesture_result())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("gesture pinch", sid, t0, exit_code, error)


@gesture_group.command("two-finger-scroll")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("x", type=int)
@click.argument("y", type=int)
@click.argument("direction")
@click.argument("amount", type=int, default=3, required=False)
def gesture_two_finger_scroll_cmd(
    session_id: Optional[str],
    x: int,
    y: int,
    direction: str,
    amount: int,
) -> None:
    """Two-finger scroll at (x, y) in direction."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        gesture_uc.two_finger_scroll(
            session_id=sid, x=x, y=y, direction=direction, amount=amount
        )
        click.echo(format_gesture_result())
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("gesture two-finger-scroll", sid, t0, exit_code, error)


# ─── Navigate (GAP-08) ──────────────────────────────────────────────


@cli.command("navigate")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("action", type=click.Choice(["back", "home", "recent"]))
def navigate_cmd(session_id: Optional[str], action: str) -> None:
    """Platform-abstracted navigation: back, home, recent."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = navigate_uc.execute(session_id=sid, action=action)
        click.echo(format_navigate_result(status=result.status, warning=result.warning))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("navigate", sid, t0, exit_code, error)


# ─── Goto / Reload (AIYES-112) ──────────────────────────────────────


@cli.command("goto")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("url")
def goto_cmd(session_id: Optional[str], url: str) -> None:
    """Navigate a linux browser session to a URL via address-bar automation."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = goto_uc.execute(session_id=sid, url=url)
        click.echo(
            format_goto_result(
                status=result.status,
                session_id=result.session_id,
                action=result.action,
                url=result.url,
                reason=result.reason,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("goto", sid, t0, exit_code, error)


@cli.command("reload")
@click.option("--session", "session_id", default=None, help="Session ID.")
def reload_cmd(session_id: Optional[str]) -> None:
    """Cache-bypassing hard reload of the current page (linux browser session)."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = reload_uc.execute(session_id=sid)
        click.echo(
            format_reload_result(
                status=result.status,
                session_id=result.session_id,
                action=result.action,
                reason=result.reason,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("reload", sid, t0, exit_code, error)


# ─── Marionette DOM lens (AIYES-117) ────────────────────────────────


@cli.command("eval")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("script")
def eval_cmd(session_id: Optional[str], script: str) -> None:
    """Run operator JavaScript in the Firefox content context and return its value."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = eval_uc.execute(session_id=sid, script=script)
        click.echo(
            format_eval_result(
                status=result.status,
                session_id=result.session_id,
                action=result.action,
                value=result.value,
                reason=result.reason,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("eval", sid, t0, exit_code, error)


@cli.command("query-dom")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("selector")
def query_dom_cmd(session_id: Optional[str], selector: str) -> None:
    """Return a measured, structured view of the elements matching a CSS selector."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = query_dom_uc.execute(session_id=sid, selector=selector)
        click.echo(
            format_query_dom_result(
                status=result.status,
                session_id=result.session_id,
                selector=result.selector,
                count=result.count,
                nodes=result.nodes,
                truncated=result.truncated,
                reason=result.reason,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("query-dom", sid, t0, exit_code, error)


@cli.command("page-text")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("selector", required=False, default=None)
def page_text_cmd(session_id: Optional[str], selector: Optional[str]) -> None:
    """Read the page's rendered innerText (whole body, or a scoped CSS selector)."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = page_text_uc.execute(session_id=sid, selector=selector)
        click.echo(
            format_page_text_result(
                status=result.status,
                session_id=result.session_id,
                selector=result.selector,
                text=result.text,
                found=result.found,
                reason=result.reason,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("page-text", sid, t0, exit_code, error)


@cli.command("screenshot-selector")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("selector")
def screenshot_selector_cmd(session_id: Optional[str], selector: str) -> None:
    """Capture and store a native screenshot of a CSS-selected element."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = screenshot_selector_uc.execute(session_id=sid, selector=selector)
        click.echo(
            format_screenshot_selector_result(
                status=result.status,
                session_id=result.session_id,
                selector=result.selector,
                path=result.path,
                width=result.width,
                height=result.height,
                reason=result.reason,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("screenshot-selector", sid, t0, exit_code, error)


# ─── Menu (GAP-11) ──────────────────────────────────────────────────


@cli.command("menu")
@click.option("--session", "session_id", default=None, help="Session ID.")
@click.argument("menu_path")
def menu_cmd(session_id: Optional[str], menu_path: str) -> None:
    """Traverse menu by dot-separated path (e.g. File.Save)."""
    t0 = clock.now()
    sid = ""
    exit_code = 0
    error = ""
    try:
        sid = resolve_session_id(session_id)
        result = menu_uc.execute(session_id=sid, menu_path=menu_path)
        click.echo(
            format_menu_result(
                status=result.status,
                node_id=result.node_id,
                node_name=result.node_name,
            )
        )
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("menu", sid, t0, exit_code, error)


# ─── Scenario runner ────────────────────────────────────────────────


@cli.group("scenario")
def scenario_group() -> None:
    """Deterministic release scenario commands."""
    pass


@scenario_group.command("run")
@click.option(
    "--real",
    "real_execution",
    is_flag=True,
    help="Execute against real GUI backends instead of dry-run.",
)
@click.option(
    "--public-fixture",
    is_flag=True,
    help="Reject private/local references while loading the scenario.",
)
@click.option(
    "--evidence-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write a scenario evidence bundle to this directory.",
)
@click.option(
    "--profile",
    type=click.Choice(["compact", "deep"]),
    default="compact",
    help="Evidence detail profile: compact (default) or deep (raw tree detail).",
)
@click.argument(
    "scenario_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def scenario_run_cmd(
    scenario_path: Path,
    real_execution: bool,
    public_fixture: bool,
    evidence_dir: Optional[Path],
    profile: str,
) -> None:
    """Run a deterministic scenario document."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        loaded = load_scenario_file(scenario_path, public_fixture=public_fixture)
        if not loaded.ok or loaded.scenario is None:
            exit_code = 2
            click.echo(format_scenario_validation_errors(loaded.issues))
            sys.exit(2)
        runner = scenario_real_run_uc if real_execution else scenario_run_uc
        result = runner.execute(loaded.scenario)
        if evidence_dir is not None:
            write_scenario_evidence_bundle(evidence_dir, result, profile=profile)
        click.echo(format_scenario_run(result, profile=profile))
        # A10-CRIT-004: emit LE-02 EXACTLY ONCE at this command boundary, even
        # when --evidence-dir triggers both the bundle writer and the presenter.
        _emit_profile_selection(result, profile)
        if result.status != "passed":
            exit_code = 1
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("scenario run", "", t0, exit_code, error)


@scenario_group.command("preflight")
@click.option(
    "--real",
    "real_execution",
    is_flag=True,
    help="Preflight real GUI/device execution prerequisites; execution remains opt-in.",
)
@click.option(
    "--public-fixture",
    is_flag=True,
    help="Reject private/local references while loading the scenario.",
)
@click.option(
    "--evidence-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Validate a scenario evidence output directory without writing evidence.",
)
@click.argument(
    "scenario_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def scenario_preflight_cmd(
    scenario_path: Path,
    real_execution: bool,
    public_fixture: bool,
    evidence_dir: Optional[Path],
) -> None:
    """Check scenario readiness without executing steps."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        loaded = load_scenario_file(scenario_path, public_fixture=public_fixture)
        if not loaded.ok or loaded.scenario is None:
            result = scenario_validation_preflight_result(loaded.issues)
            click.echo(format_scenario_preflight(result))
            sys.exit(2)
        runner = scenario_real_preflight_uc if real_execution else scenario_preflight_uc
        result = runner.execute(
            loaded.scenario,
            real_execution=real_execution,
            evidence_dir=_cli_evidence_dir_check(evidence_dir),
        )
        click.echo(format_scenario_preflight(result))
        if result.status != "passed":
            exit_code = 1
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("scenario preflight", "", t0, exit_code, error)


@scenario_group.command("fixtures")
def scenario_fixtures_cmd() -> None:
    """List public deterministic scenario fixtures."""
    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        click.echo(format_scenario_fixtures(list_public_scenario_fixtures()))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("scenario fixtures", "", t0, exit_code, error)


def _cli_evidence_dir_check(evidence_dir: Optional[Path]) -> ScenarioEvidencePathCheck:
    if evidence_dir is None:
        return ScenarioEvidencePathCheck(status="not_requested")
    if evidence_dir.exists() and evidence_dir.is_file():
        return ScenarioEvidencePathCheck(
            status="failed",
            path=str(evidence_dir),
            reason="evidence_dir must not target an existing file",
        )
    return ScenarioEvidencePathCheck(status="passed", path=str(evidence_dir))


# ─── Help JSON ───────────────────────────────────────────────────────


@cli.command("help-json")
def help_json_cmd() -> None:
    """List all commands and their parameter schemas as JSON."""
    import importlib
    import json

    help_json_mod = importlib.import_module("aiyes.cli.help_json")

    t0 = clock.now()
    exit_code = 0
    error = ""
    try:
        data = help_json_mod.build_help_json(cli)
        click.echo(json.dumps(data, indent=2))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        click.echo(format_system_error(str(exc)), err=True)
        sys.exit(1)
    finally:
        _log_operation("help-json", "", t0, exit_code, error)


# ─── Module entry point ──────────────────────────────────────────────
if __name__ == "__main__":
    cli()
