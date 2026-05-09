"""Tests for AIYES-10: Environment Gating (R-TDDV6-05).

Requirements covered:
  R-TDDV6-05.1: available() function (AC-01 through AC-05)
  R-TDDV6-05.2: No import side effects (AC-06, AC-07)
  R-TDDV6-05.3: gui_runtime marker (AC-08, AC-09, AC-10)
  R-TDDV6-05.4: gui_session fixture (AC-11, AC-12, AC-13, AC-14)
  ARCH-01, ARCH-03: Architectural constraints
  WIRE: Wiring tests for gui_session delegation
"""

from __future__ import annotations

import ast
import itertools
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "aiyes"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _fake_which(binaries_present: Dict[str, Optional[str]]):
    """Return a shutil.which replacement that returns paths for given binaries.

    Args:
        binaries_present: mapping of binary name to path (or None if absent).
    """

    def _which(name: str) -> Optional[str]:
        return binaries_present.get(name)

    return _which


def _get_imports_from_file(filepath: Path) -> List[str]:
    """Parse a Python file and extract all import module names."""
    source = filepath.read_text()
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


# Known stdlib prefixes — duplicated from test_architecture.py for isolation.
_STDLIB_PREFIXES = frozenset(
    [
        "abc",
        "ast",
        "builtins",
        "collections",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "io",
        "itertools",
        "json",
        "math",
        "os",
        "pathlib",
        "re",
        "shutil",
        "signal",
        "stat",
        "string",
        "subprocess",
        "sys",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "typing",
        "types",
        "unittest",
        "uuid",
        "warnings",
        "__future__",
    ]
)


# ═══════════════════════════════════════════════════════════════════════
# R-TDDV6-05.1: available() function
# ═══════════════════════════════════════════════════════════════════════


class TestAvailableAllPresent:
    """R-TDDV6-05.1 AC-01: available() returns True when all prerequisites exist."""

    @patch("shutil.which")
    def test_available_all_present_returns_true(self, mock_which: MagicMock) -> None:
        """R-TDDV6-05.1 AC-01: Returns True when Xvfb, xdotool, scrot on PATH."""
        mock_which.side_effect = _fake_which(
            {
                "Xvfb": "/usr/bin/Xvfb",
                "xdotool": "/usr/bin/xdotool",
                "scrot": "/usr/bin/scrot",
                "import": "/usr/bin/import",
            }
        )

        from aiyes.domain.environment import available

        result = available()

        assert result is True
        # Postcondition: verify it actually checked the required binaries
        called_binaries = {c.args[0] for c in mock_which.call_args_list}
        assert "Xvfb" in called_binaries
        assert "xdotool" in called_binaries
        assert "scrot" in called_binaries or "import" in called_binaries


class TestAvailableXvfbMissing:
    """R-TDDV6-05.1 AC-02: available() returns False when Xvfb missing."""

    @patch("shutil.which")
    def test_available_xvfb_missing_returns_false(self, mock_which: MagicMock) -> None:
        """R-TDDV6-05.1 AC-02: Returns False when Xvfb absent, others present."""
        mock_which.side_effect = _fake_which(
            {
                "Xvfb": None,
                "xdotool": "/usr/bin/xdotool",
                "scrot": "/usr/bin/scrot",
                "import": "/usr/bin/import",
            }
        )

        from aiyes.domain.environment import available

        result = available()

        assert result is False


class TestAvailableXdotoolMissing:
    """R-TDDV6-05.1 AC-03: available() returns False when xdotool missing."""

    @patch("shutil.which")
    def test_available_xdotool_missing_returns_false(
        self, mock_which: MagicMock
    ) -> None:
        """R-TDDV6-05.1 AC-03: Returns False when xdotool absent, others present."""
        mock_which.side_effect = _fake_which(
            {
                "Xvfb": "/usr/bin/Xvfb",
                "xdotool": None,
                "scrot": "/usr/bin/scrot",
                "import": "/usr/bin/import",
            }
        )

        from aiyes.domain.environment import available

        result = available()

        assert result is False


class TestAvailableNoScreenshotTool:
    """R-TDDV6-05.1 AC-04: available() returns False when neither scrot nor import."""

    @patch("shutil.which")
    def test_available_no_screenshot_tool_returns_false(
        self, mock_which: MagicMock
    ) -> None:
        """R-TDDV6-05.1 AC-04: Returns False when both scrot and import absent."""
        mock_which.side_effect = _fake_which(
            {
                "Xvfb": "/usr/bin/Xvfb",
                "xdotool": "/usr/bin/xdotool",
                "scrot": None,
                "import": None,
            }
        )

        from aiyes.domain.environment import available

        result = available()

        assert result is False


class TestAvailableReturnType:
    """R-TDDV6-05.1 AC-05: available() returns strict bool type."""

    @patch("shutil.which")
    def test_available_return_type_is_bool_when_true(
        self, mock_which: MagicMock
    ) -> None:
        """R-TDDV6-05.1 AC-05: Return type is exactly bool (True case)."""
        mock_which.side_effect = _fake_which(
            {
                "Xvfb": "/usr/bin/Xvfb",
                "xdotool": "/usr/bin/xdotool",
                "scrot": "/usr/bin/scrot",
                "import": None,
            }
        )

        from aiyes.domain.environment import available

        result = available()

        assert type(result) is bool

    @patch("shutil.which")
    def test_available_return_type_is_bool_when_false(
        self, mock_which: MagicMock
    ) -> None:
        """R-TDDV6-05.1 AC-05: Return type is exactly bool (False case)."""
        mock_which.side_effect = _fake_which(
            {
                "Xvfb": None,
                "xdotool": None,
                "scrot": None,
                "import": None,
            }
        )

        from aiyes.domain.environment import available

        result = available()

        assert type(result) is bool


class TestAvailableBiconditionalTruthTable:
    """R-TDDV6-05.1: Biconditional — True IFF Xvfb AND xdotool AND (scrot OR import)."""

    @pytest.mark.parametrize(
        "xvfb,xdotool,scrot,import_tool",
        list(itertools.product([True, False], repeat=4)),
        ids=[
            f"Xvfb={'Y' if x else 'N'}_xdotool={'Y' if d else 'N'}_scrot={'Y' if s else 'N'}_import={'Y' if i else 'N'}"
            for x, d, s, i in itertools.product([True, False], repeat=4)
        ],
    )
    @patch("shutil.which")
    def test_available_truth_table(
        self,
        mock_which: MagicMock,
        xvfb: bool,
        xdotool: bool,
        scrot: bool,
        import_tool: bool,
    ) -> None:
        """R-TDDV6-05.1 AC-01..04: Exhaustive truth table (16 combinations)."""
        mock_which.side_effect = _fake_which(
            {
                "Xvfb": "/usr/bin/Xvfb" if xvfb else None,
                "xdotool": "/usr/bin/xdotool" if xdotool else None,
                "scrot": "/usr/bin/scrot" if scrot else None,
                "import": "/usr/bin/import" if import_tool else None,
            }
        )

        from aiyes.domain.environment import available

        expected = xvfb and xdotool and (scrot or import_tool)
        result = available()

        assert result is expected, (
            f"available() returned {result}, expected {expected} for "
            f"Xvfb={xvfb}, xdotool={xdotool}, scrot={scrot}, import={import_tool}"
        )
        # Postcondition: return type is always bool
        assert type(result) is bool


# ═══════════════════════════════════════════════════════════════════════
# R-TDDV6-05.2: No import side effects
# ═══════════════════════════════════════════════════════════════════════


class TestImportNoProcessSpawn:
    """R-TDDV6-05.2 AC-06: import aiyes does not spawn additional processes."""

    def test_import_aiyes_no_process_spawn(self) -> None:
        """R-TDDV6-05.2 AC-06: import aiyes does not spawn child processes."""
        # Run in subprocess to get clean import state and measure PIDs
        script = (
            "import os, glob\n"
            "def child_pids():\n"
            "    my_pid = str(os.getpid())\n"
            "    count = 0\n"
            "    for entry in glob.glob('/proc/[0-9]*/status'):\n"
            "        try:\n"
            "            with open(entry) as f:\n"
            "                for line in f:\n"
            "                    if line.startswith('PPid:'):\n"
            "                        if line.split()[1] == my_pid:\n"
            "                            count += 1\n"
            "                        break\n"
            "        except (OSError, IndexError):\n"
            "            pass\n"
            "    return count\n"
            "before = child_pids()\n"
            "import aiyes\n"
            "after = child_pids()\n"
            "assert after == before, f'Spawned {after - before} child process(es) during import'\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Import spawn check failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout


class TestAvailableNoProcessSpawn:
    """R-TDDV6-05.1 TEST-06: available() spawns no child processes."""

    def test_available_no_process_spawn(self) -> None:
        """R-TDDV6-05.1 TEST-06: available() spawns no child processes."""
        import textwrap as _textwrap

        # Run in subprocess to get clean process tree
        script = _textwrap.dedent("""\
            import os
            from aiyes import available
            available()
            import subprocess
            children = subprocess.run(['pgrep', '-P', str(os.getpid())], capture_output=True, text=True)
            assert children.stdout.strip() == '', f"available() spawned child processes: {children.stdout}"
        """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Test failed: {result.stderr}"


class TestImportSucceedsWithoutBinaries:
    """R-TDDV6-05.2 AC-07: import aiyes succeeds when binaries are missing."""

    def test_import_aiyes_succeeds_without_binaries(self) -> None:
        """R-TDDV6-05.2 AC-07: import aiyes succeeds with empty PATH."""
        # Run in subprocess with a PATH pointing to an empty dir
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            env = os.environ.copy()
            env["PATH"] = tmpdir
            result = subprocess.run(
                [sys.executable, "-c", "import aiyes; print('OK')"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
        assert result.returncode == 0, (
            f"import aiyes failed with restricted PATH:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout
        # Postcondition: no exception traceback in stderr
        assert "Traceback" not in result.stderr


# ═══════════════════════════════════════════════════════════════════════
# R-TDDV6-05.3: gui_runtime marker
# ═══════════════════════════════════════════════════════════════════════


class TestGuiRuntimeMarkerSkip:
    """R-TDDV6-05.3 AC-08: gui_runtime marker skips when available() False."""

    def test_gui_runtime_skips_when_unavailable(self) -> None:
        """R-TDDV6-05.3 AC-08: Test with gui_runtime is SKIPPED when available() returns False."""
        # Use subprocess to run a minimal pytest invocation with a test file
        # that uses the gui_runtime marker, with available() patched to False
        script = (
            "import pytest\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "\n"
            "# Patch available() to return False\n"
            "with patch('aiyes.available', return_value=False):\n"
            "    # Write a tiny test\n"
            "    import tempfile, os\n"
            "    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, prefix='test_gui_') as f:\n"
            "        f.write('import pytest\\n')\n"
            "        f.write('@pytest.mark.gui_runtime\\n')\n"
            "        f.write('def test_needs_gui():\\n')\n"
            "        f.write('    assert True\\n')\n"
            "        tmpfile = f.name\n"
            "    try:\n"
            "        exit_code = pytest.main([tmpfile, '-v', '--tb=short', '--no-header'])\n"
            "    finally:\n"
            "        os.unlink(tmpfile)\n"
            "    # Exit code 0 means all passed/skipped, no failures\n"
            "    # We need to check the test was SKIPPED\n"
            "    sys.exit(exit_code)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # The test should be skipped (exit code 0 = no failures)
        assert result.returncode == 0, (
            f"Expected gui_runtime test to skip:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        stdout_lower = result.stdout.lower()
        assert "skipped" in stdout_lower or "SKIPPED" in result.stdout, (
            f"Expected SKIPPED in output:\n{result.stdout}"
        )
        # TEST-09: skip reason must reference GUI runtime unavailability
        assert "gui" in stdout_lower, (
            f"Expected skip reason to contain 'GUI' (case-insensitive):\n{result.stdout}"
        )


class TestGuiRuntimeMarkerRuns:
    """R-TDDV6-05.3 AC-09: gui_runtime marker runs when available() True."""

    def test_gui_runtime_runs_when_available(self) -> None:
        """R-TDDV6-05.3 AC-09: Test with gui_runtime runs normally when available() True."""
        script = (
            "import pytest\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "\n"
            "with patch('aiyes.available', return_value=True):\n"
            "    import tempfile, os\n"
            "    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, prefix='test_gui_') as f:\n"
            "        f.write('import pytest\\n')\n"
            "        f.write('@pytest.mark.gui_runtime\\n')\n"
            "        f.write('def test_needs_gui():\\n')\n"
            "        f.write('    assert True\\n')\n"
            "        tmpfile = f.name\n"
            "    try:\n"
            "        exit_code = pytest.main([tmpfile, '-v', '--tb=short', '--no-header'])\n"
            "    finally:\n"
            "        os.unlink(tmpfile)\n"
            "    sys.exit(exit_code)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Expected gui_runtime test to pass:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # Postcondition: test actually ran (PASSED, not SKIPPED)
        assert "passed" in result.stdout.lower() or "PASSED" in result.stdout, (
            f"Expected PASSED in output:\n{result.stdout}"
        )


class TestGuiRuntimeMarkerRegistered:
    """R-TDDV6-05.3 AC-10: gui_runtime marker registered without warning."""

    def test_gui_runtime_marker_registered(self) -> None:
        """R-TDDV6-05.3 AC-10: gui_runtime listed in pytest --markers without warning."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--markers"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, (
            f"pytest --markers failed:\nstderr: {result.stderr}"
        )
        # Postcondition: gui_runtime appears in marker listing
        assert "gui_runtime" in result.stdout, (
            f"gui_runtime marker not registered in pytest --markers output:\n{result.stdout}"
        )

    def test_gui_runtime_no_unknown_mark_warning(self) -> None:
        """R-TDDV6-05.3 AC-10: No PytestUnknownMarkWarning for gui_runtime."""
        # Run pytest collection on a dummy test with gui_runtime and check for warnings
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            prefix="test_marker_check_",
            dir=str(PROJECT_ROOT / "tests"),
        ) as f:
            f.write("import pytest\n")
            f.write("@pytest.mark.gui_runtime\n")
            f.write("def test_dummy():\n")
            f.write("    pass\n")
            tmpfile = f.name

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    tmpfile,
                    "--collect-only",
                    "-W",
                    "error",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(PROJECT_ROOT),
            )
            # With -W error, PytestUnknownMarkWarning would cause nonzero exit
            assert result.returncode == 0, (
                f"gui_runtime marker caused warning (promoted to error via -W error):\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        finally:
            os.unlink(tmpfile)


# ═══════════════════════════════════════════════════════════════════════
# R-TDDV6-05.4: gui_session fixture
# ═══════════════════════════════════════════════════════════════════════


class TestGuiSessionSkipsWhenUnavailable:
    """R-TDDV6-05.4 AC-11: gui_session skips test when available() False."""

    @patch("aiyes.available", return_value=False)
    def test_gui_session_skips_when_unavailable(
        self, mock_available: MagicMock
    ) -> None:
        """R-TDDV6-05.4 AC-11: gui_session fixture skips when available() returns False."""
        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        # The fixture should call pytest.skip when available() is False
        # We test this by importing and calling the fixture generator
        with pytest.raises(pytest.skip.Exception) as exc_info:
            # Execute the fixture setup phase
            gen = gui_session_fixture_fn()
            next(gen)

        # REV-04: verify skip reason references GUI
        skip_msg = str(exc_info.value)
        assert "gui" in skip_msg.lower(), (
            f"Expected skip reason to contain 'GUI' (case-insensitive): {skip_msg}"
        )


class TestGuiSessionReturnsSessionId:
    """R-TDDV6-05.4 AC-12: gui_session factory returns session_id string."""

    @patch("aiyes.available", return_value=True)
    @patch("aiyes.session_api.start_session")
    @patch("aiyes.session_api.stop_session")
    def test_gui_session_returns_session_id(
        self,
        mock_stop: MagicMock,
        mock_start: MagicMock,
        mock_available: MagicMock,
    ) -> None:
        """R-TDDV6-05.4 AC-12: gui_session('app') returns non-empty session_id string."""
        mock_start.return_value = "test-session-abc"

        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        gen = gui_session_fixture_fn()
        factory = next(gen)

        session_id = factory("gedit")

        # Postcondition: session_id is a non-empty string
        assert isinstance(session_id, str)
        assert len(session_id) > 0
        assert session_id == "test-session-abc"

        # Postcondition: start_session was called with correct app_command
        mock_start.assert_called_once()
        call_kwargs = mock_start.call_args
        assert call_kwargs[1].get(
            "app_command", call_kwargs[0][0] if call_kwargs[0] else None
        ) == "gedit" or (len(call_kwargs[0]) > 0 and call_kwargs[0][0] == "gedit")

        # Trigger teardown
        try:
            next(gen)
        except StopIteration:
            pass


class TestGuiSessionTeardownStopsSession:
    """R-TDDV6-05.4 AC-13: Session is stopped after test (teardown)."""

    @patch("aiyes.available", return_value=True)
    @patch("aiyes.session_api.start_session")
    @patch("aiyes.session_api.stop_session")
    def test_gui_session_teardown_stops_session(
        self,
        mock_stop: MagicMock,
        mock_start: MagicMock,
        mock_available: MagicMock,
    ) -> None:
        """R-TDDV6-05.4 AC-13: Teardown calls session_stop with session_id."""
        mock_start.return_value = "test-session-xyz"

        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        gen = gui_session_fixture_fn()
        factory = next(gen)

        session_id = factory("gedit")

        # Trigger teardown
        try:
            next(gen)
        except StopIteration:
            pass

        # Postcondition: stop_session was called with the session_id
        mock_stop.assert_called_with("test-session-xyz")


class TestGuiSessionTeardownOnException:
    """R-TDDV6-05.4 AC-14: Teardown runs even when test raises exception."""

    @patch("aiyes.available", return_value=True)
    @patch("aiyes.session_api.start_session")
    @patch("aiyes.session_api.stop_session")
    def test_gui_session_teardown_runs_on_exception(
        self,
        mock_stop: MagicMock,
        mock_start: MagicMock,
        mock_available: MagicMock,
    ) -> None:
        """R-TDDV6-05.4 AC-14: Teardown runs even when test body raises."""
        mock_start.return_value = "test-session-err"

        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        gen = gui_session_fixture_fn()
        factory = next(gen)

        session_id = factory("gedit")

        # Simulate test body raising an exception by throwing into the generator
        try:
            gen.throw(RuntimeError("test body failed"))
        except (RuntimeError, StopIteration):
            pass

        # Postcondition: stop_session was still called despite the exception
        mock_stop.assert_called_with("test-session-err")


class TestGuiSessionMultipleFactoryCallsDistinctIds:
    """R-TDDV6-05.4 TEST-13a: Multiple factory calls return distinct session_ids."""

    @patch("aiyes.available", return_value=True)
    @patch("aiyes.session_api.start_session")
    @patch("aiyes.session_api.stop_session")
    def test_multiple_factory_calls_return_distinct_session_ids(
        self,
        mock_stop: MagicMock,
        mock_start: MagicMock,
        mock_available: MagicMock,
    ) -> None:
        """TEST-13a: Two factory calls with different app_commands return distinct session_ids."""
        mock_start.side_effect = ["session-id-001", "session-id-002"]

        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        gen = gui_session_fixture_fn()
        factory = next(gen)

        session_id_1 = factory("gedit")
        session_id_2 = factory("firefox")

        # Postcondition: both session_ids are distinct strings
        assert isinstance(session_id_1, str)
        assert isinstance(session_id_2, str)
        assert session_id_1 != session_id_2

        # Postcondition: start_session was called exactly twice
        assert mock_start.call_count == 2

        # Teardown
        try:
            next(gen)
        except StopIteration:
            pass


class TestGuiSessionTeardownLIFOOrdering:
    """R-TDDV6-05.4 TEST-13b: Teardown stops sessions in LIFO order."""

    @patch("aiyes.available", return_value=True)
    @patch("aiyes.session_api.start_session")
    @patch("aiyes.session_api.stop_session")
    def test_teardown_stops_sessions_in_lifo_order(
        self,
        mock_stop: MagicMock,
        mock_start: MagicMock,
        mock_available: MagicMock,
    ) -> None:
        """TEST-13b: Teardown calls stop_session for all sessions in reverse creation order."""
        mock_start.side_effect = ["session-lifo-first", "session-lifo-second"]

        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        gen = gui_session_fixture_fn()
        factory = next(gen)

        factory("gedit")
        factory("firefox")

        # Trigger teardown
        try:
            next(gen)
        except StopIteration:
            pass

        # Postcondition: stop_session was called twice
        assert mock_stop.call_count == 2

        # Postcondition: LIFO order — second session stopped first
        stop_calls = [call.args[0] for call in mock_stop.call_args_list]
        assert stop_calls == ["session-lifo-second", "session-lifo-first"], (
            f"Expected LIFO teardown order, got: {stop_calls}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Architecture constraint tests
# ═══════════════════════════════════════════════════════════════════════


class TestEnvironmentDomainPurity:
    """ARCH-01: domain/environment.py uses only stdlib imports."""

    def test_environment_module_stdlib_only(self) -> None:
        """ARCH-01: domain/environment.py has zero non-stdlib imports."""
        env_file = SRC_ROOT / "domain" / "environment.py"
        assert env_file.exists(), f"domain/environment.py does not exist at {env_file}"

        imports = _get_imports_from_file(env_file)
        non_stdlib = []
        for imp in imports:
            top = imp.split(".")[0]
            if top not in _STDLIB_PREFIXES and not imp.startswith("aiyes.domain"):
                non_stdlib.append(imp)

        assert non_stdlib == [], (
            f"domain/environment.py has non-stdlib imports: {non_stdlib}"
        )


class TestPluginAndApiPlacement:
    """ARCH-03: pytest_plugin.py and session_api.py at package root, not in domain/ports/adapters/cli."""

    def test_pytest_plugin_at_package_root(self) -> None:
        """ARCH-03: pytest_plugin.py exists at src/aiyes/pytest_plugin.py."""
        expected = SRC_ROOT / "pytest_plugin.py"
        assert expected.exists(), f"pytest_plugin.py not found at {expected}"

    def test_session_api_at_package_root(self) -> None:
        """ARCH-03: session_api.py exists at src/aiyes/session_api.py."""
        expected = SRC_ROOT / "session_api.py"
        assert expected.exists(), f"session_api.py not found at {expected}"

    def test_pytest_plugin_not_in_layer_dirs(self) -> None:
        """ARCH-03: No pytest_plugin.py in domain/, ports/, adapters/, cli/."""
        layer_dirs = ["domain", "ports", "adapters", "cli"]
        for layer in layer_dirs:
            matches = list((SRC_ROOT / layer).rglob("pytest_plugin.py"))
            assert matches == [], f"pytest_plugin.py found in {layer}/: {matches}"

    def test_session_api_not_in_layer_dirs(self) -> None:
        """ARCH-03: No session_api.py in domain/, ports/, adapters/, cli/."""
        layer_dirs = ["domain", "ports", "adapters", "cli"]
        for layer in layer_dirs:
            matches = list((SRC_ROOT / layer).rglob("session_api.py"))
            assert matches == [], f"session_api.py found in {layer}/: {matches}"


class TestPluginAdapterImportProhibition:
    """ARCH-04: pytest_plugin.py must not import from aiyes.adapters."""

    def test_pytest_plugin_no_adapter_imports(self) -> None:
        """ARCH-04: pytest_plugin.py has no aiyes.adapters imports."""
        plugin_file = SRC_ROOT / "pytest_plugin.py"
        assert plugin_file.exists(), "pytest_plugin.py must exist"

        imports = _get_imports_from_file(plugin_file)
        adapter_imports = [i for i in imports if i.startswith("aiyes.adapters")]
        assert adapter_imports == [], (
            f"pytest_plugin.py imports from adapters: {adapter_imports}"
        )

    def test_pytest_plugin_no_composition_root_import(self) -> None:
        """ARCH-04: pytest_plugin.py does not import aiyes.cli.composition_root directly."""
        plugin_file = SRC_ROOT / "pytest_plugin.py"
        assert plugin_file.exists(), "pytest_plugin.py must exist"

        imports = _get_imports_from_file(plugin_file)
        cr_imports = [i for i in imports if i == "aiyes.cli.composition_root"]
        assert cr_imports == [], (
            f"pytest_plugin.py imports composition_root directly: {cr_imports}"
        )


class TestSessionApiAdapterProhibition:
    """ARCH-05: session_api.py must not import from aiyes.adapters."""

    def test_session_api_no_adapter_imports(self) -> None:
        """ARCH-05: session_api.py has no aiyes.adapters imports."""
        api_file = SRC_ROOT / "session_api.py"
        assert api_file.exists(), "session_api.py must exist"

        imports = _get_imports_from_file(api_file)
        adapter_imports = [i for i in imports if i.startswith("aiyes.adapters")]
        assert adapter_imports == [], (
            f"session_api.py imports from adapters: {adapter_imports}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Wiring tests
# ═══════════════════════════════════════════════════════════════════════


class TestGuiSessionWiring:
    """WIRE: gui_session fixture delegates to session_api start/stop."""

    @patch("aiyes.available", return_value=True)
    @patch("aiyes.session_api.start_session")
    @patch("aiyes.session_api.stop_session")
    def test_gui_session_delegates_to_start_session(
        self,
        mock_stop: MagicMock,
        mock_start: MagicMock,
        mock_available: MagicMock,
    ) -> None:
        """WIRE: gui_session factory call delegates to session_api.start_session."""
        mock_start.return_value = "wired-session-001"

        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        gen = gui_session_fixture_fn()
        factory = next(gen)

        factory("gedit", app_args=["file.txt"], resolution="1920x1080")

        # Postcondition: start_session was called (wiring verified)
        mock_start.assert_called_once()
        call_args = mock_start.call_args
        # Verify the app_command and kwargs were passed through
        all_args = {
            **dict(zip(["app_command"], call_args[0] if call_args[0] else [])),
            **(call_args[1] or {}),
        }
        assert all_args.get("app_command") == "gedit" or (
            call_args[0] and call_args[0][0] == "gedit"
        )

        # Teardown
        try:
            next(gen)
        except StopIteration:
            pass

    @patch("aiyes.available", return_value=True)
    @patch("aiyes.session_api.start_session")
    @patch("aiyes.session_api.stop_session")
    def test_gui_session_teardown_delegates_to_stop_session(
        self,
        mock_stop: MagicMock,
        mock_start: MagicMock,
        mock_available: MagicMock,
    ) -> None:
        """WIRE: gui_session teardown delegates to session_api.stop_session."""
        mock_start.return_value = "wired-session-002"

        from aiyes.pytest_plugin import gui_session as gui_session_fixture_fn

        gen = gui_session_fixture_fn()
        factory = next(gen)

        factory("firefox")

        # Trigger teardown
        try:
            next(gen)
        except StopIteration:
            pass

        # Postcondition: stop_session was called with the session_id
        mock_stop.assert_called_once_with("wired-session-002")


class TestSessionApiWiring:
    """WIRE: session_api.py imports from composition_root."""

    def test_session_api_imports_composition_root(self) -> None:
        """WIRE: session_api.py imports from aiyes.cli.composition_root."""
        api_file = SRC_ROOT / "session_api.py"
        assert api_file.exists(), "session_api.py must exist"

        imports = _get_imports_from_file(api_file)
        cr_imports = [i for i in imports if i == "aiyes.cli.composition_root"]
        assert len(cr_imports) > 0, (
            f"session_api.py must import from aiyes.cli.composition_root, found: {imports}"
        )


class TestAvailableExposedAtTopLevel:
    """ARCH-06: available() accessible via `from aiyes import available`."""

    def test_available_importable_from_top_level(self) -> None:
        """ARCH-06: `from aiyes import available` succeeds."""
        from aiyes import available

        assert callable(available)

    def test_available_is_domain_function(self) -> None:
        """ARCH-06: aiyes.available is the same object as aiyes.domain.environment.available."""
        import aiyes
        from aiyes.domain.environment import available as domain_available

        assert aiyes.available is domain_available


class TestPyproject11EntryPoint:
    """ARCH-07: pyproject.toml registers pytest11 entry point."""

    def test_pytest11_entry_point_exists(self) -> None:
        """ARCH-07: pyproject.toml has [project.entry-points.pytest11] with aiyes.pytest_plugin."""
        pyproject_path = PROJECT_ROOT / "pyproject.toml"
        assert pyproject_path.exists(), "pyproject.toml not found"

        content = pyproject_path.read_text()
        assert "pytest11" in content, (
            "pyproject.toml missing [project.entry-points.pytest11] section"
        )
        assert "aiyes.pytest_plugin" in content, (
            "pyproject.toml pytest11 entry does not reference aiyes.pytest_plugin"
        )


class TestImportAiyesNoAdapterLoading:
    """IMPL-08: import aiyes does not load adapter modules."""

    def test_import_aiyes_does_not_load_adapters(self) -> None:
        """IMPL-08: import aiyes does not load adapter modules."""
        import textwrap as _textwrap

        script = _textwrap.dedent("""\
            import sys
            import aiyes
            adapter_modules = [m for m in sys.modules if m.startswith('aiyes.adapters')]
            comp_root = 'aiyes.cli.composition_root' in sys.modules
            assert not adapter_modules, f"Adapter modules loaded: {adapter_modules}"
            assert not comp_root, "composition_root loaded on import aiyes"
        """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"IMPL-08 violated: {result.stderr}"
