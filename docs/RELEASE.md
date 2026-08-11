# Release checklist

This is the release manager's pre-flight checklist for `mcp-foss-connectors`.
Everything here runs **offline** — no live server, token, or network is required.

## 1. Sanity & hygiene

- [ ] Working tree clean (`git status`), on the release branch.
- [ ] No secrets, hostnames, or personal logins anywhere in the tree:
  ```sh
  grep -ridnE 'inno3\.(eu|fr)|password|token.*=.*["'\''][A-Za-z0-9]' \
    --include='*.py' --include='*.json' --include='*.md' . | grep -v NOTICE
  ```
  Only attribution mentions of inno³ (NOTICE, README credits, copyright headers)
  are expected. A default *value* leaking an inno³ host fails `test_manifests.py`.
- [ ] `CHANGELOG` / release notes updated.

## 2. Lint & tests (the CI gate, run locally)

- [ ] `pip install -e ".[dev]"` succeeds in a **fresh** venv (catches packaging
  errors that an already-installed source tree hides — e.g. a malformed
  `[project]` table).
- [ ] `ruff check .` → "All checks passed!"
- [ ] `pytest -q` → all green (currently 359 tests on py3.11 / py3.12).

The CI configs (`.github/workflows/ci.yml`, `.gitlab-ci.yml`) run exactly these
three steps on Python 3.11 and 3.12.

## 3. Manifest ↔ server parity

`tests/test_manifests.py` already enforces this, but eyeball it once:

- [ ] Every connector's `manifest.json` `tools[]` matches its `@mcp.tool()`
  functions. Organisation-specific extension tools (loaded via the
  `dolibarr_mcp.extensions` entry-point group from a *separate* package) are
  intentionally **not** in the core manifest nor in the core server module.
- [ ] Every `user_config` field has a non-empty `description`.
- [ ] `manifest_version` is `"0.3"`, `name` ends with `-mcp`.
- [ ] `[project.scripts]` entry points all resolve to a callable `main`.

## 4. MCPB bundles (Claude Desktop)

- [ ] Build all bundles: `python scripts/build_mcpb.py all`
- [ ] Spot-check one bundle is well formed:
  ```sh
  unzip -l dist/<name>-mcp.mcpb | head
  # expect: manifest.json at root, src/server.py, src/lib/*.dist-info, no __pycache__
  ```
- [ ] Confirm `src/lib` ships the `*.dist-info` dirs — `importlib.metadata`
  needs them at runtime (see [PACKAGING.md](PACKAGING.md)).
- [ ] Bundles are **build artifacts** — never commit them (`.gitignore` covers
  `*.mcpb`).

## 5. Dolibarr extension point (generic core stays generic)

The public core must expose **only** its generic tools; org-specific tools come
from a *separately-installed* package via the `dolibarr_mcp.extensions`
entry-point group (the public repo declares none).

- [ ] Fresh install of `mcp-foss-connectors` alone exposes the core tool set
  and **no** `*_meetingnote*` / `*_portal_url` tools:
  ```sh
  DOLIBARR_URL=x DOLIBARR_API_KEY=x python -c \
    "import dolibarr_mcp.server as s; print(len(s.mcp._tool_manager._tools))"   # 42
  ```
- [ ] Installing an extension package (e.g. `inno3-mcp-extensions`) makes its tools
  appear automatically, with no env flag and no change to the core:
  ```sh
  pip install inno3-mcp-extensions
  DOLIBARR_URL=x DOLIBARR_API_KEY=x python -c \
    "import dolibarr_mcp.server as s; print(len(s.mcp._tool_manager._tools))"   # 71
  ```
- [ ] A broken extension is caught (logged to stderr) and never crashes the
  core — the server still starts with its 42 tools.

## 6. Docs

- [ ] Top-level `README.md` connector table tool-counts are accurate.
- [ ] Each `*_mcp/README.md` lists the right env vars and tools.
- [ ] The inno³ attribution section (NOTICE + README credits) is present and
  correct.
- [ ] `Source` URL in `README.md` / `pyproject.toml` points at the real repo
  (still a placeholder until hosting is decided).

## 7. Tag & publish

- [ ] Bump `version` in `pyproject.toml`.
- [ ] Annotated tag: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
- [ ] Attach the freshly built `.mcpb` bundles to the release.
