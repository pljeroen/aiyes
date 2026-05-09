"""Wait-stable use case — poll until the tree is structurally stable or timeout."""

from __future__ import annotations

import dataclasses
from typing import Tuple

from aiyes.domain.tree import compute_tree_diff, trees_structurally_equal
from aiyes.ports.accessibility_tree import AccessibilityTreePort
from aiyes.ports.clock import ClockPort
from aiyes.ports.storage import SessionRepositoryPort


@dataclasses.dataclass(frozen=True)
class WaitStableResult:
    """Result of a wait-stable operation."""

    stable: bool
    timeout: bool = False
    polls: int = 0
    changes: Tuple[dict, ...] = ()
    comparison_mode: str = "node_id"


class WaitStableUseCase:
    """Poll the accessibility tree until structurally stable or timeout."""

    def __init__(
        self,
        tree: AccessibilityTreePort,
        session_repo: SessionRepositoryPort,
        clock: ClockPort,
    ) -> None:
        self._tree = tree
        self._session_repo = session_repo
        self._clock = clock

    def execute(
        self,
        session_id: str,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
        consecutive: int = 3,
        tolerance: int = 0,
        ignore_ids: frozenset = frozenset(),
    ) -> WaitStableResult:
        """Wait for consecutive structurally identical tree polls.

        First poll establishes the baseline (does not increment the counter).
        Counter resets on structural change. Timeout returns a result, not
        an exception. If poll_interval <= 0, no sleep between polls.

        tolerance: max allowed node-level diffs for trees to be considered equal.
        ignore_ids: node IDs (and subtrees) to exclude from comparison.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        use_stable_ids = session.backend == "android"
        comparison_mode = "normalized_stable_id" if use_stable_ids else "node_id"
        start_time = self._clock.now()
        polls = 0

        # First poll: baseline
        prev_tree = self._tree.get_tree(session)
        polls += 1
        counter = 0
        # Track the last comparison pair for diagnostics on timeout
        last_compare_a = prev_tree
        last_compare_b = prev_tree

        while True:
            elapsed = self._clock.now() - start_time
            if elapsed >= timeout:
                # Compute diagnostic diff on the last comparison pair
                tree_diffs = compute_tree_diff(
                    last_compare_a,
                    last_compare_b,
                    ignore_ids,
                    use_stable_ids=use_stable_ids,
                )
                changes = tuple(dataclasses.asdict(d) for d in tree_diffs)
                return WaitStableResult(
                    stable=False,
                    timeout=True,
                    polls=polls,
                    changes=changes,
                    comparison_mode=comparison_mode,
                )

            if poll_interval > 0:
                self._clock.sleep(poll_interval)

            curr_tree = self._tree.get_tree(session)
            polls += 1

            # Record this comparison pair for timeout diagnostics
            last_compare_a = prev_tree
            last_compare_b = curr_tree

            if trees_structurally_equal(
                prev_tree,
                curr_tree,
                tolerance=tolerance,
                ignore_ids=ignore_ids,
                use_stable_ids=use_stable_ids,
            ):
                counter += 1
                if counter >= consecutive:
                    return WaitStableResult(
                        stable=True,
                        polls=polls,
                        comparison_mode=comparison_mode,
                    )
            else:
                counter = 0
                prev_tree = curr_tree
