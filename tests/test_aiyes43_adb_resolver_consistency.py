"""AIYES-43 RED tests: adb_path resolver consistency across adapters and doctor.

These tests pin the desired post-fix behavior for the four sites identified
in INTEGRATION_MAP.yaml#root_cause_verified:

  1. src/aiyes/adapters/adb_activity_adapter.py:25  (hardcoded "adb")
  2. src/aiyes/adapters/adb_window_adapter.py:35    (hardcoded "adb")
  3. src/aiyes/adapters/system_dependency_check.py:75  (generic _EXECUTABLE_DEPS branch)
  4. src/aiyes/adapters/system_dependency_check.py:259 (_check_android_device)

All tests are expected to FAIL on current code and PASS after the fix routes
those four sites through aiyes.adapters.adb_path.resolve_adb_path.

Test policy:
  - The real resolver (resolve_adb_path) is exercised; it is NOT mocked.
  - For tests requiring a fallback adb to be present, a stub shell script
    is written under tmp_path/android-sdk/platform-tools/adb and HOME is
    monkeypatched to tmp_path. PATH is stripped so the resolver must use
    the fallback branch.
  - subprocess.run is observed (via monkeypatch wrapping) to assert which
    executable path was passed as argv[0]. Production code is not modified
    by these tests.

See VALIDATED_INTENT_PKG.yaml#definition_of_done for acceptance criteria.
"""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import patch


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

_DUMPSYS_ACTIVITY_FIXTURE = """\
ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)
  mResumedActivity: ActivityRecord{abc123 u0 com.example.app/.MainActivity t42}
"""

_DUMPSYS_WINDOW_FIXTURE = """\
WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #0 Window{abc Surface(name=com.example.app/MainActivity)}: title=foo
"""


def _write_fake_adb(tmp_path: Path, stdout_fixture: str) -> Path:
    """Write a tmp_path/android-sdk/platform-tools/adb shell script.

    The script ignores its arguments and prints `stdout_fixture` to stdout.
    Returns the absolute path to the script.
    """
    sdk_dir = tmp_path / "android-sdk" / "platform-tools"
    sdk_dir.mkdir(parents=True, exist_ok=True)
    adb = sdk_dir / "adb"
    adb.write_text(
        "#!/usr/bin/env bash\n"
        # Print the fixture verbatim. Use a heredoc to keep newlines clean.
        "cat <<'__AIYES43_FIX__'\n"
        f"{stdout_fixture}"
        "__AIYES43_FIX__\n"
    )
    adb.chmod(adb.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return adb


def _write_fake_adb_at(path: Path, executable: bool) -> Path:
    """Write an adb stub at an exact path with controlled execute bits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\nprintf 'stub adb\\n'\n")
    mode = path.stat().st_mode
    if executable:
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    else:
        path.chmod(mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return path


def _strip_path(monkeypatch: Any) -> None:
    """Strip PATH so resolve_adb_path must fall back to ~/android-sdk/....

    Use /usr/bin:/bin so the fake adb's `#!/usr/bin/env bash` shebang still
    resolves bash; neither directory contains adb on test machines, so
    shutil.which("adb") still returns None and the resolver must use the
    HOME-rooted fallback.
    """
    monkeypatch.setenv("PATH", "/usr/bin:/bin")


def _set_home(monkeypatch: Any, tmp_path: Path) -> None:
    """Point HOME at tmp_path so os.path.expanduser('~') resolves there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # os.path.expanduser also reads $USER on some platforms; HOME is sufficient
    # on POSIX, which is what aiyes targets.


def _spy_subprocess_run(monkeypatch: Any) -> List[Tuple[Any, ...]]:
    """Wrap subprocess.run to record the argv passed by the adapter.

    Returns a list that will be appended to with each call's argv.
    The original subprocess.run is still invoked so the fake adb runs.
    """
    calls: List[Tuple[Any, ...]] = []
    original_run = subprocess.run

    def spy(*args: Any, **kwargs: Any) -> Any:
        # First positional or kwarg "args" carries the argv list.
        argv = args[0] if args else kwargs.get("args")
        calls.append(tuple(argv) if isinstance(argv, (list, tuple)) else (argv,))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


# ─────────────────────────────────────────────────────────────────────────
# T1: AdbActivityQueryAdapter must use resolve_adb_path
# ─────────────────────────────────────────────────────────────────────────


class TestAdbActivityAdapterUsesResolver:
    """AIYES-43 T1: AdbActivityQueryAdapter routes through resolve_adb_path."""

    def test_adb_activity_adapter_uses_resolver(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """T1: With PATH stripped and a fake adb at ~/android-sdk/platform-tools/adb,
        AdbActivityQueryAdapter.get_resumed_activity must succeed and the
        subprocess call must use the resolver-returned path, not literal "adb".
        """
        from aiyes.adapters.adb_activity_adapter import AdbActivityQueryAdapter

        fake_adb = _write_fake_adb(tmp_path, _DUMPSYS_ACTIVITY_FIXTURE)
        _set_home(monkeypatch, tmp_path)
        _strip_path(monkeypatch)

        calls = _spy_subprocess_run(monkeypatch)

        adapter = AdbActivityQueryAdapter()
        result = adapter.get_resumed_activity("emulator-5554")

        # DbC postcondition 1: the call succeeded (current adapter swallows
        # FileNotFoundError to None — RED proof comes from this assertion).
        assert result == "com.example.app/.MainActivity", (
            f"adapter must succeed via resolver fallback; got {result!r}. "
            f"This fails on current code because subprocess.run with literal "
            f"'adb' raises FileNotFoundError when PATH has no adb."
        )

        # DbC postcondition 2: argv[0] is the resolved fallback path,
        # not the literal "adb".
        assert calls, "subprocess.run was not called"
        argv0 = calls[0][0]
        assert argv0 == str(fake_adb), (
            f"adapter must invoke resolver result, got argv[0]={argv0!r}; "
            f"expected {str(fake_adb)!r}"
        )
        assert argv0 != "adb", "adapter must not pass literal 'adb' as argv[0]"


# ─────────────────────────────────────────────────────────────────────────
# T2: AdbWindowAdapter must use resolve_adb_path
# ─────────────────────────────────────────────────────────────────────────


class TestAdbWindowAdapterUsesResolver:
    """AIYES-43 T2: AdbWindowAdapter routes through resolve_adb_path."""

    def test_adb_window_adapter_uses_resolver(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """T2: Same shape as T1 for AdbWindowAdapter.list_top_level_windows."""
        from aiyes.adapters.adb_window_adapter import AdbWindowAdapter

        fake_adb = _write_fake_adb(tmp_path, _DUMPSYS_WINDOW_FIXTURE)
        _set_home(monkeypatch, tmp_path)
        _strip_path(monkeypatch)

        calls = _spy_subprocess_run(monkeypatch)

        # AdbWindowAdapter.list_top_level_windows takes a session-like object
        # with attribute device_serial.
        class _Session:
            device_serial = "emulator-5554"

        adapter = AdbWindowAdapter()
        windows = adapter.list_top_level_windows(_Session())

        # DbC postcondition 1: the call succeeded (current adapter returns []
        # on FileNotFoundError — RED proof comes from this assertion).
        assert windows, (
            "adapter must succeed via resolver fallback; got empty list. "
            "On current code, subprocess.run with literal 'adb' raises "
            "FileNotFoundError when PATH has no adb."
        )

        # DbC postcondition 2: argv[0] is the resolver-returned path.
        assert calls, "subprocess.run was not called"
        argv0 = calls[0][0]
        assert argv0 == str(fake_adb), (
            f"adapter must invoke resolver result, got argv[0]={argv0!r}; "
            f"expected {str(fake_adb)!r}"
        )
        assert argv0 != "adb", "adapter must not pass literal 'adb' as argv[0]"


# ─────────────────────────────────────────────────────────────────────────
# T3: SystemDependencyCheck adb resolution uses the resolver fallback
# ─────────────────────────────────────────────────────────────────────────


class TestSystemDependencyCheckAdbFallback:
    """AIYES-43 T3: doctor's adb check honors fallback locations.

    Covers BOTH:
      - line 75 generic _EXECUTABLE_DEPS branch via check("adb")
      - line 259 _check_android_device branch via check("android_device")
    """

    def test_check_adb_finds_fallback_path_via_executable_deps_branch(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """T3.a: check('adb') must report PRESENT with the fallback location
        when adb is only at ~/android-sdk/platform-tools/adb (PATH cleared).

        This pins the fix at system_dependency_check.py:75
        (_EXECUTABLE_DEPS generic branch).
        """
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        fake_adb = _write_fake_adb(tmp_path, "irrelevant\n")
        _set_home(monkeypatch, tmp_path)
        _strip_path(monkeypatch)

        checker = SystemDependencyCheck()
        result = checker.check("adb")

        assert result.status == "pass", (
            f"check('adb') must pass when adb is reachable via fallback; "
            f"got status={result.status!r} message={result.message!r}. "
            f"Current code uses shutil.which only and reports fail."
        )
        # The fallback path must appear in the message — pins that the
        # resolver was used, not just any 'pass' result.
        assert str(fake_adb) in result.message, (
            f"result message must reference the resolved fallback path "
            f"{str(fake_adb)!r}; got {result.message!r}"
        )

    def test_check_android_device_uses_resolver_fallback(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """T3.b: check('android_device') must locate adb via the resolver
        when only the fallback path exists. Pins the fix at line 259.
        """
        from aiyes.adapters.system_dependency_check import SystemDependencyCheck

        # adb stub prints a "no devices" listing — we don't need a device,
        # we only need adb to be invoked successfully via the fallback path.
        no_devices = "List of devices attached\n\n"
        fake_adb = _write_fake_adb(tmp_path, no_devices)
        _set_home(monkeypatch, tmp_path)
        _strip_path(monkeypatch)

        calls = _spy_subprocess_run(monkeypatch)

        checker = SystemDependencyCheck()
        result = checker.check("android_device")

        # On current code, shutil.which("adb") returns None and the result is
        # "fail" with message "adb not found in PATH; cannot check for devices".
        # After fix, adb is resolved to the fallback path and `adb devices`
        # runs, returning "warn" (no devices attached) — never "fail" for
        # the "adb not found" reason.
        assert "not found" not in result.message.lower(), (
            f"check('android_device') must NOT report adb missing when adb "
            f"is reachable via fallback; got message={result.message!r}"
        )
        assert result.status in ("pass", "warn"), (
            f"check('android_device') must reach adb via fallback and report "
            f"pass/warn; got status={result.status!r} message={result.message!r}"
        )
        # subprocess.run must have been invoked with the fallback adb path.
        adb_calls = [c for c in calls if c and "devices" in c]
        assert adb_calls, "adb devices subprocess call did not occur"
        assert adb_calls[0][0] == str(fake_adb), (
            f"check('android_device') must invoke resolver path; "
            f"got argv[0]={adb_calls[0][0]!r}; expected {str(fake_adb)!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# AIYES-52: adb_path resolver fallback reliability
# ─────────────────────────────────────────────────────────────────────────


class TestAdbPathFallbackReliability:
    """AIYES-52 R-010: fallback candidates are executable and complete."""

    def test_non_executable_fallback_is_skipped(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        from aiyes.adapters.adb_path import resolve_adb_path

        _set_home(monkeypatch, tmp_path)
        _strip_path(monkeypatch)
        non_executable = _write_fake_adb_at(
            tmp_path / "android-sdk" / "platform-tools" / "adb",
            executable=False,
        )
        executable = _write_fake_adb_at(
            tmp_path / "Android" / "Sdk" / "platform-tools" / "adb",
            executable=True,
        )

        assert resolve_adb_path() == str(executable)
        assert not os.access(non_executable, os.X_OK)

    def test_android_home_platform_tools_adb_is_selected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        from aiyes.adapters.adb_path import resolve_adb_path

        _set_home(monkeypatch, tmp_path / "home-without-adb")
        _strip_path(monkeypatch)
        adb = _write_fake_adb_at(
            tmp_path / "android-home" / "platform-tools" / "adb",
            executable=True,
        )
        monkeypatch.setenv("ANDROID_HOME", str(tmp_path / "android-home"))

        assert resolve_adb_path() == str(adb)

    def test_android_sdk_root_platform_tools_adb_is_selected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        from aiyes.adapters.adb_path import resolve_adb_path

        _set_home(monkeypatch, tmp_path / "home-without-adb")
        _strip_path(monkeypatch)
        adb = _write_fake_adb_at(
            tmp_path / "android-sdk-root" / "platform-tools" / "adb",
            executable=True,
        )
        monkeypatch.delenv("ANDROID_HOME", raising=False)
        monkeypatch.setenv("ANDROID_SDK_ROOT", str(tmp_path / "android-sdk-root"))

        assert resolve_adb_path() == str(adb)

    def test_error_mentions_checked_fallback_locations(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        from aiyes.adapters.adb_path import resolve_adb_path

        _set_home(monkeypatch, tmp_path / "home-without-adb")
        _strip_path(monkeypatch)
        monkeypatch.setenv("ANDROID_HOME", str(tmp_path / "android-home"))
        monkeypatch.setenv("ANDROID_SDK_ROOT", str(tmp_path / "android-sdk-root"))

        try:
            resolve_adb_path()
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("resolve_adb_path must fail when no adb exists")

        assert "PATH (shutil.which)" in message
        assert str(tmp_path / "android-home" / "platform-tools" / "adb") in message
        assert str(tmp_path / "android-sdk-root" / "platform-tools" / "adb") in message
        assert str(tmp_path / "home-without-adb" / "android-sdk" / "platform-tools" / "adb") in message


# ─────────────────────────────────────────────────────────────────────────
# T4: Static guard — no hardcoded "adb" subprocess argv literals
# ─────────────────────────────────────────────────────────────────────────


class TestNoHardcodedAdbInAdapterSubprocessCalls:
    """AIYES-43 T4: lightweight regression guard against future drift.

    Walks every .py under src/aiyes/adapters/ and asserts no
    subprocess.run/Popen/check_output call has a list-literal first
    argument whose first element is the literal string "adb".

    Allowed: adb_path.py uses the literal "adb" inside shutil.which —
    but never inside a subprocess argv list. We only flag list literals
    passed as the args argument to subprocess.* calls.
    """

    _SUBPROCESS_FUNCS = frozenset(
        {"run", "Popen", "check_output", "check_call", "call"}
    )

    def test_no_hardcoded_adb_in_subprocess_calls_under_adapters(self) -> None:
        adapters_dir = Path("src/aiyes/adapters")
        assert adapters_dir.is_dir(), f"adapters dir not found: {adapters_dir}"

        offenders: List[str] = []
        for py_file in sorted(adapters_dir.rglob("*.py")):
            try:
                source = py_file.read_text()
            except OSError:
                continue
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # Match subprocess.run(...) / subprocess.Popen(...) etc.
                if isinstance(func, ast.Attribute):
                    if (
                        isinstance(func.value, ast.Name)
                        and func.value.id == "subprocess"
                        and func.attr in self._SUBPROCESS_FUNCS
                    ):
                        argv_node = self._first_argv_arg(node)
                        if self._argv_starts_with_literal_adb(argv_node):
                            offenders.append(
                                f"{py_file}:{node.lineno} subprocess.{func.attr}"
                                f" argv starts with literal 'adb'"
                            )
                # Match bare run(...) when imported via `from subprocess import run`.
                elif isinstance(func, ast.Name) and func.id in self._SUBPROCESS_FUNCS:
                    argv_node = self._first_argv_arg(node)
                    if self._argv_starts_with_literal_adb(argv_node):
                        offenders.append(
                            f"{py_file}:{node.lineno} {func.id}"
                            f" argv starts with literal 'adb'"
                        )

        assert not offenders, (
            "Found hardcoded 'adb' as argv[0] in subprocess calls under "
            "src/aiyes/adapters/ — must use resolve_adb_path() instead:\n  "
            + "\n  ".join(offenders)
        )

    @staticmethod
    def _first_argv_arg(call: ast.Call) -> ast.AST | None:
        """Return the argv argument node (first positional, or args= kwarg)."""
        if call.args:
            return call.args[0]
        for kw in call.keywords:
            if kw.arg == "args":
                return kw.value
        return None

    @staticmethod
    def _argv_starts_with_literal_adb(node: ast.AST | None) -> bool:
        if not isinstance(node, (ast.List, ast.Tuple)):
            return False
        if not node.elts:
            return False
        first = node.elts[0]
        return isinstance(first, ast.Constant) and first.value == "adb"


# ─────────────────────────────────────────────────────────────────────────
# T5: Conformance test exception is narrow (only adb_path is permitted)
# ─────────────────────────────────────────────────────────────────────────


class TestConformanceTestPermitsOnlyAdbPath:
    """AIYES-43 T5: pin that the conformance exception is narrow.

    The fix updates TestSystemDependencyCheckConformance.test_no_adapter_or_cli_imports
    to permit exactly one import: `from aiyes.adapters.adb_path import resolve_adb_path`.
    Any other aiyes.adapters.* or aiyes.cli import in system_dependency_check.py
    must still be flagged as a violation.

    Implementation: re-applies the same conformance rule (with the narrow
    exception) to a forged source string. Asserts:
      - permitted module (aiyes.adapters.adb_path) → no violation
      - any other adapter module → violation
      - any aiyes.cli module → violation
    """

    @staticmethod
    def _detect_violation(source: str) -> bool:
        """Mirror the post-fix conformance rule.

        Permitted: ImportFrom node.module == 'aiyes.adapters.adb_path'.
        Forbidden: any other 'aiyes.adapters.*' or 'aiyes.cli' / 'aiyes.cli.*'.
        Returns True if a violation is found.
        """
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                if module == "aiyes.adapters.adb_path":
                    continue  # the sole narrow exception
                if module.startswith("aiyes.adapters."):
                    return True
                if module == "aiyes.cli" or module.startswith("aiyes.cli."):
                    return True
        return False

    def test_permits_resolver_import(self) -> None:
        """The narrow exception: aiyes.adapters.adb_path is allowed."""
        source = "from aiyes.adapters.adb_path import resolve_adb_path\n"
        assert not self._detect_violation(source), (
            "aiyes.adapters.adb_path must be the sole permitted "
            "aiyes.adapters.* import in system_dependency_check.py"
        )

    def test_rejects_other_adapter_import(self) -> None:
        """Forging an import of a sibling adapter must still be flagged."""
        source = (
            "from aiyes.adapters.adb_clipboard_adapter import AdbClipboardAdapter\n"
        )
        assert self._detect_violation(source), (
            "Conformance exception must remain narrow — sibling adapter "
            "imports must still be flagged as violations."
        )

    def test_rejects_cli_import(self) -> None:
        """Forging a CLI import must still be flagged."""
        source = "from aiyes.cli.doctor import main\n"
        assert self._detect_violation(source), (
            "aiyes.cli imports remain forbidden in system_dependency_check.py"
        )

    def test_current_conformance_rule_is_too_broad(self) -> None:
        """Pin that the CURRENT rule (pre-fix) rejects the resolver import.

        This proves the conformance test must be updated as part of the fix.
        On current code, the conformance test in tests/test_adapters.py would
        flag `from aiyes.adapters.adb_path import resolve_adb_path` as a
        violation. After A9's fix, the rule narrows. The post-fix rule
        (above) permits adb_path while still rejecting other modules.

        This test asserts the post-fix narrowing is meaningful: a literal
        prefix-only check (the pre-fix rule) would reject adb_path too.
        """
        source = "from aiyes.adapters.adb_path import resolve_adb_path\n"

        # Pre-fix rule (literal prefix check).
        tree = ast.parse(source)
        prefix_violation = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("aiyes.adapters."):
                    prefix_violation = True

        assert prefix_violation, (
            "Sanity: the current (pre-fix) prefix-only rule must reject "
            "aiyes.adapters.adb_path — proving the narrowing is required."
        )
        # And the post-fix narrowed rule must accept it.
        assert not self._detect_violation(source), (
            "Post-fix rule must accept aiyes.adapters.adb_path."
        )
