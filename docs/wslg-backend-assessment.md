# WSLg backend assessment

**Status:** backlog / feasibility note. Not contracted or implemented.

## Decision summary

For unattended, isolated GUI automation, use an ordinary Linux runner with
the existing Xvfb backend. It preserves Aiyes' isolation model and is the
lowest-risk path to independent browser evidence.

If WSL support is needed for attended local use, the viable product mode is
an explicit `wslg-host-display` backend. It must use WSLg's existing display,
not attempt to start Xvfb. This is not an automatic fallback for `linux` and
is not suitable for unattended automation or CI.

## Observed constraint

On the investigated WSL host, `/tmp/.X11-unix` is a symlink to WSLg's socket
directory. Xvfb cannot create the Unix listener it requires there and exits
immediately. Starting Xvfb with TCP enabled did work, but it listened on all
interfaces. That would create a network-reachable input and screenshot
surface, so it is not an acceptable fallback.

The MCP 2.x compatibility repair and Xvfb fail-fast diagnostic were shipped
separately in commit `bc2319d`. They make the current failure observable; they
do not create WSL GUI support.

## Candidate: `wslg-host-display`

The backend would:

- require explicit selection and report `isolated=false` and
  `attended_only=true` in session output;
- preserve WSLg's `DISPLAY=:0`, user D-Bus, and AT-SPI environment;
- launch and stop only the Aiyes-owned target process; never create, stop, or
  otherwise manage WSLg, its display, or its accessibility bus;
- allow one active WSLg Aiyes session per user/display; and
- require a persistent MCP client/server process or a one-process scenario
  runner. Separate short-lived command runners kill their child processes.

This backend has a different security class from isolated Linux/Xvfb. WSLg
shares a desktop: AT-SPI inspection can see other applications, root-window
screenshots can expose them, and raw mouse, keyboard, or clipboard operations
can affect them. A supportable implementation therefore also requires:

- proof that every inspected, found, and acted-on window belongs to the target
  process, with typed refusal when ownership cannot be proved;
- target-window screenshot cropping rather than root-display capture;
- focus/window identity verification immediately before input dispatch;
- clipboard and unconstrained raw input disabled by default; and
- typed failure, without automatic recovery, if WSLg or the target display is
  lost.

## Feasibility gate

Do not implement beyond a spike until all of the following pass on a real
WSLg host:

1. Target Firefox launches on `:0`; AT-SPI can inspect it, find a known node,
   perform a semantic action, and stop it through one persistent MCP session.
2. With another GUI application open, inspection and find exclude its nodes;
   attempted cross-target actions refuse.
3. Screenshots contain only the target window; clipboard access is refused by
   default.
4. A second WSLg session is refused while the first is active.
5. Target exit or display loss yields a correct failed/stopped state without
   touching host display or accessibility processes.
6. The existing isolated Linux/Xvfb real run remains green.

If target ownership cannot be established or AT-SPI cannot provide the needed
scope on WSLg, stop the work. A shared display must not be presented as an
isolated backend.

## Effort estimate

These are engineering estimates, not commitments:

| Outcome | Estimate |
| --- | ---: |
| Existing native Linux runner: provision and validate | 0.5–1 day |
| New native Linux runner: provision and validate | 1–2 days |
| WSLg feasibility spike | 1–2 days |
| Minimal experimental attended WSLg backend | 4–7 days total |
| Supportable attended WSLg backend, including safeguards, tests, and docs | 6–10 days |
| Isolated, unattended WSL backend | 3–6 weeks after discovery |

A truly isolated WSL backend would need a private display arrangement (for
example a namespace/container/VM design where the target, X server, and Aiyes
worker share a private Unix socket). It is a separate design problem, not a
TCP-Xvfb configuration change.
