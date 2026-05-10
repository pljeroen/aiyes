"""AIYES-54 release artifact gate and version consistency tests."""

from __future__ import annotations

import ast
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-artifacts.yml"
RELEASE_CHECK = PROJECT_ROOT / "scripts" / "release-check.sh"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
RUNTIME_VERSION = PROJECT_ROOT / "src" / "aiyes" / "__init__.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _position(content: str, needle: str) -> int:
    position = content.find(needle)
    assert position >= 0, f"missing required release gate content: {needle}"
    return position


def _runtime_version() -> str:
    module = ast.parse(_read(RUNTIME_VERSION))
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(statement.value)
                    assert isinstance(value, str)
                    return value
    raise AssertionError("src/aiyes/__init__.py does not define __version__")


def _project_version() -> str:
    match = re.search(r'^version = "([^"]+)"$', _read(PYPROJECT), re.MULTILINE)
    assert match is not None, "pyproject.toml does not define project version"
    return match.group(1)


class TestReleaseGate:
    def test_release_workflow_is_removed_for_public_repo_posture(self) -> None:
        assert not RELEASE_WORKFLOW.exists()

    def test_local_release_gate_runs_all_gates_before_sbom(
        self,
    ) -> None:
        content = _read(RELEASE_CHECK)

        required_in_order = [
            "rm -rf dist build src/aiyes.egg-info",
            "python -m ruff check src tests",
            "python -m mypy src/aiyes",
            "python -m pytest -q",
            "python -m build",
            "python -m twine check dist/*",
            'bin/pip-audit" --strict',
            'bin/cyclonedx-py" environment',
        ]

        positions = [_position(content, needle) for needle in required_in_order]
        assert positions == sorted(positions)

    def test_release_gate_requires_dev_security_tools(self) -> None:
        content = _read(RELEASE_CHECK)

        assert "missing required release tool" in content
        assert 'python -m pip install -e ".[dev]"' in content
        assert "pip-audit" in content
        assert "cyclonedx-py" in content

    def test_local_release_gate_has_no_publish_or_remote_ci_behavior(self) -> None:
        content = _read(RELEASE_CHECK)

        assert "twine upload" not in content
        assert "actions/" not in content
        assert "GITHUB_TOKEN" not in content


class TestVersionConsistency:
    def test_pyproject_version_matches_runtime_version(self) -> None:
        assert _project_version() == _runtime_version()

    def test_runtime_version_is_plain_pep440_release_number(self) -> None:
        assert re.fullmatch(r"\d+\.\d+\.\d+", _runtime_version())
