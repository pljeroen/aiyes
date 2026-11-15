# Changelog

## [Unreleased]

### Fixed

- **Tree pruning keeps the children of removed nodes**: default pruning in `inspect` and `find` removed `filler` and `redundant_object` nodes together with everything under them, so widgets inside such containers (for example in Qt Widgets apps) were missing from default output and could not be found. The children of a removed node are now kept and moved up to its parent. Nodes reported with the raw AT-SPI role `redundant object` (with a space) are now removed too, with their children moved up the same way; before, they stayed in default output.
- **Visible changes for callers of default (pruned) output**: the output of `inspect` and `find`, and the tree they store for later `action` by node ID, `diff`, `menu` and `screenshot` by node, change accordingly:
  - Apps with such containers show more nodes, so output is larger. `find` can return more matches, so result counts and the first match can change. Context fields (`parent_role`, `parent_name`, `depth`, `index_in_parent`, `sibling_count`) of nodes next to or below a removed node can change.
  - IDs of moved-up nodes are now accepted by `action` after a default `inspect` or `find`.
  - `redundant object` wrapper nodes no longer appear: a default `find` for role `redundant_object` now returns nothing, and their IDs are no longer accepted by `action` after a default `inspect` or `find`. `inspect --no-prune` still shows them.
  - An unnamed `panel` is collapsed only when exactly one child is left after pruning, and that count now includes moved-up children, so an unnamed panel can newly appear or disappear in default output.
  - The first `diff` after a default `inspect` or `find` no longer reports moved-up nodes as added, and now reports `redundant object` nodes as added.
- **`menu` finds menu bars and menu items on Linux desktop apps**: `menu` matched node roles only in their underscore spelling (`menu_bar`, `menu_item`), while Linux desktop apps report them with spaces (`menu bar`, `menu item`, `check menu item`, `radio menu item`), so paths naming such nodes failed with "Menu item not found". Menu bars, menus and menu items are now found in either spelling.
- **Visible changes for callers of `menu`** (CLI command and MCP tool):
  - Paths that failed with "Menu item not found" now activate the item they name. This is a state-changing action for callers that treated the failure as a no-op.
  - When a found item cannot be activated, for example because it offers none of the actions `menu` sends (`click`, `Press`, `ShowMenu`), `menu` now exits with an error naming the item and the actions it offers, and acts on no later segment. Before, it reported `"status": "ok"`. `menu` still does not check the effect of an activation: an enabled item that offers the action and accepts it, but that the app does not act on, is treated as activated, so `menu` goes on with the next segment and can still report `"status": "ok"`. Items that are not enabled, and activations the app declines, now fail (see the entry below).
  - Items that offer `Press` or `ShowMenu` instead of `click` (Qt Widgets apps) are now activated with that action. Items that offer `click` still receive `click`.
  - Every segment after the first is looked up only inside the menu named by the segment before it. A path whose item exists only in another menu now fails as not found instead of activating the other menu's item, and a submenu name shared by several menus now resolves to the one inside the named menu.
- **Linux actions on nodes that are not enabled, and actions the app declines, are reported as errors**: on Linux, `action`, `do`, `goto` and `menu` reported `"status": "ok"` for an action on a node that is not enabled (a greyed-out widget) and for a request the app declined. A node whose accessibility state is neither enabled nor sensitive is now refused without sending the action, with an error saying it is not enabled, and a request the app declines is reported as declined.
- **Visible changes for callers of `action`, `do`, `goto` and `menu` on Linux** (CLI commands and MCP tools):
  - In these cases `action`, `do` and `goto` exit with code 0 and report `"status": "error"` with a reason saying the node is not enabled or that the app declined the action, for example `Action 'click' not sent: node 'n_003' is not enabled`; the MCP tools return the same JSON. `action` also lists the node's actions in `"available_actions"`, as an empty list when the node offers none. `menu` exits with code 1 and an error naming the item, the path and the cause, and acts on no later segment; the MCP tool returns an error result.
  - Results that scripts and MCP clients receive change accordingly. `focus` on a node that cannot take focus, for example a menu item, now fails as declined. `set_text` on a text field that is not enabled, which some apps (Qt Widgets, GTK3) applied, now fails with "not enabled" and leaves the field unchanged.
  - The `"action"` object of `do` now carries a `"reason"` whenever it reports `"status": "error"`, with the same text `action` gives for that case. This includes an action that is not available on the node, for which `do` gave no reason before. This `do` change applies on every platform, not only Linux.
  - Scenario `action` steps record the error and its reason in their output, but their pass/fail outcome is unchanged: the step still passes. An `action_ok` assertion on the step's output fails and catches the error.
  - `"status": "ok"` still means that the app accepted the request, not that it acted on it: an enabled node that accepts a request but does not act on it is still reported `"status": "ok"`. For example, `set_text` on a read-only text field that the app (GTK3) accepts without changing the text is reported `"status": "ok"`.

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
