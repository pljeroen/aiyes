"""AIYES-114: role-drift diagnostic in general find/wait — RED phase.

These tests pin the AIYES-114 behavior the implementation (A9) must deliver.
They FAIL before implementation because the new shared domain detector
(``find_role_drift`` / ``RoleDriftCandidate`` in ``aiyes.domain.tree``), the new
additive result fields (``FindResult.role_drift`` = ``()`` default,
``WaitResult.role_drift`` = ``None`` default), the widened presenter envelope,
the ``format_wait`` ``role_drift`` parameter, and the caller wiring do not exist
yet.

Traceability — FORMAL_CONSTRAINT_MAP (C1..C8) and VALIDATED_INTENT_PKG matrix:

  C5  ``find_role_drift`` returns exactly the name-matching, role!=requested
      candidates in input order; ``()`` when role=='*' or name_pattern falsy; a
      role-already-matching node is NEVER a candidate; ALL candidates (no
      first-only); state ignored.  (grounds R1+R2)
  C1  Exact-role + name_pattern find yielding ZERO matches surfaces
      ``role_drift`` = all same-name-different-role candidates in the
      (scope-respecting) pool, stable order; the returned node set is UNCHANGED.
  C2  The two "target never matched" wait timeouts (wait.py :145 transient
      never-seen, :148 normal) carry ``role_drift`` over the last poll's pre-role
      pool; every excluded branch keeps ``role_drift`` None + primary fields
      unchanged.
  C3  FC-FINDPURE-02 diagnostic-only purity: the detector NEVER changes what
      find/wait match or select and NEVER auto-selects; role=='*' / no
      name_pattern / no drift => role_drift empty and behavior identical.
  C4a WaitResult.role_drift defaults to None (NOT ()), so every no-drift scenario
      wait step emits byte-identical JSON (no new key) via the generic
      _to_jsonable path (which drops None but keeps ()).
  C4b format_find_nodes widens to ``scope_requested OR role_drift``:
      unscoped-no-drift stays a byte-identical bare array; scope-only stays the
      byte-identical AIYES-113 two-key envelope (role_drift key OMITTED when
      empty); role_drift non-empty yields an envelope with a role_drift key.
      format_wait / MCP / CLI thread role_drift identically.
  C7  find_role_drift + RoleDriftCandidate are pure domain; use-case __init__
      signatures unchanged; RoleDriftCandidate is a frozen value object.
  C8  Exactly one ``def find_role_drift`` (in tree.py); both find.py and wait.py
      route through it; RoleDriftCandidate defined once.
  R4  role_drift candidates are machine-readable structured {id, role, name}.

Each test asserts an OBSERVABLE post-state (the candidate SET/order, the
matching-left-unchanged, the byte-identical outputs, the None default, the wired
call), not merely a return value.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import aiyes.domain.use_cases.find as find_mod
import aiyes.domain.use_cases.wait as wait_mod
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.adapters.scenario_use_case_executor import _jsonable_dict
from aiyes.cli.presenter import format_find_nodes, format_wait
from aiyes.domain.matching import name_matches
from aiyes.domain.scenario import ScenarioStep
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.use_cases.find import AncestorRef, FindResult, FindUseCase, FoundNode
from aiyes.domain.use_cases.wait import WaitResult, WaitUseCase

from tests.conftest import (
    FakeAccessibilityTree,
    FakeClock,
    FakeSessionRepository,
    FakeTreeStore,
    make_domain_tree,
    make_node,
)

# The NEW AIYES-114 domain API is imported behind a guard so this module COLLECTS
# cleanly in the RED phase (before implementation). Each test references these
# symbols directly; while they are None the tests FAIL at call/assert time (never
# a collection error), which is the intended RED signal.
try:  # pragma: no cover - exercised by the RED run
    from aiyes.domain.tree import RoleDriftCandidate, find_role_drift

    _NEW_API_PRESENT = True
except ImportError:  # pragma: no cover - RED phase only
    RoleDriftCandidate = None  # type: ignore[assignment,misc]
    find_role_drift = None  # type: ignore[assignment]
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


def _setup_wait_uc(tree: AccessibilityTree, session_id: str = "test-s") -> WaitUseCase:
    repo = FakeSessionRepository()
    repo.save(_make_linux_session(session_id))
    return WaitUseCase(
        tree=FakeAccessibilityTree(tree),
        session_repo=repo,
        tree_store=FakeTreeStore(),
        clock=FakeClock(),
    )


def _dn(node_id: str, role: str, name: str, children: tuple = ()) -> Node:
    """Build a raw domain Node (no enrichment) for pure-detector tests."""
    return Node(
        id=node_id,
        role=role,
        name=name,
        bounds=(0, 0, 10, 10),
        states=("enabled",),
        actions=("click",),
        children=tuple(children),
    )


def _dual_role_tree() -> AccessibilityTree:
    """A Button and a View that share the name "Target Markets".

    Mirrors the Flutter friction example: a tappable exposed as role Button
    while a scenario hardcoded role View. Same name, different role.
    """
    return make_domain_tree(
        [
            make_node(
                "n_win",
                "frame",
                "Window",
                children=[
                    make_node("n_btn", "Button", "Target Markets"),
                    make_node("n_view", "View", "Target Markets"),
                ],
            )
        ]
    )


def _wait_drift_tree() -> AccessibilityTree:
    """A single Button "Target Markets" — the drift sibling for a View request."""
    return make_domain_tree(
        [
            make_node(
                "n_win",
                "frame",
                "Window",
                children=[make_node("n_btn", "Button", "Target Markets")],
            )
        ]
    )


def _scoped_drift_tree() -> AccessibilityTree:
    """Two "Add" buttons in sibling sections + an unrelated section.

    Lets a scoped find prove that role-drift respects the requested ancestor
    scope: the drift candidate inside the scope is surfaced, the identically
    named button in the sibling section is NOT.
    """
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
                        children=[make_node("n_add_rec", "Button", "Add")],
                    ),
                    make_node(
                        "n_sec_hol",
                        "section",
                        "Holidays",
                        children=[make_node("n_add_hol", "Button", "Add")],
                    ),
                    make_node(
                        "n_sec_empty",
                        "section",
                        "Empty Section",
                        children=[make_node("n_se", "View", "Something Else")],
                    ),
                ],
            )
        ]
    )


def _ids(nodes: Any) -> list:
    return [n.id for n in nodes]


def _triples(candidates: Any) -> list:
    return [(c.id, c.role, c.name) for c in candidates]


# The exact byte string the CURRENT presenter emits for the two FoundNodes built
# below (captured from format_find_nodes before AIYES-114; json.dumps(masked,
# indent=2), no trailing newline). The unscoped-AND-no-drift path MUST remain
# byte-identical to this (C4b).
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


def _two_found_nodes() -> tuple:
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


# ═══════════════════════════════════════════════════════════════════════
# C5 — shared detector correctness (tree.find_role_drift)
# ═══════════════════════════════════════════════════════════════════════


class TestFindRoleDriftDetectorC5:
    def _flat(self) -> List[Node]:
        # b1(Button)  v1(View, role matches "View")  l1(Link)  b2(Button)
        # o1(Button, name does not match)
        return [
            _dn("b1", "Button", "Target Markets"),
            _dn("v1", "View", "Target Markets"),
            _dn("l1", "Link", "Target Markets"),
            _dn("b2", "Button", "Target Markets"),
            _dn("o1", "Button", "Other Widget"),
        ]

    def test_wildcard_role_returns_empty(self) -> None:
        assert find_role_drift(self._flat(), "*", "Target Markets") == ()

    def test_none_name_pattern_returns_empty(self) -> None:
        assert find_role_drift(self._flat(), "View", None) == ()

    def test_empty_name_pattern_returns_empty(self) -> None:
        assert find_role_drift(self._flat(), "View", "") == ()

    def test_no_name_matching_node_returns_empty(self) -> None:
        assert find_role_drift(self._flat(), "View", "Nonexistent Name") == ()

    def test_single_candidate_carries_actual_role(self) -> None:
        flat = [
            _dn("b1", "Button", "Target Markets"),
            _dn("v1", "View", "Other Widget"),
        ]
        drift = find_role_drift(flat, "View", "Target Markets")
        assert _triples(drift) == [("b1", "Button", "Target Markets")]

    def test_all_candidates_stable_order_no_first_only(self) -> None:
        # b1, l1, b2 all name-match under a role != "View"; v1 (role View) and
        # o1 (name mismatch) are excluded. Order == input/pre-order order.
        drift = find_role_drift(self._flat(), "View", "Target Markets")
        assert _triples(drift) == [
            ("b1", "Button", "Target Markets"),
            ("l1", "Link", "Target Markets"),
            ("b2", "Button", "Target Markets"),
        ]

    def test_role_already_matching_node_never_a_candidate(self) -> None:
        # v1 name-matches AND role already == requested "View" -> NEVER included.
        # This is the state-only-miss false-positive guard.
        drift = find_role_drift(self._flat(), "View", "Target Markets")
        assert "v1" not in [c.id for c in drift]
        assert all(c.role != "View" for c in drift)

    def test_state_is_ignored_by_detector(self) -> None:
        # The detector takes (nodes, role, name_pattern) only — no state axis.
        assert "state" not in inspect.signature(find_role_drift).parameters

    def test_result_entries_are_role_drift_candidates(self) -> None:
        drift = find_role_drift(self._flat(), "View", "Target Markets")
        assert isinstance(drift, tuple)
        assert all(isinstance(c, RoleDriftCandidate) for c in drift)

    @settings(deadline=None, max_examples=60)
    @given(
        specs=st.lists(
            st.tuples(
                st.text(alphabet="AB", min_size=1, max_size=2),  # role
                st.text(alphabet="xy ", min_size=0, max_size=4),  # name
            ),
            min_size=0,
            max_size=6,
        ),
        role=st.text(alphabet="AB", min_size=1, max_size=2),
        pattern=st.text(alphabet="xy", min_size=1, max_size=2),
    )
    def test_detector_matches_reference_predicate(
        self, specs: list, role: str, pattern: str
    ) -> None:
        """Differential property: the detector equals the reference predicate
        (name_matches AND role != requested), in input order, for all inputs."""
        nodes = [_dn(f"id{i}", r, n) for i, (r, n) in enumerate(specs)]
        expected = tuple(
            RoleDriftCandidate(id=f"id{i}", role=r, name=n)
            for i, (r, n) in enumerate(specs)
            if name_matches(n, pattern) and r != role
        )
        assert find_role_drift(nodes, role, pattern) == expected


# ═══════════════════════════════════════════════════════════════════════
# C1 — find surfaces role_drift on the zero-match path (matching unchanged)
# ═══════════════════════════════════════════════════════════════════════


class TestFindDriftDiagnosticC1:
    def test_zero_match_surfaces_all_candidates_and_matches_empty(self) -> None:
        uc = _setup_find_uc(_dual_role_tree())
        # role "Foobar" matches nothing; name "Target Markets" matches under
        # Button AND View — both drift candidates in document order.
        result = uc.execute("test-s", "Foobar", name_pattern="Target Markets")
        # Matching is UNCHANGED — the zero-match set stays empty.
        assert list(result) == []
        # The diagnostic carries every same-name-different-role candidate.
        assert _triples(result.role_drift) == [
            ("n_btn", "Button", "Target Markets"),
            ("n_view", "View", "Target Markets"),
        ]

    def test_same_role_match_leaves_role_drift_empty(self) -> None:
        uc = _setup_find_uc(_dual_role_tree())
        result = uc.execute("test-s", "Button", name_pattern="Target Markets")
        assert _ids(result) == ["n_btn"]  # normal match
        assert tuple(result.role_drift) == ()

    def test_non_zero_match_empty_even_with_drift_sibling(self) -> None:
        # The Button matches (non-zero), so the diagnostic does NOT fire even
        # though a same-name View sibling exists (fires on OVERALL zero only).
        uc = _setup_find_uc(_dual_role_tree())
        result = uc.execute("test-s", "Button", name_pattern="Target Markets")
        assert list(result)  # non-empty
        assert tuple(result.role_drift) == ()

    def test_zero_match_no_drift_leaves_role_drift_empty(self) -> None:
        uc = _setup_find_uc(_dual_role_tree())
        result = uc.execute("test-s", "View", name_pattern="Nonexistent")
        assert list(result) == []
        assert tuple(result.role_drift) == ()

    def test_scoped_drift_inside_scope_is_surfaced_from_scoped_pool(self) -> None:
        uc = _setup_find_uc(_scoped_drift_tree())
        result = uc.execute(
            "test-s",
            "View",
            name_pattern="Add",
            within_name="Recurring holidays",
        )
        assert list(result) == []
        assert result.scope_matched is True
        # Only the in-scope drift button; the sibling section's Add is NOT here.
        assert _triples(result.role_drift) == [("n_add_rec", "Button", "Add")]
        assert "n_add_hol" not in [c.id for c in result.role_drift]

    def test_scoped_drift_only_outside_scope_yields_empty(self) -> None:
        uc = _setup_find_uc(_scoped_drift_tree())
        result = uc.execute(
            "test-s",
            "View",
            name_pattern="Add",
            within_name="Empty Section",
        )
        assert list(result) == []
        assert result.scope_matched is True
        # The "Add" drift buttons live OUTSIDE the scoped subtree, so no
        # whole-tree bypass: role_drift is empty.
        assert tuple(result.role_drift) == ()


# ═══════════════════════════════════════════════════════════════════════
# C2 — wait surfaces role_drift on the two "never matched" timeouts only
# ═══════════════════════════════════════════════════════════════════════


class TestWaitDriftDiagnosticC2:
    def test_normal_mode_timeout_with_drift_is_populated(self) -> None:
        uc = _setup_wait_uc(_wait_drift_tree())
        result = uc.execute(
            "test-s", "View", name_pattern="Target Markets", timeout=0.0
        )
        assert result.found is False
        assert result.timeout is True
        assert result.role_drift is not None
        assert _triples(result.role_drift) == [("n_btn", "Button", "Target Markets")]

    def test_transient_never_seen_timeout_with_drift_is_populated(self) -> None:
        uc = _setup_wait_uc(_wait_drift_tree())
        result = uc.execute(
            "test-s",
            "View",
            name_pattern="Target Markets",
            timeout=0.0,
            transient=True,
        )
        assert result.found is False
        assert result.timeout is True
        assert _triples(result.role_drift) == [("n_btn", "Button", "Target Markets")]

    def test_absent_mode_timeout_stays_none_even_with_drift_sibling(self) -> None:
        # Button is present -> absent-mode times out (found=True); the query's
        # exact role matched, so role_drift stays None despite the View sibling.
        uc = _setup_wait_uc(_dual_role_tree())
        result = uc.execute(
            "test-s",
            "Button",
            name_pattern="Target Markets",
            timeout=0.0,
            absent=True,
        )
        assert result.found is True
        assert result.timeout is True
        assert result.role_drift is None

    def test_transient_present_at_timeout_stays_none(self) -> None:
        uc = _setup_wait_uc(_dual_role_tree())
        result = uc.execute(
            "test-s",
            "Button",
            name_pattern="Target Markets",
            timeout=0.0,
            transient=True,
        )
        assert result.found is True
        assert result.role_drift is None

    def test_normal_success_stays_none(self) -> None:
        uc = _setup_wait_uc(_wait_drift_tree())
        result = uc.execute(
            "test-s", "Button", name_pattern="Target Markets", timeout=5.0
        )
        assert result.found is True
        assert result.timeout is False
        assert result.role_drift is None


# ═══════════════════════════════════════════════════════════════════════
# C3 — FC-FINDPURE-02: diagnostic-only purity (differential)
# ═══════════════════════════════════════════════════════════════════════


class TestDiagnosticOnlyPurityC3:
    def test_find_stub_detector_does_not_change_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patch the shared detector find.py routes through to return a fixed
        # NON-EMPTY sentinel; the zero-match result MUST stay empty (no
        # auto-select / leak), and role_drift MUST carry exactly the sentinel.
        monkeypatch.setattr(
            find_mod, "find_role_drift", lambda *a, **k: ("SENTINEL",), raising=False
        )
        uc = _setup_find_uc(_dual_role_tree())
        result = uc.execute("test-s", "Foobar", name_pattern="Target Markets")
        assert list(result) == []  # matching UNCHANGED by a non-empty detector
        assert result.role_drift == ("SENTINEL",)  # detector wired, additive only

    def test_wait_stub_detector_does_not_change_primary_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            wait_mod, "find_role_drift", lambda *a, **k: ("SENTINEL",), raising=False
        )
        uc = _setup_wait_uc(_wait_drift_tree())
        result = uc.execute(
            "test-s", "View", name_pattern="Target Markets", timeout=0.0
        )
        assert result.found is False
        assert result.timeout is True
        assert result.transient is False
        assert result.role_drift == ("SENTINEL",)

    def test_find_wildcard_role_is_empty_and_behavior_identical(self) -> None:
        uc = _setup_find_uc(_dual_role_tree())
        result = uc.execute("test-s", "*", name_pattern="Nonexistent")
        assert list(result) == []
        assert tuple(result.role_drift) == ()

    def test_find_no_name_pattern_is_empty_and_behavior_identical(self) -> None:
        uc = _setup_find_uc(_dual_role_tree())
        result = uc.execute("test-s", "Nonrole", name_pattern=None)
        assert list(result) == []
        assert tuple(result.role_drift) == ()

    def test_wait_wildcard_role_stays_none(self) -> None:
        uc = _setup_wait_uc(_wait_drift_tree())
        result = uc.execute("test-s", "*", name_pattern="Nonexistent", timeout=0.0)
        assert result.found is False
        assert result.timeout is True
        assert result.role_drift is None

    def test_wait_no_name_pattern_stays_none(self) -> None:
        uc = _setup_wait_uc(_wait_drift_tree())
        result = uc.execute("test-s", "Nonrole", name_pattern=None, timeout=0.0)
        assert result.found is False
        assert result.timeout is True
        assert result.role_drift is None


# ═══════════════════════════════════════════════════════════════════════
# C4a — WaitResult None default is load-bearing (scenario byte-identity)
# ═══════════════════════════════════════════════════════════════════════


class _SpyWaitUseCase:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list = []

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self._result


def _build_started_executor(**use_cases: Any) -> Any:
    from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor

    start = SimpleNamespace(
        execute=lambda **kw: SimpleNamespace(session_id="s1", backend="linux")
    )
    repo = FakeSessionRepository()
    repo.save(_make_linux_session("s1"))
    defaults: dict[str, Any] = {
        "session_start": start,
        "inspect": SimpleNamespace(execute=lambda **kw: SimpleNamespace(tree=None)),
        "find": SimpleNamespace(execute=lambda **kw: FindResult(nodes=())),
        "action": SimpleNamespace(execute=lambda **kw: SimpleNamespace(status="ok")),
        "type_text": SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        "screenshot": SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        "session_stop": SimpleNamespace(execute=lambda **kw: SimpleNamespace()),
        "session_repo": repo,
        "clock": SimpleNamespace(now=lambda: 0.0),
    }
    defaults.update(use_cases)
    executor = ScenarioUseCaseExecutor(**defaults)
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "app", "wait_seconds": 0.0},
        )
    )
    return executor


class TestWaitResultNoneDefaultC4a:
    def test_waitresult_role_drift_defaults_to_none(self) -> None:
        fields = WaitResult.__dataclass_fields__
        assert "role_drift" in fields
        assert fields["role_drift"].default is None

    def test_none_default_dropped_but_empty_tuple_would_leak(self) -> None:
        """Proves the None default is load-bearing: the generic scenario
        serializer (_to_jsonable) drops a None value but KEEPS a ()-value as
        [] — so a ()-default WaitResult.role_drift would add a spurious key to
        every no-drift wait step."""

        @dataclasses.dataclass(frozen=True)
        class _WithNone:
            found: bool
            role_drift: object = None

        @dataclasses.dataclass(frozen=True)
        class _WithEmptyTuple:
            found: bool
            role_drift: tuple = ()

        none_out = _jsonable_dict(_WithNone(found=True))
        tuple_out = _jsonable_dict(_WithEmptyTuple(found=True))
        assert "role_drift" not in none_out  # None -> dropped (byte-identical)
        assert tuple_out.get("role_drift") == []  # () -> spurious "[]" key

    def test_scenario_wait_step_no_drift_omits_role_drift_key(self) -> None:
        spy = _SpyWaitUseCase(WaitResult(found=True, timeout=False, id="n1"))
        executor = _build_started_executor(wait=spy)
        result = executor.execute(
            ScenarioStep(
                id="w",
                kind="wait",
                parameters={"role": "Button", "name_pattern": "X"},
            )
        )
        # C4a: a no-drift wait step's output is byte-identical to today —
        # NO role_drift key (the None default is dropped by _to_jsonable).
        assert "role_drift" not in result.output

    def test_scenario_wait_step_drift_surfaces_role_drift_key(self) -> None:
        spy = _SpyWaitUseCase(
            WaitResult(
                found=False,
                timeout=True,
                role_drift=(RoleDriftCandidate(id="n_btn", role="Button", name="Add"),),
            )
        )
        executor = _build_started_executor(wait=spy)
        result = executor.execute(
            ScenarioStep(
                id="w",
                kind="wait",
                parameters={"role": "View", "name_pattern": "Add"},
            )
        )
        assert result.output.get("role_drift") == [
            {"id": "n_btn", "role": "Button", "name": "Add"}
        ]


# ═══════════════════════════════════════════════════════════════════════
# C4b — presenter three-way envelope + format_wait + MCP/CLI parity
# ═══════════════════════════════════════════════════════════════════════


class TestPresenterThreeWayEnvelopeC4b:
    def test_unscoped_no_drift_is_byte_identical_bare_array(self) -> None:
        result = FindResult(nodes=_two_found_nodes())
        assert format_find_nodes(result) == _GOLDEN_UNSCOPED_BARE_ARRAY

    def test_scope_only_no_drift_envelope_omits_role_drift_key(self) -> None:
        result = FindResult(
            nodes=(),
            scope_requested=True,
            scope_matched=True,
            matched_ancestors=(
                AncestorRef(id="n_sec", role="section", name="Recurring holidays"),
            ),
        )
        rendered = format_find_nodes(result)
        parsed = json.loads(rendered)
        # AIYES-113 two-key envelope preserved byte-shape: no role_drift key.
        assert set(parsed.keys()) == {"nodes", "scope_matched", "matched_ancestors"}
        assert "role_drift" not in rendered

    def test_unscoped_with_drift_widens_to_envelope_no_scope_keys(self) -> None:
        result = FindResult(
            nodes=(),
            role_drift=(
                RoleDriftCandidate(id="n_btn", role="Button", name="Target Markets"),
            ),
        )
        parsed = json.loads(format_find_nodes(result))
        assert isinstance(parsed, dict)
        assert parsed["nodes"] == []
        assert parsed["role_drift"] == [
            {"id": "n_btn", "role": "Button", "name": "Target Markets"}
        ]
        # unscoped -> NO scope keys.
        assert "scope_matched" not in parsed
        assert "matched_ancestors" not in parsed

    def test_scoped_with_drift_envelope_has_all_three_keys(self) -> None:
        result = FindResult(
            nodes=(),
            scope_requested=True,
            scope_matched=True,
            matched_ancestors=(
                AncestorRef(id="n_sec", role="section", name="Recurring holidays"),
            ),
            role_drift=(RoleDriftCandidate(id="n_btn", role="Button", name="Add"),),
        )
        parsed = json.loads(format_find_nodes(result))
        assert set(parsed.keys()) == {
            "nodes",
            "scope_matched",
            "matched_ancestors",
            "role_drift",
        }
        assert parsed["role_drift"] == [
            {"id": "n_btn", "role": "Button", "name": "Add"}
        ]


class TestFormatWaitRoleDriftC4b:
    def test_timeout_with_drift_emits_role_drift_key(self) -> None:
        out = format_wait(
            found=False,
            timeout=True,
            role_drift=(
                RoleDriftCandidate(id="n_btn", role="Button", name="Target Markets"),
            ),
        )
        parsed = json.loads(out)
        assert parsed["role_drift"] == [
            {"id": "n_btn", "role": "Button", "name": "Target Markets"}
        ]

    def test_empty_role_drift_omits_the_key(self) -> None:
        out = format_wait(found=True, timeout=False, role_drift=())
        assert "role_drift" not in json.loads(out)


class TestWaitCallerParityC4b:
    def test_cli_wait_threads_role_drift(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from click.testing import CliRunner

        import aiyes.cli.main as cli_main

        fake_result = SimpleNamespace(
            found=False,
            timeout=True,
            id=None,
            transient=False,
            role_drift=(
                RoleDriftCandidate(id="n_btn", role="Button", name="Target Markets"),
            ),
        )
        spy = _SpyWaitUseCase(fake_result)
        monkeypatch.setattr(cli_main, "wait_uc", spy)
        monkeypatch.setattr(cli_main, "resolve_session_id", lambda s: s or "s1")

        runner = CliRunner()
        cli_result = runner.invoke(
            cli_main.cli,
            ["wait", "View", "Target Markets", "--timeout", "0"],
        )
        assert cli_result.exit_code == 0, cli_result.output
        parsed = json.loads(cli_result.output)
        assert parsed["role_drift"] == [
            {"id": "n_btn", "role": "Button", "name": "Target Markets"}
        ]

    @pytest.mark.asyncio
    async def test_mcp_wait_threads_role_drift(self) -> None:
        fake_result = SimpleNamespace(
            found=False,
            timeout=True,
            id=None,
            transient=False,
            role_drift=(
                RoleDriftCandidate(id="n_btn", role="Button", name="Target Markets"),
            ),
        )
        mock_wait = MagicMock()
        mock_wait.execute.return_value = fake_result
        mock_resolve = MagicMock(return_value="test-session")
        mock_clock = MagicMock()
        mock_clock.now.return_value = 1000.0

        fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
        fields.update(
            wait_uc=mock_wait,
            resolve_session_id=mock_resolve,
            clock=mock_clock,
            operation_log=MagicMock(),
        )
        server = create_mcp_server(ServerDependencies(**fields))

        mcp_result = await server.call_tool(
            "wait",
            {
                "session_id": "test-session",
                "role": "View",
                "name_pattern": "Target Markets",
                "timeout": 0.0,
            },
        )
        assert mcp_result.isError is False
        parsed = json.loads(mcp_result.content[0].text)
        assert parsed["role_drift"] == [
            {"id": "n_btn", "role": "Button", "name": "Target Markets"}
        ]


# ═══════════════════════════════════════════════════════════════════════
# C8 — one shared detector, routed by BOTH find and wait
# ═══════════════════════════════════════════════════════════════════════


class TestSharedDetectorC8:
    def _src(self, module: Any) -> str:
        return Path(module.__file__).read_text()

    def test_detector_defined_only_in_tree(self) -> None:
        import aiyes.domain.tree as tree_mod

        tree_src = self._src(tree_mod)
        find_src = self._src(find_mod)
        wait_src = self._src(wait_mod)
        assert "def find_role_drift" in tree_src
        assert "def find_role_drift" not in find_src
        assert "def find_role_drift" not in wait_src

    def test_both_use_cases_import_the_shared_detector(self) -> None:
        find_src = self._src(find_mod)
        wait_src = self._src(wait_mod)
        assert "find_role_drift" in find_src
        assert "find_role_drift" in wait_src
        assert "aiyes.domain.tree" in find_src
        assert "aiyes.domain.tree" in wait_src

    def test_role_drift_candidate_defined_once(self) -> None:
        src_root = Path(find_mod.__file__).resolve().parents[2]  # .../src/aiyes
        definitions = []
        for path in src_root.rglob("*.py"):
            if "class RoleDriftCandidate" in path.read_text():
                definitions.append(str(path))
        assert len(definitions) == 1, definitions
        assert definitions[0].endswith("domain/tree.py")

    def test_find_and_wait_both_route_through_detector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"find": 0, "wait": 0}

        def _find_spy(*a: Any, **k: Any) -> tuple:
            calls["find"] += 1
            return ()

        def _wait_spy(*a: Any, **k: Any) -> tuple:
            calls["wait"] += 1
            return ()

        monkeypatch.setattr(find_mod, "find_role_drift", _find_spy, raising=False)
        monkeypatch.setattr(wait_mod, "find_role_drift", _wait_spy, raising=False)

        _setup_find_uc(_dual_role_tree()).execute(
            "test-s", "Foobar", name_pattern="Target Markets"
        )
        _setup_wait_uc(_wait_drift_tree()).execute(
            "test-s", "View", name_pattern="Target Markets", timeout=0.0
        )
        assert calls["find"] >= 1
        assert calls["wait"] >= 1


# ═══════════════════════════════════════════════════════════════════════
# C7 — domain purity: additive field, unchanged constructors, frozen VO
# ═══════════════════════════════════════════════════════════════════════


class TestDomainPurityC7:
    def test_find_use_case_init_signature_unchanged(self) -> None:
        params = list(inspect.signature(FindUseCase.__init__).parameters)
        assert params == ["self", "tree", "session_repo", "tree_store"]

    def test_wait_use_case_init_signature_unchanged(self) -> None:
        params = list(inspect.signature(WaitUseCase.__init__).parameters)
        assert params == ["self", "tree", "session_repo", "tree_store", "clock"]

    def test_domain_files_import_no_adapters(self) -> None:
        import aiyes.domain.tree as tree_mod

        for module in (tree_mod, find_mod, wait_mod):
            src = Path(module.__file__).read_text()
            assert "aiyes.adapters" not in src

    def test_role_drift_candidate_is_frozen_value_object(self) -> None:
        assert dataclasses.is_dataclass(RoleDriftCandidate)
        candidate = RoleDriftCandidate(id="n1", role="Button", name="Add")
        with pytest.raises(dataclasses.FrozenInstanceError):
            candidate.id = "n2"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# R4 — machine-readable structured candidate {id, role, name}
# ═══════════════════════════════════════════════════════════════════════


class TestMachineReadableR4:
    def test_candidate_fields_are_id_role_name(self) -> None:
        field_names = [f.name for f in dataclasses.fields(RoleDriftCandidate)]
        assert field_names == ["id", "role", "name"]

    def test_candidate_serializes_to_id_role_name_dict(self) -> None:
        candidate = RoleDriftCandidate(id="n_btn", role="Button", name="Target Markets")
        assert dataclasses.asdict(candidate) == {
            "id": "n_btn",
            "role": "Button",
            "name": "Target Markets",
        }
