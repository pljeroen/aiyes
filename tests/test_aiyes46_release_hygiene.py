"""AIYES-46 public release hygiene contract tests."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
CI = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = PROJECT_ROOT / ".github" / "workflows" / "release-artifacts.yml"
SECURITY = PROJECT_ROOT / "SECURITY.md"
SMOKE = PROJECT_ROOT / "docs" / "release-smoke.md"
README = PROJECT_ROOT / "README.md"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
RELEASE_CHECK = PROJECT_ROOT / "scripts" / "release-check.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestLocalReleaseGate:
    def test_public_github_workflows_are_not_tracked(self) -> None:
        assert not CI.exists()
        assert not RELEASE.exists()

    def test_release_check_runs_quality_build_audit_and_sbom(self) -> None:
        content = _read(RELEASE_CHECK)

        for command in (
            "python -m ruff check src tests",
            "python -m mypy src/aiyes",
            "python -m pytest -q",
            "python -m build",
            "python -m twine check dist/*",
            'bin/pip-audit" --strict',
            'bin/cyclonedx-py" environment',
        ):
            assert command in content

        assert 'AUDIT_VENV="$(mktemp -d)"' in content
        assert 'python -m venv "$AUDIT_VENV"' in content
        assert "pip install pip-audit cyclonedx-bom" in content

    def test_dev_extra_contains_local_release_security_tools(self) -> None:
        content = _read(PYPROJECT)

        assert "pip-audit>=2.10.0" in content
        assert "cyclonedx-bom>=7.3.0" in content


class TestSecurityAndSmokeDocs:
    def test_security_policy_states_trusted_local_non_networked_mcp_boundary(self) -> None:
        content = _read(SECURITY)

        assert "trusted local" in content.lower()
        assert "local stdio" in content.lower()
        assert "Do not expose" in content
        assert "not a sandbox" in content.lower()
        assert "Release checks are maintainer-local" in content

    def test_readme_documents_maintainer_local_release_gate(self) -> None:
        content = _read(README)

        assert "Maintainer release gate" in content
        assert "Actions should remain disabled" in content
        assert "scripts/release-check.sh" in content
        assert "CycloneDX SBOM" in content

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
