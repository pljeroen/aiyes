# Golden Smoke Matrix

This matrix defines the maintained real-world smoke targets used to judge AIYES release readiness. A target is "golden" when it has a clear backend/toolkit purpose, a stable launch path, and an expected semantic capability that can be checked by CLI and MCP.

## Maintained Targets

| Target | Backend | Toolkit/provider | Purpose | Expected semantic capability |
|--------|---------|------------------|---------|------------------------------|
| `linux-gedit` | Linux | GTK/AT-SPI | Public control app for baseline Linux behavior | Non-empty AT-SPI tree, typed text flow, screenshot, cleanup |
| `android-settings` | Android | adb/UIAutomator | Public control app for baseline Android behavior | Non-empty UIAutomator tree, named node discovery, action, screenshot, cleanup |

## Capability Expectations

The minimum semantic capability for a passing golden target is:

- `inspect` returns a non-empty accessibility tree for the target backend.
- `find` returns at least one semantically useful or actionable node.
- One action or input command can be executed.
- A follow-up `inspect`, `wait`, or screenshot verifies that the app is still observable.
- `session stop` or equivalent cleanup succeeds without leaving active-session noise.
- The flow follows an observe-act-verify shape.

## Failure Classification

A control app failure is an AIYES-wide release blocker. Control apps represent backend baseline behavior; if `linux-gedit` or `android-settings` fails, treat the result as evidence that AIYES itself, its backend adapter, or local runtime assumptions need remediation before widening the audience.

Toolkit/app evidence is narrower. Maintainer-private or third-party application failures are not an AIYES-wide failure unless a public control app fails under the same backend. Recorded toolkit/app evidence should point to the application, toolkit accessibility layer, labels/semantics, or emulator/device state until a control app proves the backend itself is broken.

This distinction matters for public release notes: do not generalize a toolkit-specific empty tree into "AIYES cannot inspect Linux" or "AIYES cannot inspect Android" unless a control app fails.

## Evidence Commands

Use the opt-in harness for command-level evidence:

```bash
python -m aiyes.smoke_harness --target linux-gedit --output /tmp/aiyes-linux-gedit-smoke.json
AIYES_RUN_REAL_SMOKE=1 python -m aiyes.smoke_harness --target android-settings --output /tmp/aiyes-android-settings-smoke.json
```

Default harness runs are skipped evidence only. Real GUI/device execution requires `--run-real` or `AIYES_RUN_REAL_SMOKE=1`.
