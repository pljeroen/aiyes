# Contributing

aiyes is public source, not open governance.

Issues, reproducible bug reports, and security reports are welcome. Code
contributions are reviewed selectively and may be declined to preserve the
project's security model, architecture, and release discipline.

Do not submit pull requests that require:

- running untrusted MCP clients or agent flows,
- self-hosted runners,
- release credentials,
- Android devices or GUI control surfaces in public CI,
- private application fixtures,
- broad dependency additions outside the CLI/adapters layer.

All external code, prompts, tests, fixtures, documentation, and workflow changes
are treated as untrusted input.
