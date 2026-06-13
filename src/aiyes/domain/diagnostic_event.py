"""Diagnostic event value object for the observability port (AIYES-103..107).

A single frozen value object carries the union of the LE-01 (failure
classification) and LE-02 (evidence profile selection) payload fields. The
``action`` field discriminates which event kind an instance represents; the
remaining fields are optional and default to ``None`` so each event populates
only the subset its action requires.

Stdlib only — domain purity (FC-ARCH-01).
"""

from __future__ import annotations

import dataclasses
from typing import Optional


@dataclasses.dataclass(frozen=True)
class DiagnosticEvent:
    """An immutable diagnostic event.

    LE-01 (scenario.diagnostic.failure_classified) populates contract_id,
    step_id, failure_code, diagnostic_summary. LE-02
    (evidence.profile.selected) populates profile, raw_tree_included,
    preserved_failure_count. The ``action`` field is the discriminator.
    """

    action: str
    # LE-01 fields
    contract_id: Optional[str] = None
    step_id: Optional[str] = None
    failure_code: Optional[str] = None
    diagnostic_summary: Optional[str] = None
    # LE-02 fields
    profile: Optional[str] = None
    raw_tree_included: Optional[bool] = None
    preserved_failure_count: Optional[int] = None
