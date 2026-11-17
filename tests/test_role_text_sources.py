"""Where a node's role text comes from, per backend.

Linux: the role is the text AT-SPI's get_role_name returns for the node,
unchanged, in both the one-shot worker and the persistent worker; an empty
value stays "".

Android: the role is the node's class attribute with one leading
"android.widget.", "android.view." or "android.webkit." removed; any other
class is kept whole, and a node without a class gets "".
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import types
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch
from xml.sax.saxutils import quoteattr

import pytest

from aiyes.adapters import atspi_persistent_worker as persistent_worker
from aiyes.adapters import atspi_subprocess_worker as worker
from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml
from aiyes.domain.tree import flatten_nodes, raw_tree_to_domain

_BUS = "unix:abstract=/tmp/dbus-role-text-test"


# ──────────────────────────────────────────────────────────────────────
# Linux: an in-memory AT-SPI desktop
# ──────────────────────────────────────────────────────────────────────


class _NoStates:
    def get_states(self) -> List[str]:
        return []


class _Accessible:
    """An AT-SPI accessible whose role name is scripted."""

    def __init__(
        self,
        role: Optional[str],
        name: str,
        children: Optional[List["_Accessible"]] = None,
    ):
        self._role = role
        self._name = name
        self._children = children or []

    def get_role_name(self) -> Optional[str]:
        return self._role

    def get_name(self) -> str:
        return self._name

    def get_component(self) -> None:
        return None

    def get_state_set(self) -> _NoStates:
        return _NoStates()

    def get_n_actions(self) -> int:
        return 0

    def get_action_name(self, index: int) -> str:  # pragma: no cover - no actions
        return ""

    def get_description(self) -> str:
        return ""

    def get_child_count(self) -> int:
        return len(self._children)

    def get_child_at_index(self, index: int) -> "_Accessible":
        return self._children[index]


class _Desktop:
    def __init__(self, roots: List[_Accessible]) -> None:
        self._roots = roots

    def get_child_count(self) -> int:
        return len(self._roots)

    def get_child_at_index(self, index: int) -> _Accessible:
        return self._roots[index]


def _gi_modules(desktop: _Desktop) -> Dict[str, types.ModuleType]:
    atspi = types.ModuleType("gi.repository.Atspi")
    atspi.get_desktop = lambda index: desktop  # type: ignore[attr-defined]
    repository = types.ModuleType("gi.repository")
    repository.Atspi = atspi  # type: ignore[attr-defined]
    gi = types.ModuleType("gi")
    gi.require_version = lambda namespace, version: None  # type: ignore[attr-defined]
    gi.repository = repository  # type: ignore[attr-defined]
    return {"gi": gi, "gi.repository": repository, "gi.repository.Atspi": atspi}


def _one_shot(roots: List[_Accessible]) -> Dict[str, Any]:
    """What the one-shot worker prints for its tree command, decoded."""
    printed = io.StringIO()
    with (
        patch.dict(sys.modules, _gi_modules(_Desktop(roots))),
        patch.object(worker, "_setup_env"),
        contextlib.redirect_stdout(printed),
    ):
        worker._do_tree(":99", _BUS)
    return json.loads(printed.getvalue())


def _persistent(roots: List[_Accessible]) -> Dict[str, Any]:
    """The persistent worker's answer to a get_tree request."""
    with (
        patch.dict(sys.modules, _gi_modules(_Desktop(roots))),
        patch.object(worker, "_setup_env"),
    ):
        return persistent_worker._dispatch("get_tree", {}, ":99", _BUS)


WORKERS: Dict[str, Callable[[List[_Accessible]], Dict[str, Any]]] = {
    "one-shot": _one_shot,
    "persistent": _persistent,
}


@pytest.mark.parametrize("worker_path", sorted(WORKERS))
@pytest.mark.parametrize(
    "role_name",
    ["push button", "button", "Button", "bouton à bascule", ""],
)
def test_linux_node_role_is_the_role_name_atspi_returns(
    worker_path: str, role_name: str
) -> None:
    roots = [_Accessible("frame", "Win", [_Accessible(role_name, "Child")])]

    walked = WORKERS[worker_path](roots)

    child = walked["tree"][0]["children"][0]
    assert child["name"] == "Child"
    assert child["role"] == role_name
    nodes = {n.name: n for n in flatten_nodes(raw_tree_to_domain(walked).roots)}
    assert nodes["Child"].role == role_name


# ──────────────────────────────────────────────────────────────────────
# Android: the uiautomator class attribute
# ──────────────────────────────────────────────────────────────────────


def _hierarchy(class_attr: Optional[str]) -> str:
    klass = "" if class_attr is None else f" class={quoteattr(class_attr)}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<hierarchy rotation="0">\n'
        f'  <node index="0" text="Item"{klass} clickable="true" enabled="true"'
        ' bounds="[0,0][100,50]" />\n'
        "</hierarchy>\n"
    )


@pytest.mark.parametrize(
    "class_attr, role",
    [
        ("android.widget.Button", "Button"),
        ("android.view.View", "View"),
        ("android.webkit.WebView", "WebView"),
        (
            "androidx.recyclerview.widget.RecyclerView",
            "androidx.recyclerview.widget.RecyclerView",
        ),
        ("com.example.Custom_Button", "com.example.Custom_Button"),
        (None, ""),
    ],
)
def test_android_node_role_is_the_class_without_its_platform_prefix(
    class_attr: Optional[str], role: str
) -> None:
    tree, _ = parse_uiautomator_xml(_hierarchy(class_attr))

    (item,) = flatten_nodes(tree.roots)
    assert item.name == "Item"
    assert item.role == role
