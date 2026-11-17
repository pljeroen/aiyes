"""The README, manual, scenario guide and CHANGELOG describe the description.

Sections are found by their headings, never by position.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parents[1]

# sha256 of the [Unreleased] '### Fixed' block at the implementation base.
_FIXED_BLOCK_SHA256 = "1b671c34dce5a4c8885cf74463ddcdecc527b8c5b17de99579d9aa4e61ee181a"


def _read(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text under the first heading matching ``heading`` (a regex), up to
    the next heading of the same or a higher level; fenced code is skipped
    when looking for headings."""
    lines = text.splitlines()
    pattern = re.compile(heading)
    start = level = None
    fenced = False
    for index, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line.startswith("#"):
            continue
        depth = len(line) - len(line.lstrip("#"))
        if start is None:
            if pattern.match(line):
                start, level = index, depth
        elif depth <= level:
            return "\n".join(lines[start + 1 : index])
    assert start is not None, f"no heading matching {heading!r}"
    return "\n".join(lines[start + 1 :])


def _top_level_entries(block: str) -> List[str]:
    entries: List[List[str]] = []
    for line in block.splitlines():
        if line.startswith("- "):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    return ["\n".join(e).rstrip() for e in entries]


def test_readme_names_the_option_and_the_listing() -> None:
    readme = _read("README.md")
    assert "--description" in readme
    assert "descriptions_containing" in readme


def test_the_manual_documents_the_field_options_and_scopes() -> None:
    manual = _read("docs/manual.md")
    table = _section(manual, r"### Accessibility tree\b")
    assert re.search(r"^\| `description` \|", table, re.MULTILINE)
    for command in ("find", "wait", "do"):
        section = _section(manual, rf"#### `aieyes {command}(?![\w-])")
        assert "--description" in section, command
    assert "scope" in _section(manual, r"### Tree pruning\b").lower()


def test_the_scenario_guide_names_the_step_key() -> None:
    assert "description_pattern" in _read("docs/release-scenarios.md")


def test_the_changelog_adds_two_entries_before_the_unchanged_fixes() -> None:
    unreleased = _section(_read("CHANGELOG.md"), r"## \[Unreleased\]")
    headings = [line for line in unreleased.splitlines() if line.startswith("### ")]
    assert headings[:2] == ["### Added", "### Fixed"]
    added = _top_level_entries(_section(unreleased, r"### Added$"))
    assert len(added) == 2, added
    visible = [e for e in added if e.startswith("- **Visible changes for callers")]
    assert len(visible) == 1, added
    assert "--within-name" in visible[0]
    assert "panel" in visible[0].lower() or "tab page" in visible[0].lower()
    fixed = _section(unreleased, r"### Fixed$").strip("\n")
    assert hashlib.sha256(fixed.encode("utf-8")).hexdigest() == _FIXED_BLOCK_SHA256
