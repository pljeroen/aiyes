"""Session value object — immutable frozen dataclass.

Holds all session state fields: display, PIDs, bus address, config.
Supports multiple backends (linux, android) via the backend discriminator.
"""

from __future__ import annotations

import dataclasses
import re
from typing import List, Optional, Tuple, Union

# session_id must be alphanumeric, hyphens, or underscores only.
_VALID_SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

_VALID_BACKENDS = frozenset(("linux", "android"))


@dataclasses.dataclass(frozen=True)
class Session:
    """Immutable session state."""

    session_id: str
    app_pid: int
    app_command: str
    app_args: Tuple[str, ...]
    name: Optional[str]
    # Linux-specific fields — Optional with defaults for Android sessions
    display: str = ""
    atspi_bus_pid: int = 0
    atspi_bus_address: str = ""
    xvfb_pid: int = 0
    resolution: str = ""
    color_depth: int = 0
    started_at: float = 0.0
    # Backend discriminator (linux or android)
    backend: str = "linux"
    # Android-specific fields
    device_serial: Optional[str] = None
    package_name: str = ""
    activity_name: str = ""

    def __post_init__(self) -> None:
        """Validate fields and ensure collection fields are truly immutable."""
        validate_session_id(self.session_id)
        if self.backend not in _VALID_BACKENDS:
            raise ValueError(
                f"backend must be one of {sorted(_VALID_BACKENDS)}, "
                f"got {self.backend!r}"
            )
        if isinstance(self.app_args, list):
            object.__setattr__(self, "app_args", tuple(self.app_args))


def parse_android_package_identity(
    app_command: str, app_args: Union[Tuple[str, ...], List[str]]
) -> Tuple[str, str]:
    """Extract Android package/activity identity from common launch commands."""
    candidates = [app_command] + list(app_args)
    for index, value in enumerate(candidates):
        if value in ("-n", "--component") and index + 1 < len(candidates):
            return _split_android_component(candidates[index + 1])
        if value in ("-p", "--pkg", "--package") and index + 1 < len(candidates):
            package = candidates[index + 1]
            if _looks_like_android_package(package):
                return package, ""
    for value in candidates:
        if _looks_like_android_component(value):
            return _split_android_component(value)
    for value in candidates:
        if _looks_like_android_package(value):
            return value, ""
    return "", ""


def android_package_name(session: object) -> str:
    """Return persisted Android package name, deriving it from command data if needed."""
    package_name = getattr(session, "package_name", "")
    if package_name:
        return package_name
    app_command = getattr(session, "app_command", "")
    app_args = getattr(session, "app_args", ())
    derived, _ = parse_android_package_identity(app_command, app_args)
    return derived


def _split_android_component(component: str) -> Tuple[str, str]:
    package, _, activity = component.partition("/")
    return package, activity


def _looks_like_android_package(value: str) -> bool:
    if value.startswith("-") or "/" in value:
        return False
    return "." in value and all(part for part in value.split("."))


def _looks_like_android_component(value: str) -> bool:
    if value.startswith("-") or "/" not in value:
        return False
    package, _, activity = value.partition("/")
    return _looks_like_android_package(package) and bool(activity)


def validate_session_id(session_id: str) -> None:
    """Validate session_id against path traversal and injection attacks.

    Only alphanumeric characters, hyphens, and underscores are allowed.
    Rejects empty strings, path separators, "..", and any other
    characters that could escape the session directory.

    Raises:
        ValueError: If session_id is invalid.
    """
    if not session_id:
        raise ValueError("session_id must not be empty")
    if not _VALID_SESSION_ID.match(session_id):
        raise ValueError(
            f"session_id contains invalid characters: {session_id!r}. "
            "Only alphanumeric characters, hyphens, and underscores are allowed."
        )
