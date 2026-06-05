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

## MCP Parity

MCP exposes the same release-scenario surfaces as the CLI:

- load and validate a scenario document,
- reject unsafe public fixture references when requested,
- run declared steps deterministically,
- return the same normalized run result,
- optionally write the same evidence bundle policy,
- preserve the trusted-local stdio MCP threat model.

It must not introduce an LLM planner, remote service, or private fixture.
