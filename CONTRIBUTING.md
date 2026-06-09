# Contributing

Thanks for your interest in `mcp-foss-connectors`! This project provides
vendor-neutral [Model Context Protocol](https://modelcontextprotocol.io)
servers for popular free/open-source back-office tools.

## Ground rules

- **No secrets, ever.** Connectors are configured exclusively through
  environment variables. Never hardcode a URL, login, token or password in
  `server.py`, in a manifest, in a test, or in documentation. Tests use fake
  values set via `os.environ.setdefault(...)`.
- **Stay generic.** A connector must work against *any* instance of the target
  tool, not one specific deployment. Avoid hardcoded hostnames, project IDs,
  user IDs or content-type names in code paths. Such examples belong in
  docstrings only, and should be clearly marked as examples.
- **Keep token usage low.** Responses are returned as compact JSON
  (`separators=(",", ":")`) with sensible default limits, to preserve the MCP
  client's context window.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

All tests are offline unit tests (HTTP calls are mocked). They must pass
without any network access or real credentials.

## Adding or changing a connector

Each connector lives in `<tool>_mcp/` and ships:

- `server.py` — a `FastMCP("<tool>")` server exposing `@mcp.tool` functions.
- `manifest.json` — the MCPB descriptor (see `scripts/build_mcpb.py`).
- `README.md` — configuration (env vars), available tools, install steps.
- a matching `tests/test_<tool>.py` with mocked HTTP.

Every new tool should have at least one unit test covering its happy path and
its error handling.

## License of contributions

By submitting a contribution you agree that it is licensed under the
[Apache License 2.0](LICENSE), consistent with the rest of the project.
Add the SPDX header to new source files:

```python
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
```
