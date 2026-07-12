# aiyes

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-AGPL--3.0-blue) ![Tests](https://img.shields.io/badge/tests-pytest-green) ![Architecture](https://img.shields.io/badge/architecture-hexagonal-purple) ![Code style](https://img.shields.io/badge/code%20style-ruff-orange) ![Type checked](https://img.shields.io/badge/type%20checked-mypy-blue) ![Platform](https://img.shields.io/badge/platform-linux%20%7C%20android-blue) ![Status](https://img.shields.io/badge/status-beta-blue)

A local, deterministic CLI tool that gives AI agents eyes and hands for Linux and Android GUI inspection and control.

**aiyes** reads GUI state through accessibility trees and screenshots, and drives input through keyboard, mouse, and semantic actions. It does not reason, plan, or orchestrate workflows — the AI agent provides that. aiyes provides the state interface.

Current release posture: public beta for trusted local developer workflows. Linux/Xvfb remains the highest-fidelity backend. Android emulator/device support is included for observe-act-verify workflows and reports backend limitations explicitly where UIAutomator semantics are weaker than AT-SPI.

## How it works

```
session start -- firefox          # launch app in isolated X11 session
inspect                           # get accessibility tree + screenshot
find button "Submit"              # locate a node by role and name
action <node-id> click            # execute semantic action
screenshot --region 100,200,400,300   # capture a region
session stop                      # clean up
```

All commands return JSON to stdout. Errors go to stderr. Designed for machine consumption.

The `screenshot` response also carries `width` and `height` — the returned image's actual pixel dimensions (post-crop for a `--region`/`--node` capture, read from the returned file's own bytes, not the device/display size):

```json
{
  "path": "/home/user/.aieyes/<session-id>/screenshot.png",
  "width": 1280,
  "height": 800
}
```

The `width`/`height` keys are omitted when the dimensions cannot be read from the returned file's bytes (an unrecognized image format).

## 5-minute success path

Install and verify local dependencies:

```bash
pip install aiyes
aieyes doctor
```

Start a simple Linux GUI session, inspect it, act, verify, and clean up:

```bash
aieyes session start --name notes -- gedit /tmp/aiyes-demo.txt
aieyes inspect --no-screenshot --tree-depth 3
aieyes find textbox
aieyes action <node-id> focus
aieyes type "hello from aiyes"
aieyes inspect --no-screenshot
aieyes session stop
```

If a command returns a node ID such as `n_001`, use that concrete ID in the next command. See [examples/](examples/) for complete Linux, Android, and MCP walkthroughs.

## Trust model

aiyes is a trusted local tool. Run it only for commands, applications, devices, and MCP clients you trust.

- Linux sessions use Xvfb display isolation so the target app does not interact with your host desktop.
- This is not a sandbox for host data. The target app still runs as your user and can access files your user can access.
- The MCP server is local stdio integration for local agent clients. Do not expose `aieyes-mcp` to untrusted remote clients or network bridges.
- Android control uses adb against a device or emulator you authorize.

## Platforms

| Platform | Backend | How |
|----------|---------|-----|
| **Linux** | Xvfb + AT-SPI2 + xdotool | Isolated X11 session, works on any host (including Wayland) |
| **Android** | adb + UIAutomator | Physical or emulated device via Android Debug Bridge |

### Backend capability matrix

| Capability | Linux Xvfb + AT-SPI2 | Android adb + UIAutomator |
|------------|----------------------|----------------------------|
| Session start/stop/list/status | Yes | Yes |
| Session resize | Yes | No resize on Android |
| Accessibility tree inspect/find/wait | Yes | Yes, with fewer states than Linux |
| `wait-reactive` | Normalized AT-SPI native events where available, with source disclosure | Same command and result shape using adb foreground polling and UIAutomator snapshot polling; no helper APK |
| `diff` and `wait-stable` | Full tree-based support | Stable-ID normalized matching where fingerprints are available; falls back safely on volatile IDs |
| Semantic actions | AT-SPI2 actions when exposed by app | adb action ladder with method disclosure, currently node-bounds tap/input for node actions |
| Mouse/key/type/screenshot | Yes | Yes via adb input/screencap |
| Clipboard | Requires `xclip` | Uses adb clipboard strategy |
| Gesture/navigation | Limited Linux gestures | Android back/home/recent and gestures |
| Reliability dependency | App must expose useful AT-SPI metadata | App should expose text, content descriptions, resource IDs, or test tags for stable AI operation |

## Installation

```bash
pip install aiyes
```

### System dependencies

aiyes shells out to system tools. Run `aieyes doctor` to check what's installed.

**Linux backend:**

| Tool | Package (Fedora) | Package (Debian/Ubuntu) | Required |
|------|-------------------|--------------------------|----------|
| Xvfb | `xorg-x11-server-Xvfb` | `xvfb` | Yes |
| xdotool | `xdotool` | `xdotool` | Yes |
| xclip | `xclip` | `xclip` | For clipboard |
| scrot or ImageMagick | `scrot` or `ImageMagick` | `scrot` or `imagemagick` | Yes (either) |
| at-spi2-core | `at-spi2-core` | `at-spi2-core` | Yes |
| PyGObject | `python3-gobject` | `python3-gi` | Yes |
| AT-SPI GIR bindings | `gobject-introspection` | `gir1.2-atspi-2.0` | Yes |
| Mesa software rendering | `mesa-dri-drivers` | `libegl-mesa0` | For headless |

Fedora quick install:
```bash
sudo dnf install xorg-x11-server-Xvfb xdotool xclip ImageMagick at-spi2-core \
    python3-gobject gobject-introspection mesa-dri-drivers
```

Debian/Ubuntu quick install:
```bash
sudo apt install xvfb xdotool xclip imagemagick at-spi2-core \
    python3-gi gir1.2-atspi-2.0 libegl-mesa0
```

**Android backend:**

| Tool | Source | Required |
|------|--------|----------|
| adb | Android SDK platform-tools | Yes |
| Device | Physical or emulator with USB debugging | Yes |

## Commands

| Group | Commands | Purpose |
|-------|----------|---------|
| **session** | start, stop, list, resize, status, metrics, prune | Manage isolated display sessions |
| **inspect** | inspect, find, diff, wait, wait-reactive, wait-stable, detect-dialog | Read and query GUI state |
| **control** | action, mouse (move/click/drag/scroll), key, type, do | Drive input |
| **scenario** | scenario run | Run deterministic release scenario files and optional evidence bundles |
| **interaction** | clipboard (read/write), gesture (pinch/scroll), navigate, menu, goto, reload | Platform-specific actions |
| **browser DOM lens** | eval, query-dom, page-text, screenshot-selector | Firefox/Marionette CSS + JS + visual QA (launch-time opt-in) |
| **diagnostics** | doctor, debug-bundle, screenshot, mcp-manifest, help-json | System checks and introspection |

Run `aieyes --help` or `aieyes <command> --help` for full usage.

`goto <url>` and `reload` are **linux/AT-SPI + xdotool browser-session
primitives** (not available on the Android backend). `goto` is address-bar
automation — it locates the browser address bar (an `entry` whose accessible
name contains "address"), focuses it, selects all, types the URL and presses
Return; it is verified on Firefox and works for any AT-SPI-exposed address bar,
but is not a hard cross-browser guarantee. If the address bar cannot be located
or focused, `goto` reports a structured error and sends no keystrokes rather
than typing into the wrong control. `reload` performs a cache-bypassing hard
reload (Ctrl+Shift+R).

### Firefox DOM lens (`eval` / `query-dom` / `page-text` / `screenshot-selector`)

The DOM lens sees what the accessibility tree cannot — pure-visual `<div>`s,
computed CSS, and rendered prose — for CSS/visual QA of your own web apps. It is
a **launch-time opt-in**: start a Firefox session with `--marionette`
(`aieyes session start --marionette -- firefox`) and aieyes allocates a distinct
Marionette port per session and splices `-marionette` into the launch. The opt-in
cannot be retrofitted onto a running session — restart with `--marionette`. It is
firefox/linux only; a non-Firefox command with `--marionette` is rejected, and on
any non-marionette session the four lens commands return a structured
`status: "error"` naming the fix and perform zero browser I/O.

- `eval <script>` runs operator JavaScript in the page **content context** (never
  the privileged chrome context) and returns its JSON value; a bare expression is
  auto-wrapped so it yields a value, and a JavaScript exception maps to
  `status: "error"` (never a crash).
- `query-dom <css>` returns a measured, structured view of the matched elements —
  `getBoundingClientRect` plus a fixed 15-property computed-style subset,
  `classList`, and `textContent` — with the true match `count`, the node list
  capped at 50, and a `truncated` flag. An empty match is `status: "ok"`, not an
  error.
- `page-text [css]` reads rendered `innerText` (the whole `document.body` by
  default, or a scoped selector with a `found` flag).
- `screenshot-selector <css>` captures a native element screenshot (scrolled into
  view), stores it via the session screenshot store, and returns its `path` plus
  `width`/`height`.

Marionette state is discoverable on both surfaces: `session capabilities` (static:
does the backend support the lens) and `session status` (per-session runtime: is
**this** session marionette-launched, on what port) each report top-level
`marionette_enabled` and `marionette_port` (the port omitted when the session is
not marionette-launched).

`find` accepts two optional flags, `--within-name` and `--within-role`, that
restrict the search to the descendants of a matching ancestor (for example a
named section). They disambiguate identical results in different places: with
two separate "Add" buttons, one under a "Holidays" section and one under a
"Recurring holidays" section, `find push_button Add --within-role section
--within-name "Recurring holidays"` returns only the button inside that
section. `--within-name` is the same case-insensitive substring match used for a
node's name; `--within-role` is an exact role match; when both are given the
ancestor must match both. When a scope is requested the JSON result is an
envelope `{"nodes": [...], "scope_matched": <bool>, "matched_ancestors":
[{"id", "role", "name"}]}`: `scope_matched` is `false` (with empty
`matched_ancestors`) when no ancestor matched — a scoped miss, never a silent
whole-tree fallback — and distinguishes that miss from a matched section that
simply contained no results. Without these flags `find` is unchanged: it
searches the whole tree and returns a bare JSON array exactly as before. The
flags are independent options, not a selector language, and apply to `find`
only.

When an exact-role `find` with a name pattern matches zero nodes, yet the same
name matches under a *different* role (for example you asked for `View` but the
tappable is exposed as a `push_button`), the result carries an additive
`role_drift` field — `[{"id", "role", "name"}, ...]`, the same shape as
`matched_ancestors` — naming every node whose name matches under another role, in
document order (scoped finds report only candidates inside the requested scope).
`wait` surfaces the identical field, via the same detector, on a "never matched"
timeout. `role_drift` is a diagnostic only: it does not change what `find`/`wait`
match or select and never auto-selects a drifted node. The key is omitted
entirely when there is no drift.

`find` also accepts `--resource-id`, which selects nodes by their **exact**
Android resource-id (`viewIdResourceName`, e.g. `com.example.app:id/create`). It
is a full-string equality match, never a substring or regex: `--resource-id
com.x:id/add` matches `com.x:id/add` and never `com.x:id/add_extra`. It composes
(AND) with `role`, the name pattern, `--within-*`, and `--state`; an absent or
empty value applies no resource-id filter. This is an **Android-only** selector:
Linux/AT-SPI nodes carry no resource-id, so the field is empty there and the
filter matches nothing. Matched nodes surface a `resource_id` field in the
`find` and `inspect` JSON output so a caller can discover the stable identifier;
following the same compaction convention as the context fields, the key is
omitted entirely when empty (Linux/AT-SPI output is byte-identical to before).

## Release scenarios

`aieyes scenario run` executes deterministic scenario files for release-smoke
evidence. It is not an LLM planner: the model may run the command or inspect the
evidence, but `aiyes` only validates and executes declared steps.

Public fixtures are in [examples/scenarios/](examples/scenarios/):

```bash
aieyes scenario run --public-fixture examples/scenarios/linux-gedit-text.json
aieyes scenario run --public-fixture examples/scenarios/android-settings.json
```

See [Release Scenarios](docs/release-scenarios.md) for the secondary release
gate pattern, evidence bundle usage, and MCP parity follow-up.

## Maintainer release gate

This repository does not rely on public GitHub Actions for release decisions.
Actions should remain disabled for the public repository because external code
and GUI-control workflows are untrusted input.

Maintainer-local release checks are run from a trusted local checkout:

```bash
python -m pip install -e ".[dev]"
scripts/release-check.sh
```

The local gate runs lint, typecheck, full tests, package build/check,
dependency audit, and writes a CycloneDX SBOM to `dist/aiyes-sbom.cdx.json`.

## MCP server

An optional MCP (Model Context Protocol) server adapter is available:

```bash
pip install aiyes[mcp]
```

Register with Claude Code (user-level, available in all projects):
```bash
claude mcp add --scope user aieyes -- aieyes-mcp
```

Or project-level (current project only):
```bash
claude mcp add aieyes -- aieyes-mcp
```

This exposes all aiyes commands as MCP tools. Verify with `claude mcp list`.

`aieyes-mcp` is a local stdio server. It is intended for trusted local clients such as Claude Code running under your user account. Do not expose it through a remote server, socket bridge, or untrusted automation gateway.

## Architecture

Hexagonal (ports and adapters):

- **Domain** — Pure business logic, zero external dependencies (stdlib only)
- **Ports** — Protocol-based contracts (structural typing)
- **Adapters** — External integrations (AT-SPI2, adb, xdotool, etc.)
- **CLI** — Click-based command interface, single composition root

## License

AGPL-3.0-or-later. Copyright (c) 2026 Jeroen. See [LICENSE](LICENSE).
