"""AIYES-115: wire the standalone ``scroll`` action to the native a11y path.

The standalone ``scroll`` action on ``AndroidActionAdapter.do_action`` gains a
native accessibility tier BEFORE the existing blind ``input swipe``:

  * native attempted IFF ``set_native_scroll`` was wired AND the target node
    satisfies ``_node_scrollable`` (the pure predicate reused from the executor);
  * on native success -> ``action_method == "native_scroll"``, NO swipe issued;
  * on ANY fallback (not-wired / not-scrollable / native unsuccessful / native
    raises) -> the EXISTING swipe path runs UNCHANGED
    (``["input","swipe",str(cx),str(cy),str(cx),str(cy-300)]``,
    ``action_method == "node_bounds_tap"``), and the fallback reason / exception
    is SURFACED via ``logging.debug`` (not silently swallowed).

Requirement / constraint anchors (VALIDATED_INTENT_PKG + FORMAL_CONSTRAINT_MAP):
  IT-1  R1/C1  native used when wired + scrollable
  IT-2  R4/C7  STRONG swipe-baseline pin for the not-wired case
               (strengthens the WEAK test_aiyes24_android_text.py:428 which
               asserts only success + subcommand=="swipe")
  IT-3  R2/C2  not-scrollable -> swipe fallback, native NOT called
  IT-4  R2/C2  native unsuccessful -> swipe fallback + fallback_reason surfaced
  IT-5  R2/C2  native raises -> swipe fallback, exception not propagated, logged
  C5    reuse: no duplicated ACTION_SCROLL mapping, _node_scrollable imported
  C6    port-typing: set_native_scroll param typed NativeScrollPort (not Any)
  IT-6  RW2   composition root injects the _android_native_scroll singleton into
              the lazily-built AndroidActionAdapter (native-scroll reachability)
  R3           action_method value rides on IT-1 (native_scroll) + IT-2..5
               (node_bounds_tap)
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

from aiyes.adapters.android_action_adapter import AndroidActionAdapter, _bounds_center
from aiyes.domain.tree import AccessibilityTree, Node
from aiyes.domain.types import NativeScrollResult

_ADAPTER_MODULE = "aiyes.adapters.android_action_adapter"
_LOGGER_NAME = "aiyes.adapters.android_action_adapter"


# ═══════════════════════════════════════════════════════════════════════
# Helpers / fakes
# ═══════════════════════════════════════════════════════════════════════


def _make_android_session(**overrides: Any) -> Any:
    """Construct a minimal Android session object for testing."""
    defaults = dict(device_serial="emulator-5554", backend="android")
    defaults.update(overrides)
    return type("S", (), defaults)()


def _scrollable_node() -> Node:
    """A node that satisfies ``_node_scrollable`` (role + state + action).

    bounds (0, 400, 1080, 1520) -> center (540, 1160) -> swipe end (540, 860).
    Mirrors the WebView bounds used by the existing weak scroll test.
    """
    return Node(
        id="n_004",
        role="ScrollView",
        name="Content",
        bounds=(0, 400, 1080, 1520),
        states=("enabled", "scrollable"),
        actions=("scroll",),
    )


def _non_scrollable_node() -> Node:
    """A resolvable node that does NOT satisfy ``_node_scrollable``.

    bounds (100, 300, 400, 80) -> center (300, 340) -> swipe end (300, 40).
    """
    return Node(
        id="n_007",
        role="Button",
        name="OK",
        bounds=(100, 300, 400, 80),
        states=("enabled",),
        actions=("click",),
    )


def _native_success(node_id: str = "n_004") -> NativeScrollResult:
    return NativeScrollResult(
        success=True,
        method="android_accessibility_helper",
        requested_action="ACTION_SCROLL_FORWARD",
        action_id=4096,
        node_id=node_id,
        direction="down",
    )


def _native_failure(
    node_id: str = "n_004", fallback_reason: str = "native_scroll_helper_failed"
) -> NativeScrollResult:
    return NativeScrollResult(
        success=False,
        method="android_accessibility_helper",
        requested_action="ACTION_SCROLL_FORWARD",
        action_id=4096,
        node_id=node_id,
        direction="down",
        fallback_reason=fallback_reason,
    )


class _FakeNativeScroll:
    """Spy NativeScrollPort double: records scroll() calls; returns a preset
    NativeScrollResult or raises a preset exception.

    Its signature is byte-structurally identical to NativeScrollPort.scroll so
    the adapter may call it positionally or by keyword; every call is recorded.
    """

    def __init__(
        self,
        *,
        result: Optional[NativeScrollResult] = None,
        raises: Optional[BaseException] = None,
    ) -> None:
        self.calls: List[dict] = []
        self._result = result
        self._raises = raises

    def scroll(
        self,
        session: object,
        node_id: str,
        direction: str,
        *,
        stable_id: str = "",
        bounds: Optional[tuple] = None,
    ) -> NativeScrollResult:
        self.calls.append(
            {
                "session": session,
                "node_id": node_id,
                "direction": direction,
                "stable_id": stable_id,
                "bounds": bounds,
            }
        )
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def _make_adapter(node: Node, native: Optional[_FakeNativeScroll] = None):
    """AndroidActionAdapter with a mock tree resolving ``node``; optionally
    wire a native-scroll double via the (new) set_native_scroll setter."""
    adapter = AndroidActionAdapter()
    mock_tree = MagicMock()
    mock_tree.get_tree.return_value = AccessibilityTree(roots=(node,))
    adapter.set_tree_adapter(mock_tree)
    if native is not None:
        adapter.set_native_scroll(native)
    return adapter


def _shell_args(cmd: List[str]) -> List[str]:
    """Return args after 'shell' in an adb command list."""
    try:
        idx = cmd.index("shell")
    except ValueError:
        return cmd
    return cmd[idx + 1 :]


def _issued_swipes(mock_run: MagicMock) -> List[List[str]]:
    """Every ['input','swipe',...] arg list issued via mocked subprocess.run."""
    swipes = []
    for call in mock_run.call_args_list:
        args = _shell_args(call[0][0])
        if args[:2] == ["input", "swipe"]:
            swipes.append(args)
    return swipes


# ═══════════════════════════════════════════════════════════════════════
# IT-1 (R1/C1) — native path used when wired + scrollable
# ═══════════════════════════════════════════════════════════════════════


class TestNativeScrollUsedWhenAvailableC1:
    def test_scroll_uses_native_when_available_and_scrollable(self) -> None:
        """Wired native + scrollable node -> native.scroll('down') called once,
        action_method=='native_scroll', and NO adb 'input swipe' is issued."""
        fake = _FakeNativeScroll(result=_native_success("n_004"))
        adapter = _make_adapter(_scrollable_node(), native=fake)
        session = _make_android_session()

        with patch(f"{_ADAPTER_MODULE}.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_004", "scroll")

        # Native tier chosen.
        assert result.success is True
        assert result.action_method == "native_scroll"

        # Native port called EXACTLY once, direction hardcoded "down", node
        # threaded, on the resolved session.
        assert len(fake.calls) == 1
        assert fake.calls[0]["direction"] == "down"
        assert fake.calls[0]["node_id"] == "n_004"
        assert fake.calls[0]["session"] is session

        # C1 falsifier: no blind swipe issued when native succeeds.
        assert _issued_swipes(mock_run) == []


# ═══════════════════════════════════════════════════════════════════════
# IT-2 (R4/C7) — STRONG swipe-baseline pin for the not-wired case
# ═══════════════════════════════════════════════════════════════════════


class TestSwipeBaselinePinC7:
    def test_scroll_falls_back_to_swipe_when_native_not_wired(self) -> None:
        """Default-constructed adapter (set_native_scroll never called) emits the
        EXACT current swipe command AND action_method=='node_bounds_tap'.

        This is the C7 regression anchor: it PINS the exact arg list
        ['input','swipe',str(cx),str(cy),str(cx),str(cy-300)] that the weak
        test_aiyes24_android_text.py::test_scroll_action_unchanged (asserts only
        success + shell_args[0][1]=='swipe') does not lock down.
        """
        node = _scrollable_node()  # center (540, 1160) -> end (540, 860)
        adapter = _make_adapter(node)  # NOT wired: no set_native_scroll
        session = _make_android_session()

        with patch(f"{_ADAPTER_MODULE}.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_004", "scroll")

        assert result.success is True
        assert result.action_method == "node_bounds_tap"

        # Exactly one adb command, and it is the EXACT swipe shape.
        assert mock_run.call_count == 1
        swipes = _issued_swipes(mock_run)
        assert len(swipes) == 1
        assert swipes[0] == ["input", "swipe", "540", "1160", "540", "860"]

        # Cross-check: the pinned literals are the documented center-derived shape.
        cx, cy = _bounds_center(node.bounds)
        assert swipes[0] == [
            "input",
            "swipe",
            str(cx),
            str(cy),
            str(cx),
            str(cy - 300),
        ]


# ═══════════════════════════════════════════════════════════════════════
# IT-3 / IT-4 / IT-5 (R2/C2) — swipe fallback branches
# ═══════════════════════════════════════════════════════════════════════


class TestSwipeFallbackBranchesC2:
    def test_scroll_falls_back_when_node_not_scrollable(self) -> None:
        """IT-3: native wired but node NOT scrollable -> native.scroll is NOT
        called; swipe fallback with action_method=='node_bounds_tap'."""
        fake = _FakeNativeScroll(result=_native_success("n_007"))
        node = _non_scrollable_node()  # center (300, 340) -> end (300, 40)
        adapter = _make_adapter(node, native=fake)
        session = _make_android_session()

        with patch(f"{_ADAPTER_MODULE}.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = adapter.do_action(session, "n_007", "scroll")

        assert result.success is True
        assert result.action_method == "node_bounds_tap"

        # Gated OUT by _node_scrollable: native port never invoked.
        assert fake.calls == []

        swipes = _issued_swipes(mock_run)
        assert len(swipes) == 1
        assert swipes[0] == ["input", "swipe", "300", "340", "300", "40"]

    def test_scroll_falls_back_when_native_unsuccessful(self, caplog) -> None:
        """IT-4: native wired + scrollable but returns success=False ->
        native attempted once, then swipe fallback; fallback_reason surfaced
        via logging.debug (not silently swallowed)."""
        fake = _FakeNativeScroll(
            result=_native_failure("n_004", "native_scroll_helper_failed")
        )
        node = _scrollable_node()
        adapter = _make_adapter(node, native=fake)
        session = _make_android_session()

        with patch(f"{_ADAPTER_MODULE}.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
                result = adapter.do_action(session, "n_004", "scroll")

        assert result.success is True
        assert result.action_method == "node_bounds_tap"

        # Native attempted exactly once, then fell through to swipe.
        assert len(fake.calls) == 1
        swipes = _issued_swipes(mock_run)
        assert len(swipes) == 1
        assert swipes[0] == ["input", "swipe", "540", "1160", "540", "860"]

        # C2 falsifier: the fallback_reason MUST be observable at DEBUG.
        assert "native_scroll_helper_failed" in caplog.text

    def test_scroll_falls_back_when_native_raises(self, caplog) -> None:
        """IT-5: native.scroll raises -> exception is caught (does NOT propagate),
        swipe fallback runs, action_method=='node_bounds_tap', and the exception
        is surfaced via logging.debug."""
        boom = RuntimeError("native-scroll-boom")
        fake = _FakeNativeScroll(raises=boom)
        node = _scrollable_node()
        adapter = _make_adapter(node, native=fake)
        session = _make_android_session()

        with patch(f"{_ADAPTER_MODULE}.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
                # Must NOT raise — the adapter swallows and falls back.
                result = adapter.do_action(session, "n_004", "scroll")

        assert result.success is True
        assert result.action_method == "node_bounds_tap"

        assert len(fake.calls) == 1
        swipes = _issued_swipes(mock_run)
        assert len(swipes) == 1
        assert swipes[0] == ["input", "swipe", "540", "1160", "540", "860"]

        # C2 falsifier: the exception MUST be observable at DEBUG, not discarded.
        assert "native-scroll-boom" in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# C5 — reuse, no re-implementation (structural)
# ═══════════════════════════════════════════════════════════════════════


class TestReuseNoReimplementationC5:
    def test_action_scroll_mapping_not_duplicated_and_predicate_reused(self) -> None:
        """C5: the ACTION_SCROLL mapping stays SOLELY in
        android_native_scroll_adapter.py, and _node_scrollable is REUSED
        (imported from the executor), not re-defined in the action adapter."""
        import inspect

        import aiyes.adapters.android_action_adapter as action_mod
        from aiyes.adapters import scenario_use_case_executor as exec_mod

        src = inspect.getsource(action_mod)

        # No duplicated native ACTION_SCROLL mapping / magic action-ids here.
        assert "ACTION_SCROLL_FORWARD" not in src
        assert "ACTION_SCROLL_BACKWARD" not in src
        assert "_ACTION_BY_DIRECTION" not in src
        assert "4096" not in src
        assert "8192" not in src

        # No second native-scroll adapter class introduced.
        assert "class AndroidNativeScrollAdapter" not in src

        # Scrollability predicate is imported (same object), not re-implemented.
        assert "def _node_scrollable" not in src
        assert (
            getattr(action_mod, "_node_scrollable", None) is exec_mod._node_scrollable
        )


# ═══════════════════════════════════════════════════════════════════════
# C6 — clean composition port-typing
# ═══════════════════════════════════════════════════════════════════════


class TestPortTypingC6:
    def test_set_native_scroll_param_typed_native_scroll_port(self) -> None:
        """C6: the set_native_scroll setter's parameter is annotated with the
        existing NativeScrollPort (imported from aiyes.ports.native_scroll),
        NOT bare Any."""
        import typing

        from aiyes.ports.native_scroll import NativeScrollPort

        hints = typing.get_type_hints(AndroidActionAdapter.set_native_scroll)
        assert "native_scroll" in hints
        assert hints["native_scroll"] is NativeScrollPort
        assert hints["native_scroll"] is not typing.Any


# ═══════════════════════════════════════════════════════════════════════
# IT-6 (RW2) — composition-root wires set_native_scroll (reachability, Rule 41)
# ═══════════════════════════════════════════════════════════════════════


class TestCompositionWiringIT6:
    """IT-6 (bound_design.composition_wiring_RW2): the PRODUCTION composition
    root MUST inject the composed ``_android_native_scroll`` singleton into the
    lazily-built ``AndroidActionAdapter`` — anchoring native-scroll REACHABILITY
    (Rule 41). Without this row the feature could ship wired inert: IT-1 injects
    a fake directly via ``set_native_scroll``, so IT-1 stays GREEN even if
    composition never calls ``set_native_scroll`` in production.

    This drives the SAME lazy-build path the CLI/MCP use
    (``get_adapters_for_backend('android')`` -> ``_android.action`` ->
    ``_LazyAndroidAdapters._ensure()``) and asserts the built action adapter's
    ``_native_scroll`` IS the composed ``_android_native_scroll`` singleton.

    RED now: ``_ensure()`` builds the adapter and calls ``set_tree_adapter`` but
    does NOT yet call ``set_native_scroll`` (one missing line, mirroring the
    existing tree wiring), so ``_native_scroll`` is absent/None.
    """

    def test_composition_injects_native_scroll_singleton_into_action_adapter(
        self,
    ) -> None:
        from aiyes.cli import composition_root
        from aiyes.adapters.android_action_adapter import AndroidActionAdapter
        from aiyes.adapters.android_native_scroll_adapter import (
            AndroidNativeScrollAdapter,
        )

        # Drive the production composition / lazy-build path (identical to what
        # the CLI/MCP trigger when a session is backend=="android").
        action = composition_root.get_adapters_for_backend("android")["action"]
        assert isinstance(action, AndroidActionAdapter)

        # It is the SAME singleton the dispatching action routes to at call time.
        assert action is composition_root._dispatching_action._android.action

        native = getattr(action, "_native_scroll", None)

        # RED now: _ensure() never calls set_native_scroll -> native is None.
        assert native is not None, (
            "composition_root._LazyAndroidAdapters._ensure() must inject the "
            "_android_native_scroll singleton into the AndroidActionAdapter "
            "(one new line mirroring the existing set_tree_adapter wiring) — "
            "otherwise the native-scroll tier is unreachable in production"
        )

        # Reachability pin: it is the ONE composed AndroidNativeScrollAdapter
        # singleton (identity to line-154 instance), not an ad-hoc/fake double.
        assert native is composition_root._android_native_scroll
        assert isinstance(native, AndroidNativeScrollAdapter)
