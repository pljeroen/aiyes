"""goto chooses the browser's address bar and never a control inside the web page.

Firefox 159 exposes its address bar as a ``combo box`` inside the
``Navigation`` tool bar, outside every document. The loaded page can hold form
fields whose names contain "address" as well. These tests drive
``GotoUseCase`` with ports that record every request in order, and check which
node receives ``activate`` and whether anything is typed:

* the address bar is an ``entry``, or a ``combo box`` inside a tool bar, whose
  accessible name contains "address"; raw and canonical role spellings are
  equivalent; the first such node in tree order is used;
* nothing below a document node (the page, a frame inside it, another tab) is
  ever used, whatever its role, name, actions or place in the tree;
* "activate" is sent at most once, only to the address bar, and the URL is
  typed only after it succeeded; the reasons for a failed focus are kept;
* when no address bar is found, the reason says what was searched, on the use
  case, the CLI command and the MCP tool;
* the goto description is unchanged, and the module docstring, the README goto
  paragraph and the changelog describe the behaviour.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest.mock import MagicMock, patch

import pytest

from aiyes.cli.main import cli
from aiyes.domain.tree import AccessibilityTree, raw_tree_to_domain
from aiyes.domain.types import ACTION_DECLINED, ACTION_NOT_ENABLED, ActionPortResult
from aiyes.domain.use_cases.goto import GotoResult, GotoUseCase

from tests.conftest import FakeSessionRepository, make_cli_runner
from tests.test_aiyes126_declined_actions import (
    _SESSION_ID,
    _assert_same_json_on_cli_and_mcp,
    _call_mcp,
    _invoke_cli,
    _linux_session,
)
from tests.test_aiyes127_action_step_outcome import _changelog_unreleased_fixed


_URL = "file:///tmp/target.html"

_NOT_FOUND = (
    "Address bar not found (role=entry, or combo box in a tool bar; "
    "name~address; outside page content)"
)
_OLD_NOT_FOUND = "Address bar not found (role=entry, name~address)"


# ═══════════════════════════════════════════════════════════════════════
# Trees
# ═══════════════════════════════════════════════════════════════════════

# State sets as the Linux tree reports them (numeric AT-SPI state ids).
_BAR_STATES = "7 8 9 11 19 24 25 26 30 37 38 42"
_ENTRY_STATES = "7 8 11 19 24 25 26 30 37 38"
_PLAIN_STATES = "8 19 24 25 30"


def _node(
    node_id: str,
    role: str,
    name: str,
    *children: Dict[str, Any],
    actions: Sequence[str] = (),
    states: str = _PLAIN_STATES,
    bounds: Sequence[int] = (0, 0, 10, 10),
) -> Dict[str, Any]:
    """A node in the raw form the Linux tree adapter produces."""
    return {
        "id": node_id,
        "role": role,
        "name": name,
        "bounds": list(bounds),
        "states": states.split(),
        "actions": list(actions),
        "children": list(children),
    }


def _tree(*roots: Dict[str, Any]) -> AccessibilityTree:
    return raw_tree_to_domain({"tree": list(roots)})


_BAR = "n_bar"
_BAR_NAME = "Search with Google or enter address"


def _address_bar(role: str = "combo box") -> Dict[str, Any]:
    return _node(_BAR, role, _BAR_NAME, actions=["activate"], states=_BAR_STATES)


def _navigation_toolbar(
    bar: Optional[Dict[str, Any]], role: str = "tool bar"
) -> Dict[str, Any]:
    controls = [
        _node("n_site_info", "push button", "View site information", actions=["click"])
    ]
    if bar is not None:
        controls.append(bar)
    controls.append(
        _node(
            "n_bookmark",
            "push button",
            "Bookmark this page (Ctrl+D)",
            actions=["click"],
        )
    )
    return _node(
        "n_navigation", role, "Navigation", _node("n_nav_panel", "panel", "", *controls)
    )


def _sign_up_page() -> Dict[str, Any]:
    """A loaded page whose controls are named with 'address'."""
    return _node(
        "n_page",
        "document web",
        "Sign up",
        _node("n_page_email_label", "label", "Email address", actions=["click"]),
        _node(
            "n_page_email",
            "entry",
            "Email address",
            actions=["activate"],
            states=_ENTRY_STATES,
        ),
        _node("n_page_shipping", "combo box", "Shipping address", actions=["open"]),
        _node(
            "n_page_enter",
            "entry",
            "Enter address",
            actions=["activate"],
            states=_ENTRY_STATES,
        ),
        _node(
            "n_page_billing",
            "combo box",
            "Billing address",
            actions=["activate"],
            states=_BAR_STATES,
        ),
        _node("n_page_edit", "link", "Edit address", actions=["jump"]),
        _node("n_page_save", "push button", "Save address", actions=["press"]),
    )


def _new_tab_page() -> Dict[str, Any]:
    """A background tab whose search button is named like the address bar."""
    return _node(
        "n_new_tab",
        "document web",
        "New Tab",
        _node("n_new_tab_search", "push button", _BAR_NAME, actions=["press"]),
    )


def _content_area(*documents: Dict[str, Any]) -> Dict[str, Any]:
    panes = [
        _node(
            f"n_scroll_{index}",
            "scroll pane",
            "",
            _node(f"n_internal_frame_{index}", "internal frame", "", document),
        )
        for index, document in enumerate(documents)
    ]
    return _node("n_content", "panel", "", *panes)


def _browser_window(*frame_children: Dict[str, Any]) -> AccessibilityTree:
    return _tree(
        _node(
            "n_app",
            "application",
            "Firefox",
            _node("n_frame", "frame", "Sign up — Mozilla Firefox", *frame_children),
        )
    )


def _firefox_159(
    bar_role: str = "combo box",
    toolbar_role: str = "tool bar",
    with_bar: bool = True,
    page_first: bool = False,
) -> AccessibilityTree:
    """The Firefox 159 window shape: chrome tool bar, then the page and a tab."""
    toolbar = _navigation_toolbar(
        _address_bar(bar_role) if with_bar else None, toolbar_role
    )
    content = _content_area(_sign_up_page(), _new_tab_page())
    children = (content, toolbar) if page_first else (toolbar, content)
    return _browser_window(*children)


def _chrome_with_decoys(with_bar: bool = True) -> AccessibilityTree:
    """Browser chrome only (no document) with controls that are not the bar."""
    controls = [
        _node(
            "n_find",
            "entry",
            "Find in page",
            actions=["activate"],
            states=_ENTRY_STATES,
        ),
        _node("n_address_label", "label", "Address:"),
        _node("n_address_book", "push button", "Address book", actions=["press"]),
    ]
    if with_bar:
        controls.append(_address_bar())
    return _tree(
        _node(
            "n_frame",
            "frame",
            "Mozilla Firefox",
            _node("n_toolbar", "tool bar", "Navigation", *controls),
        )
    )


_ENTRY_BAR = "n_entry_bar"


def _entry_bar_and_combo_box_bar(entry_first: bool) -> AccessibilityTree:
    entry_bar = _node(
        _ENTRY_BAR,
        "entry",
        "Search or enter address",
        actions=["activate"],
        states=_ENTRY_STATES,
    )
    toolbar = _node("n_toolbar", "tool bar", "Navigation", _address_bar())
    children = (entry_bar, toolbar) if entry_first else (toolbar, entry_bar)
    return _tree(_node("n_frame", "frame", "Browser", *children))


_SERVER_ADDRESS = "n_server_address"


def _server_address_combo_box(container_role: str) -> AccessibilityTree:
    """An application form's combo box named with 'address', outside any page."""
    combo = _node(
        _SERVER_ADDRESS,
        "combo box",
        "Server address",
        actions=["activate"],
        states=_BAR_STATES,
    )
    return _tree(
        _node(
            "n_frame",
            "frame",
            "Connect",
            _node("n_container", container_role, "", combo),
        )
    )


def _no_address_named_node() -> AccessibilityTree:
    return _tree(
        _node(
            "n_frame",
            "frame",
            "Editor",
            _node("n_find", "entry", "Find in page", actions=["activate"]),
            _node("n_ok", "push button", "OK", actions=["press"]),
        )
    )


_DOCUMENT_ROLES = [
    "document web",
    "document frame",
    "document text",
    "document email",
    "document presentation",
    "document spreadsheet",
]


def _page_entry(document_role: str, levels_up: int) -> AccessibilityTree:
    """An entry 'Email address' whose document ancestor is ``levels_up`` above it."""
    node = _node(
        "n_page_email",
        "entry",
        "Email address",
        actions=["activate"],
        states=_ENTRY_STATES,
    )
    for role in ("form", "section")[: levels_up - 1]:
        node = _node(f"n_{role}", role, "", node)
    document = _node("n_document", document_role, "Sign up", node)
    return _tree(_node("n_frame", "frame", "Browser", document))


def _nested_documents() -> AccessibilityTree:
    inner = _node(
        "n_inner_document",
        "document web",
        "Checkout",
        _node(
            "n_inner_shipping",
            "entry",
            "Shipping address",
            actions=["activate"],
            states=_ENTRY_STATES,
        ),
    )
    outer = _node(
        "n_outer_document",
        "document web",
        "Shop",
        _node("n_inner_frame", "internal frame", "", inner),
    )
    return _tree(_node("n_frame", "frame", "Browser", outer))


def _tool_bar_inside_the_page() -> AccessibilityTree:
    """A web page's own tool bar holding a combo box named with 'address'."""
    page = _node(
        "n_page",
        "document web",
        "Compose",
        _node(
            "n_page_toolbar",
            "tool bar",
            "Recipients",
            _node(
                "n_page_to",
                "combo box",
                "To address",
                actions=["activate"],
                states=_BAR_STATES,
            ),
        ),
    )
    return _tree(_node("n_frame", "frame", "Browser", page))


def _recorded(
    node_id: str,
    role: str,
    name: str,
    states: str,
    actions: Sequence[str],
    bounds: Sequence[int],
    *children: Dict[str, Any],
) -> Dict[str, Any]:
    return _node(
        node_id, role, name, *children, actions=actions, states=states, bounds=bounds
    )


_RECORDED_BAR = "n_063"


def _recorded_firefox_159(with_bar: bool = True) -> AccessibilityTree:
    """Reduced from an inspect of Firefox 159.0 showing a page with address fields.

    Kept: the address bar with its ancestors, the page document with every node
    under it, and the New Tab document with its button named like the address
    bar. Ids, roles, names, states, actions and bounds are as recorded; the
    other nodes of the window are left out.
    """
    bar = [
        _recorded(
            _RECORDED_BAR,
            "combo box",
            "Search with Google or enter address",
            "7 8 9 11 19 24 25 26 30 37 38 42",
            ["activate"],
            [260, 48, 610, 32],
        )
    ]
    page = _recorded(
        "n_074",
        "document web",
        "Addr Test",
        "8 11 12 19 24 25 30 38",
        [],
        [-1, -1, -1, -1],
        _recorded(
            "n_075", "heading", "Form", "8 19 24 25 30 38", [], [8, 106, 1146, 44]
        ),
        _recorded(
            "n_076",
            "label",
            "Email address",
            "8 19 24 25 30 38",
            ["click"],
            [8, 172, 108, 22],
        ),
        _recorded(
            "n_077",
            "entry",
            "Email address",
            "7 8 11 19 24 25 26 30 37 38",
            ["activate"],
            [116, 172, 180, 24],
        ),
        _recorded(
            "n_078",
            "label",
            "Shipping address",
            "8 19 24 25 30 38",
            ["click"],
            [300, 172, 131, 22],
        ),
        _recorded(
            "n_079",
            "combo box",
            "Shipping address",
            "8 9 11 19 24 25 30 42",
            ["open"],
            [431, 172, 68, 24],
            _recorded(
                "n_080",
                "menu",
                "",
                "8 19 24 25 30",
                ["click"],
                [-1, -1, -1, -1],
                _recorded(
                    "n_081",
                    "menu item",
                    "Home",
                    "8 11 19 22 23 24 25 30",
                    ["select"],
                    [-1, -1, -1, -1],
                ),
                _recorded(
                    "n_082",
                    "menu item",
                    "Work",
                    "8 11 19 22 24 30",
                    ["select"],
                    [-1, -1, -1, -1],
                ),
            ),
        ),
        _recorded(
            "n_083",
            "entry",
            "Enter address",
            "7 8 11 19 24 25 26 30 37 38",
            ["activate"],
            [503, 172, 180, 24],
        ),
        _recorded(
            "n_084",
            "label",
            "Billing address",
            "8 19 24 25 30 38",
            ["click"],
            [687, 172, 113, 22],
        ),
        _recorded(
            "n_085",
            "combo box",
            "Billing address",
            "7 8 9 11 19 24 25 26 30 37 38 42",
            ["activate"],
            [800, 172, 180, 24],
        ),
        _recorded(
            "n_086",
            "link",
            "Edit address",
            "8 11 19 24 25 30 38",
            ["jump"],
            [984, 172, 94, 22],
        ),
        _recorded(
            "n_087",
            "button",
            "Save address",
            "8 11 19 24 25 30 38",
            ["press"],
            [8, 196, 94, 24],
        ),
    )
    new_tab = _recorded(
        "n_799",
        "document web",
        "New Tab",
        "8 11 19 24 30 38",
        [],
        [0, 85, 1162, 645],
        _recorded(
            "n_816",
            "button",
            "Search with Google or enter address",
            "8 11 19 24 30 38",
            ["press"],
            [328, 181, 506, 52],
        ),
    )
    return _tree(
        _recorded(
            "n_001",
            "application",
            "Firefox",
            "25 30",
            [],
            [0, 0, 0, 0],
            _recorded(
                "n_002",
                "frame",
                "Addr Test — Mozilla Firefox",
                "1 8 11 19 21 24 25 30",
                ["click"],
                [0, 0, 1162, 730],
                _recorded(
                    "n_053",
                    "tool bar",
                    "Navigation",
                    "8 19 24 25 30",
                    ["clickAncestor"],
                    [0, 44, 1162, 40],
                    _recorded(
                        "n_061",
                        "panel",
                        "",
                        "8 19 24 25 30",
                        ["click"],
                        [226, 48, 678, 32],
                        *(bar if with_bar else []),
                    ),
                ),
                _recorded(
                    "n_071",
                    "panel",
                    "",
                    "8 19 24 25 30",
                    ["clickAncestor"],
                    [0, 84, 1162, 646],
                    _recorded(
                        "n_072",
                        "scroll pane",
                        "",
                        "8 19 24 25 30",
                        ["clickAncestor"],
                        [0, 84, 1162, 646],
                        _recorded(
                            "n_073",
                            "internal frame",
                            "",
                            "8 11 19 24 25 30",
                            ["click"],
                            [0, 85, 1162, 645],
                            page,
                        ),
                    ),
                    _recorded(
                        "n_797",
                        "scroll pane",
                        "",
                        "8 19 24 30",
                        ["clickAncestor"],
                        [0, 84, 1162, 646],
                        _recorded(
                            "n_798",
                            "internal frame",
                            "",
                            "8 19 24 30",
                            ["click"],
                            [0, 85, 1162, 645],
                            new_tab,
                        ),
                    ),
                ),
            ),
        )
    )


# ═══════════════════════════════════════════════════════════════════════
# Recording ports and the goto runner
# ═══════════════════════════════════════════════════════════════════════


class _RecordingPorts:
    """Tree, action and input ports that record every request in one list.

    ``events`` holds ``("get_tree",)``, ``("do_action", node_id, action)``,
    ``("key", keys)`` and ``("type_text", text)`` in the order goto made them.
    ``do_action`` answers with ``success``; a refusal carries
    ``failure_reason`` and ``available_actions``.
    """

    def __init__(
        self,
        tree: AccessibilityTree,
        success: bool = True,
        failure_reason: Optional[str] = None,
        available_actions: Sequence[str] = ("activate",),
    ) -> None:
        self._tree = tree
        self._success = success
        self._failure_reason = failure_reason
        self._available_actions = tuple(available_actions)
        self.events: List[Tuple[Any, ...]] = []

    def get_tree(self, session: Any) -> AccessibilityTree:
        self.events.append(("get_tree",))
        return self._tree

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        self.events.append(("do_action", node_id, action_name))
        if self._success:
            return ActionPortResult(
                success=True, available_actions=self._available_actions
            )
        return ActionPortResult(
            success=False,
            available_actions=self._available_actions,
            failure_reason=self._failure_reason,
        )

    def key(self, session: Any, key_specs: List[str]) -> None:
        self.events.append(("key", tuple(key_specs)))

    def type_text(self, session: Any, text: str, delay_ms: int = 0) -> None:
        self.events.append(("type_text", text))


def _goto_use_case(ports: _RecordingPorts) -> GotoUseCase:
    repo = FakeSessionRepository()
    repo.save(_linux_session())
    return GotoUseCase(
        tree_port=ports, action_port=ports, input_port=ports, session_repo=repo
    )


def _assert_activate_at_most_once(events: List[Tuple[Any, ...]]) -> None:
    requests = [event for event in events if event[0] == "do_action"]
    assert len(requests) <= 1, requests
    assert all(event[2] == "activate" for event in requests), requests


def _goto(ports: _RecordingPorts) -> GotoResult:
    """Run goto on ``ports``; every run asks for at most one 'activate'."""
    result = _goto_use_case(ports).execute(_SESSION_ID, _URL)
    _assert_activate_at_most_once(ports.events)
    return result


def _navigated_through(node_id: str) -> List[Tuple[Any, ...]]:
    return [
        ("get_tree",),
        ("do_action", node_id, "activate"),
        ("key", ("ctrl+a",)),
        ("type_text", _URL),
        ("key", ("Return",)),
    ]


_NAVIGATED = GotoResult(
    status="ok", session_id=_SESSION_ID, action="goto", url=_URL, reason=None
)
_NOT_FOUND_RESULT = GotoResult(
    status="error", session_id=_SESSION_ID, action="goto", url=None, reason=_NOT_FOUND
)


# ═══════════════════════════════════════════════════════════════════════
# The address bar is found and used
# ═══════════════════════════════════════════════════════════════════════

_TREES_WITH_AN_ADDRESS_BAR: Dict[str, Callable[[], Tuple[AccessibilityTree, str]]] = {
    "firefox_159": lambda: (_firefox_159(), _BAR),
    "firefox_159_canonical_role_spelling": lambda: (
        _firefox_159(bar_role="combo_box", toolbar_role="tool_bar"),
        _BAR,
    ),
    "page_before_the_toolbar": lambda: (_firefox_159(page_first=True), _BAR),
    "recorded_firefox_159": lambda: (_recorded_firefox_159(), _RECORDED_BAR),
    "chrome_decoys": lambda: (_chrome_with_decoys(), _BAR),
    "entry_bar_first": lambda: (
        _entry_bar_and_combo_box_bar(entry_first=True),
        _ENTRY_BAR,
    ),
    "combo_box_bar_first": lambda: (
        _entry_bar_and_combo_box_bar(entry_first=False),
        _BAR,
    ),
    "combo_box_directly_in_a_tool_bar": lambda: (
        _server_address_combo_box("tool bar"),
        _SERVER_ADDRESS,
    ),
}


class TestGotoUsesTheAddressBar:
    @pytest.mark.parametrize("case", list(_TREES_WITH_AN_ADDRESS_BAR))
    def test_activates_the_address_bar_and_then_types_the_url(self, case: str) -> None:
        tree, address_bar = _TREES_WITH_AN_ADDRESS_BAR[case]()
        ports = _RecordingPorts(tree)

        result = _goto(ports)

        assert ports.events == _navigated_through(address_bar)
        assert result == _NAVIGATED


# ═══════════════════════════════════════════════════════════════════════
# Nothing else is ever used: not found, nothing sent
# ═══════════════════════════════════════════════════════════════════════


def _page_entry_cases() -> Dict[str, Callable[[], AccessibilityTree]]:
    cases: Dict[str, Callable[[], AccessibilityTree]] = {}
    for role in _DOCUMENT_ROLES:
        for spelling, written in (
            ("spaced", role),
            ("underscored", role.replace(" ", "_")),
        ):
            key = role.replace(" ", "_")
            cases[f"entry_directly_in_{key}_{spelling}"] = lambda written=written: (
                _page_entry(written, levels_up=1)
            )
            cases[f"entry_three_levels_below_{key}_{spelling}"] = (
                lambda written=written: _page_entry(written, levels_up=3)
            )
    return cases


_TREES_WITHOUT_AN_ADDRESS_BAR: Dict[str, Callable[[], AccessibilityTree]] = {
    "firefox_159_without_the_bar": lambda: _firefox_159(with_bar=False),
    "no_node_named_address": _no_address_named_node,
    "chrome_decoys_without_the_bar": lambda: _chrome_with_decoys(with_bar=False),
    "combo_box_outside_a_tool_bar": lambda: _server_address_combo_box("panel"),
    "nested_documents": _nested_documents,
    "tool_bar_inside_the_page": _tool_bar_inside_the_page,
    "recorded_firefox_159_without_the_bar": lambda: _recorded_firefox_159(
        with_bar=False
    ),
    **_page_entry_cases(),
}


class TestGotoNeverUsesAnotherControl:
    @pytest.mark.parametrize("case", list(_TREES_WITHOUT_AN_ADDRESS_BAR))
    def test_reports_not_found_and_sends_nothing(self, case: str) -> None:
        ports = _RecordingPorts(_TREES_WITHOUT_AN_ADDRESS_BAR[case]())

        result = _goto(ports)

        assert result == _NOT_FOUND_RESULT
        assert ports.events == [("get_tree",)]


# ═══════════════════════════════════════════════════════════════════════
# Focus-before-type guard on the combo-box address bar
# ═══════════════════════════════════════════════════════════════════════

_REFUSALS: Dict[str, Tuple[Optional[str], Tuple[str, ...], str]] = {
    "not_enabled": (
        ACTION_NOT_ENABLED,
        ("activate",),
        "Could not focus address bar: node 'n_bar' is not enabled",
    ),
    "declined": (
        ACTION_DECLINED,
        ("activate",),
        "Could not focus address bar: 'activate' was declined by the application",
    ),
    "unavailable_with_activate_listed": (
        None,
        ("activate",),
        "Could not focus address bar: 'activate' unavailable (available: ['activate'])",
    ),
    "unavailable_with_no_actions": (
        None,
        (),
        "Could not focus address bar: 'activate' unavailable (available: [])",
    ),
}


class TestFailedFocusOnTheAddressBar:
    @pytest.mark.parametrize("case", list(_REFUSALS))
    def test_names_the_cause_and_types_nothing(self, case: str) -> None:
        failure_reason, available, reason = _REFUSALS[case]
        ports = _RecordingPorts(
            _firefox_159(),
            success=False,
            failure_reason=failure_reason,
            available_actions=available,
        )

        result = _goto(ports)

        assert result == GotoResult(
            status="error",
            session_id=_SESSION_ID,
            action="goto",
            url=None,
            reason=reason,
        )
        assert ports.events == [("get_tree",), ("do_action", _BAR, "activate")]


# ═══════════════════════════════════════════════════════════════════════
# CLI command and MCP tool
# ═══════════════════════════════════════════════════════════════════════


def _cli_and_mcp_goto(
    tree: Callable[[], AccessibilityTree],
) -> Tuple[Any, Tuple[bool, str], List[_RecordingPorts]]:
    cli_ports = _RecordingPorts(tree())
    mcp_ports = _RecordingPorts(tree())
    cli_result = _invoke_cli("goto_uc", _goto_use_case(cli_ports), ["goto", _URL])
    mcp_result = _call_mcp("goto_uc", _goto_use_case(mcp_ports), "goto", {"url": _URL})
    return cli_result, mcp_result, [cli_ports, mcp_ports]


class TestGotoCommandAndTool:
    def test_not_found_reports_the_search_on_cli_and_mcp(self) -> None:
        cli_result, mcp_result, recordings = _cli_and_mcp_goto(
            lambda: _firefox_159(with_bar=False)
        )

        _assert_same_json_on_cli_and_mcp(
            cli_result,
            mcp_result,
            {
                "status": "error",
                "session_id": _SESSION_ID,
                "action": "goto",
                "reason": _NOT_FOUND,
            },
        )
        assert [ports.events for ports in recordings] == [[("get_tree",)]] * 2

    def test_navigation_through_the_bar_reports_ok_on_cli_and_mcp(self) -> None:
        cli_result, mcp_result, recordings = _cli_and_mcp_goto(_firefox_159)

        _assert_same_json_on_cli_and_mcp(
            cli_result,
            mcp_result,
            {
                "status": "ok",
                "session_id": _SESSION_ID,
                "action": "goto",
                "url": _URL,
            },
        )
        assert [ports.events for ports in recordings] == [_navigated_through(_BAR)] * 2

    def test_goto_description_and_parameters_are_unchanged(self) -> None:
        """Pins goto's command description and generated parameters, option descriptions included."""
        runner = make_cli_runner()
        with patch("aiyes.cli.main.operation_log_adapter", MagicMock()):
            result = runner.invoke(cli, ["help-json"])

        assert result.exit_code == 0, result.stderr
        entries = [
            command
            for command in json.loads(result.stdout)["commands"]
            if command["name"] == "goto"
        ]
        assert len(entries) == 1
        assert entries[0]["description"] == (
            "Navigate a linux browser session to a URL via address-bar automation."
        )
        assert entries[0]["parameters"] == {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Session ID.",
                },
                "url": {"type": "string"},
            },
            "required": ["url"],
        }


# ═══════════════════════════════════════════════════════════════════════
# Documentation and source text
# ═══════════════════════════════════════════════════════════════════════

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _normalised(text: str) -> str:
    """``text`` with every run of whitespace replaced by one space."""
    return " ".join(text.split())


def _goto_module_docstring() -> str:
    docstring = ast.get_docstring(
        ast.parse(_read("src/aiyes/domain/use_cases/goto.py"))
    )
    assert docstring is not None
    return _normalised(docstring)


_DOCSTRING_TEXT = [
    ("names_the_combo_box", "combo box", True),
    ("mentions_the_page", "page", True),
    ("drops_the_entry_only_rule", 'role=="entry" AND name~"address"', False),
]


class TestGotoModuleDocstring:
    @pytest.mark.parametrize(
        "text, present",
        [(text, present) for _, text, present in _DOCSTRING_TEXT],
        ids=[case for case, _, _ in _DOCSTRING_TEXT],
    )
    def test_docstring_describes_the_address_bar_rule(
        self, text: str, present: bool
    ) -> None:
        assert (text in _goto_module_docstring()) is present


def _readme_goto_paragraph() -> str:
    """The README paragraph that starts with ``goto <url>``, found by that start."""
    blocks = [_normalised(block) for block in re.split(r"\n\s*\n", _read("README.md"))]
    found = [block for block in blocks if block.startswith("`goto <url>`")]
    assert len(found) == 1, found
    return found[0]


_README_GUARD_SENTENCE = (
    "If the address bar cannot be located or focused, `goto` reports a structured "
    "error and sends no keystrokes rather than typing into the wrong control."
)

_README_TEXT = [
    (
        "drops_the_entry_only_rule",
        'an `entry` whose accessible name contains "address"',
        False,
    ),
    ("names_the_combo_box", "`combo box`", True),
    ("says_the_web_page_is_never_used", "web page", True),
    ("keeps_the_guard_sentence", _README_GUARD_SENTENCE, True),
    ("names_the_verified_firefox_version", "Firefox 159", True),
    ("names_the_verified_interface_language", "English", True),
    (
        "drops_the_claim_for_any_address_bar",
        "works for any AT-SPI-exposed address bar",
        False,
    ),
]


class TestReadmeGotoParagraph:
    @pytest.mark.parametrize(
        "text, present",
        [(text, present) for _, text, present in _README_TEXT],
        ids=[case for case, _, _ in _README_TEXT],
    )
    def test_paragraph_describes_the_address_bar_rule(
        self, text: str, present: bool
    ) -> None:
        assert (text in _readme_goto_paragraph()) is present


def _unreleased_fixed_entries() -> List[str]:
    """Top-level entries under ``### Fixed`` of ``[Unreleased]``, with sub-items."""
    entries: List[List[str]] = []
    for line in _changelog_unreleased_fixed():
        if line.startswith("- "):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    for lines in entries:
        while lines and lines[-1] == "":
            lines.pop()
    return ["\n".join(lines) for lines in entries]


_GOTO_VISIBLE_CHANGES = "- **Visible changes for callers of `goto`**"
_GOTO_FIX_ENTRY = (
    "- **`goto` finds the address bar of current Firefox and no longer types "
    "into the web page**"
)

# The first and the last of the eight entries that precede the goto entries.
_FIRST_EARLIER_ENTRY = "- **Tree pruning keeps the children of removed nodes**"
_LAST_EARLIER_ENTRY = "- **Visible changes for scenario authors**"

# sha256 of those eight entries, joined by "\n".
_EARLIER_ENTRIES_SHA256 = (
    "26a09e46f4e9868960e8f43936e094952b493956794977f714d4d27ebc034535"
)


def _entry_index(entries: List[str], title: str) -> int:
    """Index of the one entry that starts with ``title``."""
    found = [index for index, entry in enumerate(entries) if entry.startswith(title)]
    assert len(found) == 1, (title, found)
    return found[0]


def _goto_visible_changes_entry() -> str:
    entries = _unreleased_fixed_entries()
    return entries[_entry_index(entries, _GOTO_VISIBLE_CHANGES)]


class TestChangelog:
    def test_earlier_entries_are_unchanged(self) -> None:
        entries = _unreleased_fixed_entries()
        first = _entry_index(entries, _FIRST_EARLIER_ENTRY)
        last = _entry_index(entries, _LAST_EARLIER_ENTRY)
        earlier = "\n".join(entries[first : last + 1])

        assert hashlib.sha256(earlier.encode("utf-8")).hexdigest() == (
            _EARLIER_ENTRIES_SHA256
        )

    def test_visible_changes_for_goto_follow_the_goto_fix_entry(self) -> None:
        entries = _unreleased_fixed_entries()

        # The goto fix entry comes after the earlier entries.
        assert _entry_index(entries, _GOTO_FIX_ENTRY) > (
            _entry_index(entries, _LAST_EARLIER_ENTRY)
        )
        # The visible changes for goto come right after the goto fix entry.
        assert _entry_index(entries, _GOTO_VISIBLE_CHANGES) == (
            _entry_index(entries, _GOTO_FIX_ENTRY) + 1
        )

    @pytest.mark.parametrize(
        "reason", [_OLD_NOT_FOUND, _NOT_FOUND], ids=["old_reason", "new_reason"]
    )
    def test_visible_changes_for_goto_quote_the_not_found_reason(
        self, reason: str
    ) -> None:
        assert reason in _normalised(_goto_visible_changes_entry())
