"""Scenario action steps fail when their action does not report ok.

A scenario ``action`` step passes only when its action reports
``"status": "ok"``, unless the step says what it expects with the optional
``"expect"`` field:

* ``"ok"`` (the default, also when the field is absent): any other result fails
  the step with ``action_failed``;
* ``"refused"``: a negative check. The step passes only when the action was
  refused: the result's reason says the node is not enabled or the application
  declined the action. It fails with ``action_not_refused`` when the action went
  through, and with ``action_failed`` on any other error, also when the result
  lists the target's actions;
* ``"any"``: best effort. The step passes whatever the action reports, including
  a target that is not there, and keeps the action's error in its output; a
  failure of the setup or of the environment still fails it.

These tests pin the step outcome for every expect value and every kind of
action result, what a failed step records, the run result that follows (the
top-level code, the next-action message, the CLI exit code, the MCP result and
the evidence bundle), cleanup steps, the diagnostic event, the load-time
validation of ``"expect"``, behaviour that stays as it was, and the
documentation.

All ports, accessibles, devices and applications are in-memory doubles, so no
display, accessibility bus, device or toolkit is needed.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import json
import re
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from aiyes.adapters.android_action_adapter import AndroidActionAdapter
from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_dry_run_executor import ScenarioDryRunExecutor
from aiyes.adapters.scenario_evidence import write_scenario_evidence_bundle
from aiyes.adapters.scenario_loader import load_scenario_file
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.cli.main import cli
from aiyes.domain.scenario import (
    _VALID_STEP_KINDS,
    ReleaseScenario,
    ScenarioStep,
    validate_scenario_document,
)
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import ActionPortResult
from aiyes.domain.use_cases.action import ActionResult, ActionUseCase
from aiyes.domain.use_cases.compound_do import CompoundDoUseCase
from aiyes.domain.use_cases.scenario_preflight import (
    scenario_validation_preflight_result,
)
from aiyes.domain.use_cases.scenario_run import (
    ScenarioNextAction,
    ScenarioRunResult,
    ScenarioRunUseCase,
)

from tests.conftest import (
    FakeAccessibilityTree,
    FakeClock,
    FakeSessionRepository,
    FakeTreeStore,
    make_cli_runner,
)
from tests.test_aiyes126_declined_actions import (
    _DISABLED_STATES,
    _RAISE,
    _SESSION_ID,
    _FakeAccessible,
    _FakeStateSet,
    _RecordingUseCase,
    _android_session,
    _call_mcp,
    _do_tree,
    _invoke_cli,
    _repo,
    _run_worker,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXECUTOR_SRC = (
    _REPO_ROOT / "src" / "aiyes" / "adapters" / "scenario_use_case_executor.py"
)

_STEP = "press-save"
_NODE = "n_009"
_ANDROID_SESSION_ID = "android-test"

# Marks a step that carries no "expect" key at all.
_ABSENT = "<absent>"
_EXPECT_VALUES = [_ABSENT, "ok", "refused", "any"]

_AF = "action_failed"
_ANR = "action_not_refused"
_EMPTY_SOURCE = "empty_source_node_id"
_CLASSIFIED_EVENT = "scenario.diagnostic.failure_classified"

_NOT_ENABLED_REASON = f"Action 'click' not sent: node '{_NODE}' is not enabled"
_DECLINED_REASON = f"Action 'click' on node '{_NODE}' was declined by the application"
_NOT_AVAILABLE_REASON = f"Action 'click' not available on node '{_NODE}'"
_FOCUS_NOT_AVAILABLE_REASON = f"Action 'focus' not available on node '{_NODE}'"
_UNKNOWN_NODE_TEXT = "Unknown node_id: 'n_777' not found in stored tree"
_NO_SESSION_TEXT = "scenario step requires an active session"

_NOT_STORED = object()


# ═══════════════════════════════════════════════════════════════════════
# Doubles
# ═══════════════════════════════════════════════════════════════════════


class _ScriptedPort:
    """Action port answering each node from a script.

    ``script`` maps a node id to ``(success, available_actions, failure_reason)``;
    a node missing from it gets ``default``. Every request is recorded.
    """

    def __init__(
        self,
        script: Optional[Dict[str, Tuple[bool, Tuple[str, ...], Optional[str]]]] = None,
        default: Tuple[bool, Tuple[str, ...], Optional[str]] = (True, ("click",), None),
    ) -> None:
        self._script = dict(script or {})
        self._default = default
        self.calls: List[Tuple[str, str, Optional[str]]] = []

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        self.calls.append((node_id, action_name, value))
        success, actions, reason = self._script.get(node_id, self._default)
        return ActionPortResult(
            success=success, available_actions=tuple(actions), failure_reason=reason
        )


def _refusing_port(actions: Tuple[str, ...], reason: Optional[str]) -> _ScriptedPort:
    return _ScriptedPort(default=(False, actions, reason))


class _RaisingPort:
    """Action port whose every request raises, as a failing backend does."""

    def __init__(self, message: str) -> None:
        self._message = message

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        raise RuntimeError(self._message)


class _FixedResult:
    """Action dependency that returns the same result for every call."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self, **kwargs: Any) -> Any:
        return self._result


class _RaisingAction:
    """Action dependency that raises the same exception for every call."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def execute(self, **kwargs: Any) -> Any:
        raise self._exc


class _CountingAction:
    """Records every call to an action dependency and what it returned."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: List[Dict[str, Any]] = []
        self.results: List[Any] = []

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        result = self._inner.execute(**kwargs)
        self.results.append(result)
        return result


class _RecordingSink:
    """Diagnostic sink keeping every event."""

    def __init__(self) -> None:
        self.events: List[Any] = []

    def emit_event(self, event: Any) -> None:
        self.events.append(event)


class _SpyRunner:
    """Records every scenario handed to a runner, then runs it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: List[Any] = []

    def execute(self, scenario: Any, **kwargs: Any) -> Any:
        self.calls.append(scenario)
        return self._inner.execute(scenario, **kwargs)


class _PassingPreflight:
    """Preflight use case that finds every scenario ready."""

    def execute(self, scenario: Any, **kwargs: Any) -> Any:
        return dataclasses.replace(
            scenario_validation_preflight_result(()),
            scenario_id=scenario.id,
            status="passed",
            failure_code="",
            next_actions=(),
        )


class _RaisingSink:
    """Diagnostic sink that fails on every event."""

    def emit_event(self, event: Any) -> None:
        raise RuntimeError("diagnostic sink unavailable")


class _LiveTree:
    """Android tree adapter returning a fixed live tree."""

    def __init__(self, tree: AccessibilityTree) -> None:
        self._tree = tree

    def get_tree(self, session: Any) -> AccessibilityTree:
        return self._tree


class _InProcessWorker:
    """Persistent-worker double answering with the worker's own action entry point."""

    def __init__(self, node: Optional[_FakeAccessible]) -> None:
        self._node = node

    def is_alive(self) -> bool:
        return True

    def send(self, op: str, **kwargs: Any) -> Any:
        return _run_worker(self._node, kwargs["action_name"], kwargs.get("value"))


@dataclasses.dataclass
class _Rig:
    """A real executor over recording doubles."""

    executor: ScenarioUseCaseExecutor
    action: _CountingAction
    sink: Any
    session_start: _RecordingUseCase
    screenshot: _RecordingUseCase


def _rig(inner_action: Any, sink: Any = None, session_id: str = _SESSION_ID) -> _Rig:
    action = _CountingAction(inner_action)
    sink = _RecordingSink() if sink is None else sink
    session_start = _RecordingUseCase(SimpleNamespace(session_id=session_id))
    screenshot = _RecordingUseCase(SimpleNamespace(path="shot.png"))
    executor = ScenarioUseCaseExecutor(
        session_start=session_start,
        inspect=_RecordingUseCase(SimpleNamespace(tree={"roots": []})),
        find=_RecordingUseCase([]),
        action=action,
        type_text=_RecordingUseCase(SimpleNamespace(status="ok")),
        screenshot=screenshot,
        session_stop=_RecordingUseCase(SimpleNamespace(status="ok")),
        diagnostic_log=sink,
    )
    return _Rig(executor, action, sink, session_start, screenshot)


def _linux_uc(
    port: Any, stored_tree: Optional[AccessibilityTree] = None
) -> ActionUseCase:
    store = FakeTreeStore()
    if stored_tree is not None:
        store.save_tree(_SESSION_ID, stored_tree, None)
    return ActionUseCase(port, _repo(), store)


def _android_uc(adapter: AndroidActionAdapter) -> ActionUseCase:
    repo = FakeSessionRepository()
    repo.save(_android_session())
    store = FakeTreeStore()
    store.save_tree(_ANDROID_SESSION_ID, _do_tree(), None)
    return ActionUseCase(adapter, repo, store)


def _android_tree(
    actions: Tuple[str, ...], states: Tuple[str, ...]
) -> AccessibilityTree:
    target = Node(
        id=_NODE,
        role="android.widget.Button",
        name="Target",
        bounds=(10, 20, 90, 30),
        states=states,
        actions=actions,
    )
    frame = Node(
        id="n_001",
        role="android.widget.FrameLayout",
        name="",
        bounds=(0, 0, 640, 480),
        states=("enabled",),
        actions=(),
        children=(target,),
    )
    return AccessibilityTree(roots=(frame,))


def _plain(result: Any) -> Dict[str, Any]:
    """The fields an action returned, as JSON values, without unset ones."""
    fields = (
        dataclasses.asdict(result) if dataclasses.is_dataclass(result) else vars(result)
    )
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in fields.items()
        if value is not None
    }


def _marker(expect: Any) -> Dict[str, Any]:
    return {"expect": expect} if expect in ("refused", "any") else {}


def _with_expect(item: Dict[str, Any], expect: Any) -> Dict[str, Any]:
    step = dict(item)
    if expect != _ABSENT:
        step["expect"] = expect
    return step


def _step(item: Dict[str, Any]) -> ScenarioStep:
    return ScenarioStep(
        id=item["id"],
        kind=item["kind"],
        parameters={k: v for k, v in item.items() if k not in ("id", "kind")},
    )


def _start(rig: _Rig) -> None:
    rig.executor.execute(
        ScenarioStep(id="start", kind="start_session", parameters={"command": "probe"})
    )


def _action_doc(
    expect: Any = _ABSENT,
    step_id: str = _STEP,
    node_id: str = _NODE,
    **extra: Any,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {"id": step_id, "kind": "action", "node_id": node_id}
    item.setdefault("action", "click")
    item.update(extra)
    return _with_expect(item, expect)


def _document(
    steps: List[Dict[str, Any]],
    cleanup: Optional[List[Dict[str, Any]]] = None,
    target: str = "linux",
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "action-outcome",
        "title": "Action step outcome",
        "target": target,
        "prerequisites": [],
        "steps": [{"id": "start", "kind": "start_session", "command": "probe"}] + steps,
        "cleanup": (
            [{"id": "stop", "kind": "stop_session"}] if cleanup is None else cleanup
        ),
        "evidence_policy": {"required": ["steps"]},
    }


def _scenario(document: Dict[str, Any]) -> ReleaseScenario:
    loaded = validate_scenario_document(document)
    assert loaded.ok, [dataclasses.asdict(issue) for issue in loaded.issues]
    assert loaded.scenario is not None
    return loaded.scenario


def _real_runner(rig: _Rig) -> ScenarioRunUseCase:
    return ScenarioRunUseCase(executor=rig.executor, mode="real")


def _run_scenario(
    rig: _Rig,
    steps: List[Dict[str, Any]],
    cleanup: Optional[List[Dict[str, Any]]] = None,
) -> ScenarioRunResult:
    return _real_runner(rig).execute(_scenario(_document(steps, cleanup)))


def _record(run: Any, step_id: str, cleanup: bool = False) -> Any:
    """The run's record for a step, from a run result or its JSON payload."""
    if isinstance(run, dict):
        matches = [
            s
            for s in run["steps"]
            if s["step_id"] == step_id and s["cleanup"] == cleanup
        ]
    else:
        matches = [
            s for s in run.steps if s.step_id == step_id and s.cleanup == cleanup
        ]
    assert len(matches) == 1, f"expected one record for {step_id!r}"
    return matches[0]


def _write(
    tmp_path: Path, document: Dict[str, Any], name: str = "scenario.json"
) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run_cli(argv: List[str], **patched: Any) -> Any:
    runner = make_cli_runner()
    with ExitStack() as stack:
        stack.enter_context(patch("aiyes.cli.main.operation_log_adapter", MagicMock()))
        for name, value in patched.items():
            stack.enter_context(patch(f"aiyes.cli.main.{name}", value))
        return runner.invoke(cli, argv)


def _mcp_deps(**overrides: Any) -> ServerDependencies:
    fields: Dict[str, Any] = {
        name: MagicMock() for name in ServerDependencies.__dataclass_fields__
    }
    clock = MagicMock()
    clock.now.return_value = 1000.0
    fields.update(
        clock=clock,
        operation_log=MagicMock(),
        diagnostic_log=None,
        load_scenario_file=load_scenario_file,
        write_scenario_evidence_bundle=write_scenario_evidence_bundle,
        scenario_validation_preflight_result=scenario_validation_preflight_result,
    )
    fields.update(overrides)
    return ServerDependencies(**fields)


def _call_tool(
    deps: ServerDependencies, tool: str, arguments: Dict[str, Any]
) -> Tuple[bool, str]:
    async def call() -> Any:
        return await create_mcp_server(deps).call_tool(tool, arguments)

    result = asyncio.run(call())
    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
    return result.isError, text


# ═══════════════════════════════════════════════════════════════════════
# Step outcome for every expect value and every kind of action result
# ═══════════════════════════════════════════════════════════════════════

_FIND_SAVE = {"id": "f", "kind": "find", "role": "push_button", "name_pattern": "Save"}
_TYPE_HI = {"id": "typed", "kind": "type_text", "text": "hi"}
_STOP_EARLY = {"id": "stop-early", "kind": "stop_session"}


def _empty_find_source_output(pre: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "failure_code": _EMPTY_SOURCE,
        "source_step_id": "f",
        "source_step_kind": "find",
        "requested_selector": {
            "role": "push_button",
            "name_pattern": "Save",
            "state": None,
        },
        "selector_diagnostics": pre["f"]["selector_diagnostics"],
    }


@dataclasses.dataclass(frozen=True)
class _Variant:
    """One kind of action result, built from real use cases where possible.

    ``group`` names how the result counts: ``reported_ok``,
    ``refusal_confirmed`` (the reason says the node is not enabled or the
    application declined), ``not_a_refusal`` (the target lists actions but the
    reason gives no refusal cause: the action is not offered, or it is offered
    and failed), ``refusal_unconfirmed`` (no actions and no refusal cause, so
    the target may not have been there), ``unreadable`` (no status, or an
    unknown one),
    ``empty_source`` (the source step gave no node id; the action is not
    called), ``unknown_node`` (the node id is not in the stored tree) and
    ``cannot_attempt`` (no session, an invalid step, or a failing backend).
    """

    name: str
    group: str
    build: Callable[[], Any]
    params: Dict[str, Any]
    calls: int
    output: Callable[[Dict[str, Dict[str, Any]]], Dict[str, Any]] = lambda pre: {}
    error: str = ""
    start_session: bool = True
    pre_steps: Tuple[Dict[str, Any], ...] = ()


_TARGET = {"node_id": _NODE, "action": "click"}

_VARIANTS = [
    _Variant(
        "reported_ok",
        "reported_ok",
        lambda: _linux_uc(_ScriptedPort()),
        _TARGET,
        calls=1,
        output=lambda pre: {"status": "ok", "action": "click", "target": _NODE},
    ),
    _Variant(
        "not_enabled_with_actions",
        "refusal_confirmed",
        lambda: _linux_uc(_refusing_port(("click", "focus"), "not_enabled")),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": _NOT_ENABLED_REASON,
            "available_actions": ["click", "focus"],
        },
    ),
    _Variant(
        "not_enabled_without_actions",
        "refusal_confirmed",
        lambda: _linux_uc(_refusing_port((), "not_enabled")),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": _NOT_ENABLED_REASON,
            "available_actions": [],
        },
    ),
    _Variant(
        "declined_without_actions",
        "refusal_confirmed",
        lambda: _linux_uc(_refusing_port((), "declined")),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": _DECLINED_REASON,
            "available_actions": [],
        },
    ),
    _Variant(
        "not_offered_by_a_node_with_other_actions",
        "not_a_refusal",
        lambda: _linux_uc(_refusing_port(("press",), None)),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": _NOT_AVAILABLE_REASON,
            "available_actions": ["press"],
        },
    ),
    _Variant(
        "offered_action_without_cause",
        "not_a_refusal",
        lambda: _linux_uc(_refusing_port(("click", "focus"), None)),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": _NOT_AVAILABLE_REASON,
            "available_actions": ["click", "focus"],
        },
    ),
    _Variant(
        "unrecognised_reason_with_actions",
        "not_a_refusal",
        lambda: _FixedResult(
            SimpleNamespace(
                status="error",
                action="click",
                target=_NODE,
                reason="something else",
                available_actions=["click"],
            )
        ),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": "something else",
            "available_actions": ["click"],
        },
    ),
    _Variant(
        "not_available_without_actions",
        "refusal_unconfirmed",
        lambda: _linux_uc(_refusing_port((), None)),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": _NOT_AVAILABLE_REASON,
            "available_actions": [],
        },
    ),
    _Variant(
        "error_without_reason_or_actions",
        "refusal_unconfirmed",
        lambda: _FixedResult(
            SimpleNamespace(status="error", action="click", target=_NODE)
        ),
        _TARGET,
        calls=1,
        output=lambda pre: {"status": "error", "action": "click", "target": _NODE},
    ),
    _Variant(
        "result_without_status",
        "unreadable",
        lambda: _FixedResult(SimpleNamespace(action="click", target=_NODE)),
        _TARGET,
        calls=1,
        output=lambda pre: {"action": "click", "target": _NODE},
    ),
    _Variant(
        "result_with_unknown_status",
        "unreadable",
        lambda: _FixedResult(
            SimpleNamespace(status="pending", action="click", target=_NODE)
        ),
        _TARGET,
        calls=1,
        output=lambda pre: {"status": "pending", "action": "click", "target": _NODE},
    ),
    _Variant(
        "result_with_unknown_status_and_reason",
        "unreadable",
        lambda: _FixedResult(
            SimpleNamespace(
                status="pending",
                action="click",
                target=_NODE,
                reason="backend diagnostic",
            )
        ),
        _TARGET,
        calls=1,
        output=lambda pre: {
            "status": "pending",
            "action": "click",
            "target": _NODE,
            "reason": "backend diagnostic",
        },
    ),
    _Variant(
        "empty_find_source",
        "empty_source",
        lambda: _linux_uc(_ScriptedPort()),
        {"source": "f", "action": "click"},
        calls=0,
        output=_empty_find_source_output,
        pre_steps=(_FIND_SAVE,),
    ),
    _Variant(
        "source_without_node_id",
        "empty_source",
        lambda: _linux_uc(_ScriptedPort()),
        {"source": "typed", "action": "click"},
        calls=0,
        output=lambda pre: {
            "failure_code": _EMPTY_SOURCE,
            "source_step_id": "typed",
            "source_step_kind": "type_text",
        },
        pre_steps=(_TYPE_HI,),
    ),
    _Variant(
        "node_unknown_to_stored_tree",
        "unknown_node",
        lambda: _linux_uc(_ScriptedPort(), stored_tree=_do_tree()),
        {"node_id": "n_777", "action": "click"},
        calls=1,
        error=_UNKNOWN_NODE_TEXT,
    ),
    _Variant(
        "no_active_session",
        "cannot_attempt",
        lambda: _linux_uc(_ScriptedPort()),
        _TARGET,
        calls=0,
        error="scenario step requires an active session",
        start_session=False,
    ),
    _Variant(
        "session_not_in_repository",
        "cannot_attempt",
        lambda: ActionUseCase(
            _ScriptedPort(), FakeSessionRepository(), FakeTreeStore()
        ),
        _TARGET,
        calls=1,
        error=f"Session not found: {_SESSION_ID}",
    ),
    _Variant(
        "no_node_id_or_source",
        "cannot_attempt",
        lambda: _linux_uc(_ScriptedPort()),
        {"action": "click"},
        calls=0,
        error="action step requires node_id or source",
    ),
    _Variant(
        "source_never_ran",
        "cannot_attempt",
        lambda: _linux_uc(_ScriptedPort()),
        {"source": "ghost", "action": "click"},
        calls=0,
        error="source step did not provide a node id: ghost",
    ),
    _Variant(
        "blank_node_id",
        "cannot_attempt",
        lambda: _linux_uc(_ScriptedPort()),
        {"node_id": "  ", "action": "click"},
        calls=1,
        error="Invalid node_id: must be non-empty",
    ),
    _Variant(
        "backend_raises",
        "cannot_attempt",
        lambda: _linux_uc(_RaisingPort("adb action failed (rc=1): x")),
        _TARGET,
        calls=1,
        error="adb action failed (rc=1): x",
    ),
]
_VARIANT = {variant.name: variant for variant in _VARIANTS}


@dataclasses.dataclass(frozen=True)
class _Outcome:
    status: str
    error: str
    output: Dict[str, Any]
    stored: bool
    event_code: Optional[str]


def _expected(
    variant: _Variant, expect: Any, pre: Dict[str, Dict[str, Any]]
) -> _Outcome:
    marker = _marker(expect)
    mode = expect if expect in ("refused", "any") else "ok"
    base = variant.output(pre)

    def failed(code: str) -> _Outcome:
        return _Outcome(
            "failed", code, {**base, "failure_code": code, **marker}, True, code
        )

    def passed() -> _Outcome:
        return _Outcome("passed", "", {**base, **marker}, True, None)

    group = variant.group
    if group == "reported_ok":
        return failed(_ANR) if mode == "refused" else passed()
    if group == "refusal_confirmed":
        return failed(_AF) if mode == "ok" else passed()
    if group == "not_a_refusal":
        return passed() if mode == "any" else failed(_AF)
    if group == "refusal_unconfirmed":
        return passed() if mode == "any" else failed(_AF)
    if group == "unreadable":
        return failed(_AF)
    if group == "empty_source":
        if mode == "any":
            return passed()
        return _Outcome(
            "failed", _EMPTY_SOURCE, {**base, **marker}, True, _EMPTY_SOURCE
        )
    if group == "unknown_node" and mode == "any":
        return _Outcome("passed", "", {"reason": variant.error, **marker}, True, None)
    return _Outcome("failed", variant.error, dict(marker), False, None)


def _run_cell(
    variant: _Variant, expect: Any, sink: Any = None
) -> Tuple[_Rig, Any, Dict[str, Dict[str, Any]]]:
    rig = _rig(variant.build(), sink=sink)
    if variant.start_session:
        _start(rig)
    pre: Dict[str, Dict[str, Any]] = {}
    for item in variant.pre_steps:
        pre[item["id"]] = dict(rig.executor.execute(_step(item)).output)
    step = rig.executor.execute(
        ScenarioStep(
            id=_STEP, kind="action", parameters=_with_expect(variant.params, expect)
        )
    )
    return rig, step, pre


def _assert_classified_event(event: Any, code: str, reason: Optional[str]) -> None:
    assert event.action == _CLASSIFIED_EVENT
    assert event.failure_code == code
    summary = event.diagnostic_summary
    assert isinstance(summary, str) and summary
    assert "\n" not in summary
    assert len(summary) <= 200
    assert _STEP in summary
    if reason:
        assert reason in summary
    assert event.contract_id


class TestStepOutcome:
    @pytest.mark.parametrize(
        "expect", _EXPECT_VALUES, ids=["absent", "ok", "refused", "any"]
    )
    @pytest.mark.parametrize("variant", _VARIANTS, ids=[v.name for v in _VARIANTS])
    def test_step_outcome_follows_the_expect_value_and_the_action_result(
        self, variant: _Variant, expect: Any
    ) -> None:
        rig, step, pre = _run_cell(variant, expect)
        want = _expected(variant, expect, pre)

        assert (step.status, step.error) == (want.status, want.error)
        assert step.output == want.output
        stored = rig.executor._outputs.get(_STEP, _NOT_STORED)
        if want.stored:
            assert stored == want.output
        else:
            assert stored is _NOT_STORED
        assert len(rig.action.calls) == variant.calls
        events = [e for e in rig.sink.events if e.step_id == _STEP]
        assert [e.failure_code for e in events] == (
            [want.event_code] if want.event_code else []
        )
        if want.event_code in (_AF, _ANR):
            _assert_classified_event(
                events[0], want.event_code, variant.output(pre).get("reason")
            )

    @pytest.mark.parametrize(
        "variant_name, expect, run_code",
        [
            ("not_enabled_with_actions", _ABSENT, _AF),
            ("reported_ok", "refused", _ANR),
            ("result_without_status", "any", _AF),
            ("empty_find_source", _ABSENT, "executor_error"),
        ],
        ids=[
            "action_failed",
            "action_not_refused",
            "unreadable_under_any",
            "empty_source",
        ],
    )
    def test_a_failing_diagnostic_sink_changes_no_outcome(
        self, variant_name: str, expect: Any, run_code: str
    ) -> None:
        variant = _VARIANT[variant_name]
        steps = [
            *variant.pre_steps,
            _with_expect({"id": _STEP, "kind": "action", **variant.params}, expect),
        ]

        recorded = _run_scenario(_rig(variant.build()), steps)
        raised = _run_scenario(_rig(variant.build(), sink=_RaisingSink()), steps)

        assert recorded.failure_code == run_code
        assert raised == recorded

    @pytest.mark.parametrize(
        "section, expect, stop_first, error",
        [
            ("steps", "any", True, _NO_SESSION_TEXT),
            ("steps", _ABSENT, True, _EMPTY_SOURCE),
            ("steps", "refused", True, _EMPTY_SOURCE),
            ("steps", "any", False, ""),
            ("cleanup", "any", True, _NO_SESSION_TEXT),
            ("cleanup", _ABSENT, True, _EMPTY_SOURCE),
            ("cleanup", "refused", True, _EMPTY_SOURCE),
        ],
        ids=[
            "best_effort",
            "absent",
            "refused",
            "best_effort_while_the_session_is_active",
            "best_effort_in_cleanup",
            "absent_in_cleanup",
            "refused_in_cleanup",
        ],
    )
    def test_best_effort_empty_source_after_the_session_stopped_fails(
        self, section: str, expect: Any, stop_first: bool, error: str
    ) -> None:
        rig = _rig(_linux_uc(_ScriptedPort()))
        action = _with_expect(
            {"id": _STEP, "kind": "action", "source": "f", "action": "click"}, expect
        )
        main: List[Dict[str, Any]] = [_FIND_SAVE]
        if stop_first:
            main.append(_STOP_EARLY)

        if section == "steps":
            run = _run_scenario(rig, [*main, action])
        else:
            run = _run_scenario(rig, main, cleanup=[action])

        step = _record(run, _STEP, cleanup=section == "cleanup")
        assert (step.status, step.error) == ("failed" if error else "passed", error)
        assert rig.action.calls == []
        if error == _NO_SESSION_TEXT:
            assert step.output == {"expect": "any"}
            assert _STEP not in rig.executor._outputs
            assert [e for e in rig.sink.events if e.step_id == _STEP] == []
        if section == "steps" and error:
            assert (run.status, run.failure_code) == ("failed", "executor_error")
            assert run.next_actions == (
                ScenarioNextAction(code="inspect_error", message=error),
            )
        else:
            assert (run.status, run.failure_code) == ("passed", "")

    def test_best_effort_unknown_node_after_the_session_stopped_fails(self) -> None:
        rig = _rig(_linux_uc(_ScriptedPort(), stored_tree=_do_tree()))

        run = _run_scenario(rig, [_STOP_EARLY, _action_doc("any", node_id="n_777")])

        step = _record(run, _STEP)
        assert (step.status, step.error, step.output) == (
            "failed",
            _NO_SESSION_TEXT,
            {"expect": "any"},
        )
        assert rig.action.calls == []
        assert _STEP not in rig.executor._outputs
        assert (run.status, run.failure_code) == ("failed", "executor_error")


# ═══════════════════════════════════════════════════════════════════════
# Linux and Android action errors fail the step and keep the action output
# ═══════════════════════════════════════════════════════════════════════


def _assert_fails_keeping_the_action_output(
    rig: _Rig, step: Any, code: str, call: Dict[str, Any], expect: Any = _ABSENT
) -> None:
    assert (step.status, step.error) == ("failed", code)
    assert rig.action.calls == [call]
    assert step.output == {
        **_plain(rig.action.results[0]),
        "failure_code": code,
        **_marker(expect),
    }


_LINUX_ERRORS = [
    (reason, action)
    for reason in ("not_enabled", "declined", None)
    for action in ("click", "focus", "set_text")
]


class TestBackendErrors:
    @pytest.mark.parametrize(
        "failure_reason, action",
        _LINUX_ERRORS,
        ids=[f"{reason or 'no_reason'}-{action}" for reason, action in _LINUX_ERRORS],
    )
    def test_linux_action_error_fails_the_step(
        self, failure_reason: Optional[str], action: str
    ) -> None:
        value = "hi" if action == "set_text" else None
        port = _refusing_port(("click", "focus"), failure_reason)
        rig = _rig(_linux_uc(port, stored_tree=_do_tree()))
        _start(rig)

        params: Dict[str, Any] = {"node_id": _NODE, "action": action}
        if value is not None:
            params["value"] = value
        step = rig.executor.execute(
            ScenarioStep(id=_STEP, kind="action", parameters=params)
        )

        _assert_fails_keeping_the_action_output(
            rig,
            step,
            _AF,
            {
                "session_id": _SESSION_ID,
                "node_id": _NODE,
                "action_name": action,
                "value": value,
            },
        )

    def test_linux_unknown_node_without_a_stored_tree_fails_the_step(self) -> None:
        rig = _rig(_linux_uc(_refusing_port(("click", "focus"), None)))
        _start(rig)

        step = rig.executor.execute(
            ScenarioStep(
                id=_STEP,
                kind="action",
                parameters={"node_id": "n_777", "action": "click"},
            )
        )

        _assert_fails_keeping_the_action_output(
            rig,
            step,
            _AF,
            {
                "session_id": _SESSION_ID,
                "node_id": "n_777",
                "action_name": "click",
                "value": None,
            },
        )

    @pytest.mark.parametrize(
        "action, value, live_tree",
        [
            ("click", None, AccessibilityTree(roots=())),
            ("click", None, None),
            ("frobnicate", None, _do_tree()),
            ("set_text", "hi", _do_tree()),
        ],
        ids=[
            "click_node_not_in_live_tree",
            "click_without_tree_adapter",
            "unknown_action_name",
            "set_text_on_node_without_set_text",
        ],
    )
    def test_android_action_error_fails_the_step(
        self, action: str, value: Optional[str], live_tree: Optional[AccessibilityTree]
    ) -> None:
        adapter = AndroidActionAdapter()
        if live_tree is not None:
            adapter.set_tree_adapter(_LiveTree(live_tree))
        rig = _rig(_android_uc(adapter), session_id=_ANDROID_SESSION_ID)
        params: Dict[str, Any] = {"node_id": _NODE, "action": action}
        if value is not None:
            params["value"] = value

        with patch("aiyes.adapters.android_action_adapter._run_adb", return_value=None):
            _start(rig)
            step = rig.executor.execute(
                ScenarioStep(id=_STEP, kind="action", parameters=params)
            )

        _assert_fails_keeping_the_action_output(
            rig,
            step,
            _AF,
            {
                "session_id": _ANDROID_SESSION_ID,
                "node_id": _NODE,
                "action_name": action,
                "value": value,
            },
        )

    @pytest.mark.parametrize("expect", [_ABSENT, "ok"], ids=["absent", "ok"])
    @pytest.mark.parametrize("backend", ["linux", "android"])
    def test_action_reported_ok_passes_with_its_output_unchanged(
        self, backend: str, expect: Any
    ) -> None:
        params = _with_expect({"node_id": _NODE, "action": "click"}, expect)
        if backend == "linux":
            rig = _rig(_linux_uc(_ScriptedPort(), stored_tree=_do_tree()))
            _start(rig)
            step = rig.executor.execute(
                ScenarioStep(id=_STEP, kind="action", parameters=params)
            )
            expected = {"status": "ok", "action": "click", "target": _NODE}
        else:
            adapter = AndroidActionAdapter()
            adapter.set_tree_adapter(_LiveTree(_do_tree()))
            rig = _rig(_android_uc(adapter), session_id=_ANDROID_SESSION_ID)
            with patch(
                "aiyes.adapters.android_action_adapter._run_adb", return_value=None
            ):
                _start(rig)
                step = rig.executor.execute(
                    ScenarioStep(id=_STEP, kind="action", parameters=params)
                )
            expected = {
                "status": "ok",
                "action": "click",
                "target": _NODE,
                "action_method": "node_bounds_tap",
            }

        assert (step.status, step.error, step.output) == ("passed", "", expected)

    def test_action_result_fields_are_unchanged(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(ActionResult))

        assert names == (
            "status",
            "action",
            "target",
            "reason",
            "available_actions",
            "node_value",
            "node_states",
            "action_method",
        )


# ═══════════════════════════════════════════════════════════════════════
# "refused" and "any" through the real backend adapters
# ═══════════════════════════════════════════════════════════════════════


@dataclasses.dataclass(frozen=True)
class _BackendCase:
    """An action on a real adapter and its outcome under "refused"."""

    name: str
    backend: str
    action: str
    under_refused: str  # "" for a pass, else the failure code
    accessible: Callable[[], Optional[_FakeAccessible]] = lambda: None
    live_tree: Callable[[], AccessibilityTree] = lambda: AccessibilityTree(roots=())
    value: Optional[str] = None


_BACKEND_CASES = [
    _BackendCase(
        "linux_not_enabled",
        "linux",
        "click",
        "",
        accessible=lambda: _FakeAccessible(actions=["click"], states=_FakeStateSet()),
    ),
    _BackendCase(
        "linux_declined",
        "linux",
        "click",
        "",
        accessible=lambda: _FakeAccessible(actions=["click"], do_action=False),
    ),
    _BackendCase(
        "linux_not_offered_by_a_node_with_other_actions",
        "linux",
        "click",
        _AF,
        accessible=lambda: _FakeAccessible(actions=["press"]),
    ),
    _BackendCase("linux_node_not_in_live_tree", "linux", "click", _AF),
    _BackendCase(
        "linux_node_without_actions",
        "linux",
        "click",
        _AF,
        accessible=lambda: _FakeAccessible(actions=[]),
    ),
    _BackendCase(
        "linux_focus_on_a_disabled_node_without_actions",
        "linux",
        "focus",
        "",
        accessible=lambda: _FakeAccessible(
            actions=[], states=_FakeStateSet(*_DISABLED_STATES)
        ),
    ),
    _BackendCase(
        "linux_focus_on_an_enabled_node",
        "linux",
        "focus",
        _ANR,
        accessible=lambda: _FakeAccessible(actions=["Press", "SetFocus"]),
    ),
    _BackendCase(
        "linux_offered_action_whose_call_raises",
        "linux",
        "click",
        _AF,
        accessible=lambda: _FakeAccessible(actions=["click"], do_action=_RAISE),
    ),
    _BackendCase(
        "android_set_text_on_a_clickable_widget",
        "android",
        "set_text",
        _AF,
        live_tree=lambda: _android_tree(("click",), ("enabled",)),
        value="hi",
    ),
    _BackendCase(
        "android_click_on_a_disabled_widget",
        "android",
        "click",
        _ANR,
        live_tree=lambda: _android_tree(("click",), ()),
    ),
    _BackendCase("android_click_node_not_in_live_tree", "android", "click", _AF),
]


def _run_backend_case(case: _BackendCase, expect: str) -> Tuple[_Rig, Any]:
    params: Dict[str, Any] = {"node_id": _NODE, "action": case.action, "expect": expect}
    if case.value is not None:
        params["value"] = case.value
    step = ScenarioStep(id=_STEP, kind="action", parameters=params)
    if case.backend == "linux":
        adapter = AtSpi2ActionAdapter()
        adapter.set_worker(_InProcessWorker(case.accessible()))
        rig = _rig(_linux_uc(adapter, stored_tree=_do_tree()))
        _start(rig)
        return rig, rig.executor.execute(step)
    android = AndroidActionAdapter()
    android.set_tree_adapter(_LiveTree(case.live_tree()))
    rig = _rig(_android_uc(android), session_id=_ANDROID_SESSION_ID)
    with patch("aiyes.adapters.android_action_adapter._run_adb", return_value=None):
        _start(rig)
        return rig, rig.executor.execute(step)


class TestRealBackendAdapters:
    @pytest.mark.parametrize("expect", ["refused", "any"])
    @pytest.mark.parametrize(
        "case", _BACKEND_CASES, ids=[c.name for c in _BACKEND_CASES]
    )
    def test_expect_outcome_through_the_real_adapter(
        self, case: _BackendCase, expect: str
    ) -> None:
        rig, step = _run_backend_case(case, expect)
        code = case.under_refused if expect == "refused" else ""

        assert (step.status, step.error) == ("failed" if code else "passed", code)
        expected = {**_plain(rig.action.results[0]), "expect": expect}
        if code:
            expected["failure_code"] = code
        assert step.output == expected


# ═══════════════════════════════════════════════════════════════════════
# What a failed step records; reserved codes
# ═══════════════════════════════════════════════════════════════════════


class TestReservedCodes:
    @pytest.mark.parametrize("token", [_AF, _ANR])
    def test_code_is_a_module_constant_and_never_an_exception_message(
        self, token: str
    ) -> None:
        tree = ast.parse(_EXECUTOR_SRC.read_text(encoding="utf-8"))
        literals = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == token
        ]
        definitions = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and node.value.value == token
        ]

        assert len(definitions) == 1, f"the executor must define {token!r} once"
        assert literals == [definitions[0].value], (
            f"{token!r} may occur only in its constant definition"
        )
        name = definitions[0].targets[0].id
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                for sub in ast.walk(node.exc):
                    assert not (isinstance(sub, ast.Name) and sub.id == name), (
                        f"line {sub.lineno} raises with {name}"
                    )


# ═══════════════════════════════════════════════════════════════════════
# Run result: the run ends at the failed action, cleanup runs
# ═══════════════════════════════════════════════════════════════════════

_SHOT = {"id": "shot", "kind": "screenshot"}


class TestRunResult:
    def test_failed_action_step_ends_the_run_and_cleanup_still_runs(self) -> None:
        rig = _rig(_linux_uc(_refusing_port(("click", "focus"), "not_enabled")))

        run = _run_scenario(rig, [_action_doc(), _SHOT])

        assert [(s.step_id, s.status, s.cleanup) for s in run.steps] == [
            ("start", "passed", False),
            (_STEP, "failed", False),
            ("stop", "passed", True),
        ]
        assert rig.screenshot.calls == []
        assert (run.status, run.failure_code) == ("failed", _AF)
        assert len(run.next_actions) == 1
        assert (run.next_actions[0].code, run.next_actions[0].command_hint) == (
            "inspect_error",
            "",
        )

    def test_run_whose_action_reports_ok_is_unchanged(self) -> None:
        rig = _rig(_linux_uc(_ScriptedPort()))

        run = _run_scenario(rig, [_action_doc(), _SHOT])

        assert [(s.step_id, s.status) for s in run.steps] == [
            ("start", "passed"),
            (_STEP, "passed"),
            ("shot", "passed"),
            ("stop", "passed"),
        ]
        assert (run.status, run.failure_code, run.next_actions) == ("passed", "", ())
        assert len(rig.screenshot.calls) == 1

    def test_failed_action_step_stops_before_a_later_action_ok_assertion(self) -> None:
        rig = _rig(_linux_uc(_refusing_port(("click", "focus"), "declined")))
        check = {
            "id": "check",
            "kind": "assert",
            "assertion": {"id": "press-ok", "kind": "action_ok", "source": _STEP},
        }

        run = _run_scenario(rig, [_action_doc(), check])

        assert run.failure_code == _AF
        assert [s.step_id for s in run.steps] == ["start", _STEP, "stop"]

    @pytest.mark.parametrize(
        "build, params, pre_steps, error",
        [
            (
                lambda: _linux_uc(_ScriptedPort()),
                {"source": "f", "action": "click"},
                [_FIND_SAVE],
                _EMPTY_SOURCE,
            ),
            (
                lambda: _linux_uc(_ScriptedPort(), stored_tree=_do_tree()),
                {"node_id": "n_777", "action": "click"},
                [],
                _UNKNOWN_NODE_TEXT,
            ),
            (
                lambda: ActionUseCase(
                    _ScriptedPort(), FakeSessionRepository(), FakeTreeStore()
                ),
                _TARGET,
                [],
                f"Session not found: {_SESSION_ID}",
            ),
            (
                lambda: _linux_uc(_ScriptedPort()),
                {"action": "click"},
                [],
                "action step requires node_id or source",
            ),
            (
                lambda: _linux_uc(_RaisingPort("adb action failed (rc=1): x")),
                _TARGET,
                [],
                "adb action failed (rc=1): x",
            ),
            (
                lambda: _RaisingAction(RuntimeError("action_failed while sending")),
                _TARGET,
                [],
                "action_failed while sending",
            ),
        ],
        ids=[
            "empty_source",
            "node_unknown_to_stored_tree",
            "session_not_in_repository",
            "no_node_id_or_source",
            "backend_raises",
            "error_text_containing_a_code",
        ],
    )
    def test_failures_before_or_outside_the_action_keep_the_generic_code(
        self,
        build: Callable[[], Any],
        params: Dict[str, Any],
        pre_steps: List[Dict[str, Any]],
        error: str,
    ) -> None:
        rig = _rig(build())

        run = _run_scenario(
            rig, [*pre_steps, {"id": _STEP, "kind": "action", **params}]
        )

        assert _record(run, _STEP).error == error
        assert run.failure_code == "executor_error"
        assert run.next_actions == (
            ScenarioNextAction(code="inspect_error", message=error),
        )

    def test_refused_and_best_effort_steps_that_pass_let_the_run_continue(self) -> None:
        port = _ScriptedPort(
            {
                "n_disabled": (False, ("click",), "not_enabled"),
                "n_gone": (False, (), None),
            }
        )
        rig = _rig(_linux_uc(port))
        steps = [
            _action_doc("refused", step_id="negative", node_id="n_disabled"),
            _action_doc("any", step_id="dismiss", node_id="n_gone"),
            _FIND_SAVE,
            _with_expect(
                {
                    "id": "dismiss-again",
                    "kind": "action",
                    "source": "f",
                    "action": "click",
                },
                "any",
            ),
            _SHOT,
        ]

        run = _run_scenario(rig, steps)

        assert (run.status, run.failure_code, run.next_actions) == ("passed", "", ())
        assert [(s.step_id, s.status) for s in run.steps] == [
            ("start", "passed"),
            ("negative", "passed"),
            ("dismiss", "passed"),
            ("f", "passed"),
            ("dismiss-again", "passed"),
            ("shot", "passed"),
            ("stop", "passed"),
        ]
        assert _record(run, "negative").output.get("expect") == "refused"
        assert len(rig.screenshot.calls) == 1

    def test_refused_step_whose_action_goes_through_ends_the_run(self) -> None:
        rig = _rig(_linux_uc(_ScriptedPort()))

        run = _run_scenario(rig, [_action_doc("refused"), _SHOT])

        assert (run.status, run.failure_code) == ("failed", _ANR)
        assert [s.step_id for s in run.steps] == ["start", _STEP, "stop"]
        assert rig.screenshot.calls == []


# ═══════════════════════════════════════════════════════════════════════
# The run's message names the step, quotes the reason and gives the fix
# ═══════════════════════════════════════════════════════════════════════

_REFUSED_FIX = '"expect": "refused"'
_ANY_FIX = '"expect": "any"'
_TARGET_NOT_CONFIRMED = "could not confirm the target was there"
_NOT_CONFIRMED_HAPPENED = "could not confirm that the action happened"
_WAIT_FIX = "wait for it"
_NOT_OBSERVABLE = "cannot be observed"
_FOCUS_FIX = '"action": "focus"'
_REQUIRED_FIND = "required find"
_NAME_ONE = "name one of those"
_NO_CAUSE = "no refusal cause"
_BACKEND_DIAGNOSTIC = "backend diagnostic"

# Action results used only by the message tests, which do not check outputs.
_MESSAGE_VARIANT = {
    **_VARIANT,
    "focus_not_available_without_actions": _Variant(
        "focus_not_available_without_actions",
        "refusal_unconfirmed",
        lambda: _linux_uc(_refusing_port((), None)),
        {"node_id": _NODE, "action": "focus"},
        calls=1,
    ),
    "not_offered_by_a_node_with_press_and_setfocus": _Variant(
        "not_offered_by_a_node_with_press_and_setfocus",
        "not_a_refusal",
        lambda: _linux_uc(_refusing_port(("Press", "SetFocus"), None)),
        _TARGET,
        calls=1,
    ),
}


class TestRunMessage:
    @pytest.mark.parametrize(
        "variant_name, expect, code, must_contain, must_not_contain",
        [
            (
                "not_enabled_with_actions",
                _ABSENT,
                _AF,
                [_STEP, _NOT_ENABLED_REASON, _REFUSED_FIX, _ANY_FIX],
                [],
            ),
            (
                "not_available_without_actions",
                _ABSENT,
                _AF,
                [
                    _STEP,
                    _NOT_AVAILABLE_REASON,
                    _TARGET_NOT_CONFIRMED,
                    _WAIT_FIX,
                    _ANY_FIX,
                ],
                [_REFUSED_FIX, _REQUIRED_FIND],
            ),
            (
                "not_available_without_actions",
                "refused",
                _AF,
                [
                    _STEP,
                    _NOT_AVAILABLE_REASON,
                    _TARGET_NOT_CONFIRMED,
                    _NOT_OBSERVABLE,
                    _FOCUS_FIX,
                    _WAIT_FIX,
                    _ANY_FIX,
                ],
                [_REQUIRED_FIND],
            ),
            (
                "result_without_status",
                _ABSENT,
                _AF,
                [_STEP, _NOT_CONFIRMED_HAPPENED],
                [],
            ),
            (
                "result_with_unknown_status",
                _ABSENT,
                _AF,
                [_STEP, _NOT_CONFIRMED_HAPPENED],
                [],
            ),
            ("reported_ok", "refused", _ANR, [_STEP, "went through", _REFUSED_FIX], []),
            (
                "focus_not_available_without_actions",
                "refused",
                _AF,
                [
                    _STEP,
                    _FOCUS_NOT_AVAILABLE_REASON,
                    _TARGET_NOT_CONFIRMED,
                    _NOT_OBSERVABLE,
                    _WAIT_FIX,
                    _ANY_FIX,
                ],
                [_FOCUS_FIX, _REQUIRED_FIND],
            ),
            (
                "result_with_unknown_status_and_reason",
                _ABSENT,
                _AF,
                [_STEP, _NOT_CONFIRMED_HAPPENED, _BACKEND_DIAGNOSTIC],
                [],
            ),
            (
                "result_with_unknown_status_and_reason",
                "refused",
                _AF,
                [_STEP, _NOT_CONFIRMED_HAPPENED, _BACKEND_DIAGNOSTIC],
                [],
            ),
            (
                "result_with_unknown_status_and_reason",
                "any",
                _AF,
                [_STEP, _NOT_CONFIRMED_HAPPENED, _BACKEND_DIAGNOSTIC],
                [],
            ),
            (
                "not_offered_by_a_node_with_press_and_setfocus",
                _ABSENT,
                _AF,
                [_STEP, _NOT_AVAILABLE_REASON, "Press", "SetFocus", _NAME_ONE],
                [_REFUSED_FIX],
            ),
            (
                "not_offered_by_a_node_with_press_and_setfocus",
                "refused",
                _AF,
                [_STEP, _NOT_AVAILABLE_REASON, "Press", "SetFocus", _NAME_ONE],
                [],
            ),
            (
                "offered_action_without_cause",
                _ABSENT,
                _AF,
                [_STEP, _NOT_AVAILABLE_REASON, _NO_CAUSE],
                [_NAME_ONE, _REFUSED_FIX],
            ),
        ],
        ids=[
            "refusal_confirmed",
            "target_not_confirmed",
            "refusal_expected_but_target_not_confirmed",
            "result_without_status",
            "result_with_unknown_status",
            "action_went_through",
            "refusal_expected_with_focus_but_target_not_confirmed",
            "result_with_unknown_status_and_reason",
            "refusal_expected_but_result_with_unknown_status_and_reason",
            "best_effort_but_result_with_unknown_status_and_reason",
            "action_not_offered",
            "refusal_expected_but_action_not_offered",
            "offered_action_failed_without_cause",
        ],
    )
    def test_message_names_the_step_the_reason_and_the_fix(
        self,
        variant_name: str,
        expect: Any,
        code: str,
        must_contain: List[str],
        must_not_contain: List[str],
    ) -> None:
        variant = _MESSAGE_VARIANT[variant_name]
        rig = _rig(variant.build())

        run = _run_scenario(
            rig,
            [_with_expect({"id": _STEP, "kind": "action", **variant.params}, expect)],
        )

        step = _record(run, _STEP)
        assert (step.status, step.error) == ("failed", code)
        assert (run.status, run.failure_code) == ("failed", code)
        assert len(run.next_actions) == 1
        action = run.next_actions[0]
        assert (action.code, action.command_hint) == ("inspect_error", "")
        for text in must_contain:
            assert text in action.message
        for text in must_not_contain:
            assert text not in action.message
        assert "\n" not in action.message


# ═══════════════════════════════════════════════════════════════════════
# Cleanup action steps
# ═══════════════════════════════════════════════════════════════════════

_CLEANUP_PORT_SCRIPT = {"n_disabled": (False, ("click",), "not_enabled")}


class TestCleanupSteps:
    def test_failed_cleanup_action_is_recorded_without_failing_the_run(
        self, tmp_path: Path
    ) -> None:
        rig = _rig(_linux_uc(_ScriptedPort(_CLEANUP_PORT_SCRIPT)))
        cleanup = [
            _action_doc(step_id="undo", node_id="n_disabled"),
            {"id": "stop", "kind": "stop_session"},
        ]
        path = _write(tmp_path, _document([_action_doc()], cleanup))

        result = _run_cli(
            ["scenario", "run", "--real", str(path)],
            scenario_real_run_uc=_real_runner(rig),
        )

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert (payload["status"], payload["failure_code"]) == ("passed", "")
        undo = _record(payload, "undo", cleanup=True)
        assert (undo["status"], undo.get("error")) == ("failed", _AF)
        assert undo["output"].get("failure_code") == _AF
        assert _record(payload, "stop", cleanup=True)["status"] == "passed"

    def test_cleanup_failure_keeps_the_main_step_code(self) -> None:
        rig = _rig(_linux_uc(_ScriptedPort(_CLEANUP_PORT_SCRIPT)))
        cleanup = [
            _action_doc(step_id="undo", node_id="n_disabled"),
            {"id": "stop", "kind": "stop_session"},
        ]

        run = _run_scenario(rig, [_action_doc("refused")], cleanup)

        assert (run.status, run.failure_code) == ("failed", _ANR)
        undo = _record(run, "undo", cleanup=True)
        assert (undo.status, undo.error) == ("failed", _AF)

    def test_best_effort_cleanup_action_passes_with_the_marker(self) -> None:
        rig = _rig(_linux_uc(_ScriptedPort(_CLEANUP_PORT_SCRIPT)))
        cleanup = [
            _action_doc("any", step_id="undo", node_id="n_disabled"),
            {"id": "stop", "kind": "stop_session"},
        ]

        run = _run_scenario(rig, [_action_doc()], cleanup)

        assert (run.status, run.failure_code) == ("passed", "")
        undo = _record(run, "undo", cleanup=True)
        assert (undo.status, undo.error) == ("passed", "")
        assert undo.output.get("expect") == "any"
        assert undo.output["status"] == "error"


# ═══════════════════════════════════════════════════════════════════════
# CLI and MCP
# ═══════════════════════════════════════════════════════════════════════


class TestCliScenarioRun:
    @pytest.mark.parametrize(
        "refused, expect, exit_code, status, code",
        [
            (True, _ABSENT, 1, "failed", _AF),
            (True, "refused", 0, "passed", ""),
            (True, "any", 0, "passed", ""),
            (False, "refused", 1, "failed", _ANR),
            (False, _ABSENT, 0, "passed", ""),
        ],
        ids=[
            "refusal_without_expect",
            "refusal_expected",
            "refusal_best_effort",
            "refusal_expected_but_went_through",
            "reported_ok",
        ],
    )
    def test_exit_code_follows_the_action_step(
        self,
        tmp_path: Path,
        refused: bool,
        expect: Any,
        exit_code: int,
        status: str,
        code: str,
    ) -> None:
        port = (
            _refusing_port(("click", "focus"), "not_enabled")
            if refused
            else _ScriptedPort()
        )
        rig = _rig(_linux_uc(port))
        path = _write(tmp_path, _document([_action_doc(expect)]))

        result = _run_cli(
            ["scenario", "run", "--real", str(path)],
            scenario_real_run_uc=_real_runner(rig),
        )

        payload = json.loads(result.stdout)
        assert (result.exit_code, payload["status"], payload["failure_code"]) == (
            exit_code,
            status,
            code,
        )
        output = _record(payload, _STEP)["output"]
        assert output.get("expect", _ABSENT) == (
            expect if expect in ("refused", "any") else _ABSENT
        )

    @pytest.mark.parametrize(
        "argv, issues_key",
        [
            (["scenario", "run", "--real"], "errors"),
            (["scenario", "run"], "errors"),
            (["scenario", "preflight"], "validation_errors"),
        ],
        ids=["run_real", "run_dry", "preflight"],
    )
    @pytest.mark.parametrize(
        "step, code",
        [
            (_action_doc("bogus", step_id="act"), "action_expect_invalid"),
            (
                {"id": "f", "kind": "find", "role": "push_button", "expect": "refused"},
                "expect_not_supported",
            ),
        ],
        ids=["invalid_value_on_action", "expect_on_find"],
    )
    def test_invalid_expect_exits_2_before_any_session(
        self,
        tmp_path: Path,
        argv: List[str],
        issues_key: str,
        step: Dict[str, Any],
        code: str,
    ) -> None:
        rig = _rig(_linux_uc(_ScriptedPort()))
        dry_runner = _SpyRunner(ScenarioRunUseCase(ScenarioDryRunExecutor()))
        preflight = _SpyRunner(_PassingPreflight())
        path = _write(tmp_path, _document([step]))

        result = _run_cli(
            [*argv, str(path)],
            scenario_real_run_uc=_real_runner(rig),
            scenario_run_uc=dry_runner,
            scenario_preflight_uc=preflight,
            scenario_real_preflight_uc=preflight,
        )

        assert result.exit_code == 2
        payload = json.loads(result.stdout)
        assert payload["failure_code"] == "validation_error"
        assert [(e["path"], e["code"]) for e in payload[issues_key]] == [
            ("steps[1].expect", code)
        ]
        assert rig.session_start.calls == []
        assert dry_runner.calls == []
        assert preflight.calls == []


class TestMcpScenarioRun:
    @pytest.mark.parametrize(
        "expect, status, code",
        [(_ABSENT, "failed", _AF), ("any", "passed", "")],
        ids=["refusal_without_expect", "refusal_best_effort"],
    )
    def test_scenario_run_result_equals_the_cli_result(
        self, tmp_path: Path, expect: Any, status: str, code: str
    ) -> None:
        path = _write(tmp_path, _document([_action_doc(expect)]))

        def runner() -> ScenarioRunUseCase:
            port = _refusing_port(("click", "focus"), "not_enabled")
            return _real_runner(_rig(_linux_uc(port)))

        is_error, text = _call_tool(
            _mcp_deps(scenario_real_run_uc=runner()),
            "scenario_run",
            {"scenario_path": str(path), "real_execution": True},
        )
        cli_result = _run_cli(
            ["scenario", "run", "--real", str(path)], scenario_real_run_uc=runner()
        )

        payload = json.loads(text)
        assert is_error is False
        assert (payload["status"], payload["failure_code"]) == (status, code)
        assert _record(payload, _STEP)["output"].get("expect", _ABSENT) == (
            "any" if expect == "any" else _ABSENT
        )
        assert payload == json.loads(cli_result.stdout)

    @pytest.mark.parametrize(
        "step, code",
        [
            (_action_doc("bogus", step_id="act"), "action_expect_invalid"),
            (
                {"id": "f", "kind": "find", "role": "push_button", "expect": "refused"},
                "expect_not_supported",
            ),
        ],
        ids=["invalid_value_on_action", "expect_on_find"],
    )
    def test_invalid_expect_returns_the_validation_result_before_any_session(
        self, tmp_path: Path, step: Dict[str, Any], code: str
    ) -> None:
        rig = _rig(_linux_uc(_ScriptedPort()))
        preflight = _SpyRunner(_PassingPreflight())
        deps = _mcp_deps(
            scenario_real_run_uc=_real_runner(rig),
            scenario_run_uc=_SpyRunner(ScenarioRunUseCase(ScenarioDryRunExecutor())),
            scenario_preflight_uc=preflight,
            scenario_real_preflight_uc=preflight,
        )
        path = str(_write(tmp_path, _document([step])))

        run_error, run_text = _call_tool(
            deps, "scenario_run", {"scenario_path": path, "real_execution": True}
        )
        run_payload = json.loads(run_text)
        assert run_payload["failure_code"] == "validation_error"
        assert [(e["path"], e["code"]) for e in run_payload["errors"]] == [
            ("steps[1].expect", code)
        ]
        preflight_error, preflight_text = _call_tool(
            deps, "scenario_preflight", {"scenario_path": path, "real_execution": True}
        )
        preflight_payload = json.loads(preflight_text)
        assert preflight_payload["failure_code"] == "validation_error"
        assert [
            (e["path"], e["code"]) for e in preflight_payload["validation_errors"]
        ] == [("steps[1].expect", code)]
        assert (run_error, preflight_error) == (False, False)
        assert rig.session_start.calls == []
        assert preflight.calls == []


# ═══════════════════════════════════════════════════════════════════════
# Evidence bundle
# ═══════════════════════════════════════════════════════════════════════


def _bundle(
    directory: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    run_json = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (directory / "steps.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, run_json, records


def _jsonl_record(records: List[Dict[str, Any]], step_id: str) -> Dict[str, Any]:
    matches = [r for r in records if r["step_id"] == step_id]
    assert len(matches) == 1
    return matches[0]


class TestEvidenceBundle:
    def test_bundle_of_a_run_failed_at_an_action_step(self, tmp_path: Path) -> None:
        rig = _rig(_linux_uc(_refusing_port(("click", "focus"), "not_enabled")))
        run = _run_scenario(rig, [_action_doc(), _SHOT])

        write_scenario_evidence_bundle(tmp_path / "compact", run, environment={})
        write_scenario_evidence_bundle(
            tmp_path / "deep", run, environment={}, profile="deep"
        )

        manifest, run_json, records = _bundle(tmp_path / "compact")
        assert manifest["status"] == "failed"
        assert (run_json["status"], run_json["failure_code"]) == ("failed", _AF)
        assert run_json["next_actions"] == [
            dataclasses.asdict(action) for action in run.next_actions
        ]
        assert len(run_json["next_actions"]) == 1
        act = _jsonl_record(records, _STEP)
        assert (act["status"], act.get("error"), act.get("failure_code")) == (
            "failed",
            _AF,
            _AF,
        )
        assert act["diagnostic_summary"]
        for key, value in {
            "status": "error",
            "action": "click",
            "target": _NODE,
            "reason": _NOT_ENABLED_REASON,
            "available_actions": ["click", "focus"],
        }.items():
            assert act["output"][key] == value
        assert "expect" not in act["output"]

        _, _, deep_records = _bundle(tmp_path / "deep")
        assert deep_records == [
            json.loads(json.dumps(dataclasses.asdict(step), sort_keys=True))
            for step in run.steps
        ]

    def test_bundle_of_refused_and_best_effort_steps_that_pass(
        self, tmp_path: Path
    ) -> None:
        port = _ScriptedPort({"n_disabled": (False, ("click",), "not_enabled")})
        rig = _rig(_linux_uc(port))
        steps = [
            _action_doc("refused", step_id="negative", node_id="n_disabled"),
            _FIND_SAVE,
            _with_expect(
                {"id": "dismiss", "kind": "action", "source": "f", "action": "click"},
                "any",
            ),
        ]
        run = _run_scenario(rig, steps)

        write_scenario_evidence_bundle(tmp_path, run, environment={})

        manifest, _, records = _bundle(tmp_path)
        assert manifest["status"] == "passed"
        negative = _jsonl_record(records, "negative")
        assert negative["status"] == "passed"
        assert negative["output"] == {
            "status": "error",
            "action": "click",
            "target": "n_disabled",
            "reason": "Action 'click' not sent: node 'n_disabled' is not enabled",
            "available_actions": ["click"],
            "expect": "refused",
        }
        assert "failure_code" not in negative
        assert negative["diagnostic_summary"] == ""
        dismiss = _jsonl_record(records, "dismiss")
        assert dismiss["status"] == "passed"
        assert dismiss["output"].get("expect") == "any"
        assert dismiss["output"].get("failure_code") == _EMPTY_SOURCE
        assert dismiss.get("failure_code") == _EMPTY_SOURCE
        assert dismiss["diagnostic_summary"] == ""

    def test_cli_writes_the_bundle_for_a_failed_action_step(
        self, tmp_path: Path
    ) -> None:
        rig = _rig(_linux_uc(_refusing_port(("click", "focus"), "not_enabled")))
        path = _write(tmp_path, _document([_action_doc()]))
        evidence = tmp_path / "evidence"

        result = _run_cli(
            ["scenario", "run", "--real", "--evidence-dir", str(evidence), str(path)],
            scenario_real_run_uc=_real_runner(rig),
        )

        assert result.exit_code == 1
        manifest, run_json, records = _bundle(evidence)
        assert manifest["status"] == "failed"
        assert run_json["failure_code"] == _AF
        assert _jsonl_record(records, _STEP).get("failure_code") == _AF


# ═══════════════════════════════════════════════════════════════════════
# Dry run does not perform actions
# ═══════════════════════════════════════════════════════════════════════


class TestDryRun:
    def test_dry_run_records_refused_and_best_effort_steps_as_passed(self) -> None:
        scenario = _scenario(
            _document(
                [
                    _action_doc("refused", step_id="negative"),
                    _action_doc("any", step_id="dismiss"),
                ]
            )
        )

        run = ScenarioRunUseCase(ScenarioDryRunExecutor()).execute(scenario)

        assert run.status == "passed"
        for step_id in ("negative", "dismiss"):
            record = _record(run, step_id)
            assert record.status == "passed"
            assert record.output == {"dry_run": True, "kind": "action"}


# ═══════════════════════════════════════════════════════════════════════
# Load-time validation of "expect"
# ═══════════════════════════════════════════════════════════════════════

_INVALID_EXPECT_VALUES = [
    "OK",
    "Refused",
    " ok",
    "ok ",
    "",
    "none",
    "absent",
    "refuse",
    True,
    False,
    1,
    0,
    None,
    [],
    {},
    ["ok"],
]
_EXPECT_MESSAGE = 'action.expect must be one of "ok", "refused", "any"'
_SECTIONS = [("steps", "steps[1].expect"), ("cleanup", "cleanup[0].expect")]


def _action_step_document(expect: Any, section: str) -> Dict[str, Any]:
    action = _with_expect(
        {"id": "act", "kind": "action", "node_id": _NODE, "action": "click"}, expect
    )
    if section == "steps":
        return _document([action])
    return _document([], cleanup=[action, {"id": "stop", "kind": "stop_session"}])


def _issues(result: Any) -> List[Tuple[str, str, str]]:
    return [(issue.path, issue.code, issue.message) for issue in result.issues]


# A minimal valid step of every supported kind other than action.
_MINIMAL_STEPS: Dict[str, Dict[str, Any]] = {
    "start_session": {"command": "probe"},
    "inspect": {},
    "find": {"role": "push_button"},
    "type_text": {"text": "hi"},
    "wait": {"role": "push_button"},
    "wait_stable": {},
    "wait_reactive": {"condition": "screen-change"},
    "key": {"keys": ["Return"]},
    "sleep": {"seconds": 0.5, "reason": "let the dialog finish its animation"},
    "mouse_drag": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
    "mouse_scroll": {"direction": "down"},
    "gesture_pinch": {"scale_factor": 1.5},
    "gesture_two_finger_scroll": {"direction": "down"},
    "swipe": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
    "scroll_into_view": {"role": "list_item", "name_pattern": "Row"},
    "screenshot": {},
    "navigate": {},
    "stop_session": {},
    "assert": {"assertion": {"id": "a", "kind": "action_ok", "source": "start"}},
}


def _kind_document(kind: str, section: str, expect: Any = _ABSENT) -> Dict[str, Any]:
    step = _with_expect({"id": "other", "kind": kind, **_MINIMAL_STEPS[kind]}, expect)
    if section == "steps":
        return _document([step])
    return _document([], cleanup=[step, {"id": "stop", "kind": "stop_session"}])


class TestExpectValidation:
    @pytest.mark.parametrize("section, path", _SECTIONS, ids=["steps", "cleanup"])
    @pytest.mark.parametrize("value", _INVALID_EXPECT_VALUES, ids=repr)
    def test_invalid_value_is_rejected_when_the_file_loads(
        self, section: str, path: str, value: Any
    ) -> None:
        result = validate_scenario_document(_action_step_document(value, section))

        assert _issues(result) == [(path, "action_expect_invalid", _EXPECT_MESSAGE)]
        assert result.scenario is None

    @pytest.mark.parametrize("section, path", _SECTIONS, ids=["steps", "cleanup"])
    @pytest.mark.parametrize(
        "value", _EXPECT_VALUES, ids=["absent", "ok", "refused", "any"]
    )
    def test_valid_value_loads_and_is_kept(
        self, section: str, path: str, value: Any
    ) -> None:
        result = validate_scenario_document(_action_step_document(value, section))

        assert result.ok, _issues(result)
        assert result.scenario is not None
        step = (
            result.scenario.steps[1]
            if section == "steps"
            else result.scenario.cleanup[0]
        )
        assert step.parameters.get("expect", _ABSENT) == value

    def test_file_loader_reports_the_same_issue(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _action_step_document("bogus", "steps"))

        result = load_scenario_file(path)

        assert _issues(result) == [
            ("steps[1].expect", "action_expect_invalid", _EXPECT_MESSAGE)
        ]

    def test_every_supported_kind_other_than_action_is_covered(self) -> None:
        assert set(_MINIMAL_STEPS) == set(_VALID_STEP_KINDS) - {"action"}

    @pytest.mark.parametrize("section", ["steps", "cleanup"])
    @pytest.mark.parametrize("kind", sorted(_MINIMAL_STEPS))
    def test_expect_on_a_step_that_is_not_an_action_is_rejected(
        self, kind: str, section: str
    ) -> None:
        path = "steps[1].expect" if section == "steps" else "cleanup[0].expect"
        control = validate_scenario_document(_kind_document(kind, section))
        assert control.ok, _issues(control)
        message = f"expect applies to action steps only, not to {kind} steps"

        for value in ("refused", "any", "ok", "bogus", True):
            result = validate_scenario_document(_kind_document(kind, section, value))
            assert _issues(result) == [(path, "expect_not_supported", message)], value

    def test_keys_that_only_start_with_expect_are_not_affected(self) -> None:
        document = _document(
            [
                {
                    "id": "maybe",
                    "kind": "find",
                    "role": "push_button",
                    "expect_absent": True,
                },
                {
                    "id": "check",
                    "kind": "assert",
                    "assertion": {
                        "id": "absent-text",
                        "kind": "text_or_name_matches",
                        "pattern": "Error",
                        "expect_present": False,
                    },
                },
            ]
        )

        assert validate_scenario_document(document).ok

    def test_expect_on_an_unsupported_kind_reports_only_the_kind(self) -> None:
        document = _document([{"id": "odd", "kind": "bogus", "expect": "refused"}])

        result = validate_scenario_document(document)

        assert [(i.path, i.code) for i in result.issues] == [
            ("steps[1].kind", "unsupported_step_kind")
        ]


# ═══════════════════════════════════════════════════════════════════════
# Standalone commands and failures of the setup are unchanged
# ═══════════════════════════════════════════════════════════════════════


def _unknown_node_action_uc() -> ActionUseCase:
    return _linux_uc(_ScriptedPort(), stored_tree=_do_tree())


def _do_uc_with_unrelated_stored_tree() -> CompoundDoUseCase:
    store = FakeTreeStore()
    other = AccessibilityTree(
        roots=(
            Node(
                id="n_500",
                role="frame",
                name="Other",
                bounds=(0, 0, 1, 1),
                states=(),
                actions=(),
            ),
        )
    )
    store.save_tree(_SESSION_ID, other, None)
    return CompoundDoUseCase(
        FakeAccessibilityTree(_do_tree()),
        _ScriptedPort(),
        _repo(),
        store,
        FakeClock(),
    )


class TestUnchangedBehaviour:
    def test_standalone_action_on_a_node_unknown_to_the_stored_tree(self) -> None:
        cli_result = _invoke_cli(
            "action_uc", _unknown_node_action_uc(), ["action", "n_777", "click"]
        )
        is_error, text = _call_mcp(
            "action_uc",
            _unknown_node_action_uc(),
            "action",
            {"node_id": "n_777", "action_name": "click"},
        )

        assert (cli_result.exit_code, cli_result.stdout) == (1, "")
        assert cli_result.stderr == f"Error: {_UNKNOWN_NODE_TEXT}\n"
        assert (is_error, text) == (True, _UNKNOWN_NODE_TEXT)

    def test_standalone_do_with_a_stored_tree_without_the_node(self) -> None:
        argv = ["do", "--role", "push_button", "--name", "Target", "--action", "click"]
        cli_result = _invoke_cli(
            "compound_do_uc", _do_uc_with_unrelated_stored_tree(), argv
        )
        is_error, text = _call_mcp(
            "compound_do_uc",
            _do_uc_with_unrelated_stored_tree(),
            "do",
            {"role": "push_button", "name_pattern": "Target", "action_name": "click"},
        )

        assert cli_result.exit_code == 0
        assert json.loads(cli_result.stdout) == {
            "found": {
                "id": _NODE,
                "role": "push_button",
                "name": "Target",
                "bounds": [10, 20, 90, 30],
                "states": ["enabled", "sensitive"],
                "actions": ["click", "focus"],
            },
            "action": {"status": "ok", "action": "click", "target": _NODE},
        }
        assert is_error is False
        assert text == cli_result.stdout.rstrip("\n")

    @pytest.mark.parametrize(
        "message",
        [
            "Session not found: s",
            "adb action failed (rc=1): x",
            _UNKNOWN_NODE_TEXT,
        ],
        ids=["session_not_found", "backend_failure", "unknown_node_wording"],
    )
    def test_best_effort_step_still_fails_when_the_dependency_raises(
        self, message: str
    ) -> None:
        rig = _rig(_RaisingAction(RuntimeError(message)))
        _start(rig)

        step = rig.executor.execute(
            ScenarioStep(
                id=_STEP,
                kind="action",
                parameters={"node_id": _NODE, "action": "click", "expect": "any"},
            )
        )

        assert (step.status, step.error, step.output) == (
            "failed",
            message,
            {"expect": "any"},
        )


# ═══════════════════════════════════════════════════════════════════════
# Documentation
# ═══════════════════════════════════════════════════════════════════════


def _read(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _level_two_sections(text: str) -> Dict[str, str]:
    """Each ``## `` heading of a document mapped to its section text."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    sections: Dict[str, str] = {}
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        heading = lines[start][3:].strip()
        assert heading not in sections, f"heading {heading!r} occurs twice"
        sections[heading] = "\n".join(lines[start:end]).rstrip()
    return sections


def _blocks(text: str) -> List[str]:
    """Paragraphs and list items of a section, continuation lines joined."""
    blocks: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        starts_item = bool(re.match(r"^([-*]|\d+\.)\s", stripped))
        if not stripped or starts_item or stripped.startswith("#"):
            if current:
                blocks.append(" ".join(current))
            current = [stripped] if stripped and not stripped.startswith("#") else []
            continue
        current.append(stripped)
    if current:
        blocks.append(" ".join(current))
    return blocks


def _sentences(text: str) -> List[str]:
    sentences: List[str] = []
    for block in _blocks(text):
        sentences.extend(
            s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z`\"*(])", block) if s
        )
    return sentences


_POLICY_HEADING = "## Action Step Policy"

# sha256 of the text of sections that keep their wording, heading line included.
_KEPT_RELEASE_SECTIONS = {
    "Optional CI Pattern": "9d1c5630af9c2c50ece92f3af2baaead7a6dfbf78388c9b01a160bf538ddb2db",
    "Wait Timeout Policy": "fe698cbb633c285f68744817e77f8d5fed153fc5fd4d2b62f01c7ef3dfda7015",
    "MCP Parity": "f3e2874861da92cca164e70f0ba16d38395aba3edecd1727f496117ea44afa0f",
}

_POLICY_TERMS = [
    '"expect"',
    '"ok"',
    '"refused"',
    '"any"',
    "allow_timeout",
    "action_failed",
    "action_not_refused",
    "action_expect_invalid",
    "expect_not_supported",
    "could not confirm the target was there",
    "cannot be observed",
    '"action": "focus"',
    "does not offer",
]

# Advice that a find before the step makes the refusal observable; it does not.
_REQUIRED_FIND_ADVICE = ("required `find` right before", "required find right before")

_ERROR_HANDLING_BULLETS = [
    "- Exit code 0 + JSON on stdout = success",
    "- Exit code 1 + text on stderr = error",
    '- Semantic failures (e.g., action not available) still return exit 0 with `"status": "error"` in JSON',
    "- Always check both exit code and JSON status field",
]

_CHANGELOG_TERMS = [
    '"expect"',
    '"refused"',
    '"any"',
    "action_failed",
    "action_not_refused",
    "action_expect_invalid",
    "expect_not_supported",
    '"action": "focus"',
    "does not offer",
]


def _policy_section(text: str) -> str:
    """The action-step policy section of a document, found by its own heading.

    The section runs from the heading line up to the next ``## `` line, or to
    the end of the document.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line == _POLICY_HEADING]
    assert len(starts) == 1, f"expected one {_POLICY_HEADING!r} line, found {starts}"
    end = next(
        (i for i in range(starts[0] + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[starts[0] : end])


def _manual_error_handling() -> str:
    lines = _read("docs/manual.md").splitlines()
    starts = [i for i, line in enumerate(lines) if line == "### Error handling"]
    assert len(starts) == 1
    body: List[str] = []
    for line in lines[starts[0] + 1 :]:
        if re.match(r"^#{1,4} ", line):
            break
        body.append(line)
    return "\n".join(body)


def _changelog_unreleased_fixed() -> List[str]:
    lines = _read("CHANGELOG.md").splitlines()
    unreleased = lines.index("## [Unreleased]")
    end = next(
        (i for i in range(unreleased + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    section = lines[unreleased + 1 : end]
    fixed = section.index("### Fixed")
    fixed_end = next(
        (i for i in range(fixed + 1, len(section)) if section[i].startswith("#")),
        len(section),
    )
    return section[fixed + 1 : fixed_end]


def _entries_after_the_linux_action_changes() -> str:
    """Fixed entries placed after the visible-changes entry for Linux actions."""
    fixed = _changelog_unreleased_fixed()
    anchor = "- **Visible changes for callers of `action`, `do`, `goto` and `menu` on Linux**"
    starts = [i for i, line in enumerate(fixed) if line.startswith(anchor)]
    assert len(starts) == 1
    after = starts[0] + 1
    while after < len(fixed) and not fixed[after].startswith("- "):
        after += 1
    return "\n".join(fixed[after:]).strip()


class TestDocumentation:
    def test_release_scenarios_has_a_section_on_the_action_step_policy(self) -> None:
        section = _policy_section(_read("docs/release-scenarios.md"))

        for term in _POLICY_TERMS:
            assert term in section, term
        for advice in _REQUIRED_FIND_ADVICE:
            assert advice not in section, advice
        sentences = _sentences(section)
        assert any("standalone" in s and "exit 0" in s for s in sentences)
        assert any(
            "Android" in s and "action_not_refused" in s and "click" in s
            for s in sentences
        )
        assert any("Android" in s and "never passes" in s for s in sentences)

    def test_policy_section_is_found_by_its_own_heading(self) -> None:
        text = _read("docs/release-scenarios.md")
        lines = text.splitlines()
        assert lines.count("## MCP Parity") == 1
        at = lines.index("## MCP Parity")
        unrelated = ["## Unrelated Guidance", "", "Advice on something else.", ""]
        extended = "\n".join(lines[:at] + unrelated + lines[at:])

        assert len(_level_two_sections(extended)) == len(_level_two_sections(text)) + 1
        assert _policy_section(extended) == _policy_section(text)

    @pytest.mark.parametrize("heading", sorted(_KEPT_RELEASE_SECTIONS))
    def test_release_scenarios_keeps_the_existing_sections(self, heading: str) -> None:
        sections = _level_two_sections(_read("docs/release-scenarios.md"))

        digest = hashlib.sha256(sections[heading].encode("utf-8")).hexdigest()
        assert digest == _KEPT_RELEASE_SECTIONS[heading]

    def test_manual_says_scenario_action_steps_fail_on_an_error(self) -> None:
        section = _manual_error_handling()

        assert section.count('"expect"') == 1
        assert section.count("docs/release-scenarios.md") == 1
        block = next(b for b in _blocks(section) if '"expect"' in b)
        assert "docs/release-scenarios.md" in block
        assert "scenario" in block

    def test_manual_keeps_the_error_handling_bullets(self) -> None:
        lines = _manual_error_handling().splitlines()

        positions = [lines.index(bullet) for bullet in _ERROR_HANDLING_BULLETS]
        assert positions == sorted(positions)

    def test_changelog_no_longer_says_the_step_still_passes(self) -> None:
        assert (
            "but their pass/fail outcome is unchanged: the step still passes"
            not in (_read("CHANGELOG.md"))
        )

    def test_changelog_describes_the_scenario_change_once(self) -> None:
        entry = _entries_after_the_linux_action_changes()

        assert entry
        for term in _CHANGELOG_TERMS:
            assert term in entry, term
        assert "cannot be used on a target that offers no actions" not in entry
        changelog = _read("CHANGELOG.md")
        assert changelog.count("action_expect_invalid") == entry.count(
            "action_expect_invalid"
        )
