# Release Scenarios

Release scenarios are deterministic `aieyes` scenario files for repeatable GUI
smoke evidence. They are a secondary release gate, not a replacement for deterministic tests,
unit tests, integration tests, or platform-native E2E frameworks.

The scenario runner does not plan or reason. It executes declared steps, writes
machine-readable output, and can optionally write an evidence bundle. An LLM can
run the command or inspect the evidence, but the model is outside `aiyes`.

## Public Fixtures

Tracked public fixtures live in `examples/scenarios/`:

- `examples/scenarios/linux-gedit-text.json` starts `gedit`, performs a simple
  text operation, verifies the typed text, captures a screenshot, and cleans up.
- `examples/scenarios/android-settings.json` launches Android Settings with
  `android.settings.SETTINGS`, performs a safe top-level navigation smoke,
  captures a screenshot, navigates back, and cleans up.

These fixtures intentionally avoid maintainer-private application names, paths,
package names, accounts, network services, and credentials.

## Local Use

Validate and dry-run a public scenario:

```bash
aieyes scenario run --public-fixture examples/scenarios/linux-gedit-text.json
```

Dry-run is the default. It validates the document and records declared steps
without launching applications, touching a GUI, or requiring Android devices.

Run against real local GUI backends only when explicitly requested:

```bash
aieyes scenario run \
  --real \
  --public-fixture \
  examples/scenarios/linux-gedit-text.json
```

Real runs are prerequisite-gated. Missing dependency-gated prerequisites such as
`gedit`, `adb`, or an available Android device produce a skipped scenario result
that can still be written as evidence.

Write an evidence bundle:

```bash
aieyes scenario run \
  --public-fixture \
  --evidence-dir /tmp/aiyes-release-scenario \
  examples/scenarios/linux-gedit-text.json
```

The runner records declared steps through a deterministic dry-run executor
unless `--real` is supplied. Real observe-act-verify execution uses the same
scenario schema, assertion flow, and evidence format.

## Android Flutter Selectors

Flutter Android apps often expose tappable rows as `Button` even when scenario
authors think of them as views. Prefer stable accessible names in
`name_pattern`, and use `role="*"` when the role is not part of the behavior
being verified. If a selector fails, scenario output includes bounded candidate
diagnostics with likely alternatives and mismatch reasons such as role mismatch,
name mismatch, not visible, or not actionable.

## Optional CI Pattern

Default CI should validate scenario files and docs without requiring a real GUI
or Android emulator:

```bash
python -m pytest tests/test_aiyes74_75_public_scenarios.py tests/test_aiyes76_release_scenario_docs.py -q
```

Real GUI/device runs should remain opt-in and prerequisite-gated. Missing `gedit`,
Linux accessibility dependencies, `adb`, or an emulator should produce skip
evidence rather than a misleading pass.

## Wait Timeout Policy

Scenario `wait`, `wait_stable`, and `wait_reactive` steps fail the scenario step
by default when they time out (or, for `wait_reactive`, end in an unmatched
terminal outcome). A step that does not get what it waited for is a real signal:
treating a timeout as a failure aligns the scenario result with the author's
intent — the scenario exists to catch regressions, so a wait that never
succeeded should not be reported as a passing step.

To record a timeout as a non-failing observation instead, set the single
explicit boolean opt-in `allow_timeout: true` on the wait-family step. With
`allow_timeout: true` the step keeps a timeout as a passed observation; absence
of the field (or `allow_timeout: false`) is the default-fail policy. The
`allow_timeout` value is validated at scenario load time and must be a boolean.

This policy is scoped to scenario-step classification only. It does not change
the direct CLI or MCP `wait` exit semantics, which still report a timeout as a
non-error (exit 0) outcome on the standalone command surface.

## Action Step Policy

A scenario `action` step fails when its action does not report
`"status": "ok"`: when the action reports `"status": "error"`, for example
because the node is not enabled, the application declined the action, the
action is not available on the node or the node could not be found, or when it
returns a result whose status aiyes cannot read. The run then fails:
`aieyes scenario run` exits 1 and the MCP `scenario_run` result reports
`"status": "failed"`. Later steps do not run; cleanup steps still run. The
action's reason stays in the step output.

A failed action step reports the code `action_failed` as its `error` and as
`failure_code` in its output, next to every field the action returned. A step
that expected a refusal but whose action went through reports
`action_not_refused` the same way. The run reports the step's code as its
top-level `failure_code`, and the message of its single `next_actions` entry
names the failing step, quotes the action's reason and gives the fix, so a
client that reads only the top level sees which step failed and why.

To say what an action step expects, set its optional `"expect"` field:

- `"ok"` (the default, also when the field is absent): the step passes only when
  the action reports `"status": "ok"`.
- `"refused"`: a negative check. The step passes only when the action was
  attempted and refused: its reason says the node is not enabled or the
  application declined the action. It fails with `action_not_refused` when the
  action goes through, and with `action_failed` on any other error.
- `"any"`: best effort, the action-step counterpart of `allow_timeout: true` on
  wait-family steps. The step passes whatever the action reports, including a
  target that is not there (an empty source from an optional `find`, or a node
  id the stored tree does not contain), and its output keeps the action's error
  and reason. It still fails when aiyes cannot read the action result, and when
  aiyes itself or the scenario's setup fails: no session (also for an empty
  `find` source after the scenario stopped its session), an invalid step, or a
  failing backend.

A step whose `"expect"` is `"refused"` or `"any"` shows that value in its
output, also when it passes, so evidence explains why a step whose action
reported an error passed. Under `"ok"` and `"refused"`, a step that cannot
attempt the action fails as before: an empty `find` source
(`empty_source_node_id`), a node id the stored tree does not contain, no
session, or an invalid step.

`"refused"` counts an error as a refusal only when its cause is that the node is
not enabled or that the application declined the action, as the reason says.
On Linux, a node that is not enabled is reported as not enabled whatever action
the step names, also when it does not offer that action or offers no actions
(some toolkits, for example Qt, list no actions for a widget that is not
enabled), so `"refused"` passes on it. The check then does not show that the
target offers the action it names: a refused check that names an action the
target never offers passes while the node is not enabled, and fails with
`action_failed` as an action the target does not offer once the node is
enabled.

An action that an enabled target does not offer is not a refusal, because the
application was never asked: the step fails with `action_failed`, and the
message names the actions the target offers. An error without a refusal cause
on a target that lists no actions also fails with `action_failed`, with a
message saying that aiyes could not confirm the target was there, because a
target that is missing when the action runs gives the same result as an enabled
target that offers no actions. If the target may appear late, wait for it
before the step; if it may be absent, `"any"` makes the step best effort, so it
no longer checks a refusal.

On Linux, `"action": "focus"` with `"expect": "refused"` passes on a node that is
not enabled, like any other action, and also when the application declines
focus; on an enabled node it moves the keyboard focus and fails with
`action_not_refused`.

The `"expect"` value is validated when the scenario file loads, in dry-run,
real runs and `scenario preflight`, before any application starts. A value other
than exactly `"ok"`, `"refused"` or `"any"` is rejected with the code
`action_expect_invalid`, and an `"expect"` key on a step that is not an
`action` step is rejected with the code `expect_not_supported`. The CLI exits 2
and prints the validation result; over MCP the result carries `failure_code`
`validation_error` with the same issue.

On Android, `"refused"` never passes, because the Android backend reports no
refusal cause, so a refusal cannot be observed there. For `click`, `focus`,
`long_click` and `scroll`, a widget that is found is tapped and reported
`"status": "ok"`, also one that is not enabled, so on Android a refused `click`
on a disabled widget fails with `action_not_refused`; any other error (a widget
that is not found, an action it does not offer) fails with `action_failed`.

This policy is scoped to scenario-step classification only. The standalone
`action` and `do` commands still exit 0 and report `"status": "error"` in their
JSON when an action fails, on the CLI and over MCP. Dry-run performs no actions,
so action steps pass there. A failed cleanup action step is recorded as failed
with the same codes, but cleanup steps do not change the run result. A passing
action step means the application accepted the request, not that it acted on
it: follow it with a wait or an assertion to check the effect. An `action_ok`
assertion is no longer needed to catch an action error and keeps its meaning.

## MCP Parity

MCP exposes the same release-scenario surfaces as the CLI:

- load and validate a scenario document,
- reject unsafe public fixture references when requested,
- run declared steps deterministically,
- return the same normalized run result,
- optionally write the same evidence bundle policy,
- preserve the trusted-local stdio MCP threat model.

It must not introduce an LLM planner, remote service, or private fixture.
