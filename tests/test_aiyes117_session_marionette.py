"""AIYES-117 — session_start marionette opt-in + dual-surface state (RED).

Pins FR-05 (C-PORTALLOC + C-STATESURFACE) and NFR-01 (C-BACKCOMPAT):

  * session_start marionette opt-in on a firefox/linux launch: a DISTINCT port
    (2828 + display_num, DEC-A7-02), `-marionette` spliced into the launch argv,
    and Session.marionette_port recorded;
  * a non-firefox app_command + marionette=True is REJECTED with ValueError
    (DEC-A7-04), surfaced as status=error at the CLI/MCP boundary;
  * marionette_enabled + marionette_port surface as TOP-LEVEL fields on BOTH
    session_capabilities AND session_status (DEC-A7-05), consistent across the
    two surfaces (CAP.marionette_port == ST.marionette_port == session.marionette_port);
  * Session.marionette_port is an APPENDED Optional[int]=None field — kwargs-safe
    for the 123 existing construction sites — and session_to_dict OMITS it when
    None while INCLUDING the int when set (the contract's dominant
    silent-pass-on-miss serializer site);
  * BOTH format_session_status callers (MCP _handle_session_status, CLI
    session_status_cmd) pass the two values through — the 4-site lockstep whose
    per-caller miss is a silent per-entry-point degrade (A-W1).

RED discipline: the module collects cleanly (all top-level imports resolve). Each
new-behavior test fails at CALL time — TypeError on the not-yet-present
`marionette` execute() param / `marionette_port` Session kwarg /
`marionette_enabled` result field / format_session_status kwarg, or the
serializer / caller-passthrough assertion.

Traceability — must_tier1_coverage_matrix evidence_pointers:
  FR-05  [C-PORTALLOC,C-STATESURFACE] ->
             test_marionette_launch_records_distinct_port_capability_and_status
  NFR-01 [C-BACKCOMPAT] ->
             test_marionette_port_appended_kwargs_safe_and_omitted_when_none
"""

from __future__ import annotations

import dataclasses
import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from aiyes.adapters.file_marionette_profile import FileMarionetteProfile
from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.cli.main import cli
from aiyes.cli.presenter import format_session_capabilities, format_session_status
from aiyes.domain.output_formatter import session_to_dict
from aiyes.domain.session import Session
from aiyes.domain.use_cases.session_capabilities import (
    SessionCapabilitiesResult,
    SessionCapabilitiesUseCase,
)
from aiyes.domain.use_cases.session_start import SessionStartUseCase
from aiyes.domain.use_cases.session_status import SessionStatusUseCase

import aiyes.cli.composition_root as comp_root
import aiyes.cli.main as cli_main

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
    make_cli_runner,
)

_MARIONETTE_BASE_PORT = 2828  # DEC-A7-02: marionette_port = 2828 + display_num


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


class FakeMarionetteProfile:
    """Observable fake for MarionetteProfilePort.

    Records the (session_id, port, existing_profile) provision requests and the
    port each returned profile dir was configured with, so the launch oracle can
    assert the DERIVED port is actually conveyed to Firefox (A10-AF-003), not
    merely recorded on the Session.
    """

    def __init__(self) -> None:
        self.provision_calls: list = []  # (session_id, port, existing_profile)
        self.cleanup_calls: list = []
        self._configured_port: dict = {}  # profile_dir -> marionette.port value

    def provision(self, session_id, port, existing_profile):
        self.provision_calls.append((session_id, port, existing_profile))
        profile_dir = existing_profile or f"/fake/aiyes-marionette-profile-{session_id}"
        # Simulate writing user.js: the returned profile carries this port.
        self._configured_port[profile_dir] = port
        return profile_dir

    def cleanup(self, session_id):
        self.cleanup_calls.append(session_id)

    def configured_port(self, profile_dir):
        """The marionette.port value the fake wrote into that profile's user.js."""
        return self._configured_port.get(profile_dir)


def _start_uc(
    repo: FakeSessionRepository,
    process: FakeProcess,
    display_num: int = 99,
    profile=None,
) -> SessionStartUseCase:
    return SessionStartUseCase(
        display_server=FakeDisplayServer(),
        allocator=FakeDisplayAllocator(display_num=display_num),
        atspi_bus=FakeAccessibilityBus(),
        process=process,
        session_repo=repo,
        clock=FakeClock(),
        marionette_profile=profile if profile is not None else FakeMarionetteProfile(),
    )


def _status_uc(repo: FakeSessionRepository) -> SessionStatusUseCase:
    return SessionStatusUseCase(
        session_repo=repo,
        process=MagicMock(),
        window_query=MagicMock(),
        adb_activity=MagicMock(),
    )


def _make_mock_deps(**overrides: Any) -> ServerDependencies:
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    fields.update(overrides)
    return ServerDependencies(**fields)


def _content_text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


def _base_session_kwargs(session_id: str = "mz") -> dict:
    return dict(
        session_id=session_id,
        display=":9",
        app_pid=1,
        app_command="firefox",
        app_args=(),
        xvfb_pid=2,
        name=None,
        backend="linux",
    )


# ═══════════════════════════════════════════════════════════════════════
# FR-05 [C-PORTALLOC + C-STATESURFACE] — the designated evidence test
# ═══════════════════════════════════════════════════════════════════════


class TestMarionetteLaunchAndSurfacingFR05:
    def test_marionette_launch_records_distinct_port_capability_and_status(
        self,
    ) -> None:
        repo = FakeSessionRepository()
        process = FakeProcess()
        session = _start_uc(repo, process, display_num=99).execute(
            app_command="firefox", app_args=[], wait=0.0, marionette=True
        )

        # marionette_port recorded on the launched session (2828 + 99).
        assert session.marionette_port == _MARIONETTE_BASE_PORT + 99

        # (A) session_capabilities surfaces the state (top-level fields).
        cap = SessionCapabilitiesUseCase(session_repo=repo).execute(session.session_id)
        assert cap.marionette_enabled is True
        assert cap.marionette_port == _MARIONETTE_BASE_PORT + 99

        # (B) session_status surfaces the SAME state (top-level fields).
        st = _status_uc(repo).execute(session.session_id)
        assert st.marionette_enabled is True
        assert st.marionette_port == _MARIONETTE_BASE_PORT + 99

        # Cross-surface consistency predicate (C-STATESURFACE).
        assert cap.marionette_port == st.marionette_port == session.marionette_port

    def test_two_sessions_get_distinct_marionette_ports(self) -> None:
        repo = FakeSessionRepository()
        s1 = _start_uc(repo, FakeProcess(), display_num=5).execute(
            app_command="firefox", app_args=[], wait=0.0, marionette=True
        )
        s2 = _start_uc(repo, FakeProcess(), display_num=12).execute(
            app_command="firefox", app_args=[], wait=0.0, marionette=True
        )
        # Distinctness is the binding invariant (SC-04 / RISK-04: no 2828 collision).
        assert s1.marionette_port != s2.marionette_port
        assert isinstance(s1.marionette_port, int)
        assert isinstance(s2.marionette_port, int)
        assert s1.marionette_port == _MARIONETTE_BASE_PORT + 5
        assert s2.marionette_port == _MARIONETTE_BASE_PORT + 12

    def test_marionette_conveys_derived_port_to_firefox_launch(self) -> None:
        """A10-AF-003: the DERIVED port must actually reach Firefox, not just the
        Session. Firefox honours the port only via the `marionette.port` profile
        pref (the CLI arg is ignored — proven by live probe), so the launch MUST
        (a) ask the profile port to configure the derived port, (b) pass that
        profile to Firefox via `-profile`, and (c) that profile's user.js must
        carry the derived port. Asserting only `-marionette` + recorded port (the
        old oracle) let A10-AF-001 ship: a recorded port Firefox never listened on.
        """
        repo = FakeSessionRepository()
        process = FakeProcess()
        profile = FakeMarionetteProfile()
        session = _start_uc(repo, process, display_num=99, profile=profile).execute(
            app_command="firefox", app_args=[], wait=0.0, marionette=True
        )
        derived = _MARIONETTE_BASE_PORT + 99

        # (a) The profile port was asked to make Firefox listen on the derived port.
        assert profile.provision_calls, (
            "MarionetteProfilePort.provision was never called — Firefox would "
            "listen on the default 2828, not the recorded derived port (AF-001)."
        )
        _sid, requested_port, existing = profile.provision_calls[-1]
        assert requested_port == derived, (
            f"profile configured for port {requested_port}, expected {derived}"
        )
        assert existing is None  # no caller-supplied -profile in this launch

        # (b) The derived-port profile is conveyed to Firefox via -profile in argv.
        start_calls = [c for c in process.calls if c[0] == "start"]
        assert start_calls, "process.start was never invoked"
        _, (command, args, env) = start_calls[-1]
        args = list(args)
        assert "-marionette" in args, (
            f"'-marionette' not spliced into launch argv: {args!r}"
        )
        assert "-profile" in args, (
            f"'-profile' not spliced into launch argv — derived port is not "
            f"conveyed to Firefox: {args!r}"
        )
        profile_dir = args[args.index("-profile") + 1]

        # (c) That profile's user.js carries the DERIVED port (the conveyance).
        assert profile.configured_port(profile_dir) == derived, (
            f"profile {profile_dir!r} was not configured with derived port {derived}"
        )

        # Recorded port stays consistent with what Firefox is configured for.
        assert session.marionette_port == derived

    def test_marionette_reuses_caller_supplied_profile_and_cleans_up(self) -> None:
        """When the caller already passes `-profile <dir>`, we augment THAT
        profile (append the pref) instead of creating a temp one, and do not
        splice a second `-profile`."""
        repo = FakeSessionRepository()
        process = FakeProcess()
        profile = FakeMarionetteProfile()
        caller_dir = "/caller/owned/profile"
        session = _start_uc(repo, process, display_num=7, profile=profile).execute(
            app_command="firefox",
            app_args=["-profile", caller_dir],
            wait=0.0,
            marionette=True,
        )
        derived = _MARIONETTE_BASE_PORT + 7
        _sid, requested_port, existing = profile.provision_calls[-1]
        assert requested_port == derived
        assert existing == caller_dir  # caller profile detected + augmented
        assert profile.configured_port(caller_dir) == derived

        start_calls = [c for c in process.calls if c[0] == "start"]
        _, (command, args, env) = start_calls[-1]
        args = list(args)
        # Exactly one -profile token, pointing at the caller dir (not a temp one).
        assert args.count("-profile") == 1
        assert args[args.index("-profile") + 1] == caller_dir
        assert session.marionette_port == derived

    def test_marionette_requires_profile_port_wired(self) -> None:
        """Defensive: a marionette launch with no MarionetteProfilePort wired
        must fail loudly rather than silently record an unreachable port."""
        repo = FakeSessionRepository()
        uc = SessionStartUseCase(
            display_server=FakeDisplayServer(),
            allocator=FakeDisplayAllocator(display_num=3),
            atspi_bus=FakeAccessibilityBus(),
            process=FakeProcess(),
            session_repo=repo,
            clock=FakeClock(),
            marionette_profile=None,
        )
        with pytest.raises(RuntimeError):
            uc.execute(app_command="firefox", app_args=[], wait=0.0, marionette=True)

    def test_non_firefox_marionette_rejected_with_value_error(self) -> None:
        repo = FakeSessionRepository()
        with pytest.raises(ValueError):
            _start_uc(repo, FakeProcess()).execute(
                app_command="gedit", app_args=[], wait=0.0, marionette=True
            )
        # No marionette-bearing session persisted (rejected before save).
        assert all(getattr(s, "marionette_port", None) is None for s in repo.load_all())

    def test_non_marionette_launch_leaves_state_absent(self) -> None:
        # A firefox launch WITHOUT marionette must not set the port; both
        # surfaces report disabled/None (None/false when not marionette-launched).
        repo = FakeSessionRepository()
        session = _start_uc(repo, FakeProcess()).execute(
            app_command="firefox", app_args=[], wait=0.0, marionette=False
        )
        assert getattr(session, "marionette_port", None) is None
        cap = SessionCapabilitiesUseCase(session_repo=repo).execute(session.session_id)
        assert cap.marionette_enabled is False
        assert cap.marionette_port is None
        st = _status_uc(repo).execute(session.session_id)
        assert st.marionette_enabled is False
        assert st.marionette_port is None


# ═══════════════════════════════════════════════════════════════════════
# NFR-01 [C-BACKCOMPAT] — appended field, kwargs-safe, omit-when-None
# ═══════════════════════════════════════════════════════════════════════


class TestSessionMarionettePortBackCompatNFR01:
    def test_marionette_port_appended_kwargs_safe_and_omitted_when_none(
        self,
    ) -> None:
        # ---- present-int: field carried AND serialized ------------------
        with_port = Session(marionette_port=2927, **_base_session_kwargs("mz"))
        assert with_port.marionette_port == 2927
        d_with = session_to_dict(with_port)
        assert d_with["marionette_port"] == 2927

        # ---- absent-when-None: serializer OMITS the key (dominant risk) ---
        without = Session(marionette_port=None, **_base_session_kwargs("mz2"))
        d_without = session_to_dict(without)
        assert "marionette_port" not in d_without

    def test_existing_session_construction_unaffected(self) -> None:
        # GREEN-throughout back-compat guard: an existing-shape construction
        # (no marionette_port kwarg) keeps working; the field defaults to None
        # and is omitted from the serialized dict. Proves the append is
        # kwargs-safe for the 123 pre-existing Session(...) sites.
        s = Session(
            session_id="bc",
            app_pid=1,
            app_command="gedit",
            app_args=(),
            name=None,
        )
        assert s.session_id == "bc"
        assert getattr(s, "marionette_port", None) is None
        assert "marionette_port" not in session_to_dict(s)


# ═══════════════════════════════════════════════════════════════════════
# C-STATESURFACE surfaced form — presenter output on BOTH surfaces
# ═══════════════════════════════════════════════════════════════════════


class TestPresenterSurfacing:
    def test_capabilities_presenter_surfaces_marionette(self) -> None:
        enabled = SessionCapabilitiesResult(
            session_id="x",
            backend="linux",
            capabilities={},
            marionette_enabled=True,
            marionette_port=2927,
        )
        out = json.loads(format_session_capabilities(enabled))
        assert out["marionette_enabled"] is True
        assert out["marionette_port"] == 2927

        disabled = SessionCapabilitiesResult(
            session_id="y",
            backend="linux",
            capabilities={},
            marionette_enabled=False,
            marionette_port=None,
        )
        out_none = json.loads(format_session_capabilities(disabled))
        assert out_none["marionette_enabled"] is False
        assert "marionette_port" not in out_none  # omit-when-None (NFR-01)

    def test_status_presenter_surfaces_marionette(self) -> None:
        out = json.loads(
            format_session_status(
                app_alive=True,
                app_foreground=False,
                display_alive=True,
                marionette_enabled=True,
                marionette_port=2927,
            )
        )
        assert out["marionette_enabled"] is True
        assert out["marionette_port"] == 2927

        out_none = json.loads(
            format_session_status(
                app_alive=True,
                app_foreground=False,
                display_alive=True,
                marionette_enabled=False,
                marionette_port=None,
            )
        )
        assert out_none["marionette_enabled"] is False
        assert "marionette_port" not in out_none  # omit-when-None


# ═══════════════════════════════════════════════════════════════════════
# A-W1 — the 4-site lockstep: BOTH session_status callers pass through
# ═══════════════════════════════════════════════════════════════════════


def _status_result_double() -> SimpleNamespace:
    return SimpleNamespace(
        app_alive=True,
        app_foreground=False,
        display_alive=True,
        marionette_enabled=True,
        marionette_port=2927,
    )


class TestSessionStatusCallerLockstep:
    @pytest.mark.asyncio
    async def test_mcp_session_status_surfaces_marionette(self) -> None:
        spy_uc = SimpleNamespace(execute=lambda **kw: _status_result_double())
        clock = MagicMock()
        clock.now.return_value = 1000.0
        deps = _make_mock_deps(
            session_status_uc=spy_uc,
            resolve_session_id=MagicMock(return_value="sess-x"),
            clock=clock,
            operation_log=MagicMock(),
        )
        server = create_mcp_server(deps)

        result = await server.call_tool("session_status", {"session_id": "raw"})
        parsed = json.loads(_content_text(result))
        # RED now: the MCP caller does not pass marionette state through, so
        # these keys are absent (per-entry-point silent degrade).
        assert parsed["marionette_enabled"] is True
        assert parsed["marionette_port"] == 2927

    def test_cli_session_status_surfaces_marionette(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy_uc = SimpleNamespace(execute=lambda **kw: _status_result_double())
        monkeypatch.setattr(comp_root, "session_status_uc", spy_uc)
        monkeypatch.setattr(cli_main, "resolve_session_id", lambda s: s or "sess-x")

        runner = make_cli_runner()
        res = runner.invoke(cli, ["session", "status", "--session", "sess-x"])
        assert res.exit_code == 0, res.output
        parsed = json.loads(res.output)
        # RED now: the CLI caller (session_status_cmd) does not pass marionette
        # state through, so the keys are absent.
        assert parsed["marionette_enabled"] is True
        assert parsed["marionette_port"] == 2927


# ═══════════════════════════════════════════════════════════════════════
# A10-AF-001 conveyance — the FileMarionetteProfile adapter actually writes the
# `marionette.port` pref Firefox honours (the only mechanism that works; the
# `--marionette-port` CLI arg is ignored, proven by live probe).
# ═══════════════════════════════════════════════════════════════════════


class TestFileMarionetteProfileConveyance:
    def test_temp_profile_user_js_carries_only_the_derived_pref(self, tmp_path) -> None:
        adapter = FileMarionetteProfile(base_dir=str(tmp_path))
        profile_dir = adapter.provision(
            session_id="abc123", port=2927, existing_profile=None
        )
        user_js = os.path.join(profile_dir, "user.js")
        assert os.path.isfile(user_js)
        with open(user_js, encoding="utf-8") as handle:
            body = handle.read()
        # The derived port is written as the marionette.port pref (the conveyance).
        assert 'user_pref("marionette.port", 2927);' in body
        # Stay in scope: ONLY that pref, nothing else.
        pref_lines = [ln for ln in body.splitlines() if ln.strip()]
        assert pref_lines == ['user_pref("marionette.port", 2927);']

    def test_cleanup_removes_the_temp_profile(self, tmp_path) -> None:
        adapter = FileMarionetteProfile(base_dir=str(tmp_path))
        profile_dir = adapter.provision(
            session_id="abc123", port=2927, existing_profile=None
        )
        assert os.path.isdir(profile_dir)
        adapter.cleanup("abc123")
        assert not os.path.exists(profile_dir)

    def test_existing_profile_is_augmented_not_replaced_and_cleanup_is_noop(
        self, tmp_path
    ) -> None:
        adapter = FileMarionetteProfile(base_dir=str(tmp_path))
        caller_dir = tmp_path / "caller-profile"
        caller_dir.mkdir()
        (caller_dir / "user.js").write_text(
            'user_pref("browser.startup.homepage", "about:blank");\n',
            encoding="utf-8",
        )
        returned = adapter.provision(
            session_id="abc123", port=2929, existing_profile=str(caller_dir)
        )
        assert returned == str(caller_dir)
        body = (caller_dir / "user.js").read_text(encoding="utf-8")
        # Caller's pref preserved AND our derived pref appended (last wins).
        assert 'user_pref("browser.startup.homepage", "about:blank");' in body
        assert 'user_pref("marionette.port", 2929);' in body
        # cleanup only targets the aiyes-owned temp dir — caller dir is untouched.
        adapter.cleanup("abc123")
        assert caller_dir.exists()
        assert (caller_dir / "user.js").exists()
