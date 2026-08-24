# Feature request — capture-provenance manifest (anti-substitution binding for governance drives)

**Status:** feature request / discovery note (not yet contracted).
**Discovered:** 2026-08-24, driving the **socialzzz** Flutter Android app (emulator-5554) to live-verify a governed change (contract `SZZ-C6-PROD01-DUAL-EDITOR`) under a TDDv6 GUI-runtime gate.
**aiyes version at time of writing:** 0.1.2 (`aiyes`, the Python CLI) — note this is distinct from the Rust `sheepdog-bench/tddv6-aieyes` benchmark.
**Author:** Claude Code (orchestrator session).

---

## TL;DR

aiyes already supports everything needed to *drive* the app for governance verification — **session naming** (`session start --name`), the **android backend** (`--backend android --device-serial`), inspect/do/screenshot. What it does **not** emit is any **anti-substitution provenance** for the captures: no per-frame content hash, and no binding of a drive to the **identity of the build that was driven**. Because a governed GUI-runtime gate must prove *"these frames are real captures from **this reviewed build**, not fabricated and not from a different build,"* the agent currently falls back to **raw adb + a hand-rolled `capture_records` block** (per-frame sha256 + the installed APK's sha256 + timestamp + serial). That fallback exists **only** because aiyes doesn't produce this natively. Emitting it would let aiyes fully replace the adb fallback for governance-grade evidence.

## Why this came up

Two socialzzz contracts (`SZZ-C9-PRIMARY-ACTION-HIERARCHY`, `SZZ-C6-PROD01-DUAL-EDITOR`) each closed with a carried residual — `RES-C9-AIEYES-SESSION-ID` / `RES-C6-AIEYES-SESSION-ID` — because the cross-vendor reviewer (codex) demanded a verifiable **aiyes session id** in the runtime evidence as anti-substitution proof, and the drive was done via raw adb instead. The operator's steer (2026-08-24): *prefer aiyes to keep things in the flow and surface friction at the source, rather than institutionalize the adb workaround.* On investigation the "no session id" framing was **not** an aiyes gap (naming exists); the **real** gap is the one below.

## The gap (source-cited)

1. **Screenshots carry no content hash.** `aiyes screenshot` writes a bare PNG (`--output`) or base64; there is no emitted sha256 of the frame. (`src/aiyes/domain/use_cases/` screenshot path; `hashlib.sha256` in the tree is used only for scenario-name/payload hashing — `src/aiyes/adapters/scenario_use_case_executor.py:1832,1848` — not for capture provenance.)
2. **No binding of captures to the driven build's identity.** A session records the launch **command / package** (the `app` field surfaced by `aiyes session list`), but not the *artifact* that is running — for android, the installed package's **APK sha256** (or versionName/versionCode + signing-cert digest). Same package name, different build ⇒ indistinguishable from the evidence. `session_capabilities` explicitly notes "no helper APK is required" (`src/aiyes/domain/use_cases/session_capabilities.py:216`) — the driven build's hash is never captured.

Net: even a correctly-named aiyes session proves *a session existed*, not *which build it drove* or *that a given PNG is an unaltered capture from it*. A governance gate needs both.

## The request

Add an optional **capture-provenance manifest**, emitted per session/drive as a machine-readable artifact (e.g. `session_capture_manifest.json` under the session dir, or `--provenance <path>`), containing:

- **Build identity, captured at `session start`** (before any drive):
  - android backend: the installed package's **APK sha256** (`pm path <pkg>` → hash the returned base.apk), plus versionName/versionCode and the signing-cert digest (`dumpsys package <pkg>`); the device serial.
  - linux backend: sha256 of the driven binary/command.
- **Per-frame records** for every capture in the session: frame path, **sha256**, the aiyes op that produced it (`screenshot`/`inspect`/scenario step), and a timestamp.
- **Session binding:** the `session_id` and the `--name`, so evidence is traceable end-to-end.

That single manifest is exactly the anti-substitution binding a TDDv6 GUI-runtime gate (and a skeptical cross-vendor reviewer) needs — it makes an aiyes drive **self-verifying** and removes the reason to fall back to raw adb + a hand-rolled record.

## Acceptance sketch

- `aiyes session start --backend android --device-serial <s> --name <n> -- <launch>` writes a manifest stub recording build identity (APK sha256 + version + signer) at start.
- Each `screenshot`/`inspect` (and each scenario capture) appends a `{frame, sha256, op, ts}` record.
- `aiyes session stop` finalizes the manifest; it round-trips: re-hashing the referenced frames + re-reading the on-device APK reproduces the recorded hashes.
- A verifier can confirm: (1) each frame is unaltered (sha256), (2) all frames came from the build whose APK sha256 is recorded, (3) that APK sha256 equals the reviewed build's — with no manual capture_records.

## For reference — the manual workaround this would replace

`socialzzz/.tddv6/contracts/SZZ-C6-PROD01-DUAL-EDITOR/runtime_context.yaml` `capture_records:` block: each frame → `sha256` + `captured_at` + reviewed-APK `sha256` (`0312de9c…`) + `device_serial`. Hand-maintained today; would be an aiyes artifact under this request.
