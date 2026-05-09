"""SubprocessAdapter — implements ProcessPort via subprocess.Popen."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
from typing import Dict, List, Optional


class SubprocessAdapter:
    """Generic process management via subprocess."""

    def __init__(self) -> None:
        self._processes: Dict[int, subprocess.Popen] = {}

    def start(
        self,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
    ) -> int:
        """Start a process, return its PID."""
        process = subprocess.Popen(
            [command] + args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self._processes[process.pid] = process
        return process.pid

    def stop(self, pid: int) -> None:
        """Stop a process by PID.

        For tracked processes: sends SIGTERM, waits up to 3 seconds,
        then escalates to SIGKILL if the process refuses to exit.
        For untracked PIDs: verifies PID ownership via /proc before
        sending SIGTERM (S-02 security fix).
        """
        process = self._processes.pop(pid, None)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            return
        self._verify_pid_ownership(pid)
        os.kill(pid, signal.SIGTERM)

    def _verify_pid_ownership(self, pid: int) -> None:
        """Check that PID belongs to current user before sending signals.

        Reads /proc/{pid}/status to extract the process UID and compares
        it to the current user's UID. Raises PermissionError if the PID
        belongs to a different user, or RuntimeError if /proc is not
        available (non-Linux).

        S-02 security fix: prevents sending signals to arbitrary PIDs.
        """
        status_path = f"/proc/{pid}/status"
        try:
            with open(status_path) as f:
                status_text = f.read()
        except FileNotFoundError:
            raise RuntimeError(
                f"Cannot verify PID {pid} ownership: "
                f"/proc/{pid}/status not found "
                "(process may not exist or /proc is unavailable)"
            )
        except PermissionError:
            raise PermissionError(f"Cannot read /proc/{pid}/status: permission denied")

        # Parse UID line: "Uid:\treal\teffective\tsaved\tfs"
        my_uid = os.getuid()
        for line in status_text.splitlines():
            if line.startswith("Uid:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        proc_uid = int(parts[1])
                    except ValueError:
                        raise RuntimeError(f"Cannot parse UID from /proc/{pid}/status")
                    if proc_uid != my_uid:
                        raise PermissionError(
                            f"PID {pid} belongs to UID {proc_uid}, "
                            f"not current user UID {my_uid}"
                        )
                    return
        raise RuntimeError(f"No Uid line found in /proc/{pid}/status")

    def is_running(self, pid: int) -> bool:
        """Check if a process is still running."""
        process = self._processes.get(pid)
        if process is not None:
            return process.poll() is None
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return not self._is_zombie(pid)

    def _is_zombie(self, pid: int) -> bool:
        """Return True when the process exists but is already a zombie."""
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            stat_fields = stat_path.read_text().split()
        except OSError:
            return False

        if len(stat_fields) < 3:
            return False
        return stat_fields[2] == "Z"
