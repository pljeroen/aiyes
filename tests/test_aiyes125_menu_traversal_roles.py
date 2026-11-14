"""Menu traversal by dot-separated path on accessibility trees whose menu roles
use either the spaced role names Linux desktop toolkits report ("menu bar",
"menu item", "check menu item", ...) or the underscore names ("menu_bar",
"menu_item", "check_menu_item", ...).

Behaviour covered:

- every segment matches menu roles in both spellings, and the set of roles a
  segment may match is not widened (containers such as "popup menu" and
  non-menu nodes are never used);
- each node receives the action it offers: "click", else "Press", else
  "ShowMenu", read from the node's own action list;
- an action the application refuses stops the traversal with an error that
  names the item and the actions it offers, instead of reporting success;
- every segment after the first is looked up only inside the node the previous
  segment resolved to, so a same-named item in another menu is never used;
- the ``menu`` CLI command and the ``menu`` MCP tool deliver the same behaviour
  with an unchanged output format.

Every test runs on in-memory fakes; none needs a display or a live application.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple
from unittest.mock import MagicMock, patch

import pytest

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.cli.main import cli
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.types import ActionPortResult
from aiyes.domain.use_cases.menu import MenuResult, MenuUseCase

from tests.conftest import (
    FakeAccessibilityAction,
    FakeClock,
    FakeSessionRepository,
    FakeTreeStore,
    make_cli_runner,
    make_domain_tree,
    make_node,
)


# ═══════════════════════════════════════════════════════════════════════
# Role spellings
# ═══════════════════════════════════════════════════════════════════════

# Role kinds used in fixture specs. They are replaced by a concrete role
# string of the chosen spelling when the tree is built; any other role in a
# spec is used literally.
MENU_BAR = "<menu bar>"
MENU = "<menu>"
MENU_ITEM = "<menu item>"
CHECK_ITEM = "<check menu item>"
RADIO_ITEM = "<radio menu item>"
POPUP = "<popup menu>"

UNDERSCORE = "underscore"
SPACED = "spaced"
SPELLINGS = (UNDERSCORE, SPACED)

_ROLE_NAMES: Dict[str, Dict[str, str]] = {
    UNDERSCORE: {
        MENU_BAR: "menu_bar",
        MENU: "menu",
        MENU_ITEM: "menu_item",
        CHECK_ITEM: "check_menu_item",
        RADIO_ITEM: "radio_menu_item",
        POPUP: "popup_menu",
    },
    SPACED: {
        MENU_BAR: "menu bar",
        MENU: "menu",
        MENU_ITEM: "menu item",
        CHECK_ITEM: "check menu item",
        RADIO_ITEM: "radio menu item",
        POPUP: "popup menu",
    },
}


@dataclasses.dataclass(frozen=True)
class N:
    """Spec of one tree node. ``role`` is a role kind or a literal role."""

    node_id: str
    role: str
    name: str
    actions: Tuple[str, ...] = ("click",)
    children: Tuple["N", ...] = ()


def _raw(spec: N, spelling: str) -> Dict[str, Any]:
    role = _ROLE_NAMES[spelling].get(spec.role, spec.role)
    return make_node(
        spec.node_id,
        role,
        spec.name,
        actions=list(spec.actions),
        children=[_raw(child, spelling) for child in spec.children],
    )


def _tree(spelling: str, *roots: N) -> AccessibilityTree:
    return make_domain_tree([_raw(root, spelling) for root in roots])


# ═══════════════════════════════════════════════════════════════════════
# Fixture trees
# ═══════════════════════════════════════════════════════════════════════

# GTK3-shaped menu, as a GTK3 application exposes it: top-level and submenu
# parents are "menu" nodes, leaves are "menu item" / "check menu item"; every
# menu node offers "click".
GTK = (
    N(
        "g_frame",
        "frame",
        "gtk menu probe",
        (),
        (
            N(
                "g_bar",
                MENU_BAR,
                "",
                (),
                (
                    N(
                        "g_file",
                        MENU,
                        "File",
                        children=(
                            N("g_save", MENU_ITEM, "Save"),
                            N("g_autosave", CHECK_ITEM, "Autosave"),
                            N(
                                "g_export",
                                MENU,
                                "Export",
                                children=(N("g_pdf", MENU_ITEM, "PDF"),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
)

# Qt Widgets-shaped menu: every menu node, the top level included, is a
# "menu item"; a "popup menu" container sits between levels; nodes that open a
# submenu offer only "ShowMenu", leaves offer only "Press".
QT = (
    N(
        "q_frame",
        "frame",
        "menu probe",
        (),
        (
            N(
                "q_bar",
                MENU_BAR,
                "",
                (),
                (
                    N(
                        "q_file",
                        MENU_ITEM,
                        "File",
                        ("ShowMenu",),
                        (
                            N(
                                "q_popup",
                                POPUP,
                                "",
                                (),
                                (
                                    N("q_save", MENU_ITEM, "Save", ("Press",)),
                                    N("q_autosave", MENU_ITEM, "Autosave", ("Press",)),
                                    N(
                                        "q_export",
                                        MENU_ITEM,
                                        "Export",
                                        ("ShowMenu",),
                                        (
                                            N(
                                                "q_export_popup",
                                                POPUP,
                                                "",
                                                (),
                                                (
                                                    N(
                                                        "q_pdf",
                                                        MENU_ITEM,
                                                        "PDF",
                                                        ("Press",),
                                                    ),
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
)
_QT_CONTAINERS = {"q_bar", "q_popup", "q_export_popup"}

# Three menus sharing item names: "Close" in File and Window, an "Export"
# submenu in File and Edit; "Save" only in File, "Preferences" only in Edit.
CROSS_MENU = (
    N(
        "c_frame",
        "frame",
        "App",
        (),
        (
            N(
                "c_bar",
                MENU_BAR,
                "",
                (),
                (
                    N(
                        "c_file",
                        MENU,
                        "File",
                        children=(
                            N("c_file_close", MENU_ITEM, "Close"),
                            N("c_file_save", MENU_ITEM, "Save"),
                            N(
                                "c_file_export",
                                MENU,
                                "Export",
                                children=(N("c_pdf", MENU_ITEM, "PDF"),),
                            ),
                        ),
                    ),
                    N(
                        "c_edit",
                        MENU,
                        "Edit",
                        children=(
                            N("c_prefs", MENU_ITEM, "Preferences"),
                            N(
                                "c_edit_export",
                                MENU,
                                "Export",
                                children=(N("c_png", MENU_ITEM, "PNG"),),
                            ),
                        ),
                    ),
                    N(
                        "c_window",
                        MENU,
                        "Window",
                        children=(N("c_window_close", MENU_ITEM, "Close"),),
                    ),
                ),
            ),
        ),
    ),
)

# Qt Widgets-shaped variant of the shared-name case: "Close" in File and Window.
QT_CROSS_MENU = (
    N(
        "x_frame",
        "frame",
        "App",
        (),
        (
            N(
                "x_bar",
                MENU_BAR,
                "",
                (),
                (
                    N(
                        "x_file",
                        MENU_ITEM,
                        "File",
                        ("ShowMenu",),
                        (
                            N(
                                "x_file_popup",
                                POPUP,
                                "",
                                (),
                                (
                                    N("x_file_close", MENU_ITEM, "Close", ("Press",)),
                                    N("x_file_save", MENU_ITEM, "Save", ("Press",)),
                                ),
                            ),
                        ),
                    ),
                    N(
                        "x_window",
                        MENU_ITEM,
                        "Window",
                        ("ShowMenu",),
                        (
                            N(
                                "x_window_popup",
                                POPUP,
                                "",
                                (),
                                (
                                    N(
                                        "x_window_close",
                                        MENU_ITEM,
                                        "Close",
                                        ("Press",),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
)

# A plain application window: menu bar with File and Edit menus plus a panel.
STANDARD = (
    N(
        "s_root",
        "frame",
        "App Window",
        (),
        (
            N(
                "s_menubar",
                MENU_BAR,
                "",
                children=(
                    N(
                        "s_file",
                        MENU,
                        "File",
                        children=(
                            N("s_save", MENU_ITEM, "Save"),
                            N("s_saveas", MENU_ITEM, "Save As"),
                        ),
                    ),
                    N(
                        "s_edit",
                        MENU,
                        "Edit",
                        children=(N("s_prefs", MENU_ITEM, "Preferences"),),
                    ),
                ),
            ),
            N("s_content", "panel", "Content", ()),
        ),
    ),
)


def _frame(*children: N) -> N:
    return N("frame", "frame", "App", (), children)


def _bar(*menus: N) -> N:
    return N("bar", MENU_BAR, "", (), menus)


# ═══════════════════════════════════════════════════════════════════════
# Fakes and harness
# ═══════════════════════════════════════════════════════════════════════


class RecordingAction:
    """Action port fake. Records every call in the FakeAccessibilityAction
    format. Succeeds, except for node ids listed in ``refuse``, which maps a
    node id to the actions that node reports offering."""

    def __init__(self, refuse: Optional[Dict[str, Sequence[str]]] = None) -> None:
        self._refuse = dict(refuse or {})
        self.calls: List[Tuple[str, Any]] = []

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        self.calls.append(
            ("do_action", (session, node_id, action_name, value, registry))
        )
        if node_id in self._refuse:
            return ActionPortResult(
                success=False, available_actions=tuple(self._refuse[node_id])
            )
        return ActionPortResult(success=True, available_actions=())


class TreeSequence:
    """Tree port fake: read n returns trees[n]; the last tree repeats."""

    def __init__(self, *trees: AccessibilityTree) -> None:
        self._trees = trees
        self.reads = 0

    def get_tree(self, session: Any) -> AccessibilityTree:
        tree = self._trees[min(self.reads, len(self._trees) - 1)]
        self.reads += 1
        return tree


class RevealingTree:
    """Tree port fake for menus whose contents appear only after their parent
    has been acted on: every read builds the tree from the set of node ids
    acted on so far."""

    def __init__(
        self,
        action_port: Any,
        build: Callable[[FrozenSet[str]], AccessibilityTree],
    ) -> None:
        self._action_port = action_port
        self._build = build
        self.reads = 0

    def get_tree(self, session: Any) -> AccessibilityTree:
        self.reads += 1
        acted = frozenset(node_id for node_id, _ in _sent(self._action_port))
        return self._build(acted)


def _sent(action_port: Any) -> List[Tuple[str, str]]:
    """Ordered (node id, action name) pairs of every do_action call."""
    return [
        (args[1], args[2]) for kind, args in action_port.calls if kind == "do_action"
    ]


def _linux_session() -> Session:
    return Session(
        session_id="test-s",
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
        started_at=1000.0,
    )


def _android_session() -> Session:
    return Session(
        session_id="android-test",
        app_pid=200,
        app_command="com.example.app/.MainActivity",
        app_args=(),
        name=None,
        started_at=1000.0,
        backend="android",
        device_serial="emulator-5554",
    )


@dataclasses.dataclass
class Rig:
    use_case: MenuUseCase
    session: Session
    tree_port: Any
    action_port: Any
    tree_store: FakeTreeStore
    clock: FakeClock


def _rig(
    stored: AccessibilityTree,
    *,
    tree_port: Any = None,
    action_port: Any = None,
    session: Optional[Session] = None,
) -> Rig:
    """A real MenuUseCase over fakes. ``stored`` is the tree saved by the last
    tree read of the session; ``tree_port`` serves every fresh read (default:
    the stored tree on every read)."""
    session = session if session is not None else _linux_session()
    repo = FakeSessionRepository()
    repo.save(session)
    tree_store = FakeTreeStore()
    tree_store.save_tree(session.session_id, stored, {})
    tree_port = tree_port if tree_port is not None else TreeSequence(stored)
    action_port = action_port if action_port is not None else RecordingAction()
    clock = FakeClock()
    use_case = MenuUseCase(
        tree_port=tree_port,
        action_port=action_port,
        session_repo=repo,
        tree_store=tree_store,
        clock=clock,
    )
    return Rig(use_case, session, tree_port, action_port, tree_store, clock)


@dataclasses.dataclass
class Run:
    result: Optional[MenuResult]
    error: Optional[Exception]
    sent: List[Tuple[str, str]]
    reads: int
    sleeps: List[float]
    tree_loads: int

    @property
    def targets(self) -> List[str]:
        return [node_id for node_id, _ in self.sent]


def run_menu(
    path: str,
    stored: AccessibilityTree,
    *,
    tree_port: Any = None,
    action_port: Any = None,
    session: Optional[Session] = None,
) -> Run:
    """Run ``menu <path>`` and record its outcome, the actions sent, the tree
    reads and the waits."""
    rig = _rig(stored, tree_port=tree_port, action_port=action_port, session=session)
    result: Optional[MenuResult] = None
    error: Optional[Exception] = None
    try:
        result = rig.use_case.execute(session_id=rig.session.session_id, menu_path=path)
    except Exception as exc:  # the use case reports every failure by raising
        error = exc
    return Run(
        result=result,
        error=error,
        sent=_sent(rig.action_port),
        reads=rig.tree_port.reads,
        sleeps=list(rig.clock.sleep_calls),
        tree_loads=sum(1 for kind, _ in rig.tree_store.calls if kind == "load_tree"),
    )


def _not_found(segment: str, path: str) -> str:
    return f"Menu item not found: '{segment}' in path '{path}'"


def _not_found_later(segment: str, path: str) -> str:
    return (
        f"Menu item not found: '{segment}' in path '{path}' "
        "— the submenu may not have appeared yet"
    )


def _assert_ok(run: Run, node_id: str, node_name: str) -> None:
    assert run.error is None, f"expected success, got {run.error!r}"
    assert run.result == MenuResult(status="ok", node_id=node_id, node_name=node_name)


def _assert_error(run: Run, message: str) -> None:
    assert isinstance(run.error, RuntimeError), (
        f"expected RuntimeError({message!r}); got error {run.error!r}, "
        f"result {run.result!r}, actions {run.sent!r}"
    )
    assert str(run.error) == message


def _assert_refusal_message(message: str, required: Sequence[str]) -> None:
    assert "not found" not in message.lower(), (
        f"a refused action must not be reported as not found: {message!r}"
    )
    for part in required:
        assert part in message, f"{part!r} missing from {message!r}"


def _outcome(run: Run) -> Tuple[Any, ...]:
    if run.error is not None:
        return (run.sent, "error", type(run.error).__name__, str(run.error))
    return (run.sent, "ok", run.result)


# ═══════════════════════════════════════════════════════════════════════
# Role matching at each segment
# ═══════════════════════════════════════════════════════════════════════


class TestRoleMatchingPerSegment:
    """Menu roles match in both spellings; the role set per segment is fixed."""

    @pytest.mark.parametrize(
        "role",
        [
            "menu",
            "menu bar",
            "menu_bar",
            "menu item",
            "menu_item",
            "check menu item",
            "check_menu_item",
            "radio menu item",
            "radio_menu_item",
        ],
    )
    def test_first_segment_matches_a_menu_role_in_either_spelling(
        self, role: str
    ) -> None:
        """A one-segment path activates the named node whatever the spelling
        of its menu role."""
        tree = _tree(SPACED, _frame(N("target", role, "Target")))

        run = run_menu("Target", tree)

        _assert_ok(run, "target", "Target")
        assert run.targets == ["target"]

    @pytest.mark.parametrize(
        "role",
        [
            "menu",
            "menu item",
            "menu_item",
            "check menu item",
            "check_menu_item",
            "radio menu item",
            "radio_menu_item",
        ],
    )
    def test_later_segment_matches_an_item_role_in_either_spelling(
        self, role: str
    ) -> None:
        """A second segment activates the named item inside the first
        segment's menu whatever the spelling of the item's role."""
        tree = _tree(
            SPACED,
            _frame(
                N(
                    "bar",
                    "menu bar",
                    "",
                    (),
                    (N("top", "menu", "Top", children=(N("leaf", role, "Leaf"),)),),
                )
            ),
        )

        run = run_menu("Top.Leaf", tree)

        _assert_ok(run, "leaf", "Leaf")
        assert run.targets == ["top", "leaf"]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_menu_bar_is_not_matched_by_a_later_segment(self, spelling: str) -> None:
        """A menu bar inside a menu is not an item a later segment can name."""
        tree = _tree(
            spelling,
            _frame(N("top", MENU, "Top", children=(N("leaf", MENU_BAR, "Leaf"),))),
        )

        run = run_menu("Top.Leaf", tree)

        _assert_error(run, _not_found_later("Leaf", "Top.Leaf"))
        assert run.targets == ["top"]

    @pytest.mark.parametrize("role", ["popup menu", "tearoff menu item"])
    def test_container_node_is_never_matched_by_the_first_segment(
        self, role: str
    ) -> None:
        """Popup-menu and tear-off containers are not menu items."""
        tree = _tree(SPACED, _frame(N("container", role, "Target")))

        run = run_menu("Target", tree)

        _assert_error(run, _not_found("Target", "Target"))
        assert run.sent == []

    @pytest.mark.parametrize("role", ["popup menu", "tearoff menu item"])
    def test_container_node_is_never_matched_by_a_later_segment(
        self, role: str
    ) -> None:
        """Popup-menu and tear-off containers inside a menu are not items."""
        tree = _tree(
            SPACED,
            _frame(
                N(
                    "bar",
                    "menu bar",
                    "",
                    (),
                    (
                        N(
                            "top",
                            "menu",
                            "Top",
                            children=(N("container", role, "Target"),),
                        ),
                    ),
                )
            ),
        )

        run = run_menu("Top.Target", tree)

        _assert_error(run, _not_found_later("Target", "Top.Target"))
        assert run.targets == ["top"]


# ═══════════════════════════════════════════════════════════════════════
# GTK3- and Qt Widgets-shaped menus
# ═══════════════════════════════════════════════════════════════════════


class TestGtkShapedMenu:
    """Paths through a GTK3-shaped menu activate each segment with click."""

    @pytest.mark.parametrize(
        "path, expected_sent, final_id, final_name",
        [
            ("File", [("g_file", "click")], "g_file", "File"),
            (
                "File.Save",
                [("g_file", "click"), ("g_save", "click")],
                "g_save",
                "Save",
            ),
            (
                "File.Autosave",
                [("g_file", "click"), ("g_autosave", "click")],
                "g_autosave",
                "Autosave",
            ),
            (
                "File.Export.PDF",
                [("g_file", "click"), ("g_export", "click"), ("g_pdf", "click")],
                "g_pdf",
                "PDF",
            ),
        ],
    )
    def test_path_activates_each_segment_in_order(
        self,
        path: str,
        expected_sent: List[Tuple[str, str]],
        final_id: str,
        final_name: str,
    ) -> None:
        """Each segment is resolved and clicked in path order; the result
        names the last segment's node."""
        run = run_menu(path, _tree(SPACED, *GTK))

        _assert_ok(run, final_id, final_name)
        assert run.sent == expected_sent

    def test_submenus_that_appear_only_after_their_parent_is_activated(
        self,
    ) -> None:
        """Menu contents absent until the parent is activated are found on
        the re-read that follows each activation."""

        def build(acted: FrozenSet[str]) -> AccessibilityTree:
            export_items = (
                (N("g_pdf", MENU_ITEM, "PDF"),) if "g_export" in acted else ()
            )
            file_items = (
                (
                    N("g_save", MENU_ITEM, "Save"),
                    N("g_autosave", CHECK_ITEM, "Autosave"),
                    N("g_export", MENU, "Export", children=export_items),
                )
                if "g_file" in acted
                else ()
            )
            return _tree(
                SPACED,
                N(
                    "g_frame",
                    "frame",
                    "gtk menu probe",
                    (),
                    (
                        N(
                            "g_bar",
                            MENU_BAR,
                            "",
                            (),
                            (N("g_file", MENU, "File", children=file_items),),
                        ),
                    ),
                ),
            )

        action = RecordingAction()
        run = run_menu(
            "File.Export.PDF",
            build(frozenset()),
            tree_port=RevealingTree(action, build),
            action_port=action,
        )

        _assert_ok(run, "g_pdf", "PDF")
        assert run.sent == [
            ("g_file", "click"),
            ("g_export", "click"),
            ("g_pdf", "click"),
        ]


class TestQtShapedMenu:
    """Paths through a Qt Widgets-shaped menu use the actions its nodes offer."""

    @pytest.mark.parametrize(
        "path, expected_sent, final_id, final_name",
        [
            ("File", [("q_file", "ShowMenu")], "q_file", "File"),
            (
                "File.Save",
                [("q_file", "ShowMenu"), ("q_save", "Press")],
                "q_save",
                "Save",
            ),
            (
                "File.Autosave",
                [("q_file", "ShowMenu"), ("q_autosave", "Press")],
                "q_autosave",
                "Autosave",
            ),
            (
                "File.Export.PDF",
                [("q_file", "ShowMenu"), ("q_export", "ShowMenu"), ("q_pdf", "Press")],
                "q_pdf",
                "PDF",
            ),
        ],
    )
    def test_path_activates_each_segment_with_the_action_it_offers(
        self,
        path: str,
        expected_sent: List[Tuple[str, str]],
        final_id: str,
        final_name: str,
    ) -> None:
        """A top-level menu reported as a menu item resolves; ShowMenu opens
        a menu, Press activates an item; the menu bar and the popup-menu
        containers are searched through but never acted on."""
        run = run_menu(path, _tree(SPACED, *QT))

        _assert_ok(run, final_id, final_name)
        assert not set(run.targets) & _QT_CONTAINERS
        assert run.sent == expected_sent


# ═══════════════════════════════════════════════════════════════════════
# Waiting for a submenu
# ═══════════════════════════════════════════════════════════════════════


def _closed_file_menu(*file_items: N) -> AccessibilityTree:
    return _tree(
        SPACED,
        _frame(_bar(N("file", MENU, "File", children=file_items))),
    )


class TestSubmenuWait:
    """After a non-final segment the tree is re-read until the next item
    appears: at most three re-reads, 0.2 s apart."""

    def test_item_that_appears_on_a_later_reread_is_activated(self) -> None:
        closed = _closed_file_menu()
        opened = _closed_file_menu(N("save", MENU_ITEM, "Save"))

        run = run_menu("File.Save", closed, tree_port=TreeSequence(closed, opened))

        _assert_ok(run, "save", "Save")
        assert run.sent == [("file", "click"), ("save", "click")]
        assert run.reads >= 2

    def test_item_that_never_appears_reports_the_wait_error(self) -> None:
        closed = _closed_file_menu()

        run = run_menu("File.Save", closed, tree_port=TreeSequence(closed))

        _assert_error(run, _not_found_later("Save", "File.Save"))
        assert run.targets == ["file"]
        assert run.reads == 3
        assert run.sleeps == [0.2, 0.2, 0.2]


# ═══════════════════════════════════════════════════════════════════════
# Non-menu nodes with a menu item's name
# ═══════════════════════════════════════════════════════════════════════

_NON_MENU_ROLES = [
    "button",
    "push button",
    "push_button",
    "label",
    "text",
    "list item",
    "page tab",
    "popup menu",
]


class TestNonMenuNodesAreIgnored:
    """Nodes without a menu role are never activated, whatever their name."""

    def test_menu_item_is_chosen_over_an_earlier_same_named_button(self) -> None:
        tree = _tree(
            SPACED,
            _frame(
                N("button_file", "button", "File"),
                N(
                    "bar",
                    "menu bar",
                    "",
                    (),
                    (
                        N(
                            "file",
                            "menu item",
                            "File",
                            children=(
                                N(
                                    "popup",
                                    "popup menu",
                                    "",
                                    (),
                                    (N("save", "menu item", "Save"),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )

        run = run_menu("File.Save", tree)

        _assert_ok(run, "save", "Save")
        assert run.targets == ["file", "save"]

    @pytest.mark.parametrize("placement", ["beside_the_menu", "inside_the_menu"])
    @pytest.mark.parametrize("role", _NON_MENU_ROLES)
    def test_same_named_non_menu_node_is_never_used(
        self, role: str, placement: str
    ) -> None:
        """Neither as a first segment nor as the item awaited after a menu is
        opened, whether the node sits beside the menu bar or inside the menu."""
        other = N("other", role, "Save")
        if placement == "beside_the_menu":
            tree = _tree(
                SPACED,
                _frame(
                    other, N("bar", "menu bar", "", (), (N("file", "menu", "File"),))
                ),
            )
        else:
            tree = _tree(
                SPACED,
                _frame(
                    N(
                        "bar",
                        "menu bar",
                        "",
                        (),
                        (N("file", "menu", "File", children=(other,)),),
                    )
                ),
            )

        first = run_menu("Save", tree)
        later = run_menu("File.Save", tree)

        _assert_error(first, _not_found("Save", "Save"))
        assert first.sent == []
        _assert_error(later, _not_found_later("Save", "File.Save"))
        assert later.targets == ["file"]


# ═══════════════════════════════════════════════════════════════════════
# Existing error texts and the Android guard
# ═══════════════════════════════════════════════════════════════════════


class TestExistingErrors:
    """The not-found texts and the Android refusal are unchanged."""

    def test_unknown_first_segment(self) -> None:
        run = run_menu("Nope.Save", _tree(SPACED, *GTK))

        _assert_error(run, _not_found("Nope", "Nope.Save"))
        assert run.sent == []

    def test_unknown_later_segment(self) -> None:
        run = run_menu("File.Nope", _tree(SPACED, *GTK))

        _assert_error(run, _not_found_later("Nope", "File.Nope"))
        assert run.targets == ["g_file"]

    def test_android_session_is_refused_before_any_tree_read_or_action(
        self,
    ) -> None:
        tree = _tree(SPACED, *GTK)

        run = run_menu(
            "File.Save",
            tree,
            tree_port=TreeSequence(tree),
            session=_android_session(),
        )

        _assert_error(
            run,
            "Menu traversal not supported on Android — use find + action instead",
        )
        assert run.reads == 0
        assert run.tree_loads == 0
        assert run.sent == []


# ═══════════════════════════════════════════════════════════════════════
# Spelling invariance
# ═══════════════════════════════════════════════════════════════════════

_FIXTURES: Dict[str, Tuple[N, ...]] = {
    "standard": STANDARD,
    "gtk": GTK,
    "qt": QT,
    "cross_menu": CROSS_MENU,
}

_INVARIANCE_CASES = (
    [
        ("standard", path)
        for path in ("File", "File.Save", "Edit.Preferences", "File.Nope", "Nope")
    ]
    + [
        ("gtk", path)
        for path in ("File", "File.Save", "File.Export.PDF", "File.Nope", "Nope")
    ]
    + [
        ("qt", path)
        for path in ("File", "File.Save", "File.Export.PDF", "File.Nope", "Nope")
    ]
    + [
        ("cross_menu", path)
        for path in (
            "File",
            "File.Save",
            "Edit.Preferences",
            "File.Export.PDF",
            "Window.Close",
            "Window.Save",
            "File.Nope",
            "Nope",
        )
    ]
)


class TestSpellingInvariance:
    """The same tree gives the same outcome in either role spelling."""

    @pytest.mark.parametrize("fixture, path", _INVARIANCE_CASES)
    def test_outcome_does_not_depend_on_role_spelling(
        self, fixture: str, path: str
    ) -> None:
        """Same ordered (node, action) pairs and the same result or error."""
        roots = _FIXTURES[fixture]

        underscore = _outcome(run_menu(path, _tree(UNDERSCORE, *roots)))
        spaced = _outcome(run_menu(path, _tree(SPACED, *roots)))

        assert spaced == underscore


# ═══════════════════════════════════════════════════════════════════════
# Refused actions
# ═══════════════════════════════════════════════════════════════════════


class TestRefusedAction:
    """An action the application refuses stops the traversal with an error
    naming the segment, the path, the node, the action and what it offers."""

    def test_refused_first_segment_stops_without_waiting(self) -> None:
        action = FakeAccessibilityAction(success=False, available_actions=["Press"])

        run = run_menu("File.Save", _tree(SPACED, *GTK), action_port=action)

        assert isinstance(run.error, RuntimeError), (
            f"expected an error, got result {run.result!r}"
        )
        _assert_refusal_message(
            str(run.error), ["'File'", "'File.Save'", "g_file", "click", "Press"]
        )
        assert run.sent == [("g_file", "click")]
        assert run.reads == 0
        assert run.sleeps == []

    def test_refused_final_segment_reports_an_error_not_success(self) -> None:
        action = RecordingAction(refuse={"g_save": ["Press"]})

        run = run_menu("File.Save", _tree(SPACED, *GTK), action_port=action)

        assert isinstance(run.error, RuntimeError), (
            f"expected an error, got result {run.result!r}"
        )
        _assert_refusal_message(
            str(run.error), ["'Save'", "'File.Save'", "g_save", "click", "Press"]
        )
        assert run.sent == [("g_file", "click"), ("g_save", "click")]
        assert run.result is None

    def test_refusal_without_offered_actions_says_none_were_reported(self) -> None:
        action = FakeAccessibilityAction(success=False)

        run = run_menu("File.Save", _tree(SPACED, *GTK), action_port=action)

        assert isinstance(run.error, RuntimeError), (
            f"expected an error, got result {run.result!r}"
        )
        _assert_refusal_message(
            str(run.error),
            ["none were reported", "'File'", "'File.Save'", "g_file", "click"],
        )
        assert run.sent == [("g_file", "click")]


# ═══════════════════════════════════════════════════════════════════════
# Action choice
# ═══════════════════════════════════════════════════════════════════════


class TestActionChoice:
    """Each node receives click, else Press, else ShowMenu, as the node's own
    action list offers them; click when it offers none of them."""

    @pytest.mark.parametrize("spelling", SPELLINGS)
    @pytest.mark.parametrize(
        "offered, expected",
        [
            (("Press", "click"), "click"),
            (("ShowMenu", "Press"), "Press"),
            (("ShowMenu",), "ShowMenu"),
            ((), "click"),
            (("SetFocus",), "click"),
            (("press",), "click"),
            (("showmenu",), "click"),
        ],
    )
    def test_action_preference(
        self, offered: Tuple[str, ...], expected: str, spelling: str
    ) -> None:
        tree = _tree(spelling, _frame(N("target", MENU_ITEM, "Target", offered)))

        run = run_menu("Target", tree)

        _assert_ok(run, "target", "Target")
        assert run.sent == [("target", expected)]

    def test_first_segment_uses_the_action_list_of_the_stored_tree(self) -> None:
        """The first segment is resolved in the tree saved by the last read."""
        stored = _tree(UNDERSCORE, _frame(N("file", MENU, "File", ("ShowMenu",))))
        fresh = _tree(UNDERSCORE, _frame(N("file", MENU, "File", ("click",))))

        run = run_menu("File", stored, tree_port=TreeSequence(fresh))

        assert run.sent == [("file", "ShowMenu")]

    def test_later_segment_uses_the_action_list_of_the_reread_tree(self) -> None:
        """A later segment is resolved in the tree re-read after the previous
        activation."""
        stored = _tree(
            UNDERSCORE,
            _frame(N("file", MENU, "File", children=(N("save", MENU_ITEM, "Save"),))),
        )
        reread = _tree(
            UNDERSCORE,
            _frame(
                N(
                    "file",
                    MENU,
                    "File",
                    children=(N("save", MENU_ITEM, "Save", ("Press",)),),
                )
            ),
        )

        run = run_menu("File.Save", stored, tree_port=TreeSequence(reread))

        assert run.sent == [("file", "click"), ("save", "Press")]


# ═══════════════════════════════════════════════════════════════════════
# Lookup scoped to the previous segment's node
# ═══════════════════════════════════════════════════════════════════════


class TestScopedLookup:
    """Every segment after the first is looked up only among the descendants
    of the node the previous segment resolved to."""

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_shared_item_name_resolves_inside_the_previous_menu(
        self, spelling: str
    ) -> None:
        run = run_menu("Window.Close", _tree(spelling, *CROSS_MENU))

        _assert_ok(run, "c_window_close", "Close")
        assert run.targets == ["c_window", "c_window_close"]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    @pytest.mark.parametrize(
        "path, missing, opened, elsewhere",
        [
            ("Window.Save", "Save", "c_window", "c_file_save"),
            ("File.Preferences", "Preferences", "c_file", "c_prefs"),
        ],
    )
    def test_item_that_exists_only_in_another_menu_is_not_found(
        self, path: str, missing: str, opened: str, elsewhere: str, spelling: str
    ) -> None:
        run = run_menu(path, _tree(spelling, *CROSS_MENU))

        _assert_error(run, _not_found_later(missing, path))
        assert elsewhere not in run.targets
        assert run.targets == [opened]
        assert run.reads == 3
        assert run.sleeps == [0.2, 0.2, 0.2]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_shared_submenu_name_resolves_inside_the_previous_menu(
        self, spelling: str
    ) -> None:
        run = run_menu("Edit.Export.PNG", _tree(spelling, *CROSS_MENU))

        _assert_ok(run, "c_png", "PNG")
        assert run.targets == ["c_edit", "c_edit_export", "c_png"]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_item_that_exists_only_in_another_menus_submenu_is_not_found(
        self, spelling: str
    ) -> None:
        run = run_menu("File.Export.PNG", _tree(spelling, *CROSS_MENU))

        _assert_error(run, _not_found_later("PNG", "File.Export.PNG"))
        assert run.targets == ["c_file", "c_file_export"]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_qt_shaped_shared_item_name_resolves_inside_the_previous_menu(
        self, spelling: str
    ) -> None:
        run = run_menu("Window.Close", _tree(spelling, *QT_CROSS_MENU))

        _assert_ok(run, "x_window_close", "Close")
        assert run.sent == [("x_window", "ShowMenu"), ("x_window_close", "Press")]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_qt_shaped_item_that_exists_only_in_another_menu_is_not_found(
        self, spelling: str
    ) -> None:
        run = run_menu("Window.Save", _tree(spelling, *QT_CROSS_MENU))

        _assert_error(run, _not_found_later("Save", "Window.Save"))
        assert run.sent == [("x_window", "ShowMenu")]

    def test_later_segment_may_name_an_item_inside_a_nested_submenu(self) -> None:
        """Descendants, not only children: the nested submenu is not opened."""
        run = run_menu("File.PDF", _tree(SPACED, *GTK))

        _assert_ok(run, "g_pdf", "PDF")
        assert run.targets == ["g_file", "g_pdf"]

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_previous_menu_missing_from_the_reread_tree_is_not_found(
        self, spelling: str
    ) -> None:
        """If the re-read tree no longer holds the previous segment's menu, a
        same-named item in another menu is not used."""
        stored = _tree(
            spelling,
            _frame(
                _bar(
                    N(
                        "file",
                        MENU,
                        "File",
                        children=(N("file_save", MENU_ITEM, "Save"),),
                    )
                )
            ),
        )
        reread = _tree(
            spelling,
            _frame(
                _bar(
                    N(
                        "edit",
                        MENU,
                        "Edit",
                        children=(N("edit_save", MENU_ITEM, "Save"),),
                    )
                )
            ),
        )

        run = run_menu("File.Save", stored, tree_port=TreeSequence(reread))

        _assert_error(run, _not_found_later("Save", "File.Save"))
        assert run.targets == ["file"]
        assert run.reads == 3

    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_wait_for_submenu_ignores_a_same_named_item_in_another_menu(
        self, spelling: str
    ) -> None:
        """The wait after opening File ends only when Save appears inside
        File, not when a Save appears in Edit first."""

        def menus(
            file_items: Tuple[N, ...], edit_items: Tuple[N, ...]
        ) -> AccessibilityTree:
            return _tree(
                spelling,
                _frame(
                    _bar(
                        N("file", MENU, "File", children=file_items),
                        N("edit", MENU, "Edit", children=edit_items),
                    )
                ),
            )

        edit_save = N("edit_save", MENU_ITEM, "Save")
        file_save = N("file_save", MENU_ITEM, "Save")
        stored = menus((), ())
        first_reread = menus((), (edit_save,))
        later_rereads = menus((file_save,), (edit_save,))

        run = run_menu(
            "File.Save", stored, tree_port=TreeSequence(first_reread, later_rereads)
        )

        _assert_ok(run, "file_save", "Save")
        assert run.targets == ["file", "file_save"]
        assert run.reads >= 2


# ═══════════════════════════════════════════════════════════════════════
# Entry points: CLI command and MCP tool
# ═══════════════════════════════════════════════════════════════════════

_OK_JSON = re.compile(r'"status"\s*:\s*"ok"')


def _invoke_cli(path: str, stored: AccessibilityTree, action_port: Any) -> Any:
    rig = _rig(stored, action_port=action_port)
    runner = make_cli_runner()
    with (
        patch("aiyes.cli.main.menu_uc", rig.use_case),
        patch("aiyes.cli.main.resolve_session_id", return_value=rig.session.session_id),
        patch("aiyes.cli.main.operation_log_adapter", MagicMock()),
    ):
        return runner.invoke(cli, ["menu", path])


async def _call_mcp_tool(
    path: str, stored: AccessibilityTree, action_port: Any
) -> Tuple[bool, str]:
    rig = _rig(stored, action_port=action_port)
    fields: Dict[str, Any] = {
        f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)
    }
    clock = MagicMock()
    clock.now.return_value = 1000.0
    fields.update(
        menu_uc=rig.use_case,
        resolve_session_id=MagicMock(return_value=rig.session.session_id),
        clock=clock,
        operation_log=MagicMock(),
    )
    server = create_mcp_server(ServerDependencies(**fields))
    result = await server.call_tool(
        "menu", {"menu_path": path, "session_id": rig.session.session_id}
    )
    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
    return result.isError, text


class TestMenuCliCommand:
    """``aieyes menu`` reaches the use case; output format is unchanged."""

    def test_success_prints_the_result_json(self) -> None:
        result = _invoke_cli("File.Save", _tree(SPACED, *GTK), RecordingAction())

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout) == {
            "status": "ok",
            "node_id": "g_save",
            "node_name": "Save",
        }

    def test_item_not_found_exits_1_with_the_message_on_stderr(self) -> None:
        result = _invoke_cli("File.Nope", _tree(SPACED, *GTK), RecordingAction())

        assert result.exit_code == 1
        assert _not_found("Nope", "File.Nope") in result.stderr
        assert not _OK_JSON.search(result.stdout)

    def test_refused_action_exits_1_with_the_refusal_on_stderr(self) -> None:
        action = FakeAccessibilityAction(success=False, available_actions=["Press"])

        result = _invoke_cli("File.Save", _tree(SPACED, *GTK), action)

        assert result.exit_code == 1
        assert result.stderr.startswith("Error: ")
        _assert_refusal_message(
            result.stderr, ["'File'", "'File.Save'", "g_file", "click", "Press"]
        )
        assert not _OK_JSON.search(result.stdout)


class TestMenuMcpTool:
    """The ``menu`` MCP tool reaches the use case; output format is unchanged."""

    @pytest.mark.asyncio
    async def test_success_returns_the_result_json(self) -> None:
        is_error, text = await _call_mcp_tool(
            "File.Save", _tree(SPACED, *GTK), RecordingAction()
        )

        assert is_error is False, text
        assert json.loads(text) == {
            "status": "ok",
            "node_id": "g_save",
            "node_name": "Save",
        }

    @pytest.mark.asyncio
    async def test_item_not_found_is_a_tool_error(self) -> None:
        is_error, text = await _call_mcp_tool(
            "File.Nope", _tree(SPACED, *GTK), RecordingAction()
        )

        assert is_error is True
        assert _not_found("Nope", "File.Nope") in text

    @pytest.mark.asyncio
    async def test_refused_action_is_a_tool_error(self) -> None:
        action = FakeAccessibilityAction(success=False, available_actions=["Press"])

        is_error, text = await _call_mcp_tool("File.Save", _tree(SPACED, *GTK), action)

        assert is_error is True
        _assert_refusal_message(
            text, ["'File'", "'File.Save'", "g_file", "click", "Press"]
        )
