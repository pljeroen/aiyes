"""Operation log port — Protocol for append-only operation logging."""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from aiyes.domain.operation_record import OperationRecord


@runtime_checkable
class OperationLogPort(Protocol):
    """Port for append-only operation logging."""

    def append(self, record: OperationRecord) -> None:
        """Append a single operation record."""
        ...

    def read(self, session_id: str) -> List[OperationRecord]:
        """Read all records for a session."""
        ...

    def read_all(self) -> List[OperationRecord]:
        """Read all records across all sessions."""
        ...

    def list_session_ids(self) -> List[str]:
        """Return directory names of sessions with operation logs."""
        ...
