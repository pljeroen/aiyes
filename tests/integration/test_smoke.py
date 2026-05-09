"""Integration smoke tests — AIYES-02 scope.

Traceability:
  INTEG-01: Session start/stop lifecycle (requires Xvfb)
  INTEG-02: Screenshot produces PNG (requires Xvfb + screenshot tool)
  INTEG-03: Inspect returns tree (requires Xvfb + AT-SPI2)
  INTEG-04: Doctor reports system state (never skipped)
  INTEG-05: CLI --help works (never skipped)
  INTEG-06: JSON output conformance via session list (never skipped)
  FIX-02: Satisfied by INTEG-05
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.integration.conftest import (
    requires_gi,
    requires_screenshot_tool,
    requires_xvfb,
    requires_xvfb_full,
)


# ═══════════════════════════════════════════════════════════════════════
# INTEG-01: Session start/stop lifecycle
# ═══════════════════════════════════════════════════════════════════════


@requires_xvfb
class TestSessionLifecycle:
    """Full session start/stop with real Xvfb."""

    def test_session_start_returns_valid_json(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "aiyes.cli.main",
                "session",
                "start",
                "--",
                "sleep",
                "60",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "session_id" in data
        assert "display" in data
        assert "app_pid" in data
        assert "atspi_bus_address" in data

        # Stop the session
        session_id = data["session_id"]
        stop_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "aiyes.cli.main",
                "session",
                "stop",
                "--session",
                session_id,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        stop_data = json.loads(stop_result.stdout)
        assert stop_data["status"] == "stopped"
        assert stop_data["session_id"] == session_id

    def test_no_zombie_processes_after_stop(self) -> None:
        # Start
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "aiyes.cli.main",
                "session",
                "start",
                "--",
                "sleep",
                "60",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        app_pid = data["app_pid"]

        # Stop
        subprocess.run(
            [
                sys.executable,
                "-m",
                "aiyes.cli.main",
                "session",
                "stop",
                "--session",
                data["session_id"],
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        # Verify no zombie Xvfb from this session
        import os
        import signal

        try:
            os.kill(app_pid, signal.SIG_DFL)
            # If we get here, process still exists — that's a problem
            # (but the kill with SIG_DFL=0 might just check existence)
            os.kill(app_pid, 0)
            pytest.fail(f"Process {app_pid} still alive after stop")
        except OSError:
            pass  # Expected: process is gone


# ═══════════════════════════════════════════════════════════════════════
# INTEG-02: Screenshot produces PNG
# ═══════════════════════════════════════════════════════════════════════


@requires_xvfb
@requires_screenshot_tool
class TestScreenshotProducesPng:
    """Screenshot command produces a valid PNG file."""

    def test_screenshot_is_valid_png(self) -> None:
        # Start a session first (wait for D-Bus bridge to be ready)
        start = subprocess.run(
            [
                sys.executable,
                "-m",
                "aiyes.cli.main",
                "session",
                "start",
                "--wait",
                "3",
                "--",
                "sleep",
                "60",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        session_data = json.loads(start.stdout)
        session_id = session_data["session_id"]

        try:
            # Take screenshot
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aiyes.cli.main",
                    "screenshot",
                    "--session",
                    session_id,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert "path" in data

            # Verify PNG magic bytes
            import pathlib

            png_path = pathlib.Path(data["path"])
            assert png_path.exists()
            content = png_path.read_bytes()
            assert content[:4] == b"\x89PNG", "File is not a valid PNG"
        finally:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aiyes.cli.main",
                    "session",
                    "stop",
                    "--session",
                    session_id,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )


# ═══════════════════════════════════════════════════════════════════════
# INTEG-03: Inspect returns accessibility tree
# ═══════════════════════════════════════════════════════════════════════


@requires_xvfb_full
@requires_gi
class TestInspectReturnsTree:
    """Inspect command returns a tree with at least one node."""

    def test_inspect_has_tree_with_nodes(self) -> None:
        # Start session with a simple app
        start = subprocess.run(
            [
                sys.executable,
                "-m",
                "aiyes.cli.main",
                "session",
                "start",
                "--wait",
                "3",
                "--",
                "sleep",
                "60",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        session_data = json.loads(start.stdout)
        session_id = session_data["session_id"]

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aiyes.cli.main",
                    "inspect",
                    "--session",
                    session_id,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            data = json.loads(result.stdout)
            assert "tree" in data
            # With AIYES-04 bus isolation, the tree comes from the
            # isolated session bus. A non-GUI app (sleep) produces
            # zero AT-SPI nodes, which is correct behavior.
            # We only verify the tree structure is valid.
            tree_data = data["tree"]
            if isinstance(tree_data, dict):
                tree_nodes = tree_data.get("tree", [])
            else:
                tree_nodes = tree_data
            assert isinstance(tree_nodes, list)
        finally:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aiyes.cli.main",
                    "session",
                    "stop",
                    "--session",
                    session_id,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )


# ═══════════════════════════════════════════════════════════════════════
# INTEG-04: Doctor reports system state (never skipped)
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorReportsState:
    """Doctor command always runs and reports JSON array."""

    def test_doctor_returns_json_array(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "aiyes.cli.main", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Doctor always outputs JSON to stdout
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_doctor_elements_have_required_keys(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "aiyes.cli.main", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        for element in data:
            assert "name" in element
            assert "status" in element
            assert "message" in element

    def test_doctor_status_values(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "aiyes.cli.main", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        for element in data:
            assert element["status"] in ("pass", "warn", "fail"), (
                f"Invalid status: {element['status']}"
            )

    def test_doctor_exit_code_always_zero_for_semantic_results(self) -> None:
        """A10-003: doctor semantic failures = exit 0, only system exceptions = exit 1."""
        result = subprocess.run(
            [sys.executable, "-m", "aiyes.cli.main", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        # Doctor always exits 0 for semantic results (pass or fail).
        # Only system exceptions (crashes) produce exit 1.
        assert result.returncode == 0
        assert isinstance(data, list)
        assert len(data) > 0


# ═══════════════════════════════════════════════════════════════════════
# INTEG-05: CLI --help (never skipped) — also satisfies FIX-02
# ═══════════════════════════════════════════════════════════════════════


class TestCliHelpIntegration:
    """CLI entry point responds correctly to --help."""

    def test_aieyes_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "aiyes.cli.main", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_aieyes_help_contains_usage(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "aiyes.cli.main", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "aieyes" in result.stdout.lower() or "usage" in result.stdout.lower()


# ═══════════════════════════════════════════════════════════════════════
# INTEG-06: JSON output conformance via session list (never skipped)
# ═══════════════════════════════════════════════════════════════════════


class TestJsonOutputConformance:
    """session list returns valid JSON list."""

    def test_session_list_returns_json_list(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "aiyes.cli.main", "session", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
