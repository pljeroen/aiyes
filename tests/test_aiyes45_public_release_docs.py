"""AIYES-45 public release documentation contract tests."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
README = PROJECT_ROOT / "README.md"
MANUAL = PROJECT_ROOT / "docs" / "manual.md"
EXAMPLES = PROJECT_ROOT / "examples"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestReadmeReleasePositioning:
    def test_readme_has_current_test_badge(self) -> None:
        content = _read(README)

        assert "tests-pytest" in content
        assert "tests-1892" not in content
        assert "tests-1885" not in content
        assert "tests-1745" not in content

    def test_readme_states_trusted_local_threat_model_near_setup(self) -> None:
        content = _read(README)

        assert "trusted local" in content.lower()
        assert "not a sandbox" in content.lower()
        assert "do not expose" in content.lower()
        assert "remote" in content.lower()

    def test_readme_has_backend_capability_matrix_with_android_limits(self) -> None:
        content = _read(README)

        assert "Backend capability matrix" in content
        assert "Linux" in content
        assert "Android" in content
        assert "wait-stable" in content
        assert "diff" in content
        assert "fewer states" in content.lower()
        assert "no resize" in content.lower()

    def test_readme_has_five_minute_success_path(self) -> None:
        content = _read(README)

        assert "5-minute success path" in content
        for command in (
            "pip install",
            "aieyes doctor",
            "aieyes session start",
            "aieyes inspect",
            "aieyes find",
            "aieyes action",
            "aieyes session stop",
        ):
            assert command in content


class TestManualReleasePositioning:
    def test_manual_repeats_android_limitations_and_local_mcp_boundary(self) -> None:
        content = _read(MANUAL)

        assert "Android limitations" in content
        assert "local stdio" in content
        assert "Do not expose" in content

    def test_manual_uses_actual_do_command_options(self) -> None:
        content = _read(MANUAL)

        assert "aieyes do --role button --name Submit --action click --verify" in content
        assert "do --find" not in content


class TestRunnableExamples:
    def test_required_example_files_exist(self) -> None:
        expected = {
            "linux-gedit-smoke.md",
            "linux-browser-form.md",
            "android-basic-flow.md",
            "mcp-claude-code.md",
        }

        actual = {path.name for path in EXAMPLES.glob("*.md")}
        assert expected.issubset(actual)

    def test_examples_cover_observe_act_verify_cleanup_loop(self) -> None:
        for name in (
            "linux-gedit-smoke.md",
            "linux-browser-form.md",
            "android-basic-flow.md",
            "mcp-claude-code.md",
        ):
            path = EXAMPLES / name
            content = _read(path)

            assert "aieyes session start" in content or "claude mcp add" in content
            assert "inspect" in content
            assert "find" in content or "do" in content
            assert "wait" in content or "verify" in content.lower()
            assert "session stop" in content

    def test_android_example_launches_through_adb_command(self) -> None:
        content = _read(EXAMPLES / "android-basic-flow.md")

        assert "adb -s emulator-5554 shell monkey -p com.example.app 1" in content
        assert "-- com.example.app" not in content
