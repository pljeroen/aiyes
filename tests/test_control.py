"""Tests for control commands: action, mouse, key, type, wait.

Requirements covered:
  R-CONTROL-01: action (AT-SPI2 action on node, two-tier error model)
  R-CONTROL-02: mouse (move, click, drag, scroll)
  R-CONTROL-03: key (key events via xdotool)
  R-CONTROL-04: type (character-by-character text input)
  R-CONTROL-05: wait (poll for node, timeout)
"""

from __future__ import annotations


import pytest

# RED imports — define expected API
from aiyes.domain.use_cases.action import ActionUseCase
from aiyes.domain.use_cases.mouse import MouseUseCase
from aiyes.domain.use_cases.key import KeyUseCase
from aiyes.domain.use_cases.type_text import TypeTextUseCase
from aiyes.domain.use_cases.wait import WaitUseCase
from aiyes.domain.session import Session

from tests.conftest import (  # noqa: F401
    FakeAccessibilityAction,
    FakeAccessibilityTree,
    FakeClock,
    FakeInput,
    FakeSessionRepository,
    FakeTreeStore,
    make_domain_tree,
    make_tree,
)


def _make_test_session() -> Session:
    return Session(
        session_id="test-s",
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=[],
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=99,
        name=None,
        resolution="1280x800",
        color_depth=24,
    )


# ──────────────────────────────────────────────────────────────────────
# R-CONTROL-01: Action use case
# ──────────────────────────────────────────────────────────────────────


class TestAction:
    """Execute AT-SPI2 actions on nodes."""

    def test_action_success(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-CONTROL-01: Successful action returns status=ok."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", node_id="n_002", action_name="click")

        assert result.status == "ok"
        assert result.action == "click"
        assert result.target == "n_002"

    def test_action_semantic_failure(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-CONTROL-01: Unavailable action returns status=error + available_actions."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        failing_action = FakeAccessibilityAction(
            success=False,
            available_actions=["click", "set_text"],
        )

        uc = ActionUseCase(
            action=failing_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", node_id="n_002", action_name="toggle")

        assert result.status == "error"
        assert hasattr(result, "reason")
        assert hasattr(result, "available_actions")
        assert "click" in result.available_actions

    def test_action_set_text(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-CONTROL-01: set_text action passes value parameter."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(
            session_id="test-s",
            node_id="n_002",
            action_name="set_text",
            value="Hello World",
        )

        do_calls = [c for c in fake_accessibility_action.calls if c[0] == "do_action"]
        assert len(do_calls) == 1
        _, _, _, value, _ = do_calls[0][1]
        assert value == "Hello World"

    def test_action_no_session_raises(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """R-CONTROL-01: Action with no session is a system error."""
        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        with pytest.raises(Exception):
            uc.execute(session_id="nonexistent", node_id="n_002", action_name="click")

    def test_action_invalid_node_id_raises(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """F-17: Action with unknown node_id is a system error."""
        session = _make_test_session()
        fake_session_repo.save(session)

        # Save a tree with known node IDs
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        with pytest.raises(RuntimeError, match="Unknown node_id"):
            uc.execute(
                session_id="test-s", node_id="n_nonexistent", action_name="click"
            )

    def test_action_valid_node_id_succeeds(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """F-17: Action with valid node_id against stored tree succeeds."""
        session = _make_test_session()
        fake_session_repo.save(session)

        # Save a tree with known node IDs (n_001, n_002, n_003)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", node_id="n_002", action_name="click")
        assert result.status == "ok"

    def test_action_invalid_node_id_against_stored_tree_raises(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """F-21: Action with invalid node_id (not in stored tree) is a system error.

        When a tree has been stored and node_id is not in it, ActionUseCase
        must fail closed with RuntimeError, not silently forward the node_id.
        """
        session = _make_test_session()
        fake_session_repo.save(session)

        # Store a tree that does NOT contain n_nonexistent
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        with pytest.raises(RuntimeError, match="Unknown node_id"):
            uc.execute(
                session_id="test-s", node_id="n_nonexistent", action_name="click"
            )

        # Action port must NOT have been called
        action_calls = [
            c for c in fake_accessibility_action.calls if c[0] == "do_action"
        ]
        assert len(action_calls) == 0

    def test_action_empty_node_id_raises(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """N-23: Action with empty node_id is rejected before any lookup."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        with pytest.raises(RuntimeError, match="Invalid node_id"):
            uc.execute(session_id="test-s", node_id="", action_name="click")

        # Action port must NOT have been called
        action_calls = [
            c for c in fake_accessibility_action.calls if c[0] == "do_action"
        ]
        assert len(action_calls) == 0

    def test_action_blank_node_id_raises(
        self,
        fake_accessibility_action: FakeAccessibilityAction,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """N-23: Action with whitespace-only node_id is rejected."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        uc = ActionUseCase(
            action=fake_accessibility_action,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        with pytest.raises(RuntimeError, match="Invalid node_id"):
            uc.execute(session_id="test-s", node_id="   ", action_name="click")

    def test_action_system_failure_from_adapter_raises(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """Infrastructure failures must stay system errors."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        class ExplodingActionPort:
            def do_action(self, *args, **kwargs):
                raise RuntimeError("AT-SPI worker crashed")

        uc = ActionUseCase(
            action=ExplodingActionPort(),
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )

        with pytest.raises(RuntimeError, match="AT-SPI worker crashed"):
            uc.execute(session_id="test-s", node_id="n_002", action_name="click")


# ──────────────────────────────────────────────────────────────────────
# R-CONTROL-02: Mouse use case
# ──────────────────────────────────────────────────────────────────────


class TestMouse:
    """Mouse control commands."""

    def test_mouse_move(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02: mouse move sends coordinates."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.move(session_id="test-s", x=100, y=200)

        move_calls = [c for c in fake_input.calls if c[0] == "mouse_move"]
        assert len(move_calls) == 1
        _, x, y = move_calls[0][1]
        assert x == 100
        assert y == 200

    def test_mouse_click_with_coords(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02: mouse click at specified position."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.click(session_id="test-s", x=50, y=75)

        click_calls = [c for c in fake_input.calls if c[0] == "mouse_click"]
        assert len(click_calls) == 1
        _, x, y, button = click_calls[0][1]
        assert x == 50
        assert y == 75
        assert button == "left"

    def test_mouse_click_without_coords(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02, OQ-07: click without coords = current cursor position."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.click(session_id="test-s")

        click_calls = [c for c in fake_input.calls if c[0] == "mouse_click"]
        assert len(click_calls) == 1
        _, x, y, button = click_calls[0][1]
        assert x is None
        assert y is None

    def test_mouse_click_right_button(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02: --button right sends right click."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.click(session_id="test-s", x=50, y=75, button="right")

        click_calls = [c for c in fake_input.calls if c[0] == "mouse_click"]
        _, _, _, button = click_calls[0][1]
        assert button == "right"

    def test_mouse_drag(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02: mouse drag from (x1,y1) to (x2,y2)."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.drag(session_id="test-s", x1=10, y1=20, x2=100, y2=200)

        drag_calls = [c for c in fake_input.calls if c[0] == "mouse_drag"]
        assert len(drag_calls) == 1
        _, x1, y1, x2, y2 = drag_calls[0][1]
        assert (x1, y1, x2, y2) == (10, 20, 100, 200)

    def test_mouse_scroll(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02: mouse scroll with direction and amount."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.scroll(session_id="test-s", direction="down", amount=5)

        scroll_calls = [c for c in fake_input.calls if c[0] == "mouse_scroll"]
        assert len(scroll_calls) == 1
        _, direction, amount = scroll_calls[0][1]
        assert direction == "down"
        assert amount == 5

    def test_mouse_scroll_default_amount(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02, OQ-10: Default scroll amount is 3."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.scroll(session_id="test-s", direction="up")

        scroll_calls = [c for c in fake_input.calls if c[0] == "mouse_scroll"]
        _, _, amount = scroll_calls[0][1]
        assert amount == 3

    def test_mouse_scroll_valid_directions(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-02, OQ-10: Direction must be up/down/left/right."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = MouseUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )

        for direction in ["up", "down", "left", "right"]:
            uc.scroll(session_id="test-s", direction=direction)

        with pytest.raises(Exception):
            uc.scroll(session_id="test-s", direction="invalid")


# ──────────────────────────────────────────────────────────────────────
# R-CONTROL-03: Key use case
# ──────────────────────────────────────────────────────────────────────


class TestKey:
    """Key event sending."""

    def test_single_key(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-03: Single key press."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = KeyUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id="test-s", key_specs=["Return"])

        key_calls = [c for c in fake_input.calls if c[0] == "key"]
        assert len(key_calls) == 1
        _, keys = key_calls[0][1]
        assert keys == ["Return"]

    def test_key_combination(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-03: Key combination (e.g. ctrl+s)."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = KeyUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id="test-s", key_specs=["ctrl+s"])

        key_calls = [c for c in fake_input.calls if c[0] == "key"]
        _, keys = key_calls[0][1]
        assert "ctrl+s" in keys

    def test_multiple_keys_in_sequence(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-03: Multiple keys sent in sequence."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = KeyUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.execute(
            session_id="test-s", key_specs=["Tab", "Tab", "Tab", "Return"]
        )

        key_calls = [c for c in fake_input.calls if c[0] == "key"]
        _, keys = key_calls[0][1]
        assert keys == ["Tab", "Tab", "Tab", "Return"]


# ──────────────────────────────────────────────────────────────────────
# R-CONTROL-04: Type use case
# ──────────────────────────────────────────────────────────────────────


class TestTypeText:
    """Character-by-character text typing."""

    def test_type_text(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-04: Type sends text to virtual display."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = TypeTextUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        result = uc.execute(session_id="test-s", text="Hello World")

        type_calls = [c for c in fake_input.calls if c[0] == "type_text"]
        assert len(type_calls) == 1
        _, text = type_calls[0][1]
        assert text == "Hello World"

    def test_type_targets_session_display(
        self,
        fake_input: FakeInput,
        fake_session_repo: FakeSessionRepository,
    ) -> None:
        """R-CONTROL-04, R-SEC-01: Type targets session's Xvfb display."""
        session = _make_test_session()
        fake_session_repo.save(session)

        uc = TypeTextUseCase(
            input_port=fake_input,
            session_repo=fake_session_repo,
        )
        uc.execute(session_id="test-s", text="test")

        type_calls = [c for c in fake_input.calls if c[0] == "type_text"]
        passed_session, _ = type_calls[0][1]
        assert passed_session.display == ":99"


# ──────────────────────────────────────────────────────────────────────
# R-CONTROL-05: Wait use case
# ──────────────────────────────────────────────────────────────────────


class TestWait:
    """Poll until node found or timeout."""

    def test_wait_found(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-CONTROL-05: Returns found=true when node matches immediately."""
        session = _make_test_session()
        fake_session_repo.save(session)

        # Tree with a button named "OK"
        tree_with_button = FakeAccessibilityTree(tree=make_tree())

        uc = WaitUseCase(
            tree=tree_with_button,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(session_id="test-s", role="push_button", name_pattern="OK")

        assert result.found is True
        assert hasattr(result, "id")

    def test_wait_timeout(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-CONTROL-05: Returns found=false on timeout."""
        session = _make_test_session()
        fake_session_repo.save(session)

        empty_tree = FakeAccessibilityTree(tree=make_tree(nodes=[]))

        uc = WaitUseCase(
            tree=empty_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            timeout=1.0,  # Short timeout for test
        )

        assert result.found is False
        assert result.timeout is True

    def test_wait_default_timeout_30(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-CONTROL-05, OQ-08: Default timeout is 30 seconds."""
        session = _make_test_session()
        fake_session_repo.save(session)

        empty_tree = FakeAccessibilityTree(tree=make_tree(nodes=[]))

        uc = WaitUseCase(
            tree=empty_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s", role="push_button", name_pattern="Missing"
        )

        # Clock should have been advanced by approximately 30 seconds total
        total_sleep = sum(fake_clock.sleep_calls)
        assert total_sleep >= 29.0  # Allow for polling interval rounding

    def test_wait_exit_code_0_on_timeout(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-CONTROL-05: Timeout is exit 0 (not an error)."""
        session = _make_test_session()
        fake_session_repo.save(session)

        empty_tree = FakeAccessibilityTree(tree=make_tree(nodes=[]))

        uc = WaitUseCase(
            tree=empty_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        # Should NOT raise an exception — timeout is a valid outcome
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="Missing",
            timeout=1.0,
        )

        assert result.found is False
        assert result.timeout is True

    def test_wait_uses_clock_port(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-CONTROL-05, PC-02: Wait uses ClockPort, not time.sleep directly."""
        session = _make_test_session()
        fake_session_repo.save(session)

        empty_tree = FakeAccessibilityTree(tree=make_tree(nodes=[]))

        uc = WaitUseCase(
            tree=empty_tree,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="Missing",
            timeout=1.0,
        )

        sleep_calls = [c for c in fake_clock.calls if c[0] == "sleep"]
        assert len(sleep_calls) > 0

    def test_wait_with_state_filter(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
        fake_clock: FakeClock,
    ) -> None:
        """R-CONTROL-05: Wait with --state waits for node with specific state."""
        session = _make_test_session()
        fake_session_repo.save(session)

        tree_port = FakeAccessibilityTree(tree=make_tree())

        uc = WaitUseCase(
            tree=tree_port,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
            clock=fake_clock,
        )
        result = uc.execute(
            session_id="test-s",
            role="push_button",
            name_pattern="OK",
            state="enabled",
        )

        # Should find it since our test buttons have "enabled" state
        assert result.found is True
