"""The manual's action failure example and pruning tip match what the commands do.

* The "action (failure)" example under Output format is the JSON the action
  command prints when the node does not offer the requested action. The
  expected text comes from running the command, not from a copy in this file.
* Tips for effective use names ``inspect --no-prune``, the command that has
  the option, and mentions ``--no-prune`` only as part of
  ``inspect --no-prune``.

Each passage is found by its heading text, whatever the heading's level, and
the example also by its label. The command runs against in-memory doubles, so
no display, live session or application is needed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import MagicMock, patch

from aiyes.cli.main import cli
from aiyes.domain.session import Session
from aiyes.domain.types import ActionPortResult
from aiyes.domain.use_cases.action import ActionUseCase

from tests.conftest import FakeSessionRepository, FakeTreeStore, make_cli_runner

_MANUAL = Path(__file__).resolve().parents[1] / "docs" / "manual.md"
_HEADING = re.compile(r"(#{1,6})[ \t]+(.+)")
_OPENING_FENCE = re.compile(r"```[^`]*")
_CLOSING_FENCE = "```"
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_SESSION_ID = "manual-example"


@dataclass(frozen=True)
class _Block:
    """A heading, a fenced code block or any other line of the manual."""

    kind: str  # "heading", "code" or "line"
    text: str  # the heading's title, the code block's content, or the line
    level: int = 0  # the heading's level; 0 for the other kinds


_BLANK_LINE = _Block("line", "")


def _blocks() -> List[_Block]:
    """The manual as a sequence of headings, fenced code blocks and lines.

    Trailing whitespace is ignored on every line but kept in code block
    content. A code block opens at a line of three backticks and an optional
    info string, and closes at the next line of exactly three backticks; a
    block that is never closed runs to the end of the manual. A heading is a
    line outside code blocks made of one to six ``#``, spaces or tabs, and its
    title.
    """
    blocks: List[_Block] = []
    code: Optional[List[str]] = None
    for raw in _MANUAL.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if code is not None:
            if line == _CLOSING_FENCE:
                blocks.append(_Block("code", "\n".join(code)))
                code = None
            else:
                code.append(raw)
        elif _OPENING_FENCE.fullmatch(line):
            code = []
        else:
            heading = _HEADING.fullmatch(line)
            if heading is None:
                blocks.append(_Block("line", line))
            else:
                title = heading.group(2)
                blocks.append(_Block("heading", title, len(heading.group(1))))
    if code is not None:
        blocks.append(_Block("code", "\n".join(code)))
    return blocks


def _section(title: str) -> List[_Block]:
    """The blocks under the one heading titled ``title``, whatever its level.

    The section ends at the next heading of the same or a higher level.
    """
    blocks = _blocks()
    starts = [
        i
        for i, block in enumerate(blocks)
        if block.kind == "heading" and block.text == title
    ]
    assert len(starts) == 1, f"expected one {title!r} heading, found {len(starts)}"
    level = blocks[starts[0]].level
    section: List[_Block] = []
    for block in blocks[starts[0] + 1 :]:
        if block.kind == "heading" and block.level <= level:
            break
        section.append(block)
    return section


def _json_example(section: List[_Block], label: str) -> Any:
    """The code block that the line ``label`` introduces, parsed as JSON.

    Exactly one line of ``section`` outside code blocks must be the label,
    surrounding whitespace aside, and only blank lines may come between that
    line and the block.
    """
    starts = [
        i
        for i, block in enumerate(section)
        if block.kind == "line" and block.text.strip() == label
    ]
    assert len(starts) == 1, f"expected one {label!r} label, found {len(starts)}"
    following = [block for block in section[starts[0] + 1 :] if block != _BLANK_LINE]
    assert following and following[0].kind == "code", (
        f"{label!r} is not followed by a code block"
    )
    return json.loads(following[0].text)


def _text(section: List[_Block]) -> str:
    """The words of ``section`` joined by single spaces.

    Line breaks and runs of whitespace therefore read as one space. Code block
    content and the titles of lower-level headings are included; fence lines
    and ``#`` markers are not.
    """
    return " ".join(" ".join(block.text for block in section).split())


def _code_spans(text: str) -> List[str]:
    """The contents of the code spans in ``text``.

    A code span opens with a run of backticks and closes at the next run of
    exactly as many backticks.
    """
    return [match.group(2) for match in _CODE_SPAN.finditer(text)]


class _NodeWithoutTheAction:
    """Action port for a node that does not offer the requested action.

    The request fails with no reason given, and the result lists the actions
    the node does offer.
    """

    def __init__(self, offered: Sequence[str]) -> None:
        self._offered = tuple(offered)

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Optional[Any] = None,
    ) -> ActionPortResult:
        return ActionPortResult(success=False, available_actions=self._offered)


def _action_command_output(
    node_id: str, action_name: str, offered: Sequence[str]
) -> Dict[str, Any]:
    """Run ``aieyes action NODE_ID ACTION_NAME`` and parse what it prints."""
    sessions = FakeSessionRepository()
    sessions.save(
        Session(
            session_id=_SESSION_ID,
            app_pid=100,
            app_command="app",
            app_args=(),
            name=None,
        )
    )
    use_case = ActionUseCase(_NodeWithoutTheAction(offered), sessions, FakeTreeStore())
    with (
        patch("aiyes.cli.main.action_uc", use_case),
        patch("aiyes.cli.main.resolve_session_id", return_value=_SESSION_ID),
        patch("aiyes.cli.main.operation_log_adapter", MagicMock()),
    ):
        result = make_cli_runner().invoke(cli, ["action", node_id, action_name])
    assert result.exit_code == 0, result.output
    printed: Dict[str, Any] = json.loads(result.stdout)
    return printed


def test_action_failure_example_is_what_the_action_command_prints() -> None:
    example = _json_example(_section("Output format"), "**action (failure)**:")

    printed = _action_command_output("n_002", "set_text", ("click", "activate"))

    assert example == printed


def test_pruning_tip_names_the_command_that_has_the_option() -> None:
    tips = _text(_section("Tips for effective use"))

    assert "inspect --no-prune" in _code_spans(tips), (
        f"no `inspect --no-prune` code span in: {tips}"
    )
    assert re.findall(r"(?<!inspect )--no-prune", tips) == [], (
        f"--no-prune without inspect before it in: {tips}"
    )
