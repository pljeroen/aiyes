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
| **interaction** | clipboard (read/write), gesture (pinch/scroll), navigate, menu | Platform-specific actions |
| **diagnostics** | doctor, debug-bundle, screenshot, mcp-manifest, help-json | System checks and introspection |

Run `aieyes --help` or `aieyes <command> --help` for full usage.

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
