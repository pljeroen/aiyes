"""AIYES-44: wire wait, wait_stable, assert step kinds in scenario executor.

Three step kinds were declared in domain _VALID_STEP_KINDS but the real
executor raised "unsupported real scenario step kind" because there were
no executor branches. This contract wires the executor branches so
scenarios using these kinds run end-to-end.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from aiyes.adapters.scenario_use_case_executor import ScenarioUseCaseExecutor
from aiyes.domain.scenario import ScenarioStep


@dataclasses.dataclass
class RecordingUseCase:
    """Capture kwargs and return a configured result."""

    result: Any
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.result


def _executor_with_session(
    *,
    wait: Any = None,
    wait_stable: Any = None,
) -> ScenarioUseCaseExecutor:
    """Build an executor with an established session for wait-family tests."""
    start = RecordingUseCase(SimpleNamespace(session_id="s1", backend="linux"))
    inspect = RecordingUseCase(
        SimpleNamespace(tree={"roots": []}, screenshot_path=None)
    )
    find = RecordingUseCase([])
    action = RecordingUseCase(SimpleNamespace(status="ok"))
    type_text = RecordingUseCase(SimpleNamespace(status="ok"))
    screenshot = RecordingUseCase(SimpleNamespace(path="/tmp/shot.png", data=None))
    stop = RecordingUseCase(SimpleNamespace(status="ok", session_id="s1"))
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=inspect,
        find=find,
        action=action,
        type_text=type_text,
        screenshot=screenshot,
        session_stop=stop,
        wait=wait,
        wait_stable=wait_stable,
    )
    # Establish session
    executor.execute(
        ScenarioStep(
            id="start",
            kind="start_session",
            parameters={"command": "gedit", "wait_seconds": 0.0},
        )
    )
    return executor


# ─── AC-44-01: wait dispatch + parameter forwarding ───────────────────


def test_wait_step_dispatches_to_wait_use_case_with_all_parameters() -> None:
    wait = RecordingUseCase(
        SimpleNamespace(found=True, timeout=False, id="n-1", transient=False)
    )
    executor = _executor_with_session(wait=wait)

    result = executor.execute(
        ScenarioStep(
            id="wait_for_dialog",
            kind="wait",
            parameters={
                "role": "dialog",
                "name_pattern": "Save",
                "timeout": 5.0,
                "state": "showing",
                "absent": False,
                "transient": False,
            },
        )
    )

    assert result.status == "passed"
    assert wait.calls == [
        {
            "session_id": "s1",
            "role": "dialog",
            "name_pattern": "Save",
            "timeout": 5.0,
            "state": "showing",
            "absent": False,
            "transient": False,
        }
    ]


def test_wait_step_uses_defaults_when_optional_parameters_omitted() -> None:
    wait = RecordingUseCase(
        SimpleNamespace(found=True, timeout=False, id="n-2", transient=False)
    )
    executor = _executor_with_session(wait=wait)

    executor.execute(
        ScenarioStep(id="wait_role_only", kind="wait", parameters={"role": "button"})
    )

    call = wait.calls[0]
    assert call["session_id"] == "s1"
    assert call["role"] == "button"
    assert call["name_pattern"] is None
    assert call["timeout"] is None
    assert call["state"] is None
    assert call["absent"] is False
    assert call["transient"] is False


def test_wait_step_surfaces_timeout_as_passed_with_found_false() -> None:
    """Timeout is not an error per WaitUseCase semantics (exit 0)."""
    wait = RecordingUseCase(
        SimpleNamespace(found=False, timeout=True, id=None, transient=False)
    )
    executor = _executor_with_session(wait=wait)

    result = executor.execute(
        ScenarioStep(
            id="wait_t", kind="wait", parameters={"role": "button", "timeout": 0.1}
        )
    )

    assert result.status == "passed"
    assert result.output["found"] is False
    assert result.output["timeout"] is True


def test_wait_step_fails_when_session_not_started() -> None:
    wait = RecordingUseCase(SimpleNamespace(found=True))
    start = RecordingUseCase(SimpleNamespace(session_id="s1"))
    executor = ScenarioUseCaseExecutor(
        session_start=start,
        inspect=RecordingUseCase(None),
        find=RecordingUseCase([]),
        action=RecordingUseCase(None),
        type_text=RecordingUseCase(None),
        screenshot=RecordingUseCase(None),
        session_stop=RecordingUseCase(None),
        wait=wait,
    )

    result = executor.execute(
        ScenarioStep(id="early_wait", kind="wait", parameters={"role": "button"})
    )

    assert result.status == "failed"
    assert "session" in result.error.lower()
    assert wait.calls == []


# ─── AC-44-02: wait_stable dispatch + parameter forwarding ────────────


def test_wait_stable_step_dispatches_with_all_parameters() -> None:
    wait_stable = RecordingUseCase(
        SimpleNamespace(
            stable=True, timeout=False, polls=4, changes=(), comparison_mode="node_id"
        )
    )
    executor = _executor_with_session(wait_stable=wait_stable)

    result = executor.execute(
        ScenarioStep(
            id="stable",
            kind="wait_stable",
            parameters={
                "timeout": 5.0,
                "interval": 0.25,
                "consecutive": 5,
                "tolerance": 1,
                "ignore_nodes": ["n-x", "n-y"],
            },
        )
    )

    assert result.status == "passed"
    call = wait_stable.calls[0]
    assert call["session_id"] == "s1"
    assert call["timeout"] == 5.0
    assert call["poll_interval"] == 0.25
    assert call["consecutive"] == 5
    assert call["tolerance"] == 1
    assert call["ignore_ids"] == frozenset({"n-x", "n-y"})


def test_wait_stable_step_uses_defaults_when_optional_parameters_omitted() -> None:
    wait_stable = RecordingUseCase(
        SimpleNamespace(
            stable=True, timeout=False, polls=3, changes=(), comparison_mode="node_id"
        )
    )
    executor = _executor_with_session(wait_stable=wait_stable)

    executor.execute(ScenarioStep(id="stable_def", kind="wait_stable", parameters={}))

    call = wait_stable.calls[0]
    assert call["session_id"] == "s1"
    assert call["timeout"] == 10.0
    assert call["poll_interval"] == 0.5
    assert call["consecutive"] == 3
    assert call["tolerance"] == 0
    assert call["ignore_ids"] == frozenset()


def test_wait_stable_timeout_surfaces_as_passed_with_stable_false() -> None:
    """Timeout is observable in output, not an executor error."""
    wait_stable = RecordingUseCase(
        SimpleNamespace(
            stable=False, timeout=True, polls=2, changes=(), comparison_mode="node_id"
        )
    )
    executor = _executor_with_session(wait_stable=wait_stable)

    result = executor.execute(
        ScenarioStep(id="stable_t", kind="wait_stable", parameters={"timeout": 0.05})
    )

    assert result.status == "passed"
    assert result.output["stable"] is False
    assert result.output["timeout"] is True


# ─── AC-44-03, AC-44-04, AC-44-05: assert ─────────────────────────────


def test_assert_step_evaluates_against_prior_step_output() -> None:
    """assert reads source step output from accumulated context and evaluates."""
    executor = _executor_with_session()

    # Seed an inspect-like prior output
    executor._outputs["earlier_inspect"] = {
        "tree": {"id": "root", "children": [{"id": "n-1"}]}
    }

    result = executor.execute(
        ScenarioStep(
            id="check_node",
            kind="assert",
            parameters={
                "assertion": {
                    "id": "check_n1",
                    "kind": "node_exists",
                    "source": "earlier_inspect",
                    "node_id": "n-1",
                }
            },
        )
    )

    assert result.status == "passed"
    assertion_out = result.output.get("assertion")
    assert isinstance(assertion_out, dict)
    assert assertion_out["assertion_id"] == "check_n1"
    assert assertion_out["kind"] == "node_exists"
    assert assertion_out["status"] == "passed"


def test_assert_step_with_failing_condition_returns_failed_with_reason() -> None:
    executor = _executor_with_session()
    executor._outputs["empty_inspect"] = {"tree": {}}

    result = executor.execute(
        ScenarioStep(
            id="check_empty",
            kind="assert",
            parameters={
                "assertion": {
                    "id": "should_be_non_empty",
                    "kind": "tree_non_empty",
                    "source": "empty_inspect",
                }
            },
        )
    )

    assert result.status == "failed"
    assert result.error  # non-empty reason
    assertion_out = result.output.get("assertion")
    assert isinstance(assertion_out, dict)
    assert assertion_out["status"] == "failed"
    assert assertion_out["message"]  # populated failure message


def test_assert_step_with_unknown_source_surfaces_failure() -> None:
    """Unknown source defaults to empty context per scenario_assertions._source."""
    executor = _executor_with_session()

    result = executor.execute(
        ScenarioStep(
            id="bad_source",
            kind="assert",
            parameters={
                "assertion": {
                    "id": "ghost",
                    "kind": "node_exists",
                    "source": "no_such_step",
                    "node_id": "n-1",
                }
            },
        )
    )

    assert result.status == "failed"
    assert result.error


def test_assert_step_missing_assertion_parameter_returns_failed() -> None:
    executor = _executor_with_session()

    result = executor.execute(
        ScenarioStep(id="no_assertion", kind="assert", parameters={})
    )

    assert result.status == "failed"


# ─── AC-44-06: public fixture validates ───────────────────────────────
# This is a load-time test of the public Linux fixture exercising all
# three new kinds. End-to-end Xvfb run is exercised by the existing
# real-scenario gate (test_aiyes80_real_scenario_gate) if the fixture
# is picked up by its discovery; here we verify the fixture is valid.


def test_aiyes44_public_fixture_validates() -> None:
    """The new public Linux fixture loads and validates against the schema."""
    import json
    from pathlib import Path

    from aiyes.domain.scenario import validate_scenario_document

    fixture_path = Path("examples/scenarios/linux-gedit-wait-assert.json")
    assert fixture_path.is_file(), "AIYES-44 public fixture must exist"

    document = json.loads(fixture_path.read_text())
    result = validate_scenario_document(document, public_fixture=True)
    assert result.ok, f"AIYES-44 fixture invalid: {result.issues}"
    scenario = result.scenario
    assert scenario is not None
    kinds = {step.kind for step in scenario.steps}
    assert "wait" in kinds
    assert "wait_stable" in kinds
    assert "assert" in kinds
