"""AIYES-61: session hygiene active-only and prune guidance."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aiyes.cli import main
from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_list import SessionListEntry, SessionListUseCase
from tests.conftest import FakeClock, FakeProcess, FakeSessionRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SMOKE = PROJECT_ROOT / "docs" / "release-smoke.md"


def _session(session_id: str, app_pid: int, xvfb_pid: int) -> Session:
    return Session(
        session_id=session_id,
        display=f":{app_pid}",
        app_pid=app_pid,
        app_command="app",
        app_args=(),
        atspi_bus_pid=app_pid + 1,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=xvfb_pid,
        name=None,
        started_at=10.0,
    )


class TestSessionListActiveOnly:
    def test_use_case_filters_stale_sessions_when_active_only(self) -> None:
        repo = FakeSessionRepository()
        repo.save(_session("active-session", app_pid=100, xvfb_pid=200))
        repo.save(_session("stale-session", app_pid=101, xvfb_pid=201))
        process = FakeProcess()
        process._running[100] = True
        process._running[200] = True
        process._running[101] = False
        process._running[201] = False
        uc = SessionListUseCase(
            session_repo=repo,
            process=process,
            clock=FakeClock(now_value=20.0),
        )

        entries = uc.execute(active_only=True)

        assert [entry.session_id for entry in entries] == ["active-session"]
        assert entries[0].status == "active"

    def test_cli_passes_active_only_and_returns_filtered_json(self, monkeypatch) -> None:
        class StubSessionListUseCase:
            def __init__(self) -> None:
                self.calls: list[bool] = []

            def execute(self, active_only: bool = False):
                self.calls.append(active_only)
                return [
                    SessionListEntry(
                        session_id="active-session",
                        display=":99",
                        app="app",
                        status="active",
                        uptime=10.0,
                        backend="linux",
                    )
                ]

        stub = StubSessionListUseCase()
        monkeypatch.setattr(main, "session_list_uc", stub)
        runner = CliRunner()

        result = runner.invoke(main.cli, ["session", "list", "--active-only"])

        assert result.exit_code == 0
        assert stub.calls == [True]
        data = json.loads(result.output)
        assert data[0]["session_id"] == "active-session"


class TestReleaseSmokePruneGuidance:
    def test_release_smoke_requires_prune_before_evidence_capture(self) -> None:
        content = RELEASE_SMOKE.read_text(encoding="utf-8")

        assert "Before capturing release smoke evidence" in content
        assert "aieyes session prune --dry-run" in content
        assert "aieyes session prune" in content
        assert "aieyes session list --active-only" in content
