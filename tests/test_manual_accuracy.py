"""The manual's examples and tips agree with what the commands do.

* The "action (failure)" example under Output format is the JSON the action
  command prints when the node does not offer the requested action. The
  expected text comes from running the command, not from a copy in this file.
* The pruning tip under Tips for effective use names ``inspect --no-prune``,
  the command that has the option, and does not present ``--no-prune`` as an
  option of any command.

The manual is located by its headings and example labels. The command runs
against in-memory doubles, so no display, session or application is needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import MagicMock, patch

from aiyes.cli.main import cli
from aiyes.domain.session import Session
from aiyes.domain.types import ActionPortResult
from aiyes.domain.use_cases.action import ActionUseCase

from tests.conftest import FakeSessionRepository, FakeTreeStore, make_cli_runner

_MANUAL = Path(__file__).resolve().parents[1] / "docs" / "manual.md"
_HEADING = re.compile(r"^(#{1,6}) ")
_FENCE = "```"
_SESSION_ID = "manual-example"


def _section(heading: str) -> List[str]:
    """Lines of the manual section that starts at ``heading``.

    The section ends at the next heading of the same or a higher level.
    Lines inside fenced code blocks are never taken for headings.
    """
    lines = _MANUAL.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line == heading]
    assert len(starts) == 1, f"expected one {heading!r} heading, found {len(starts)}"
    level_match = _HEADING.match(heading)
    assert level_match is not None, f"{heading!r} is not a heading"
    level = len(level_match.group(1))
    body: List[str] = []
    in_fence = False
    for line in lines[starts[0] + 1 :]:
        if line.startswith(_FENCE):
            in_fence = not in_fence
        elif not in_fence:
            match = _HEADING.match(line)
            if match is not None and len(match.group(1)) <= level:
                break
        body.append(line)
    return body


def _json_example(section: List[str], label: str) -> Any:
    """The JSON block that directly follows the line ``label`` in ``section``."""
    starts = [i for i, line in enumerate(section) if line == label]
    assert len(starts) == 1, f"expected one {label!r} example, found {len(starts)}"
    start = starts[0] + 1
    assert section[start] == _FENCE + "json", (
        f"{label!r} is not followed by a JSON block"
    )
    end = section.index(_FENCE, start + 1)
    return json.loads("\n".join(section[start + 1 : end]))


def _bullets(section: List[str]) -> List[str]:
    """Top-level list items of ``section``, each with its continuation lines."""
    items: List[str] = []
    current: Optional[List[str]] = None
    for line in section:
        if line.startswith("- "):
            current = [line[2:]]
            items.append("")
        elif current is not None and line.strip():
            current.append(line.strip())
        else:
            current = None
            continue
        items[-1] = " ".join(current)
    return items


def _code_spans(text: str) -> List[str]:
    return re.findall(r"`([^`]+)`", text)


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
    example = _json_example(_section("## Output format"), "**action (failure)**:")

    printed = _action_command_output("n_002", "set_text", ("click", "activate"))

    assert example == printed


def test_pruning_tip_names_the_command_that_has_the_option() -> None:
    tips = [
        bullet
        for bullet in _bullets(_section("### Tips for effective use"))
        if "--no-prune" in bullet
    ]

    assert len(tips) == 1, tips
    spans = _code_spans(tips[0])
    assert "inspect --no-prune" in spans, tips[0]
    assert "--no-prune" not in spans, tips[0]
