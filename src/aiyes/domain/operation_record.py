"""Operation record value objects — OperationRecord, MetricsSummary, PruneResult.

All frozen dataclasses for immutability. Uses only stdlib imports.
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Tuple


@dataclasses.dataclass(frozen=True)
class OperationRecord:
    """Immutable record of a single CLI command invocation.

    Pure domain value object — no serialization format awareness.
    JSONL mapping lives in the adapter layer (file_operation_log.py).
    """

    timestamp: float
    session_id: str
    command: str
    duration_ms: float
    exit_code: int
    error: str = ""


@dataclasses.dataclass(frozen=True)
class MetricsSummary:
    """Immutable summary of operation metrics.

    Tuple-of-tuples for collection fields (frozen dataclass compatibility).
    """

    session_id: Optional[str]
    total_commands: int
    command_counts: Tuple[Tuple[str, int], ...]
    latency_p50: Tuple[Tuple[str, float], ...]
    latency_p95: Tuple[Tuple[str, float], ...]
    failure_rate: Tuple[Tuple[str, float], ...]
    session_duration_s: float
    period_start: float
    period_end: float


@dataclasses.dataclass(frozen=True)
class PruneResult:
    """Immutable result of a session prune operation."""

    pruned_count: int
    skipped_active: int
    dry_run: bool
    sessions_pruned: Tuple[str, ...]
    orphans_found: int = 0
    orphans_killed: int = 0
