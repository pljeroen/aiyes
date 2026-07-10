"""AIYES-113: ancestor / section-scoped find — RED phase.

These tests pin the AIYES-113 behavior the implementation (A9) must deliver.
They FAIL before implementation because the new domain helpers
(``locate_ancestor_nodes`` / ``flatten_scoped_subtrees``), the new result type
(``FindResult`` / ``AncestorRef``), the new ``within_role`` / ``within_name``
parameters on ``FindUseCase.execute``, and the caller wiring do not exist yet.

Traceability — VALIDATED_INTENT_PKG.must_tier1_coverage_matrix:

  R1  Scoped find restricts the candidate pool to the descendants of the
      matched ancestor(s); the existing role/name_pattern/state filter chain
      runs UNCHANGED on that pool; nothing outside the matched subtree(s) is
      returned. (constraints C1, C7, C8)
  R2  Absent ancestor -> structured scoped-miss (scope_requested=True,
      scope_matched=False, nodes=(), matched_ancestors=()), NEVER a whole-tree
      fallback; mechanically distinguishable from a scope-matched-zero result
      (scope_matched=True, matched_ancestors!=()); no exception. (C1, C2, C4)
  R3  Unscoped find is byte-identical to pre-AIYES-113 at BOTH the Python
      return level (FindResult is a collections.abc.Sequence[FoundNode] —
      len/index/slice/negative-index/iterate/truthiness) and the CLI/MCP output
      level (bare JSON array, byte-identical golden). (C3)
  C4  Multiple matching ancestors -> search ALL subtrees, dedup by node.id in
      stable first-seen (pre-order) order; matched_ancestors lists all matched
      ancestors (plural).
  C8  All three find surfaces thread within_role/within_name: MCP _handle_find,
      CLI find_cmd, scenario runner step.kind=='find'.
  R4  Scoped result echoes scope_matched + matched_ancestors (AncestorRef
      {id,role,name} per matched ancestor); the ancestor may sit ABOVE
      FoundNode.parent_role/parent_name. (C5)

Every test asserts an OBSERVABLE post-state (the returned set, the never-fallback
control flow, the byte-identical JSON, the Sequence idioms, or the wired-through
call), not merely a return value.
"""

from __future__ import annotations

import asyncio
import collections.abc
import dataclasses
from collections.abc import Mapping
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node, flatten_nodes
from aiyes.domain.use_cases.find import FindUseCase, FoundNode
from aiyes.cli.presenter import format_find_nodes
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.domain.scenario import ScenarioStep

from tests.conftest import (
    FakeAccessibilityTree,
    FakeSessionRepository,
    FakeTreeStore,
    make_domain_tree,
    make_node,
)

# The new AIYES-113 API is imported behind a guard so that this module COLLECTS
# cleanly in the RED phase (before implementation). Each test references these
# symbols directly; while they are None the tests FAIL at call time (never a
# collection error), which is the intended RED signal.
try:  # pragma: no cover - exercised by the RED run
    from aiyes.domain.use_cases.find import AncestorRef, FindResult
    from aiyes.domain.tree import flatten_scoped_subtrees, locate_ancestor_nodes

    _NEW_API_PRESENT = True
except ImportError:  # pragma: no cover - RED phase only
    AncestorRef = None  # type: ignore[assignment,misc]
    FindResult = None  # type: ignore[assignment,misc]
    flatten_scoped_subtrees = None  # type: ignore[assignment]
    locate_ancestor_nodes = None  # type: ignore[assignment]
    _NEW_API_PRESENT = False


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_linux_session(session_id: str = "test-s") -> Session:
    return Session(
        session_id=session_id,
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=(),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
    )


def _setup_find_uc(tree: AccessibilityTree, session_id: str = "test-s") -> FindUseCase:
    repo = FakeSessionRepository()
    repo.save(_make_linux_session(session_id))
    return FindUseCase(
        tree=FakeAccessibilityTree(tree),
        session_repo=repo,
        tree_store=FakeTreeStore(),
    )


def _dn(node_id: str, role: str, name: str, children: tuple = ()) -> Node:
    """Build a raw domain Node (no enrichment) for pure-helper tests."""
    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=(0, 0, 10, 10),
        states=("enabled",),
        actions=("click",),
        children=tuple(children),
    )


def _sectioned_tree() -> AccessibilityTree:
    """Two sibling sections, each with an identically-named "Add" button.

    Mirrors the WEB_UI_DRIVING_FRICTION_REPORT "two Add buttons" motivating
    example. Named sections survive pruning.
    """
    return make_domain_tree(
        [
            make_node(
                "n_win",
                "frame",
                "Window",
                children=[
                    make_node(
                        "n_sec_hol",
                        "section",
                        "Holidays",
                        children=[make_node("n_add_hol", "push_button", "Add")],
                    ),
                    make_node(
                        "n_sec_rec",
                        "section",
                        "Recurring holidays",
                        children=[make_node("n_add_rec", "push_button", "Add")],
                    ),
                ],
            )
        ]
    )


def _deep_tree() -> AccessibilityTree:
    """Ancestor two levels above the hit (section > group > button)."""
    return make_domain_tree(
        [
            make_node(
                "n_win",
                "frame",
                "Window",
                children=[
                    make_node(
                        "n_sec_rec",
                        "section",
                        "Recurring holidays",
                        children=[
                            make_node(
                                "n_group",
                                "group",
                                "Row",
                                children=[make_node("n_add", "push_button", "Add")],
                            )
                        ],
                    )
                ],
            )
        ]
    )


def _ids(nodes: Any) -> list:
    return [n.id for n in nodes]


def _ancestor_id(entry: Any) -> str:
    """Read an ancestor id from either a Mapping or an object."""
    if isinstance(entry, Mapping):
        return entry["id"]
    return entry.id


# The exact byte string the CURRENT presenter emits for the two FoundNodes built
# in TestBackCompatByteIdentical. Captured from format_find_nodes before the
# change (json.dumps(masked, indent=2); no trailing newline). The unscoped path
# MUST remain byte-identical to this (C3).
_GOLDEN_UNSCOPED_BARE_ARRAY = "\n".join(
    [
        "[",
        "  {",
        '    "id": "n_add_recurring",',
        '    "role": "push_button",',
        '    "name": "Add",',
        '    "bounds": [',
        "      10,",
        "      20,",
        "      30,",
        "      40",
        "    ],",
        '    "states": [',
        '      "enabled",',
        '      "visible"',
        "    ],",
        '    "actions": [',
        '      "click"',
        "    ],",
        '    "value": null',
        "  },",
        "  {",
        '    "id": "n_add_holidays",',
        '    "role": "push_button",',
        '    "name": "Add",',
        '    "bounds": [',
        "      50,",
        "      60,",
        "      70,",
        "      80",
        "    ],",
        '    "states": [',
        '      "enabled"',
        "    ],",
        '    "actions": [',
        '      "click",',
        '      "focus"',
        "    ],",
        '    "value": "v1"',
        "  }",
        "]",
    ]
)


# ═══════════════════════════════════════════════════════════════════════
# Pure helpers: locate_ancestor_nodes (R1 / C1)
# ═══════════════════════════════════════════════════════════════════════


class TestLocateAncestorNodes:
    """Pure-function unit tests for locate_ancestor_nodes."""

    def _flat(self) -> list:
        tree = _sectioned_tree()
        return flatten_nodes(tree.roots)

    def test_role_only_matches_both_sections(self) -> None:
        found = locate_ancestor_nodes(self._flat(), "section", None)
        assert set(_ids(found)) == {"n_sec_hol", "n_sec_rec"}

    def test_name_only_matches_single_ancestor(self) -> None:
        found = locate_ancestor_nodes(self._flat(), None, "Recurring holidays")
        assert _ids(found) == ["n_sec_rec"]

    def test_name_only_substring_matches_multiple(self) -> None:
        # "holidays" is a substring of both "Holidays" and "Recurring holidays".
        found = locate_ancestor_nodes(self._flat(), None, "holidays")
        assert set(_ids(found)) == {"n_sec_hol", "n_sec_rec"}

    def test_role_and_name_anded(self) -> None:
        found = locate_ancestor_nodes(self._flat(), "section", "Recurring holidays")
        assert _ids(found) == ["n_sec_rec"]

    def test_role_and_name_role_mismatch_excludes(self) -> None:
        # No 'frame' is named "Recurring holidays", so AND-ing yields nothing.
        found = locate_ancestor_nodes(self._flat(), "frame", "Recurring holidays")
        assert list(found) == []

    def test_no_match_returns_empty(self) -> None:
        found = locate_ancestor_nodes(self._flat(), "section", "Nonexistent")
        assert list(found) == []


# ═══════════════════════════════════════════════════════════════════════
# Pure helpers: flatten_scoped_subtrees (C4 dedup / ancestor-boundary)
# ═══════════════════════════════════════════════════════════════════════


class TestFlattenScopedSubtrees:
    """Pure-function unit tests for flatten_scoped_subtrees."""

    def _nested(self) -> tuple:
        btn = _dn("btn_x", "push_button", "X")
        inner = _dn("sec_inner", "section", "Group inner", children=(btn,))
        outer = _dn("sec_outer", "section", "Group", children=(inner,))
        return outer, inner, btn

    def test_descendants_only_excludes_the_ancestor_itself(self) -> None:
        outer, inner, btn = self._nested()
        flattened = flatten_scoped_subtrees([outer])
        assert _ids(flattened) == ["sec_inner", "btn_x"]
        assert "sec_outer" not in _ids(flattened)

    def test_overlapping_ancestors_dedup_by_id_first_seen(self) -> None:
        outer, inner, btn = self._nested()
        # outer's subtree = [inner, btn]; inner's subtree = [btn].
        flattened = flatten_scoped_subtrees([outer, inner])
        assert _ids(flattened) == ["sec_inner", "btn_x"]  # btn appears exactly once
        assert _ids(flattened).count("btn_x") == 1

    def test_multiple_disjoint_ancestors_document_order(self) -> None:
        a_btn = _dn("a_btn", "push_button", "A")
        b_btn = _dn("b_btn", "push_button", "B")
        sec_a = _dn("sec_a", "section", "A", children=(a_btn,))
        sec_b = _dn("sec_b", "section", "B", children=(b_btn,))
        flattened = flatten_scoped_subtrees([sec_a, sec_b])
        assert _ids(flattened) == ["a_btn", "b_btn"]

    def test_empty_ancestor_list_yields_empty(self) -> None:
        assert list(flatten_scoped_subtrees([])) == []


# ═══════════════════════════════════════════════════════════════════════
# R1 — scoped find returns only the in-scope node(s)
# ═══════════════════════════════════════════════════════════════════════


class TestScopedFindR1:
    def test_scoped_find_returns_only_the_in_scope_button(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_role="section",
            within_name="Recurring holidays",
        )
        # ONLY the Add button under "Recurring holidays" — the sibling section's
        # identically-named Add button is NOT returned.
        assert _ids(result) == ["n_add_rec"]
        assert "n_add_hol" not in _ids(result)
        assert result.scope_requested is True
        assert result.scope_matched is True

    def test_scoped_hit_echoes_the_matched_ancestor(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_role="section",
            within_name="Recurring holidays",
        )
        assert len(result.matched_ancestors) == 1
        anc = result.matched_ancestors[0]
        assert (anc.id, anc.role, anc.name) == (
            "n_sec_rec",
            "section",
            "Recurring holidays",
        )

    def test_name_only_scope_also_works(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_name="Recurring holidays",
        )
        assert _ids(result) == ["n_add_rec"]


# ═══════════════════════════════════════════════════════════════════════
# R2 — never-fallback + distinguishable scoped-miss
# ═══════════════════════════════════════════════════════════════════════


class TestNeverFallbackR2:
    def test_absent_ancestor_is_a_structured_scoped_miss(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())

        # Baseline: unscoped, the SAME selector DOES match (two Add buttons).
        baseline = uc.execute("test-s", "push_button", name_pattern="Add")
        assert set(_ids(baseline)) == {"n_add_hol", "n_add_rec"}

        # Scoped to a non-existent ancestor -> structured miss, empty result.
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_name="Nonexistent Section",
        )
        assert result.scope_requested is True
        assert result.scope_matched is False
        assert tuple(result.nodes) == ()
        assert tuple(result.matched_ancestors) == ()
        # CRUCIAL: nodes that WOULD match unscoped are NOT returned — proving
        # there is no silent whole-tree fallback.
        assert "n_add_hol" not in _ids(result)
        assert "n_add_rec" not in _ids(result)

    def test_scope_miss_does_not_run_filter_chain_over_whole_tree(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spy the find use case's name matcher: on a scope-miss the existing
        role/name_pattern/state filter chain must NOT run over the whole tree."""
        import aiyes.domain.use_cases.find as find_mod

        calls: list = []
        original = find_mod.name_matches

        def _spy(node_name: str, pattern: str) -> bool:
            calls.append((node_name, pattern))
            return original(node_name, pattern)

        monkeypatch.setattr(find_mod, "name_matches", _spy)

        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_name="Nonexistent Section",
        )
        assert result.scope_matched is False
        assert tuple(result.nodes) == ()
        # The name-pattern filter (find.py) never ran -> no whole-tree fallback.
        assert calls == []

    def test_scope_matched_zero_is_distinguishable_from_miss(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())

        # Ancestor matches, but no "Delete" button inside it -> matched, zero.
        matched_zero = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Delete",
            within_name="Recurring holidays",
        )
        assert matched_zero.scope_requested is True
        assert matched_zero.scope_matched is True
        assert tuple(matched_zero.nodes) == ()
        assert len(matched_zero.matched_ancestors) == 1

        # Ancestor absent -> miss, zero.
        miss = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Delete",
            within_name="Nonexistent Section",
        )
        assert miss.scope_matched is False
        assert tuple(miss.matched_ancestors) == ()

        # The two zero-node outcomes are mechanically distinct.
        assert matched_zero.scope_matched != miss.scope_matched
        assert bool(matched_zero.matched_ancestors) != bool(miss.matched_ancestors)


# ═══════════════════════════════════════════════════════════════════════
# C4 — multiple matching ancestors: search all, dedup, plural echo
# ═══════════════════════════════════════════════════════════════════════


class TestMultipleAncestorsC4:
    def test_two_ancestors_search_all_and_dedup(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_name="holidays",  # substring matches BOTH sections
        )
        assert result.scope_matched is True
        assert set(_ids(result)) == {"n_add_hol", "n_add_rec"}
        # No duplicate node ids.
        assert len(_ids(result)) == len(set(_ids(result)))

    def test_matched_ancestors_lists_both(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_name="holidays",
        )
        assert len(result.matched_ancestors) == 2
        assert {a.id for a in result.matched_ancestors} == {"n_sec_hol", "n_sec_rec"}


# ═══════════════════════════════════════════════════════════════════════
# R3 — back-compat: Sequence protocol + byte-identical unscoped output
# ═══════════════════════════════════════════════════════════════════════


class TestBackCompatSequenceR3:
    def test_unscoped_result_is_a_sequence_like_the_old_list(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute("test-s", "push_button", name_pattern="Add")

        # New return type, but behaves exactly like the old List[FoundNode].
        assert isinstance(result, FindResult)
        assert isinstance(result, collections.abc.Sequence)
        assert result.scope_requested is False
        assert result.scope_matched is True
        assert tuple(result.matched_ancestors) == ()

        # len / indexing / slicing / negative index / iteration / truthiness.
        assert len(result) == 2
        assert result[0].id == "n_add_hol"
        assert result[1].id == "n_add_rec"
        assert [n.id for n in result[:1]] == ["n_add_hol"]
        assert result[-1].id == "n_add_rec"
        assert [n.id for n in result] == ["n_add_hol", "n_add_rec"]
        assert bool(result) is True

    def test_empty_unscoped_result_is_falsy(self) -> None:
        uc = _setup_find_uc(_sectioned_tree())
        result = uc.execute("test-s", "push_button", name_pattern="NoSuchName")
        assert len(result) == 0
        assert bool(result) is False
        assert result.scope_requested is False


class TestBackCompatByteIdentical:
    def _two_found_nodes(self) -> tuple:
        return (
            FoundNode(
                id="n_add_recurring",
                role="push_button",
                name="Add",
                bounds=(10, 20, 30, 40),
                states=("enabled", "visible"),
                actions=("click",),
            ),
            FoundNode(
                id="n_add_holidays",
                role="push_button",
                name="Add",
                bounds=(50, 60, 70, 80),
                states=("enabled",),
                actions=("click", "focus"),
                value="v1",
            ),
        )

    def test_unscoped_presenter_output_is_byte_identical_golden(self) -> None:
        result = FindResult(nodes=self._two_found_nodes())
        rendered = format_find_nodes(result)
        # STRING equality (not json.loads-equal): key order and whitespace must
        # match the pre-change bytes exactly.
        assert rendered == _GOLDEN_UNSCOPED_BARE_ARRAY

    def test_unscoped_presenter_output_has_no_envelope_keys(self) -> None:
        result = FindResult(nodes=self._two_found_nodes())
        rendered = format_find_nodes(result)
        assert '"scope_matched"' not in rendered
        assert '"matched_ancestors"' not in rendered
        assert rendered.lstrip().startswith("[")  # bare array, not an object


# ═══════════════════════════════════════════════════════════════════════
# R4 / C5 — observability: scoped envelope + ancestor above the parent
# ═══════════════════════════════════════════════════════════════════════


class TestObservabilityR4:
    def test_matched_ancestor_is_the_scoping_node_not_the_immediate_parent(
        self,
    ) -> None:
        uc = _setup_find_uc(_deep_tree())
        result = uc.execute(
            "test-s",
            "push_button",
            name_pattern="Add",
            within_role="section",
            within_name="Recurring holidays",
        )
        assert _ids(result) == ["n_add"]
        # The hit's IMMEDIATE parent is the group, but the SCOPING ancestor
        # echoed is the section two levels up.
        assert result[0].parent_role == "group"
        assert result[0].parent_name == "Row"
        assert len(result.matched_ancestors) == 1
        anc = result.matched_ancestors[0]
        assert (anc.id, anc.role, anc.name) == (
            "n_sec_rec",
            "section",
            "Recurring holidays",
        )
        assert anc.role != result[0].parent_role

    def test_scoped_presenter_emits_envelope_with_ancestor_echo(self) -> None:
        import json

        found = FoundNode(
            id="n_add",
            role="push_button",
            name="Add",
            bounds=(0, 0, 1, 1),
            states=("enabled",),
            actions=("click",),
        )
        result = FindResult(
            nodes=(found,),
            scope_requested=True,
            scope_matched=True,
            matched_ancestors=(
                AncestorRef(id="n_sec_rec", role="section", name="Recurring holidays"),
            ),
        )
        parsed = json.loads(format_find_nodes(result))
        assert set(parsed.keys()) == {"nodes", "scope_matched", "matched_ancestors"}
        assert parsed["scope_matched"] is True
        assert parsed["matched_ancestors"] == [
            {"id": "n_sec_rec", "role": "section", "name": "Recurring holidays"}
        ]
        assert [n["id"] for n in parsed["nodes"]] == ["n_add"]

    def test_presenter_miss_and_matched_zero_are_distinguishable_bytes(self) -> None:
        import json

        miss = FindResult(
            nodes=(),
            scope_requested=True,
            scope_matched=False,
            matched_ancestors=(),
        )
        matched_zero = FindResult(
            nodes=(),
            scope_requested=True,
            scope_matched=True,
            matched_ancestors=(
                AncestorRef(id="n_sec_rec", role="section", name="Recurring holidays"),
            ),
        )
        miss_out = format_find_nodes(miss)
        matched_zero_out = format_find_nodes(matched_zero)

        # C2: the two zero-node outcomes MUST differ at the output boundary.
        assert miss_out != matched_zero_out

        miss_parsed = json.loads(miss_out)
        matched_parsed = json.loads(matched_zero_out)
        assert miss_parsed["scope_matched"] is False
        assert miss_parsed["matched_ancestors"] == []
        assert matched_parsed["scope_matched"] is True
        assert matched_parsed["matched_ancestors"] != []


# ═══════════════════════════════════════════════════════════════════════
# C8 — all three find surfaces thread within_role / within_name
# ═══════════════════════════════════════════════════════════════════════


def _scoped_hit_result() -> Any:
    return FindResult(
        nodes=(
            FoundNode(
                id="n_add_rec",
                role="push_button",
                name="Add",
                bounds=(0, 0, 1, 1),
                states=("enabled",),
                actions=("click",),
            ),
        ),
        scope_requested=True,
        scope_matched=True,
        matched_ancestors=(
            AncestorRef(id="n_sec_rec", role="section", name="Recurring holidays"),
        ),
    )


class TestCallerWiringMCPC8:
    def _mock_deps(self, **overrides: Any) -> ServerDependencies:
        fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
        fields.update(overrides)
        return ServerDependencies(**fields)

    @pytest.mark.asyncio
    async def test_mcp_find_threads_within_params(self) -> None:
        mock_find = MagicMock()
        mock_find.execute.return_value = _scoped_hit_result()
        mock_resolve = MagicMock(return_value="test-session")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        deps = self._mock_deps(
            find_uc=mock_find,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        await server.call_tool(
            "find",
            {
                "session_id": "test-session",
                "role": "push_button",
                "name_pattern": "Add",
                "within_role": "section",
                "within_name": "Recurring holidays",
            },
        )

        mock_find.execute.assert_called_once_with(
            session_id="test-session",
            role="push_button",
            name_pattern="Add",
            state=None,
            within_role="section",
            within_name="Recurring holidays",
        )


class _SpyFindUseCase:
    """Records execute kwargs; returns a canned result."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list = []

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self._result


class TestCallerWiringScenarioC8:
    def _build_started_executor(self, find: Any) -> ScenarioUseCaseExecutor:
        from types import SimpleNamespace

        start = SimpleNamespace(
            execute=lambda **kw: SimpleNamespace(session_id="s1", backend="linux")
        )
        repo = FakeSessionRepository()
        repo.save(_make_linux_session("s1"))
        executor = ScenarioUseCaseExecutor(
            session_start=start,
            inspect=SimpleNamespace(execute=lambda **kw: SimpleNamespace(tree=None)),
            find=find,
            action=SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
            type_text=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
            screenshot=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
            session_stop=SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
            session_repo=repo,
            clock=SimpleNamespace(now=lambda: 0.0),
        )
        executor.execute(
            ScenarioStep(
                id="start",
                kind="start_session",
                parameters={"command": "app", "wait_seconds": 0.0},
            )
        )
        return executor

    def test_scenario_find_threads_within_params_and_envelope(self) -> None:
        spy = _SpyFindUseCase(_scoped_hit_result())
        executor = self._build_started_executor(spy)

        result = executor.execute(
            ScenarioStep(
                id="scoped_find",
                kind="find",
                parameters={
                    "role": "push_button",
                    "name_pattern": "Add",
                    "within_role": "section",
                    "within_name": "Recurring holidays",
                },
            )
        )

        # within_role / within_name reached the use case.
        assert spy.calls, "find use case was never called"
        last = spy.calls[-1]
        assert last.get("within_role") == "section"
        assert last.get("within_name") == "Recurring holidays"

        # When scope was requested, the step output carries the scope envelope.
        output = result.output
        assert output.get("scope_matched") is True
        ancestors = output.get("matched_ancestors")
        assert ancestors, "matched_ancestors missing from scoped find output"
        assert _ancestor_id(ancestors[0]) == "n_sec_rec"

    def test_scenario_unscoped_find_has_no_scope_keys(self) -> None:
        unscoped = FindResult(
            nodes=(
                FoundNode(
                    id="n_x",
                    role="push_button",
                    name="Add",
                    bounds=(0, 0, 1, 1),
                    states=("enabled",),
                    actions=("click",),
                ),
            )
        )
        spy = _SpyFindUseCase(unscoped)
        executor = self._build_started_executor(spy)

        result = executor.execute(
            ScenarioStep(
                id="plain_find",
                kind="find",
                parameters={"role": "push_button", "name_pattern": "Add"},
            )
        )
        output = result.output
        # C3 conditional envelope: no scope keys on the unscoped path.
        assert "scope_matched" not in output
        assert "matched_ancestors" not in output


class TestCallerWiringCLIC8:
    def test_cli_find_accepts_within_options_and_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        import aiyes.cli.main as cli_main

        spy = _SpyFindUseCase(_scoped_hit_result())
        monkeypatch.setattr(cli_main, "find_uc", spy)
        monkeypatch.setattr(cli_main, "resolve_session_id", lambda s: s or "s1")

        runner = CliRunner()
        cli_result = runner.invoke(
            cli_main.cli,
            [
                "find",
                "push_button",
                "Add",
                "--within-role",
                "section",
                "--within-name",
                "Recurring holidays",
            ],
        )
        assert cli_result.exit_code == 0, cli_result.output

        assert spy.calls, "find use case was never called"
        last = spy.calls[-1]
        assert last.get("within_role") == "section"
        assert last.get("within_name") == "Recurring holidays"
        # Scoped CLI output is the envelope, not a bare array.
        assert '"scope_matched"' in cli_result.output
