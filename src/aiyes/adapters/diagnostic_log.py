"""In-memory diagnostic-event sink (AIYES-103..107 observability).

A local, bounded, fail-open implementation of DiagnosticEventPort. It stores
emitted events in a bounded list (cap per logging.yaml retention_bound),
enforces adapter-side redaction (the diagnostic_summary is forced to a single
line truncated to SUMMARY_MAX_LEN), and swallows any internal emit error while
incrementing an observable failure counter (logging.yaml
failure_semantics.may_block_caller=false, failure_count_observable=true).
"""

from __future__ import annotations

import dataclasses

from aiyes.domain.diagnostic_event import DiagnosticEvent
from aiyes.ports.diagnostic_event import DiagnosticEventPort

# OD-03: diagnostic_summary is a single line truncated at 200 chars.
SUMMARY_MAX_LEN = 200

# logging.yaml retention_bound.cap.
_RETENTION_CAP = 1000


def _redact_summary(summary: object) -> str:
    """Force a diagnostic summary to a single line truncated to SUMMARY_MAX_LEN.

    Empty / non-string input normalizes to "" (no diagnostic text).
    """
    if not isinstance(summary, str) or not summary:
        return ""
    return summary.splitlines()[0][:SUMMARY_MAX_LEN]


class InMemoryDiagnosticLog:
    """Local, bounded, fail-open DiagnosticEventPort implementation."""

    def __init__(self) -> None:
        self._events: list[DiagnosticEvent] = []
        self._failures: int = 0

    def emit_event(self, event: DiagnosticEvent) -> None:
        """Store an event with adapter-enforced redaction; never raise to caller."""
        try:
            redacted = dataclasses.replace(
                event, diagnostic_summary=_redact_summary(event.diagnostic_summary)
            )
            self._events.append(redacted)
            if len(self._events) > _RETENTION_CAP:
                del self._events[0 : len(self._events) - _RETENTION_CAP]
        except Exception:
            # Fail-open: swallow and count (OD-05).
            self._failures += 1

    def emission_failure_count(self) -> int:
        """Return the number of swallowed emission failures."""
        return self._failures

    @property
    def events(self) -> list[DiagnosticEvent]:
        """Read-back accessor for emitted events (test/inspection surface)."""
        return self._events


# Structural conformance check (import-time, cheap): the class satisfies the port.
_PORT_CHECK: DiagnosticEventPort = InMemoryDiagnosticLog()
