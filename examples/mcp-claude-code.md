# MCP Claude Code Walkthrough

Purpose: expose aiyes to a trusted local Claude Code session through stdio MCP.

Trust boundary:

- `aieyes-mcp` is local stdio only.
- Do not expose it through a remote server, socket bridge, or untrusted client.

Install and register:

```bash
pip install "aiyes[mcp]"
claude mcp add --scope user aieyes -- aieyes-mcp
claude mcp list
```

Agent workflow:

```text
Use the aieyes MCP tools to:
1. session_start a trusted local GUI app.
2. inspect the tree.
3. find the target node.
4. action or do the intended operation.
5. wait for the expected state.
6. inspect again to verify.
7. session_stop when finished.
```

Equivalent CLI cleanup if needed:

```bash
aieyes session list
aieyes session stop --session <session-id>
```

Verify:

- The MCP client lists aieyes tools.
- The agent can inspect before acting.
- The agent performs a final verify step before cleanup.
