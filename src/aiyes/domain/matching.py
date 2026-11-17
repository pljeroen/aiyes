"""Centralized accessibility name, role and description comparison utilities.

Accessibility node names may contain embedded newlines, tabs, or multiple
spaces from different UI frameworks (Flutter, React Native, Electron, etc.).
This module normalizes whitespace before comparison so that user-supplied
patterns match regardless of the specific whitespace in the node name.

The original (un-normalized) name, role and description are NEVER modified —
only comparison operands are normalized.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from aiyes.domain.role_aliases import ROLE_ALIAS_TABLE

# Most whole descriptions a no-match result lists.
DESCRIPTIONS_CONTAINING_LIMIT = 5

ANDROID_DESCRIPTION_REFUSAL = (
    "Selecting by description is not supported on Android sessions. On Android "
    "a node without text is named by its content description, so select it by "
    "name instead: NAME_PATTERN on find and wait, --name on do, name_pattern in "
    "MCP calls and scenario steps."
)


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


def _description_key(text: str) -> str:
    """The comparison form of a description or a description selector."""
    return normalize_whitespace(text).lower()


def description_selector_given(pattern: Optional[str]) -> bool:
    """Return whether a description selector filters at all.

    A selector is given when it is a string with something other than
    whitespace in it. None, "" and whitespace-only values are not given: they
    mean no description filter.
    """
    return isinstance(pattern, str) and _description_key(pattern) != ""


def description_matches(description: str, pattern: str) -> bool:
    """Whole-description match with case and whitespace runs ignored.

    Both sides are compared after collapsing every whitespace run to one
    space, stripping the ends and lower-casing (the normalization
    name_matches uses). The whole description must equal the pattern; a part
    of it does not match. An empty or whitespace-only description, or a
    pattern that is not given, never matches.
    """
    wanted = _description_key(pattern)
    return wanted != "" and _description_key(description) == wanted


def descriptions_containing(
    descriptions: Iterable[str], pattern: str
) -> Tuple[Tuple[str, ...], int]:
    """The distinct descriptions that contain the pattern, and how many there are.

    A description contains the pattern when the pattern's comparison form is
    a substring of the description's comparison form. Each distinct
    description is kept once, as given, in the order of its first
    occurrence; at most DESCRIPTIONS_CONTAINING_LIMIT are returned, together
    with the number of distinct descriptions found.
    """
    wanted = _description_key(pattern)
    found: List[str] = []
    if wanted:
        for description in descriptions:
            if description not in found and wanted in _description_key(description):
                found.append(description)
    return tuple(found[:DESCRIPTIONS_CONTAINING_LIMIT]), len(found)


def android_description_refusal(backend: str, pattern: Optional[str]) -> Optional[str]:
    """The error message for a given description selector on Android, else None.

    The Android backend carries no description, so a given selector is
    refused rather than matching nothing. A selector that is not given is
    never refused.
    """
    if backend == "android" and description_selector_given(pattern):
        return ANDROID_DESCRIPTION_REFUSAL
    return None
