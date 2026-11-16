"""Find output: a scoped result reports its scope fields whatever list type carries it.

format_find_nodes accepts a FindResult or a list of FoundNode. A list whose
``scope_requested`` attribute is set is rendered like a FindResult: the output
envelope carries the ``scope_matched`` and ``matched_ancestors`` read from the
result itself, and a missing scope field raises AttributeError naming it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aiyes.cli.presenter import format_find_nodes
from aiyes.domain.use_cases.find import AncestorRef, FindResult, FoundNode

_NODE = FoundNode(
    id="n_001",
    role="button",
    name="OK",
    bounds=(0, 0, 10, 10),
    states=("enabled",),
    actions=("click",),
    parent_role="panel",
    parent_name="Main",
    index_in_parent=0,
    depth=2,
    sibling_count=1,
)
_ANCESTOR = AncestorRef(id="n_000", role="panel", name="Main")


class ScopedNodes(list[FoundNode]):
    """A list of found nodes that carries scope attributes of its own."""


def _scoped_list(nodes: tuple[FoundNode, ...], **attributes: Any) -> ScopedNodes:
    result = ScopedNodes(nodes)
    for name, value in attributes.items():
        setattr(result, name, value)
    return result


@pytest.mark.parametrize(
    ("nodes", "scope_matched", "matched_ancestors", "expected_ancestors"),
    [
        pytest.param((), False, (), [], id="scope-missed-without-nodes"),
        pytest.param(
            (_NODE,),
            True,
            (_ANCESTOR,),
            [{"id": "n_000", "role": "panel", "name": "Main"}],
            id="scope-matched-with-an-ancestor",
        ),
    ],
)
def test_list_result_that_carries_scope_fields_is_rendered_like_a_find_result(
    nodes: tuple[FoundNode, ...],
    scope_matched: bool,
    matched_ancestors: tuple[AncestorRef, ...],
    expected_ancestors: list[dict[str, str]],
) -> None:
    as_list = _scoped_list(
        nodes,
        scope_requested=True,
        scope_matched=scope_matched,
        matched_ancestors=matched_ancestors,
    )
    as_find_result = FindResult(
        nodes=nodes,
        scope_requested=True,
        scope_matched=scope_matched,
        matched_ancestors=matched_ancestors,
    )

    rendered = json.loads(format_find_nodes(as_list))

    assert rendered == json.loads(format_find_nodes(as_find_result))
    assert rendered["scope_matched"] is scope_matched
    assert rendered["matched_ancestors"] == expected_ancestors


@pytest.mark.parametrize(
    ("attributes", "missing"),
    [
        pytest.param({}, "scope_matched", id="no-scope-fields"),
        pytest.param(
            {"scope_matched": True}, "matched_ancestors", id="no-matched-ancestors"
        ),
    ],
)
def test_list_result_that_requests_scope_without_a_scope_field_raises_attribute_error(
    attributes: dict[str, Any], missing: str
) -> None:
    as_list = _scoped_list((_NODE,), scope_requested=True, **attributes)

    with pytest.raises(AttributeError, match=missing):
        format_find_nodes(as_list)
