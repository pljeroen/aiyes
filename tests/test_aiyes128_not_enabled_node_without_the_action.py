"""Linux actions on a node that is not enabled are refused whatever the node offers.

Some toolkits list no actions for a widget that is not enabled (a disabled Qt
Widgets button lists none), and some list only other actions (a disabled GTK3
button lists ``click``; a disabled item of a closed Qt menu lists ``Press``).
On Linux, a request on a node that is not enabled is refused as not enabled
before the application is asked, whether the node offers the requested action,
offers only other actions or offers none. An enabled node that does not offer
the action is still reported as not available, and a state set that cannot be
read never produces a refusal.

These tests pin:

* the worker response, on the one-shot and on the persistent path, for every
  requested action on a node that is not enabled, and the responses that stay
  as they were;
* the single warning written when the state set cannot be read;
* the outputs of action, do, goto and menu on the CLI and over MCP, through the
  real action adapter;
* the scenario step and run outcome under each expect value, and the run
  message when aiyes could not confirm that the target was there;
* the documentation and source text that described the earlier order.

All accessibles, state sets and AT-SPI bindings are in-memory doubles, so no
display, accessibility bus or toolkit is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from aiyes.adapters import atspi_persistent_worker as persistent_worker
from aiyes.adapters import atspi_subprocess_worker as worker
from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

from tests.conftest import FakeInput
from tests.test_aiyes126_declined_actions import (
    _BUS,
    _DISABLED_STATES,
    _ENABLED_STATES,
    _RAISE,
    _SESSION_ID,
    _FakeAccessible,
    _FakeStateSet,
    _NoStateSetAccessible,
    _action_uc,
    _assert_same_json_on_cli_and_mcp,
    _call_mcp,
    _do_tree,
    _do_uc,
    _enabled_check_warnings,
    _failed,
    _fake_atspi_modules,
    _goto_uc,
    _invoke_cli,
    _menu_uc,
    _not_enabled,
    _run_persistent_worker,
    _run_worker,
    _without_null_reason,
)
from tests.test_aiyes127_action_step_outcome import (
    _ABSENT,
    _AF,
    _MESSAGE_VARIANT,
    _NODE,
    _STEP,
    _action_doc,
    _call_tool,
    _changelog_unreleased_fixed,
    _document,
    _linux_uc,
    _mcp_deps,
    _policy_section,
    _read,
    _real_runner,
    _record,
    _rig,
    _run_cli,
    _run_scenario,
    _with_expect,
    _write,
)

_REFUSED_FIX = '"expect": "refused"'
_ANY_FIX = '"expect": "any"'


def _not_enabled_reason(action: str, node_id: str) -> str:
    return f"Action '{action}' not sent: node '{node_id}' is not enabled"


def _not_available_reason(action: str, node_id: str) -> str:
    return f"Action '{action}' not available on node '{node_id}'"


def _disabled() -> _FakeStateSet:
    """State set of a widget that is not enabled: focusable, showing, visible."""
    return _FakeStateSet(*_DISABLED_STATES)


def _empty() -> _FakeStateSet:
    """Empty state set, as a disabled item of a closed Qt menu reports."""
    return _FakeStateSet()


# ═══════════════════════════════════════════════════════════════════════
# Doubles
# ═══════════════════════════════════════════════════════════════════════


class _ActionListingFails(_FakeAccessible):
    """Accessible whose action listing raises."""

    def get_n_actions(self) -> int:
        raise RuntimeError("action listing failed")


class _InProcessPersistentWorker:
    """Persistent-worker connection answered by the worker's own dispatcher.

    ``nodes`` maps a node id to the accessible the worker finds for it; a node
    id that is missing from it is not found. Every request is recorded as
    ``(node_id, action_name)``.
    """

    def __init__(self, nodes: Dict[str, _FakeAccessible]) -> None:
        self._nodes = dict(nodes)
        self.calls: List[Tuple[str, str]] = []

    def is_alive(self) -> bool:
        return True

    def send(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((kwargs["node_id"], kwargs["action_name"]))
        request = {
            "node_id": kwargs["node_id"],
            "action_name": kwargs["action_name"],
            "value": kwargs.get("value"),
            "registry": kwargs.get("registry"),
        }
        with (
            patch.dict(sys.modules, _fake_atspi_modules()),
            patch.object(worker, "_setup_env"),
            patch.object(worker, "_search_tree", side_effect=self._search),
        ):
            return persistent_worker._dispatch(op, request, ":99", _BUS)

    def _search(
        self, desktop: Any, node_id: str, path: Any, registry: Any
    ) -> Optional[_FakeAccessible]:
        return self._nodes.get(node_id)


def _adapter(
    nodes: Dict[str, _FakeAccessible],
) -> Tuple[AtSpi2ActionAdapter, _InProcessPersistentWorker]:
    """The real Linux action adapter over an in-process persistent worker."""
    connection = _InProcessPersistentWorker(nodes)
    adapter = AtSpi2ActionAdapter()
    adapter.set_worker(connection)
    return adapter, connection


# ═══════════════════════════════════════════════════════════════════════
# Worker: a node that is not enabled is refused whatever it offers
# ═══════════════════════════════════════════════════════════════════════

_NOT_ENABLED_STATE_SETS: Dict[str, Callable[[], _FakeStateSet]] = {
    "disabled": _disabled,
    "empty_state_set": _empty,
}

# What the node lists; None stands for a list holding only the requested name.
_LISTINGS: Dict[str, Optional[Tuple[str, ...]]] = {
    "lists_none": (),
    "lists_press": ("Press",),
    "lists_press_and_setfocus": ("Press", "SetFocus"),
    "lists_the_requested_action": None,
}

_REQUESTED_NAMES = [
    "click",
    "Press",
    "activate",
    "ShowMenu",
    "toggle",
    "SetFocus",
    "bogus",
]


def _listing(kind: str, requested: str) -> Tuple[str, ...]:
    listed = _LISTINGS[kind]
    return (requested,) if listed is None else listed


class TestWorkerRefusesANodeThatIsNotEnabled:
    """Neither ENABLED nor SENSITIVE: refused before the application is asked."""

    @pytest.mark.parametrize("requested", _REQUESTED_NAMES)
    @pytest.mark.parametrize("listing", sorted(_LISTINGS))
    @pytest.mark.parametrize("states", sorted(_NOT_ENABLED_STATE_SETS))
    def test_node_that_is_not_enabled_is_refused_whatever_it_offers(
        self, states: str, listing: str, requested: str
    ) -> None:
        actions = _listing(listing, requested)
        node = _FakeAccessible(
            actions=actions, states=_NOT_ENABLED_STATE_SETS[states]()
        )

        response = _run_worker(node, requested)

        assert response == _not_enabled(list(actions))
        assert node.act_calls == []

    @pytest.mark.parametrize(
        "actions", [(), ("click",)], ids=["lists_none", "lists_click"]
    )
    def test_set_text_without_a_value_on_a_node_that_is_not_enabled_is_refused(
        self, actions: Tuple[str, ...]
    ) -> None:
        node = _FakeAccessible(
            actions=actions,
            states=_disabled(),
            editable=True,
            editable_result=True,
            fallback_result=True,
        )

        response = _run_worker(node, "set_text", None)

        assert response == _not_enabled(list(actions))
        assert node.act_calls == []

    def test_node_whose_action_listing_fails_is_refused_with_no_actions(
        self,
    ) -> None:
        node = _ActionListingFails(actions=["click"], states=_disabled())

        response = _run_worker(node, "click")

        assert response == _not_enabled([])
        assert node.act_calls == []


_PARITY_CASES: Dict[
    str, Tuple[Callable[[], _FakeAccessible], str, Optional[str], Dict[str, Any]]
] = {
    "lists_none": (
        lambda: _FakeAccessible(actions=(), states=_disabled()),
        "click",
        None,
        _not_enabled([]),
    ),
    "lists_another_action": (
        lambda: _FakeAccessible(actions=("Press",), states=_disabled()),
        "click",
        None,
        _not_enabled(["Press"]),
    ),
    "empty_state_set_lists_none": (
        lambda: _FakeAccessible(actions=(), states=_empty()),
        "Press",
        None,
        _not_enabled([]),
    ),
    "empty_state_set_lists_another_action": (
        lambda: _FakeAccessible(actions=("click",), states=_empty()),
        "Press",
        None,
        _not_enabled(["click"]),
    ),
    "set_text_without_a_value": (
        lambda: _FakeAccessible(actions=("click",), states=_disabled()),
        "set_text",
        None,
        _not_enabled(["click"]),
    ),
    "action_listing_fails": (
        lambda: _ActionListingFails(actions=("click",), states=_disabled()),
        "click",
        None,
        _not_enabled([]),
    ),
}


class TestPersistentWorkerRefusesTheSameWay:
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
# Worker: an action listing that fails after some names were read
# ═══════════════════════════════════════════════════════════════════════


class _ActionListingFailsAfterAName(_FakeAccessible):
    """Accessible that counts one action more than it can name.

    ``get_n_actions`` reports the listed actions plus one, and
    ``get_action_name`` raises for that last index, so the listing fails after
    the earlier names were read.
    """

    def get_n_actions(self) -> int:
        return len(self._actions) + 1

    def get_action_name(self, index: int) -> str:
        if index >= len(self._actions):
            raise RuntimeError("action name unavailable")
        return self._actions[index]


def _enabled() -> _FakeStateSet:
    """State set of an enabled widget."""
    return _FakeStateSet(*_ENABLED_STATES)


_WORKER_ROUTES: Dict[str, Callable[..., Dict[str, Any]]] = {
    "one_shot": _run_worker,
    "persistent": _run_persistent_worker,
}

# Requests on a node that is not enabled: (state set, action, value).
_PARTIAL_LISTING_REFUSALS: Dict[
    str, Tuple[Callable[[], _FakeStateSet], str, Optional[str]]
] = {
    "empty_state_set_asked_for_click": (_empty, "click", None),
    "disabled_asked_for_a_name_read_before_the_failure": (_disabled, "Press", None),
    "disabled_set_text_without_a_value": (_disabled, "set_text", None),
}

# Responses that keep the names read before the failure, or do not use them:
# (state set, action, value, response, act calls).
_PARTIAL_LISTING_UNCHANGED: Dict[
    str,
    Tuple[
        Callable[[], _FakeStateSet],
        str,
        Optional[str],
        Dict[str, Any],
        List[Tuple[Any, ...]],
    ],
] = {
    "enabled_action_not_offered": (_enabled, "click", None, _failed(["Press"]), []),
    "enabled_action_offered": (
        _enabled,
        "Press",
        None,
        {
            "success": True,
            "available_actions": ["Press"],
            "node_value": None,
            "node_states": None,
        },
        [("do_action", "Press")],
    ),
    "focus_not_enabled": (_disabled, "focus", None, _not_enabled(["Press"]), []),
    "focus_enabled": (
        _enabled,
        "focus",
        None,
        {
            "success": True,
            "available_actions": ["Press"],
            "node_value": None,
            "node_states": [str(state) for state in _enabled().get_states()],
        },
        [("grab_focus",)],
    ),
    "set_text_not_enabled": (
        _disabled,
        "set_text",
        "hi",
        _not_enabled(["set_text"]),
        [],
    ),
    "set_text_enabled": (
        _enabled,
        "set_text",
        "hi",
        {
            "success": True,
            "available_actions": ["set_text"],
            "node_value": "typed text",
            "node_states": None,
        },
        [("set_text_contents", "hi")],
    ),
}


class TestWorkerActionListingFailsAfterAName:
    """A refused node reports no actions when its listing could not be read."""

    @pytest.mark.parametrize("request_kind", sorted(_PARTIAL_LISTING_REFUSALS))
    @pytest.mark.parametrize("route", sorted(_WORKER_ROUTES))
    def test_node_that_is_not_enabled_is_refused_with_no_actions(
        self, route: str, request_kind: str
    ) -> None:
        states, action_name, value = _PARTIAL_LISTING_REFUSALS[request_kind]
        node = _ActionListingFailsAfterAName(actions=("Press",), states=states())

        response = _WORKER_ROUTES[route](node, action_name, value)

        assert response == _not_enabled([])
        assert node.act_calls == []

    @pytest.mark.parametrize("case", sorted(_PARTIAL_LISTING_UNCHANGED))
    def test_enabled_focus_and_set_text_responses_are_unchanged(
        self, case: str
    ) -> None:
        states, action_name, value, expected, act_calls = _PARTIAL_LISTING_UNCHANGED[
            case
        ]
        node = _ActionListingFailsAfterAName(actions=("Press",), states=states())

        response = _run_worker(node, action_name, value)

        assert _without_null_reason(response) == expected
        assert node.act_calls == act_calls


# ═══════════════════════════════════════════════════════════════════════
# Worker: every other response is unchanged
# ═══════════════════════════════════════════════════════════════════════

_UNCHANGED_RESPONSES: Dict[
    str,
    Tuple[Callable[[], Optional[_FakeAccessible]], str, Optional[str], Dict[str, Any]],
] = {
    "node_not_found": (
        lambda: None,
        "click",
        None,
        {"success": False, "available_actions": []},
    ),
    "enabled_lists_none": (
        lambda: _FakeAccessible(actions=()),
        "click",
        None,
        _failed([]),
    ),
    "enabled_lists_press_and_setfocus": (
        lambda: _FakeAccessible(actions=("Press", "SetFocus")),
        "click",
        None,
        _failed(["Press", "SetFocus"]),
    ),
    "enabled_lists_click_asked_for_press": (
        lambda: _FakeAccessible(actions=("click",)),
        "Press",
        None,
        _failed(["click"]),
    ),
    "enabled_set_text_without_a_value": (
        lambda: _FakeAccessible(actions=("click",)),
        "set_text",
        None,
        _failed(["click"]),
    ),
    "sensitive_only_lists_none": (
        lambda: _FakeAccessible(
            actions=(), states=_FakeStateSet("SENSITIVE", "SHOWING", "VISIBLE")
        ),
        "click",
        None,
        _failed([]),
    ),
    "enabled_only_lists_none": (
        lambda: _FakeAccessible(
            actions=(), states=_FakeStateSet("ENABLED", "SHOWING", "VISIBLE")
        ),
        "click",
        None,
        _failed([]),
    ),
}


class TestWorkerResponsesThatStayAsTheyWere:
    @pytest.mark.parametrize("case", sorted(_UNCHANGED_RESPONSES))
    def test_response_is_unchanged(self, case: str) -> None:
        make_node, action_name, value, expected = _UNCHANGED_RESPONSES[case]
        node = make_node()

        response = _run_worker(node, action_name, value)

        assert _without_null_reason(response) == expected
        if node is not None:
            assert node.act_calls == []


_UNREADABLE_STATE_SETS: Dict[str, Callable[[Tuple[str, ...]], _FakeAccessible]] = {
    "state_set_raises": lambda actions: _FakeAccessible(actions=actions, states=_RAISE),
    "state_set_missing": lambda actions: _NoStateSetAccessible(actions=actions),
    "contains_raises": lambda actions: _FakeAccessible(
        actions=actions,
        states=_FakeStateSet(*_ENABLED_STATES, contains_raises=True),
    ),
}


class TestWorkerUnreadableStateSetForAnActionNotOffered:
    """An inconclusive check is never a refusal and warns once per request."""

    @pytest.mark.parametrize(
        "actions", [(), ("Press",)], ids=["lists_none", "lists_press"]
    )
    @pytest.mark.parametrize("mode", sorted(_UNREADABLE_STATE_SETS))
    def test_unreadable_state_set_keeps_the_response_and_warns_once(
        self,
        mode: str,
        actions: Tuple[str, ...],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        node = _UNREADABLE_STATE_SETS[mode](actions)

        response = _run_worker(node, "click")

        assert _without_null_reason(response) == _failed(list(actions))
        assert node.act_calls == []
        assert _enabled_check_warnings(capsys.readouterr().err) == 1


# ═══════════════════════════════════════════════════════════════════════
# action, do, goto and menu through the real action adapter
# ═══════════════════════════════════════════════════════════════════════

_ACTION_ARGV = ["action", "n_009", "click"]
_ACTION_MCP = {"node_id": "n_009", "action_name": "click"}
_DO_ARGV = ["do", "--role", "push_button", "--name", "Target", "--action", "click"]
_DO_MCP = {"role": "push_button", "name_pattern": "Target", "action_name": "click"}
_MENU_PATH = "File.Export.PDF"
_URL = "file:///tmp/page.html"

_TARGET_LISTINGS = [(), ("Press",)]
_TARGET_LISTING_IDS = ["lists_none", "lists_press"]


def _action_use_case(node: _FakeAccessible) -> Any:
    adapter, _connection = _adapter({"n_009": node})
    return _action_uc(adapter)


def _do_use_case(node: _FakeAccessible) -> Any:
    adapter, _connection = _adapter({"n_009": node})
    return _do_uc(adapter)


def _menu_use_case(
    last_item: _FakeAccessible,
) -> Tuple[Any, _InProcessPersistentWorker, Dict[str, _FakeAccessible]]:
    nodes = {
        "m_file": _FakeAccessible(actions=("click",)),
        "m_export": _FakeAccessible(actions=("click",)),
        "m_pdf": last_item,
    }
    adapter, connection = _adapter(nodes)
    return _menu_uc(adapter), connection, nodes


def _menu_error(use_case: Any) -> str:
    with pytest.raises(RuntimeError) as raised:
        use_case.execute(_SESSION_ID, _MENU_PATH)
    return str(raised.value)


def _assert_menu_command_fails_with(
    make_use_case: Callable[[], Any], message: str
) -> None:
    cli_result = _invoke_cli("menu_uc", make_use_case(), ["menu", _MENU_PATH])
    is_error, text = _call_mcp(
        "menu_uc", make_use_case(), "menu", {"menu_path": _MENU_PATH}
    )

    assert cli_result.exit_code == 1
    assert cli_result.stderr.strip() == f"Error: {message}"
    assert cli_result.stdout == ""
    assert is_error is True
    assert message in text


class TestCommandsReportTheNodeIsNotEnabled:
    @pytest.mark.parametrize("actions", _TARGET_LISTINGS, ids=_TARGET_LISTING_IDS)
    def test_action_reports_not_enabled_with_the_node_actions(
        self, actions: Tuple[str, ...]
    ) -> None:
        def use_case() -> Any:
            return _action_use_case(
                _FakeAccessible(actions=actions, states=_disabled())
            )

        cli_result = _invoke_cli("action_uc", use_case(), _ACTION_ARGV)
        mcp_result = _call_mcp("action_uc", use_case(), "action", _ACTION_MCP)

        _assert_same_json_on_cli_and_mcp(
            cli_result,
            mcp_result,
            {
                "status": "error",
                "action": "click",
                "target": "n_009",
                "reason": _not_enabled_reason("click", "n_009"),
                "available_actions": list(actions),
            },
        )

    @pytest.mark.parametrize("actions", _TARGET_LISTINGS, ids=_TARGET_LISTING_IDS)
    def test_do_reports_not_enabled(self, actions: Tuple[str, ...]) -> None:
        def use_case() -> Any:
            return _do_use_case(_FakeAccessible(actions=actions, states=_disabled()))

        cli_result = _invoke_cli("compound_do_uc", use_case(), _DO_ARGV)
        is_error, text = _call_mcp("compound_do_uc", use_case(), "do", _DO_MCP)

        assert cli_result.exit_code == 0, cli_result.stderr
        assert is_error is False, text
        assert text == cli_result.stdout.rstrip("\n")
        assert json.loads(cli_result.stdout)["action"] == {
            "status": "error",
            "action": "click",
            "target": "n_009",
            "reason": _not_enabled_reason("click", "n_009"),
        }

    def test_goto_reports_not_enabled_and_types_nothing(self) -> None:
        address_bar = _FakeAccessible(actions=(), states=_disabled())
        adapter, connection = _adapter({"n_020": address_bar})
        input_port = FakeInput()

        result = _goto_uc(adapter, input_port).execute(_SESSION_ID, _URL)

        assert result.status == "error"
        assert result.reason == (
            "Could not focus address bar: node 'n_020' is not enabled"
        )
        assert connection.calls == [("n_020", "activate")]
        assert address_bar.act_calls == []
        assert input_port.calls == []

    @pytest.mark.parametrize(
        "actions, available",
        [((), "none were reported"), (("SetFocus",), "'SetFocus'")],
        ids=["lists_none", "lists_setfocus"],
    )
    def test_menu_stops_at_an_item_that_is_not_enabled(
        self, actions: Tuple[str, ...], available: str
    ) -> None:
        message = (
            "Menu item 'PDF' in path 'File.Export.PDF' could not be activated: "
            "node 'm_pdf' is not enabled, so action 'click' was not sent "
            f"(available actions: {available})"
        )
        use_case, connection, nodes = _menu_use_case(
            _FakeAccessible(actions=actions, states=_disabled())
        )

        assert _menu_error(use_case) == message
        assert connection.calls == [
            ("m_file", "click"),
            ("m_export", "click"),
            ("m_pdf", "click"),
        ]
        assert nodes["m_file"].act_calls == [("do_action", "click")]
        assert nodes["m_export"].act_calls == [("do_action", "click")]
        assert nodes["m_pdf"].act_calls == []
        _assert_menu_command_fails_with(
            lambda: _menu_use_case(
                _FakeAccessible(actions=actions, states=_disabled())
            )[0],
            message,
        )


class TestCommandsOnAnEnabledNodeWithoutTheAction:
    """An enabled node that offers no actions keeps every earlier output."""

    def test_action_output_is_unchanged(self) -> None:
        def use_case() -> Any:
            return _action_use_case(_FakeAccessible(actions=()))

        cli_result = _invoke_cli("action_uc", use_case(), _ACTION_ARGV)
        mcp_result = _call_mcp("action_uc", use_case(), "action", _ACTION_MCP)

        _assert_same_json_on_cli_and_mcp(
            cli_result,
            mcp_result,
            {
                "status": "error",
                "action": "click",
                "target": "n_009",
                "reason": _not_available_reason("click", "n_009"),
            },
        )

    def test_do_output_is_unchanged(self) -> None:
        def use_case() -> Any:
            return _do_use_case(_FakeAccessible(actions=()))

        cli_result = _invoke_cli("compound_do_uc", use_case(), _DO_ARGV)
        is_error, text = _call_mcp("compound_do_uc", use_case(), "do", _DO_MCP)

        assert cli_result.exit_code == 0, cli_result.stderr
        assert is_error is False, text
        assert text == cli_result.stdout.rstrip("\n")
        assert json.loads(cli_result.stdout)["action"] == {
            "status": "error",
            "action": "click",
            "target": "n_009",
            "reason": _not_available_reason("click", "n_009"),
        }

    def test_goto_output_is_unchanged(self) -> None:
        adapter, connection = _adapter({"n_020": _FakeAccessible(actions=())})
        input_port = FakeInput()

        result = _goto_uc(adapter, input_port).execute(_SESSION_ID, _URL)

        assert result.status == "error"
        assert result.reason == (
            "Could not focus address bar: 'activate' unavailable (available: [])"
        )
        assert input_port.calls == []

    def test_menu_error_is_unchanged(self) -> None:
        message = (
            "Menu item 'PDF' in path 'File.Export.PDF' could not be activated: "
            "action 'click' on node 'm_pdf' did not succeed "
            "(available actions: none were reported)"
        )
        use_case, connection, _nodes = _menu_use_case(_FakeAccessible(actions=()))

        assert _menu_error(use_case) == message
        _assert_menu_command_fails_with(
            lambda: _menu_use_case(_FakeAccessible(actions=()))[0], message
        )


# ═══════════════════════════════════════════════════════════════════════
# Scenario action steps through the real action adapter
# ═══════════════════════════════════════════════════════════════════════


def _scenario_rig(nodes: Dict[str, _FakeAccessible]) -> Any:
    adapter, _connection = _adapter(nodes)
    return _rig(_linux_uc(adapter, stored_tree=_do_tree()))


def _action_output(actions: Tuple[str, ...], reason: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "action": "click",
        "target": _NODE,
        "reason": reason,
        "available_actions": list(actions),
    }


class TestScenarioStepOnANodeThatIsNotEnabled:
    @pytest.mark.parametrize("actions", _TARGET_LISTINGS, ids=_TARGET_LISTING_IDS)
    def test_step_without_expect_fails_with_the_refusal_message(
        self, actions: Tuple[str, ...]
    ) -> None:
        rig = _scenario_rig(
            {_NODE: _FakeAccessible(actions=actions, states=_disabled())}
        )
        reason = _not_enabled_reason("click", _NODE)

        run = _run_scenario(rig, [_action_doc()])

        step = _record(run, _STEP)
        assert (step.status, step.error) == ("failed", _AF)
        assert step.output == {**_action_output(actions, reason), "failure_code": _AF}
        assert (run.status, run.failure_code) == ("failed", _AF)
        assert len(run.next_actions) == 1
        message = run.next_actions[0].message
        for text in (_STEP, reason, _REFUSED_FIX, _ANY_FIX):
            assert text in message

    @pytest.mark.parametrize("expect", ["refused", "any"])
    @pytest.mark.parametrize("actions", _TARGET_LISTINGS, ids=_TARGET_LISTING_IDS)
    def test_refused_and_best_effort_steps_pass(
        self, actions: Tuple[str, ...], expect: str
    ) -> None:
        rig = _scenario_rig(
            {_NODE: _FakeAccessible(actions=actions, states=_disabled())}
        )
        reason = _not_enabled_reason("click", _NODE)

        run = _run_scenario(rig, [_action_doc(expect)])

        step = _record(run, _STEP)
        assert (step.status, step.error) == ("passed", "")
        assert step.output == {**_action_output(actions, reason), "expect": expect}
        assert (run.status, run.failure_code, run.next_actions) == ("passed", "", ())


_SCENARIO_CONTROLS: Dict[str, Callable[[], Dict[str, _FakeAccessible]]] = {
    "node_not_found": lambda: {},
    "enabled_lists_none": lambda: {_NODE: _FakeAccessible(actions=())},
    "unreadable_state_set_lists_none": lambda: {
        _NODE: _FakeAccessible(actions=(), states=_RAISE)
    },
}

# Step and run outcome by expect value for the controls: (status, code).
_CONTROL_OUTCOMES = {
    _ABSENT: ("failed", _AF),
    "refused": ("failed", _AF),
    "any": ("passed", ""),
}


class TestScenarioStepControls:
    """A missing target, an enabled one without actions, an unreadable state."""

    @pytest.mark.parametrize("expect", [_ABSENT, "refused", "any"])
    @pytest.mark.parametrize("control", sorted(_SCENARIO_CONTROLS))
    def test_outcome_is_unchanged(self, control: str, expect: Any) -> None:
        rig = _scenario_rig(_SCENARIO_CONTROLS[control]())
        status, code = _CONTROL_OUTCOMES[expect]

        run = _run_scenario(rig, [_action_doc(expect)])

        step = _record(run, _STEP)
        assert (step.status, step.error) == (status, code)
        assert (run.status, run.failure_code) == (status, code)


class TestRefusedCheckOnTheCliAndOverMcp:
    def test_scenario_run_passes_on_the_cli_and_over_mcp(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _document([_action_doc("refused")]))

        def runner() -> Any:
            node = _FakeAccessible(actions=(), states=_disabled())
            return _real_runner(_scenario_rig({_NODE: node}))

        cli_result = _run_cli(
            ["scenario", "run", "--real", str(path)], scenario_real_run_uc=runner()
        )
        is_error, text = _call_tool(
            _mcp_deps(scenario_real_run_uc=runner()),
            "scenario_run",
            {"scenario_path": str(path), "real_execution": True},
        )

        assert cli_result.exit_code == 0, cli_result.stdout
        assert json.loads(cli_result.stdout)["status"] == "passed"
        assert is_error is False
        assert json.loads(text)["status"] == "passed"


# ═══════════════════════════════════════════════════════════════════════
# The run message for a target aiyes could not confirm
# ═══════════════════════════════════════════════════════════════════════

_TARGET_NOT_CONFIRMED = "could not confirm the target was there"
_NOT_OBSERVABLE = "cannot be observed"
_WAIT_FIX = "wait for it"
_FOCUS_FIX = '"action": "focus"'
_REQUIRED_FIND = "required find"
_SOME_TOOLKITS = "some toolkits"
_ENABLED_ADVICE = "that it is enabled"


def _run_message(variant_name: str, expect: Any) -> str:
    variant = _MESSAGE_VARIANT[variant_name]
    rig = _rig(variant.build())

    run = _run_scenario(
        rig, [_with_expect({"id": _STEP, "kind": "action", **variant.params}, expect)]
    )

    assert run.status == "failed"
    assert len(run.next_actions) == 1
    return run.next_actions[0].message


class TestRunMessageForATargetThatCouldNotBeConfirmed:
    @pytest.mark.parametrize(
        "variant_name, expect, must_contain, must_not_contain",
        [
            (
                "not_available_without_actions",
                _ABSENT,
                [
                    _STEP,
                    _not_available_reason("click", _NODE),
                    _TARGET_NOT_CONFIRMED,
                    _WAIT_FIX,
                    _ANY_FIX,
                ],
                [_SOME_TOOLKITS, _ENABLED_ADVICE, _REFUSED_FIX, _REQUIRED_FIND],
            ),
            (
                "not_available_without_actions",
                "refused",
                [
                    _STEP,
                    _not_available_reason("click", _NODE),
                    _TARGET_NOT_CONFIRMED,
                    _NOT_OBSERVABLE,
                    _WAIT_FIX,
                    _ANY_FIX,
                ],
                [_FOCUS_FIX, _REQUIRED_FIND, _SOME_TOOLKITS],
            ),
            (
                "focus_not_available_without_actions",
                "refused",
                [_STEP, _not_available_reason("focus", _NODE)],
                [_FOCUS_FIX],
            ),
        ],
        ids=[
            "without_expect",
            "refusal_expected",
            "refusal_expected_with_focus",
        ],
    )
    def test_message_gives_no_advice_about_a_target_that_is_not_enabled(
        self,
        variant_name: str,
        expect: Any,
        must_contain: List[str],
        must_not_contain: List[str],
    ) -> None:
        message = _run_message(variant_name, expect)

        for text in must_contain:
            assert text in message
        for text in must_not_contain:
            assert text not in message
        assert "\n" not in message


# Messages for every other case, as they read before this change.
_UNCHANGED_MESSAGES = {
    "refusal_confirmed": (
        "not_enabled_with_actions",
        _ABSENT,
        "Action step 'press-save' failed: Action 'click' not sent: node 'n_009' is "
        "not enabled. Check the application state at this point; if the refusal is "
        'intended, set "expect": "refused" (negative check) or "expect": "any" '
        "(best effort) on that step.",
    ),
    "result_without_status": (
        "result_without_status",
        _ABSENT,
        "Action step 'press-save' failed: aiyes could not confirm that the action "
        "happened (the action result had no status).",
    ),
    "result_with_unknown_status": (
        "result_with_unknown_status",
        _ABSENT,
        "Action step 'press-save' failed: aiyes could not confirm that the action "
        "happened (the action result had status 'pending').",
    ),
    "action_went_through": (
        "reported_ok",
        "refused",
        "Action step 'press-save' failed: the action went through although the step "
        'expected a refusal ("expect": "refused"); check that the application is in '
        "the state the check expects at this point.",
    ),
    "result_with_unknown_status_and_reason": (
        "result_with_unknown_status_and_reason",
        _ABSENT,
        "Action step 'press-save' failed: aiyes could not confirm that the action "
        "happened (the action result had status 'pending'). Reason given: backend "
        "diagnostic.",
    ),
    "refusal_expected_but_result_with_unknown_status_and_reason": (
        "result_with_unknown_status_and_reason",
        "refused",
        "Action step 'press-save' failed: aiyes could not confirm that the action "
        "happened (the action result had status 'pending'). Reason given: backend "
        "diagnostic.",
    ),
    "best_effort_but_result_with_unknown_status_and_reason": (
        "result_with_unknown_status_and_reason",
        "any",
        "Action step 'press-save' failed: aiyes could not confirm that the action "
        "happened (the action result had status 'pending'). Reason given: backend "
        "diagnostic.",
    ),
    "action_not_offered": (
        "not_offered_by_a_node_with_press_and_setfocus",
        _ABSENT,
        "Action step 'press-save' failed: Action 'click' not available on node "
        "'n_009'. 'click' is not offered by the target: it offers Press, SetFocus: "
        "name one of those.",
    ),
    "refusal_expected_but_action_not_offered": (
        "not_offered_by_a_node_with_press_and_setfocus",
        "refused",
        "Action step 'press-save' failed: Action 'click' not available on node "
        "'n_009'. 'click' is not offered by the target: it offers Press, SetFocus: "
        "name one of those. The application was not asked, so this is not a "
        "refusal. A refusal counts only when the node is not enabled or the "
        "application declines the action.",
    ),
    "offered_action_failed_without_cause": (
        "offered_action_without_cause",
        _ABSENT,
        "Action step 'press-save' failed: Action 'click' not available on node "
        "'n_009'. The target offers 'click', but the request did not succeed and no "
        "refusal cause was reported: check the application state at this point.",
    ),
}


class TestOtherRunMessagesAreUnchanged:
    @pytest.mark.parametrize("case", sorted(_UNCHANGED_MESSAGES))
    def test_message_is_unchanged(self, case: str) -> None:
        variant_name, expect, expected = _UNCHANGED_MESSAGES[case]

        assert _run_message(variant_name, expect) == expected


# ═══════════════════════════════════════════════════════════════════════
# Documentation and source text
# ═══════════════════════════════════════════════════════════════════════


def _normalised(text: str) -> str:
    """``text`` with every run of whitespace replaced by one space."""
    return " ".join(text.split())


_WORKER_SOURCE = "src/aiyes/adapters/atspi_subprocess_worker.py"

_STALE_POLICY_TEXT = [
    "On a toolkit that hides those actions, use `focus`",
    "on a toolkit that keeps listing the actions of a disabled widget",
    "list no actions for a widget that is not enabled), a refusal of that action "
    "cannot be observed on it",
]

_STALE_CHANGELOG_SENTENCE = (
    "On a target that offers no actions (Qt, for example, lists none for a widget "
    "that is not enabled) a refusal of such an action cannot be observed; on "
    'Linux, `"action": "focus"` with `"expect": "refused"` passes on a node that '
    "is not enabled even then."
)

# Top-level entries under "### Fixed" in the unreleased part of the changelog.
_UNRELEASED_FIXED_ENTRIES = 12


class TestDocumentationOfTheOrder:
    def test_worker_docstring_no_longer_says_the_offered_actions_come_first(
        self,
    ) -> None:
        source = _normalised(_read(_WORKER_SOURCE))

        assert "For a listed action the offered actions are checked first" not in source

    def test_manual_no_longer_says_an_action_not_offered_is_reported_as_before(
        self,
    ) -> None:
        manual = _normalised(_read("docs/manual.md"))

        assert (
            "An action the node does not offer is reported as not available, "
            "as before." not in manual
        )

    @pytest.mark.parametrize(
        "stale",
        _STALE_POLICY_TEXT,
        ids=[
            "focus_workaround",
            "toolkit_that_keeps_listing",
            "refusal_not_observable",
        ],
    )
    def test_scenario_guide_drops_the_advice_for_disabled_widgets(
        self, stale: str
    ) -> None:
        section = _normalised(_policy_section(_read("docs/release-scenarios.md")))

        assert stale not in section

    def test_changelog_drops_the_sentence_about_targets_without_actions(
        self,
    ) -> None:
        changelog = _normalised(_read("CHANGELOG.md"))

        assert _STALE_CHANGELOG_SENTENCE not in changelog

    def test_changelog_is_corrected_in_place_without_a_new_entry(self) -> None:
        entries = [
            line for line in _changelog_unreleased_fixed() if line.startswith("- **")
        ]

        assert len(entries) == _UNRELEASED_FIXED_ENTRIES
