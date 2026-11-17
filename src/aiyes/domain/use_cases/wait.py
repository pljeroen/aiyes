"""Wait use case — poll until a node is found or timeout."""

from __future__ import annotations

import dataclasses
from typing import Optional, Tuple

from aiyes.domain.matching import (
    android_description_refusal,
    description_matches,
    description_selector_given,
    descriptions_containing,
    name_matches,
    role_matches,
)
from aiyes.domain.tree import RoleDriftCandidate, find_role_drift, flatten_nodes
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.clock import ClockPort
from aiyes.ports.storage import SessionRepositoryPort
from aiyes.ports.tree_store import TreeStorePort


@dataclasses.dataclass(frozen=True)
class WaitResult:
    """Result of a wait operation."""

    found: bool
    timeout: bool = False
    id: Optional[str] = None
    transient: bool = False
    # AIYES-114 diagnostic: same-name-different-role candidates surfaced on the
    # two "target never matched" timeouts. The None default is LOAD-BEARING
    # (C4a): the scenario executor's generic _to_jsonable drops a None value but
    # would leak a ()-default as an always-present "role_drift": [] key. None on
    # every success / absent / present branch keeps those outputs byte-identical.
    role_drift: Optional[Tuple[RoleDriftCandidate, ...]] = None
    # Set only on the two "target never matched" timeouts of a description
    # query: up to five whole descriptions that contain the given text, among
    # the nodes of the last poll that pass the other selectors, and how many
    # such descriptions there are. None otherwise, like role_drift.
    descriptions_containing: Optional[Tuple[str, ...]] = None
    descriptions_containing_count: Optional[int] = None


class WaitUseCase:
    """Poll the accessibility tree until a matching node is found or timeout."""

    POLL_INTERVAL: float = 0.5
    TRANSIENT_POLL_INTERVAL: float = 0.2
    DEFAULT_TIMEOUT: float = 30.0

    def __init__(
        self,
        tree: AccessibilityTreePort,
        session_repo: SessionRepositoryPort,
        tree_store: TreeStorePort,
        clock: ClockPort,
    ) -> None:
        self._tree = tree
        self._session_repo = session_repo
        self._tree_store = tree_store
        self._clock = clock

    def execute(
        self,
        session_id: str,
        role: str,
        name_pattern: Optional[str] = None,
        timeout: Optional[float] = None,
        state: Optional[str] = None,
        absent: bool = False,
        transient: bool = False,
        description_pattern: Optional[str] = None,
    ) -> WaitResult:
        """Wait for a node matching role/name/state to appear or disappear.

        When absent=False (default): polls until a matching node is found.
        Returns WaitResult(found=True, id=matched_id) on success,
        or WaitResult(found=False, timeout=True) on timeout.

        When absent=True: polls until NO matching node exists.
        Returns WaitResult(found=False, timeout=False) on success (node absent),
        or WaitResult(found=True, timeout=True) on timeout (node still present).

        When transient=True: polls at 200ms, accumulates all seen node IDs.
        If target was seen at any point but is now gone at timeout:
        returns WaitResult(found=True, transient=True, id=first_seen_id).
        Mutually exclusive with absent.

        ``description_pattern``, when given (description_selector_given),
        narrows each poll's match to nodes whose whole description matches it
        (description_matches), together with the other selectors. On the two
        "never matched" timeouts the result then also lists the descriptions
        that contain it among the last poll's nodes that pass the other
        selectors. On an Android session a given description selector is
        refused before the first poll. A selector that is not given changes
        nothing.

        Timeout is NOT an error (exit 0).
        """
        if transient and absent:
            raise ValueError("transient and absent are mutually exclusive")

        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")
        refusal = android_description_refusal(session.backend, description_pattern)
        if refusal is not None:
            raise RuntimeError(refusal)
        described = description_selector_given(description_pattern)

        poll_interval = (
            self.TRANSIENT_POLL_INTERVAL if transient else self.POLL_INTERVAL
        )
        seen_ids: list = []  # ordered list of first-seen IDs (transient mode)
        start_time = self._clock.now()

        while True:
            # Port returns AccessibilityTree domain type
            domain_tree = self._tree.get_tree(session)
            nodes = flatten_nodes(domain_tree.roots)

            # AIYES-114: retain the pre-role-filter pool (this poll's snapshot)
            # for the role-drift diagnostic used at the "never matched" timeouts.
            all_nodes = nodes

            # Filter by role
            if role != "*":
                nodes = [n for n in nodes if role_matches(n.role, role)]

            # Filter by name pattern (whitespace-normalized)
            if name_pattern is not None:
                nodes = [n for n in nodes if name_matches(n.name, name_pattern)]

            # Filter by state
            if state is not None:
                nodes = [n for n in nodes if state in n.states]

            # The nodes that pass every selector but the description; a given
            # description selector then keeps those whose whole description
            # matches.
            other_selected = nodes
            if described and description_pattern is not None:
                wanted = description_pattern
                nodes = [n for n in nodes if description_matches(n.description, wanted)]

            if transient:
                # Accumulate seen IDs across all polls
                for n in nodes:
                    if n.id not in seen_ids:
                        seen_ids.append(n.id)
                # Continue polling until timeout to detect transient appearance
            elif absent:
                # Absent mode: success when no nodes match
                if not nodes:
                    return WaitResult(found=False, timeout=False)
            else:
                # Normal mode: success when a node matches
                if nodes:
                    matched = nodes[0]
                    return WaitResult(
                        found=True,
                        timeout=False,
                        id=matched.id,
                    )

            elapsed = self._clock.now() - start_time
            if elapsed >= timeout:
                if transient:
                    if nodes:
                        # Node currently present at timeout
                        return WaitResult(
                            found=True,
                            timeout=False,
                            id=nodes[0].id,
                            transient=False,
                        )
                    if seen_ids:
                        # Node was seen but is now gone
                        return WaitResult(
                            found=True,
                            timeout=False,
                            transient=True,
                            id=seen_ids[0],
                        )
                if absent:
                    return WaitResult(found=True, timeout=True)
                # A "target never matched" timeout (normal mode, or transient
                # mode with nothing seen): attach the role-drift diagnostic and,
                # for a description query, the descriptions that contain it.
                drift = find_role_drift(
                    all_nodes, role, name_pattern, description_pattern
                )
                listed: Optional[Tuple[str, ...]] = None
                listed_count: Optional[int] = None
                if described and description_pattern is not None:
                    texts, count = descriptions_containing(
                        (n.description for n in other_selected), description_pattern
                    )
                    if count:
                        listed, listed_count = texts, count
                return WaitResult(
                    found=False,
                    timeout=True,
                    role_drift=drift or None,
                    descriptions_containing=listed,
                    descriptions_containing_count=listed_count,
                )

            self._clock.sleep(poll_interval)
