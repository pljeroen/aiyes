"""AIYES-121 — malformed-``command`` input hardening (RED reproduction + guards).

CT-03 input hardening. Today a malformed MCP ``command`` (any non-``str`` element
inside the list, or a non-list/``str`` scalar) reaches
``parse_android_package_identity`` and produces a RAW ``AttributeError``
(``'int' object has no attribute 'startswith'``) instead of a clean, actionable
``ValueError`` that names the offending field/element/type. The fix is
defense-in-depth (VALIDATED_INTENT_PKG.yaml fix_layer_decision):

  * Layer (a) — MCP adapter boundary (``mcp_server._handle_session_start``):
      rejects the malformed ``command`` at the trust boundary, BEFORE any
      ``process.start`` launch. Uniquely covers the non-list/``str`` SCALAR shape
      (for ``backend="linux"`` parse is never reached; for ``backend="android"``
      ``process.start`` runs before parse) and fails fast so no wasted adb app is
      launched.
  * Layer (b) — domain (``parse_android_package_identity`` + ``android_package_name``):
      the root-cause type guard that makes the domain function correct for EVERY
      caller — the direct use-case call (the pinned AIYES-120 test), the 6
      downstream ``android_package_name`` call sites, and any future caller.

Strata (mirrors the AIYES-118/119/120 house pattern):

  RED (fail against current code; GREEN after A9 adds the two guards):
    * Layer (a): a non-``str`` list element and a non-list/``str`` scalar
      ``command`` must surface as ``status=error`` with a message naming
      ``command`` + the offending value + its Python type, and ``process.start``
      must NEVER be called (fail-fast, no wasted launch).
    * Layer (b): ``parse_android_package_identity`` / ``android_package_name``
      must raise ``ValueError`` (NOT ``AttributeError``) on any non-``str``
      candidate, exercising each helper path
      (``_split_android_component`` / ``_looks_like_android_package`` /
      ``_looks_like_android_component``).

  EQUIVALENCE GUARDS (GREEN now AND after — pin the success path so any drift on
  valid input fails):
    * Layer (a): a valid all-``str`` list command still launches a session; a
      bare ``str`` scalar command (``{"command": "am"}``) stays accepted (EQUIV-6).
    * Layer (b): valid all-``str`` candidates return byte-identical
      ``(package, activity)`` results, pinned to today's exact outputs.

Fault injection: NONE is needed — a non-``str`` element/scalar is a real,
externally-reachable input, so the PRODUCTION code genuinely raises. Layer-(a)
tests wire a REAL ``SessionStartUseCase`` with the shared conftest fakes (LOCAL
helper, ZERO edit to conftest) so ``process.start`` call-absence is observable.

Traceability (VALIDATED_INTENT_PKG.yaml required_test_set):
  MCP-BOUNDARY-non-str-element        -> test_layer_a_non_str_element_* (int + types)
  MCP-BOUNDARY-non-list-command-scalar-> test_layer_a_scalar_command_* (linux + android + types)
  DOMAIN-parse-identity-non-str-valueerror -> test_layer_b_parse_* + android_package_name
  EQUIV-valid-input-unchanged         -> test_equiv_b_parse_valid_inputs_unchanged
  EQUIV-android-package-name-unchanged-> test_equiv_b_android_package_name_*
  ACCEPT-str-scalar-command           -> test_layer_a_str_scalar_command_accepted
"""

from __future__ import annotations

import dataclasses
import types
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from aiyes.adapters.mcp_server import ServerDependencies, create_mcp_server
from aiyes.domain.session import (
    android_package_name,
    parse_android_package_identity,
)
from aiyes.domain.use_cases.session_start import SessionStartUseCase

from tests.conftest import (
    FakeAccessibilityBus,
    FakeClock,
    FakeDisplayAllocator,
    FakeDisplayServer,
    FakeProcess,
    FakeSessionRepository,
)

_APP_PID = 77021  # the process the fake returns from start()
_DEVICE_SERIAL = "emulator-5554"


# ═══════════════════════════════════════════════════════════════════════
# Builders (LOCAL — zero edit to the shared conftest, per AIYES-118/119/120)
# ═══════════════════════════════════════════════════════════════════════


def _wire_uc(process: FakeProcess) -> SessionStartUseCase:
    """Build a REAL ``SessionStartUseCase`` over the shared conftest fakes.

    The same constructor serves both backends; ``_execute_linux`` uses the
    display/allocator/atspi ports while ``_execute_android`` ignores them. Wiring
    a real use case (rather than a MagicMock) makes ``process.start`` call-absence
    the falsifiable observable for the layer-(a) fail-fast requirement.
    """
    return SessionStartUseCase(
        display_server=FakeDisplayServer(),
        allocator=FakeDisplayAllocator(),
        atspi_bus=FakeAccessibilityBus(),
        process=process,
        session_repo=FakeSessionRepository(),
        clock=FakeClock(),
    )


def _make_deps(**overrides: Any) -> ServerDependencies:
    """ServerDependencies with all fields MagicMock, plus the given overrides.

    Mirrors ``tests/test_aiyes23_mcp_server.py``'s ``_make_mock_deps`` locally so
    this module stays self-contained. ``clock.now`` returns a float so the
    op-log finally does not blow up (it is best-effort anyway).
    """
    fields = {f.name: MagicMock() for f in dataclasses.fields(ServerDependencies)}
    clock = MagicMock()
    clock.now.return_value = 1000.0
    fields["clock"] = clock
    fields["operation_log"] = MagicMock()
    fields.update(overrides)
    return ServerDependencies(**fields)


def _text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


def _start_calls(process: FakeProcess) -> List[Any]:
    return [c for c in process.calls if c[0] == "start"]


class _RecordingLog:
    """LOCAL operation-log fake: captures every appended ``OperationRecord`` so the
    PERSISTED ``error`` text is directly inspectable (no MagicMock ``call_args``
    archaeology). ``call_tool_handler`` only ever calls ``.append(record)`` on the
    operation log, so a single method is a faithful stand-in — and it lets the
    FIND-121-A10-01 regression assert the secret never reaches the op-log sink.
    """

    def __init__(self) -> None:
        self.records: List[Any] = []

    def append(self, record: Any) -> None:
        self.records.append(record)


def _oplog_error(op_log: _RecordingLog) -> str:
    """Concatenated ``OperationRecord.error`` of every persisted record."""
    return "\n".join(str(getattr(r, "error", "") or "") for r in op_log.records)


def _presenter_patch() -> Any:
    """Patch the presenter so success dispatches return a fixed string.

    Isolates layer-(a) tests from presenter serialization: on the success path it
    yields ``isError=False`` deterministically (mirrors the existing house test
    ``test_session_start_dispatches_with_array_command``); on the malformed path
    the use case raises before the presenter is ever reached.
    """
    return patch(
        "aiyes.cli.presenter.format_session_start",
        return_value='{"session_id": "aiyes121"}',
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER (a) — MCP boundary: non-str ELEMENT in a list command  (RED)
#   Shape 1 of the malformed-input taxonomy. Must reject at the boundary with a
#   clean ValueError naming command + element + type, BEFORE process.start.
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_layer_a_non_str_element_rejected_before_launch() -> None:
    """MCP-BOUNDARY-non-str-element: ``{"command": ["am", "start", 42]}`` (android)
    surfaces as ``status=error`` with a clean ValueError naming ``command`` + the
    offending element ``42`` + type ``int`` — NOT the raw AttributeError text —
    and ``process.start`` is NEVER called (fail-fast, no wasted adb launch).

    RED today: no boundary guard exists, so the int survives to
    ``parse_android_package_identity`` which raises AttributeError only AFTER
    ``process.start`` launched the app (start-then-stop, a wasted launch).
    """
    process = FakeProcess(pid=_APP_PID)
    deps = _make_deps(session_start_uc=_wire_uc(process))
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {
                "command": ["am", "start", 42],
                "backend": "android",
                "device_serial": _DEVICE_SERIAL,
            },
        )

    text = _text(result)
    assert result.isError is True
    # Clean, actionable message: names the field, the value, and the type.
    assert "command" in text
    assert "42" in text
    assert "int" in text
    # The raw AttributeError implementation-leak must be gone.
    assert "has no attribute" not in text
    # Fail-fast: no wasted adb launch at the boundary.
    assert _start_calls(process) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_element",
    [None, True, 3.14, {"k": "v"}, ["nested"]],
    ids=["none", "bool", "float", "dict", "nested_list"],
)
async def test_layer_a_non_str_element_types_rejected_before_launch(
    bad_element: Any,
) -> None:
    """MCP-BOUNDARY-non-str-element (taxonomy shape_1 breadth): every non-``str``
    element type — None/bool/float/dict/nested-list — is rejected at the boundary
    with ``status=error`` and NO ``process.start`` launch. (``isinstance(True, str)``
    is False, so JSON ``true`` is caught despite bool subclassing int.)

    RED today: each element survives to parse and raises AttributeError only after
    ``process.start`` launched the app.
    """
    process = FakeProcess(pid=_APP_PID)
    deps = _make_deps(session_start_uc=_wire_uc(process))
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {
                "command": ["am", bad_element],
                "backend": "android",
                "device_serial": _DEVICE_SERIAL,
            },
        )

    assert result.isError is True
    assert "has no attribute" not in _text(result)
    assert _start_calls(process) == []


# ═══════════════════════════════════════════════════════════════════════
# LAYER (a) — MCP boundary: non-list/str SCALAR command  (RED)
#   Shape 2 of the taxonomy — UNIQUELY covered by layer (a): for linux parse is
#   never reached; for android start runs before parse.
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_layer_a_scalar_command_linux_rejected_before_launch() -> None:
    """MCP-BOUNDARY-non-list-command-scalar (linux default): ``{"command": 42}``
    surfaces as ``status=error`` with a clean ValueError naming ``command`` + the
    scalar value + type, and ``process.start`` is NEVER called.

    RED today: the linux path never calls ``parse_android_package_identity``, so
    the int ``app_command`` flows straight to ``process.start(42, ...)`` and a
    bogus session is created (or the presenter chokes) — no clean rejection.
    """
    process = FakeProcess(pid=_APP_PID)
    deps = _make_deps(session_start_uc=_wire_uc(process))
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool("session_start", {"command": 42})

    text = _text(result)
    assert result.isError is True
    assert "command" in text
    assert "42" in text
    assert "int" in text
    assert "has no attribute" not in text
    assert _start_calls(process) == []


@pytest.mark.asyncio
async def test_layer_a_scalar_command_android_rejected_before_launch() -> None:
    """MCP-BOUNDARY-non-list-command-scalar (android): ``{"command": 42}`` with
    ``backend="android"`` must reject at the boundary — NO wasted ``process.start``.

    RED today: ``process.start(42, [], env)`` runs (session_start.py:190) BEFORE
    parse (:203), so the app is launched then torn down — a wasted launch and a
    raw AttributeError.
    """
    process = FakeProcess(pid=_APP_PID)
    deps = _make_deps(session_start_uc=_wire_uc(process))
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {"command": 42, "backend": "android", "device_serial": _DEVICE_SERIAL},
        )

    assert result.isError is True
    assert "has no attribute" not in _text(result)
    assert _start_calls(process) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_scalar",
    [True, 3.14, {"k": "v"}],
    ids=["bool", "float", "dict"],
)
async def test_layer_a_scalar_command_types_linux_rejected(bad_scalar: Any) -> None:
    """MCP-BOUNDARY-non-list-command-scalar (taxonomy shape_2 breadth): a
    bool/float/dict scalar ``command`` (default linux) is rejected at the boundary
    with ``status=error`` and NO ``process.start``.

    RED today: each non-``str`` scalar becomes ``app_command`` and reaches
    ``process.start`` on the linux path.
    """
    process = FakeProcess(pid=_APP_PID)
    deps = _make_deps(session_start_uc=_wire_uc(process))
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool("session_start", {"command": bad_scalar})

    assert result.isError is True
    assert _start_calls(process) == []


# ═══════════════════════════════════════════════════════════════════════
# LAYER (a) — EQUIVALENCE GUARDS  (GREEN now AND after)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_layer_a_valid_list_command_still_starts() -> None:
    """EQUIV-5 (GREEN): a valid all-``str`` list command dispatches identically —
    ``process.start`` is called with the translated app_command/app_args and the
    result is NOT an error. The boundary guard must be a pure pass-through here.
    """
    process = FakeProcess(pid=_APP_PID)
    deps = _make_deps(session_start_uc=_wire_uc(process))
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {"command": ["firefox", "--no-remote", "https://example.com"]},
        )

    assert result.isError is False
    start = _start_calls(process)
    assert len(start) == 1
    # ("start", (command, args, env)) — the translation is unchanged.
    assert start[0][1][0] == "firefox"
    assert start[0][1][1] == ["--no-remote", "https://example.com"]


@pytest.mark.asyncio
async def test_layer_a_str_scalar_command_accepted() -> None:
    """EQUIV-6 / ACCEPT-str-scalar-command (GREEN): a bare ``str`` scalar command
    ``{"command": "am"}`` stays VALID — ``app_command="am"``, ``app_args=[]`` — the
    guard rejects only NON-``str`` scalars, never a ``str`` scalar.
    """
    process = FakeProcess(pid=_APP_PID)
    deps = _make_deps(session_start_uc=_wire_uc(process))
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool("session_start", {"command": "am"})

    assert result.isError is False
    start = _start_calls(process)
    assert len(start) == 1
    assert start[0][1][0] == "am"
    assert start[0][1][1] == []


# ═══════════════════════════════════════════════════════════════════════
# FIND-121-A10-01 (Rule 22, secret sanitization) — SECRET REDACTION  (RED)
#   The rev-0 error text ``repr()``s the ENTIRE offending element, so a container
#   element carrying credentials echoes the literal secret into BOTH the MCP
#   CallToolResult error text AND the persisted OperationRecord.error (op-log sink).
#   The remediation: name the offending element's TYPE + index, but for any
#   container/object (dict/list/tuple/set/bytes/custom) use a bounded,
#   NON-recursive placeholder — do NOT dump the container's contents. Safe scalars
#   (int/float/bool/None) may still show their value (pinned GREEN above/below).
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_layer_a_secret_dict_element_redacted_in_response_and_oplog() -> None:
    """FIND-121-A10-01: ``{"command": ["am", {"token": "SECRET123"}]}`` must reject
    at the boundary WITHOUT echoing the secret-bearing dict's contents. The message
    still names the offending TYPE (``dict``) + index, but the planted secret
    ``SECRET123`` appears in NEITHER the CallToolResult text NOR the persisted
    ``OperationRecord.error`` (op-log sink). ``process.start`` is never called.

    RED today: the layer-(a) guard formats the element with ``repr()``, so
    ``{'token': 'SECRET123'}`` — including the literal secret — is rendered into the
    error text and stored in ``OperationRecord.error`` via ``error_msg``.
    """
    process = FakeProcess(pid=_APP_PID)
    op_log = _RecordingLog()
    deps = _make_deps(session_start_uc=_wire_uc(process), operation_log=op_log)
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {
                "command": ["am", {"token": "SECRET123"}],
                "backend": "android",
                "device_serial": _DEVICE_SERIAL,
            },
        )

    text = _text(result)
    assert result.isError is True
    # The container contents (the planted secret) must NOT leak to the response...
    assert "SECRET123" not in text
    # ...NOR to the persisted operation-log sink.
    assert "SECRET123" not in _oplog_error(op_log)
    # The message stays actionable: it still names the offending type + index.
    assert "dict" in text
    assert "index" in text
    assert "1" in text
    # And the raw AttributeError implementation-leak must remain absent.
    assert "has no attribute" not in text
    # Fail-fast: no wasted adb launch.
    assert _start_calls(process) == []


@pytest.mark.asyncio
async def test_layer_a_secret_list_element_redacted_in_response_and_oplog() -> None:
    """FIND-121-A10-01 (sibling — LIST element): ``{"command": ["am",
    ["nested", "SECRET456"]]}`` must reject without echoing the nested list's
    contents. Names the TYPE (``list``) + index; ``SECRET456`` leaks to neither the
    response text nor the persisted ``OperationRecord.error``; no launch.

    RED today: ``repr(['nested', 'SECRET456'])`` is dumped into both sinks.
    """
    process = FakeProcess(pid=_APP_PID)
    op_log = _RecordingLog()
    deps = _make_deps(session_start_uc=_wire_uc(process), operation_log=op_log)
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {
                "command": ["am", ["nested", "SECRET456"]],
                "backend": "android",
                "device_serial": _DEVICE_SERIAL,
            },
        )

    text = _text(result)
    assert result.isError is True
    assert "SECRET456" not in text
    assert "SECRET456" not in _oplog_error(op_log)
    assert "list" in text
    assert "index" in text
    assert "1" in text
    assert "has no attribute" not in text
    assert _start_calls(process) == []


# ═══════════════════════════════════════════════════════════════════════
# LAYER (b) — domain: parse_android_package_identity non-str -> ValueError (RED)
#   One RED per helper path so the single centralized guard is proven to cover
#   every branch (_split_android_component / _looks_like_android_package /
#   _looks_like_android_component).
# ═══════════════════════════════════════════════════════════════════════


def test_layer_b_parse_dash_n_non_str_raises_valueerror() -> None:
    """DOMAIN-parse-identity-non-str-valueerror (``_split_android_component`` path):
    ``parse_android_package_identity("am", ["-n", 42])`` — the pinned-test shape —
    must raise ``ValueError`` (NOT ``AttributeError``) naming the offending element.

    RED today: the ``-n``/42 pair reaches ``_split_android_component(42).partition``
    -> ``AttributeError``.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("am", ["-n", 42])

    msg = str(excinfo.value)
    assert "command" in msg
    assert "42" in msg
    assert "int" in msg
    assert "has no attribute" not in msg


def test_layer_b_parse_command_itself_non_str_raises_valueerror() -> None:
    """DOMAIN-parse-identity-non-str-valueerror (``_looks_like_android_component``
    path, offender = ``app_command``): ``parse_android_package_identity(42, [])``
    must raise ``ValueError`` (NOT ``AttributeError``).

    RED today: the fallback scan reaches ``_looks_like_android_component(42)`` ->
    ``42.startswith`` -> ``AttributeError``.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity(42, [])  # type: ignore[arg-type]

    msg = str(excinfo.value)
    assert "42" in msg
    assert "int" in msg
    assert "has no attribute" not in msg


def test_layer_b_parse_dash_p_non_str_raises_valueerror() -> None:
    """DOMAIN-parse-identity-non-str-valueerror (``_looks_like_android_package``
    path): ``parse_android_package_identity("-p", [42])`` must raise ``ValueError``.

    RED today: the ``-p`` branch reaches ``_looks_like_android_package(42)`` ->
    ``42.startswith`` -> ``AttributeError``.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("-p", [42])

    msg = str(excinfo.value)
    assert "42" in msg
    assert "int" in msg
    assert "has no attribute" not in msg


def test_layer_b_parse_fallback_component_non_str_raises_valueerror() -> None:
    """DOMAIN-parse-identity-non-str-valueerror (non-str element inside app_args,
    fallback scan): ``parse_android_package_identity("am", ["start", 42])`` must
    raise ``ValueError``.

    RED today: the fallback ``_looks_like_android_component`` scan reaches
    ``42.startswith`` -> ``AttributeError``.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("am", ["start", 42])

    msg = str(excinfo.value)
    assert "42" in msg
    assert "int" in msg
    assert "has no attribute" not in msg


@pytest.mark.parametrize(
    "bad_element",
    [True, None, 3.14, {"k": "v"}, ["nested"]],
    ids=["bool", "none", "float", "dict", "nested_list"],
)
def test_layer_b_parse_non_str_element_types_raise_valueerror(
    bad_element: Any,
) -> None:
    """DOMAIN-parse-identity-non-str-valueerror (taxonomy shape_1 breadth): every
    non-``str`` element type raises ``ValueError`` (NOT ``AttributeError``) naming
    its Python type. ``isinstance(True, str)`` is False -> bool is caught.

    RED today: each raises ``AttributeError`` from a raw str-method call.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("am", ["start", bad_element])

    msg = str(excinfo.value)
    assert type(bad_element).__name__ in msg
    assert "has no attribute" not in msg


def test_layer_b_android_package_name_non_str_app_args_raises_valueerror() -> None:
    """DOMAIN-parse-identity-non-str-valueerror (via ``android_package_name``): a
    session-like object with a non-``str`` ``app_args`` element must surface a clean
    ``ValueError`` (NOT ``AttributeError``) — protecting the 6 downstream callers.

    RED today: ``android_package_name`` forwards to ``parse_android_package_identity``
    which raises ``AttributeError`` on the int element.
    """
    session_like = types.SimpleNamespace(
        package_name="",
        app_command="am",
        app_args=("-n", 42),
    )

    with pytest.raises(ValueError) as excinfo:
        android_package_name(session_like)

    msg = str(excinfo.value)
    assert "42" in msg
    assert "has no attribute" not in msg


# ═══════════════════════════════════════════════════════════════════════
# LAYER (b) — domain: SECRET REDACTION  (RED — FIND-121-A10-01)
#   The domain ValueError message ``repr()``s the offending element too, so it
#   leaks the same way as the adapter for the 7 domain callers. The message must
#   name the container's TYPE but NOT its contents.
# ═══════════════════════════════════════════════════════════════════════


def test_layer_b_parse_secret_dict_element_redacted() -> None:
    """FIND-121-A10-01 (domain): ``parse_android_package_identity("am",
    [{"password": "hunter2"}])`` raises ``ValueError`` whose message names the
    offending TYPE (``dict``) but does NOT contain the planted secret ``hunter2``.

    RED today: the guard formats the element with ``repr()``, so
    ``{'password': 'hunter2'}`` — secret included — is rendered into the message.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("am", [{"password": "hunter2"}])

    msg = str(excinfo.value)
    assert "hunter2" not in msg
    assert "dict" in msg
    assert "index" in msg
    assert "has no attribute" not in msg


def test_layer_b_parse_secret_nested_list_element_redacted() -> None:
    """FIND-121-A10-01 (domain, nested list): ``parse_android_package_identity("am",
    [["nested", "leak-XYZ"]])`` raises ``ValueError`` naming the TYPE (``list``) but
    NOT the nested list's contents (``leak-XYZ``).

    RED today: ``repr(['nested', 'leak-XYZ'])`` is dumped into the message.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("am", [["nested", "leak-XYZ"]])

    msg = str(excinfo.value)
    assert "leak-XYZ" not in msg
    assert "list" in msg
    assert "index" in msg
    assert "has no attribute" not in msg


# ═══════════════════════════════════════════════════════════════════════
# LAYER (b) — EQUIVALENCE GUARDS  (GREEN now AND after)
#   Pin today's EXACT (package, activity) outputs so any drift on valid input
#   fails. Values verified against the current implementation.
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "app_command, app_args, expected",
    [
        (
            "am",
            ["start", "-n", "com.example.app/.MainActivity"],
            ("com.example.app", ".MainActivity"),
        ),
        (
            "com.example.app/.MainActivity",
            [],
            ("com.example.app", ".MainActivity"),
        ),
        ("am", ["-p", "com.example.app"], ("com.example.app", "")),
        ("bash", [], ("", "")),
    ],
    ids=["dash_n_component", "bare_component", "dash_p_package", "non_android"],
)
def test_equiv_b_parse_valid_inputs_unchanged(
    app_command: str, app_args: List[str], expected: Any
) -> None:
    """EQUIV-1 (GREEN): valid all-``str`` candidates return byte-identical
    ``(package, activity)`` results — pinned to today's exact outputs."""
    assert parse_android_package_identity(app_command, app_args) == expected


def test_equiv_b_android_package_name_derives() -> None:
    """EQUIV-2 (GREEN): ``android_package_name`` on a valid session-like object with
    all-``str`` ``app_args`` derives the identical package name as today."""
    session_like = types.SimpleNamespace(
        package_name="",
        app_command="am",
        app_args=("-n", "com.example.app/.MainActivity"),
    )
    assert android_package_name(session_like) == "com.example.app"


def test_equiv_b_android_package_name_preset_short_circuits() -> None:
    """EQUIV-2 (GREEN): a preset ``package_name`` short-circuits — no parse, byte
    identical to today."""
    session_like = types.SimpleNamespace(
        package_name="com.preset.pkg",
        app_command="am",
        app_args=("-n", 42),  # would raise if parse were (wrongly) reached
    )
    assert android_package_name(session_like) == "com.preset.pkg"


# ═══════════════════════════════════════════════════════════════════════
# FIND-121-A10-01 (rev-2) — int/float SUBCLASS with a secret-bearing __repr__
#   The rev-1 redaction gates "safe scalar" via ``isinstance(value, (bool, int,
#   float))`` in BOTH helpers (``domain/session.py`` + ``adapters/mcp_server.py``).
#   ``isinstance`` is TRUE for int/float SUBCLASSES, so a crafted subclass whose
#   ``__repr__`` returns a secret is (wrongly) treated as a safe scalar and its
#   ``repr()`` — the secret — is rendered verbatim into the domain ``ValueError``,
#   the MCP ``CallToolResult`` text, AND the persisted ``OperationRecord.error``.
#   Remediation being pinned (A10 rev-1 recommendation): gate on the EXACT built-in
#   type — ``value is None or type(value) in (bool, int, float)`` — so every
#   subclass collapses to the bounded, non-recursive ``<typename>`` placeholder.
#   These MUST be RED against the current isinstance gate; the exact built-in
#   scalar cases (``42``/``True``/``None``/plain ``float``) stay GREEN above.
# ═══════════════════════════════════════════════════════════════════════

_SUBCLASS_SECRET = "SECRET_SUBCLASS_9999"


class _EvilInt(int):
    """An ``int`` SUBCLASS whose ``repr`` carries a secret — the FIND-121-A10-01
    rev-2 attack. ``isinstance(x, int)`` is True, so an isinstance-based safe-scalar
    gate admits it and dumps the secret via ``repr``; only an EXACT-type gate
    (``type(x) in (bool, int, float)``) redacts it to a ``<typename>`` placeholder."""

    def __repr__(self) -> str:
        return _SUBCLASS_SECRET


class _EvilFloat(float):
    """A ``float`` SUBCLASS mirror of ``_EvilInt`` — same secret-bearing ``repr``.
    ``isinstance(x, float)`` is True, so it leaks identically under the isinstance
    gate and is redacted only under the exact-type gate."""

    def __repr__(self) -> str:
        return _SUBCLASS_SECRET


def test_layer_b_parse_int_subclass_secret_repr_redacted() -> None:
    """FIND-121-A10-01 (rev-2, domain, int subclass):
    ``parse_android_package_identity("am", [_EvilInt(5)])`` raises ``ValueError``
    whose message NAMES the type (``_EvilInt``) but does NOT contain the
    secret-bearing repr (``SECRET_SUBCLASS_9999``).

    RED today: the safe-scalar gate is ``isinstance(value, (bool, int, float))`` and
    an ``int`` subclass satisfies it, so ``_render_untrusted_scalar`` returns
    ``repr(_EvilInt(5)) == 'SECRET_SUBCLASS_9999'`` — the secret leaks into the
    message. GREEN once the gate is ``type(value) in (bool, int, float)`` (exact
    built-ins only). The type-name clause (``of type _EvilInt``) is present in both
    states, so ``'_EvilInt' in msg`` is the stable actionability assertion and the
    secret-absence assertion is the one that flips.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("am", [_EvilInt(5)])

    msg = str(excinfo.value)
    assert _SUBCLASS_SECRET not in msg
    assert "_EvilInt" in msg
    assert "has no attribute" not in msg


def test_layer_b_parse_float_subclass_secret_repr_redacted() -> None:
    """FIND-121-A10-01 (rev-2, domain, float subclass mirror):
    ``parse_android_package_identity("am", ["start", _EvilFloat(1.5)])`` raises
    ``ValueError`` naming ``_EvilFloat`` but NOT the secret repr.

    RED today: ``isinstance(_EvilFloat(1.5), float)`` is True, so the ``float``
    subclass is admitted as a safe scalar and its secret ``repr`` leaks.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_android_package_identity("am", ["start", _EvilFloat(1.5)])

    msg = str(excinfo.value)
    assert _SUBCLASS_SECRET not in msg
    assert "_EvilFloat" in msg
    assert "has no attribute" not in msg


@pytest.mark.asyncio
async def test_layer_a_int_subclass_secret_repr_redacted_in_response_and_oplog() -> (
    None
):
    """FIND-121-A10-01 (rev-2, adapter + op-log sink, int subclass):
    ``{"command": ["am", "start", _EvilInt(5)], backend: android}`` rejects at the
    boundary WITHOUT echoing the subclass's secret-bearing repr into EITHER the
    ``CallToolResult`` text OR the persisted ``OperationRecord.error``. The message
    still names the type (``_EvilInt``); ``process.start`` is NEVER called.

    Constructing the subclass directly in-test is correct: JSON cannot produce an
    ``int`` subclass, but ``_render_untrusted_scalar`` must be TOTAL over every
    caller, and the boundary guard is the layer that must hold for any Python-object
    ``command`` element (layer (b) guarantees the same for the 7 domain callers).

    RED today: the isinstance gate admits ``_EvilInt`` so ``repr()`` ==
    ``'SECRET_SUBCLASS_9999'`` is rendered into BOTH sinks. GREEN after the
    exact-type gate collapses it to ``<_EvilInt>``.
    """
    process = FakeProcess(pid=_APP_PID)
    op_log = _RecordingLog()
    deps = _make_deps(session_start_uc=_wire_uc(process), operation_log=op_log)
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {
                "command": ["am", "start", _EvilInt(5)],
                "backend": "android",
                "device_serial": _DEVICE_SERIAL,
            },
        )

    text = _text(result)
    assert result.isError is True
    # The subclass's secret repr must NOT leak to the response...
    assert _SUBCLASS_SECRET not in text
    # ...NOR to the persisted operation-log sink.
    assert _SUBCLASS_SECRET not in _oplog_error(op_log)
    # The message stays actionable: it still names the offending type.
    assert "_EvilInt" in text
    assert "has no attribute" not in text
    # Fail-fast: no wasted adb launch.
    assert _start_calls(process) == []


@pytest.mark.asyncio
async def test_layer_a_float_subclass_secret_repr_redacted_in_response_and_oplog() -> (
    None
):
    """FIND-121-A10-01 (rev-2, adapter + op-log sink, float subclass mirror):
    ``{"command": ["am", _EvilFloat(1.5)], backend: android}`` rejects without
    echoing the secret repr into the response text or ``OperationRecord.error``;
    names ``_EvilFloat``; no launch.

    RED today: ``isinstance(_EvilFloat(1.5), float)`` admits it, so its secret
    ``repr`` leaks into both sinks. GREEN after the exact-type gate.
    """
    process = FakeProcess(pid=_APP_PID)
    op_log = _RecordingLog()
    deps = _make_deps(session_start_uc=_wire_uc(process), operation_log=op_log)
    server = create_mcp_server(deps)

    with _presenter_patch():
        result = await server.call_tool(
            "session_start",
            {
                "command": ["am", _EvilFloat(1.5)],
                "backend": "android",
                "device_serial": _DEVICE_SERIAL,
            },
        )

    text = _text(result)
    assert result.isError is True
    assert _SUBCLASS_SECRET not in text
    assert _SUBCLASS_SECRET not in _oplog_error(op_log)
    assert "_EvilFloat" in text
    assert "has no attribute" not in text
    assert _start_calls(process) == []
