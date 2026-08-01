"""XvfbAdapter — implements DisplayServerPort via subprocess."""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Dict


class XvfbAdapter:
    """Starts and stops Xvfb virtual display server."""

    def __init__(self) -> None:
        self._processes: Dict[int, subprocess.Popen] = {}

    def start(self, display_num: int, resolution: str, color_depth: int) -> int:
        """Start Xvfb on the given display number. Returns PID."""
        cmd = [
            "Xvfb",
            f":{display_num}",
            "-screen",
            "0",
            f"{resolution}x{color_depth}",
            "+extension",
            "RANDR",
            "+extension",
            "XKEYBOARD",
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        returncode = process.poll()
        if isinstance(returncode, int):
            raise RuntimeError(
                f"Xvfb failed to start on display :{display_num}: "
                f"it exited immediately with code {returncode}. "
                "WSLg is incompatible with the Unix socket required by "
                "isolated Xvfb displays."
            )
        self._processes[process.pid] = process
        return process.pid

    def stop(self, pid: int) -> None:
        """Terminate Xvfb by PID. Escalates to SIGKILL on timeout."""
        process = self._processes.pop(pid, None)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            return
        os.kill(pid, signal.SIGTERM)

    def configure_keyboard(self, display: str) -> None:
        """Configure XKB keyboard layout. Best-effort, never fatal."""
        env = dict(os.environ)
        env["DISPLAY"] = display
        try:
            subprocess.run(
                ["setxkbmap", "us"],
                env=env,
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass  # Best-effort — setxkbmap missing or failing is non-fatal

    def resize(self, display: str, resolution: str) -> None:
        """Resize the display framebuffer via xrandr."""
        cmd = ["xrandr", "--fb", resolution]
        env = dict(os.environ)
        env["DISPLAY"] = display
        try:
            subprocess.run(
                cmd,
                check=True,
                env=env,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise RuntimeError(
                f"xrandr resize failed for display {display}: {stderr}"
            ) from exc
