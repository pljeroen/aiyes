"""Top-level window domain type — stdlib only."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class TopLevelWindow:
    """A top-level window identified by role and name."""

    role: str
    name: str
