"""Wait use case — poll until a node is found or timeout."""

from __future__ import annotations

import dataclasses
from typing import Optional, Tuple

from aiyes.domain.matching import name_matches, role_matches
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

        Timeout is NOT an error (exit 0).
        """
        if transient and absent:
            raise ValueError("transient and absent are mutually exclusive")

        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

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
                    # Never seen (transient) — a genuine "target never matched"
                    # timeout: attach the role-drift diagnostic (C2).
                    drift = find_role_drift(all_nodes, role, name_pattern)
                    return WaitResult(
                        found=False, timeout=True, role_drift=drift or None
                    )
                if absent:
                    return WaitResult(found=True, timeout=True)
                # Normal-mode "target never matched" timeout — attach role drift.
                drift = find_role_drift(all_nodes, role, name_pattern)
                return WaitResult(found=False, timeout=True, role_drift=drift or None)

            self._clock.sleep(poll_interval)
