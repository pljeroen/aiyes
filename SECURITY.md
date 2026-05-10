# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in aiyes, please report it responsibly:

1. **Do not** open a public issue.
2. Email the maintainer directly or use GitHub's [private vulnerability reporting](https://github.com/pljeroen/aiyes/security/advisories/new).
3. Include a description of the vulnerability, steps to reproduce, and potential impact.

You should receive an acknowledgment within 48 hours. Fixes for confirmed vulnerabilities will be released as soon as practical.

## Scope

aiyes manages isolated display sessions and spawns subprocesses. Security-relevant areas include:

- **Subprocess isolation**: All commands run in isolated Xvfb sessions, never the host desktop
- **Credential handling**: Sensitive environment variables are stripped from subprocess environments
- **Input sanitization**: Session IDs are validated, ADB text input is escaped
- **No network access**: The domain layer has zero network dependencies

aiyes is a trusted local tool, not a sandbox. It is intended for local developer and agent workflows where the operator trusts the commands, applications, devices, and MCP clients involved.

## Threat Model

aiyes operates under the following assumptions and boundaries:

- **Local tool for trusted operators.** aiyes is a CLI tool run by a local user who already has full access to their own machine. It does not provide multi-tenant isolation or protect against a malicious operator.
- **MCP server is optional and locally-configured.** The MCP server, when enabled, is local stdio integration for locally-configured agent clients (e.g., Claude Code). Do not expose it to untrusted networks, socket bridges, arbitrary remote clients, or automation gateways.
- **External contributions are untrusted input.** Do not connect aiyes MCP, GUI-control surfaces, Android devices, release credentials, or self-hosted runners to public pull-request workflows or unreviewed external code.
- **Release checks are maintainer-local.** Public GitHub Actions are intentionally not part of the trust boundary. Release decisions are made from a trusted local checkout using the maintainer release gate.
- **Launching arbitrary commands is core functionality.** `session start` executing user-specified commands is the tool's purpose, not a vulnerability. The operator chooses what to run.
- **Display isolation (Xvfb) protects against app-to-host-desktop escape.** The isolated X11 session prevents a launched application from interacting with the operator's real desktop (reading keystrokes, taking screenshots of other windows, etc.). It does **not** provide host-data confinement — the launched application still runs as the same user and can access the filesystem.
- **Credential stripping is defense-in-depth, not a sandbox boundary.** Environment variables containing credentials are stripped from the child process environment as a precaution. This reduces accidental exposure but is not a security boundary — a determined application running as the same user could access credentials through other means (e.g., reading dotfiles).
- **File permissions (0o700/0o600) are defense-in-depth.** Session data directories and files use restrictive permissions to limit exposure on shared systems, but the primary trust boundary is the user account, not file permissions.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes       |
| 0.1.x   | No        |
