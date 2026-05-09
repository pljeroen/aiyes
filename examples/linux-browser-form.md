# Linux Browser Form Walkthrough

Purpose: let an agent inspect a browser page, find a form control, act, wait, verify, and clean up.

Prerequisites:

- Firefox or another GUI browser installed.
- Linux system dependencies installed.

```bash
aieyes doctor
aieyes session start --name browser-form -- firefox https://example.com
aieyes inspect --tree-depth 4
aieyes find text "Example Domain"
aieyes screenshot
aieyes wait text "Example Domain" --timeout 10
aieyes inspect --no-screenshot --tree-depth 4
aieyes session stop
```

For pages with real forms, replace the `find text` step with:

```bash
aieyes find textbox
aieyes action <node-id> focus
aieyes type "agent input"
aieyes wait button "Submit" --timeout 10
```

Verify:

- The page title or expected text is present before acting.
- The target node exists before using its node ID.
- A final `inspect` confirms the expected page or form state.
