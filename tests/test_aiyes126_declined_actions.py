"""Linux requests to nodes that are not enabled, and declined requests, are errors.

The AT-SPI worker asks the application to act through ``do_action``,
``Component.grab_focus`` and ``set_text_contents``. A truthy result shows only that
the application accepted the request, not that it acted on it. A node with neither
ENABLED nor SENSITIVE is greyed out: a user could not operate it.

These tests pin the resulting behaviour end to end:

* the worker honours a falsy return from every call ("declined"), and refuses
  a node that is not enabled before asking the application to act
  ("not_enabled"); an unreadable state set never blocks a request;
* the reason travels on the port result and the action, do, goto and menu use
  cases turn it into a message that says what happened;
* CLI and MCP outcomes follow the documented error handling (action, do, goto:
  exit 0 with ``"status": "error"``; menu: exit 1 with the message on stderr);
* responses outside these cases are unchanged, scenario action steps keep their
  pass/fail outcome, and the documentation describes the new outcomes.

All accessibles, state sets and AT-SPI bindings are in-memory doubles, so no
display, accessibility bus or toolkit is needed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import json
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest.mock import MagicMock, patch

import pytest

import aiyes.adapters.atspi_action_adapter as action_adapter_mod
from aiyes.adapters import atspi_persistent_worker as persistent_worker
from aiyes.adapters import atspi_subprocess_worker as worker
from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.cli.main import cli
from aiyes.cli.presenter import format_do
from aiyes.domain.scenario import ScenarioStep
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import ActionPortResult
from aiyes.domain.use_cases.action import ActionUseCase
from aiyes.domain.use_cases.compound_do import (
    CompoundActionResult,
    CompoundDoUseCase,
)
from aiyes.domain.use_cases.goto import GotoUseCase
from aiyes.domain.use_cases.menu import MenuUseCase

from tests.conftest import (
    FakeAccessibilityTree,
    FakeClock,
    FakeInput,
    FakeSessionRepository,
    FakeTreeStore,
    make_cli_runner,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _Missing:
    """Value reported for an attribute the object does not have."""

    def __repr__(self) -> str:
        return "<attribute missing>"


_MISSING = _Missing()


# ═══════════════════════════════════════════════════════════════════════
# In-memory AT-SPI bindings and accessibles for the worker
# ═══════════════════════════════════════════════════════════════════════

# Same names and values as Atspi.StateType in the installed GObject bindings.
StateType = enum.IntEnum(
    "StateType",
    [
        ("INVALID", 0),
        ("ACTIVE", 1),
        ("ARMED", 2),
        ("BUSY", 3),
        ("CHECKED", 4),
        ("COLLAPSED", 5),
        ("DEFUNCT", 6),
        ("EDITABLE", 7),
        ("ENABLED", 8),
        ("EXPANDABLE", 9),
        ("EXPANDED", 10),
        ("FOCUSABLE", 11),
        ("FOCUSED", 12),
        ("HAS_TOOLTIP", 13),
        ("HORIZONTAL", 14),
        ("ICONIFIED", 15),
        ("MODAL", 16),
        ("MULTI_LINE", 17),
        ("MULTISELECTABLE", 18),
        ("OPAQUE", 19),
        ("PRESSED", 20),
        ("RESIZABLE", 21),
        ("SELECTABLE", 22),
        ("SELECTED", 23),
        ("SENSITIVE", 24),
        ("SHOWING", 25),
        ("SINGLE_LINE", 26),
        ("STALE", 27),
        ("TRANSIENT", 28),
        ("VERTICAL", 29),
        ("VISIBLE", 30),
        ("MANAGES_DESCENDANTS", 31),
        ("INDETERMINATE", 32),
        ("REQUIRED", 33),
        ("TRUNCATED", 34),
        ("ANIMATED", 35),
        ("INVALID_ENTRY", 36),
        ("SUPPORTS_AUTOCOMPLETION", 37),
        ("SELECTABLE_TEXT", 38),
        ("IS_DEFAULT", 39),
        ("VISITED", 40),
        ("CHECKABLE", 41),
        ("HAS_POPUP", 42),
        ("READ_ONLY", 43),
        ("LAST_DEFINED", 44),
    ],
)

# Marks a call that raises instead of returning.
_RAISE = object()


def _answer(result: Any) -> Any:
    if result is _RAISE:
        raise RuntimeError("accessibility call failed")
    return result


class _RenderedState:
    """A state object that only renders as text."""

    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text


class _FakeStateSet:
    """State set double answering membership through ``contains``."""

    def __init__(
        self,
        *present: str,
        rendered: Optional[Sequence[Any]] = None,
        contains_raises: bool = False,
        get_states_raises: bool = False,
    ) -> None:
        self._present = frozenset(StateType[name] for name in present)
        self._rendered = rendered
        self._contains_raises = contains_raises
        self._get_states_raises = get_states_raises

    def contains(self, state: Any) -> bool:
        if self._contains_raises:
            raise RuntimeError("state query failed")
        return state in self._present

    def is_empty(self) -> bool:
        if self._contains_raises:
            raise RuntimeError("state query failed")
        return not self._present

    def get_states(self) -> List[Any]:
        if self._get_states_raises:
            raise RuntimeError("state list unavailable")
        if self._rendered is not None:
            return list(self._rendered)
        return sorted(self._present)


# Measured shapes: enabled widgets carry ENABLED and SENSITIVE; disabled
# widgets carry neither (but stay focusable, showing and visible); a disabled
# item of a closed Qt menu reports an empty state set.
_ENABLED_STATES = ("ENABLED", "SENSITIVE", "FOCUSABLE", "SHOWING", "VISIBLE")
_DISABLED_STATES = ("FOCUSABLE", "SHOWING", "VISIBLE")


class _FakeComponent:
    def __init__(self, node: "_FakeAccessible", result: Any) -> None:
        self._node = node
        self._result = result

    def grab_focus(self) -> Any:
        self._node.act_calls.append(("grab_focus",))
        return _answer(self._result)


class _FakeEditableText:
    def __init__(self, node: "_FakeAccessible", result: Any) -> None:
        self._node = node
        self._result = result

    def set_text_contents(self, value: str) -> Any:
        self._node.act_calls.append(("set_text_contents", value))
        return _answer(self._result)


class _FakeAccessible:
    """Accessible double recording every call that asks the app to act.

    ``act_calls`` lists, in order, ``("do_action", <action name>)``,
    ``("grab_focus",)``, ``("set_text_contents", value)`` (editable-text
    interface) and ``("fallback_set_text_contents", value)`` (the accessible's
    own method).
    """

    def __init__(
        self,
        *,
        actions: Sequence[str] = (),
        states: Any = None,
        do_action: Any = True,
        grab_focus: Any = True,
        component: bool = True,
        editable: Any = True,
        editable_result: Any = True,
        fallback_result: Any = True,
        readback: str = "typed text",
    ) -> None:
        self._actions = list(actions)
        self._states = _FakeStateSet(*_ENABLED_STATES) if states is None else states
        self._do_action = do_action
        self._grab_focus = grab_focus
        self._component = component
        self._editable = editable
        self._editable_result = editable_result
        self._fallback_result = fallback_result
        self._readback = readback
        self.act_calls: List[Tuple[Any, ...]] = []
        self.readbacks = 0

    def get_role_name(self) -> str:
        return "push button"

    def get_name(self) -> str:
        return "Target"

    def get_n_actions(self) -> int:
        return len(self._actions)

    def get_action_name(self, index: int) -> str:
        return self._actions[index]

    def do_action(self, index: int) -> Any:
        self.act_calls.append(("do_action", self._actions[index]))
        return _answer(self._do_action)

    def get_component(self) -> Optional[_FakeComponent]:
        if not self._component:
            return None
        return _FakeComponent(self, self._grab_focus)

    def get_editable_text(self) -> Optional[_FakeEditableText]:
        if self._editable is _RAISE:
            raise RuntimeError("editable text interface failed")
        if not self._editable:
            return None
        return _FakeEditableText(self, self._editable_result)

    def set_text_contents(self, value: str) -> Any:
        self.act_calls.append(("fallback_set_text_contents", value))
        return _answer(self._fallback_result)

    def get_text(self, start: int, end: int) -> str:
        self.readbacks += 1
        return self._readback

    def get_state_set(self) -> Any:
        return _answer(self._states)


def _fake_atspi_modules() -> Dict[str, types.ModuleType]:
    atspi = types.ModuleType("gi.repository.Atspi")
    atspi.StateType = StateType  # type: ignore[attr-defined]
    atspi.get_desktop = lambda index: object()  # type: ignore[attr-defined]
    repository = types.ModuleType("gi.repository")
    repository.Atspi = atspi  # type: ignore[attr-defined]
    gi = types.ModuleType("gi")
    gi.require_version = lambda namespace, version: None  # type: ignore[attr-defined]
    gi.repository = repository  # type: ignore[attr-defined]
    return {"gi": gi, "gi.repository": repository, "gi.repository.Atspi": atspi}


_BUS = "unix:abstract=/tmp/dbus-test"


def _run_worker(
    node: Optional[_FakeAccessible], action_name: str, value: Optional[str] = None
) -> Dict[str, Any]:
    """Run the worker's action entry point against an in-memory accessible."""
    with (
        patch.dict(sys.modules, _fake_atspi_modules()),
        patch.object(worker, "_setup_env"),
        patch.object(worker, "_search_tree", return_value=node),
    ):
        return worker._do_action_impl(":99", _BUS, "n_009", action_name, value)


def _run_persistent_worker(
    node: _FakeAccessible, action_name: str, value: Optional[str] = None
) -> Any:
    """Run the same request through the persistent worker's dispatcher."""
    request = {
        "node_id": "n_009",
        "action_name": action_name,
        "value": value,
        "registry": None,
    }
    with (
        patch.dict(sys.modules, _fake_atspi_modules()),
        patch.object(worker, "_setup_env"),
        patch.object(worker, "_search_tree", return_value=node),
    ):
        return persistent_worker._dispatch("do_action", request, ":99", _BUS)


def _declined(available: List[str]) -> Dict[str, Any]:
    return {
        "success": False,
        "available_actions": available,
        "node_value": None,
        "node_states": None,
        "failure_reason": "declined",
    }


def _not_enabled(available: List[str]) -> Dict[str, Any]:
    return {
        "success": False,
        "available_actions": available,
        "node_value": None,
        "node_states": None,
        "failure_reason": "not_enabled",
    }


def _without_null_reason(response: Dict[str, Any]) -> Dict[str, Any]:
    """Drop a ``failure_reason`` that is null; any other value is kept."""
    trimmed = dict(response)
    if "failure_reason" in trimmed and trimmed["failure_reason"] is None:
        del trimmed["failure_reason"]
    return trimmed


def _rendered(states: _FakeStateSet) -> List[str]:
    return [str(state) for state in states.get_states()]


def _enabled_check_warnings(stderr: str) -> int:
    count = 0
    for line in stderr.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("warning") == "enabled_check":
            count += 1
    return count


@dataclasses.dataclass(frozen=True)
class _Request:
    """One kind of request that asks the application to act."""

    actions: Tuple[str, ...]
    action_name: str
    value: Optional[str]
    reported_actions: List[str]
    expected_act_call: Tuple[Any, ...]


_REQUESTS = {
    "listed_action": _Request(
        ("click",), "click", None, ["click"], ("do_action", "click")
    ),
    "focus_action": _Request(
        ("focus",), "focus", None, ["focus"], ("do_action", "focus")
    ),
    "focus_by_grab": _Request(("Press",), "focus", None, ["Press"], ("grab_focus",)),
    "set_text": _Request(
        ("activate",), "set_text", "hi", ["set_text"], ("set_text_contents", "hi")
    ),
}


def _request_node(
    request: _Request, states: Any, result: Any = True
) -> _FakeAccessible:
    """Accessible for ``request`` whose act call returns ``result``."""
    return _FakeAccessible(
        actions=request.actions,
        states=states,
        do_action=result,
        grab_focus=result,
        editable_result=result,
    )


# ═══════════════════════════════════════════════════════════════════════
# Worker: the result of every request is honoured
# ═══════════════════════════════════════════════════════════════════════


class TestWorkerHonoursTheResultOfTheRequest:
    """A falsy return means the application did not carry the request out."""

    @pytest.mark.parametrize("returned", [False, None], ids=["false", "none"])
    def test_listed_action_the_application_declines_is_reported_as_declined(
        self, returned: Any
    ) -> None:
        node = _FakeAccessible(actions=["click"], do_action=returned)

        assert _run_worker(node, "click") == _declined(["click"])
        assert node.act_calls == [("do_action", "click")]

    def test_listed_action_the_application_carries_out_succeeds(self) -> None:
        node = _FakeAccessible(actions=["click"], do_action=True)

        assert _without_null_reason(_run_worker(node, "click")) == {
            "success": True,
            "available_actions": ["click"],
            "node_value": None,
            "node_states": None,
        }

    @pytest.mark.parametrize("returned", [False, None], ids=["false", "none"])
    def test_focus_action_the_application_declines_is_reported_as_declined(
        self, returned: Any
    ) -> None:
        node = _FakeAccessible(actions=["focus"], do_action=returned)

        assert _run_worker(node, "focus") == _declined(["focus"])
        assert node.act_calls == [("do_action", "focus")]

    def test_focus_action_the_application_carries_out_reports_the_states(self) -> None:
        states = _FakeStateSet(*_ENABLED_STATES)
        node = _FakeAccessible(actions=["focus"], states=states, do_action=True)

        assert _without_null_reason(_run_worker(node, "focus")) == {
            "success": True,
            "available_actions": ["focus"],
            "node_value": None,
            "node_states": _rendered(states),
        }

    @pytest.mark.parametrize("returned", [False, None], ids=["false", "none"])
    def test_grab_focus_the_application_declines_is_reported_as_declined(
        self, returned: Any
    ) -> None:
        node = _FakeAccessible(actions=["Press"], grab_focus=returned)

        assert _run_worker(node, "focus") == _declined(["Press"])
        assert node.act_calls == [("grab_focus",)]

    def test_grab_focus_the_application_carries_out_reports_the_states(self) -> None:
        states = _FakeStateSet(*_ENABLED_STATES)
        node = _FakeAccessible(actions=["Press"], states=states, grab_focus=True)

        assert _without_null_reason(_run_worker(node, "focus")) == {
            "success": True,
            "available_actions": ["Press"],
            "node_value": None,
            "node_states": _rendered(states),
        }

    @pytest.mark.parametrize("returned", [False, None], ids=["false", "none"])
    def test_set_text_declined_by_the_editable_text_interface_is_final(
        self, returned: Any
    ) -> None:
        node = _FakeAccessible(editable=True, editable_result=returned)

        assert _run_worker(node, "set_text", "hi") == _declined(["set_text"])
        assert node.act_calls == [("set_text_contents", "hi")]
        assert node.readbacks == 0

    @pytest.mark.parametrize("returned", [False, None], ids=["false", "none"])
    def test_set_text_declined_by_the_fallback_is_reported_as_declined(
        self, returned: Any
    ) -> None:
        node = _FakeAccessible(editable=False, fallback_result=returned)

        assert _run_worker(node, "set_text", "hi") == _declined(["set_text"])
        assert node.act_calls == [("fallback_set_text_contents", "hi")]
        assert node.readbacks == 0

    @pytest.mark.parametrize(
        "editable, editable_result",
        [(_RAISE, True), (True, _RAISE)],
        ids=["interface_raises", "call_raises"],
    )
    def test_set_text_falls_back_when_the_editable_text_interface_fails(
        self, editable: Any, editable_result: Any
    ) -> None:
        node = _FakeAccessible(
            editable=editable, editable_result=editable_result, fallback_result=True
        )

        assert _without_null_reason(_run_worker(node, "set_text", "hi")) == {
            "success": True,
            "available_actions": ["set_text"],
            "node_value": "typed text",
            "node_states": None,
        }
        assert node.act_calls[-1] == ("fallback_set_text_contents", "hi")

    def test_set_text_carried_out_reads_the_value_back(self) -> None:
        node = _FakeAccessible(editable=True, editable_result=True)

        assert _without_null_reason(_run_worker(node, "set_text", "hi")) == {
            "success": True,
            "available_actions": ["set_text"],
            "node_value": "typed text",
            "node_states": None,
        }
        assert node.act_calls == [("set_text_contents", "hi")]

    def test_set_text_fails_without_a_reason_when_the_fallback_raises(self) -> None:
        node = _FakeAccessible(editable=False, fallback_result=_RAISE)

        assert _without_null_reason(_run_worker(node, "set_text", "hi")) == {
            "success": False,
            "available_actions": ["set_text"],
            "node_value": None,
            "node_states": None,
        }


# ═══════════════════════════════════════════════════════════════════════
# Worker: a node that is not enabled is refused before any request
# ═══════════════════════════════════════════════════════════════════════


class TestWorkerChecksTheEnabledState:
    """Neither ENABLED nor SENSITIVE: refused, and the app is never asked."""

    @pytest.mark.parametrize("kind", sorted(_REQUESTS))
    def test_node_that_is_not_enabled_is_refused_without_a_request(
        self, kind: str
    ) -> None:
        request = _REQUESTS[kind]
        node = _request_node(request, _FakeStateSet(*_DISABLED_STATES))

        response = _run_worker(node, request.action_name, request.value)

        assert response == _not_enabled(request.reported_actions)
        assert node.act_calls == []

    @pytest.mark.parametrize("kind", sorted(_REQUESTS))
    def test_node_with_an_empty_state_set_is_refused_without_a_request(
        self, kind: str
    ) -> None:
        request = _REQUESTS[kind]
        node = _request_node(request, _FakeStateSet())

        response = _run_worker(node, request.action_name, request.value)

        assert response == _not_enabled(request.reported_actions)
        assert node.act_calls == []

    @pytest.mark.parametrize("state", ["ENABLED", "SENSITIVE"])
    @pytest.mark.parametrize("kind", sorted(_REQUESTS))
    def test_node_with_either_enabled_or_sensitive_is_asked_to_act(
        self, kind: str, state: str
    ) -> None:
        request = _REQUESTS[kind]
        node = _request_node(request, _FakeStateSet(state, "SHOWING", "VISIBLE"))

        response = _run_worker(node, request.action_name, request.value)

        assert node.act_calls == [request.expected_act_call]
        assert response["success"] is True
        assert response.get("failure_reason") is None

    @pytest.mark.parametrize("kind", sorted(_REQUESTS))
    def test_enabled_check_ignores_how_the_states_render(self, kind: str) -> None:
        request = _REQUESTS[kind]
        states = _FakeStateSet(
            rendered=[_RenderedState("enabled"), _RenderedState("sensitive")]
        )
        node = _request_node(request, states)

        response = _run_worker(node, request.action_name, request.value)

        assert response == _not_enabled(request.reported_actions)
        assert node.act_calls == []

    @pytest.mark.parametrize("kind", sorted(_REQUESTS))
    def test_enabled_check_does_not_need_the_rendered_states(
        self, kind: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        request = _REQUESTS[kind]
        states = _FakeStateSet(*_ENABLED_STATES, get_states_raises=True)
        node = _request_node(request, states)

        response = _run_worker(node, request.action_name, request.value)

        assert node.act_calls == [request.expected_act_call]
        assert response["success"] is True
        assert response.get("failure_reason") is None
        assert _enabled_check_warnings(capsys.readouterr().err) == 0

    def test_action_the_node_does_not_offer_is_reported_as_before(self) -> None:
        node = _FakeAccessible(actions=["click"], states=_FakeStateSet())

        assert _without_null_reason(_run_worker(node, "Press")) == {
            "success": False,
            "available_actions": ["click"],
            "node_value": None,
            "node_states": None,
        }
        assert node.act_calls == []


# ═══════════════════════════════════════════════════════════════════════
# Worker: an unreadable state set never produces an error by itself
# ═══════════════════════════════════════════════════════════════════════


class _NoStateSetAccessible(_FakeAccessible):
    """Accessible whose ``get_state_set()`` returns None."""

    def get_state_set(self) -> Any:
        return None


def _state_set_raises(request: _Request, result: Any) -> _FakeAccessible:
    return _request_node(request, _RAISE, result)


def _state_set_missing(request: _Request, result: Any) -> _FakeAccessible:
    return _NoStateSetAccessible(
        actions=request.actions,
        do_action=result,
        grab_focus=result,
        editable_result=result,
    )


def _contains_raises(request: _Request, result: Any) -> _FakeAccessible:
    states = _FakeStateSet(*_ENABLED_STATES, contains_raises=True)
    return _request_node(request, states, result)


_UNREADABLE_STATES = {
    "state_set_raises": _state_set_raises,
    "state_set_missing": _state_set_missing,
    "contains_raises": _contains_raises,
}


def _unreadable_node(request: _Request, mode: str, result: Any) -> _FakeAccessible:
    return _UNREADABLE_STATES[mode](request, result)


class TestWorkerUnreadableStateSet:
    """An inconclusive check warns once and lets the request through."""

    @pytest.mark.parametrize("kind", sorted(_REQUESTS))
    @pytest.mark.parametrize("mode", sorted(_UNREADABLE_STATES))
    def test_unreadable_state_set_does_not_block_the_request(
        self, mode: str, kind: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        request = _REQUESTS[kind]
        node = _unreadable_node(request, mode, True)

        response = _run_worker(node, request.action_name, request.value)

        assert node.act_calls == [request.expected_act_call]
        assert response["success"] is True
        assert response.get("failure_reason") is None
        assert _enabled_check_warnings(capsys.readouterr().err) == 1

    @pytest.mark.parametrize("mode", sorted(_UNREADABLE_STATES))
    def test_unreadable_state_set_still_honours_a_declined_request(
        self, mode: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        node = _unreadable_node(_REQUESTS["listed_action"], mode, False)

        assert _run_worker(node, "click") == _declined(["click"])
        assert node.act_calls == [("do_action", "click")]
        assert _enabled_check_warnings(capsys.readouterr().err) == 1


# ═══════════════════════════════════════════════════════════════════════
# Worker: responses outside the new cases are unchanged
# ═══════════════════════════════════════════════════════════════════════


def _failed(available: List[str]) -> Dict[str, Any]:
    return {
        "success": False,
        "available_actions": available,
        "node_value": None,
        "node_states": None,
    }


_UNCHANGED_RESPONSES = {
    "node_not_found": (
        lambda: None,
        "click",
        None,
        {"success": False, "available_actions": []},
    ),
    "action_not_offered": (
        lambda: _FakeAccessible(actions=["click"]),
        "Press",
        None,
        _failed(["click"]),
    ),
    "listed_action_raises": (
        lambda: _FakeAccessible(actions=["click"], do_action=_RAISE),
        "click",
        None,
        _failed(["click"]),
    ),
    "focus_action_raises": (
        lambda: _FakeAccessible(actions=["focus"], do_action=_RAISE),
        "focus",
        None,
        _failed(["focus"]),
    ),
    "grab_focus_raises": (
        lambda: _FakeAccessible(actions=["Press"], grab_focus=_RAISE),
        "focus",
        None,
        _failed(["Press"]),
    ),
    "focus_without_component": (
        lambda: _FakeAccessible(actions=["Press"], component=False),
        "focus",
        None,
        _failed(["Press"]),
    ),
    "set_text_without_value": (
        lambda: _FakeAccessible(actions=["click"]),
        "set_text",
        None,
        _failed(["click"]),
    ),
}


class TestWorkerUnchangedResponses:
    """Cases that are neither declined nor refused answer exactly as before."""

    @pytest.mark.parametrize("case", sorted(_UNCHANGED_RESPONSES))
    def test_response_is_unchanged(self, case: str) -> None:
        make_node, action_name, value, expected = _UNCHANGED_RESPONSES[case]

        response = _run_worker(make_node(), action_name, value)

        assert _without_null_reason(response) == expected

    @pytest.mark.parametrize(
        "states",
        [
            ("ENABLED", "SENSITIVE", "READ_ONLY"),
            ("ENABLED", "SENSITIVE", "EDITABLE", "READ_ONLY"),
        ],
        ids=["read_only", "editable_read_only"],
    )
    def test_set_text_on_an_enabled_read_only_field_is_still_requested(
        self, states: Tuple[str, ...]
    ) -> None:
        node = _FakeAccessible(states=_FakeStateSet(*states), editable_result=True)

        response = _run_worker(node, "set_text", "ro")

        assert node.act_calls == [("set_text_contents", "ro")]
        assert response["success"] is True
        assert response.get("failure_reason") is None


# ═══════════════════════════════════════════════════════════════════════
# Persistent worker: same answers as the one-shot worker
# ═══════════════════════════════════════════════════════════════════════

_PARITY_CASES = {
    "listed_action_declined": (
        lambda: _FakeAccessible(actions=["click"], do_action=False),
        "click",
        None,
        _declined(["click"]),
    ),
    "grab_focus_declined": (
        lambda: _FakeAccessible(actions=["Press"], grab_focus=False),
        "focus",
        None,
        _declined(["Press"]),
    ),
    "set_text_declined": (
        lambda: _FakeAccessible(editable=True, editable_result=False),
        "set_text",
        "hi",
        _declined(["set_text"]),
    ),
    "not_enabled": (
        lambda: _FakeAccessible(
            actions=["click"], states=_FakeStateSet(*_DISABLED_STATES)
        ),
        "click",
        None,
        _not_enabled(["click"]),
    ),
}


class TestPersistentWorkerParity:
    @pytest.mark.parametrize("case", sorted(_PARITY_CASES))
    def test_persistent_worker_answers_like_the_one_shot_worker(
        self, case: str
    ) -> None:
        make_node, action_name, value, expected = _PARITY_CASES[case]

        direct = _run_worker(make_node(), action_name, value)
        dispatched = _run_persistent_worker(make_node(), action_name, value)

        assert dispatched == direct
        assert dispatched == expected


# ═══════════════════════════════════════════════════════════════════════
# Port result and adapter: the reason crosses the port
# ═══════════════════════════════════════════════════════════════════════

_PORT_FIELDS = {field.name for field in dataclasses.fields(ActionPortResult)}


def _port_result(
    success: bool, actions: Sequence[str], failure_reason: Optional[str] = None
) -> ActionPortResult:
    """Port result carrying ``failure_reason`` when the port result has one."""
    if "failure_reason" in _PORT_FIELDS:
        return ActionPortResult(
            success=success,
            available_actions=tuple(actions),
            failure_reason=failure_reason,  # type: ignore[call-arg]
        )
    return ActionPortResult(success=success, available_actions=tuple(actions))


def _adapter_session() -> SimpleNamespace:
    return SimpleNamespace(display=":99", atspi_bus_address=_BUS, backend="linux")


def _worker_response(reason: Optional[str]) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "success": False,
        "available_actions": ["click"],
        "node_value": None,
        "node_states": None,
    }
    if reason is not None:
        response["failure_reason"] = reason
    return response


_RELAYED_REASONS = [
    ("not_enabled", "not_enabled"),
    ("declined", "declined"),
    (None, None),
]


class TestPortResultCarriesTheReason:
    @pytest.mark.parametrize(
        "sent, expected", _RELAYED_REASONS, ids=["not_enabled", "declined", "absent"]
    )
    def test_persistent_worker_reason_is_copied(
        self, sent: Optional[str], expected: Optional[str]
    ) -> None:
        connection = MagicMock()
        connection.is_alive.return_value = True
        connection.send.return_value = _worker_response(sent)
        adapter = AtSpi2ActionAdapter()
        adapter.set_worker(connection)

        result = adapter.do_action(_adapter_session(), "n_009", "click")

        assert result.success is False
        assert result.available_actions == ("click",)
        assert getattr(result, "failure_reason", _MISSING) == expected

    @pytest.mark.parametrize(
        "sent, expected", _RELAYED_REASONS, ids=["not_enabled", "declined", "absent"]
    )
    def test_one_shot_worker_reason_is_copied(
        self, sent: Optional[str], expected: Optional[str]
    ) -> None:
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = json.dumps(_worker_response(sent))
        completed.stderr = ""
        adapter = AtSpi2ActionAdapter()

        with (
            patch.object(action_adapter_mod, "_GI_AVAILABLE", True),
            patch.object(action_adapter_mod.subprocess, "run", return_value=completed),
        ):
            result = adapter.do_action(_adapter_session(), "n_009", "click")

        assert result.success is False
        assert result.available_actions == ("click",)
        assert getattr(result, "failure_reason", _MISSING) == expected

    def test_reason_is_an_optional_last_field_defaulting_to_none(self) -> None:
        names = [field.name for field in dataclasses.fields(ActionPortResult)]
        assert names[-1:] == ["failure_reason"]

        by_keyword = ActionPortResult(success=True, available_actions=())
        by_position = ActionPortResult(True, ("click",), "v", ("8",), "tap")
        assert getattr(by_keyword, "failure_reason", _MISSING) is None
        assert getattr(by_position, "failure_reason", _MISSING) is None
        assert by_position.action_method == "tap"


# ═══════════════════════════════════════════════════════════════════════
# Use cases: shared doubles
# ═══════════════════════════════════════════════════════════════════════

_SESSION_ID = "test-s"


def _linux_session() -> Session:
    return Session(
        session_id=_SESSION_ID,
        app_pid=100,
        app_command="probe",
        app_args=(),
        name=None,
        display=":99",
        atspi_bus_pid=101,
        atspi_bus_address=_BUS,
        xvfb_pid=99,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
    )


def _android_session() -> Session:
    return Session(
        session_id="android-test",
        app_pid=200,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )


def _repo(session: Optional[Session] = None) -> FakeSessionRepository:
    repo = FakeSessionRepository()
    repo.save(session or _linux_session())
    return repo


class _ReasonPort:
    """Action port that answers ``refuse_node`` (or every node) with a failure.

    Requests for any other node succeed. Every request is recorded as
    ``(node_id, action_name)``.
    """

    def __init__(
        self,
        failure_reason: Optional[str],
        actions: Sequence[str] = ("click", "focus"),
        refuse_node: Optional[str] = None,
        success: bool = False,
    ) -> None:
        self._failure_reason = failure_reason
        self._actions = tuple(actions)
        self._refuse_node = refuse_node
        self._success = success
        self.calls: List[Tuple[str, str]] = []

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        self.calls.append((node_id, action_name))
        refused = not self._success and (
            self._refuse_node is None or node_id == self._refuse_node
        )
        if not refused:
            return _port_result(True, self._actions)
        return _port_result(False, self._actions, self._failure_reason)


def _target_node(node_id: str = "n_009") -> Node:
    return Node(
        id=node_id,
        role="push_button",
        name="Target",
        bounds=(10, 20, 90, 30),
        states=("enabled", "sensitive"),
        actions=("click", "focus"),
    )


def _do_tree() -> AccessibilityTree:
    frame = Node(
        id="n_001",
        role="frame",
        name="Probe Window",
        bounds=(0, 0, 640, 480),
        states=("enabled", "showing"),
        actions=(),
        children=(_target_node(),),
    )
    return AccessibilityTree(roots=(frame,))


def _action_uc(port: Any, session: Optional[Session] = None) -> ActionUseCase:
    return ActionUseCase(port, _repo(session), FakeTreeStore())


def _do_uc(
    port: Any,
    tree: Optional[AccessibilityTree] = None,
    session: Optional[Session] = None,
) -> CompoundDoUseCase:
    return CompoundDoUseCase(
        FakeAccessibilityTree(tree or _do_tree()),
        port,
        _repo(session),
        FakeTreeStore(),
        FakeClock(),
    )


def _run_do(port: Any, action_name: str) -> Any:
    return _do_uc(port).execute(
        session_id=_SESSION_ID,
        role="push_button",
        action_name=action_name,
        name_pattern="Target",
    )


_FAILURE_REASONS = ["not_enabled", "declined", None, "something_else"]
_ACTION_NAMES = ["click", "focus", "set_text"]


def _action_reason(failure_reason: Optional[str], action: str, node_id: str) -> str:
    if failure_reason == "not_enabled":
        return f"Action '{action}' not sent: node '{node_id}' is not enabled"
    if failure_reason == "declined":
        return f"Action '{action}' on node '{node_id}' was declined by the application"
    return f"Action '{action}' not available on node '{node_id}'"


# ═══════════════════════════════════════════════════════════════════════
# action: the message says what happened
# ═══════════════════════════════════════════════════════════════════════


class TestActionUseCaseReasons:
    @pytest.mark.parametrize("failure_reason", _FAILURE_REASONS)
    @pytest.mark.parametrize("action", _ACTION_NAMES)
    def test_reason_names_the_cause(
        self, action: str, failure_reason: Optional[str]
    ) -> None:
        port = _ReasonPort(failure_reason)
        value = "hi" if action == "set_text" else None

        result = _action_uc(port).execute(_SESSION_ID, "n_009", action, value)

        assert result.status == "error"
        assert result.reason == _action_reason(failure_reason, action, "n_009")
        assert result.available_actions == ("click", "focus")
        assert result.action_method is None


# ═══════════════════════════════════════════════════════════════════════
# do: every error carries the same reason action gives
# ═══════════════════════════════════════════════════════════════════════

# format_do output for a do whose action succeeded (unchanged output).
_DO_OK_OUTPUT = (
    '{\n  "found": {\n    "id": "n_009",\n    "role": "push_button",\n'
    '    "name": "Target",\n    "bounds": [\n      10,\n      20,\n      90,\n'
    '      30\n    ],\n    "states": [\n      "enabled",\n      "sensitive"\n'
    '    ],\n    "actions": [\n      "click",\n      "focus"\n    ]\n  },\n'
    '  "action": {\n    "status": "ok",\n    "action": "click",\n'
    '    "target": "n_009"\n  }\n}'
)

_DO_NO_MATCH_OUTPUT = '{\n  "error": "No matching node found"\n}'

_DO_EMPTY_ID_OUTPUT = (
    '{\n  "found": {\n    "id": "",\n    "role": "push_button",\n'
    '    "name": "Blank",\n    "bounds": [\n      1,\n      2,\n      3,\n'
    '      4\n    ],\n    "states": [\n      "enabled"\n    ],\n'
    '    "actions": [\n      "click"\n    ]\n  },\n'
    '  "error": "Found node has invalid (empty) ID"\n}'
)


class TestDoReasons:
    @pytest.mark.parametrize("failure_reason", _FAILURE_REASONS)
    @pytest.mark.parametrize("action", _ACTION_NAMES)
    def test_do_error_reason_equals_the_action_reason(
        self, action: str, failure_reason: Optional[str]
    ) -> None:
        expected = _action_reason(failure_reason, action, "n_009")
        value = "hi" if action == "set_text" else None

        do_result = _run_do(_ReasonPort(failure_reason), action)
        action_result = _action_uc(_ReasonPort(failure_reason)).execute(
            _SESSION_ID, "n_009", action, value
        )

        assert do_result.action_result.status == "error"
        assert getattr(do_result.action_result, "reason", _MISSING) == expected
        assert action_result.reason == expected

    def test_do_success_output_is_unchanged(self) -> None:
        do_result = _run_do(_ReasonPort(None, success=True), "click")

        assert getattr(do_result.action_result, "reason", None) is None
        assert format_do(do_result) == _DO_OK_OUTPUT

    def test_action_result_built_by_keyword_has_no_reason(self) -> None:
        result = CompoundActionResult(status="ok", action="click", target="n_009")

        assert getattr(result, "reason", _MISSING) is None

    def test_do_error_output_carries_the_not_available_reason(self) -> None:
        do_result = _run_do(_ReasonPort(None), "Press")

        assert json.loads(format_do(do_result))["action"] == {
            "status": "error",
            "action": "Press",
            "target": "n_009",
            "reason": "Action 'Press' not available on node 'n_009'",
        }

    def test_do_without_a_matching_node_is_unchanged(self) -> None:
        do_result = _do_uc(_ReasonPort(None)).execute(
            session_id=_SESSION_ID,
            role="dialog",
            action_name="click",
            name_pattern="Nope",
        )

        assert format_do(do_result) == _DO_NO_MATCH_OUTPUT

    def test_do_on_a_node_with_an_empty_id_is_unchanged(self) -> None:
        blank = Node(
            id="",
            role="push_button",
            name="Blank",
            bounds=(1, 2, 3, 4),
            states=("enabled",),
            actions=("click",),
        )
        port = _ReasonPort(None)

        do_result = _do_uc(port, AccessibilityTree(roots=(blank,))).execute(
            session_id=_SESSION_ID,
            role="push_button",
            action_name="click",
            name_pattern="Blank",
        )

        assert format_do(do_result) == _DO_EMPTY_ID_OUTPUT
        assert port.calls == []

    def test_do_action_result_fields_carry_no_cause_code(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(CompoundActionResult))

        assert names == ("status", "action", "target", "reason")

    def test_android_failure_gets_the_same_reason_from_action_and_do(self) -> None:
        def android_port() -> _ReasonPort:
            return _ReasonPort(None, actions=("click", "long_click"))

        tree = AccessibilityTree(
            roots=(
                Node(
                    id="n_btn_01",
                    role="Button",
                    name="Expand",
                    bounds=(0, 0, 100, 40),
                    states=("enabled",),
                    actions=("click", "long_click"),
                ),
            )
        )
        expected = "Action 'expand' not available on node 'n_btn_01'"

        action_result = _action_uc(android_port(), _android_session()).execute(
            "android-test", "n_btn_01", "expand"
        )
        do_result = _do_uc(android_port(), tree, _android_session()).execute(
            session_id="android-test",
            role="Button",
            action_name="expand",
            name_pattern="Expand",
        )

        assert action_result.reason == expected
        assert getattr(do_result.action_result, "reason", _MISSING) == expected


# ═══════════════════════════════════════════════════════════════════════
# goto: truthful reason, and nothing is typed
# ═══════════════════════════════════════════════════════════════════════


def _goto_tree() -> AccessibilityTree:
    address_bar = Node(
        id="n_020",
        role="entry",
        name="Search or enter address",
        bounds=(100, 40, 800, 30),
        states=("enabled", "sensitive"),
        actions=("activate",),
    )
    frame = Node(
        id="n_001",
        role="frame",
        name="Browser",
        bounds=(0, 0, 1280, 800),
        states=("enabled",),
        actions=(),
        children=(address_bar,),
    )
    return AccessibilityTree(roots=(frame,))


def _goto_uc(port: Any, input_port: Optional[FakeInput] = None) -> GotoUseCase:
    return GotoUseCase(
        FakeAccessibilityTree(_goto_tree()),
        port,
        input_port or FakeInput(),
        _repo(),
    )


_GOTO_REASONS = {
    "not_enabled": "Could not focus address bar: node 'n_020' is not enabled",
    "declined": "Could not focus address bar: 'activate' was declined by the application",
    None: "Could not focus address bar: 'activate' unavailable (available: ['activate'])",
}


class TestGotoReasons:
    @pytest.mark.parametrize(
        "failure_reason",
        ["not_enabled", "declined", None],
        ids=["not_enabled", "declined", "none"],
    )
    def test_failed_focus_names_the_cause_and_types_nothing(
        self, failure_reason: Optional[str]
    ) -> None:
        port = _ReasonPort(failure_reason, actions=("activate",))
        input_port = FakeInput()

        result = _goto_uc(port, input_port).execute(
            _SESSION_ID, "file:///tmp/page.html"
        )

        assert result.status == "error"
        assert result.reason == _GOTO_REASONS[failure_reason]
        assert port.calls == [("n_020", "activate")]
        assert input_port.calls == []


# ═══════════════════════════════════════════════════════════════════════
# menu: the error names the cause and the traversal stops
# ═══════════════════════════════════════════════════════════════════════


def _menu_node(node_id: str, role: str, name: str, *children: Node) -> Node:
    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=(0, 0, 80, 20),
        states=("enabled", "sensitive"),
        actions=("click",),
        children=tuple(children),
    )


def _menu_tree() -> AccessibilityTree:
    menu_bar = Node(
        id="m_bar",
        role="menu_bar",
        name="",
        bounds=(0, 0, 640, 20),
        states=("enabled",),
        actions=(),
        children=(
            _menu_node(
                "m_file",
                "menu",
                "File",
                _menu_node("m_save", "menu_item", "Save"),
                _menu_node(
                    "m_export",
                    "menu",
                    "Export",
                    _menu_node("m_pdf", "menu_item", "PDF"),
                ),
            ),
        ),
    )
    frame = Node(
        id="m_frame",
        role="frame",
        name="App",
        bounds=(0, 0, 640, 480),
        states=("enabled",),
        actions=(),
        children=(menu_bar,),
    )
    return AccessibilityTree(roots=(frame,))


def _menu_uc(port: Any) -> MenuUseCase:
    tree_store = FakeTreeStore()
    tree_store.save_tree(_SESSION_ID, _menu_tree(), None)
    return MenuUseCase(
        FakeAccessibilityTree(_menu_tree()),
        port,
        _repo(),
        tree_store,
        FakeClock(),
    )


def _menu_error(port: Any, menu_path: str) -> str:
    with pytest.raises(RuntimeError) as raised:
        _menu_uc(port).execute(_SESSION_ID, menu_path)
    return str(raised.value)


_EXPORT_PATH = "File.Export.PDF"


class TestMenuReasons:
    @pytest.mark.parametrize(
        "segment, node_id, activated",
        [
            ("File", "m_file", ["m_file"]),
            ("Export", "m_export", ["m_file", "m_export"]),
            ("PDF", "m_pdf", ["m_file", "m_export", "m_pdf"]),
        ],
        ids=["first", "middle", "last"],
    )
    def test_segment_that_is_not_enabled_stops_the_traversal(
        self, segment: str, node_id: str, activated: List[str]
    ) -> None:
        port = _ReasonPort("not_enabled", actions=("click",), refuse_node=node_id)

        message = _menu_error(port, _EXPORT_PATH)

        assert message == (
            f"Menu item '{segment}' in path 'File.Export.PDF' could not be "
            f"activated: node '{node_id}' is not enabled, so action 'click' was "
            "not sent (available actions: 'click')"
        )
        assert [node for node, _action in port.calls] == activated

    def test_declined_last_segment_names_the_decline(self) -> None:
        port = _ReasonPort("declined", actions=("click",), refuse_node="m_pdf")

        message = _menu_error(port, _EXPORT_PATH)

        assert message == (
            "Menu item 'PDF' in path 'File.Export.PDF' could not be activated: "
            "action 'click' on node 'm_pdf' was declined by the application "
            "(available actions: 'click')"
        )

    @pytest.mark.parametrize(
        "failure_reason", [None, "something_else"], ids=["none", "other"]
    )
    def test_failure_without_a_known_reason_keeps_its_message(
        self, failure_reason: Optional[str]
    ) -> None:
        port = _ReasonPort(failure_reason, actions=("click",), refuse_node="m_pdf")

        message = _menu_error(port, _EXPORT_PATH)

        assert message == (
            "Menu item 'PDF' in path 'File.Export.PDF' could not be activated: "
            "action 'click' on node 'm_pdf' did not succeed "
            "(available actions: 'click')"
        )


# ═══════════════════════════════════════════════════════════════════════
# CLI and MCP outcomes
# ═══════════════════════════════════════════════════════════════════════


def _invoke_cli(use_case_name: str, use_case: Any, argv: List[str]) -> Any:
    runner = make_cli_runner()
    with (
        patch(f"aiyes.cli.main.{use_case_name}", use_case),
        patch("aiyes.cli.main.resolve_session_id", return_value=_SESSION_ID),
        patch("aiyes.cli.main.operation_log_adapter", MagicMock()),
    ):
        return runner.invoke(cli, argv)


def _call_mcp(
    use_case_name: str, use_case: Any, tool: str, arguments: Dict[str, Any]
) -> Tuple[bool, str]:
    fields: Dict[str, Any] = {
        field.name: MagicMock() for field in dataclasses.fields(ServerDependencies)
    }
    clock = MagicMock()
    clock.now.return_value = 1000.0
    fields.update(
        {
            use_case_name: use_case,
            "resolve_session_id": MagicMock(return_value=_SESSION_ID),
            "clock": clock,
            "operation_log": MagicMock(),
        }
    )

    async def call() -> Any:
        server = create_mcp_server(ServerDependencies(**fields))
        return await server.call_tool(tool, dict(arguments, session_id=_SESSION_ID))

    result = asyncio.run(call())
    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
    return result.isError, text


def _assert_same_json_on_cli_and_mcp(
    cli_result: Any, mcp_result: Tuple[bool, str], expected: Dict[str, Any]
) -> None:
    is_error, text = mcp_result
    assert cli_result.exit_code == 0, cli_result.stderr
    assert json.loads(cli_result.stdout) == expected
    assert is_error is False, text
    assert text == cli_result.stdout.rstrip("\n")


_COMMAND_REASONS = ["not_enabled", "declined", None]
_COMMAND_IDS = ["not_enabled", "declined", "none"]


class TestCommandOutcomes:
    @pytest.mark.parametrize("failure_reason", _COMMAND_REASONS, ids=_COMMAND_IDS)
    def test_action_reports_status_error_with_the_reason(
        self, failure_reason: Optional[str]
    ) -> None:
        def use_case() -> ActionUseCase:
            return _action_uc(_ReasonPort(failure_reason))

        cli_result = _invoke_cli("action_uc", use_case(), ["action", "n_009", "click"])
        mcp_result = _call_mcp(
            "action_uc",
            use_case(),
            "action",
            {"node_id": "n_009", "action_name": "click"},
        )

        _assert_same_json_on_cli_and_mcp(
            cli_result,
            mcp_result,
            {
                "status": "error",
                "action": "click",
                "target": "n_009",
                "reason": _action_reason(failure_reason, "click", "n_009"),
                "available_actions": ["click", "focus"],
            },
        )

    @pytest.mark.parametrize(
        "failure_reason, action",
        [("not_enabled", "click"), ("declined", "click"), (None, "Press")],
        ids=_COMMAND_IDS,
    )
    def test_do_reports_status_error_with_the_reason(
        self, failure_reason: Optional[str], action: str
    ) -> None:
        def use_case() -> CompoundDoUseCase:
            return _do_uc(_ReasonPort(failure_reason))

        argv = ["do", "--role", "push_button", "--name", "Target", "--action", action]
        cli_result = _invoke_cli("compound_do_uc", use_case(), argv)
        mcp_result = _call_mcp(
            "compound_do_uc",
            use_case(),
            "do",
            {"role": "push_button", "name_pattern": "Target", "action_name": action},
        )

        is_error, text = mcp_result
        assert cli_result.exit_code == 0, cli_result.stderr
        assert is_error is False, text
        assert text == cli_result.stdout.rstrip("\n")
        parsed = json.loads(cli_result.stdout)
        assert sorted(parsed) == ["action", "found"]
        assert parsed["action"] == {
            "status": "error",
            "action": action,
            "target": "n_009",
            "reason": _action_reason(failure_reason, action, "n_009"),
        }

    @pytest.mark.parametrize("failure_reason", _COMMAND_REASONS, ids=_COMMAND_IDS)
    def test_goto_reports_status_error_with_the_reason(
        self, failure_reason: Optional[str]
    ) -> None:
        def use_case() -> GotoUseCase:
            return _goto_uc(_ReasonPort(failure_reason, actions=("activate",)))

        url = "file:///tmp/page.html"
        cli_result = _invoke_cli("goto_uc", use_case(), ["goto", url])
        mcp_result = _call_mcp("goto_uc", use_case(), "goto", {"url": url})

        _assert_same_json_on_cli_and_mcp(
            cli_result,
            mcp_result,
            {
                "status": "error",
                "session_id": _SESSION_ID,
                "action": "goto",
                "reason": _GOTO_REASONS[failure_reason],
            },
        )

    @pytest.mark.parametrize(
        "failure_reason, message",
        [
            (
                "not_enabled",
                "Menu item 'Save' in path 'File.Save' could not be activated: "
                "node 'm_save' is not enabled, so action 'click' was not sent "
                "(available actions: 'click')",
            ),
            (
                "declined",
                "Menu item 'Save' in path 'File.Save' could not be activated: "
                "action 'click' on node 'm_save' was declined by the application "
                "(available actions: 'click')",
            ),
        ],
        ids=["not_enabled", "declined"],
    )
    def test_menu_exits_1_with_the_reason(
        self, failure_reason: str, message: str
    ) -> None:
        def use_case() -> MenuUseCase:
            return _menu_uc(
                _ReasonPort(failure_reason, actions=("click",), refuse_node="m_save")
            )

        cli_result = _invoke_cli("menu_uc", use_case(), ["menu", "File.Save"])
        is_error, text = _call_mcp(
            "menu_uc", use_case(), "menu", {"menu_path": "File.Save"}
        )

        assert cli_result.exit_code == 1
        assert cli_result.stderr.strip() == f"Error: {message}"
        assert cli_result.stdout == ""
        assert is_error is True
        assert message in text


# action output for a requested action that is not available on a node that
# offers no actions (unchanged output).
_ACTION_NOT_AVAILABLE_OUTPUT = (
    '{\n  "status": "error",\n  "action": "focus",\n  "target": "n_009",\n'
    "  \"reason\": \"Action 'focus' not available on node 'n_009'\"\n}\n"
)


class TestActionOnANodeOfferingNoActions:
    @pytest.mark.parametrize("failure_reason", ["not_enabled", "declined"])
    def test_error_carries_an_empty_available_actions_list(
        self, failure_reason: str
    ) -> None:
        def use_case() -> ActionUseCase:
            return _action_uc(_ReasonPort(failure_reason, actions=()))

        cli_result = _invoke_cli("action_uc", use_case(), ["action", "n_009", "focus"])
        mcp_result = _call_mcp(
            "action_uc",
            use_case(),
            "action",
            {"node_id": "n_009", "action_name": "focus"},
        )

        _assert_same_json_on_cli_and_mcp(
            cli_result,
            mcp_result,
            {
                "status": "error",
                "action": "focus",
                "target": "n_009",
                "reason": _action_reason(failure_reason, "focus", "n_009"),
                "available_actions": [],
            },
        )

    def test_action_that_is_not_available_keeps_its_output(self) -> None:
        def use_case() -> ActionUseCase:
            return _action_uc(_ReasonPort(None, actions=()))

        cli_result = _invoke_cli("action_uc", use_case(), ["action", "n_009", "focus"])
        is_error, text = _call_mcp(
            "action_uc",
            use_case(),
            "action",
            {"node_id": "n_009", "action_name": "focus"},
        )

        assert cli_result.exit_code == 0, cli_result.stderr
        assert cli_result.stdout == _ACTION_NOT_AVAILABLE_OUTPUT
        assert is_error is False, text
        assert text == _ACTION_NOT_AVAILABLE_OUTPUT.rstrip("\n")


# ═══════════════════════════════════════════════════════════════════════
# Scenario action steps keep their outcome; action_ok catches the error
# ═══════════════════════════════════════════════════════════════════════


@dataclasses.dataclass
class _RecordingUseCase:
    result: Any
    calls: List[Dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.result


class TestScenarioActionStep:
    def test_step_records_the_reason_and_action_ok_fails(self) -> None:
        executor = ScenarioUseCaseExecutor(
            session_start=_RecordingUseCase(SimpleNamespace(session_id=_SESSION_ID)),
            inspect=_RecordingUseCase(SimpleNamespace(tree={"roots": []})),
            find=_RecordingUseCase([]),
            action=_action_uc(_ReasonPort("not_enabled")),
            type_text=_RecordingUseCase(SimpleNamespace(status="ok")),
            screenshot=_RecordingUseCase(SimpleNamespace(path="/tmp/s.png")),
            session_stop=_RecordingUseCase(SimpleNamespace(status="ok")),
        )
        executor.execute(
            ScenarioStep(
                id="start",
                kind="start_session",
                parameters={"command": "probe", "wait_seconds": 0.0},
            )
        )

        step = executor.execute(
            ScenarioStep(
                id="press",
                kind="action",
                parameters={"node_id": "n_009", "action": "click"},
            )
        )
        check = executor.execute(
            ScenarioStep(
                id="check",
                kind="assert",
                parameters={
                    "assertion": {
                        "id": "press-ok",
                        "kind": "action_ok",
                        "source": "press",
                    }
                },
            )
        )

        assert step.status == "passed"
        assert step.output["status"] == "error"
        assert step.output["reason"] == (
            "Action 'click' not sent: node 'n_009' is not enabled"
        )
        assert check.status == "failed"
        assert check.output["assertion"]["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════
# Documentation
# ═══════════════════════════════════════════════════════════════════════

_HEADING = re.compile(r"^#{2,4} ")


def _read(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _manual_section(command_heading_prefix: str) -> str:
    """Text of the manual section whose heading starts with the prefix."""
    lines = _read("docs/manual.md").splitlines()
    starts = [
        i for i, line in enumerate(lines) if line.startswith(command_heading_prefix)
    ]
    assert len(starts) == 1, f"expected one heading starting {command_heading_prefix!r}"
    body: List[str] = []
    for line in lines[starts[0] + 1 :]:
        if _HEADING.match(line):
            break
        body.append(line)
    return "\n".join(body)


_SECTIONS = {
    "action": "#### `aieyes action ",
    "menu": "#### `aieyes menu ",
    "do": "#### `aieyes do ",
}


class TestDocumentation:
    @pytest.mark.parametrize(
        "relative, phrase",
        [
            ("docs/manual.md", "for example because the item is disabled"),
            ("CHANGELOG.md", "for example a disabled item, is treated as activated"),
            ("src/aiyes/domain/use_cases/menu.py", "for example a disabled menu item"),
            (
                "src/aiyes/domain/use_cases/menu.py",
                "reports success once the action has been sent",
            ),
        ],
        ids=["manual", "changelog", "menu_class_doc", "menu_success_doc"],
    )
    def test_disabled_item_is_no_longer_described_as_activated(
        self, relative: str, phrase: str
    ) -> None:
        assert phrase not in _read(relative)

    def test_changelog_menu_entry_keeps_the_earlier_behaviour_sentence(self) -> None:
        lines = _read("CHANGELOG.md").splitlines()
        anchor = "**Visible changes for callers of `menu`**"
        starts = [i for i, line in enumerate(lines) if anchor in line]
        assert len(starts) == 1
        block: List[str] = []
        for line in lines[starts[0] + 1 :]:
            if line.startswith("- ") or line.startswith("#"):
                break
            block.append(line)

        assert 'Before, it reported `"status": "ok"`.' in "\n".join(block)

    @pytest.mark.parametrize(
        "section, phrase",
        [
            ("action", "not enabled"),
            ("action", "declined"),
            ("menu", "not enabled"),
            ("do", "not enabled"),
            ("do", "reason"),
            ("do", "not available"),
        ],
        ids=[
            "action_not_enabled",
            "action_declined",
            "menu_not_enabled",
            "do_not_enabled",
            "do_reason",
            "do_not_available",
        ],
    )
    def test_manual_documents_the_outcome(self, section: str, phrase: str) -> None:
        assert phrase in _manual_section(_SECTIONS[section])
