# aiyes Manual

## What aiyes is

aiyes is a local, deterministic CLI tool that gives AI agents (and humans) eyes and hands for GUI inspection and control on Linux and Android.

It reads GUI state through accessibility trees and screenshots, and drives input through keyboard, mouse, and semantic actions. It does not reason, plan, or orchestrate — the caller provides that. aiyes provides the state interface.

> **Package vs command name**: The Python package is `aiyes`. The CLI command is `aieyes`. Install with `pip install aiyes`, run with `aieyes`.

**Design principle**: observe, then act, then verify. Every interaction follows this loop:

```
session start -- <app>    # launch in isolated session
inspect                   # read current state
find/action/mouse/key     # do something
inspect                   # verify what changed
session stop              # clean up
```

All commands return JSON to stdout. Errors go to stderr as plain text. Exit code 0 = success, 1 = error.

---

## Concepts

### Sessions

A session is an isolated environment for running and inspecting a GUI application.

- **Linux**: creates a virtual X11 display (Xvfb) with its own AT-SPI2 bus. The host desktop is never touched.
- **Android**: connects to a device via adb. No isolation layer needed — the device is already separate.

Sessions are identified by a short hex ID (e.g., `9e53f0a7`). State is stored in `~/.aieyes/<session-id>/`.

When only one session exists, `--session` can be omitted. When multiple sessions are active, you must specify which one.

### Trust boundary

aiyes is a trusted local tool. Linux sessions isolate the display, not the filesystem or user account. A target application still runs as your user and can access files available to that user.

The MCP server is local stdio integration for trusted local agent clients. Do not expose `aieyes-mcp` to remote or untrusted clients.

### Accessibility tree

The accessibility tree is a structured representation of the GUI. Every visible element — buttons, text fields, menus, labels — appears as a node with:

| Property | Description |
|----------|-------------|
| `id` | Stable node identifier (session-scoped, path-based) |
| `role` | What the element is, in the text the backend reports: on Linux the AT-SPI role name, for example `button`, `entry`, `menu item` or `combo box` (as recorded with Firefox 159); on Android the widget class without its `android.widget.`, `android.view.` or `android.webkit.` prefix, for example `Button` or `EditText`. Commands that take a role also match it written with underscores instead of spaces, or as an alias (see [Role aliases](#role-aliases)) |
| `name` | Human-readable label (e.g., "Submit", "File", "Username") |
| `bounds` | Position and size as `[x, y, width, height]` |
| `states` | Current states: `focused`, `checked`, `enabled`, `selected`, etc. |
| `actions` | Available actions: `click`, `activate`, `set_text`, etc. |
| `value` | Current value (for text fields, sliders, etc.) |
| `description` | Accessible description as the toolkit reports it, shown only when it is not empty (Linux). Measured on Qt and GTK3: the description the app set or, when none is set, the widget's tooltip; on Qt the application node reports the Python interpreter path |
| `children` | Nested child nodes |

Context fields (populated when tree enrichment is enabled):
- `parent_role`, `parent_name` — parent node info
- `depth`, `index_in_parent`, `sibling_count` — position in tree

### Node IDs

Node IDs are deterministic within a session. They follow the format `n_001`, `n_002`, etc. — sequential counters assigned based on each node's role, name, and position in the tree. The same node always receives the same ID within a registry instance. Use them to target actions, screenshots, and queries.

### Role aliases

Commands that take a `role` argument accept both canonical AT-SPI2 role names and friendly aliases:

| Alias | Canonical |
|-------|-----------|
| `button` | `push_button` |
| `checkbox` | `check_box` |
| `textbox` | `text` |
| `radio` | `radio_button` |
| `tab` | `page_tab` |
| `toolbar` | `tool_bar` |
| `scrollbar` | `scroll_bar` |
| `combobox` | `combo_box` |
| `menuitem` | `menu_item` |
| `listitem` | `list_item` |
| `treeitem` | `tree_item` |
| `statusbar` | `status_bar` |
| `progressbar` | `progress_bar` |

Canonical names also work directly: `dialog`, `label`, `frame`, `panel`, `menu`, `menu_bar`, `separator`, `image`, etc.

### Tree pruning

By default, both `inspect` and `find` prune noise from the tree before they return or store it:
- Nodes with role `filler` or `redundant_object` are removed, whatever their name. Their children are kept: they move up to the removed node's parent, in the removed node's place and in their original order. The raw AT-SPI spelling `redundant object` (with a space) counts as `redundant_object`, so such nodes do not appear in default output either.
- Unnamed `section` nodes are collapsed (children moved up to the parent).
- Unnamed `panel` nodes are collapsed when exactly one child is left after their own children have been pruned. An unnamed panel left with no children, or with two or more, is kept.

Moved-up nodes keep their node IDs, so an ID found by a default `inspect` or `find` can be passed to `action`. `--tree-depth` counts depth in the pruned tree.

Named containers that pruning hides still count as scopes for `find --within-name` and `--within-role`: a node with role `filler` that has a name is a scope ancestor, although `find` output never shows it. On Qt, plain panels, tab widgets, tab pages, stacked pages and scroll areas are such nodes; a tab page is addressed by the page widget's accessible name, not by its tab title, which is a separate node, while on GTK3 the tab title node holds the page. Such a container can appear in `matched_ancestors` although default output does not show it, and `action` does not accept its ID after a default `find`.

Use `inspect --no-prune` to get the raw tree. The `find` command has no `--no-prune` option.

### Password masking

Nodes with role `password_text` have their `value` replaced with `***` in all output.

---

## Command reference

### Session management

#### `aieyes session start -- <command> [args...]`

Launch an application in a new isolated session.

| Option | Default | Description |
|--------|---------|-------------|
| `--resolution WxH` | `1280x800` | Display resolution |
| `--color-depth N` | `24` | Color depth |
| `--wait SECONDS` | `2.0` | Wait for app to initialize |
| `--name TEXT` | auto | Human-readable session name |
| `--backend linux\|android` | `linux` | Platform backend |
| `--device-serial TEXT` | — | Android device serial (required for android) |

```bash
aieyes session start -- firefox https://example.com
aieyes session start --resolution 1920x1080 -- gedit /tmp/test.txt
aieyes session start --backend android --device-serial emulator-5554 -- \
    adb -s emulator-5554 shell monkey -p com.example.app 1
```

#### `aieyes session stop [--session ID]`

Terminate a session and clean up resources.

#### `aieyes session list`

List all active sessions with their IDs, names, backends, and PIDs.

#### `aieyes session resize RESOLUTION [--session ID] [--settle SECONDS]`

Resize the display. `--settle` (default 0.5s) waits for the app to adapt.

#### `aieyes session status [--session ID]`

Check whether the app is alive, the display is running, and the session is healthy.

#### `aieyes session metrics [--session ID]`

Show operation counts, durations, and percentiles for the session.

#### `aieyes session prune [--older-than HOURS] [--dry-run]`

Remove stale session directories. Default: sessions older than 72 hours.

---

### Inspection

#### `aieyes inspect [OPTIONS]`

Read the current GUI state. Returns the accessibility tree and/or a screenshot.

| Option | Description |
|--------|-------------|
| `--session ID` | Target session |
| `--no-screenshot` | Skip screenshot capture |
| `--no-tree` | Skip tree inspection |
| `--tree-depth N` | Limit tree depth |
| `--no-prune` | Return raw tree without noise removal |
| `--screenshot-base64` | Include screenshot as base64 in JSON output |
| `--focus-window TEXT` | Focus a window by title before inspecting |

```bash
aieyes inspect
aieyes inspect --no-screenshot --tree-depth 3
aieyes inspect --screenshot-base64
```

#### `aieyes find ROLE [NAME_PATTERN] [--session ID] [--state STATE]`

Search the tree for nodes matching a role and optional name pattern.

`--description TEXT` selects by the node's whole accessible description (the `description` field). Upper/lower case and runs of whitespace are ignored; unlike the name pattern, a part of the description does not match. It narrows together with the role, the name pattern, `--state`, `--within-name`, `--within-role` and `--resource-id`, and an empty value means no description filter. A tooltip reported as the description is usually translated text, so addressing that stays stable across interface languages needs the app to set the accessible description. Selecting by description is not supported on Android sessions (see [Android limitations](#android-limitations)).

When a description query returns no node, without a scope or inside a scope that matched, and some nodes that pass the other selectors have a description containing the given text, the output is the object `{"nodes": [], ...}` with `descriptions_containing` (up to five of those whole descriptions, in tree order) and `descriptions_containing_count` (how many there are) after any `scope_matched`, `matched_ancestors` and `role_drift` keys. Each listed description, given as `--description` on the same tree, selects a node. A scope that matched nothing (`"scope_matched": false`) lists none.

```bash
aieyes find button "Submit"
aieyes find textbox --state focused
aieyes find menu_item "Save"
aieyes find combo_box --description "Shipping method"
```

#### `aieyes diff [--session ID]`

Compare the stored tree (from last `inspect`) against the live tree. Shows added, removed, and changed nodes. A change of a node's description alone is not reported.

#### `aieyes wait ROLE [NAME_PATTERN] [OPTIONS]`

Poll until a matching node appears (or disappears).

| Option | Default | Description |
|--------|---------|-------------|
| `--timeout SECONDS` | `30.0` | Give up after this long |
| `--state STATE` | — | Node must have this state |
| `--absent` | `false` | Wait for the node to disappear |
| `--transient` | `false` | Detect transient elements (toasts, snackbars) |
| `--description TEXT` | — | The node's whole accessible description must match, as for `find --description`. When the wait times out without ever selecting a node, the output lists the descriptions that contain the text after `role_drift`, as for `find`; it never does with `--absent` |

```bash
aieyes wait dialog "Save changes"
aieyes wait button "Submit" --state enabled --timeout 10
aieyes wait dialog "Loading" --absent
```

#### `aieyes wait-stable [OPTIONS]`

Wait for the accessibility tree to stop changing (useful after navigation or loading).

| Option | Default | Description |
|--------|---------|-------------|
| `--timeout SECONDS` | `10.0` | Max wait time |
| `--interval SECONDS` | `0.5` | Poll interval |
| `--consecutive N` | `3` | Required consecutive identical polls |

#### `aieyes wait-reactive CONDITION [NAME_PATTERN] [OPTIONS]`

Wait for a backend-neutral GUI condition using one command for Linux and
Android. The JSON result always uses the same fields: `condition`, `matched`,
`timeout`, `backend`, `source`, `elapsed_ms`, `polls`, `events`,
`failure_code`, and `next_actions`.

| Option | Default | Description |
|--------|---------|-------------|
| `--timeout SECONDS` | `10.0` | Give up after this long |
| `--quiet SECONDS` | `0.0` | Reserved quiet period for future stability gating |
| `--poll-interval SECONDS` | `0.25` | Polling interval for non-native sources |

Supported conditions: `screen-change`, `node-appears`, `node-disappears`,
`focus-change`, and `app-change`.

The `source` field tells the agent how the result was observed:
`native_event` for Linux AT-SPI native events, `adb_state_poll` for Android
foreground app/activity polling, `snapshot_poll` for tree snapshot deltas, or
`unsupported` when the backend cannot provide the requested condition.

```bash
# Linux and Android use the same agent-facing command shape.
aieyes wait-reactive node-appears "Submit" --timeout 10
aieyes wait-reactive screen-change --poll-interval 0.2
aieyes wait-reactive app-change --timeout 5
```

Android first-wave reactive waits do not use an AccessibilityService helper
APK, root, logcat event streaming, or privileged settings. They use adb-visible
foreground state and UIAutomator snapshots, so agents should trust the shared
result schema but still inspect `source` before assuming native event fidelity.

#### `aieyes detect-dialog [--session ID]`

Check if a new top-level window appeared since the last `inspect`.

---

### Control

#### `aieyes action NODE_ID ACTION_NAME [VALUE] [--session ID]`

Execute a semantic accessibility action on a node.

```bash
aieyes action n_003 click
aieyes action n_001 set_text "hello world"
aieyes action n_005 toggle
```

The available actions depend on the node. Check the `actions` field in the tree.

On Linux, `action` does not send an action to a node that is not enabled (a greyed-out widget). It returns `"status": "error"` with a reason saying the node is not enabled, for example `"reason": "Action 'click' not sent: node 'n_003' is not enabled"`. This holds whatever action is requested, also when the node does not offer that action or offers no actions (some toolkits, for example Qt, list no actions for a widget that is not enabled). `set_text` on a text field that is not enabled is refused the same way, and the text is not applied.

When the app is asked to act but declines, `action` returns `"status": "error"` with a reason saying the action was declined, for example `"reason": "Action 'focus' on node 'n_007' was declined by the application"`. `focus` on a node that cannot take focus, such as a menu item, is reported this way. An action that an enabled node does not offer is reported as not available. In all these cases the exit code is 0. For a node that is not enabled and for a declined action, the error also lists the node's actions in `"available_actions"`, as an empty list when the node offers none.

`"status": "ok"` does not prove that the app acted on the request. On Linux it means the app accepted the request, and an enabled node that accepts a request but does not act on it is reported `"status": "ok"`. For example, `set_text` on a read-only text field is reported `"status": "ok"`: some apps (GTK3) accept it without changing the text, others (Qt Widgets) apply it. aiyes does not check whether a field is editable or read-only.

#### `aieyes mouse move X Y [--session ID]`

Move the mouse cursor to absolute coordinates.

#### `aieyes mouse click [X Y] [--x X --y Y] [--session ID] [--button left|middle|right]`

Click at coordinates (or current position if omitted). Coordinates can be given
as positional arguments or as named options:

```bash
aieyes mouse click 540 960                          # positional form
aieyes mouse click --x 540 --y 960                  # named form
aieyes mouse click --x 540 --y 960 --button right   # named with button
aieyes mouse click                                   # click at current position
```

When using `--x`/`--y`, both must be provided. Positional and named forms cannot
be combined in the same invocation.

#### `aieyes mouse drag X1 Y1 X2 Y2 [--session ID]`

Click-drag from one point to another.

#### `aieyes mouse scroll DIRECTION [AMOUNT] [--session ID]`

Scroll in a direction (`up`, `down`, `left`, `right`). Default amount: 3.

#### `aieyes key KEYS... [--session ID]`

Send key events. Key names follow xdotool conventions.

```bash
aieyes key Return
aieyes key ctrl+s
aieyes key ctrl+a ctrl+c
aieyes key Tab Tab Return
```

#### `aieyes type TEXT [--session ID]`

Type text character by character (handles special characters properly).

```bash
aieyes type "Hello, world!"
```

#### `aieyes do --role ROLE --name NAME --action NAME [OPTIONS]`

Compound command: find a node, execute an action, optionally verify.

| Option | Description |
|--------|-------------|
| `--role ROLE` | Accessibility role to match, or `*` for any role |
| `--name TEXT` | Substring to match in the node name |
| `--action NAME` | Action to execute on found node |
| `--value TEXT` | Value to pass to action |
| `--verify` | Re-inspect after action to verify state change |
| `--timeout SECONDS` | Timeout for the find phase |
| `--description TEXT` | The node's whole accessible description must match, as for `find --description`; the action goes to the first matching node in tree order. When no node is found, the output lists the descriptions that contain the text after `"error"`, as for `find` |

```bash
aieyes do --role button --name Submit --action click --verify
aieyes do --role textbox --name Username --action set_text --value "admin"
```

Whenever the `"action"` object of the output has `"status": "error"`, it also has a `"reason"` with the same text `aieyes action` gives for that case: the node is not enabled, the app declined the action, or the action is not available on the node. For example:

```json
"action": {"status": "error", "action": "click", "target": "n_009", "reason": "Action 'click' not sent: node 'n_009' is not enabled"}
```

The exit code is 0 in these cases. A `"reason"` is given on every platform; on Linux it can also say that the node is not enabled or that the app declined the action.

`"status": "ok"` in the `"action"` object does not prove that the app acted on the request, as with `aieyes action`. On Linux, an enabled node that accepts a request but does not act on it is reported `"status": "ok"`, with no `"reason"`; for example `set_text` on a read-only text field that the app (GTK3) accepts without changing the text.

#### `aieyes screenshot [OPTIONS]`

Capture a screenshot.

| Option | Description |
|--------|-------------|
| `--session ID` | Target session |
| `--output PATH` | Save to specific file |
| `--base64` | Return as base64 string |
| `--region X,Y,W,H` | Crop to rectangle |
| `--node NODE_ID` | Crop to node bounding box |

```bash
aieyes screenshot
aieyes screenshot --region 100,200,400,300
aieyes screenshot --node n_004 --base64
```

---

### Interaction

#### `aieyes clipboard read [--session ID]`

Read current clipboard contents.

#### `aieyes clipboard write TEXT [--session ID]`

Write text to the clipboard.

#### `aieyes navigate back|home|recent [--session ID]`

Platform-abstracted navigation. Primarily useful on Android (`back` = device back button, `home` = home, `recent` = app switcher).

#### `aieyes menu MENU_PATH [--session ID]`

Traverse a menu by dot-separated path.

```bash
aieyes menu File.Save
aieyes menu Edit.Preferences
aieyes menu View.Zoom.200%
```

Each segment is opened in turn, and the item named by the last segment is activated as if clicked. This changes the app's state: `aieyes menu File.Save` saves.

Each segment after the first is looked up only inside the menu named by the segment before it, so a same-named item in another menu is never used. `Window.Close` activates the `Close` item of the `Window` menu even when `File` also has a `Close` item. A path whose item exists only in another menu fails as not found: `Window.Save` fails when only `File` has a `Save` item.

On failure the command prints an error and exits with code 1:

- An item that cannot be found: the error names the missing segment and the path, for example `Menu item not found: 'Save' in path 'Window.Save' — the submenu may not have appeared yet`.
- An enabled item that is found but cannot be activated, for example because it offers none of the actions `menu` sends (`click`, `Press`, `ShowMenu`): the error names the item, the path, the action that was tried and the actions the item offers, for example `Menu item 'Save' in path 'File.Save' could not be activated: action 'click' on node 'n_005' did not succeed (available actions: 'SetFocus')`.
- An item that is not enabled, such as a disabled item, whatever actions it offers: the action is not sent, and the error names the item, the path and that the node is not enabled, for example `Menu item 'Save' in path 'File.Save' could not be activated: node 'n_005' is not enabled, so action 'click' was not sent (available actions: 'click')`.
- An item whose activation the app declines: the error names the item, the path and that the action was declined by the application.

In every case the segments before the failing one may already have been opened.

`menu` does not check the effect of an activation. If an enabled item offers the action and the app accepts it but does not act on it, no error is reported: `menu` goes on with the next segment as if the item had been activated, and when this happens on the last segment it prints `"status": "ok"` and exits with code 0. Other requests that an enabled node accepts without acting on are reported the same way (see `aieyes action`).

In some apps the opened menu can stay on screen after the item is activated. This has been observed in Qt Widgets apps.

#### `aieyes gesture pinch X Y SCALE_FACTOR [--session ID]`

Pinch gesture (Android only). Scale > 1 = zoom in, < 1 = zoom out.

#### `aieyes gesture two-finger-scroll X Y DIRECTION [AMOUNT] [--session ID]`

Two-finger scroll gesture (Android only).

---

### Diagnostics

#### `aieyes doctor`

Check system dependencies. Returns JSON with each dependency's status (`pass`, `warn`, `fail`), message, and category (`linux` or `android`).

```bash
aieyes doctor
```

#### `aieyes debug-bundle [--session ID]`

Collect a redacted diagnostic bundle for support/debugging. The bundle summarizes session metadata, doctor output, operation counts, stored tree counts, and screenshot availability. It does not copy arbitrary user files, and sensitive environment values are redacted.

#### `aieyes mcp-manifest`

Return machine-readable capability disclosure for AI tools. Describes what aiyes can do, its backends, and its command structure.

#### `aieyes help-json`

Return all commands and their parameter schemas as JSON. Useful for dynamic tool registration.

#### `aieyes --version`

Print version and exit.

---

## Output format

All successful commands return JSON to stdout. Examples:

**session start**:
```json
{
  "session_id": "9e53f0a7",
  "backend": "linux",
  "display": ":99",
  "resolution": "1280x800",
  "pid": 12345
}
```

**inspect** (abbreviated):
```json
{
  "tree": {
    "tree": [
      {
        "id": "n_001",
        "role": "frame",
        "name": "My App",
        "bounds": [0, 0, 1280, 800],
        "states": ["active", "showing"],
        "actions": ["activate"],
        "children": [
          {
            "id": "n_002",
            "role": "button",
            "name": "Submit",
            "bounds": [100, 200, 80, 30],
            "states": ["enabled", "focusable", "showing"],
            "actions": ["click"]
          }
        ]
      }
    ]
  },
  "screenshot": "/home/user/.aieyes/9e53f0a7/screenshot.png",
  "timestamp": "2026-04-05T12:00:00"
}
```

**find**:
```json
[
  {
    "id": "n_002",
    "role": "button",
    "name": "Submit",
    "bounds": [100, 200, 80, 30],
    "states": ["enabled", "focusable", "showing"],
    "actions": ["click"]
  }
]
```

The role values in the **inspect** and **find** samples, `frame` and `button`, are role names as AT-SPI reports them on Linux (as recorded with Firefox 159). Role values are the text the backend reports, so they can differ between platforms and toolkits (see `role` under [Accessibility tree](#accessibility-tree)).

**action (success)**:
```json
{
  "status": "ok",
  "action": "click",
  "target": "n_002"
}
```

**action (failure)**:
```json
{
  "status": "error",
  "action": "set_text",
  "target": "n_002",
  "reason": "Action 'set_text' not available on node 'n_002'",
  "available_actions": ["click", "activate"]
}
```

**doctor**:
```json
[
  {"name": "xvfb", "status": "pass", "message": "found: /usr/bin/Xvfb", "category": "linux"},
  {"name": "xdotool", "status": "pass", "message": "found: /usr/bin/xdotool", "category": "linux"},
  {"name": "adb", "status": "warn", "message": "adb not found in PATH", "category": "android"}
]
```

**Errors** go to stderr as plain text:
```
Error: Session not found: abc123
Error: Multiple sessions found, specify one: ['9e53f0a7', 'bd3cbe26']
```

---

## For AI agents

This section describes how to use aiyes effectively as an AI tool.

### Integration pattern

aiyes is a CLI tool. Call it via shell execution. Parse stdout as JSON.

```python
import json
import subprocess

result = subprocess.run(
    ["aieyes", "inspect", "--session", session_id],
    capture_output=True, text=True
)
if result.returncode == 0:
    data = json.loads(result.stdout)
    tree = data["tree"]
    screenshot = data.get("screenshot")
else:
    error = result.stderr
```

### MCP integration

If your agent framework supports MCP (Model Context Protocol):

```bash
pip install aiyes[mcp]
```

**Claude Code** — register the server so all sessions can use it:

```bash
# User-level (available in all projects):
claude mcp add --scope user aieyes -- aieyes-mcp

# Or project-level (current project only):
claude mcp add aieyes -- aieyes-mcp

# Verify:
claude mcp list
```

**Other MCP clients** — point your client at the stdio server:

```bash
aieyes-mcp  # starts stdio MCP server, speaks JSON-RPC over stdin/stdout
```

All aiyes commands become available as MCP tools over local stdio. Use `aieyes mcp-manifest` and `aieyes help-json` for dynamic tool discovery. Do not expose this local control surface to untrusted remote clients.

### Android limitations

Android support is intended for adb-connected emulators and trusted devices. It is useful for AI-assisted inspection and control, but it is not equivalent to the Linux AT-SPI backend.

- Session resize is not supported on Android.
- UIAutomator reports fewer states than Linux AT-SPI.
- `wait-reactive`, `wait-stable`, and `diff` have restricted accuracy on Android.
- `wait-reactive` uses adb/UIAutomator polling on Android; it does not provide
  true native accessibility event subscription in this wave.
- Reliable targeting depends on app-provided text, `contentDescription`, resource IDs, or Compose semantics/test tags.
- Some text clearing and custom widgets can require fallback input strategies.
- Selecting by description is not supported on Android sessions: `--description` or `description_pattern` with a value that is not empty or blank is refused with an error, and nodes carry no `description` key. A node without text is named by its content description, so select it by name instead: `NAME_PATTERN` on `find` and `wait`, `--name` on `do`, `name_pattern` in MCP calls and scenario steps.

### Recommended workflow

1. **Start a session**: `aieyes session start -- <command>`
2. **Inspect**: `aieyes inspect` to get the tree and screenshot
3. **Reason**: analyze the tree to find the target element
4. **Act**: use `action`, `mouse`, `key`, `type`, or `do`
5. **Verify**: `aieyes inspect` again to confirm the action worked
6. **Repeat** steps 2-5 as needed
7. **Stop**: `aieyes session stop`

### Tips for effective use

- **Prefer semantic actions over mouse clicks.** `action NODE_ID click` is more reliable than `mouse click X Y` because it works regardless of layout changes.
- **Use `find` before `action`.** Find the node, get its ID, then act on it. The `do` compound command combines these steps.
- **Use `wait` after navigation.** After clicking a link or opening a dialog, `wait` for the expected element to appear before proceeding.
- **Use `wait-stable` after loading.** When the UI is loading or animating, `wait-stable` detects when the tree stops changing.
- **Use `diff` to detect changes.** After an action, `diff` shows exactly what changed in the tree.
- **Use `detect-dialog` for popups.** After an action that might trigger a dialog, check before continuing.
- **Prefer `--screenshot-base64` for inline analysis.** When you need to see the screenshot in your response, use base64 encoding to avoid filesystem round-trips.
- **Use `--tree-depth` for large apps.** Limit depth to focus on the relevant part of the tree.
- **Use `inspect --no-prune` when debugging.** If expected nodes are missing from `inspect` or `find` output, pruning might be hiding them; `inspect --no-prune` shows the raw tree.

### App requirements

For aiyes to work well, the target application must expose its GUI through the accessibility tree:

**Linux (AT-SPI2)**:
- GTK, Qt, and Electron apps generally work out of the box
- Set accessible names on interactive widgets
- Expose roles, states, values, and actions
- Keep focus and state changes observable
- Don't hide important UI outside the accessibility tree

**Android (UIAutomator)**:
- Set `contentDescription` on interactive views
- Provide text labels for `ImageButton` and `ImageView` widgets
- Keep accessibility events firing for state changes
- Compose: use `Modifier.semantics` / `Modifier.testTag` for stable IDs
- View: assign `resource-id` and `contentDescription` for stable identification

### Error handling

- Exit code 0 + JSON on stdout = success
- Exit code 1 + text on stderr = error
- Semantic failures (e.g., action not available) still return exit 0 with `"status": "error"` in JSON
- The exit-0 rule for semantic failures applies to the standalone commands: in a scenario run, an action step whose action reports `"status": "error"` fails the step and the run (exit 1) unless the step's `"expect"` field allows that outcome (see `docs/release-scenarios.md`).
- Always check both exit code and JSON status field

### Platform differences

| Capability | Linux | Android |
|-----------|-------|---------|
| Session isolation | Xvfb virtual display | Device (already isolated) |
| Tree source | AT-SPI2 | UIAutomator |
| Mouse input | xdotool | adb input tap/swipe |
| Keyboard input | xdotool | adb input text/keyevent |
| Screenshots | scrot / ImageMagick | adb screencap |
| Clipboard | xclip | adb broadcast |
| Gestures | Not available | adb motionevent |
| Navigation | Not applicable | back / home / recent |
| Session resize | Supported | Not supported |
| Menu traversal | AT-SPI2 menu walk | Not supported |
| Reactive waits | AT-SPI native events where available | adb/UIAutomator polling with the same command/result shape |
