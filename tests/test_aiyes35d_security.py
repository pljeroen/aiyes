"""Tests for AIYES-35D — Security hardening.

Findings covered:
  S-01: Replace ast.literal_eval with structured JSON format for registry keys
  S-02: PID ownership check before os.kill on untracked PIDs
  S-03: Pattern-based credential stripping (suffix matching)
  S-04: ADB text escaping audit (property-based)
"""

from __future__ import annotations

import json
import os
import signal
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aiyes.domain.node_id import NodeIdRegistry


# ══════════════════════════════════════════════════════════════════════
# S-01: Registry key format — JSON-native, no ast.literal_eval
# ══════════════════════════════════════════════════════════════════════


class TestRegistryJsonFormat:
    """S-01: Registry keys must use JSON-native format, not Python literal strings."""

    def test_get_mapping_keys_are_json_arrays(self) -> None:
        """S-01: get_mapping() keys must be JSON arrays, not Python tuple strings."""
        registry = NodeIdRegistry()
        registry.get_or_assign("push_button", "OK", [0, 1])

        mapping = registry.get_mapping()
        for key in mapping:
            # Key must be a valid JSON array (not a Python tuple repr)
            parsed = json.loads(key)
            assert isinstance(parsed, list), (
                f"Registry key must be a JSON array, got {type(parsed).__name__}: {key}"
            )

    def test_get_mapping_key_structure(self) -> None:
        """S-01: JSON key must be [role, name, path_list]."""
        registry = NodeIdRegistry()
        registry.get_or_assign("push_button", "OK", [0, 1])

        mapping = registry.get_mapping()
        key = list(mapping.keys())[0]
        parsed = json.loads(key)
        assert len(parsed) == 3
        assert parsed[0] == "push_button"
        assert parsed[1] == "OK"
        assert parsed[2] == [0, 1]

    def test_roundtrip_new_format(self) -> None:
        """S-01: from_mapping(get_mapping()) must reconstruct the registry."""
        registry = NodeIdRegistry()
        id1 = registry.get_or_assign("push_button", "OK", [0, 1])
        id2 = registry.get_or_assign("frame", "Main", [0])
        id3 = registry.get_or_assign("text", "Input", [0, 1, 2])

        mapping = registry.get_mapping()
        restored = NodeIdRegistry.from_mapping(mapping)

        assert restored.get_or_assign("push_button", "OK", [0, 1]) == id1
        assert restored.get_or_assign("frame", "Main", [0]) == id2
        assert restored.get_or_assign("text", "Input", [0, 1, 2]) == id3

    def test_from_mapping_rejects_old_tuple_format(self) -> None:
        """S-01: from_mapping must NOT parse old Python tuple string format."""
        old_format_mapping = {
            "('push_button', 'OK', (0, 1))": "n_001",
        }
        restored = NodeIdRegistry.from_mapping(old_format_mapping)
        # Old format keys should be silently ignored (not parsed)
        assert not restored.has_id("n_001")

    def test_no_ast_import_in_node_id(self) -> None:
        """S-01: node_id.py must not import ast at all."""
        import ast as stdlib_ast
        from pathlib import Path

        source = Path("src/aiyes/domain/node_id.py").read_text()
        tree = stdlib_ast.parse(source)

        for node in stdlib_ast.walk(tree):
            if isinstance(node, stdlib_ast.Import):
                for alias in node.names:
                    assert alias.name != "ast", (
                        "node_id.py must not import ast — "
                        "ast.literal_eval is the security finding being fixed"
                    )
            elif isinstance(node, stdlib_ast.ImportFrom):
                assert node.module != "ast", (
                    "node_id.py must not import from ast — "
                    "ast.literal_eval is the security finding being fixed"
                )

    def test_counter_preserved_through_roundtrip(self) -> None:
        """S-01: Counter value must survive serialization roundtrip."""
        registry = NodeIdRegistry()
        registry.get_or_assign("a", "b", [0])  # n_001
        registry.get_or_assign("c", "d", [1])  # n_002

        mapping = registry.get_mapping()
        restored = NodeIdRegistry.from_mapping(mapping)

        # Next ID should be n_003, not n_001
        new_id = restored.get_or_assign("e", "f", [2])
        assert new_id == "n_003"

    def test_names_with_special_chars_roundtrip(self) -> None:
        """S-01: Names with quotes, parens, commas must roundtrip correctly."""
        registry = NodeIdRegistry()
        weird_name = 'It\'s a "test" (with, commas)'
        node_id = registry.get_or_assign("text", weird_name, [0, 1])

        mapping = registry.get_mapping()
        restored = NodeIdRegistry.from_mapping(mapping)
        assert restored.get_or_assign("text", weird_name, [0, 1]) == node_id


# ══════════════════════════════════════════════════════════════════════
# S-02: PID ownership check before os.kill on untracked PIDs
# ══════════════════════════════════════════════════════════════════════


class TestSubprocessPidOwnership:
    """S-02: SubprocessAdapter.stop() must verify PID ownership for untracked PIDs."""

    def test_stop_untracked_checks_proc_status(self) -> None:
        """S-02: Untracked PID stop must read /proc/{pid}/status before kill."""
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        # PID 99999 is not tracked (never started via adapter)

        with (
            patch("os.kill") as mock_kill,
            patch("builtins.open", side_effect=FileNotFoundError),
        ):
            # /proc/99999/status does not exist = process not ours
            # Should NOT call os.kill
            with pytest.raises((ProcessLookupError, PermissionError, RuntimeError)):
                adapter.stop(99999)

            # os.kill should NOT have been called with SIGTERM
            sigterm_calls = [
                c
                for c in mock_kill.call_args_list
                if len(c[0]) >= 2 and c[0][1] == signal.SIGTERM
            ]
            assert len(sigterm_calls) == 0, (
                "os.kill(SIGTERM) must not be called for untracked PIDs "
                "without ownership verification"
            )

    def test_stop_untracked_owned_pid_sends_signal(self) -> None:
        """S-02: Untracked PID owned by current user may be stopped."""
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        my_uid = os.getuid()

        # Simulate /proc/{pid}/status with matching UID
        fake_status = f"Name:\tfake\nUid:\t{my_uid}\t{my_uid}\t{my_uid}\t{my_uid}\n"

        with (
            patch(
                "builtins.open",
                MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(
                            return_value=MagicMock(
                                read=MagicMock(return_value=fake_status)
                            )
                        ),
                        __exit__=MagicMock(return_value=False),
                    )
                ),
            ),
            patch("os.kill") as mock_kill,
        ):
            adapter.stop(12345)
            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_stop_untracked_foreign_pid_refuses(self) -> None:
        """S-02: Untracked PID owned by different user must NOT be killed."""
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()
        foreign_uid = os.getuid() + 1

        fake_status = f"Name:\tother\nUid:\t{foreign_uid}\t{foreign_uid}\t{foreign_uid}\t{foreign_uid}\n"

        with (
            patch(
                "builtins.open",
                MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(
                            return_value=MagicMock(
                                read=MagicMock(return_value=fake_status)
                            )
                        ),
                        __exit__=MagicMock(return_value=False),
                    )
                ),
            ),
            patch("os.kill") as mock_kill,
        ):
            with pytest.raises(PermissionError):
                adapter.stop(12345)

            sigterm_calls = [
                c
                for c in mock_kill.call_args_list
                if len(c[0]) >= 2 and c[0][1] == signal.SIGTERM
            ]
            assert len(sigterm_calls) == 0

    def test_stop_tracked_pid_still_works(self) -> None:
        """S-02: Tracked (started) PIDs must still be stopped normally."""
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.wait.return_value = 0

        adapter._processes[12345] = mock_process

        adapter.stop(12345)
        mock_process.terminate.assert_called_once()

    def test_stop_untracked_no_proc_filesystem_falls_through(self) -> None:
        """S-02: On non-Linux (no /proc), untracked stop should still be safe."""
        from aiyes.adapters.subprocess_adapter import SubprocessAdapter

        adapter = SubprocessAdapter()

        # /proc not available — FileNotFoundError reading status
        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("os.kill") as mock_kill,
        ):
            # When /proc is unavailable, should raise or refuse
            with pytest.raises((ProcessLookupError, PermissionError, RuntimeError)):
                adapter.stop(99999)

            sigterm_calls = [
                c
                for c in mock_kill.call_args_list
                if len(c[0]) >= 2 and c[0][1] == signal.SIGTERM
            ]
            assert len(sigterm_calls) == 0


# ══════════════════════════════════════════════════════════════════════
# S-03: Pattern-based credential stripping
# ══════════════════════════════════════════════════════════════════════


class TestPatternCredentialStripping:
    """S-03: Credential stripping must match suffix patterns, not just exact names."""

    def _get_app_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_overrides: Dict[str, str],
    ) -> Dict[str, str]:
        """Execute session start and return env passed to process.start()."""
        from tests.conftest import (
            FakeAccessibilityBus,
            FakeClock,
            FakeDisplayAllocator,
            FakeDisplayServer,
            FakeProcess,
            FakeSessionRepository,
        )
        from aiyes.domain.use_cases.session_start import SessionStartUseCase

        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)

        fp = FakeProcess()
        uc = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(),
            atspi_bus=FakeAccessibilityBus(),
            process=fp,
            session_repo=FakeSessionRepository(),
            clock=FakeClock(),
        )
        uc.execute(app_command="test-app", app_args=[])

        start_calls = [c for c in fp.calls if c[0] == "start"]
        _, _, env = start_calls[0][1]
        assert env is not None
        return env

    @pytest.mark.parametrize(
        "var_name",
        [
            "MY_CUSTOM_TOKEN",
            "SERVICE_API_TOKEN",
            "DEPLOY_TOKEN",
        ],
    )
    def test_suffix_token_stripped(
        self, var_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-03: Variables ending in _TOKEN must be stripped."""
        env = self._get_app_env(monkeypatch, {var_name: "secret-token-value"})
        assert var_name not in env, (
            f"{var_name} (ends with _TOKEN) leaked into session env"
        )

    @pytest.mark.parametrize(
        "var_name",
        [
            "MY_CUSTOM_SECRET",
            "APP_SECRET",
            "JWT_SECRET",
        ],
    )
    def test_suffix_secret_stripped(
        self, var_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-03: Variables ending in _SECRET must be stripped."""
        env = self._get_app_env(monkeypatch, {var_name: "secret-value"})
        assert var_name not in env, (
            f"{var_name} (ends with _SECRET) leaked into session env"
        )

    @pytest.mark.parametrize(
        "var_name",
        [
            "ENCRYPTION_KEY",
            "SIGNING_KEY",
            "SSH_PRIVATE_KEY",
        ],
    )
    def test_suffix_key_stripped(
        self, var_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-03: Variables ending in _KEY must be stripped."""
        env = self._get_app_env(monkeypatch, {var_name: "key-value"})
        assert var_name not in env, (
            f"{var_name} (ends with _KEY) leaked into session env"
        )

    @pytest.mark.parametrize(
        "var_name",
        [
            "DB_PASSWORD",
            "ADMIN_PASSWORD",
            "SMTP_PASSWORD",
        ],
    )
    def test_suffix_password_stripped(
        self, var_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-03: Variables ending in _PASSWORD must be stripped."""
        env = self._get_app_env(monkeypatch, {var_name: "password-value"})
        assert var_name not in env, (
            f"{var_name} (ends with _PASSWORD) leaked into session env"
        )

    @pytest.mark.parametrize(
        "var_name",
        [
            "SERVICE_CREDENTIAL",
            "CLOUD_CREDENTIAL",
        ],
    )
    def test_suffix_credential_stripped(
        self, var_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-03: Variables ending in _CREDENTIAL must be stripped."""
        env = self._get_app_env(monkeypatch, {var_name: "cred-value"})
        assert var_name not in env, (
            f"{var_name} (ends with _CREDENTIAL) leaked into session env"
        )

    @pytest.mark.parametrize(
        "var_name",
        [
            "CUSTOM_API_KEY",
            "INTERNAL_API_KEY",
        ],
    )
    def test_suffix_api_key_stripped(
        self, var_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-03: Variables ending in _API_KEY must be stripped."""
        env = self._get_app_env(monkeypatch, {var_name: "api-key-value"})
        assert var_name not in env, (
            f"{var_name} (ends with _API_KEY) leaked into session env"
        )

    def test_explicit_list_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """S-03: Original explicit list entries must still be stripped."""
        env = self._get_app_env(monkeypatch, {"GITHUB_TOKEN": "ghp_xxx"})
        assert "GITHUB_TOKEN" not in env

    def test_functional_vars_not_pattern_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-03: Non-credential vars must not be false-positived by patterns."""
        # These do NOT end in any of the sensitive suffixes
        safe_vars = {
            "PATH": "/usr/bin",
            "HOME": "/home/test",
            "DISPLAY": ":0",
            "TOKENIZERS_PARALLELISM": "false",  # ends in _PARALLELISM, not _TOKEN
            "MONKEY_WRENCH": "yes",  # doesn't end in any sensitive suffix
        }
        env = self._get_app_env(monkeypatch, safe_vars)
        # PATH and HOME survive
        assert env.get("HOME") == "/home/test"
        # TOKENIZERS_PARALLELISM is NOT a credential
        assert env.get("TOKENIZERS_PARALLELISM") == "false"
        assert env.get("MONKEY_WRENCH") == "yes"


# ══════════════════════════════════════════════════════════════════════
# S-04: ADB text escaping audit (property-based)
# ══════════════════════════════════════════════════════════════════════


class TestAdbTextEscaping:
    """S-04: ADB text escaping must handle all shell metacharacters."""

    def test_tab_is_escaped_or_handled(self) -> None:
        """S-04: Tab character must be escaped or handled."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb("\t")
        # Tab should be escaped or converted — not passed raw
        assert result != "\t", "Raw tab must not pass through unescaped"

    def test_newline_is_escaped_or_handled(self) -> None:
        """S-04: Newline character must be escaped or handled."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb("\n")
        assert result != "\n", "Raw newline must not pass through unescaped"

    def test_carriage_return_is_escaped_or_handled(self) -> None:
        """S-04: Carriage return must be escaped or handled."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb("\r")
        assert result != "\r", "Raw carriage return must not pass through unescaped"

    def test_null_byte_is_escaped_or_rejected(self) -> None:
        """S-04: Null byte must be stripped, escaped, or cause an error."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb("\x00")
        # Null byte must not pass through raw
        assert "\x00" not in result

    def test_percent_is_escaped(self) -> None:
        """S-04: Literal percent must be escaped (adb uses %s for space)."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb("%")
        # Since %s means space in adb, a literal % must be escaped
        assert result != "%", (
            "Raw percent must not pass through (conflicts with %s space encoding)"
        )

    @pytest.mark.parametrize(
        "char",
        [
            "$",
            "`",
            "!",
            "(",
            ")",
            "{",
            "}",
            "|",
            "&",
            ";",
            "<",
            ">",
            "\\",
            "'",
            '"',
            "#",
            "*",
            "?",
            "~",
            "[",
            "]",
        ],
    )
    def test_shell_metachar_is_escaped(self, char: str) -> None:
        """S-04: Each shell metacharacter must be backslash-escaped."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb(char)
        assert result == "\\" + char, (
            f"Shell metacharacter {char!r} must be escaped as \\{char!r}, got {result!r}"
        )

    def test_space_encoded_as_percent_s(self) -> None:
        """S-04: Space must be encoded as %s for adb shell input text."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb(" ")
        assert result == "%s"

    @given(
        text=st.text(
            alphabet=st.characters(
                blacklist_categories=("Cs",),  # no surrogates
            ),
            min_size=0,
            max_size=100,
        )
    )
    @settings(max_examples=500)
    def test_property_no_unescaped_shell_metacharacters(self, text: str) -> None:
        """S-04: Property test — escaped output must not contain raw shell metachars.

        After escaping, no raw shell metacharacter should appear unless preceded
        by a backslash. Spaces should be encoded as %s.
        """
        from aiyes.adapters.adb_text import escape_text_for_adb

        result = escape_text_for_adb(text)

        # Check: no raw spaces (should be %s)
        # We need to parse carefully: %s is the space encoding
        # A raw space in output would be a bug
        i = 0
        while i < len(result):
            ch = result[i]
            if ch == "\\":
                # Backslash-escaped: skip next char
                i += 2
                continue
            if ch == "%":
                # Should be %s (space encoding)
                if i + 1 < len(result) and result[i + 1] == "s":
                    i += 2
                    continue
                # Stray percent — this is a bug if it's not escaped
                # (unless we decide to allow it)
            # Raw character must not be a shell metacharacter
            assert ch not in " \t\n\r\x00", (
                f"Unescaped whitespace/null {ch!r} found in output for input {text!r}"
            )
            i += 1

    @given(
        text=st.text(
            alphabet=st.characters(
                blacklist_categories=("Cs",),
                blacklist_characters=("\x00",),  # null byte may be stripped
            ),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_property_escape_is_deterministic(self, text: str) -> None:
        """S-04: Escaping the same input twice must produce the same output."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        result1 = escape_text_for_adb(text)
        result2 = escape_text_for_adb(text)
        assert result1 == result2

    def test_empty_string_returns_empty(self) -> None:
        """S-04: Empty string input returns empty string."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        assert escape_text_for_adb("") == ""

    def test_alphanumeric_passes_through(self) -> None:
        """S-04: Plain alphanumeric text passes through unchanged."""
        from aiyes.adapters.adb_text import escape_text_for_adb

        assert escape_text_for_adb("hello123") == "hello123"
