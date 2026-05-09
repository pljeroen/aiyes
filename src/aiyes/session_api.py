"""Thin Python API for session lifecycle.

Delegates to the composition root's use-case instances.
This module is the sole bridge between the pytest plugin and the
composition root — the plugin must NOT import adapters directly.
"""

from __future__ import annotations

from typing import List, Optional


def start_session(
    app_command: str,
    app_args: Optional[List[str]] = None,
    resolution: str = "1280x800",
    color_depth: int = 24,
    wait: float = 2.0,
    name: Optional[str] = None,
) -> str:
    """Start a new GUI session and return its session_id.

    Delegates to SessionStartUseCase via the composition root.
    Import is deferred to avoid loading adapters at module import time.
    """
    from aiyes.cli.composition_root import session_start_uc

    session = session_start_uc.execute(
        app_command=app_command,
        app_args=app_args if app_args is not None else [],
        resolution=resolution,
        color_depth=color_depth,
        wait=wait,
        name=name,
    )
    return session.session_id


def stop_session(session_id: str) -> None:
    """Stop a GUI session by session_id.

    Delegates to SessionStopUseCase via the composition root.
    Import is deferred to avoid loading adapters at module import time.
    """
    from aiyes.cli.composition_root import session_stop_uc

    session_stop_uc.execute(session_id=session_id)
