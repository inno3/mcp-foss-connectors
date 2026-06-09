#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Build an MCPB bundle (Claude Desktop extension) for one connector.

An MCPB bundle is a ZIP renamed ``.mcpb`` that contains, at its root:

    manifest.json
    src/server.py            (+ any sibling modules of the package)
    src/lib/                 (vendored Python dependencies, incl. *.dist-info)

The Desktop client runs ``src/server.py`` with the *system* Python, so every
dependency must be vendored under ``src/lib`` and put on ``PYTHONPATH`` by the
manifest's ``mcp_config.env``.

Usage:
    python scripts/build_mcpb.py gitlab
    python scripts/build_mcpb.py all
    python scripts/build_mcpb.py dolibarr --output-dir dist

Requires ``pip`` on PATH. Bundles are written to ``dist/`` and are NOT committed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Runtime deps every connector needs (kept in sync with pyproject.toml).
RUNTIME_DEPS = ["mcp", "httpx"]


def _connectors() -> dict[str, Path]:
    """Map short name -> package dir for every connector with a manifest."""
    out: dict[str, Path] = {}
    for manifest in sorted(ROOT.glob("*_mcp/manifest.json")):
        pkg = manifest.parent
        out[pkg.name.removesuffix("_mcp")] = pkg
    return out


def build(name: str, pkg: Path, output_dir: Path) -> Path:
    manifest_path = pkg / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    bundle_name = manifest["name"]  # e.g. "gitlab-mcp"

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        src = stage / "src"
        lib = src / "lib"
        lib.mkdir(parents=True)

        # 1. Manifest at the root.
        shutil.copy2(manifest_path, stage / "manifest.json")

        # 2. All Python modules of the package, flattened into src/.
        for py in sorted(pkg.glob("*.py")):
            if py.name == "__init__.py":
                continue
            shutil.copy2(py, src / py.name)

        # 3. Vendored dependencies (with .dist-info — required by
        #    importlib.metadata.version("mcp") at runtime).
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--target", str(lib), *RUNTIME_DEPS],
            check=True,
        )
        # Drop console-script shims and bytecode caches (keeps the bundle small).
        shutil.rmtree(lib / "bin", ignore_errors=True)
        for cache in stage.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)

        # 4. Zip it up as <name>-mcp.mcpb.
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{bundle_name}.mcpb"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(stage))
        return out


def main() -> int:
    connectors = _connectors()
    ap = argparse.ArgumentParser(description="Build MCPB bundle(s).")
    ap.add_argument("connector", help="connector short name, or 'all'",
                    choices=[*sorted(connectors), "all"])
    ap.add_argument("--output-dir", default=str(ROOT / "dist"), type=Path)
    args = ap.parse_args()

    targets = connectors if args.connector == "all" else {
        args.connector: connectors[args.connector]
    }
    for name, pkg in targets.items():
        out = build(name, pkg, Path(args.output_dir))
        size_kb = out.stat().st_size / 1024
        print(f"  ✓ {out.relative_to(ROOT)}  ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
