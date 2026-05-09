"""AIYES-63: diagnostic debug bundle."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from click.testing import CliRunner

from aiyes.cli import main
from aiyes.domain.operation_record import OperationRecord
from aiyes.domain.session import Session
from aiyes.domain.tree import AccessibilityTree
from aiyes.domain.types import DependencyResult, StoredTree
from aiyes.domain.use_cases.debug_bundle import DebugBundleUseCase


def _session() -> Session:
    return Session(
        session_id="debug-session",
        display=":99",
        app_pid=100,
        app_command="app",
        app_args=("--safe",),
        atspi_bus_pid=101,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=102,
        name="debug",
        started_at=10.0,
    )


class FakeSessionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, session_id: str):
        if session_id == self.session.session_id:
            return self.session
        return None


class FakeDoctor:
    def execute(self):
        return (
            DependencyResult(
                name="xvfb",
                status="pass",
                message="available",
            ),
        )


class FakeOperationLog:
    def read(self, session_id: str):
        return [
            OperationRecord(
                timestamp=1.0,
                session_id=session_id,
                command="inspect",
                duration_ms=12.0,
                exit_code=0,
            ),
            OperationRecord(
                timestamp=2.0,
                session_id=session_id,
                command="action",
                duration_ms=15.0,
                exit_code=1,
                error="failed",
            ),
        ]


class FakeTreeStore:
    def load_tree(self, session_id: str):
        return StoredTree(tree=AccessibilityTree(roots=()), registry=None)


class FakeScreenshotStore:
    def get_screenshot_path(self, session_id: str) -> str:
        return f"/home/test/.aieyes/{session_id}/screenshot.png"

    def read_screenshot_bytes(self, session_id: str) -> bytes:
        return b"png"


def test_debug_bundle_collects_summaries_without_copying_files() -> None:
    uc = DebugBundleUseCase(
        session_repo=FakeSessionRepo(_session()),
        doctor_uc=FakeDoctor(),
        operation_log=FakeOperationLog(),
        tree_store=FakeTreeStore(),
        screenshot_store=FakeScreenshotStore(),
    )

    bundle = uc.execute(
        session_id="debug-session",
        environ={"PATH": "/usr/bin", "SECRET_TOKEN": "do-not-leak"},
    )

    assert bundle["session"]["session_id"] == "debug-session"
    assert bundle["doctor"][0]["name"] == "xvfb"
    assert bundle["operations"]["total"] == 2
    assert bundle["operations"]["failures"] == 1
    assert bundle["tree"]["root_count"] == 0
    assert bundle["screenshot"]["available"] is True
    assert bundle["screenshot"]["copied"] is False
    encoded = json.dumps(bundle)
    assert "do-not-leak" not in encoded
    assert bundle["environment"]["SECRET_TOKEN"] == "***"


def test_cli_debug_bundle_returns_json(monkeypatch) -> None:
    stub = MagicMock()
    stub.execute.return_value = {
        "schema_version": 1,
        "session": {"session_id": "debug-session"},
        "doctor": [],
        "operations": {"total": 0, "failures": 0},
        "tree": {"available": False, "root_count": 0},
        "screenshot": {"available": False, "copied": False},
        "environment": {},
    }
    monkeypatch.setattr(main, "debug_bundle_uc", stub)
    monkeypatch.setattr(main, "resolve_session_id", lambda session_id=None: "debug-session")

    result = CliRunner().invoke(main.cli, ["debug-bundle", "--session", "debug-session"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["session"]["session_id"] == "debug-session"
    stub.execute.assert_called_once()
    assert stub.execute.call_args.kwargs["session_id"] == "debug-session"
