# mcp-foss-connectors

Vendor-neutral [Model Context Protocol](https://modelcontextprotocol.io) (MCP)
servers that let MCP clients — Claude Desktop, Cowork, or any other MCP host —
read from and act on the **free and open-source tools** that run a typical
back office: ERP, project board, CMS, automation, chat, file storage, notes and
code forge.

Every server is a single, self-contained Python file. It is configured
**entirely through environment variables** — no hostname, login or secret is
ever baked into the code — so it works against *any* instance of the target
tool, whether self-hosted or managed.

> Origin: these connectors were built and battle-tested at
> [inno³](https://inno3.fr) against its own information system, then generalised
> and released under Apache-2.0. See [Origin & attribution](#origin--attribution).

---

## Connectors

| Connector | Server name | Tools | Integrates with | Auth |
|---|---|--:|---|---|
| **Dolibarr ERP** | `dolibarr-mcp` | 48¹ | Dolibarr 16+ REST API | `DOLAPIKEY` API key |
| **Kanboard** | `kanboard-mcp` | 53 | Kanboard JSON-RPC API | personal API token |
| **WordPress** | `wordpress-mcp` | 26 | WP REST API (+ ACF) | Application Password |
| **n8n** | `n8n-mcp` | 17 | n8n public REST API | `X-N8N-API-KEY` |
| **Matrix** | `matrix-mcp` | 10 | Matrix client-server API | access token |
| **Nextcloud** | `nextcloud-mcp` | 14 | WebDAV + OCS API | app password |
| **HedgeDoc** | `hedgedoc-mcp` | 8 | HedgeDoc 1.x API | session / API token |
| **GitLab** | `gitlab-mcp` | 18 | GitLab REST API v4 | personal access token |

All servers target **Python 3.11+** and use the official `mcp` library.
A per-connector README in each `*_mcp/` directory documents its environment
variables and tools in detail.

¹ Dolibarr exposes **48** vendor-neutral tools. Organisation-specific tools
that depend on *custom* Dolibarr modules (e.g. inno³'s MeetingNotes and signed
portal URLs) are **not** shipped here: they live in a separate package that
plugs in via the `dolibarr_mcp.extensions` entry-point group. See
[Optional extensions](#optional-extensions) and
[`dolibarr_mcp/README.md`](dolibarr_mcp/README.md).

---

## Quick start (from source)

```bash
git clone <this-repo> && cd mcp-foss-connectors
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# configure the connector you want, then run it over stdio
export GITLAB_URL="https://gitlab.com"
export GITLAB_TOKEN="glpat-…"
python -m gitlab_mcp.server
```

To wire a server into an MCP client manually, point the client at the module:

```json
{
  "mcpServers": {
    "gitlab": {
      "command": "python",
      "args": ["-m", "gitlab_mcp.server"],
      "env": { "GITLAB_URL": "https://gitlab.com", "GITLAB_TOKEN": "glpat-…" }
    }
  }
}
```

---

## Install in Claude Desktop (MCPB bundle)

Each connector can be packaged as an **MCPB** bundle (the Claude Desktop
extension format): a zip containing `manifest.json` + `src/server.py` +
`src/lib/` (vendored dependencies).

```bash
python scripts/build_mcpb.py gitlab     # produces dist/gitlab-mcp.mcpb
```

Then in **Claude Desktop → Settings → Extensions**, drag the `.mcpb` file in and
fill the requested fields (URL, token…). They are stored encrypted by the
client. See [`docs/PACKAGING.md`](docs/PACKAGING.md) for the MCPB rules and
known gotchas.

> MCPB bundles are build artifacts and are **not** committed to this repository.

### Download pre-built bundles

You don't have to build them yourself — pre-built `.mcpb` bundles for every
connector are attached to each tagged
[**release**](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases).

Latest — [**v0.1.0**](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/tag/v0.1.0):

| Connector | Download |
|---|---|
| Dolibarr ERP | [`dolibarr-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/dolibarr-mcp.mcpb) |
| Kanboard | [`kanboard-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/kanboard-mcp.mcpb) |
| WordPress | [`wordpress-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/wordpress-mcp.mcpb) |
| n8n | [`n8n-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/n8n-mcp.mcpb) |
| Matrix | [`matrix-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/matrix-mcp.mcpb) |
| Nextcloud | [`nextcloud-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/nextcloud-mcp.mcpb) |
| HedgeDoc | [`hedgedoc-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/hedgedoc-mcp.mcpb) |
| GitLab | [`gitlab-mcp.mcpb`](https://code.inno3.eu/contrib-OS/mcp-foss-connectors/releases/download/v0.1.0/gitlab-mcp.mcpb) |

The bundles embed no credentials: you fill the URL/token at install time and the
client stores them encrypted.

---

## Configuration model

- **One environment variable per setting.** Conventionally `<TOOL>_URL` for the
  base URL plus the credential variables listed in each connector's README.
- **No defaults that point at a specific deployment.** Where a connector ships a
  default URL it is the tool's public/canonical one (e.g. `https://gitlab.com`)
  or empty, never a private host.
- **Compact responses.** Servers return minified JSON with conservative default
  page sizes to keep the client's context window small.

---

## Optional extensions

The connectors stay vendor-neutral, but they are **extensible**: an
organisation that runs custom modules can add its own tools *without forking*.

The Dolibarr connector discovers extensions through the
[entry-point](https://packaging.python.org/en/latest/specifications/entry-points/)
group `dolibarr_mcp.extensions`. A separate package advertises a `register`
callable there:

```toml
# in the extension package's pyproject.toml
[project.entry-points."dolibarr_mcp.extensions"]
my_org = "my_org_pkg.extension:register"
```

```python
# my_org_pkg/extension.py
def register(mcp):
    """Receives the live FastMCP instance; add @mcp.tool()-style tools."""
    mcp.tool()(my_extra_tool)
```

When that package is installed next to `mcp-foss-connectors`, its tools appear
automatically; uninstall it and the connector falls back to the generic core.
No environment flag, no patch to this repository.

inno³ ships its own such add-on — `inno3-mcp-extensions` — for tools that depend on its
custom Dolibarr modules (MeetingNotes, signed client/support-credits portal
URLs). It is **not** part of this repo, which keeps the public core strictly
generic.

---

## Tests

```bash
pytest
```

All tests are **offline unit tests** — every HTTP call is mocked, so the suite
runs with no network and no real credentials. Each connector has a
`tests/test_<tool>.py`.

---

## Origin & attribution

This toolkit was created by **[inno³](https://inno3.fr)**, an independent
consultancy specialised in open-source and open-innovation strategy, legal and
compliance. inno³ runs its own back office entirely on the free/open-source
tools these connectors target, and originally wrote them to drive that system
from MCP clients.

The code was then generalised — stripped of any inno³-specific host, identifier
or module — and published here under the **Apache License 2.0** so that:

- any organisation running these tools can reuse the connectors as-is, and
- inno³ can migrate its own usage onto this public, community-maintainable code
  base rather than a private fork.

Organisation-specific helpers (tools tied to inno³'s own Dolibarr modules) are
kept **entirely out** of this repository and published as a separate add-on
package — see [Optional extensions](#optional-extensions).

Please keep the [`NOTICE`](NOTICE) file when redistributing, as required by the
Apache-2.0 license. "inno³" is a trademark of inno³; the tool names above belong
to their respective projects, which do not endorse this work.

---

## License

[Apache License 2.0](LICENSE) — © 2026 inno³ and contributors.
