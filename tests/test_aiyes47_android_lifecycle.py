"""AIYES-47 Android lifecycle identity and stop behavior tests."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pytest

from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_list import SessionListUseCase
from aiyes.domain.use_cases.session_resolve import SessionResolveUseCase
from aiyes.domain.use_cases.session_start import SessionStartUseCase
from aiyes.domain.use_cases.session_stop import SessionStopUseCase
from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)


class FakeAndroidLifecycle:
    """Fake Android lifecycle port for app package checks and force-stop."""

    def __init__(self) -> None:
        self.running: Dict[Tuple[str, str], bool] = {}
        self.calls: List[Tuple[str, Tuple[str, str]]] = []
        self.stop_error: Optional[Exception] = None

    def is_app_running(self, serial: str, package_name: str) -> bool:
        self.calls.append(("is_app_running", (serial, package_name)))
        return self.running.get((serial, package_name), False)

    def stop_app(self, serial: str, package_name: str) -> None:
        self.calls.append(("stop_app", (serial, package_name)))
        if self.stop_error is not None:
            raise self.stop_error
        self.running[(serial, package_name)] = False


def _make_android_session(**overrides: object) -> Session:
    data = {
        "session_id": "android-47",
        "app_pid": 0,
        "app_command": "com.example.app/.MainActivity",
        "app_args": (),
        "name": None,
        "backend": "android",
        "device_serial": "emulator-5554",
        "package_name": "com.example.app",
        "activity_name": ".MainActivity",
        "started_at": 10.0,
    }
    data.update(overrides)
    return Session(**data)


class TestAndroidSessionIdentity:
    def test_android_start_extracts_package_from_absolute_adb_monkey_launch(
        self,
    ) -> None:
        process = FakeProcess(pid=4242)
        repo = FakeSessionRepository()
        uc = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(),
            atspi_bus=FakeAccessibilityBus(),
            process=process,
            session_repo=repo,
            clock=FakeClock(),
        )

        session = uc.execute(
            app_command="/opt/android-sdk/platform-tools/adb",
            app_args=[
                "-s",
                "emulator-5554",
                "shell",
                "monkey",
                "-p",
                "com.example.publicdemo",
                "1",
            ],
            backend="android",
            device_serial="emulator-5554",
            wait=0,
        )

        assert session.package_name == "com.example.publicdemo"
        assert session.activity_name == ""

    def test_android_start_stores_package_identity_and_sanitized_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "secret")
        monkeypatch.setenv("CUSTOM_SERVICE_TOKEN", "secret")
        monkeypatch.setenv("PUBLIC_SETTING", "kept")

        process = FakeProcess(pid=4242)
        repo = FakeSessionRepository()
        uc = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(),
            atspi_bus=FakeAccessibilityBus(),
            process=process,
            session_repo=repo,
            clock=FakeClock(),
        )

        session = uc.execute(
            app_command="adb",
            app_args=[
                "-s",
                "emulator-5554",
                "shell",
                "am",
                "start",
                "-n",
                "com.example.app/.MainActivity",
            ],
            backend="android",
            device_serial="emulator-5554",
            wait=0,
        )

        assert session.package_name == "com.example.app"
        assert session.activity_name == ".MainActivity"
        start_call = [call for call in process.calls if call[0] == "start"][0]
        _, _, env = start_call[1]
        assert env is not None
        assert "OPENAI_API_KEY" not in env
        assert "CUSTOM_SERVICE_TOKEN" not in env
        assert env["PUBLIC_SETTING"] == "kept"


class TestAndroidLifecycleLiveness:
    def test_resolve_android_uses_lifecycle_not_host_pid(self) -> None:
        process = FakeProcess()
        lifecycle = FakeAndroidLifecycle()
        lifecycle.running[("emulator-5554", "com.example.app")] = True
        repo = FakeSessionRepository()
        repo.save(_make_android_session(app_pid=999999))

        uc = SessionResolveUseCase(
            session_repo=repo,
            process=process,
            android_lifecycle=lifecycle,
        )

        assert uc.execute() == "android-47"
        assert ("is_app_running", ("emulator-5554", "com.example.app")) in lifecycle.calls
        assert ("is_running", 999999) not in process.calls

    def test_list_android_uses_lifecycle_not_host_pid(self) -> None:
        process = FakeProcess()
        lifecycle = FakeAndroidLifecycle()
        lifecycle.running[("emulator-5554", "com.example.app")] = True
        repo = FakeSessionRepository()
        repo.save(_make_android_session(app_pid=999999))

        uc = SessionListUseCase(
            session_repo=repo,
            process=process,
            clock=FakeClock(now_value=20.0),
            android_lifecycle=lifecycle,
        )

        entries = uc.execute()
        assert entries[0].status == "active"
        assert entries[0].uptime == 10.0
        assert ("is_running", 999999) not in process.calls


class TestAndroidLifecycleStop:
    def test_android_stop_force_stops_package_not_host_pid(self) -> None:
        process = FakeProcess()
        lifecycle = FakeAndroidLifecycle()
        repo = FakeSessionRepository()
        repo.save(_make_android_session(app_pid=999999))

        uc = SessionStopUseCase(
            display_server=FakeDisplayServer(),
            atspi_bus=FakeAccessibilityBus(),
            process=process,
            session_repo=repo,
            android_lifecycle=lifecycle,
        )

        result = uc.execute(session_id="android-47")

        assert result.status == "stopped"
        assert ("stop_app", ("emulator-5554", "com.example.app")) in lifecycle.calls
        assert ("stop", 999999) not in process.calls

    def test_android_stop_reports_force_stop_failure(self) -> None:
        process = FakeProcess()
        lifecycle = FakeAndroidLifecycle()
        lifecycle.stop_error = RuntimeError("adb failed")
        repo = FakeSessionRepository()
        repo.save(_make_android_session(app_pid=999999))

        uc = SessionStopUseCase(
            display_server=FakeDisplayServer(),
            atspi_bus=FakeAccessibilityBus(),
            process=process,
            session_repo=repo,
            android_lifecycle=lifecycle,
        )

        result = uc.execute(session_id="android-47")

        assert result.status == "stopped_with_errors"
        assert result.errors == ("android force-stop failed: adb failed",)
