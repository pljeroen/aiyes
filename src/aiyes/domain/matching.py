"""Centralized name matching — whitespace normalization for accessibility names.

Accessibility node names may contain embedded newlines, tabs, or multiple
spaces from different UI frameworks (Flutter, React Native, Electron, etc.).
This module normalizes whitespace before comparison so that user-supplied
patterns match regardless of the specific whitespace in the node name.

The original (un-normalized) name is NEVER modified — only the comparison
is performed on normalized forms.
"""

from __future__ import annotations

import re


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to single space, strip edges."""
    return re.sub(r"\s+", " ", text).strip()


def name_matches(node_name: str, pattern: str) -> bool:
    """Case-insensitive substring match with whitespace normalization.

    Handles None node_name defensively (treats as empty string).
    Empty or whitespace-only pattern matches everything (treated as no filter).
    """
    if not node_name:
        node_name = ""
    normalized_name = normalize_whitespace(node_name).lower()
    normalized_pattern = normalize_whitespace(pattern).lower()
    if not normalized_pattern:
        return True  # empty/whitespace-only pattern matches everything
    return normalized_pattern in normalized_name
