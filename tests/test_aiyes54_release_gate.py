"""AIYES-54 release artifact gate and version consistency tests."""

from __future__ import annotations

import ast
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-artifacts.yml"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
RUNTIME_VERSION = PROJECT_ROOT / "src" / "aiyes" / "__init__.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _position(content: str, needle: str) -> int:
    position = content.find(needle)
    assert position >= 0, f"missing required release workflow content: {needle}"
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


class TestReleaseWorkflowGates:
    def test_release_artifact_workflow_runs_all_gates_before_build_and_upload(
        self,
    ) -> None:
        content = _read(RELEASE_WORKFLOW)

        required_in_order = [
            'test "$(git rev-parse HEAD)" = "$RELEASE_SHA"',
            "Project version matches runtime version",
            "Tag version matches project version",
            "ruff check src/ tests/",
            "mypy src/aiyes/",
            "python -m pytest -q",
            "python -m build",
            "python -m twine check dist/*",
            "python -m venv /tmp/aiyes-wheel-smoke",
            "aieyes --help",
            "aieyes --version",
            "python -m venv /tmp/aiyes-mcp-missing-extra",
            "aieyes-mcp",
            "requires the 'mcp' package",
            "python -m venv /tmp/aiyes-mcp-installed-extra",
            "pip install 'dist/'*.whl'[mcp]'",
            "import aiyes.adapters.mcp_server as mcp_server",
            "assert mcp_server._MCP_AVAILABLE is True",
            "actions/upload-artifact@v4",
        ]

        positions = [_position(content, needle) for needle in required_in_order]
        assert positions == sorted(positions)

    def test_release_build_job_installs_release_scope_dependencies(self) -> None:
        content = _read(RELEASE_WORKFLOW)

        assert 'python -m pip install -e ".[dev]"' in content
        assert "python -m pip install build twine" not in content

    def test_tag_and_manual_release_paths_share_same_job_without_bypass(self) -> None:
        content = _read(RELEASE_WORKFLOW)

        assert "workflow_dispatch:" in content
        assert "tags:" in content
        assert content.count("actions/upload-artifact@v4") == 1
        assert "twine upload" not in content
        assert "if: github.ref_type == 'tag'" not in content


class TestVersionConsistency:
    def test_pyproject_version_matches_runtime_version(self) -> None:
        assert _project_version() == _runtime_version()

    def test_runtime_version_is_plain_pep440_release_number(self) -> None:
        assert re.fullmatch(r"\d+\.\d+\.\d+", _runtime_version())
