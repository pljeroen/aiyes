"""AIYES-46 public release hygiene contract tests."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
CI = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = PROJECT_ROOT / ".github" / "workflows" / "release-artifacts.yml"
SECURITY = PROJECT_ROOT / "SECURITY.md"
SMOKE = PROJECT_ROOT / "docs" / "release-smoke.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestCiReleaseGate:
    def test_ci_has_least_privilege_permissions(self) -> None:
        content = _read(CI)

        assert "permissions:" in content
        assert "contents: read" in content

    def test_ci_runs_public_quality_gates(self) -> None:
        content = _read(CI)

        for command in (
            "ruff check src/ tests/",
            "mypy src/aiyes/",
            "python -m pytest -q",
            "python -m build",
            "python -m twine check dist/*",
        ):
            assert command in content

    def test_ci_does_not_ignore_test_files(self) -> None:
        content = _read(CI)

        assert "--ignore=tests/" not in content


class TestReleaseArtifactWorkflow:
    def test_release_artifact_workflow_builds_dist_without_publishing(self) -> None:
        content = _read(RELEASE)

        assert "on:" in content
        assert "workflow_dispatch:" in content
        assert "tags:" in content
        assert "python -m build" in content
        assert "python -m twine check dist/*" in content
        assert "actions/upload-artifact" in content
        assert "twine upload" not in content


class TestSecurityAndSmokeDocs:
    def test_security_policy_states_trusted_local_non_networked_mcp_boundary(self) -> None:
        content = _read(SECURITY)

        assert "trusted local" in content.lower()
        assert "local stdio" in content.lower()
        assert "Do not expose" in content
        assert "not a sandbox" in content.lower()

    def test_release_smoke_doc_records_linux_and_android_checks(self) -> None:
        content = _read(SMOKE)

        for phrase in (
            "Linux smoke",
            "Android smoke",
            "aieyes doctor",
            "aieyes session start",
            "aieyes inspect",
            "aieyes find",
            "aieyes action",
            "aieyes session stop",
            "adb devices",
        ):
            assert phrase in content
