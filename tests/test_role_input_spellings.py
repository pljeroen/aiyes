"""Role requests: every spelling of a role selects the same nodes.

find, wait and do compare a requested role with the node's role after
folding whitespace, turning spaces into underscores and applying the alias
table, so "button", "push_button" and "push button" select the same nodes
whichever of those spellings the node carries. A selected node is printed
with its own role text, and that text, sent back as the request, selects the
node again; this holds for an Android class-derived role such as "Button" too.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from aiyes.cli import presenter
from tests.description_support import SESSION_ID, forest, node, ports_for

# Spellings of one role that a request and a node may each use.
SPELLINGS: Dict[str, Tuple[str, ...]] = {
    "button": ("push button", "push_button", "button"),
    "check box": ("check box", "check_box", "checkbox"),
    "text": ("text", "textbox"),
    "Android Button": ("Button",),
}

# A node of another role in every tree, which no request here may select.
OTHER_ROLE: Dict[str, str] = {
    "button": "check box",
    "check box": "push button",
    "text": "push button",
    "Android Button": "push button",
}

Raw = List[Dict[str, Any]]


def rotations(items: Sequence[str]) -> List[Tuple[str, ...]]:
    return [tuple(items[i:]) + tuple(items[:i]) for i in range(len(items))]


def tree(role: str, order: Sequence[str]) -> Tuple[Raw, List[str]]:
    """A window holding one node per spelling in ``order`` between nodes of
    other roles; returns the raw tree and the ids of the spelled nodes."""
    children = [node("label", "Before", node_id="before")]
    ids = []
    for index, spelling in enumerate(order):
        ids.append(f"t{index}")
        children.append(node(spelling, f"Target {index}", node_id=f"t{index}"))
    children.append(node(OTHER_ROLE[role], "After", node_id="after"))
    return forest(node("frame", "Win", node_id="win", children=children)), ids


def spelling_of(raw: Raw) -> Dict[str, str]:
    return {child["id"]: child["role"] for child in raw[0]["children"]}


def find(raw: Raw, request: str) -> Tuple[List[str], Dict[str, str]]:
    """Selected ids, and the role printed for each selected node."""
    result = ports_for(raw).find_uc().execute(session_id=SESSION_ID, role=request)
    printed = json.loads(presenter.format_find_nodes(result))
    return [n.id for n in result], {item["id"]: item["role"] for item in printed}


def wait(raw: Raw, request: str) -> Tuple[List[str], Dict[str, str]]:
    """Selected id; wait prints no role."""
    result = (
        ports_for(raw)
        .wait_uc()
        .execute(session_id=SESSION_ID, role=request, timeout=0.0)
    )
    return ([result.id] if result.found and result.id else []), {}


def do(raw: Raw, request: str) -> Tuple[List[str], Dict[str, str]]:
    """Id of the node acted on, and the role printed for it."""
    ports = ports_for(raw)
    result = ports.do_uc().execute(
        session_id=SESSION_ID, role=request, action_name="click"
    )
    printed = json.loads(presenter.format_do(result))
    acted = [entry[1] for entry in ports.log if entry[0] == "do_action"]
    if "found" not in printed:
        return acted, {}
    assert acted == [printed["found"]["id"]]
    return acted, {printed["found"]["id"]: printed["found"]["role"]}


COMMANDS = {"find": find, "wait": wait, "do": do}


@pytest.mark.parametrize("role", sorted(SPELLINGS))
@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_spelling_selects_the_same_nodes_and_the_printed_role_selects_them_again(
    command: str, role: str
) -> None:
    select = COMMANDS[command]
    for order in rotations(SPELLINGS[role]):
        raw, ids = tree(role, order)
        spelled = spelling_of(raw)
        # find selects every spelled node in tree order; wait and do the first.
        expected = ids if command == "find" else ids[:1]

        for request in SPELLINGS[role]:
            selected, printed = select(raw, request)
            assert selected == expected, (order, request, selected)
            for node_id, role_text in printed.items():
                assert role_text == spelled[node_id], (
                    order,
                    request,
                    node_id,
                    role_text,
                )

        # The role a caller reads back from find selects the node again.
        _, shown = find(raw, SPELLINGS[role][0])
        for node_id in expected:
            selected, _ = select(raw, shown[node_id])
            assert node_id in selected, (order, node_id, shown[node_id], selected)
