# Changelog

## [0.2.0] - 2026-05-08

### Added

- **Public beta release gate**: clean SSH clone, wheel install, `doctor`, and real Linux/Android public scenarios now pass against the maintained fixtures.
- **Scenario preflight and fixture discovery**: `scenario preflight` and `scenario fixtures` are available through CLI and MCP with stable machine-readable output.
- **Scenario evidence manifests**: scenario evidence bundles now include `manifest.json` with mode, status, primary files, and inspection order.
- **Scenario failure taxonomy**: scenario validation, prerequisite skips, executor failures, assertion failures, and evidence path rejections report stable `failure_code` and `next_actions`.
- **Android live capability probes**: `session capabilities --live` now checks device/package-level Android readiness instead of only static backend support.
- **Android stable node identity**: UIAutomator nodes now carry persisted stable IDs based on resource ID, class, label, bounds, and tree path to reduce volatile ID churn.
- **Android empty-tree diagnostics**: `inspect` now reports structured evidence such as foreground package, UIAutomator dump status, screenshot status, and semantics guidance when an Android tree is empty.
- **Android normalized diff**: `diff` can match Android nodes by stable fingerprint before falling back to volatile node IDs.
- **Android normalized wait-stable**: `wait-stable` uses stable fingerprints on Android and reports `comparison_mode` in CLI/MCP output.
- **Android action ladder disclosure**: action results can include `action_method`, with Android node-bound adb actions reporting `node_bounds_tap`.

### Changed

- Release posture is now public beta for trusted local developer workflows after real public Linux and Android scenario validation.
- Public Linux and Android release fixtures are compatible with current Fedora gedit and Android Settings UIAutomator semantics.
- Android CLI and MCP behavior is closer to Linux AT-SPI parity for observe-act-verify workflows, especially around diagnostics, diffing, stability checks, and action result transparency.

## [0.1.2] - 2026-04-06

### Fixed

- **Multiline node name matching**: Names with newlines (e.g., Flutter's `"Home\nTab 1 of 4"`) now match patterns like `"Home Tab"`. All whitespace normalized before comparison across find, wait, do, menu, and window filtering.
- **MCP wait-stable parity**: MCP server now forwards `tolerance`, `ignore_node`, and `changes` to the wait-stable use case (was missing since AIYES-38).
- **None name defense**: Nodes with `None` name no longer crash matching code.

## [0.1.1] - 2026-04-06

### Fixed

- **Flutter/Android XML parse failure**: Strip junk after `</hierarchy>` in uiautomator dump output. Unblocks inspect, find, do, wait-stable, and diff on Flutter apps.
- **Android type drops characters**: New `--delay` option (milliseconds between keystrokes) for reliable text input on Android emulators and devices.
- **mouse click**: Added `--x`/`--y` named argument form alongside existing positional `X Y`.
- **wait-stable**: Added `--tolerance N` (allow minor tree churn), `--ignore-node ID` (exclude subtrees from stability check), and diagnostic `changes` output on timeout.

## [0.1.0] - 2026-04-05

Initial alpha release. Linux and Android GUI inspection and control for AI agents.

### Core

- Session lifecycle: start, stop, list, status, resize, metrics, prune
- Accessibility tree inspection with depth limiting and noise pruning
- Screenshot capture with region cropping and node targeting
- Node search by role, name pattern, and state filters
- Tree diffing against stored snapshots
- Wait conditions: node presence, absence, transient detection, tree stability
- Dialog/window detection
- Compound find-action-verify command (`do`)

### Input control

- Semantic accessibility actions (click, focus, set_text, etc.)
- Mouse: move, click, drag, scroll
- Keyboard: key events and text typing
- Clipboard: read and write
- Menu traversal by dot-separated path
- Platform navigation: back, home, recent (Android)
- Multi-touch gestures: pinch, two-finger scroll (Android)

### Backends

- **Linux**: Xvfb isolated X11 sessions, AT-SPI2 accessibility tree, xdotool input, scrot/ImageMagick screenshots
- **Android**: adb + UIAutomator accessibility tree, adb input, screencap

### Infrastructure

- Hexagonal architecture with mechanically enforced domain purity
- Operation logging with metrics and duration tracking
- Credential stripping from subprocess environments
- Password masking in accessibility tree output
- `doctor` command for system dependency verification
- MCP server adapter for Model Context Protocol integration
- `help-json` command for machine-readable command schema
- pytest plugin with `gui_runtime` marker for gated integration tests
- `--version` flag on CLI
