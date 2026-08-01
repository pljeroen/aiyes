"""Centralized accessibility name and role comparison utilities.

Accessibility node names may contain embedded newlines, tabs, or multiple
spaces from different UI frameworks (Flutter, React Native, Electron, etc.).
This module normalizes whitespace before comparison so that user-supplied
patterns match regardless of the specific whitespace in the node name.

The original (un-normalized) name and role are NEVER modified — only
comparison operands are normalized.
"""

from __future__ import annotations

import re

from aiyes.domain.role_aliases import ROLE_ALIAS_TABLE


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


def role_matches(node_role: str, requested_role: str) -> bool:
    """Return whether raw AT-SPI and canonical role labels are equivalent.

    AT-SPI exposes multi-word labels with spaces (for example, ``"combo
    box"``) and aliases such as ``"button"``, while aiyes accepts canonical
    underscore-separated role names (``"combo_box"``, ``"push_button"``).
    This normalizes and aliases the comparison only; callers retain the
    original raw role text for stored and returned nodes.
    """
    normalized_node_role = normalize_whitespace(node_role).replace(" ", "_")
    normalized_requested_role = normalize_whitespace(requested_role).replace(
        " ", "_"
    )
    canonical_node_role = ROLE_ALIAS_TABLE.get(
        normalized_node_role, normalized_node_role
    )
    canonical_requested_role = ROLE_ALIAS_TABLE.get(
        normalized_requested_role, normalized_requested_role
    )
    return canonical_node_role == canonical_requested_role
