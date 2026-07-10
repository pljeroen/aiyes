"""Goto use case — navigate a linux browser session to a URL.

Linux/AT-SPI+xdotool address-bar automation, verified on Firefox. Locates the
address bar (role=="entry" AND name~"address"), focuses it via the "activate"
action, then selects-all, types the URL, and presses Return.

Safety (focus-before-type guard): if the session is non-linux, or the address
bar cannot be located, or the focusing "activate" action does not succeed, the
use case returns a status="error" result and emits ZERO keystrokes — a URL is
never typed into whatever control happens to hold focus.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from aiyes.domain.matching import name_matches
from aiyes.domain.tree import flatten_nodes
from aiyes.ports.accessibility_action import AccessibilityActionPort
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.input import InputPort
from aiyes.ports.storage import SessionRepositoryPort


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
        address bar, or a failed focus. Raises RuntimeError on a system error
        (session not found), matching every sibling use case.
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
        candidates = [
            node
            for node in flatten_nodes(domain_tree.roots)
            if node.role == "entry" and name_matches(node.name, "address")
        ]
        if not candidates:
            return GotoResult(
                status="error",
                session_id=session_id,
                action="goto",
                reason="Address bar not found (role=entry, name~address)",
            )

        node = candidates[0]
        registry = getattr(self._tree, "last_registry", None)
        action_result = self._action.do_action(
            session, node.id, "activate", None, registry
        )
        if not action_result.success:
            return GotoResult(
                status="error",
                session_id=session_id,
                action="goto",
                reason=(
                    "Could not focus address bar: 'activate' unavailable "
                    f"(available: {list(action_result.available_actions)})"
                ),
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
