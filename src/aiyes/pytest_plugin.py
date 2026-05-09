"""pytest plugin providing gui_runtime marker and gui_session fixture.

Registered via the pytest11 entry point in pyproject.toml.
Auto-discovered by pytest at startup.
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Generator, List

import pytest

import aiyes
import aiyes.session_api


def pytest_configure(config: Any) -> None:
    """Register the gui_runtime marker."""
    config.addinivalue_line(
        "markers",
        "gui_runtime: mark test as requiring GUI runtime (Xvfb, xdotool, screenshot tool)",
    )


def pytest_runtest_setup(item: Any) -> None:
    """Skip tests marked with gui_runtime when GUI runtime is unavailable."""
    gui_markers = list(item.iter_markers(name="gui_runtime"))
    if gui_markers and not aiyes.available():
        pytest.skip(
            "GUI runtime unavailable (Xvfb, xdotool, or screenshot tool missing)"
        )


def gui_session() -> Generator[Callable[..., str], None, None]:
    """Factory fixture for GUI session lifecycle.

    Yields a callable factory. Call it with an app_command to start a session:

        session_id = gui_session("gedit")
        session_id2 = gui_session("firefox", resolution="1920x1080")

    All sessions are automatically stopped on teardown (even on exception).
    """
    if not aiyes.available():
        pytest.skip(
            "GUI runtime unavailable (Xvfb, xdotool, or screenshot tool missing)"
        )

    session_ids: List[str] = []

    def factory(app_command: str, **kwargs: Any) -> str:
        session_id = aiyes.session_api.start_session(app_command=app_command, **kwargs)
        session_ids.append(session_id)
        return session_id

    try:
        yield factory
    finally:
        for sid in reversed(session_ids):
            try:
                aiyes.session_api.stop_session(sid)
            except Exception as exc:
                warnings.warn(
                    f"Failed to stop session {sid} during teardown: {exc}",
                    stacklevel=1,
                )


# Register gui_session as a pytest fixture while keeping the raw function
# callable for direct testing. pytest discovers fixtures by looking for
# FixtureFunctionDefinition objects in module namespace.
_gui_session_fixture = pytest.fixture(name="gui_session")(gui_session)
