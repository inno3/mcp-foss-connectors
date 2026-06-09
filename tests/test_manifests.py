"""Release-gate tests: every connector's manifest matches its server.

These run offline and guard against drift between ``manifest.json`` (declarative,
shown in the Claude Desktop UI) and the actual ``@mcp.tool()`` functions, as well
as the per-connector entrypoints referenced by ``pyproject.toml``.
"""

import ast
import importlib
import json
import os
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = sorted(p.name for p in ROOT.glob("*_mcp") if (p / "server.py").exists())

# Dummy env so importing a server module never fails on missing config.
for var in (
    "DOLIBARR_URL", "DOLIBARR_API_KEY", "KANBOARD_URL", "KANBOARD_USER",
    "KANBOARD_TOKEN", "WP_PROD_URL", "WP_PROD_USER", "WP_PROD_APP_PASSWORD",
    "N8N_BASE_URL", "N8N_API_KEY", "MATRIX_HOMESERVER_URL", "MATRIX_ACCESS_TOKEN",
    "NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_APP_PASSWORD", "HEDGEDOC_URL",
    "HEDGEDOC_API_TOKEN", "GITLAB_URL", "GITLAB_TOKEN",
):
    os.environ.setdefault(var, "x")


def _server_tool_names(pkg: str) -> set[str]:
    tree = ast.parse((ROOT / pkg / "server.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "tool":
                    names.add(node.name)
    return names


@pytest.mark.parametrize("pkg", PACKAGES)
def test_each_package_imports_and_has_main(pkg: str) -> None:
    mod = importlib.import_module(f"{pkg}.server")
    assert callable(getattr(mod, "main", None)), f"{pkg}.server.main missing"


@pytest.mark.parametrize("pkg", PACKAGES)
def test_manifest_matches_server_tools(pkg: str) -> None:
    manifest_path = ROOT / pkg / "manifest.json"
    if not manifest_path.exists():
        pytest.skip(f"{pkg} has no manifest")
    manifest = json.loads(manifest_path.read_text())
    declared = {t["name"] for t in manifest["tools"]}
    actual = _server_tool_names(pkg)
    # Optional, organisation-specific tools (e.g. Dolibarr portal-URL helpers
    # that need custom modules) ship in a *separate* package and register via
    # the "dolibarr_mcp.extensions" entry-point group — they are never declared
    # in the public core manifest nor defined in the core server module.
    actual = {n for n in actual if not n.endswith("_portal_url")}
    assert declared == actual, (
        f"{pkg}: manifest/server tool drift. "
        f"only in manifest={declared - actual}, only in server={actual - declared}"
    )


@pytest.mark.parametrize("pkg", PACKAGES)
def test_manifest_required_fields(pkg: str) -> None:
    manifest_path = ROOT / pkg / "manifest.json"
    if not manifest_path.exists():
        pytest.skip(f"{pkg} has no manifest")
    m = json.loads(manifest_path.read_text())
    assert m["manifest_version"] == "0.3"
    assert m["name"].endswith("-mcp")
    assert m["server"]["mcp_config"]["command"]
    # Every user_config field must carry a description (else the client refuses).
    for key, cfg in m.get("user_config", {}).items():
        assert cfg.get("description"), f"{pkg}: user_config.{key} lacks a description"


def test_pyproject_scripts_point_at_real_mains() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for script, target in data["project"]["scripts"].items():
        module, func = target.split(":")
        mod = importlib.import_module(module)
        assert callable(getattr(mod, func, None)), f"{script} -> {target} not callable"


def test_no_private_inno3_defaults_in_manifests() -> None:
    """Guard against re-introducing an inno³ host/login as a default value."""
    for pkg in PACKAGES:
        mp = ROOT / pkg / "manifest.json"
        if not mp.exists():
            continue
        for key, cfg in json.loads(mp.read_text()).get("user_config", {}).items():
            default = str(cfg.get("default", "")).lower()
            assert "inno3" not in default and "inno³" not in default, (
                f"{pkg}: user_config.{key} default leaks an inno³ value"
            )
