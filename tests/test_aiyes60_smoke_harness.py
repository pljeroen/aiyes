"""AIYES-60: opt-in real smoke evidence harness."""

from __future__ import annotations

import json
from pathlib import Path

from aiyes.smoke_harness import (
    SMOKE_ENABLE_ENV,
    build_evidence,
    main,
    run_smoke_harness,
)


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, command: list[str]) -> FakeCompletedProcess:
        self.calls.append(command)
        return FakeCompletedProcess(returncode=self.returncode, stdout="ok")


class ScriptedRunner:
    def __init__(self, responses: list[FakeCompletedProcess]) -> None:
        self.calls: list[list[str]] = []
        self._responses = list(responses)

    def __call__(self, command: list[str]) -> FakeCompletedProcess:
        self.calls.append(command)
        return self._responses.pop(0)


class ScenarioBindingRunner:
    def __init__(
        self,
        responses: list[FakeCompletedProcess],
        original_scenario: Path,
    ) -> None:
        self.calls: list[list[str]] = []
        self.bound_documents: list[dict[str, object]] = []
        self.bound_paths: list[Path] = []
        self._responses = list(responses)
        self._original_scenario = original_scenario

    def __call__(self, command: list[str]) -> FakeCompletedProcess:
        self.calls.append(command)
        if command[:4] == ["aieyes", "scenario", "run", "--real"]:
            bound_path = Path(command[4])
            self.bound_paths.append(bound_path)
            assert bound_path != self._original_scenario
            self.bound_documents.append(
                json.loads(bound_path.read_text(encoding="utf-8"))
            )
        return self._responses.pop(0)


class MissingExecutableRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> FakeCompletedProcess:
        self.calls.append(command)
        raise FileNotFoundError(command[0])


def test_default_harness_emits_skipped_evidence_without_running_commands() -> None:
    runner = FakeRunner()

    evidence = run_smoke_harness(
        target="linux-gedit",
        enabled=False,
        runner=runner,
        now=lambda: "2026-05-06T00:00:00+00:00",
    )

    assert evidence["target"] == "linux-gedit"
    assert evidence["enabled"] is False
    assert evidence["status"] == "skipped"
    assert evidence["enable_env"] == SMOKE_ENABLE_ENV
    assert runner.calls == []
    assert all(step["status"] == "not_run" for step in evidence["steps"])


def test_enabled_harness_runs_target_steps_and_records_passed_evidence() -> None:
    runner = FakeRunner(returncode=0)

    evidence = run_smoke_harness(
        target="android-settings",
        enabled=True,
        runner=runner,
        now=lambda: "2026-05-06T00:00:00+00:00",
    )

    assert evidence["target"] == "android-settings"
    assert evidence["enabled"] is True
    assert evidence["status"] == "passed"
    assert len(runner.calls) == len(evidence["steps"])
    assert all(step["status"] == "passed" for step in evidence["steps"])
    assert any("examples/scenarios/android-settings.json" in step["command"] for step in evidence["steps"])


def test_enabled_harness_records_failed_step_and_stops() -> None:
    runner = FakeRunner(returncode=3)

    evidence = run_smoke_harness(
        target="linux-gedit",
        enabled=True,
        runner=runner,
        now=lambda: "2026-05-06T00:00:00+00:00",
    )

    assert evidence["status"] == "failed"
    assert len(runner.calls) == 1
    assert evidence["steps"][0]["status"] == "failed"
    assert evidence["steps"][0]["returncode"] == 3
    assert evidence["steps"][1]["status"] == "not_run"


def test_build_evidence_is_json_serializable() -> None:
    evidence = build_evidence(
        target="linux-gedit",
        enabled=False,
        now=lambda: "2026-05-06T00:00:00+00:00",
    )

    encoded = json.dumps(evidence)
    assert json.loads(encoded)["schema_version"] == 1


def test_main_writes_json_file_and_remains_opt_in(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"

    exit_code = main(
        ["--target", "linux-gedit", "--output", str(output)],
        environ={},
        runner=FakeRunner(),
        now=lambda: "2026-05-06T00:00:00+00:00",
    )

    assert exit_code == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["status"] == "skipped"
    assert data["enabled"] is False


def test_socialzzz_android_skips_with_precise_missing_env_without_commands() -> None:
    runner = FakeRunner()

    evidence = run_smoke_harness(
        target="socialzzz-android",
        enabled=True,
        environ={},
        runner=runner,
        now=lambda: "2026-06-05T00:00:00+00:00",
    )

    assert evidence["target"] == "socialzzz-android"
    assert evidence["status"] == "skipped"
    assert evidence["failure_reason"] == "missing_prerequisites"
    assert evidence["aiyes_version"] == "0.2.0"
    assert evidence["device_serial"] is None
    assert evidence["app_package"] is None
    assert evidence["scenario_names"] == [
        "compositor-share-image-happy-path",
        "compositor-share-video-happy-path",
        "demo-seed-smoke",
        "edit-target-market-regression",
        "delete-everything-dialog-smoke",
    ]
    assert evidence["missing_prerequisites"] == [
        {
            "code": "missing_env",
            "name": "AIYES_SOCIALZZZ_DEVICE_SERIAL",
            "reason": "set AIYES_SOCIALZZZ_DEVICE_SERIAL to the adb device serial",
        },
        {
            "code": "missing_env",
            "name": "AIYES_SOCIALZZZ_APP_PACKAGE",
            "reason": "set AIYES_SOCIALZZZ_APP_PACKAGE to the installed Socialzzz package",
        },
        {
            "code": "missing_env",
            "name": "AIYES_SOCIALZZZ_SCENARIO_DIR",
            "reason": "set AIYES_SOCIALZZZ_SCENARIO_DIR to the Socialzzz test/scenarios directory",
        },
    ]
    assert evidence["observed_scroll_methods"] == {
        "native_scroll": False,
        "region_swipe": False,
        "advisory_role": False,
        "strict_role": False,
        "scenarios": [],
    }
    assert runner.calls == []


def test_socialzzz_android_reports_missing_adb_separately(tmp_path: Path) -> None:
    (tmp_path / "demo-seed-smoke.json").write_text("{}", encoding="utf-8")
    runner = MissingExecutableRunner()

    evidence = run_smoke_harness(
        target="socialzzz-android",
        enabled=True,
        environ={
            "AIYES_SOCIALZZZ_DEVICE_SERIAL": "emulator-5554",
            "AIYES_SOCIALZZZ_APP_PACKAGE": "com.socialzzz.socialzzz",
            "AIYES_SOCIALZZZ_SCENARIO_DIR": str(tmp_path),
            "AIYES_SOCIALZZZ_SCENARIOS": "demo-seed-smoke.json",
        },
        runner=runner,
        now=lambda: "2026-06-05T00:00:00+00:00",
    )

    assert evidence["status"] == "skipped"
    assert evidence["missing_prerequisites"] == [
        {
            "code": "missing_adb",
            "name": "AIYES_ANDROID_ADB",
            "reason": "adb executable not found; set AIYES_ANDROID_ADB or add adb to PATH",
        }
    ]
    assert evidence["steps"] == [
        {
            "name": "adb-device",
            "command": ["adb", "-s", "emulator-5554", "get-state"],
            "status": "skipped",
            "failure_reason": (
                "adb executable not found; set AIYES_ANDROID_ADB or add adb to PATH"
            ),
        }
    ]
    assert len(runner.calls) == 1


def test_socialzzz_android_skips_when_package_lookup_is_empty(tmp_path: Path) -> None:
    (tmp_path / "demo-seed-smoke.json").write_text("{}", encoding="utf-8")
    runner = ScriptedRunner(
        [
            FakeCompletedProcess(returncode=0, stdout="device\n"),
            FakeCompletedProcess(returncode=0, stdout=""),
        ]
    )

    evidence = run_smoke_harness(
        target="socialzzz-android",
        enabled=True,
        environ={
            "AIYES_SOCIALZZZ_DEVICE_SERIAL": "emulator-5554",
            "AIYES_SOCIALZZZ_APP_PACKAGE": "com.socialzzz.socialzzz",
            "AIYES_SOCIALZZZ_SCENARIO_DIR": str(tmp_path),
            "AIYES_SOCIALZZZ_SCENARIOS": "demo-seed-smoke.json",
        },
        runner=runner,
        now=lambda: "2026-06-05T00:00:00+00:00",
    )

    assert evidence["status"] == "skipped"
    assert evidence["missing_prerequisites"] == [
        {
            "code": "missing_android_package",
            "name": "socialzzz-package",
            "reason": (
                "Socialzzz package is not installed on emulator-5554: "
                "com.socialzzz.socialzzz"
            ),
        }
    ]
    assert [call[:3] for call in runner.calls] == [
        ["adb", "-s", "emulator-5554"],
        ["adb", "-s", "emulator-5554"],
    ]


def test_socialzzz_android_records_schema_and_observed_scroll_methods(
    tmp_path: Path,
) -> None:
    for name in ("compositor-share-image-happy-path.json",):
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "compositor-share-image-happy-path",
                    "title": "Socialzzz smoke",
                    "target": "android",
                    "prerequisites": [{"kind": "android_device", "serial": "auto"}],
                    "steps": [
                        {
                            "id": "start",
                            "kind": "start_session",
                            "backend": "android",
                            "device_serial": "auto",
                            "command": [
                                "adb",
                                "shell",
                                "monkey",
                                "-p",
                                "com.other.app",
                                "1",
                            ],
                        }
                    ],
                    "cleanup": [{"id": "stop", "kind": "stop_session"}],
                    "evidence_policy": {"bundle": True},
                }
            ),
            encoding="utf-8",
        )
    (tmp_path / "edit-target-market-regression.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "edit-target-market-regression",
                "title": "Socialzzz regression",
                "target": "android",
                "steps": [{"id": "start", "kind": "start_session"}],
                "cleanup": [{"id": "stop", "kind": "stop_session"}],
                "evidence_policy": {"bundle": True},
            }
        ),
        encoding="utf-8",
    )
    runner = ScriptedRunner(
        [
            FakeCompletedProcess(returncode=0, stdout="device\n"),
            FakeCompletedProcess(
                returncode=0,
                stdout="package:/data/app/com.socialzzz.socialzzz/base.apk\n",
            ),
            FakeCompletedProcess(
                returncode=0,
                stdout=json.dumps(
                    {
                        "scenario_id": "compositor-share-image-happy-path",
                        "status": "passed",
                        "failure_code": "",
                        "steps": [
                            {
                                "step_id": "share_linkedin",
                                "kind": "scroll_into_view",
                                "status": "passed",
                                "error": "",
                                "output": {
                                    "role_match": "exact",
                                    "scroll_attempts": [
                                        {"method": "native_scroll"},
                                        {"method": "scrollable_region_swipe"},
                                    ],
                                },
                            }
                        ],
                    }
                ),
            ),
            FakeCompletedProcess(
                returncode=0,
                stdout=json.dumps(
                    {
                        "scenario_id": "edit-target-market-regression",
                        "status": "passed",
                        "failure_code": "",
                        "steps": [
                            {
                                "step_id": "target_markets",
                                "kind": "scroll_into_view",
                                "status": "passed",
                                "error": "",
                                "output": {
                                    "role_match": "advisory",
                                    "requested_role": "View",
                                    "actual_role": "Button",
                                    "scroll_attempts": [],
                                },
                            }
                        ],
                    }
                ),
            ),
        ]
    )

    evidence = run_smoke_harness(
        target="socialzzz-android",
        enabled=True,
        environ={
            "AIYES_SOCIALZZZ_DEVICE_SERIAL": "emulator-5554",
            "AIYES_SOCIALZZZ_APP_PACKAGE": "com.socialzzz.socialzzz",
            "AIYES_SOCIALZZZ_SCENARIO_DIR": str(tmp_path),
            "AIYES_SOCIALZZZ_SCENARIOS": (
                "compositor-share-image-happy-path.json,"
                "edit-target-market-regression.json"
            ),
        },
        runner=runner,
        now=lambda: "2026-06-05T00:00:00+00:00",
    )

    assert evidence["status"] == "passed"
    assert evidence["aiyes_version"] == "0.2.0"
    assert evidence["device_serial"] == "emulator-5554"
    assert evidence["app_package"] == "com.socialzzz.socialzzz"
    assert evidence["scenario_names"] == [
        "compositor-share-image-happy-path",
        "edit-target-market-regression",
    ]
    assert evidence["missing_prerequisites"] == []
    assert evidence["observed_scroll_methods"] == {
        "native_scroll": True,
        "region_swipe": True,
        "advisory_role": True,
        "strict_role": True,
        "scenarios": [
            {
                "name": "compositor-share-image-happy-path",
                "native_scroll": True,
                "region_swipe": True,
                "advisory_role": False,
                "strict_role": True,
                "steps": [
                    {
                        "step_id": "share_linkedin",
                        "kind": "scroll_into_view",
                        "status": "passed",
                        "native_scroll": True,
                        "region_swipe": True,
                        "advisory_role": False,
                        "strict_role": True,
                    }
                ],
            },
            {
                "name": "edit-target-market-regression",
                "native_scroll": False,
                "region_swipe": False,
                "advisory_role": True,
                "strict_role": False,
                "steps": [
                    {
                        "step_id": "target_markets",
                        "kind": "scroll_into_view",
                        "status": "passed",
                        "native_scroll": False,
                        "region_swipe": False,
                        "advisory_role": True,
                        "strict_role": False,
                    }
                ],
            },
        ],
    }
    assert [step["name"] for step in evidence["steps"]] == [
        "adb-device",
        "socialzzz-package",
        "compositor-share-image-happy-path",
        "edit-target-market-regression",
    ]
    assert evidence["steps"][2]["failure_reason"] == ""
    assert json.loads(evidence["steps"][2]["stdout"])["scenario_id"] == (
        "compositor-share-image-happy-path"
    )


def test_socialzzz_android_binds_scenario_to_requested_device_and_package(
    tmp_path: Path,
) -> None:
    original = tmp_path / "demo-seed-smoke.json"
    original.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "demo-seed-smoke",
                "title": "Socialzzz demo smoke",
                "target": "android",
                "prerequisites": [{"kind": "android_device", "serial": "auto"}],
                "steps": [
                    {
                        "id": "start",
                        "kind": "start_session",
                        "backend": "android",
                        "name": "demo",
                        "device_serial": "auto",
                        "command": [
                            "adb",
                            "shell",
                            "monkey",
                            "-p",
                            "com.other.app",
                            "1",
                        ],
                        "wait_seconds": 0,
                    },
                    {"id": "inspect", "kind": "inspect"},
                ],
                "cleanup": [{"id": "stop", "kind": "stop_session"}],
                "evidence_policy": {"bundle": True},
            }
        ),
        encoding="utf-8",
    )
    runner = ScenarioBindingRunner(
        [
            FakeCompletedProcess(returncode=0, stdout="device\n"),
            FakeCompletedProcess(
                returncode=0,
                stdout="package:/data/app/com.socialzzz.socialzzz/base.apk\n",
            ),
            FakeCompletedProcess(
                returncode=0,
                stdout=json.dumps(
                    {
                        "scenario_id": "demo-seed-smoke",
                        "status": "passed",
                        "failure_code": "",
                        "steps": [],
                    }
                ),
            ),
        ],
        original_scenario=original,
    )

    evidence = run_smoke_harness(
        target="socialzzz-android",
        enabled=True,
        environ={
            "AIYES_SOCIALZZZ_DEVICE_SERIAL": "emulator-5554",
            "AIYES_SOCIALZZZ_APP_PACKAGE": "com.socialzzz.socialzzz",
            "AIYES_SOCIALZZZ_SCENARIO_DIR": str(tmp_path),
            "AIYES_SOCIALZZZ_SCENARIOS": "demo-seed-smoke.json",
            "AIYES_ANDROID_ADB": "/opt/android-sdk/platform-tools/adb",
        },
        runner=runner,
        now=lambda: "2026-06-05T00:00:00+00:00",
    )

    assert evidence["status"] == "passed"
    assert len(runner.bound_documents) == 1
    bound = runner.bound_documents[0]
    assert bound["prerequisites"] == [
        {"kind": "android_device", "serial": "emulator-5554"}
    ]
    assert bound["steps"][0]["device_serial"] == "emulator-5554"
    assert bound["steps"][0]["command"] == [
        "/opt/android-sdk/platform-tools/adb",
        "-s",
        "emulator-5554",
        "shell",
        "monkey",
        "-p",
        "com.socialzzz.socialzzz",
        "1",
    ]
    assert bound["steps"][1] == {"id": "inspect", "kind": "inspect"}


def test_release_doc_mentions_socialzzz_opt_in_without_contract_methodology() -> None:
    content = (Path(__file__).parent.parent / "docs" / "release-smoke.md").read_text(
        encoding="utf-8"
    )

    assert "socialzzz-android" in content
    assert "AIYES_SOCIALZZZ_DEVICE_SERIAL" in content
    assert "AIYES_SOCIALZZZ_APP_PACKAGE" in content
    assert "AIYES_SOCIALZZZ_SCENARIO_DIR" in content
    for forbidden in ("AIYES-100", "CONTRACT_INTAKE", "TDDv6", "review artifact"):
        assert forbidden not in content
