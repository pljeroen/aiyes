"""Process port — Protocol for managing OS processes."""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol


class ProcessPort(Protocol):
    """Port for starting and managing processes."""

    def start(
        self,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
    ) -> int:
        """Start a process, return its PID."""
        ...

    def stop(self, pid: int) -> None:
        """Stop a process by PID."""
        ...

    def is_running(self, pid: int) -> bool:
        """Check if a process is still running."""
        ...
