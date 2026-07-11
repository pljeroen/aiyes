"""AIYES-116: first-class Node.resource_id + exact resource-id find selector — RED.

These tests pin the AIYES-116 behavior the implementation (A9) must deliver.
They FAIL before implementation because the new ``resource_id`` field on
``Node`` / ``FoundNode``, the exact ``resource_id`` selector on
``FindUseCase.execute``, the three omit-when-empty serializer edits, the two
persistence-reconstruction edits, and the CLI/MCP/scenario wiring do not exist
yet. The module COLLECTS cleanly (it imports only already-present symbols); each
new-behavior test fails at CALL time (TypeError on the unknown ``resource_id``
kwarg/field, or AttributeError on the missing attribute), which is the intended
RED signal.

Traceability — VALIDATED_INTENT_PKG.must_tier1_coverage_matrix (bound_design):

  R1 / C1  Node gains a first-class ``resource_id`` (Android viewIdResourceName),
           populated by the android adapter; "" for a no-resource-id element and
           for the AT-SPI (raw_tree_to_domain) path; ``stable_id`` is
           BYTE-IDENTICAL across the value-preserving ``_stable_android_id``
           refactor (golden-pinned, green-throughout).
  R2 / C3  find gains an OPTIONAL exact-equality ``resource_id`` selector
           (full-string ==, NEVER substring/regex), AND-composed with
           role/name_pattern/within_*; empty/None => no filter (truthy guard);
           threaded to the mcp / cli(--resource-id) / scenario find call sites.
  R3 / C4  THREE serializers (node_to_dict, format_find_nodes, scenario
           find-step) OMIT ``resource_id`` when "" (byte-identical to pre-change)
           and INCLUDE it when non-empty; the shared generic
           ``_to_jsonable``/``_jsonable_dict`` is UNCHANGED (None-drop only, NOT
           empty-string-drop) — LOAD-BEARING back-compat.
  R3 / C5  Persistence round-trip preserves ``resource_id`` (FileTreeStore JSON
           path + ``_raw_node_to_domain``); a "" node round-trips to "".
  R4 / C2  ``FoundNode`` gains ``resource_id`` and the find construction site
           passes ``resource_id=n.resource_id`` so a populated Node.resource_id
           surfaces in find output.

Every test asserts an OBSERVABLE post-state (the parsed field value, the exact
selected set, the byte-identical serialized bytes, the reloaded value, or the
wired-through call), not merely a return value.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml
from aiyes.adapters.file_tree_store import FileTreeStore
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_use_case_executor import (
    ScenarioUseCaseExecutor,
    _to_jsonable,
)
from aiyes.cli.presenter import format_find_nodes
from aiyes.domain.output_formatter import node_to_dict
from aiyes.domain.scenario import ScenarioStep
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node, raw_tree_to_domain
from aiyes.domain.use_cases.find import FindResult, FindUseCase, FoundNode

from tests.conftest import (
    FakeAccessibilityTree,
    FakeSessionRepository,
    FakeTreeStore,
    make_node,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures / helpers
# ═══════════════════════════════════════════════════════════════════════

# The two golden Android stable_id strings the CURRENT parser emits. AIYES-116's
# _stable_android_id refactor (accept the already-extracted resource_id instead
# of re-reading it inline) MUST keep these BYTE-IDENTICAL. Captured verbatim from
# the on-disk XML fixtures below (and mirror test_aiyes66_android_node_identity).
_RID_VALUE = "com.example.publicdemo:id/create"

XML_WITH_RESOURCE_ID = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="Create" resource-id="com.example.publicdemo:id/create"
        class="android.widget.Button" package="com.example.publicdemo"
        content-desc="Create post" bounds="[10,20][110,80]" clickable="true" />
</hierarchy>
"""

XML_WITHOUT_RESOURCE_ID = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.ImageButton"
        package="com.app" content-desc="Back" bounds="[0,0][48,48]"
        clickable="true" />
</hierarchy>
"""

_GOLDEN_STABLE_ID_WITH_RID = (
    "android:rid=com.example.publicdemo:id/create;"
    "class=android.widget.Button;"
    "name=Create post;"
    "bounds=10,20,100,60;"
    "path=0"
)

_GOLDEN_STABLE_ID_WITHOUT_RID = (
    "android:rid=;class=android.widget.ImageButton;name=Back;bounds=0,0,48,48;path=0"
)


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


def _rid_tree() -> AccessibilityTree:
    """A frame with three buttons distinguished by resource_id.

    - n_add: resource_id EXACTLY the query value -> the sole exact match.
    - n_decoy_name: NAME contains the query value but resource_id DIFFERS ->
      excluded (proves the predicate is on resource_id, distinct from the
      substring name matcher).
    - n_extra: resource_id has the query value as a PREFIX/substring ->
      excluded (proves exact ==, not substring).
    """
    return AccessibilityTree(
        roots=(
            Node(
                id="n_frame",
                role="frame",
                name="Win",
                bounds=(0, 0, 100, 100),
                states=("enabled",),
                actions=(),
                children=(
                    Node(
                        id="n_add",
                        role="button",
                        name="Add",
                        bounds=(0, 0, 10, 10),
                        states=("enabled",),
                        actions=("click",),
                        resource_id="com.x:id/add",
                    ),
                    Node(
                        id="n_decoy_name",
                        role="button",
                        name="com.x:id/add",  # name CONTAINS the query
                        bounds=(0, 0, 10, 10),
                        states=("enabled",),
                        actions=("click",),
                        resource_id="com.x:id/other",
                    ),
                    Node(
                        id="n_extra",
                        role="button",
                        name="Extra",
                        bounds=(0, 0, 10, 10),
                        states=("enabled",),
                        actions=("click",),
                        resource_id="com.x:id/add_extra",  # query is a prefix
                    ),
                ),
            ),
        )
    )


def _ids(nodes: Any) -> list:
    return [n.id for n in nodes]


class _SpyFindUseCase:
    """Records execute kwargs; returns a canned result (mirrors AIYES-113)."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list = []

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self._result


def _plain_result() -> FindResult:
    """A minimal unscoped FindResult usable by wiring/presenter tests."""
    return FindResult(
        nodes=(
            FoundNode(
                id="n_x",
                role="button",
                name="Add",
                bounds=(0, 0, 1, 1),
                states=("enabled",),
                actions=("click",),
            ),
        )
    )


def _build_started_executor(find: Any) -> ScenarioUseCaseExecutor:
    """Build a started scenario executor whose find use case is `find`."""
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


# ═══════════════════════════════════════════════════════════════════════
# R1 / C1 — Node.resource_id first-class field (Android-populated; else "")
# ═══════════════════════════════════════════════════════════════════════


class TestNodeResourceIdFieldR1C1:
    def test_android_node_carries_resource_id(self) -> None:
        tree, _ = parse_uiautomator_xml(XML_WITH_RESOURCE_ID)
        node = tree.roots[0]
        # RED: Node has no resource_id attribute yet (AttributeError).
        assert node.resource_id == _RID_VALUE

    def test_android_node_without_resource_id_is_empty(self) -> None:
        tree, _ = parse_uiautomator_xml(XML_WITHOUT_RESOURCE_ID)
        node = tree.roots[0]
        assert node.resource_id == ""

    def test_atspi_sourced_node_resource_id_is_empty(self) -> None:
        # The AT-SPI path builds domain nodes via raw_tree_to_domain from raw
        # payloads that never carry a "resource_id" key -> defaults to "".
        tree = raw_tree_to_domain({"tree": [make_node("n_1", "push_button", "OK")]})
        node = tree.roots[0]
        assert node.resource_id == ""

    def test_stable_id_byte_identical_with_resource_id(self) -> None:
        # REGRESSION PIN (green-throughout): the value-preserving
        # _stable_android_id refactor must leave this string byte-identical.
        tree, _ = parse_uiautomator_xml(XML_WITH_RESOURCE_ID)
        assert tree.roots[0].stable_id == _GOLDEN_STABLE_ID_WITH_RID

    def test_stable_id_byte_identical_without_resource_id(self) -> None:
        # REGRESSION PIN (green-throughout).
        tree, _ = parse_uiautomator_xml(XML_WITHOUT_RESOURCE_ID)
        assert tree.roots[0].stable_id == _GOLDEN_STABLE_ID_WITHOUT_RID


# ═══════════════════════════════════════════════════════════════════════
# R2 / C3 — exact-equality resource_id selector, AND-composed, distinct
# ═══════════════════════════════════════════════════════════════════════


class TestExactResourceIdSelectorR2C3:
    def test_selects_only_the_exact_resource_id_match(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        result = uc.execute("test-s", "button", resource_id="com.x:id/add")
        # ONLY the node whose resource_id EXACTLY equals the query.
        assert _ids(result) == ["n_add"]

    def test_exact_not_substring_and_distinct_from_name(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        result = uc.execute("test-s", "button", resource_id="com.x:id/add")
        ids = _ids(result)
        # A node whose NAME contains the query but whose resource_id DIFFERS is
        # NOT matched (the predicate is on resource_id, not the substring name).
        assert "n_decoy_name" not in ids
        # A node whose resource_id merely CONTAINS the query as a prefix is NOT
        # matched (exact ==, never substring — the "btn_x" vs "btn_x_extra" pin).
        assert "n_extra" not in ids

    def test_empty_and_none_resource_id_is_no_filter(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        # Absent, None, and "" all mean "no resource_id filter" (truthy guard):
        # each reproduces the byte-identical pre-change matched set.
        no_arg = _ids(uc.execute("test-s", "button"))
        none_arg = _ids(uc.execute("test-s", "button", resource_id=None))
        empty_arg = _ids(uc.execute("test-s", "button", resource_id=""))
        assert set(no_arg) == {"n_add", "n_decoy_name", "n_extra"}
        assert none_arg == no_arg
        assert empty_arg == no_arg

    def test_ands_with_role(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        # resource_id matches n_add, but n_add's role is "button" not "frame" ->
        # AND with role excludes it.
        result = uc.execute("test-s", "frame", resource_id="com.x:id/add")
        assert "n_add" not in _ids(result)

    def test_ands_with_name_pattern(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        hit = uc.execute(
            "test-s", "button", name_pattern="Add", resource_id="com.x:id/add"
        )
        assert _ids(hit) == ["n_add"]
        miss = uc.execute(
            "test-s", "button", name_pattern="Cancel", resource_id="com.x:id/add"
        )
        assert _ids(miss) == []

    def test_ands_with_within_name_scope(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        # In-scope: the resource_id match sits under the "Win" frame.
        scoped_hit = uc.execute(
            "test-s", "button", resource_id="com.x:id/add", within_name="Win"
        )
        assert _ids(scoped_hit) == ["n_add"]
        # Out-of-scope ancestor: structured scoped-miss, the resource_id match is
        # NOT returned (AND with within_* scoping).
        scoped_miss = uc.execute(
            "test-s", "button", resource_id="com.x:id/add", within_name="Nonexistent"
        )
        assert _ids(scoped_miss) == []


# ═══════════════════════════════════════════════════════════════════════
# R2 / C3 — thin wiring: --resource-id (CLI), resource_id arg (MCP), scenario
# ═══════════════════════════════════════════════════════════════════════


class TestSelectorWiringR2C3:
    def test_cli_find_threads_resource_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        import aiyes.cli.main as cli_main

        spy = _SpyFindUseCase(_plain_result())
        monkeypatch.setattr(cli_main, "find_uc", spy)
        monkeypatch.setattr(cli_main, "resolve_session_id", lambda s: s or "s1")

        runner = CliRunner()
        cli_result = runner.invoke(
            cli_main.cli,
            ["find", "button", "--resource-id", "com.x:id/add"],
        )
        assert cli_result.exit_code == 0, cli_result.output
        assert spy.calls, "find use case was never called"
        assert spy.calls[-1].get("resource_id") == "com.x:id/add"

    @pytest.mark.asyncio
    async def test_mcp_find_threads_resource_id(self) -> None:
        mock_find = MagicMock()
        mock_find.execute.return_value = _plain_result()
        mock_resolve = MagicMock(return_value="test-session")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
        fields.update(
            find_uc=mock_find,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=MagicMock(),
        )
        server = create_mcp_server(ServerDependencies(**fields))

        await server.call_tool(
            "find",
            {
                "session_id": "test-session",
                "role": "button",
                "resource_id": "com.x:id/add",
            },
        )

        assert mock_find.execute.call_args is not None
        assert mock_find.execute.call_args.kwargs.get("resource_id") == "com.x:id/add"

    def test_scenario_find_threads_resource_id_when_present(self) -> None:
        spy = _SpyFindUseCase(_plain_result())
        executor = _build_started_executor(spy)
        executor.execute(
            ScenarioStep(
                id="rid_find",
                kind="find",
                parameters={"role": "button", "resource_id": "com.x:id/add"},
            )
        )
        assert spy.calls, "find use case was never called"
        assert spy.calls[-1].get("resource_id") == "com.x:id/add"

    def test_scenario_find_omits_resource_id_when_absent(self) -> None:
        # Byte-identical call when the step never mentions resource_id (guard,
        # green-throughout): the kwarg is omitted, mirroring the AIYES-113
        # within_* conditional-inclusion precedent.
        spy = _SpyFindUseCase(_plain_result())
        executor = _build_started_executor(spy)
        executor.execute(
            ScenarioStep(
                id="plain_find",
                kind="find",
                parameters={"role": "button", "name_pattern": "Add"},
            )
        )
        assert spy.calls, "find use case was never called"
        assert "resource_id" not in spy.calls[-1]


# ═══════════════════════════════════════════════════════════════════════
# R3 / C4 — three serializers omit resource_id when "" (LOAD-BEARING);
#           shared generic _to_jsonable UNCHANGED
# ═══════════════════════════════════════════════════════════════════════


class TestSerializerOmitWhenEmptyR3C4:
    # ---- mechanism_a: output_formatter.node_to_dict --------------------
    def test_node_to_dict_omits_empty_resource_id_byte_identical(self) -> None:
        node = Node(
            id="n_1",
            role="Button",
            name="OK",
            bounds=(1, 2, 3, 4),
            states=("enabled",),
            actions=("click",),
            resource_id="",
        )
        d = node_to_dict(node)
        # Byte-identical to pre-change: NO resource_id key; whole-dict golden.
        assert d == {
            "id": "n_1",
            "role": "Button",
            "name": "OK",
            "bounds": [1, 2, 3, 4],
            "states": ["enabled"],
            "actions": ["click"],
        }

    def test_node_to_dict_includes_non_empty_resource_id(self) -> None:
        node = Node(
            id="n_1",
            role="Button",
            name="OK",
            bounds=(1, 2, 3, 4),
            states=("enabled",),
            actions=("click",),
            resource_id="com.x:id/add",
        )
        d = node_to_dict(node)
        assert d["resource_id"] == "com.x:id/add"

    # ---- mechanism_b: presenter.format_find_nodes ----------------------
    def test_format_find_nodes_omits_empty_resource_id_byte_identical(self) -> None:
        fn = FoundNode(
            id="n_1",
            role="button",
            name="OK",
            bounds=(1, 2, 3, 4),
            states=("enabled",),
            actions=("click",),
            resource_id="",
        )
        rendered = format_find_nodes(FindResult(nodes=(fn,)))
        # STRING equality: key order + whitespace must match the pre-change bytes
        # exactly (bare unscoped array, no resource_id key).
        golden = "\n".join(
            [
                "[",
                "  {",
                '    "id": "n_1",',
                '    "role": "button",',
                '    "name": "OK",',
                '    "bounds": [',
                "      1,",
                "      2,",
                "      3,",
                "      4",
                "    ],",
                '    "states": [',
                '      "enabled"',
                "    ],",
                '    "actions": [',
                '      "click"',
                "    ],",
                '    "value": null',
                "  }",
                "]",
            ]
        )
        assert rendered == golden

    def test_format_find_nodes_includes_non_empty_resource_id(self) -> None:
        fn = FoundNode(
            id="n_1",
            role="button",
            name="OK",
            bounds=(1, 2, 3, 4),
            states=("enabled",),
            actions=("click",),
            resource_id="com.x:id/add",
        )
        rendered = format_find_nodes(FindResult(nodes=(fn,)))
        assert '"resource_id": "com.x:id/add"' in rendered

    # ---- mechanism_c: scenario find-step serialization -----------------
    def test_scenario_find_step_omits_empty_resource_id(self) -> None:
        spy = _SpyFindUseCase(
            FindResult(
                nodes=(
                    FoundNode(
                        id="n_add",
                        role="button",
                        name="Add",
                        bounds=(0, 0, 1, 1),
                        states=("enabled",),
                        actions=("click",),
                        resource_id="",
                    ),
                )
            )
        )
        executor = _build_started_executor(spy)
        result = executor.execute(
            ScenarioStep(
                id="f",
                kind="find",
                parameters={"role": "button", "name_pattern": "Add"},
            )
        )
        node_dict = result.output["nodes"][0]
        assert "resource_id" not in node_dict

    def test_scenario_find_step_includes_non_empty_resource_id(self) -> None:
        spy = _SpyFindUseCase(
            FindResult(
                nodes=(
                    FoundNode(
                        id="n_add",
                        role="button",
                        name="Add",
                        bounds=(0, 0, 1, 1),
                        states=("enabled",),
                        actions=("click",),
                        resource_id="com.x:id/add",
                    ),
                )
            )
        )
        executor = _build_started_executor(spy)
        result = executor.execute(
            ScenarioStep(
                id="f",
                kind="find",
                parameters={"role": "button", "name_pattern": "Add"},
            )
        )
        node_dict = result.output["nodes"][0]
        assert node_dict.get("resource_id") == "com.x:id/add"

    # ---- HARD INVARIANT: shared generic _to_jsonable UNCHANGED ---------
    def test_shared_to_jsonable_is_none_drop_not_empty_string_drop(self) -> None:
        """The shared generic serializer must keep its is-not-None filter — a
        legitimately-"" field on a NON-find dataclass still serializes (only
        None is dropped). If someone 'fixes' _to_jsonable to drop falsy values
        globally (wrong scope, C4/C8), this guard fails."""

        @dataclasses.dataclass
        class _Generic:
            kept_empty: str = ""
            dropped_none: Optional[str] = None
            present: str = "x"

        out = _to_jsonable(_Generic())
        # "" is PRESERVED (proves no global empty-string-drop); None is dropped.
        assert out == {"kept_empty": "", "present": "x"}


# ═══════════════════════════════════════════════════════════════════════
# R3 / C5 — persistence round-trip preserves resource_id
# ═══════════════════════════════════════════════════════════════════════


class TestPersistenceRoundTripR3C5:
    def _node(self, resource_id: str) -> Node:
        return Node(
            id="n_1",
            role="button",
            name="Add",
            bounds=(0, 0, 10, 10),
            states=("enabled",),
            actions=("click",),
            resource_id=resource_id,
        )

    def test_file_tree_store_round_trips_non_empty_resource_id(self, tmp_path) -> None:
        store = FileTreeStore(base_dir=str(tmp_path))
        tree = AccessibilityTree(roots=(self._node("com.x:id/add"),))
        store.save_tree("sess1", tree)
        loaded = store.load_tree("sess1")
        assert loaded is not None
        assert loaded.tree.roots[0].resource_id == "com.x:id/add"

    def test_file_tree_store_empty_resource_id_round_trips_to_empty(
        self, tmp_path
    ) -> None:
        store = FileTreeStore(base_dir=str(tmp_path))
        tree = AccessibilityTree(roots=(self._node(""),))
        store.save_tree("sess2", tree)
        loaded = store.load_tree("sess2")
        assert loaded is not None
        assert loaded.tree.roots[0].resource_id == ""

    def test_raw_node_to_domain_reconstructs_resource_id(self) -> None:
        tree = raw_tree_to_domain(
            {
                "tree": [
                    {
                        "id": "n_1",
                        "role": "button",
                        "name": "Add",
                        "bounds": [0, 0, 10, 10],
                        "states": ["enabled"],
                        "actions": ["click"],
                        "resource_id": "com.x:id/add",
                    }
                ]
            }
        )
        assert tree.roots[0].resource_id == "com.x:id/add"

    def test_raw_node_to_domain_defaults_empty_when_key_absent(self) -> None:
        tree = raw_tree_to_domain(
            {
                "tree": [
                    {
                        "id": "n_1",
                        "role": "button",
                        "name": "Add",
                        "bounds": [0, 0, 10, 10],
                        "states": ["enabled"],
                        "actions": ["click"],
                    }
                ]
            }
        )
        assert tree.roots[0].resource_id == ""


# ═══════════════════════════════════════════════════════════════════════
# R4 / C2 — FoundNode surfacing: populated Node.resource_id reaches output
# ═══════════════════════════════════════════════════════════════════════


class TestFoundNodeSurfacingR4C2:
    def test_found_node_carries_source_node_resource_id(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        result = uc.execute("test-s", "button", name_pattern="Add")
        match = [n for n in result if n.id == "n_add"][0]
        # FoundNode.resource_id == source Node.resource_id (construction pass-through).
        assert match.resource_id == "com.x:id/add"

    def test_find_output_surfaces_resource_id_on_presenter(self) -> None:
        uc = _setup_find_uc(_rid_tree())
        result = uc.execute("test-s", "button", name_pattern="Add")
        rendered = format_find_nodes(result)
        parsed = json.loads(rendered)
        # Unscoped bare array; the matched node exposes its resource_id.
        surfaced = {
            n["id"]: n.get("resource_id") for n in parsed if isinstance(n, dict)
        }
        assert surfaced.get("n_add") == "com.x:id/add"
