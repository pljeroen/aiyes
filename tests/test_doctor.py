"""Tests for the doctor command.

Requirements covered:
  R-ARCH-07: aieyes doctor checks system dependencies
  R-SYS-04:  Dependency presence (xvfb, scrot/import, xdotool, at-spi2, python3-gi, gir1.2)
"""

from __future__ import annotations


# RED imports — define expected API
from aiyes.domain.types import DependencyResult
from aiyes.domain.use_cases.doctor import DoctorUseCase

from tests.conftest import FakeDependencyCheck


# ──────────────────────────────────────────────────────────────────────
# R-ARCH-07: Doctor use case
# ──────────────────────────────────────────────────────────────────────


class TestDoctor:
    """Doctor command checks system dependencies."""

    def test_doctor_returns_check_list(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07: Doctor returns list of check results."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        assert isinstance(result, list)
        assert len(result) > 0

    def test_doctor_checks_xvfb(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07, R-SYS-04: Doctor checks Xvfb availability."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        check_names = [r.name for r in result]
        assert "xvfb" in check_names

    def test_doctor_checks_screenshot_tool(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07, R-SYS-04: Doctor checks screenshot tool (scrot/import)."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        check_names = [r.name for r in result]
        assert "screenshot_tool" in check_names

    def test_doctor_checks_xdotool(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07, R-SYS-04: Doctor checks xdotool."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        check_names = [r.name for r in result]
        assert "xdotool" in check_names

    def test_doctor_checks_atspi(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07, R-SYS-04: Doctor checks at-spi2-core."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        check_names = [r.name for r in result]
        assert "at-spi2-core" in check_names

    def test_doctor_checks_python3_gi(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07, R-SYS-04: Doctor checks python3-gi."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        check_names = [r.name for r in result]
        assert "python3-gi" in check_names

    def test_doctor_checks_gir_atspi(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07, R-SYS-04, CV-07: Doctor checks gir1.2-atspi-2.0."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        check_names = [r.name for r in result]
        assert "gir1.2-atspi-2.0" in check_names

    def test_doctor_each_check_has_required_fields(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07: Each check result has name, status, message."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        for check in result:
            assert isinstance(check, DependencyResult)
            assert check.name
            assert check.status in ("pass", "fail", "warn")
            assert check.message

    def test_doctor_all_pass(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07: When all checks pass, result is all-pass."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        # Default fake has all passing
        statuses = [r.status for r in result]
        assert all(s == "pass" for s in statuses)

    def test_doctor_with_failure(self) -> None:
        """R-ARCH-07: When a check fails, it's reported as fail."""
        failing_check = FakeDependencyCheck(
            results={
                "xvfb": DependencyResult(
                    name="xvfb", status="fail", message="xvfb not found"
                ),
            }
        )

        uc = DoctorUseCase(dependency_check=failing_check)
        result = uc.execute()

        xvfb_result = next(r for r in result if r.name == "xvfb")
        assert xvfb_result.status == "fail"

    def test_doctor_has_all_checks(
        self,
        fake_dependency_check: FakeDependencyCheck,
    ) -> None:
        """R-ARCH-07, R-SYS-04, R-ISO-04: Doctor checks all dependencies."""
        uc = DoctorUseCase(dependency_check=fake_dependency_check)
        result = uc.execute()

        expected = {
            "xvfb",
            "screenshot_tool",
            "xdotool",
            "xclip",
            "at-spi2-core",
            "python3-gi",
            "gir1.2-atspi-2.0",
            "mesa-software-rendering",
            "mesa-vulkan-software",
            "adb",
            "android_device",
            "imagemagick",
        }
        actual = {r.name for r in result}
        assert actual == expected
