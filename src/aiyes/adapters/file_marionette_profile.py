"""FileMarionetteProfile — filesystem adapter for MarionetteProfilePort.

Owns the profile / ``user.js`` filesystem I/O that makes Firefox listen on the
derived Marionette port (AIYES-117 / A10-AF-001). The ``marionette.port`` pref is
the ONLY supported conveyance — a live probe against Firefox 149.x showed the
``--marionette-port`` CLI arg is ignored while the pref is honoured.

Temp profiles are created at a deterministic, aiyes-namespaced path keyed by the
validated ``session_id`` so :meth:`cleanup` can find and remove them on session
stop WITHOUT threading a new field through the frozen Session. A caller-supplied
``-profile`` is never at that path, so cleanup of it is a safe no-op.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

# session_id is validated (alphanumeric/-/_ only) upstream, so the leaf is safe
# to embed in a path; the prefix namespaces the dir as aiyes-owned.
_PROFILE_PREFIX = "aiyes-marionette-profile-"


def _pref_line(port: int) -> str:
    """Render the single ``marionette.port`` user_pref line (nothing else)."""
    return 'user_pref("marionette.port", %d);\n' % int(port)


class FileMarionetteProfile:
    """Provision / tear down the Marionette-port Firefox profile via the filesystem."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        # Default to the OS temp dir; injectable for tests.
        self._base_dir = base_dir or tempfile.gettempdir()

    def _temp_profile_path(self, session_id: str) -> str:
        return os.path.join(self._base_dir, _PROFILE_PREFIX + session_id)

    def provision(
        self, session_id: str, port: int, existing_profile: Optional[str] = None
    ) -> str:
        """Ensure a profile carries ``marionette.port = <port>``; return its path.

        Caller-supplied ``existing_profile`` -> append the pref to its ``user.js``.
        Otherwise create/refresh a session-scoped temp profile owned by aiyes.
        """
        if existing_profile:
            profile_dir = str(existing_profile)
            os.makedirs(profile_dir, exist_ok=True)
            user_js = os.path.join(profile_dir, "user.js")
            # Append so a caller-supplied profile's other prefs are preserved; our
            # pref is written LAST, so it is the effective value.
            with open(user_js, "a", encoding="utf-8") as handle:
                handle.write(_pref_line(port))
            return profile_dir

        profile_dir = self._temp_profile_path(session_id)
        os.makedirs(profile_dir, exist_ok=True)
        user_js = os.path.join(profile_dir, "user.js")
        # aiyes-owned temp profile: write ONLY the marionette.port pref.
        with open(user_js, "w", encoding="utf-8") as handle:
            handle.write(_pref_line(port))
        return profile_dir

    def cleanup(self, session_id: str) -> None:
        """Remove the aiyes-owned temp profile for this session (no-op otherwise)."""
        profile_dir = self._temp_profile_path(session_id)
        if os.path.isdir(profile_dir):
            shutil.rmtree(profile_dir, ignore_errors=True)
