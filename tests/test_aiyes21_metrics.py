"""AIYES-21 Metrics tests — RED phase.

Tests for MetricsSummary value object and MetricsUseCase domain logic.
These tests MUST fail because the production code does not exist yet.

Traceability — Formal Constraint Map:
  FC-AIYES21-001: Domain purity (use_cases/metrics.py)
  FC-AIYES21-009: MetricsSummary immutability
  FC-AIYES21-013: Metrics are pure computation

Requirement coverage:
  REQ-AIYES21-014: MetricsSummary frozen dataclass
  REQ-AIYES21-015: MetricsUseCase per-session and aggregate
  REQ-AIYES21-016: Percentile computation (p50, p95)
  REQ-AIYES21-017: Empty records -> zero MetricsSummary
  REQ-AIYES21-018: Session duration (max ts - min ts)
  REQ-AIYES21-029: Domain purity
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# These imports will fail (RED) — production modules do not exist yet.
from aiyes.domain.operation_record import MetricsSummary, OperationRecord
from aiyes.domain.use_cases.metrics import MetricsUseCase


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_record(**overrides: Any) -> OperationRecord:
    """Create an OperationRecord with sensible defaults."""
    defaults = dict(
        timestamp=1000.0,
        session_id="test-session-001",
        command="inspect",
        duration_ms=42.5,
        exit_code=0,
        error="",
    )
    defaults.update(overrides)
    return OperationRecord(**defaults)


class FakeOperationLog:
    """In-memory fake for OperationLogPort — structural typing."""

    def __init__(self, records: Optional[List[OperationRecord]] = None) -> None:
        self._records: List[OperationRecord] = records or []
        self.calls: List[Tuple[str, Any]] = []

    def append(self, record: OperationRecord) -> None:
        self.calls.append(("append", record))
        self._records.append(record)

    def read(self, session_id: str) -> List[OperationRecord]:
        self.calls.append(("read", session_id))
        return [r for r in self._records if r.session_id == session_id]

    def read_all(self) -> List[OperationRecord]:
        self.calls.append(("read_all", None))
        return list(self._records)

    def list_session_ids(self) -> List[str]:
        self.calls.append(("list_session_ids", None))
        seen: List[str] = []
        for r in self._records:
            sid = r.session_id if r.session_id else "_global"
            if sid not in seen:
                seen.append(sid)
        return seen


# ═══════════════════════════════════════════════════════════════════════
# MetricsSummary creation and immutability (FC-AIYES21-009, REQ-AIYES21-014)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsSummaryCreation:
    """MetricsSummary is a frozen dataclass with tuple fields."""

    def test_creation_with_all_fields(self) -> None:
        """REQ-AIYES21-014: MetricsSummary holds all specified fields."""
        summary = MetricsSummary(
            session_id=None,
            total_commands=5,
            command_counts=(("inspect", 3), ("find", 2)),
            latency_p50=(("inspect", 10.0), ("find", 5.0)),
            latency_p95=(("inspect", 50.0), ("find", 20.0)),
            failure_rate=(("inspect", 0.0), ("find", 0.5)),
            session_duration_s=100.0,
            period_start=1000.0,
            period_end=1100.0,
        )
        assert summary.session_id is None
        assert summary.total_commands == 5
        assert summary.command_counts == (("inspect", 3), ("find", 2))
        assert summary.session_duration_s == 100.0

    def test_frozen(self) -> None:
        """FC-AIYES21-009: MetricsSummary is immutable."""
        summary = MetricsSummary(
            session_id=None,
            total_commands=0,
            command_counts=(),
            latency_p50=(),
            latency_p95=(),
            failure_rate=(),
            session_duration_s=0.0,
            period_start=0.0,
            period_end=0.0,
        )
        assert MetricsSummary.__dataclass_params__.frozen is True
        with pytest.raises(AttributeError):
            summary.total_commands = 99  # type: ignore[misc]

    def test_session_id_is_optional(self) -> None:
        """REQ-AIYES21-014: session_id is Optional[str]."""
        # None for aggregate
        s1 = MetricsSummary(
            session_id=None,
            total_commands=0,
            command_counts=(),
            latency_p50=(),
            latency_p95=(),
            failure_rate=(),
            session_duration_s=0.0,
            period_start=0.0,
            period_end=0.0,
        )
        assert s1.session_id is None

        # String for per-session
        s2 = MetricsSummary(
            session_id="s1",
            total_commands=0,
            command_counts=(),
            latency_p50=(),
            latency_p95=(),
            failure_rate=(),
            session_duration_s=0.0,
            period_start=0.0,
            period_end=0.0,
        )
        assert s2.session_id == "s1"

    def test_collection_fields_are_tuples(self) -> None:
        """FC-AIYES21-009: Tuple fields (not list) for frozen compatibility."""
        summary = MetricsSummary(
            session_id=None,
            total_commands=0,
            command_counts=(("inspect", 3),),
            latency_p50=(("inspect", 10.0),),
            latency_p95=(("inspect", 50.0),),
            failure_rate=(("inspect", 0.0),),
            session_duration_s=0.0,
            period_start=0.0,
            period_end=0.0,
        )
        assert isinstance(summary.command_counts, tuple)
        assert isinstance(summary.latency_p50, tuple)
        assert isinstance(summary.latency_p95, tuple)
        assert isinstance(summary.failure_rate, tuple)


# ═══════════════════════════════════════════════════════════════════════
# MetricsUseCase — aggregate (REQ-AIYES21-015)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsUseCaseAggregate:
    """MetricsUseCase with session_id=None aggregates across all sessions."""

    def test_aggregate_total_commands(self) -> None:
        """REQ-AIYES21-015: Aggregate total equals record count."""
        records = [
            _make_record(session_id="s1", command="inspect"),
            _make_record(session_id="s1", command="find"),
            _make_record(session_id="s2", command="inspect"),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id=None)

        assert result.total_commands == 3
        assert result.session_id is None

    def test_aggregate_command_counts(self) -> None:
        """REQ-AIYES21-015: Command frequency across all sessions."""
        records = [
            _make_record(session_id="s1", command="inspect"),
            _make_record(session_id="s1", command="inspect"),
            _make_record(session_id="s2", command="find"),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id=None)

        counts_dict = dict(result.command_counts)
        assert counts_dict["inspect"] == 2
        assert counts_dict["find"] == 1

    def test_aggregate_uses_read_all(self) -> None:
        """FC-AIYES21-013: Aggregate calls read_all, not individual reads."""
        records = [_make_record(session_id="s1")]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        uc.execute(session_id=None)

        call_names = [c[0] for c in fake_log.calls]
        assert "read_all" in call_names

    def test_aggregate_no_write_calls(self) -> None:
        """A10-007, FC-AIYES21-013: Aggregate metrics makes no write-side calls."""
        records = [_make_record(session_id="s1")]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        uc.execute(session_id=None)

        write_methods = {"append"}
        call_names = [c[0] for c in fake_log.calls]
        for name in call_names:
            assert name not in write_methods, f"Unexpected write-side call: {name}"

    def test_aggregate_session_duration_is_zero(self) -> None:
        """REQ-AIYES21-018: Aggregate mode -> session_duration_s=0.0."""
        records = [
            _make_record(session_id="s1", timestamp=100.0),
            _make_record(session_id="s2", timestamp=200.0),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id=None)
        assert result.session_duration_s == 0.0

    def test_deterministic_same_input_same_output(self) -> None:
        """FC-AIYES21-013: Same input -> same output (deterministic)."""
        records = [
            _make_record(session_id="s1", command="inspect", duration_ms=10.0),
            _make_record(session_id="s1", command="find", duration_ms=20.0),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        r1 = uc.execute(session_id=None)
        r2 = uc.execute(session_id=None)
        assert r1 == r2


# ═══════════════════════════════════════════════════════════════════════
# MetricsUseCase — per session (REQ-AIYES21-015)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsUseCasePerSession:
    """MetricsUseCase with session_id filters to that session."""

    def test_per_session_filters(self) -> None:
        """REQ-AIYES21-015: Per-session returns only that session's records."""
        records = [
            _make_record(session_id="s1", command="inspect"),
            _make_record(session_id="s2", command="find"),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")

        assert result.session_id == "s1"
        assert result.total_commands == 1
        counts_dict = dict(result.command_counts)
        assert counts_dict.get("inspect") == 1
        assert "find" not in counts_dict

    def test_per_session_uses_read(self) -> None:
        """FC-AIYES21-013: Per-session calls read(session_id)."""
        records = [_make_record(session_id="s1")]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        uc.execute(session_id="s1")

        call_names = [c[0] for c in fake_log.calls]
        assert "read" in call_names

    def test_per_session_no_write_calls(self) -> None:
        """A10-007, FC-AIYES21-013: Per-session metrics makes no write-side calls."""
        records = [_make_record(session_id="s1")]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        uc.execute(session_id="s1")

        write_methods = {"append"}
        call_names = [c[0] for c in fake_log.calls]
        for name in call_names:
            assert name not in write_methods, f"Unexpected write-side call: {name}"

    def test_per_session_computes_session_duration(self) -> None:
        """REQ-AIYES21-018: Two records 10s apart -> session_duration_s=10.0."""
        records = [
            _make_record(session_id="s1", timestamp=100.0),
            _make_record(session_id="s1", timestamp=110.0),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")
        assert result.session_duration_s == 10.0


# ═══════════════════════════════════════════════════════════════════════
# MetricsUseCase — percentiles (REQ-AIYES21-016)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsUseCasePercentiles:
    """p50 and p95 computed from sorted duration_ms per command."""

    def test_percentiles_20_records(self) -> None:
        """REQ-AIYES21-016: 20 records -> p50 and p95 at correct indices."""
        durations = list(range(1, 21))  # 1..20 ms
        records = [
            _make_record(
                session_id="s1",
                command="inspect",
                duration_ms=float(d),
                timestamp=1000.0 + d,
            )
            for d in durations
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")

        p50_dict = dict(result.latency_p50)
        p95_dict = dict(result.latency_p95)

        # Sorted: [1,2,...,20]. p50 = index 10 (0-indexed) = 11.0
        # p95 = index 19 (0-indexed) = 20.0
        assert p50_dict["inspect"] == 11.0
        assert p95_dict["inspect"] == 20.0

    def test_single_record_p50_equals_p95(self) -> None:
        """REQ-AIYES21-016: Single record -> p50 = p95 = that value."""
        records = [_make_record(session_id="s1", command="find", duration_ms=42.0)]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")

        p50_dict = dict(result.latency_p50)
        p95_dict = dict(result.latency_p95)
        assert p50_dict["find"] == 42.0
        assert p95_dict["find"] == 42.0


# ═══════════════════════════════════════════════════════════════════════
# MetricsUseCase — empty records (REQ-AIYES21-017)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsUseCaseEmpty:
    """Empty records -> zero MetricsSummary."""

    def test_zero_records_produces_zero_summary(self) -> None:
        """REQ-AIYES21-017: Empty record list yields all-zero defaults."""
        fake_log = FakeOperationLog([])
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id=None)

        assert result.total_commands == 0
        assert result.command_counts == ()
        assert result.latency_p50 == ()
        assert result.latency_p95 == ()
        assert result.failure_rate == ()
        assert result.session_duration_s == 0.0
        assert result.period_start == 0.0
        assert result.period_end == 0.0

    def test_per_session_no_records(self) -> None:
        """REQ-AIYES21-017: Per-session with no records for that session."""
        fake_log = FakeOperationLog([])
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="nonexistent")
        assert result.total_commands == 0
        assert result.session_id == "nonexistent"


# ═══════════════════════════════════════════════════════════════════════
# MetricsUseCase — failure rate
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsUseCaseFailureRate:
    """Failure rate is computed per command."""

    def test_failure_rate_mixed(self) -> None:
        """Failure rate = failures / total per command."""
        records = [
            _make_record(session_id="s1", command="inspect", exit_code=0),
            _make_record(session_id="s1", command="inspect", exit_code=1, error="fail"),
            _make_record(session_id="s1", command="inspect", exit_code=0),
            _make_record(session_id="s1", command="find", exit_code=1, error="boom"),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")

        rate_dict = dict(result.failure_rate)
        # inspect: 1 fail / 3 total = ~0.333
        assert abs(rate_dict["inspect"] - (1.0 / 3.0)) < 0.001
        # find: 1 fail / 1 total = 1.0
        assert rate_dict["find"] == 1.0

    def test_all_success_rate_zero(self) -> None:
        """All successes -> failure rate 0.0 for each command."""
        records = [
            _make_record(session_id="s1", command="inspect", exit_code=0),
            _make_record(session_id="s1", command="inspect", exit_code=0),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")

        rate_dict = dict(result.failure_rate)
        assert rate_dict["inspect"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# MetricsUseCase — session duration (REQ-AIYES21-018)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsUseCaseSessionDuration:
    """session_duration_s = max(ts) - min(ts)."""

    def test_two_records_10s_apart(self) -> None:
        """REQ-AIYES21-018: 10 seconds between first and last record."""
        records = [
            _make_record(session_id="s1", timestamp=100.0),
            _make_record(session_id="s1", timestamp=110.0),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")
        assert result.session_duration_s == 10.0

    def test_single_record_zero_duration(self) -> None:
        """REQ-AIYES21-018: Single record -> 0.0."""
        records = [_make_record(session_id="s1", timestamp=100.0)]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")
        assert result.session_duration_s == 0.0

    def test_period_start_and_end(self) -> None:
        """REQ-AIYES21-014: period_start=min(ts), period_end=max(ts)."""
        records = [
            _make_record(session_id="s1", timestamp=100.0),
            _make_record(session_id="s1", timestamp=200.0),
            _make_record(session_id="s1", timestamp=150.0),
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")
        assert result.period_start == 100.0
        assert result.period_end == 200.0


# ═══════════════════════════════════════════════════════════════════════
# PBT: Metrics consistency (FC-AIYES21-013)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsUseCasePBT:
    """Property-based: metrics from N records always has consistent totals."""

    @given(
        n=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100)
    def test_total_commands_equals_record_count(self, n: int) -> None:
        """PBT: total_commands always equals number of input records."""
        records = [
            OperationRecord(
                timestamp=1000.0 + i,
                session_id="s1",
                command="inspect",
                duration_ms=float(i + 1),
                exit_code=0,
                error="",
            )
            for i in range(n)
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")
        assert result.total_commands == n

    @given(
        n=st.integers(min_value=2, max_value=50),
    )
    @settings(max_examples=100)
    def test_command_count_sum_equals_total(self, n: int) -> None:
        """PBT: Sum of command_counts always equals total_commands."""
        records = [
            OperationRecord(
                timestamp=1000.0 + i,
                session_id="s1",
                command="inspect" if i % 2 == 0 else "find",
                duration_ms=float(i + 1),
                exit_code=0,
                error="",
            )
            for i in range(n)
        ]
        fake_log = FakeOperationLog(records)
        uc = MetricsUseCase(op_log=fake_log)

        result = uc.execute(session_id="s1")
        count_sum = sum(v for _, v in result.command_counts)
        assert count_sum == result.total_commands


# ═══════════════════════════════════════════════════════════════════════
# Domain purity (FC-AIYES21-001, REQ-AIYES21-029)
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsDomainPurity:
    """domain/use_cases/metrics.py has no external imports."""

    def test_no_non_stdlib_imports(self) -> None:
        """REQ-AIYES21-029: AST check for stdlib + domain/ports only imports."""
        source_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "aiyes"
            / "domain"
            / "use_cases"
            / "metrics.py"
        )
        assert source_path.exists(), f"File not found: {source_path}"

        source = source_path.read_text()
        tree = ast.parse(source)

        allowed_prefixes = ("__future__", "dataclasses", "typing", "collections")
        allowed_domain = ("aiyes.domain.", "aiyes.ports.")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    assert any(
                        name == p or name.startswith(p + ".") for p in allowed_prefixes
                    ) or any(name.startswith(dp) for dp in allowed_domain), (
                        f"Forbidden import: {name}"
                    )

            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                module = node.module
                assert any(
                    module == p or module.startswith(p + ".") for p in allowed_prefixes
                ) or any(module.startswith(dp) for dp in allowed_domain), (
                    f"Forbidden import from: {module}"
                )
