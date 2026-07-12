"""AIYES-117 — Firefox/Marionette DOM-lens use-cases (RED).

Pins the four DOM-lens domain use-cases against a FAKE MarionettePort (no live
Firefox): EvalUseCase (value + DEC-07 auto-return wrap + JS-error->status=error),
QueryDomUseCase (shape/count/cap-50/truncation/empty-ok), PageTextUseCase
(body-default + scoped hit/miss found flag), ScreenshotSelectorUseCase (stores a
PNG path + width/height via the AIYES-111 read_dimensions path; no-match ->
status=error), the FR-06 zero-I/O guards (non-linux OR marionette-disabled ->
status=error with the MarionettePort NEVER invoked; unknown session -> RuntimeError),
and the FR-09 advertised==dispatch parity for the four new tools.

RED discipline (mirrors AIYES-116): the module COLLECTS cleanly — every top-level
import resolves against the current tree. The net-new symbols (EvalUseCase,
QueryDomUseCase, PageTextUseCase, ScreenshotSelectorUseCase, MarionetteScriptOutcome,
COMPUTED_STYLE_SUBSET, QUERY_DOM_NODE_LIMIT) are imported INSIDE the tests/helpers,
so each new-behavior test fails at CALL time (ImportError on the absent module,
TypeError on an absent kwarg, or the wiring/shape assertion) — never as a collection
error that would perturb the 2656-test baseline.

Traceability — must_tier1_coverage_matrix evidence_pointers:
  FR-01 [C-EVAL,C-EVALWRAP] -> test_eval_returns_value_and_maps_js_error
  FR-02 [C-QUERYDOM]        -> test_query_dom_shape_count_and_truncation
  FR-03 [C-PAGETEXT]        -> test_page_text_body_and_scoped
  FR-04 [C-SCREENSHOT]      -> test_screenshot_selector_stores_png_with_dimensions
  FR-06 [C-GUARD]           -> test_guards_non_linux_and_marionette_disabled_no_io
  FR-09 [C-PARITY]          -> test_advertised_equals_dispatch_for_dom_lens_tools
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from aiyes.adapters.mcp_server import _build_dispatch_table
from aiyes.cli.main import cli
from aiyes.cli.schema_gen import enumerate_commands

from tests.conftest import FakeScreenshotStore, FakeSessionRepository

# The four DOM-lens tools this contract wires (FR-09 parity target set).
_DOM_LENS_TOOLS = frozenset({"eval", "query_dom", "page_text", "screenshot_selector"})

# The frozen 15-prop computed-style subset (DEC-06), in declared order. The
# QueryDomUseCase must expose exactly these keys as COMPUTED_STYLE_SUBSET.
_EXPECTED_COMPUTED_SUBSET = [
    "display",
    "visibility",
    "position",
    "width",
    "height",
    "color",
    "background-color",
    "border-radius",
    "transform",
    "opacity",
    "font-size",
    "font-weight",
    "margin",
    "padding",
    "z-index",
]

_RECT_KEYS = {"x", "y", "width", "height", "top", "right", "bottom", "left"}


# ═══════════════════════════════════════════════════════════════════════
# Fakes / helpers (deferred net-new imports keep collection clean)
# ═══════════════════════════════════════════════════════════════════════


def _outcome(ok: bool, value: Any = None, error: Optional[str] = None) -> Any:
    """Build a real MarionetteScriptOutcome (deferred import — RED if absent)."""
    from aiyes.domain.marionette_outcome import MarionetteScriptOutcome

    return MarionetteScriptOutcome(ok=ok, value=value, error=error)


class FakeMarionettePort:
    """In-memory MarionettePort double.

    Records every execute_script/screenshot_element call so a guarded (zero-I/O)
    path can be proven to never touch it, and so the exact (auto-return-wrapped)
    script string handed to the port is directly assertable (C-EVALWRAP).
    """

    def __init__(
        self,
        script_outcome: Any = None,
        screenshot_path: Optional[str] = "/tmp/aiyes117-shot.png",
    ) -> None:
        self._script_outcome = script_outcome
        self._screenshot_path = screenshot_path
        self.scripts: List[str] = []
        self.execute_script_calls = 0
        self.screenshot_element_calls = 0
        self.screenshot_selectors: List[str] = []

    def execute_script(self, session: Any, script: str, args: Any = None) -> Any:
        self.execute_script_calls += 1
        self.scripts.append(script)
        return self._script_outcome

    def screenshot_element(self, session: Any, css_selector: str) -> Optional[str]:
        self.screenshot_element_calls += 1
        self.screenshot_selectors.append(css_selector)
        return self._screenshot_path


def _session(
    session_id: str = "s117",
    backend: str = "linux",
    marionette_port: Optional[int] = 2927,
) -> SimpleNamespace:
    """A duck-typed session double (decouples the use-case tests from the
    Session.marionette_port field append, which is pinned in the sibling
    session-marionette test file)."""
    return SimpleNamespace(
        session_id=session_id,
        backend=backend,
        marionette_port=marionette_port,
        app_pid=100,
    )


def _repo_with(session: SimpleNamespace) -> FakeSessionRepository:
    repo = FakeSessionRepository()
    repo.save(session)
    return repo


def _eval_uc(port: Any, repo: FakeSessionRepository) -> Any:
    from aiyes.domain.use_cases.eval import EvalUseCase

    return EvalUseCase(marionette=port, session_repo=repo)


def _query_dom_uc(port: Any, repo: FakeSessionRepository) -> Any:
    from aiyes.domain.use_cases.query_dom import QueryDomUseCase

    return QueryDomUseCase(marionette=port, session_repo=repo)


def _page_text_uc(port: Any, repo: FakeSessionRepository) -> Any:
    from aiyes.domain.use_cases.page_text import PageTextUseCase

    return PageTextUseCase(marionette=port, session_repo=repo)


def _screenshot_selector_uc(port: Any, repo: FakeSessionRepository, store: Any) -> Any:
    from aiyes.domain.use_cases.screenshot_selector import (
        ScreenshotSelectorUseCase,
    )

    return ScreenshotSelectorUseCase(
        marionette=port, session_repo=repo, screenshot_store=store
    )


def _node_payload() -> dict:
    """A single query_dom node payload as the content-context JS would return it."""
    return {
        "rect": {
            "x": 10,
            "y": 20,
            "width": 100,
            "height": 40,
            "top": 20,
            "right": 110,
            "bottom": 60,
            "left": 10,
        },
        "computed": {prop: "auto" for prop in _EXPECTED_COMPUTED_SUBSET},
        "classList": ["btn", "primary"],
        "textContent": "Submit",
    }


# ═══════════════════════════════════════════════════════════════════════
# FR-01 [C-EVAL + C-EVALWRAP] — value, JS-error mapping, DEC-07 auto-return
# ═══════════════════════════════════════════════════════════════════════


class TestEvalUseCaseFR01:
    def test_eval_returns_value_and_maps_js_error(self) -> None:
        # ---- ok path: the JSON value is returned verbatim -----------------
        session = _session()
        repo = _repo_with(session)
        ok_port = FakeMarionettePort(
            script_outcome=_outcome(ok=True, value="AIYES Demo")
        )
        result = _eval_uc(ok_port, repo).execute(session.session_id, "document.title")
        assert result.status == "ok"
        assert result.value == "AIYES Demo"
        assert result.action == "eval"

        # ---- DEC-07 auto-return wrap: a bare expression is wrapped --------
        # (the exact string handed to the port is asserted; no `return` token
        # present => wrapped as "return (<script>);").
        assert ok_port.scripts[-1] == "return (document.title);"

        # ---- DEC-07: a return-bearing script is sent VERBATIM -------------
        verbatim_port = FakeMarionettePort(script_outcome=_outcome(ok=True, value=4))
        _eval_uc(verbatim_port, _repo_with(session)).execute(
            session.session_id, "return 2 + 2;"
        )
        assert verbatim_port.scripts[-1] == "return 2 + 2;"

        # ---- error path: a JS exception maps to status=error, no crash ----
        err_port = FakeMarionettePort(
            script_outcome=_outcome(
                ok=False, error="ReferenceError: foo is not defined"
            )
        )
        err_result = _eval_uc(err_port, _repo_with(session)).execute(
            session.session_id, "foo()"
        )
        assert err_result.status == "error"
        assert "ReferenceError" in (err_result.reason or "")
        assert err_result.value is None


# ═══════════════════════════════════════════════════════════════════════
# FR-02 [C-QUERYDOM] — shape, true count, node cap(50), truncation, empty-ok
# ═══════════════════════════════════════════════════════════════════════


class TestQueryDomUseCaseFR02:
    def test_query_dom_shape_count_and_truncation(self) -> None:
        from aiyes.domain.use_cases.query_dom import (
            COMPUTED_STYLE_SUBSET,
            QUERY_DOM_NODE_LIMIT,
        )

        # Frozen constants (DEC-06 / SC-02) — literal, checkable.
        assert list(COMPUTED_STYLE_SUBSET) == _EXPECTED_COMPUTED_SUBSET
        assert QUERY_DOM_NODE_LIMIT == 50

        session = _session()

        # ---- shape (small match): rect(8)/computed(15)/classList/textContent
        shape_port = FakeMarionettePort(
            script_outcome=_outcome(
                ok=True, value={"count": 1, "nodes": [_node_payload()]}
            )
        )
        shape = _query_dom_uc(shape_port, _repo_with(session)).execute(
            session.session_id, ".btn"
        )
        assert shape.status == "ok"
        assert shape.count == 1
        assert len(shape.nodes) == 1
        assert shape.truncated is False
        node = shape.nodes[0]
        assert set(node.rect.keys()) == _RECT_KEYS
        assert set(node.computed.keys()) == set(_EXPECTED_COMPUTED_SUBSET)
        assert isinstance(node.classList, list)
        assert isinstance(node.textContent, str)

        # ---- cap + truncation: N=60 true total, nodes capped at 50 --------
        big_port = FakeMarionettePort(
            script_outcome=_outcome(
                ok=True,
                value={"count": 60, "nodes": [_node_payload() for _ in range(60)]},
            )
        )
        big = _query_dom_uc(big_port, _repo_with(session)).execute(
            session.session_id, "div"
        )
        assert big.count == 60  # true total (not the capped length)
        assert len(big.nodes) == 50  # min(N, QUERY_DOM_NODE_LIMIT)
        assert big.truncated is True  # count > 50

        # ---- empty match is status=ok (NOT an error) ----------------------
        empty_port = FakeMarionettePort(
            script_outcome=_outcome(ok=True, value={"count": 0, "nodes": []})
        )
        empty = _query_dom_uc(empty_port, _repo_with(session)).execute(
            session.session_id, ".nope"
        )
        assert empty.status == "ok"
        assert empty.count == 0
        assert empty.nodes == []
        assert empty.truncated is False


# ═══════════════════════════════════════════════════════════════════════
# FR-03 [C-PAGETEXT] — body-default vs scoped selector, found flag
# ═══════════════════════════════════════════════════════════════════════


class TestPageTextUseCaseFR03:
    def test_page_text_body_and_scoped(self) -> None:
        session = _session()

        # ---- default: document.body.innerText, found=True -----------------
        body_port = FakeMarionettePort(
            script_outcome=_outcome(
                ok=True, value={"text": "Hello whole page", "found": True}
            )
        )
        body = _page_text_uc(body_port, _repo_with(session)).execute(session.session_id)
        assert body.status == "ok"
        assert body.found is True
        assert body.text == "Hello whole page"

        # ---- scoped hit: the element's innerText, found=True --------------
        hit_port = FakeMarionettePort(
            script_outcome=_outcome(
                ok=True, value={"text": "Scoped text", "found": True}
            )
        )
        hit = _page_text_uc(hit_port, _repo_with(session)).execute(
            session.session_id, ".main"
        )
        assert hit.found is True
        assert hit.text == "Scoped text"

        # ---- scoped miss: found=False, text="" (still status=ok) ----------
        miss_port = FakeMarionettePort(
            script_outcome=_outcome(ok=True, value={"text": "", "found": False})
        )
        miss = _page_text_uc(miss_port, _repo_with(session)).execute(
            session.session_id, ".missing"
        )
        assert miss.status == "ok"
        assert miss.found is False
        assert miss.text == ""


# ═══════════════════════════════════════════════════════════════════════
# FR-04 [C-SCREENSHOT] — stores PNG via ScreenshotStorePort; width/height;
#                        no-match -> status=error
# ═══════════════════════════════════════════════════════════════════════


class TestScreenshotSelectorUseCaseFR04:
    def test_screenshot_selector_stores_png_with_dimensions(self) -> None:
        session = _session()

        # ---- hit: native capture path -> store -> read_dimensions ---------
        store = FakeScreenshotStore(dimensions=(120, 48))
        port = FakeMarionettePort(screenshot_path="/tmp/aiyes117-el.png")
        result = _screenshot_selector_uc(port, _repo_with(session), store).execute(
            session.session_id, ".hero"
        )

        assert result.status == "ok"
        # save_screenshot called EXACTLY once with the port-returned temp path.
        save_calls = [c for c in store.calls if c[0] == "save_screenshot"]
        assert len(save_calls) == 1
        assert save_calls[0][1] == (session.session_id, "/tmp/aiyes117-el.png")
        # path is the STORED path; width/height derived via the AIYES-111 path.
        assert result.path == "/home/test/.aieyes/s117/screenshot.png"
        assert (result.width, result.height) == (120, 48)
        assert port.screenshot_element_calls == 1

        # ---- no-match: the port yields None -> status=error, reason set ----
        miss_store = FakeScreenshotStore(dimensions=(120, 48))
        miss_port = FakeMarionettePort(screenshot_path=None)
        miss = _screenshot_selector_uc(
            miss_port, _repo_with(session), miss_store
        ).execute(session.session_id, ".gone")
        assert miss.status == "error"
        assert (miss.reason or "") != ""
        # No store write on the no-element path.
        assert not [c for c in miss_store.calls if c[0] == "save_screenshot"]


# ═══════════════════════════════════════════════════════════════════════
# FR-06 [C-GUARD] — backend/marionette guards: status=error + ZERO port I/O;
#                   unknown session -> RuntimeError
# ═══════════════════════════════════════════════════════════════════════


class TestGuardsFR06:
    def test_guards_non_linux_and_marionette_disabled_no_io(self) -> None:
        # Each entry: (builder(port, repo), call(uc, session_id)).
        def _sc_call(uc: Any, sid: str) -> Any:
            return uc.execute(sid, ".sel")

        builders = [
            (lambda p, r: _eval_uc(p, r), lambda uc, sid: uc.execute(sid, "1")),
            (lambda p, r: _query_dom_uc(p, r), _sc_call),
            (lambda p, r: _page_text_uc(p, r), lambda uc, sid: uc.execute(sid)),
            (
                lambda p, r: _screenshot_selector_uc(p, r, FakeScreenshotStore()),
                _sc_call,
            ),
        ]

        # Two guarded session shapes: android backend, and linux-but-disabled.
        android = _session("a1", backend="android", marionette_port=None)
        disabled = _session("l1", backend="linux", marionette_port=None)

        for build, call in builders:
            for guarded in (android, disabled):
                port = FakeMarionettePort(script_outcome=_outcome(ok=True, value=None))
                uc = build(port, _repo_with(guarded))
                res = call(uc, guarded.session_id)
                assert res.status == "error", (
                    f"guarded path did not return status=error: {res!r}"
                )
                assert (res.reason or "") != ""
                # ZERO Marionette I/O on any guarded path.
                assert port.execute_script_calls == 0
                assert port.screenshot_element_calls == 0

            # The marionette-disabled reason must name the fix (operator-actionable).
            port2 = FakeMarionettePort(script_outcome=_outcome(ok=True, value=None))
            uc2 = build(port2, _repo_with(disabled))
            reason = (call(uc2, disabled.session_id).reason or "").lower()
            assert "marionette" in reason

            # Unknown session_id -> RuntimeError (system error, not a status).
            empty_repo = FakeSessionRepository()
            uc3 = build(
                FakeMarionettePort(script_outcome=_outcome(ok=True)), empty_repo
            )
            with pytest.raises(RuntimeError):
                call(uc3, "does-not-exist")


# ═══════════════════════════════════════════════════════════════════════
# FR-09 [C-PARITY] — advertised MCP tools == dispatch handlers for the four
# ═══════════════════════════════════════════════════════════════════════


class TestDomLensParityFR09:
    def test_advertised_equals_dispatch_for_dom_lens_tools(self) -> None:
        from unittest.mock import MagicMock
        import dataclasses as _dc
        from aiyes.adapters.mcp_server import ServerDependencies

        advertised = {ci.tool_name for ci in enumerate_commands(cli)}

        fields = {f.name: MagicMock() for f in _dc.fields(ServerDependencies)}
        dispatchable = set(_build_dispatch_table(ServerDependencies(**fields)).keys())

        # Two-sided parity: every DOM-lens tool is BOTH advertised (a CLI
        # command that schema_gen introspects) AND dispatched (a handler key).
        missing_advertised = _DOM_LENS_TOOLS - advertised
        missing_dispatch = _DOM_LENS_TOOLS - dispatchable
        assert not missing_advertised, (
            f"DOM-lens tools not advertised: {sorted(missing_advertised)}"
        )
        assert not missing_dispatch, (
            f"DOM-lens tools not dispatched: {sorted(missing_dispatch)}"
        )
        assert (advertised & _DOM_LENS_TOOLS) == (dispatchable & _DOM_LENS_TOOLS)
