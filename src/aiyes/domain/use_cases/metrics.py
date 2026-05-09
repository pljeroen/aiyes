"""Metrics use case — pure computation over operation records.

Computes per-command counts, percentiles, failure rates, and session duration.
No I/O in domain — all data access goes through OperationLogPort.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from aiyes.domain.operation_record import MetricsSummary, OperationRecord
from aiyes.ports.operation_log import OperationLogPort


class MetricsUseCase:
    """Compute metrics from operation log records."""

    def __init__(self, op_log: OperationLogPort) -> None:
        self._op_log = op_log

    def execute(self, session_id: Optional[str] = None) -> MetricsSummary:
        """Compute metrics for a session or aggregate across all sessions.

        session_id=None means aggregate across all sessions.
        session_id=<value> means per-session metrics.
        """
        if session_id is None:
            records = self._op_log.read_all()
        else:
            records = self._op_log.read(session_id)

        return self._compute(records, session_id)

    def _compute(
        self,
        records: List[OperationRecord],
        session_id: Optional[str],
    ) -> MetricsSummary:
        """Pure computation: records -> MetricsSummary."""
        if not records:
            return MetricsSummary(
                session_id=session_id,
                total_commands=0,
                command_counts=(),
                latency_p50=(),
                latency_p95=(),
                failure_rate=(),
                session_duration_s=0.0,
                period_start=0.0,
                period_end=0.0,
            )

        total_commands = len(records)

        # Group by command
        by_command: Dict[str, List[OperationRecord]] = {}
        for r in records:
            by_command.setdefault(r.command, []).append(r)

        # Command counts
        command_counts: List[Tuple[str, int]] = []
        for cmd, cmd_records in sorted(by_command.items()):
            command_counts.append((cmd, len(cmd_records)))

        # Percentiles per command
        latency_p50: List[Tuple[str, float]] = []
        latency_p95: List[Tuple[str, float]] = []
        for cmd, cmd_records in sorted(by_command.items()):
            durations = sorted(r.duration_ms for r in cmd_records)
            p50_idx = len(durations) // 2
            p95_idx = max(0, len(durations) - 1)
            latency_p50.append((cmd, durations[p50_idx]))
            latency_p95.append((cmd, durations[p95_idx]))

        # Failure rate per command
        failure_rate: List[Tuple[str, float]] = []
        for cmd, cmd_records in sorted(by_command.items()):
            failures = sum(1 for r in cmd_records if r.exit_code != 0)
            rate = failures / len(cmd_records) if cmd_records else 0.0
            failure_rate.append((cmd, rate))

        # Timestamps
        timestamps = [r.timestamp for r in records]
        period_start = min(timestamps)
        period_end = max(timestamps)

        # Session duration: max(ts) - min(ts) for per-session, 0.0 for aggregate
        if session_id is not None and len(records) > 1:
            session_duration_s = period_end - period_start
        else:
            session_duration_s = 0.0

        return MetricsSummary(
            session_id=session_id,
            total_commands=total_commands,
            command_counts=tuple(command_counts),
            latency_p50=tuple(latency_p50),
            latency_p95=tuple(latency_p95),
            failure_rate=tuple(failure_rate),
            session_duration_s=session_duration_s,
            period_start=period_start,
            period_end=period_end,
        )
