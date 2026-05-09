# Release Smoke Checks

These checks record the manual end-to-end evidence expected before widening the audience. They are not a substitute for CI; they verify that a real GUI backend still behaves like the public examples claim.

The maintained target set and failure classification rules are defined in the
[Golden Smoke Matrix](golden-smoke-matrix.md).

For deterministic public scenario files and evidence-bundle usage, see
[Release Scenarios](release-scenarios.md). Those scenarios use only public
targets: `gedit` and Android Settings.

Before capturing release smoke evidence, remove stale session noise:

```bash
aieyes session prune --dry-run
aieyes session prune
aieyes session list --active-only
```

Expected result:

- `session prune --dry-run` shows only stale sessions that would be removed.
- `session prune` removes stale sessions and skips active sessions.
- `session list --active-only` shows only sessions that are currently alive.

## Linux smoke

Prerequisites:

- Linux system dependencies from the README are installed.
- `gedit` or another simple text editor is installed.

```bash
aieyes doctor
aieyes session start --name release-smoke -- gedit /tmp/aiyes-release-smoke.txt
aieyes inspect --no-screenshot --tree-depth 3
aieyes find textbox
aieyes action <node-id> focus
aieyes type "AIYES release smoke"
aieyes inspect --no-screenshot --tree-depth 3
aieyes session stop
```

Expected result:

- `aieyes doctor` returns JSON and shows the required Linux tools.
- `aieyes inspect` returns an accessibility tree.
- `aieyes find textbox` returns a node ID.
- The second inspect shows the edited text state or focused text entry state.
- `aieyes session stop` cleans up the session.

## Maintainer-only egui smoke

Maintainers may keep private egui application smoke checks locally. These checks
must not be committed as public scenario fixtures while the target application is
private. The public repository should use `examples/scenarios/linux-gedit-text.json`
for reproducible Linux scenario validation.

Passing criterion:

- The application starts and produces a valid screenshot.
- `inspect` returns a non-empty AT-SPI/AccessKit tree.
- `find` returns at least one actionable or semantically useful node.
- If the screenshot works but the tree is empty, record the result as an egui
  AccessKit/AT-SPI release blocker, not as a passed smoke.

Maintainer-only smoke evidence should still follow the same observe-act-verify
shape as the public `gedit` scenario: start, inspect, find, action/input,
verify, screenshot, and cleanup.

## Android smoke

Prerequisites:

- Android SDK platform-tools are installed.
- A trusted emulator or device is visible in `adb devices`.
- The app exposes useful text, content descriptions, resource IDs, or test tags.

```bash
adb devices
aieyes doctor
aieyes session start --backend android --device-serial emulator-5554 -- \
    adb -s emulator-5554 shell monkey -p com.example.app 1
aieyes inspect --no-screenshot --tree-depth 4
aieyes find button "Continue"
aieyes action <node-id> click
aieyes wait text "Home" --timeout 10
aieyes inspect --no-screenshot --tree-depth 4
aieyes session stop
```

Expected result:

- `adb devices` lists the target emulator or device.
- `aieyes doctor` reports adb and device status.
- `aieyes inspect` returns a UIAutomator tree.
- `aieyes wait` observes the expected post-click state.
- `aieyes session stop` cleans up the Android session record.

Android has lower semantic fidelity than Linux AT-SPI: no resize and fewer states. `diff` and `wait-stable` use stable-ID normalized matching when UIAutomator exposes enough resource/semantic data, and fall back safely when fingerprints are missing or ambiguous.

## Android Settings public scenario

Use `examples/scenarios/android-settings.json` for the public Android scenario.
It launches Settings with `android.settings.SETTINGS` and avoids private apps,
accounts, network dependencies, and credentials.

Passing criterion:

- `doctor` reports adb and an attached Android device.
- `session_start` or `aieyes session start` stores the package identity.
- `inspect` returns a non-empty UIAutomator tree with named or actionable nodes.
- `find` returns at least one actionable node.
- `action` returns `status: ok`.
- `screenshot` writes a valid PNG.
- Cleanup returns `stopped`; `stopped_with_errors` is a release blocker.

Maintainers may keep additional private Android app smoke checks locally. Those
fixtures must stay untracked until the target app itself is public.
