# Linux Gedit Smoke Walkthrough

Purpose: verify the basic observe, act, verify, cleanup loop on the Linux backend.

Prerequisites:

- Linux system dependencies installed.
- `aieyes doctor` reports the required Linux tools as passing.

```bash
aieyes doctor
aieyes session start --name gedit-smoke -- gedit /tmp/aiyes-gedit-smoke.txt
aieyes inspect --no-screenshot --tree-depth 3
aieyes find textbox
aieyes action <node-id> focus
aieyes type "AIYES smoke test"
aieyes inspect --no-screenshot
aieyes session stop
```

Verify:

- The first `inspect` returns a JSON tree.
- `find textbox` returns a node ID.
- The second `inspect` shows the text entry state changed.
- `session stop` removes the running session.
