# Packaging connectors as MCPB bundles

[MCPB](https://github.com/anthropics/mcpb) (`.mcpb`) is the Claude Desktop
extension format. Each connector in this repo can be packaged as a self-contained
bundle that a user installs by drag-and-drop, with no Python toolchain on their
machine.

```bash
python scripts/build_mcpb.py gitlab     # -> dist/gitlab-mcp.mcpb
python scripts/build_mcpb.py all        # build every connector
```

Bundles are **build artifacts** and are not committed (`dist/` and `*.mcpb` are
git-ignored).

## Bundle layout

A `.mcpb` is a ZIP whose root contains:

```
manifest.json
src/
  server.py            # the connector, run by the Desktop client
  lib/                 # vendored dependencies (mcp, httpx, + their deps)
    mcp/ httpx/ ...
    mcp-*.dist-info/   # REQUIRED — see gotcha #1
```

The Desktop client launches `src/server.py` with the **system** Python
interpreter, not a virtualenv. Every dependency must therefore be vendored under
`src/lib`, which the manifest puts on `PYTHONPATH`.

## The manifest

`manifest_version` is `"0.3"`. The `server.mcp_config` block is mandatory —
without it the client rejects the bundle with *"Invalid manifest: server:
Required"*.

```json
{
  "manifest_version": "0.3",
  "name": "gitlab-mcp",
  "display_name": "GitLab",
  "version": "1.0.0",
  "server": {
    "type": "python",
    "entry_point": "src/server.py",
    "mcp_config": {
      "command": "python3",
      "args": ["${__dirname}/src/server.py"],
      "env": {
        "PYTHONPATH": "${__dirname}/src/lib",
        "GITLAB_URL": "${user_config.gitlab_url}",
        "GITLAB_TOKEN": "${user_config.gitlab_token}"
      }
    }
  },
  "user_config": { "...": "..." }
}
```

Every `user_config` field **must** have a `description`, or the client refuses to
install. Mark secrets with `"sensitive": true`; the client stores them encrypted.

## Known gotchas

1. **Always ship the `.dist-info` directories.** The `mcp` library calls
   `importlib.metadata.version("mcp")` at import time; without
   `mcp-*.dist-info/` in `src/lib`, the server crashes on startup with an
   opaque *"could not connect to server"*. `pip install --target` keeps them by
   default — don't strip them.
2. **The manifest `tools` array is declarative only.** It does not change what
   the server actually exposes; it's metadata for the client UI. Keep it in sync
   anyway (a test in CI checks it matches the server).
3. **`.dxt` does not work for Python servers** — always use `.mcpb`.
4. **Bumping the manifest `name`** forces some clients to refresh their tool
   cache; reusing the same `name` across versions can leave stale tools cached.
5. The system Python may differ from your dev venv — never assume a dependency is
   "already there". If it isn't vendored under `src/lib`, it isn't available.

## Installing

In **Claude Desktop → Settings → Extensions**, drag the `.mcpb` in and fill the
requested fields (URL, token…). To wire a connector without MCPB, point any MCP
client at the module instead — see the [root README](../README.md).
