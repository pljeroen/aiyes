"""Session liveness check — shared domain logic.

Determines whether a session is active based on its backend type
and the liveness of its key processes. Used by session_stop,
session_resolve, session_list, and prune use cases.

No external dependencies — stdlib + domain/ports only.
"""

from __future__ import annotations

from typing import Optional

from aiyes.domain.session import android_package_name
from aiyes.ports.android_app_lifecycle import AndroidAppLifecyclePort
from aiyes.ports.process import ProcessPort


def is_session_active(
    session: object,
    process: ProcessPort,
    android_lifecycle: Optional[AndroidAppLifecyclePort] = None,
) -> bool:
    """Check if a session is active based on its backend.

    Linux: app_pid and xvfb_pid must both be running.
    Android: package identity on the device determines liveness when available.

    Args:
        session: A Session value object (structural typing).
        process: An object satisfying ProcessPort.

    Returns:
        True if the session's key processes are all running.
    """
    backend = getattr(session, "backend", "linux")
    if backend == "android" and android_lifecycle is not None:
        serial = getattr(session, "device_serial", None)
        package_name = android_package_name(session)
        if serial and package_name:
            return android_lifecycle.is_app_running(serial, package_name)

    app_pid: int = getattr(session, "app_pid")
    app_running: bool = process.is_running(app_pid)
    if backend == "android":
        return app_running
    # Linux: both app and Xvfb must be running
    xvfb_pid: int = getattr(session, "xvfb_pid")
    return app_running and process.is_running(xvfb_pid)
