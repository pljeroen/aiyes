"""Goto use case — navigate a linux browser session to a URL.

Linux/AT-SPI+xdotool address-bar automation. The address bar is the first
node, in tree order, that is an "entry", or a "combo box" inside a "tool bar",
whose accessible name contains "address" (case-insensitive). Raw AT-SPI and
canonical role spellings are equivalent ("combo box" and "combo_box").
Firefox 159 exposes its address bar as a combo box inside its navigation tool
bar.

Controls inside the web page are never candidates: a node that has an
ancestor with a document role (document web, document frame, document text,
document email, document presentation or document spreadsheet) is page
content, whatever its role, name, actions or place in the tree. This covers
the loaded page, frames inside it and the documents of other tabs.

The address bar is focused via the "activate" action, then the use case
selects all, types the URL, and presses Return. Verified on Firefox 159 with
an English user interface; other browsers and other interface languages are
not verified.

Safety (focus-before-type guard): if the session is non-linux, or no address
bar is found, or the focusing "activate" action does not succeed, the use case
returns a status="error" result and emits ZERO keystrokes — a URL is never
typed into page content or into whatever control happens to hold focus.
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Tuple

from aiyes.domain.matching import name_matches, role_matches
from aiyes.domain.tree import Node
from aiyes.domain.types import ACTION_DECLINED, ACTION_NOT_ENABLED, ActionPortResult
from aiyes.ports.accessibility_action import AccessibilityActionPort
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.input import InputPort
from aiyes.ports.storage import SessionRepositoryPort


# Document roles, in canonical spelling. The browser places every loaded
# document (the page, a frame inside it, the page of another tab) under a node
# with one of these roles, so every node below such a node is page content.
# Node roles are compared with role_matches, so the spaced spellings AT-SPI
# reports ("document web", ...) match as well.
_DOCUMENT_ROLES = frozenset(
    [
        "document_web",
        "document_frame",
        "document_text",
        "document_email",
        "document_presentation",
        "document_spreadsheet",
    ]
)

_ADDRESS_BAR_NOT_FOUND = (
    "Address bar not found (role=entry, or combo box in a tool bar; "
    "name~address; outside page content)"
)


def _is_document(node: Node) -> bool:
    """Return whether ``node`` has a document role (it holds page content)."""
    return any(role_matches(node.role, role) for role in _DOCUMENT_ROLES)


def _is_address_bar(node: Node, inside_tool_bar: bool) -> bool:
    """Return whether ``node``, which is not page content, is an address bar.

    An "entry" qualifies anywhere outside page content; a "combo box" only
    when ``inside_tool_bar`` says one of its ancestors is a "tool bar". The
    accessible name must contain "address".
    """
    if not name_matches(node.name, "address"):
        return False
    if role_matches(node.role, "entry"):
        return True
    return inside_tool_bar and role_matches(node.role, "combo_box")


def _find_address_bar(
    nodes: Tuple[Node, ...], inside_tool_bar: bool = False
) -> Optional[Node]:
    """Return the first address bar among ``nodes`` and their descendants.

    Searches in preorder, the order of flatten_nodes, and returns the first
    node for which _is_address_bar holds. A document node and everything below
    it are skipped, so the result is never page content, whatever the order of
    the browser chrome and the page in the tree. ``inside_tool_bar`` tells
    whether an ancestor of ``nodes`` is a "tool bar".
    """
    for node in nodes:
        if _is_document(node):
            continue
        if _is_address_bar(node, inside_tool_bar):
            return node
        found = _find_address_bar(
            node.children,
            inside_tool_bar or role_matches(node.role, "tool_bar"),
        )
        if found is not None:
            return found
    return None


def _focus_failure_reason(node_id: str, result: ActionPortResult) -> str:
    """Describe why the address bar's "activate" action did not succeed."""
    if result.failure_reason == ACTION_NOT_ENABLED:
        return f"Could not focus address bar: node '{node_id}' is not enabled"
    if result.failure_reason == ACTION_DECLINED:
        return "Could not focus address bar: 'activate' was declined by the application"
    return (
        "Could not focus address bar: 'activate' unavailable "
        f"(available: {list(result.available_actions)})"
    )


@dataclasses.dataclass(frozen=True)
class GotoResult:
    """Result of a goto (address-bar navigation) operation."""

    status: str
    session_id: str
    action: str = "goto"
    url: Optional[str] = None
    reason: Optional[str] = None


class GotoUseCase:
    """Navigate a linux browser session to a URL via address-bar automation."""

    def __init__(
        self,
        tree_port: AccessibilityTreePort,
        action_port: AccessibilityActionPort,
        input_port: InputPort,
        session_repo: SessionRepositoryPort,
    ) -> None:
        self._tree = tree_port
        self._action = action_port
        self._input = input_port
        self._session_repo = session_repo

    def execute(self, session_id: str, url: str) -> GotoResult:
        """Navigate the resolved session to url via the address bar.

        Returns GotoResult(status="ok", url=url) on success, or
        status="error" with a reason for a non-linux backend, an unlocatable
        address bar, or a failed focus (the address bar is not enabled, the
        application declined "activate", or "activate" is not available).
        Raises RuntimeError on a system error (session not found), matching
        every sibling use case.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        backend = getattr(session, "backend", "linux")
        if backend != "linux":
            return GotoResult(
                status="error",
                session_id=session_id,
                action="goto",
                reason=(
                    "goto is a linux browser-session primitive; "
                    f"not applicable to backend={backend!r}"
                ),
            )

        domain_tree = self._tree.get_tree(session)
        node = _find_address_bar(domain_tree.roots)
        if node is None:
            return GotoResult(
                status="error",
                session_id=session_id,
                action="goto",
                reason=_ADDRESS_BAR_NOT_FOUND,
            )

        registry = getattr(self._tree, "last_registry", None)
        action_result = self._action.do_action(
            session, node.id, "activate", None, registry
        )
        if not action_result.success:
            return GotoResult(
                status="error",
                session_id=session_id,
                action="goto",
                reason=_focus_failure_reason(node.id, action_result),
            )

        # Focus confirmed — select existing address text, type URL, submit.
        self._input.key(session, ["ctrl+a"])
        self._input.type_text(session, url)
        self._input.key(session, ["Return"])

        return GotoResult(
            status="ok",
            session_id=session_id,
            action="goto",
            url=url,
        )
