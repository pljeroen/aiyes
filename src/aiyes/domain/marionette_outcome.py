"""Marionette script outcome — the domain result of a Marionette script call.

A pure, frozen value object handed back from MarionettePort. It is deliberately
minimal: ``ok`` discriminates a recoverable webdriver/JS error (ok=False, with a
mapped ``error`` message) from a successful evaluation (ok=True, carrying the
JSON-serializable ``value``). Zero I/O, zero adapter imports — stdlib only, so it
stays domain-pure (NFR-02 / C-PURITY).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional


@dataclasses.dataclass(frozen=True)
class MarionetteScriptOutcome:
    """Result of a single Marionette script evaluation.

    Attributes:
        ok: True when the script evaluated without a webdriver/JS error.
        value: The JSON-serializable return value (None unless ok and the
            script returned a value).
        error: The mapped error message when ok is False; None otherwise.
    """

    ok: bool
    value: Any = None
    error: Optional[str] = None
