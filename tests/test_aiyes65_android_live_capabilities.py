"""AIYES-65: opt-in Android live capability probing."""

from __future__ import annotations

import json

from click.testing import CliRunner

from aiyes.adapters.android_capability_probe_adapter import AndroidCapabilityProbeAdapter
from aiyes.cli import main
from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_capabilities import (
    CapabilityProbeCheck,
    CapabilityProbeReport,
    SessionCapabilitiesUseCase,
)


def _android_session() -> Session:
    return Session(
        session_id="android-live-probe",
        app_pid=123,
        app_command="adb",
        app_args=("-s", "emulator-5554", "shell", "monkey", "-p", "com.app", "1"),
        name=None,
        backend="android",
        device_serial="emulator-5554",
        package_name="com.app",
    )


class FakeSessionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, session_id: str):
        if session_id == self.session.session_id:
            return self.session
        return None


class FakeAndroidProbe:
    def __init__(self) -> None:
        self.calls: list[Session] = []

    def probe(self, session: Session) -> CapabilityProbeReport:
        self.calls.append(session)
        return CapabilityProbeReport(
            backend="android",
            checks={
                "device": CapabilityProbeCheck(
                    status="available",
                    reason="adb reports emulator-5554 as device",
                ),
                "uiautomator_tree": CapabilityProbeCheck(
                    status="degraded",
                    reason="tree is available but contains no named nodes",
                ),
                "screenshot": CapabilityProbeCheck(
                    status="available",
                    reason="screencap returned PNG bytes",
                ),
                "package": CapabilityProbeCheck(
                    status="available",
                    reason="com.app is reachable",
                ),
            },
        )


class TestAndroidLiveCapabilityProbe:
    def test_default_capabilities_do_not_run_live_probe(self) -> None:
        probe = FakeAndroidProbe()
        uc = SessionCapabilitiesUseCase(
            session_repo=FakeSessionRepo(_android_session()),
            android_probe=probe,
        )

        result = uc.execute(session_id="android-live-probe")

        assert probe.calls == []
        assert result.live_probe is None

    def test_live_capabilities_include_android_probe_metadata(self) -> None:
        probe = FakeAndroidProbe()
        uc = SessionCapabilitiesUseCase(
            session_repo=FakeSessionRepo(_android_session()),
            android_probe=probe,
        )

        result = uc.execute(session_id="android-live-probe", live=True)

        assert len(probe.calls) == 1
        assert result.live_probe is not None
        assert result.live_probe.backend == "android"
        assert result.live_probe.checks["device"].status == "available"
        assert result.live_probe.checks["uiautomator_tree"].status == "degraded"


class TestAndroidLiveCapabilityCli:
    def test_session_capabilities_live_outputs_probe_json(self, monkeypatch) -> None:
        class StubUseCase:
            def execute(self, session_id: str, live: bool = False):
                assert session_id == "android-live-probe"
                assert live is True
                return SessionCapabilitiesUseCase(
                    session_repo=FakeSessionRepo(_android_session()),
                    android_probe=FakeAndroidProbe(),
                ).execute(session_id=session_id, live=True)

        monkeypatch.setattr(main, "resolve_session_id", lambda session_id=None: "android-live-probe")
        monkeypatch.setattr(main, "session_capabilities_uc", StubUseCase())

        result = CliRunner().invoke(
            main.cli,
            ["session", "capabilities", "--session", "android-live-probe", "--live"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["live_probe"]["backend"] == "android"
        assert data["live_probe"]["checks"]["device"]["status"] == "available"


class TestAndroidCapabilityProbeAdapter:
    def test_adapter_reports_device_tree_screenshot_and_package_checks(self) -> None:
        calls: list[list[str]] = []

        class Completed:
            def __init__(self, returncode: int, stdout, stderr="") -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def runner(command, **kwargs):
            calls.append(command)
            joined = " ".join(command)
            if "get-state" in joined:
                return Completed(0, "device\n")
            if "pidof com.app" in joined:
                return Completed(0, "123\n")
            if "uiautomator dump" in joined:
                return Completed(
                    0,
                    '<hierarchy><node text="Create" class="android.widget.Button" bounds="[0,0][10,10]" /></hierarchy>',
                )
            if "screencap" in joined:
                return Completed(0, b"\x89PNG\r\n\x1a\nbytes")
            raise AssertionError(f"unexpected command: {command}")

        adapter = AndroidCapabilityProbeAdapter(runner=runner, adb_path="/adb")

        report = adapter.probe(_android_session())

        assert report.checks["device"].status == "available"
        assert report.checks["package"].status == "available"
        assert report.checks["uiautomator_tree"].status == "available"
        assert report.checks["screenshot"].status == "available"
        assert calls
