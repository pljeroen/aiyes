"""Marionette profile port — Protocol for provisioning the Firefox profile that
carries the derived ``marionette.port`` preference.

AIYES-117 / A10-AF-001: Firefox honours the Marionette listen port ONLY via the
``marionette.port`` profile preference — the ``--marionette-port`` CLI argument
is silently ignored (Firefox 149.x listens on the default 2828 regardless). To
make Firefox listen on the exact port recorded on the Session, session_start must
launch Firefox against a profile whose ``user.js`` sets that pref. Writing that
file is filesystem I/O, so it lives behind this port; the domain use case only
decides the port value and whether to reuse a caller-supplied profile.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class MarionetteProfilePort(Protocol):
    """Port for provisioning / tearing down the Marionette-port Firefox profile."""

    def provision(
        self, session_id: str, port: int, existing_profile: Optional[str]
    ) -> str:
        """Ensure a Firefox profile has ``marionette.port = <port>`` set.

        If ``existing_profile`` is a caller-supplied ``-profile`` path, append the
        pref to that profile's ``user.js`` and return the same path (the caller
        owns cleanup). Otherwise create a session-scoped temp profile, write its
        ``user.js`` with ONLY the ``marionette.port`` pref, and return its path.

        The returned path is what MUST be passed to Firefox via ``-profile``.
        """
        ...

    def cleanup(self, session_id: str) -> None:
        """Remove the session-scoped temp profile created by :meth:`provision`.

        No-op when no aiyes-owned temp profile was created for this session (e.g.
        the caller supplied their own ``-profile``).
        """
        ...
