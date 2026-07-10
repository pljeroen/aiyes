"""AIYES-112 — goto(url) + reload(hard) browser navigation primitives.

RED tests (A8 test_author) for two NEW domain use cases:

  * GotoUseCase   -> src/aiyes/domain/use_cases/goto.py   (does not exist yet)
  * ReloadUseCase -> src/aiyes/domain/use_cases/reload.py (does not exist yet)

The use cases are authored per the VALIDATED_INTENT_PKG bound design:

  goto(url) on a linux session:
    load session -> (backend != "linux" -> status="error", ZERO port calls)
    -> get_tree -> locate FIRST node with role=="entry" AND
       name_matches(name, "address") -> do_action(node.id, "activate")
    -> CHECK ActionPortResult.success  (C4 focus-before-type guard)
    -> ONLY IF focused: key(["ctrl+a"]) -> type_text(url) -> key(["Return"])
    -> GotoResult(status="ok", session_id, action="goto", url=url)

  reload on a linux session:
    load session -> (backend != "linux" -> status="error", ZERO port calls)
    -> key(["ctrl+shift+r"])
    -> ReloadResult(status="ok", session_id, action="reload")

Coverage (VALIDATED_INTENT_PKG.must_tier1_coverage_matrix + tier-2 R4):

  R1 / C1 : goto happy-path ordered port trace + status="ok" + url populated
  R2 / C2 : reload single ["ctrl+shift+r"] keystroke + status="ok"
  R3 / C3 : non-linux backend -> status="error", ZERO tree/action/input calls
  C4 (F1) : address bar not found -> status="error", NO key/type_text emitted
  C4 (F2) : activate returns success=False -> status="error", NO key/type_text
  R4 / C5 : structured, readable result {status, session_id, action} (+ url/reason)
  boundary: session-not-found RAISES RuntimeError (system-error, like siblings)

RED mechanism: import-driven (function-local imports of the absent modules) so
this file still COLLECTS cleanly; each test additionally asserts REAL observable
behavior once GotoUseCase/ReloadUseCase exist. No src/ code is written here.

gui_runtime = UNIT_MOCKED_ONLY_WITH_SURFACED_LIVE_GAP: the domain orchestration
is fully covered here with mocked/spy ports (deterministic ordered-trace and
zero-side-effect assertions); the live Firefox end-to-end confirmation is a
SURFACED advisory item (no live-browser harness exists in-repo), NOT fabricated.
"""

from __future__ import annotations

import dataclasses
from typing import Any, List, Optional, Tuple

import pytest

from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.types import ActionPortResult

from tests.conftest import (
    FakeSessionRepository,
    make_domain_tree,
    make_node,
)


# ═══════════════════════════════════════════════════════════════════════
# Session factories (mirror tests/test_aiyes25_group_c.py house style)
# ═══════════════════════════════════════════════════════════════════════


def _make_linux_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="test-s",
        display=":99",
        app_pid=100,
        app_command="firefox",
        app_args=(),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
        started_at=1000.0,
        backend="linux",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_android_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="android-test",
        app_pid=200,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )
    defaults.update(overrides)
    return Session(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# Shared-ledger recording spies
#
# All three ports append to ONE ordered ``events`` list so the tests can
# assert the TRUE cross-port operation sequence (C1 falsifier: any
# missing / extra / re-ordered call). ``events`` records ONLY the four
# side-effecting port operations goto/reload may perform:
#   ("get_tree", None)
#   ("do_action", node_id, action_name)
#   ("key", (key_specs...))
#   ("type_text", text)
# session_repo.load is intentionally NOT recorded here — it is a lookup,
# not a side-effecting tree/action/input call, so an empty ``events`` list
# is exactly the C3 "zero side-effecting port calls" postcondition.
# ═══════════════════════════════════════════════════════════════════════


class _RecordingTree:
    """AccessibilityTreePort spy — records get_tree; returns a fixed tree."""

    def __init__(
        self,
        tree: AccessibilityTree,
        events: List[Tuple[Any, ...]],
        last_registry: Optional[Any] = None,
    ) -> None:
        self._tree = tree
        self._events = events
        # goto may source the registry via getattr(tree_port, "last_registry", None)
        self.last_registry = last_registry

    def get_tree(self, session: Any) -> AccessibilityTree:
        self._events.append(("get_tree", None))
        return self._tree


class _RecordingAction:
    """AccessibilityActionPort spy — do_action returns a configurable result."""

    def __init__(
        self,
        events: List[Tuple[Any, ...]],
        success: bool = True,
        available_actions: Tuple[str, ...] = (),
    ) -> None:
        self._events = events
        self._success = success
        self._available = tuple(available_actions)

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        self._events.append(("do_action", node_id, action_name))
        return ActionPortResult(
            success=self._success, available_actions=self._available
        )


class _RecordingInput:
    """InputPort spy — records key / type_text into the shared ledger."""

    def __init__(self, events: List[Tuple[Any, ...]]) -> None:
        self._events = events

    def key(self, session: Any, key_specs: List[str]) -> None:
        self._events.append(("key", tuple(key_specs)))

    def type_text(self, session: Any, text: str, delay_ms: int = 0) -> None:
        self._events.append(("type_text", text))


# ═══════════════════════════════════════════════════════════════════════
# Tree factory — a browser window whose address bar is a role=entry node
# whose accessible name contains "address" (the Firefox AT-SPI convention),
# surrounded by DECOYS that must NOT be selected:
#   * a role=entry named "Find in page" (right role, wrong name)
#   * a role=label named "Address:"     (right name, wrong role)
# so the (role=="entry" AND name~"address") filter is genuinely exercised.
# ═══════════════════════════════════════════════════════════════════════


_ADDR_NODE_ID = "n_addr"


def _browser_tree(include_address: bool = True) -> AccessibilityTree:
    children: List[Any] = [
        make_node(
            "n_reload_btn", "push_button", "Reload current page", actions=["click"]
        ),
        # decoy: entry with the WRONG name (no "address")
        make_node("n_find", "entry", "Find in page", actions=["activate", "click"]),
        # decoy: right name but WRONG role (label, not entry)
        make_node("n_addr_label", "label", "Address:", actions=[]),
    ]
    if include_address:
        children.append(
            make_node(
                _ADDR_NODE_ID,
                "entry",
                "Address and search bar",
                actions=["activate", "click"],
            )
        )
    raw = [
        make_node("n_root", "frame", "Mozilla Firefox", children=children),
    ]
    return make_domain_tree(raw)


# ═══════════════════════════════════════════════════════════════════════
# R1 / C1 — goto happy path (linux): ordered port-call sequence
# ═══════════════════════════════════════════════════════════════════════


class TestGotoHappyPath:
    def test_goto_linux_emits_exact_ordered_address_bar_sequence(self) -> None:
        """R1/C1: locate role=entry name~address -> activate -> ctrl+a ->
        type_text(url) -> Return, in exactly that order and nothing else."""
        from aiyes.domain.use_cases.goto import GotoUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_linux_session())
        tree_port = _RecordingTree(_browser_tree(include_address=True), events)
        action_port = _RecordingAction(events, success=True)
        input_port = _RecordingInput(events)

        uc = GotoUseCase(
            tree_port=tree_port,
            action_port=action_port,
            input_port=input_port,
            session_repo=repo,
        )
        url = "https://example.test/path?q=1"
        result = uc.execute(session_id="test-s", url=url)

        # C1 falsifiable ordered trace — activate targets the address entry
        # (n_addr), NOT the decoy entry/label; type_text payload == url.
        assert events == [
            ("get_tree", None),
            ("do_action", _ADDR_NODE_ID, "activate"),
            ("key", ("ctrl+a",)),
            ("type_text", url),
            ("key", ("Return",)),
        ]
        assert result.status == "ok"
        assert result.action == "goto"
        assert result.url == url
        assert result.session_id == "test-s"


# ═══════════════════════════════════════════════════════════════════════
# R2 / C2 — reload happy path (linux): exactly one ctrl+shift+r
# ═══════════════════════════════════════════════════════════════════════


class TestReloadHappyPath:
    def test_reload_linux_sends_exactly_ctrl_shift_r(self) -> None:
        """R2/C2: reload emits exactly one key(["ctrl+shift+r"]) — no other
        input, no tree/action call — and returns status="ok"."""
        from aiyes.domain.use_cases.reload import ReloadUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_linux_session())
        input_port = _RecordingInput(events)

        uc = ReloadUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="test-s")

        assert events == [("key", ("ctrl+shift+r",))]
        assert result.status == "ok"
        assert result.action == "reload"
        assert result.session_id == "test-s"


# ═══════════════════════════════════════════════════════════════════════
# R3 / C3 — non-linux backend -> structured error, ZERO side-effecting calls
# ═══════════════════════════════════════════════════════════════════════


class TestNonLinuxBackendScoping:
    def test_goto_android_returns_error_and_emits_zero_port_calls(self) -> None:
        """R3/C3: goto on a non-linux session returns status="error" (NOT ok,
        NOT a raise, NOT a silent no-op) with a reason, and reaches NO
        tree/action/input port (zero misdirected keystrokes)."""
        from aiyes.domain.use_cases.goto import GotoUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_android_session())
        tree_port = _RecordingTree(_browser_tree(include_address=True), events)
        action_port = _RecordingAction(events, success=True)
        input_port = _RecordingInput(events)

        uc = GotoUseCase(
            tree_port=tree_port,
            action_port=action_port,
            input_port=input_port,
            session_repo=repo,
        )
        result = uc.execute(session_id="android-test", url="https://example.test")

        assert result.status == "error"
        assert result.status != "ok"
        assert result.action == "goto"
        assert result.session_id == "android-test"
        assert result.reason  # non-empty machine-readable reason
        # C3 falsifier: NOT a single get_tree/do_action/key/type_text call.
        assert events == []

    def test_reload_android_returns_error_and_emits_zero_input_calls(self) -> None:
        """R3/C3: reload on a non-linux session returns status="error" with a
        reason and emits ZERO key() calls."""
        from aiyes.domain.use_cases.reload import ReloadUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_android_session())
        input_port = _RecordingInput(events)

        uc = ReloadUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="android-test")

        assert result.status == "error"
        assert result.status != "ok"
        assert result.action == "reload"
        assert result.session_id == "android-test"
        assert result.reason
        assert events == []


# ═══════════════════════════════════════════════════════════════════════
# C4 — focus-before-type guard (misdirected-keystroke prohibition)
# ═══════════════════════════════════════════════════════════════════════


class TestGotoFocusSafetyGuard:
    def test_goto_address_bar_not_found_sends_no_keystroke(self) -> None:
        """C4 (F1): when NO role=entry/name~address node exists, goto returns
        status="error" and emits ZERO key/type_text — never types a URL into
        whatever control happens to hold focus."""
        from aiyes.domain.use_cases.goto import GotoUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_linux_session())
        # Tree WITHOUT an address entry (decoys only).
        tree_port = _RecordingTree(_browser_tree(include_address=False), events)
        action_port = _RecordingAction(events, success=True)
        input_port = _RecordingInput(events)

        uc = GotoUseCase(
            tree_port=tree_port,
            action_port=action_port,
            input_port=input_port,
            session_repo=repo,
        )
        result = uc.execute(session_id="test-s", url="https://example.test")

        assert result.status == "error"
        assert result.reason  # names the not-found failure mode
        # Core C4 falsifier: no key and no type_text after a not-found locate.
        emitted = [e for e in events if e[0] in ("key", "type_text")]
        assert emitted == []
        # Nothing to activate, so no do_action either.
        assert [e for e in events if e[0] == "do_action"] == []

    def test_goto_activate_failure_sends_no_keystroke(self) -> None:
        """C4 (F2): when the address entry is located but do_action("activate")
        returns ActionPortResult.success=False, goto returns status="error" and
        emits ZERO key/type_text — never types into an unfocused control."""
        from aiyes.domain.use_cases.goto import GotoUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_linux_session())
        tree_port = _RecordingTree(_browser_tree(include_address=True), events)
        # activate FAILS.
        action_port = _RecordingAction(
            events, success=False, available_actions=("click",)
        )
        input_port = _RecordingInput(events)

        uc = GotoUseCase(
            tree_port=tree_port,
            action_port=action_port,
            input_port=input_port,
            session_repo=repo,
        )
        result = uc.execute(session_id="test-s", url="https://example.test")

        assert result.status == "error"
        assert result.reason  # names the focus failure
        # activate WAS attempted on the located address entry ...
        assert ("do_action", _ADDR_NODE_ID, "activate") in events
        # ... but NO type/submit followed the failed focus (C4 falsifier).
        emitted = [e for e in events if e[0] in ("key", "type_text")]
        assert emitted == []


# ═══════════════════════════════════════════════════════════════════════
# R4 / C5 — structured, readable, observable result shape
# ═══════════════════════════════════════════════════════════════════════


class TestObservabilityResultShape:
    def test_goto_result_carries_mandatory_readable_fields(self) -> None:
        """R4/C5: a goto result exposes {status, session_id, action} the caller
        can read; url is populated on the ok path; reason stays None on ok."""
        from aiyes.domain.use_cases.goto import GotoUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_linux_session())
        tree_port = _RecordingTree(_browser_tree(include_address=True), events)
        action_port = _RecordingAction(events, success=True)
        input_port = _RecordingInput(events)

        uc = GotoUseCase(
            tree_port=tree_port,
            action_port=action_port,
            input_port=input_port,
            session_repo=repo,
        )
        result = uc.execute(session_id="test-s", url="https://example.test")

        assert result.status == "ok"
        assert result.session_id == "test-s"
        assert result.action == "goto"
        assert result.url == "https://example.test"
        assert result.reason is None

    def test_reload_result_carries_mandatory_readable_fields(self) -> None:
        """R4/C5: a reload result exposes {status, session_id, action}; reason
        stays None on the ok path; the result is distinguishable from a
        NavigateResult (it carries session_id + action)."""
        from aiyes.domain.use_cases.reload import ReloadResult, ReloadUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()
        repo.save(_make_linux_session())
        input_port = _RecordingInput(events)

        uc = ReloadUseCase(input_port=input_port, session_repo=repo)
        result = uc.execute(session_id="test-s")

        assert result.status == "ok"
        assert result.session_id == "test-s"
        assert result.action == "reload"
        assert result.reason is None
        # Distinguishable from NavigateResult (C5 falsifier): reload carries
        # session_id + action fields that NavigateResult does not.
        assert isinstance(result, ReloadResult)

    def test_goto_result_is_frozen_dataclass(self) -> None:
        """R4/C5: GotoResult is an immutable frozen dataclass."""
        from aiyes.domain.use_cases.goto import GotoResult

        assert dataclasses.is_dataclass(GotoResult)
        r = GotoResult(status="ok", session_id="s", action="goto")
        with pytest.raises(AttributeError):
            r.status = "changed"  # type: ignore[misc]

    def test_reload_result_is_frozen_dataclass(self) -> None:
        """R4/C5: ReloadResult is an immutable frozen dataclass."""
        from aiyes.domain.use_cases.reload import ReloadResult

        assert dataclasses.is_dataclass(ReloadResult)
        r = ReloadResult(status="ok", session_id="s", action="reload")
        with pytest.raises(AttributeError):
            r.status = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# System-error boundary — session-not-found RAISES (matches every sibling)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionNotFoundBoundary:
    def test_goto_session_not_found_raises_runtime_error(self) -> None:
        """Boundary: an unknown session_id is a SYSTEM error -> RuntimeError,
        not a status="error" result (mirrors action.py/menu.py/navigate.py)."""
        from aiyes.domain.use_cases.goto import GotoUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()  # empty — no session saved
        tree_port = _RecordingTree(_browser_tree(), events)
        action_port = _RecordingAction(events, success=True)
        input_port = _RecordingInput(events)

        uc = GotoUseCase(
            tree_port=tree_port,
            action_port=action_port,
            input_port=input_port,
            session_repo=repo,
        )
        with pytest.raises(RuntimeError, match="Session not found"):
            uc.execute(session_id="nonexistent", url="https://example.test")
        assert events == []

    def test_reload_session_not_found_raises_runtime_error(self) -> None:
        """Boundary: reload on an unknown session_id raises RuntimeError."""
        from aiyes.domain.use_cases.reload import ReloadUseCase

        events: List[Tuple[Any, ...]] = []
        repo = FakeSessionRepository()  # empty
        input_port = _RecordingInput(events)

        uc = ReloadUseCase(input_port=input_port, session_repo=repo)
        with pytest.raises(RuntimeError, match="Session not found"):
            uc.execute(session_id="nonexistent")
        assert events == []
