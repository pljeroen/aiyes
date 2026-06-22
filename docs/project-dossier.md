---
project: aiyes
tier: 1
curation_status: curated
evidence_sha256: 73f66a5bff4c719fb2170ddb2c7f8ff22643157507297ef00a42d9874e0848de
harvested_at: 2026-06-22T01:02:55Z
reviewed_by: null
sign_off: null
---
# Project Dossier — aiyes

> **Doc-register runbook (FR-11 / C-DOCREG-01):** when the target repo has pre-commit hook infrastructure, its hook MUST invoke `_assert_no_secret` on `docs/project-dossier.md` before commit, so no known-host secret is ever committed through the dossier. This is note-only guidance (OVR-01) until the hook exists.

## Charter

**aiyes** is a local, deterministic CLI tool that gives AI agents "eyes and hands" for Linux and Android GUI inspection and control. It reads GUI state through accessibility trees (AT-SPI2 on Linux, UIAutomator on Android) and screenshots, and drives input through keyboard, mouse, and semantic accessibility actions. It deliberately does **not** reason, plan, or orchestrate — the AI agent supplies the intelligence; aiyes supplies the state interface. All commands emit JSON to stdout for machine consumption, with a separate MCP (Model Context Protocol) server adapter exposing every command as a tool for local agent clients such as Claude Code.

The project is a single-maintainer effort (Jeroen), AGPL-3.0-or-later, Python 3.10+, built on a hexagonal architecture (pure stdlib domain, Protocol-based ports, external adapters) with mechanically enforced domain purity. Its current posture is **public beta (v0.2.0, 2026-05-08)** for trusted local developer workflows. Linux/Xvfb is the highest-fidelity backend; Android emulator/device support is included for observe-act-verify workflows, with backend limitations reported explicitly where UIAutomator semantics are weaker than AT-SPI.

**Objective:** be a trustworthy, deterministic GUI state-and-control interface that an AI agent can rely on across Linux and Android, with release decisions gated by a maintainer-local release check (lint, typecheck, full tests, build, dependency audit, SBOM) rather than public CI.

## Scope / Deliverables

In scope (delivered):
- **Session lifecycle** — isolated Xvfb display sessions (start/stop/list/status/resize/metrics/prune) and Android device/emulator sessions via adb.
- **GUI inspection** — accessibility tree inspect/find/diff, dialog detection, and a family of wait conditions (presence, absence, stability, reactive event waits).
- **Input control** — semantic accessibility actions, mouse (move/click/drag/scroll), keyboard (key/type), clipboard, menu traversal, Android navigation and gestures, and a compound find-act-verify `do` command.
- **Release scenarios** — a deterministic scenario runner (`scenario run`) executing declared step files with assertions, evidence bundles + manifests, public fixtures, preflight, and a stable failure taxonomy. Explicitly **not** an LLM planner.
- **MCP server** — local stdio adapter exposing all commands with machine-readable schemas and trust-boundary metadata.
- **Diagnostics** — `doctor`, `debug-bundle`, `help-json`, `mcp-manifest`, structured empty-tree and selector diagnostics.

Out of scope (by decision): LLM planning/orchestration; multi-tenant or host-data sandboxing (it is a trusted-local tool, not a sandbox); public GitHub Actions in the release trust boundary; automated PyPI publishing (deferred).

## Decision Log
_Sources: docs/specs/*/decisions.yaml (8 spec packages), SECURITY.md threat model. The Redmine Decision Log issues carry the per-decision detail._

Most architecturally significant accepted decisions: build a deterministic scenario runner rather than an LLM planner (D-01); split release work into many small TDDv6 contracts rather than one omnibus contract (D-05); keep Android in scope as supported-with-explicit-limitations (D-02); defer PyPI publishing automation (D-03); add one unified reactive-wait operation rather than separate Linux/Android commands; implement Android reactive waits via adb-visible polling rather than a helper APK; replace Android host-PID lifecycle with a first-class app-lifecycle model; keep MCP trusted-local but add bounded inputs + safe file-output policy.

## Risk Register
_Sources: per-contract RESIDUAL_RISK_ACCEPTANCE.yaml, docs/improvements-flutter-driving.md, SECURITY.md, the 2026-04-12 release assessment. The Redmine Risk Register issues carry the per-risk detail._

Headline risks: (1) Android/Flutter accessibility fidelity is weaker than Linux AT-SPI — lazy lists, role drift (tappables exposed as `Button`), and blind-swipe `scroll_into_view` remain partly worked-around at the scenario layer (open backlog, `docs/improvements-flutter-driving.md`). (2) Trust-model risk: aiyes launches arbitrary commands and is **not** a host-data sandbox; Xvfb isolation prevents app→host-desktop escape only. (3) Legacy scenarios authored under the old finger-direction scroll convention will scroll opposite after the AIYES-96 view-direction unification (accepted, documented). (4) MCP exposure to untrusted remote clients is explicitly out of bounds. Most per-contract residual risks are accepted at LOW/MEDIUM with documented rationale.

## Schedule / Sequence
_Sources: real git history (commit dates only — no invented dates). Public git history begins 2026-05-09 (pre-public history was sanitized); contract artifact dates (2026-03-22 onward) cover the pre-public development._
- commit `e5a5ea1a92c6` date=2026-06-13T23:08:42+02:00 files_changed=3
- commit `cc26ee1662c4` date=2026-06-13T21:34:55+02:00 files_changed=11
- commit `0d031542e085` date=2026-06-13T20:27:07+02:00 files_changed=2
- commit `7e9aa341addb` date=2026-06-13T20:09:08+02:00 files_changed=2
- commit `83d68b487de8` date=2026-06-13T19:14:01+02:00 files_changed=5
- commit `3a20239eea5b` date=2026-06-13T18:50:39+02:00 files_changed=8
- commit `fb33e555ff5c` date=2026-06-06T01:47:26+02:00 files_changed=3
- commit `83285b0bfaeb` date=2026-06-05T23:45:43+02:00 files_changed=2
- commit `bc2cd4ec8912` date=2026-06-05T23:31:17+02:00 files_changed=3
- commit `c0b5234520b2` date=2026-06-05T22:51:50+02:00 files_changed=15
- commit `4c06b408fae6` date=2026-06-05T22:02:35+02:00 files_changed=2
- commit `b065f6fc867d` date=2026-06-05T21:46:19+02:00 files_changed=2
- commit `99279835c2c4` date=2026-06-04T10:18:15+02:00 files_changed=3
- commit `ba0d172593ba` date=2026-06-04T10:13:56+02:00 files_changed=2
- commit `67ea5b70470f` date=2026-05-18T13:58:21+02:00 files_changed=5
- commit `165f70a3f463` date=2026-05-18T13:32:41+02:00 files_changed=5
- commit `0a77e68160e5` date=2026-05-18T12:00:29+02:00 files_changed=6
- commit `0776050aa4ea` date=2026-05-12T00:32:08+02:00 files_changed=5
- commit `ed59a12d8815` date=2026-05-12T00:31:57+02:00 files_changed=2
- commit `6e91933d84f3` date=2026-05-12T00:31:48+02:00 files_changed=13
- commit `81517f54087a` date=2026-05-12T00:31:37+02:00 files_changed=5
- commit `3b2444bb2f43` date=2026-05-12T00:31:28+02:00 files_changed=5
- commit `af8d44f9804b` date=2026-05-12T00:31:18+02:00 files_changed=4
- commit `0bf1a6932459` date=2026-05-10T21:25:11+02:00 files_changed=3
- commit `c28833afbead` date=2026-05-10T21:20:44+02:00 files_changed=8
- commit `d83605e3252c` date=2026-05-09T18:41:15+02:00 files_changed=2
- commit `85907ddff78a` date=2026-05-09T18:13:13+02:00 files_changed=253

Development phases (from contract artifact dates + git history):
- **Foundation & domain** (2026-03-22 → 03-29): AIYES-01..21 — domain layer, error model, AT-SPI isolation, wait/diff/resize, observability.
- **MCP & Android backend** (2026-03-29 → 05-06): AIYES-17..43, 50..70 — MCP server, machine-readable help, Android backend + adb adapters, Android diagnostics/identity/diff/actions.
- **Public release readiness** (2026-05-08 → 05-09): AIYES-54..70, 71..93 — maintainer release gate, scenario runner, evidence bundles, public fixtures, unified reactive waits.
- **Scenario step kinds & Flutter driving** (2026-05-11 → 06-06): AIYES-44..49, 94..102 — step-kind expansion, Flutter/Android scroll fixes, selector diagnostics.
- **AI-helper diagnostics** (2026-06-13): AIYES-103..108 — required-find consumption, wait-timeout policy, near-name ranking, no-scrollable guidance, evidence profiles, top-level failure_code.

## Stakeholders (area_status=evidence_absent)
_Sources: CLAUDE.md (empty), README, transcripts (ME-01 scoped)._

Single-maintainer project. Maintainer/owner/operator: **Jeroen** (also the release-gate authority). Consumers: local AI coding agents (e.g. Claude Code via the MCP server) and developers driving Linux/Android GUI automation. No external team, sponsor, or steering group evidenced. Wider stakeholder enumeration is evidence-absent and is left to the operator.

## Communications (area_status=evidence_absent)
_Sources: README, SECURITY.md, transcripts, manifest (ME-01 scoped)._

Public surface: GitHub repo `pljeroen/aiyes` (mirrored to internal Forgejo `jeroen/aiyes`), README, CHANGELOG, CONTRIBUTING, SECURITY (private vulnerability reporting, 48h acknowledgment). No formal communications/cadence plan beyond release notes; not applicable for a single-maintainer beta. Left to the operator.
