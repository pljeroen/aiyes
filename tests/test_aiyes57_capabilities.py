"""AIYES-57 session capabilities contract tests."""

from __future__ import annotations

import json

from click.testing import CliRunner

from aiyes.domain.session import Session


def _session(backend: str = "linux") -> Session:
    return Session(
        session_id=f"{backend}-cap",
        app_pid=123,
        app_command="app",
        app_args=(),
        name=None,
        backend=backend,
        device_serial="emulator-5554" if backend == "android" else None,
    )


class FakeSessionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, session_id: str) -> Session | None:
        return self.session if session_id == self.session.session_id else None

    def load_all(self) -> list[Session]:
        return [self.session]


class TestSessionCapabilitiesUseCase:
    def test_linux_capabilities_report_truthful_statuses(self) -> None:
        from aiyes.domain.use_cases.session_capabilities import (
            SessionCapabilitiesUseCase,
        )

        result = SessionCapabilitiesUseCase(FakeSessionRepo(_session("linux"))).execute(
            "linux-cap"
        )

        assert result.backend == "linux"
        statuses = {cap.status for cap in result.capabilities.values()}
        assert statuses <= {"available", "degraded", "unavailable"}
        assert result.capabilities["semantic_tree"].status == "available"
        assert result.capabilities["screenshot"].status == "available"
        assert result.capabilities["semantic_action"].status == "available"
        assert result.capabilities["coordinate_input"].status == "available"
        assert result.capabilities["resize"].status == "available"
        assert result.capabilities["gesture"].status == "unavailable"

    def test_android_capabilities_report_degraded_and_unavailable_limits(self) -> None:
        from aiyes.domain.use_cases.session_capabilities import (
            SessionCapabilitiesUseCase,
        )

        result = SessionCapabilitiesUseCase(
            FakeSessionRepo(_session("android"))
        ).execute("android-cap")

        assert result.backend == "android"
        assert result.capabilities["semantic_tree"].status == "available"
        assert result.capabilities["semantic_action"].status == "degraded"
        assert result.capabilities["resize"].status == "unavailable"
        assert result.capabilities["gesture"].status == "degraded"
        assert result.capabilities["diff"].status == "degraded"
        assert result.capabilities["wait_stable"].status == "degraded"


class TestSessionCapabilitiesCli:
    def test_session_capabilities_command_returns_json(self, monkeypatch) -> None:
        from aiyes.cli import main
        from aiyes.domain.use_cases.session_capabilities import (
            Capability,
            SessionCapabilitiesResult,
        )

        class StubCapabilities:
            def execute(
                self, session_id: str, live: bool = False
            ) -> SessionCapabilitiesResult:
                assert session_id == "abc123"
                assert live is False
                return SessionCapabilitiesResult(
                    session_id="abc123",
                    backend="linux",
                    capabilities={
                        "semantic_tree": Capability(
                            status="available",
                            reason="AT-SPI tree inspection is supported.",
                            operations=("inspect", "find", "wait"),
                        )
                    },
                )

        monkeypatch.setattr(main, "resolve_session_id", lambda value: value or "abc123")
        monkeypatch.setattr(main, "session_capabilities_uc", StubCapabilities())

        result = CliRunner().invoke(main.cli, ["session", "capabilities"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["session_id"] == "abc123"
        assert data["backend"] == "linux"
        assert data["capabilities"]["semantic_tree"]["status"] == "available"
        assert data["capabilities"]["semantic_tree"]["operations"] == [
            "inspect",
            "find",
            "wait",
        ]
