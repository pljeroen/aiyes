"""Port for diagnostic-event emission (AIYES-103..107 observability).

A runtime_checkable Protocol with exactly two members: emit a diagnostic
event and read back the observable count of emission attempts that failed.
The port is the only diagnostic-sink surface domain/application code may
reference; concrete sinks live in adapters.

Stdlib only — domain purity (FC-ARCH-01).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from aiyes.domain.diagnostic_event import DiagnosticEvent


@runtime_checkable
class DiagnosticEventPort(Protocol):
    """Emit diagnostic events and expose the observable emission-failure count.

    Fail-open / self-counting invariant (FC-OBS-02, R-02):

    The OBSERVABLE emission-failure count is OWNED BY THE SINK, not by any
    caller. ``emit_event`` is FAIL-OPEN — it MUST NOT raise to the caller under
    any internal storage/serialization error — and it MUST SELF-COUNT: when it
    swallows such an internal failure it increments its own counter, exposed via
    ``emission_failure_count()``. Callers therefore do not (and must not) try to
    maintain the count themselves; an executor that wraps ``emit_event`` in a
    defensive try/except does so only for defense-in-depth, never as the source
    of truth for the count. A conforming sink whose ``emit_event`` raised
    without self-counting would violate this port contract.
    """

    def emit_event(self, event: DiagnosticEvent) -> None:
        """Emit one diagnostic event.

        FAIL-OPEN: never raises to the caller. On an internal failure the sink
        SWALLOWS the error and SELF-INCREMENTS its emission-failure counter
        (the count is adapter-owned, see the class invariant above).
        """

    def emission_failure_count(self) -> int:
        """Return the sink-owned count of emission attempts that failed.

        Each swallowed internal ``emit_event`` failure increments this count by
        exactly one. This is the only observable surface for fail-open losses.
        """
