"""AIYES-21 CLI instrumentation tests — RED phase.

Tests for timing instrumentation, session attribution, and new CLI
subcommands (metrics, prune). These tests MUST fail because the
production code changes do not exist yet.

Traceability — Formal Constraint Map:
  FC-AIYES21-011: Universal command logging
  FC-AIYES21-017: Session-less commands log to _global
  FC-AIYES21-018: Failed commands before resolution log to _global
  FC-AIYES21-019: Session-creating dual-path attribution
  FC-AIYES21-022: Log append failure swallowed

Requirement coverage:
  REQ-AIYES21-005: Session-less commands to _global
  REQ-AIYES21-006: Session start success/failure logging
  REQ-AIYES21-007: Failed resolution -> _global
  REQ-AIYES21-011: Append failure swallowed
  REQ-AIYES21-013: Timing instrumentation
  REQ-AIYES21-019: session metrics subcommand
  REQ-AIYES21-028: session prune subcommand
"""

from __future__ import annotations

import json
from typing import List
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from aiyes.domain.operation_record import OperationRecord

# CLI entry point — already exists
from aiyes.cli.main import cli


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


class SpyOperationLog:
    """Spy that records all append calls for assertion."""

    def __init__(self, fail_on_append: bool = False) -> None:
        self.appended: List[OperationRecord] = []
        self._fail_on_append = fail_on_append

    def append(self, record: OperationRecord) -> None:
        if self._fail_on_append:
            raise IOError("disk full")
        self.appended.append(record)

    def read(self, session_id: str) -> List[OperationRecord]:
        return [r for r in self.appended if r.session_id == session_id]

    def read_all(self) -> List[OperationRecord]:
        return list(self.appended)

    def list_session_ids(self) -> List[str]:
        seen: List[str] = []
        for r in self.appended:
            sid = r.session_id if r.session_id else "_global"
            if sid not in seen:
                seen.append(sid)
        return seen


# ═══════════════════════════════════════════════════════════════════════
# Timing instrumentation (FC-AIYES21-011, REQ-AIYES21-013)
# ═══════════════════════════════════════════════════════════════════════


class TestTimingInstrumentationAllCommands:
    """Every CLI command handler calls operation_log.append() exactly once."""

    def test_session_list_logs_operation(self) -> None:
        """REQ-AIYES21-013: session list produces an OperationRecord."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_uc,
            patch("aiyes.cli.composition_root.operation_log_adapter", spy),
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            result = runner.invoke(cli, ["session", "list"])

        assert len(spy.appended) == 1
        assert spy.appended[0].command == "session list"
        assert spy.appended[0].session_id == ""

    def test_doctor_logs_operation(self) -> None:
        """REQ-AIYES21-013: doctor produces an OperationRecord."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.doctor_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            result = runner.invoke(cli, ["doctor"])

        assert len(spy.appended) == 1
        assert spy.appended[0].command == "doctor"

    def test_inspect_logs_operation(self) -> None:
        """REQ-AIYES21-013: inspect produces an OperationRecord."""
        spy = SpyOperationLog()
        runner = CliRunner()

        mock_result = MagicMock()
        mock_result.tree = None
        mock_result.screenshot = None
        mock_result.timestamp = 1000.0
        mock_result.screenshot_base64 = False
        mock_result.screenshot_data = None

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="s1"),
            patch("aiyes.cli.main.inspect_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(cli, ["inspect"])

        assert len(spy.appended) == 1
        assert spy.appended[0].command == "inspect"
        assert spy.appended[0].session_id == "s1"

    def test_operation_record_has_duration_ms(self) -> None:
        """REQ-AIYES21-013: OperationRecord has non-negative duration_ms."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            runner.invoke(cli, ["session", "list"])

        assert spy.appended[0].duration_ms >= 0.0

    def test_operation_record_has_exit_code_zero_on_success(self) -> None:
        """REQ-AIYES21-013: Successful command -> exit_code=0."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            runner.invoke(cli, ["session", "list"])

        assert spy.appended[0].exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# Parametrized all-22-handlers instrumentation (FC-AIYES21-011, A10-004)
# ═══════════════════════════════════════════════════════════════════════


def _mk(**attrs):
    """Create a MagicMock with explicit attributes."""
    m = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _metrics_zero():
    from aiyes.domain.operation_record import MetricsSummary

    return MetricsSummary(
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


def _prune_zero():
    from aiyes.domain.operation_record import PruneResult

    return PruneResult(
        pruned_count=0,
        skipped_active=0,
        dry_run=False,
        sessions_pruned=(),
    )


class TestAll22HandlersInstrumented:
    """FC-AIYES21-011: Every CLI handler logs exactly one OperationRecord.

    Parametrized across all 22 command handlers.
    """

    def _run(self, cli_args, expected_cmd, mock_patches, resolve_sid=True):
        """Invoke CLI with patches and assert exactly one logged operation."""
        import contextlib

        spy = SpyOperationLog()
        runner = CliRunner()

        cms = [patch("aiyes.cli.main.operation_log_adapter", spy)]
        if resolve_sid:
            cms.append(patch("aiyes.cli.main.resolve_session_id", return_value="s1"))
        cms.extend(mock_patches)

        with contextlib.ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = runner.invoke(cli, cli_args)

        assert len(spy.appended) == 1, (
            f"{expected_cmd}: {len(spy.appended)} appends, "
            f"exit={result.exit_code}, out={result.output[:200]!r}"
        )
        assert spy.appended[0].command == expected_cmd
        return spy.appended[0], result

    # --- Session-less handlers (6) ---

    def test_session_list(self):
        self._run(
            ["session", "list"],
            "session list",
            [patch("aiyes.cli.main.session_list_uc", **{"execute.return_value": []})],
            resolve_sid=False,
        )

    def test_doctor(self):
        self._run(
            ["doctor"],
            "doctor",
            [patch("aiyes.cli.main.doctor_uc", **{"execute.return_value": []})],
            resolve_sid=False,
        )

    def test_mcp_manifest(self):
        self._run(["mcp-manifest"], "mcp-manifest", [], resolve_sid=False)

    def test_session_metrics(self):
        self._run(
            ["session", "metrics"],
            "session metrics",
            [
                patch(
                    "aiyes.cli.main.metrics_uc",
                    **{"execute.return_value": _metrics_zero()},
                )
            ],
            resolve_sid=False,
        )

    def test_session_prune(self):
        self._run(
            ["session", "prune"],
            "session prune",
            [
                patch(
                    "aiyes.cli.main.prune_uc", **{"execute.return_value": _prune_zero()}
                )
            ],
            resolve_sid=False,
        )

    def test_session_stop(self):
        self._run(
            ["session", "stop"],
            "session stop",
            [
                patch(
                    "aiyes.cli.main.session_stop_uc",
                    **{
                        "execute.return_value": _mk(
                            status="stopped", session_id="s1", errors=None
                        )
                    },
                )
            ],
            resolve_sid=False,
        )

    # --- Session start (special dual-path) ---

    def test_session_start(self):
        spy = SpyOperationLog()
        runner = CliRunner()
        mock_session = MagicMock()
        mock_session.session_id = "new-sess"
        with (
            patch("aiyes.cli.main.operation_log_adapter", spy),
            patch("aiyes.cli.main._get_session_start_uc") as mock_get,
            patch(
                "aiyes.cli.main.format_session_start",
                return_value='{"session_id":"new-sess"}',
            ),
        ):
            mock_get.return_value.execute.return_value = mock_session
            runner.invoke(cli, ["session", "start", "--", "gedit"])
        assert len(spy.appended) == 1
        assert spy.appended[0].command == "session start"

    # --- Session-bound handlers (15) ---

    def test_inspect(self):
        self._run(
            ["inspect"],
            "inspect",
            [
                patch(
                    "aiyes.cli.main.inspect_uc",
                    **{
                        "execute.return_value": _mk(
                            tree=None,
                            screenshot=None,
                            timestamp=1000.0,
                            screenshot_base64=False,
                            screenshot_data=None,
                        )
                    },
                )
            ],
        )

    def test_diff(self):
        self._run(
            ["diff"],
            "diff",
            [patch("aiyes.cli.main.diff_uc", **{"execute.return_value": MagicMock()})],
        )

    def test_find(self):
        self._run(
            ["find", "push_button"],
            "find",
            [patch("aiyes.cli.main.find_uc", **{"execute.return_value": []})],
        )

    def test_screenshot(self):
        self._run(
            ["screenshot"],
            "screenshot",
            [
                patch(
                    "aiyes.cli.main.screenshot_uc",
                    **{"execute.return_value": _mk(path="/tmp/s.png", data=None)},
                )
            ],
        )

    def test_action(self):
        self._run(
            ["action", "n_001", "click"],
            "action",
            [
                patch(
                    "aiyes.cli.main.action_uc",
                    **{
                        "execute.return_value": _mk(
                            status="ok",
                            action="click",
                            target="n_001",
                            reason=None,
                            available_actions=None,
                        )
                    },
                )
            ],
        )

    def test_mouse_move(self):
        self._run(
            ["mouse", "move", "100", "200"],
            "mouse move",
            [patch("aiyes.cli.main.mouse_uc")],
        )

    def test_mouse_click(self):
        self._run(
            ["mouse", "click", "100", "200"],
            "mouse click",
            [patch("aiyes.cli.main.mouse_uc")],
        )

    def test_mouse_drag(self):
        self._run(
            ["mouse", "drag", "0", "0", "100", "100"],
            "mouse drag",
            [patch("aiyes.cli.main.mouse_uc")],
        )

    def test_mouse_scroll(self):
        self._run(
            ["mouse", "scroll", "down"],
            "mouse scroll",
            [patch("aiyes.cli.main.mouse_uc")],
        )

    def test_key(self):
        self._run(
            ["key", "Return"],
            "key",
            [patch("aiyes.cli.main.key_uc")],
        )

    def test_type(self):
        self._run(
            ["type", "hello"],
            "type",
            [patch("aiyes.cli.main.type_text_uc")],
        )

    def test_wait(self):
        self._run(
            ["wait", "push_button"],
            "wait",
            [
                patch(
                    "aiyes.cli.main.wait_uc",
                    **{
                        "execute.return_value": _mk(
                            found=True, timeout=False, id="n_001"
                        )
                    },
                )
            ],
        )

    def test_wait_stable(self):
        self._run(
            ["wait-stable"],
            "wait-stable",
            [
                patch(
                    "aiyes.cli.main.wait_stable_uc",
                    **{
                        "execute.return_value": _mk(stable=True, timeout=False, polls=3)
                    },
                )
            ],
        )

    def test_do(self):
        self._run(
            ["do", "--role", "push_button", "--name", "OK", "--action", "click"],
            "do",
            [
                patch(
                    "aiyes.cli.main.compound_do_uc",
                    **{"execute.return_value": MagicMock()},
                )
            ],
        )

    def test_session_resize(self):
        self._run(
            ["session", "resize", "1920x1080"],
            "session resize",
            [
                patch(
                    "aiyes.cli.main.session_resize_uc",
                    **{
                        "execute.return_value": _mk(
                            status="resized", resolution="1920x1080"
                        )
                    },
                )
            ],
        )


# ═══════════════════════════════════════════════════════════════════════
# Session-bound command logging (REQ-AIYES21-005, REQ-AIYES21-006)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionBoundCommandLogs:
    """Session-bound commands log with resolved session_id."""

    def test_find_logs_with_session_id(self) -> None:
        """REQ-AIYES21-005: Session-bound command logs with resolved session_id."""
        spy = SpyOperationLog()
        runner = CliRunner()

        mock_results = MagicMock()
        mock_results.return_value = []

        with (
            patch("aiyes.cli.main.resolve_session_id", return_value="my-session"),
            patch("aiyes.cli.main.find_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            result = runner.invoke(cli, ["find", "push_button"])

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == "my-session"
        assert spy.appended[0].command == "find"


class TestSessionBoundCommandFailureBeforeResolution:
    """Failed session resolution logs to _global with sid=""."""

    def test_invalid_session_logs_to_global(self) -> None:
        """REQ-AIYES21-007: Failed resolve -> _global with exit_code=1."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch(
                "aiyes.cli.main.resolve_session_id",
                side_effect=RuntimeError("No active sessions"),
            ),
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            result = runner.invoke(cli, ["find", "push_button"])

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""
        assert spy.appended[0].exit_code == 1
        assert spy.appended[0].command == "find"


# ═══════════════════════════════════════════════════════════════════════
# Session start success/failure logging (REQ-AIYES21-006, FC-AIYES21-019)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionStartSuccessLogs:
    """Successful session start logs with returned session_id."""

    def test_success_logs_to_session_dir(self) -> None:
        """REQ-AIYES21-006: Success -> record with non-empty session_id."""
        spy = SpyOperationLog()
        runner = CliRunner()

        mock_session = MagicMock()
        mock_session.session_id = "new-session-001"

        with (
            patch("aiyes.cli.main._get_session_start_uc") as mock_get_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
            patch(
                "aiyes.cli.main.format_session_start",
                return_value='{"session_id":"new-session-001"}',
            ),
        ):
            mock_get_uc.return_value.execute.return_value = mock_session
            result = runner.invoke(cli, ["session", "start", "--", "gedit"])

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == "new-session-001"
        assert spy.appended[0].command == "session start"
        assert spy.appended[0].exit_code == 0


class TestSessionStartFailureLogs:
    """Failed session start logs to _global with sid=""."""

    def test_failure_logs_to_global(self) -> None:
        """REQ-AIYES21-006: Exception -> _global with exit_code=1."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.main._get_session_start_uc") as mock_get_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_get_uc.return_value.execute.side_effect = RuntimeError("launch failed")
            result = runner.invoke(cli, ["session", "start", "--", "gedit"])

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""
        assert spy.appended[0].command == "session start"
        assert spy.appended[0].exit_code == 1


# ═══════════════════════════════════════════════════════════════════════
# Session-less commands log to _global (FC-AIYES21-017, REQ-AIYES21-005)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionLessCommandsLogGlobal:
    """Session-less commands log with session_id=""."""

    @pytest.mark.parametrize(
        "cli_args,expected_cmd",
        [
            (["session", "list"], "session list"),
            (["doctor"], "doctor"),
            (["mcp-manifest"], "mcp-manifest"),
        ],
    )
    def test_session_less_commands_log_empty_sid(
        self, cli_args: List[str], expected_cmd: str
    ) -> None:
        """REQ-AIYES21-005: Session-less -> sid="" in _global."""
        spy = SpyOperationLog()
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_list,
            patch("aiyes.cli.main.doctor_uc") as mock_doc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_list.execute.return_value = []
            mock_doc.execute.return_value = []
            result = runner.invoke(cli, cli_args)

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""
        assert spy.appended[0].command == expected_cmd


# ═══════════════════════════════════════════════════════════════════════
# Append failure swallowed (FC-AIYES21-022, REQ-AIYES21-011)
# ═══════════════════════════════════════════════════════════════════════


class TestLogAppendFailureSwallowed:
    """append() failure does not break CLI command execution."""

    def test_append_ioerror_swallowed(self) -> None:
        """REQ-AIYES21-011: IOError from append() -> command still completes."""
        spy = SpyOperationLog(fail_on_append=True)
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            result = runner.invoke(cli, ["session", "list"])

        # Command should still exit 0 despite append failure
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════
# session metrics subcommand (REQ-AIYES21-019)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionMetricsCommand:
    """session metrics subcommand exists and outputs JSON."""

    def test_metrics_command_exists(self) -> None:
        """REQ-AIYES21-019: 'session metrics' is a registered subcommand."""
        session_group = cli.commands.get("session")
        assert session_group is not None
        assert isinstance(session_group, click.Group)
        assert "metrics" in session_group.commands

    def test_metrics_outputs_json(self) -> None:
        """REQ-AIYES21-019: Output is valid JSON with total_commands."""
        spy = SpyOperationLog()
        runner = CliRunner()

        # Import the expected exports from composition_root
        from aiyes.domain.operation_record import MetricsSummary

        mock_summary = MetricsSummary(
            session_id=None,
            total_commands=5,
            command_counts=(("inspect", 3), ("find", 2)),
            latency_p50=(("inspect", 10.0), ("find", 5.0)),
            latency_p95=(("inspect", 50.0), ("find", 20.0)),
            failure_rate=(("inspect", 0.0), ("find", 0.0)),
            session_duration_s=0.0,
            period_start=1000.0,
            period_end=1100.0,
        )

        with (
            patch("aiyes.cli.main.metrics_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_summary
            result = runner.invoke(cli, ["session", "metrics"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "total_commands" in parsed
        assert parsed["total_commands"] == 5

    def test_metrics_logs_to_global(self) -> None:
        """REQ-AIYES21-019: session metrics is session-less -> _global."""
        spy = SpyOperationLog()
        runner = CliRunner()

        from aiyes.domain.operation_record import MetricsSummary

        mock_summary = MetricsSummary(
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

        with (
            patch("aiyes.cli.main.metrics_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_summary
            result = runner.invoke(cli, ["session", "metrics"])

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""
        assert spy.appended[0].command == "session metrics"


class TestSessionMetricsCommandPerSession:
    """session metrics --session ID passes through to use case."""

    def test_session_option_passed(self) -> None:
        """REQ-AIYES21-019: --session ID forwarded to execute()."""
        spy = SpyOperationLog()
        runner = CliRunner()

        from aiyes.domain.operation_record import MetricsSummary

        mock_summary = MetricsSummary(
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

        with (
            patch("aiyes.cli.main.metrics_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_summary
            result = runner.invoke(cli, ["session", "metrics", "--session", "s1"])

        mock_uc.execute.assert_called_once_with(session_id="s1")


# ═══════════════════════════════════════════════════════════════════════
# session prune subcommand (REQ-AIYES21-028)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionPruneCommand:
    """session prune subcommand exists and outputs JSON."""

    def test_prune_command_exists(self) -> None:
        """REQ-AIYES21-028: 'session prune' is a registered subcommand."""
        session_group = cli.commands.get("session")
        assert session_group is not None
        assert isinstance(session_group, click.Group)
        assert "prune" in session_group.commands

    def test_prune_outputs_json(self) -> None:
        """REQ-AIYES21-028: Output is valid JSON with pruned_count."""
        spy = SpyOperationLog()
        runner = CliRunner()

        from aiyes.domain.operation_record import PruneResult

        mock_result = PruneResult(
            pruned_count=2,
            skipped_active=1,
            dry_run=False,
            sessions_pruned=("s1", "s2"),
        )

        with (
            patch("aiyes.cli.main.prune_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(cli, ["session", "prune"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "pruned_count" in parsed
        assert parsed["pruned_count"] == 2

    def test_prune_logs_to_global(self) -> None:
        """REQ-AIYES21-028: session prune is session-less -> _global."""
        spy = SpyOperationLog()
        runner = CliRunner()

        from aiyes.domain.operation_record import PruneResult

        mock_result = PruneResult(
            pruned_count=0,
            skipped_active=0,
            dry_run=False,
            sessions_pruned=(),
        )

        with (
            patch("aiyes.cli.main.prune_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(cli, ["session", "prune"])

        assert len(spy.appended) == 1
        assert spy.appended[0].session_id == ""
        assert spy.appended[0].command == "session prune"


class TestSessionPruneCommandDryRun:
    """session prune --dry-run passes through to use case."""

    def test_dry_run_flag_passed(self) -> None:
        """REQ-AIYES21-028: --dry-run -> dry_run=True to execute()."""
        spy = SpyOperationLog()
        runner = CliRunner()

        from aiyes.domain.operation_record import PruneResult

        mock_result = PruneResult(
            pruned_count=1,
            skipped_active=0,
            dry_run=True,
            sessions_pruned=("s1",),
        )

        with (
            patch("aiyes.cli.main.prune_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(cli, ["session", "prune", "--dry-run"])

        mock_uc.execute.assert_called_once()
        call_kwargs = mock_uc.execute.call_args
        # dry_run should be True
        assert call_kwargs.kwargs.get("dry_run") is True or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] is True
        )


class TestSessionPruneCommandOlderThan:
    """session prune --older-than passes as max_age_hours."""

    def test_older_than_value_passed(self) -> None:
        """REQ-AIYES21-028: --older-than 48 -> max_age_hours=48.0."""
        spy = SpyOperationLog()
        runner = CliRunner()

        from aiyes.domain.operation_record import PruneResult

        mock_result = PruneResult(
            pruned_count=0,
            skipped_active=0,
            dry_run=False,
            sessions_pruned=(),
        )

        with (
            patch("aiyes.cli.main.prune_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(cli, ["session", "prune", "--older-than", "48"])

        mock_uc.execute.assert_called_once()
        call_kwargs = mock_uc.execute.call_args
        assert call_kwargs.kwargs.get("max_age_hours") == 48.0 or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0] == 48.0
        )


class TestSessionPruneDefaultOlderThan:
    """Default --older-than is 72 hours."""

    def test_default_72_hours(self) -> None:
        """REQ-AIYES21-028: No --older-than flag -> 72 hours default."""
        spy = SpyOperationLog()
        runner = CliRunner()

        from aiyes.domain.operation_record import PruneResult

        mock_result = PruneResult(
            pruned_count=0,
            skipped_active=0,
            dry_run=False,
            sessions_pruned=(),
        )

        with (
            patch("aiyes.cli.main.prune_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = mock_result
            result = runner.invoke(cli, ["session", "prune"])

        mock_uc.execute.assert_called_once()
        call_kwargs = mock_uc.execute.call_args
        assert call_kwargs.kwargs.get("max_age_hours") == 72.0 or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0] == 72.0
        )


# ═══════════════════════════════════════════════════════════════════════
# New subcommands registered (integration check)
# ═══════════════════════════════════════════════════════════════════════


class TestNewSubcommandsRegistered:
    """Session group has all expected subcommands including new ones."""

    def test_session_group_has_all_subcommands(self) -> None:
        """Session group includes: start, stop, list, resize, metrics, prune."""
        session_group = cli.commands.get("session")
        assert session_group is not None
        assert isinstance(session_group, click.Group)

        sub_names = set(session_group.commands.keys())
        expected = {"start", "stop", "list", "resize", "metrics", "prune"}
        assert expected.issubset(sub_names), (
            f"Missing subcommands: {expected - sub_names}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Integration: real FileOperationLog postconditions (A10-005)
# FC-AIYES21-017/018/019
# ═══════════════════════════════════════════════════════════════════════


class TestAttributionFilesystemPostconditions:
    """Integration tests with real FileOperationLog on disk."""

    def test_global_attribution_creates_file(self, tmp_path) -> None:
        """FC-AIYES21-017: Session-less command writes _global/operations.jsonl."""
        from aiyes.adapters.file_operation_log import FileOperationLog

        real_log = FileOperationLog(base_dir=str(tmp_path))
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", real_log),
        ):
            mock_uc.execute.return_value = []
            result = runner.invoke(cli, ["session", "list"])

        assert result.exit_code == 0
        global_path = tmp_path / "_global" / "operations.jsonl"
        assert global_path.exists(), "_global/operations.jsonl not created"
        content = global_path.read_text().strip()
        assert len(content) > 0
        parsed = json.loads(content)
        assert parsed["cmd"] == "session list"
        assert parsed["sid"] == ""

    def test_session_start_success_creates_session_file(self, tmp_path) -> None:
        """FC-AIYES21-019: Successful session start writes <sid>/operations.jsonl."""
        from aiyes.adapters.file_operation_log import FileOperationLog

        real_log = FileOperationLog(base_dir=str(tmp_path))
        runner = CliRunner()

        mock_session = MagicMock()
        mock_session.session_id = "test-sess-001"

        with (
            patch("aiyes.cli.main.operation_log_adapter", real_log),
            patch("aiyes.cli.main._get_session_start_uc") as mock_get,
            patch(
                "aiyes.cli.main.format_session_start",
                return_value='{"session_id":"test-sess-001"}',
            ),
        ):
            mock_get.return_value.execute.return_value = mock_session
            result = runner.invoke(cli, ["session", "start", "--", "gedit"])

        assert result.exit_code == 0
        session_path = tmp_path / "test-sess-001" / "operations.jsonl"
        assert session_path.exists(), "session/operations.jsonl not created"
        content = session_path.read_text().strip()
        parsed = json.loads(content)
        assert parsed["cmd"] == "session start"
        assert parsed["sid"] == "test-sess-001"

    def test_failed_resolution_writes_global(self, tmp_path) -> None:
        """FC-AIYES21-018: Failed resolve writes to _global/operations.jsonl."""
        from aiyes.adapters.file_operation_log import FileOperationLog

        real_log = FileOperationLog(base_dir=str(tmp_path))
        runner = CliRunner()

        with (
            patch(
                "aiyes.cli.main.resolve_session_id",
                side_effect=RuntimeError("No active sessions"),
            ),
            patch("aiyes.cli.main.operation_log_adapter", real_log),
        ):
            result = runner.invoke(cli, ["find", "push_button"])

        global_path = tmp_path / "_global" / "operations.jsonl"
        assert global_path.exists()
        content = global_path.read_text().strip()
        parsed = json.loads(content)
        assert parsed["cmd"] == "find"
        assert parsed["sid"] == ""
        assert parsed["exit"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Append failure: stdout/stderr verification (A10-009, FC-AIYES21-022)
# ═══════════════════════════════════════════════════════════════════════


class TestLogAppendFailureOutputPreserved:
    """append() failure does not alter normal CLI output."""

    def test_append_failure_stdout_still_valid(self) -> None:
        """FC-AIYES21-022: Normal stdout produced despite append failure."""
        spy = SpyOperationLog(fail_on_append=True)
        runner = CliRunner()

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            result = runner.invoke(cli, ["session", "list"])

        assert result.exit_code == 0
        # stdout should contain valid JSON output (empty session list)
        parsed = json.loads(result.output)
        assert isinstance(parsed, (list, dict))

    def test_append_failure_no_stderr(self) -> None:
        """FC-AIYES21-022: No error on stderr from swallowed append failure."""
        import inspect as _insp

        from click.testing import CliRunner

        spy = SpyOperationLog(fail_on_append=True)
        _kw = (
            {"mix_stderr": False}
            if "mix_stderr" in _insp.signature(CliRunner.__init__).parameters
            else {}
        )
        runner = CliRunner(**_kw)

        with (
            patch("aiyes.cli.main.session_list_uc") as mock_uc,
            patch("aiyes.cli.main.operation_log_adapter", spy),
        ):
            mock_uc.execute.return_value = []
            result = runner.invoke(cli, ["session", "list"])

        assert result.exit_code == 0
        # stderr should be empty — the append exception is swallowed
        all_output = (result.output or "") + (getattr(result, "stderr", "") or "")
        assert "disk full" not in all_output
