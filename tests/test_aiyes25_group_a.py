"""AIYES-25 Group A — Action Enrichment: RED tests.

Tests for enriching ActionPortResult, ActionResult, and format_action
with node_value and node_states fields. Also tests Android focus action
and AT-SPI worker JSON contract extensions.

Traceability:
  FC-01: ActionPortResult gains node_value and node_states
  FC-02: ActionResult gains node_value and node_states
  FC-03: AT-SPI worker set_text returns node_value in JSON
  FC-04: AT-SPI worker focus returns node_states in JSON
  FC-05: Android set_text re-reads node text from fresh tree
  FC-06: Android focus taps at node center
  FC-07: format_action includes node_value / node_states when non-None
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Optional
from unittest.mock import MagicMock, patch


from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import ActionPortResult
from aiyes.domain.use_cases.action import ActionResult, ActionUseCase

from tests.conftest import (  # noqa: F401
    FakeAccessibilityAction,
    FakeSessionRepository,
    FakeTreeStore,
    make_domain_tree,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


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


def _make_android_session(**overrides: Any) -> Session:
    defaults = dict(
        session_id="android-test",
        app_pid=0,
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
# FC-01: ActionPortResult gains node_value and node_states
# ═══════════════════════════════════════════════════════════════════════


class TestActionPortResultEnrichment:
    """FC-01: ActionPortResult frozen dataclass gains new optional fields."""

    def test_action_port_result_has_node_value_field(self) -> None:
        """FC-01: ActionPortResult must have a node_value field."""
        r = ActionPortResult(success=True, available_actions=(), node_value="hello")
        assert r.node_value == "hello"

    def test_action_port_result_has_node_states_field(self) -> None:
        """FC-01: ActionPortResult must have a node_states field."""
        r = ActionPortResult(
            success=True, available_actions=(), node_states=("focused",)
        )
        assert r.node_states == ("focused",)

    def test_action_port_result_defaults_backward_compat(self) -> None:
        """FC-01/FC-10: Existing two-arg construction still works, new fields default to None."""
        r = ActionPortResult(success=True, available_actions=())
        assert r.node_value is None
        assert r.node_states is None

    def test_action_port_result_coerces_list_node_states_to_tuple(self) -> None:
        """FC-01: __post_init__ coerces node_states from list to tuple."""
        r = ActionPortResult(
            success=True, available_actions=(), node_states=["focused", "enabled"]
        )
        assert r.node_states == ("focused", "enabled")
        assert isinstance(r.node_states, tuple)


# ═══════════════════════════════════════════════════════════════════════
# FC-02: ActionResult gains node_value and node_states
# ═══════════════════════════════════════════════════════════════════════


class TestActionResultEnrichment:
    """FC-02: ActionResult frozen dataclass gains new optional fields."""

    def test_action_result_has_node_value_field(self) -> None:
        """FC-02: ActionResult must have a node_value field."""
        r = ActionResult(
            status="ok",
            action="set_text",
            target="n_001",
            node_value="hello",
        )
        assert r.node_value == "hello"

    def test_action_result_has_node_states_field(self) -> None:
        """FC-02: ActionResult must have a node_states field."""
        r = ActionResult(
            status="ok",
            action="focus",
            target="n_001",
            node_states=("focused",),
        )
        assert r.node_states == ("focused",)

    def test_action_result_defaults_backward_compat(self) -> None:
        """FC-02/FC-10: Existing three-arg construction still works, new fields default to None."""
        r = ActionResult(status="ok", action="click", target="n_001")
        assert r.node_value is None
        assert r.node_states is None

    def test_action_result_has_expected_fields(self) -> None:
        """FC-02/AIYES-70: ActionResult exposes compatible enriched fields."""
        fields = dataclasses.fields(ActionResult)
        assert {field.name for field in fields} == {
            "status",
            "action",
            "target",
            "reason",
            "available_actions",
            "node_value",
            "node_states",
            "action_method",
        }


# ═══════════════════════════════════════════════════════════════════════
# FC-03/FC-06 at use case level: ActionUseCase propagation
# ═══════════════════════════════════════════════════════════════════════


class _FakeActionPortWithEnrichment:
    """Fake action port that returns enriched ActionPortResult."""

    def __init__(
        self,
        success: bool = True,
        available_actions: tuple = (),
        node_value: Optional[str] = None,
        node_states: Optional[tuple] = None,
    ) -> None:
        self._success = success
        self._available_actions = available_actions
        self._node_value = node_value
        self._node_states = node_states

    def do_action(
        self, session, node_id, action_name, value=None, registry=None
    ) -> ActionPortResult:
        return ActionPortResult(
            success=self._success,
            available_actions=self._available_actions,
            node_value=self._node_value,
            node_states=self._node_states,
        )


class TestActionUseCasePropagation:
    """FC-03..06 at use case level: ActionUseCase propagates enriched fields."""

    def test_action_uc_propagates_node_value_on_success(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """Use case must propagate node_value from port result to ActionResult."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        fake_port = _FakeActionPortWithEnrichment(
            success=True,
            available_actions=("set_text",),
            node_value="typed text",
        )
        uc = ActionUseCase(
            action=fake_port,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(
            session_id="test-s",
            node_id="n_002",
            action_name="set_text",
            value="typed text",
        )

        assert result.status == "ok"
        assert result.node_value == "typed text"

    def test_action_uc_propagates_node_states_on_success(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """Use case must propagate node_states from port result to ActionResult."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        fake_port = _FakeActionPortWithEnrichment(
            success=True,
            available_actions=("focus",),
            node_states=("focused", "enabled"),
        )
        uc = ActionUseCase(
            action=fake_port,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", node_id="n_002", action_name="focus")

        assert result.status == "ok"
        assert result.node_states == ("focused", "enabled")

    def test_action_uc_returns_none_node_value_on_failure(
        self,
        fake_session_repo: FakeSessionRepository,
        fake_tree_store: FakeTreeStore,
    ) -> None:
        """Failure path must leave node_value and node_states as None."""
        session = _make_test_session()
        fake_session_repo.save(session)
        fake_tree_store.save_tree("test-s", make_domain_tree(), None)

        fake_port = _FakeActionPortWithEnrichment(
            success=False,
            available_actions=("click",),
            node_value=None,
            node_states=None,
        )
        uc = ActionUseCase(
            action=fake_port,
            session_repo=fake_session_repo,
            tree_store=fake_tree_store,
        )
        result = uc.execute(session_id="test-s", node_id="n_002", action_name="toggle")

        assert result.status == "error"
        assert result.node_value is None
        assert result.node_states is None


# ═══════════════════════════════════════════════════════════════════════
# FC-07: format_action includes node_value and node_states
# ═══════════════════════════════════════════════════════════════════════


class TestFormatActionEnrichment:
    """FC-07: format_action gains node_value and node_states parameters."""

    def test_format_action_includes_node_value_when_present(self) -> None:
        """FC-07: JSON must include node_value key when not None."""
        from aiyes.cli.presenter import format_action

        result = format_action(
            status="ok",
            action="set_text",
            target="n_001",
            node_value="hello",
        )
        parsed = json.loads(result)
        assert parsed["node_value"] == "hello"

    def test_format_action_omits_node_value_when_none(self) -> None:
        """FC-07: JSON must omit node_value key when None."""
        from aiyes.cli.presenter import format_action

        result = format_action(
            status="ok",
            action="click",
            target="n_001",
        )
        parsed = json.loads(result)
        assert "node_value" not in parsed

    def test_format_action_includes_node_states_when_present(self) -> None:
        """FC-07: JSON must include node_states when not None."""
        from aiyes.cli.presenter import format_action

        result = format_action(
            status="ok",
            action="focus",
            target="n_001",
            node_states=["focused", "enabled"],
        )
        parsed = json.loads(result)
        assert parsed["node_states"] == ["focused", "enabled"]

    def test_format_action_omits_node_states_when_none(self) -> None:
        """FC-07: JSON must omit node_states key when None."""
        from aiyes.cli.presenter import format_action

        result = format_action(
            status="ok",
            action="click",
            target="n_001",
        )
        parsed = json.loads(result)
        assert "node_states" not in parsed

    def test_format_action_backward_compat_no_new_keys(self) -> None:
        """FC-07/FC-10: Existing call omitting new params produces identical output."""
        from aiyes.cli.presenter import format_action

        result = format_action(
            status="ok",
            action="click",
            target="n_001",
        )
        parsed = json.loads(result)
        # Only the original keys should be present
        assert set(parsed.keys()) == {"status", "action", "target"}


# ═══════════════════════════════════════════════════════════════════════
# FC-05, FC-06: Android adapter enrichment
# ═══════════════════════════════════════════════════════════════════════


class TestAndroidAdapterEnrichment:
    """FC-05/FC-06: Android adapter returns enriched ActionPortResult."""

    def test_android_set_text_returns_node_value_after_typing(self) -> None:
        """FC-05: After set_text, re-read tree and populate node_value from fresh node.name."""
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        # Initial tree: node with original text
        initial_node = Node(
            id="n_001",
            role="EditText",
            name="Username",
            bounds=(100, 200, 400, 80),
            states=("enabled", "focusable"),
            actions=("click", "set_text"),
        )
        # Fresh tree after set_text: node has updated text
        fresh_node = Node(
            id="n_001",
            role="EditText",
            name="hello",  # This is the typed text (Android maps text -> name)
            bounds=(100, 200, 400, 80),
            states=("enabled", "focusable", "focused"),
            actions=("click", "set_text"),
        )

        mock_tree = MagicMock()
        # First call returns initial tree (for node resolution),
        # Second call returns fresh tree (for readback)
        mock_tree.get_tree.side_effect = [
            AccessibilityTree(roots=(initial_node,)),
            AccessibilityTree(roots=(fresh_node,)),
        ]
        adapter.set_tree_adapter(mock_tree)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_action_adapter.subprocess.run",
            return_value=mock_result,
        ):
            result = adapter.do_action(session, "n_001", "set_text", value="hello")

        assert result.success is True
        assert result.node_value == "hello"

    def test_android_focus_taps_at_center(self) -> None:
        """FC-06: Focus action taps at node center coordinates."""
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        node = Node(
            id="n_001",
            role="EditText",
            name="Username",
            bounds=(100, 200, 400, 80),
            states=("enabled", "focusable"),
            actions=("click", "set_text"),
        )
        # After tap, re-read returns focused state
        focused_node = Node(
            id="n_001",
            role="EditText",
            name="Username",
            bounds=(100, 200, 400, 80),
            states=("enabled", "focusable", "focused"),
            actions=("click", "set_text"),
        )

        mock_tree = MagicMock()
        mock_tree.get_tree.side_effect = [
            AccessibilityTree(roots=(node,)),
            AccessibilityTree(roots=(focused_node,)),
        ]
        adapter.set_tree_adapter(mock_tree)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_action_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            result = adapter.do_action(session, "n_001", "focus")

        assert result.success is True
        # Center of (100, 200, 500, 280) = (300, 240)
        cmd = mock_run.call_args[0][0]
        assert "300" in cmd
        assert "240" in cmd

    def test_android_focus_returns_success(self) -> None:
        """FC-06: Focus action returns success=True, not 'unknown action' failure."""
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        node = Node(
            id="n_001",
            role="EditText",
            name="Username",
            bounds=(100, 200, 400, 80),
            states=("enabled", "focusable"),
            actions=("click", "set_text"),
        )

        mock_tree = MagicMock()
        mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
        adapter.set_tree_adapter(mock_tree)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_action_adapter.subprocess.run",
            return_value=mock_result,
        ):
            result = adapter.do_action(session, "n_001", "focus")

        # Must be success, not the current "unknown action" -> success=False
        assert result.success is True

    def test_android_click_returns_no_node_value(self) -> None:
        """FC-05: Click action does NOT populate node_value (it remains None)."""
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter

        adapter = AndroidActionAdapter()
        session = _make_android_session()

        node = Node(
            id="n_001",
            role="Button",
            name="Login",
            bounds=(100, 300, 400, 80),
            states=("enabled",),
            actions=("click",),
        )

        mock_tree = MagicMock()
        mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
        adapter.set_tree_adapter(mock_tree)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(
            "aiyes.adapters.android_action_adapter.subprocess.run",
            return_value=mock_result,
        ):
            result = adapter.do_action(session, "n_001", "click")

        assert result.success is True
        assert result.node_value is None


# ═══════════════════════════════════════════════════════════════════════
# FC-03, FC-04: AT-SPI adapter JSON contract (test via adapter parsing)
# ═══════════════════════════════════════════════════════════════════════


class TestAtSpiActionJsonContract:
    """FC-03/FC-04: AT-SPI action adapter parses enriched worker JSON.

    The AT-SPI worker runs as a subprocess — we test the adapter's
    parsing of the worker's JSON output contract rather than the worker
    itself.
    """

    def test_atspi_action_result_json_has_node_value_key(self) -> None:
        """FC-03: Adapter parses node_value from worker JSON into ActionPortResult."""
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        worker_json = json.dumps(
            {
                "success": True,
                "available_actions": ["set_text"],
                "node_value": "typed text",
                "node_states": None,
            }
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = worker_json
        mock_result.stderr = ""

        with (
            patch(
                "aiyes.adapters.atspi_action_adapter.subprocess.run",
                return_value=mock_result,
            ),
            patch("aiyes.adapters.atspi_action_adapter._GI_AVAILABLE", True),
        ):
            session = Session(
                session_id="test-a",
                app_pid=1,
                app_command="test",
                app_args=(),
                name=None,
                display=":99",
                atspi_bus_address="unix:abstract=/tmp/dbus-test",
            )
            result = adapter.do_action(session, "n_001", "set_text", "typed text")

        assert result.node_value == "typed text"

    def test_atspi_action_result_json_has_node_states_key(self) -> None:
        """FC-04: Adapter parses node_states from worker JSON into ActionPortResult."""
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        worker_json = json.dumps(
            {
                "success": True,
                "available_actions": ["focus"],
                "node_value": None,
                "node_states": ["focused", "enabled"],
            }
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = worker_json
        mock_result.stderr = ""

        with (
            patch(
                "aiyes.adapters.atspi_action_adapter.subprocess.run",
                return_value=mock_result,
            ),
            patch("aiyes.adapters.atspi_action_adapter._GI_AVAILABLE", True),
        ):
            session = Session(
                session_id="test-a",
                app_pid=1,
                app_command="test",
                app_args=(),
                name=None,
                display=":99",
                atspi_bus_address="unix:abstract=/tmp/dbus-test",
            )
            result = adapter.do_action(session, "n_001", "focus")

        assert result.node_states == ("focused", "enabled")

    def test_atspi_focus_result_json_has_success_true(self) -> None:
        """FC-04: Focus action in worker returns success=true when focus succeeds."""
        from aiyes.adapters.atspi_action_adapter import AtSpi2ActionAdapter

        adapter = AtSpi2ActionAdapter()

        worker_json = json.dumps(
            {
                "success": True,
                "available_actions": ["focus"],
                "node_value": None,
                "node_states": ["focused"],
            }
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = worker_json
        mock_result.stderr = ""

        with (
            patch(
                "aiyes.adapters.atspi_action_adapter.subprocess.run",
                return_value=mock_result,
            ),
            patch("aiyes.adapters.atspi_action_adapter._GI_AVAILABLE", True),
        ):
            session = Session(
                session_id="test-a",
                app_pid=1,
                app_command="test",
                app_args=(),
                name=None,
                display=":99",
                atspi_bus_address="unix:abstract=/tmp/dbus-test",
            )
            result = adapter.do_action(session, "n_001", "focus")

        assert result.success is True
        assert result.node_value is None
        assert result.node_states == ("focused",)
