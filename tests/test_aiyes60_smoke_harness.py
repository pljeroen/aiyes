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
