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
    python scripts/build_mcpb.py dolibarr --with-extension ../inno3-mcp-extensions

``--with-extension`` vendors an extra package into ``src/lib`` so the connector
discovers its tools at startup through the ``dolibarr_mcp.extensions``
entry-point group. The package is installed with pip, hence *with* its
``.dist-info`` — without which ``importlib.metadata.entry_points()`` returns
nothing and the extra tools are silently absent from the bundle.

Requires ``pip`` on PATH. Bundles are written to ``dist/`` and are NOT committed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Runtime deps every connector needs (kept in sync with pyproject.toml).
# Contraintes ALIGNÉES sur pyproject.toml : sans elles, pip prend la dernière
# version publiée et mcp 2.0 supprime `mcp.server.fastmcp` -> tous les serveurs
# du bundle plantent au démarrage avec ModuleNotFoundError (constaté 05/08/2026
# sur le bundle inno3pilot fraîchement construit).
RUNTIME_DEPS = ["mcp>=1.0.0,<2.0", "httpx>=0.27"]


def _connectors() -> dict[str, Path]:
    """Map short name -> package dir for every connector with a manifest."""
    out: dict[str, Path] = {}
    for manifest in sorted(ROOT.glob("*_mcp/manifest.json")):
        pkg = manifest.parent
        out[pkg.name.removesuffix("_mcp")] = pkg
    return out


def _extension_tools(ext: Path) -> "list[dict]":
    """Noms et descriptions des outils qu'une extension enregistre.

    Lecture statique du source (ast) plutôt qu'import : importer l'extension
    exigerait le connecteur et ses dépendances dans l'interpréteur qui construit
    le bundle, alors qu'elles ne sont vendorisées que dans le bundle lui-même.
    """
    import ast

    # `pip install` laisse un build/lib/ contenant une COPIE du paquet : sans ce
    # filtre chaque outil est compté deux fois, et la copie peut être périmée.
    skip = {".venv", "build", "dist", ".git", "__pycache__", ".pytest_cache", ".ruff_cache"}

    out: list[dict] = []
    seen: set[str] = set()
    for py in sorted(ext.rglob("*.py")):
        if skip & set(py.parts) or py.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        registered = set(re.findall(r"mcp\.tool\(\)\((\w+)\)", py.read_text()))
        if not registered:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in registered or node.name in seen:
                continue
            seen.add(node.name)
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            desc = doc[0] if doc else node.name
            out.append({"name": node.name, "description": desc[:200]})
    return out


def build(name: str, pkg: Path, output_dir: Path,
          extensions: "list[Path] | None" = None,
          bundle_name: "str | None" = None,
          display_name: "str | None" = None) -> Path:
    manifest_path = pkg / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    bundle_name = bundle_name or manifest["name"]  # e.g. "gitlab-mcp"

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        src = stage / "src"
        lib = src / "lib"
        lib.mkdir(parents=True)

        # 1. Manifest at the root. Quand une extension est embarquée, ses outils
        #    sont ajoutés à la liste déclarée : celle-ci ne sert qu'à l'affichage,
        #    mais un bundle qui annonce 42 outils et en expose 71 se lit comme un
        #    chargement partiel. Le manifeste versionné, lui, reste générique.
        bundled = dict(manifest)
        # L'identite d'une extension installee est <publisher>.<name du
        # manifeste> : c'est elle qui indexe le repertoire d'installation ET le
        # fichier de reglages (URL, secrets). Republier sous un autre `name`
        # installe donc une SECONDE extension a cote de la premiere, sans
        # reprendre sa configuration. D'ou cette option : garder le depot
        # generique tout en publiant sous le nom deja installe.
        bundled["name"] = bundle_name
        if display_name:
            bundled["display_name"] = display_name
        extra = [t for ext in extensions or [] for t in _extension_tools(ext)]
        if extra:
            known = {t["name"] for t in bundled.get("tools", [])}
            bundled["tools"] = bundled.get("tools", []) + [
                t for t in extra if t["name"] not in known
            ]
        (stage / "manifest.json").write_text(
            json.dumps(bundled, ensure_ascii=False, indent=2) + "\n"
        )

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

        # 3b. Optional tool extensions, vendored the same way. `--no-deps`
        #     because an extension depends on this very connector: letting pip
        #     resolve it would pull mcp-foss-connectors from an index and shadow
        #     the modules already flattened into src/.
        for ext in extensions or []:
            if not (ext / "pyproject.toml").exists():
                raise SystemExit(f"extension introuvable ou sans pyproject.toml : {ext}")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps",
                 "--target", str(lib), str(ext)],
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
    ap.add_argument(
        "--bundle-name", metavar="NAME",
        help="publier sous ce `name` de manifeste au lieu de celui du depot. "
             "L'identite d'une extension installee etant <publisher>.<name>, "
             "reutiliser le nom deja installe met a jour EN PLACE et conserve "
             "la configuration ; un nom different installe une seconde extension.",
    )
    ap.add_argument(
        "--display-name", metavar="TITLE",
        help="libelle affiche dans le client (defaut : celui du depot)",
    )
    ap.add_argument(
        "--with-extension", action="append", default=[], metavar="PATH", type=Path,
        help="chemin d'un paquet d'extension à embarquer dans src/lib "
             "(répétable ; ex: ../inno3-mcp-extensions)",
    )
    args = ap.parse_args()

    extensions = [p.resolve() for p in args.with_extension]
    if args.bundle_name and args.connector == "all":
        raise SystemExit(
            "--bundle-name vise un connecteur precis : les huit bundles ne "
            "peuvent pas partager un meme nom d'extension."
        )
    if extensions and args.connector == "all":
        raise SystemExit(
            "--with-extension vise un connecteur précis : une extension déclare "
            "son point d'entrée pour un connecteur donné, l'embarquer dans tous "
            "les bundles y ajouterait un paquet que rien ne charge."
        )

    targets = connectors if args.connector == "all" else {
        args.connector: connectors[args.connector]
    }
    for name, pkg in targets.items():
        out = build(name, pkg, Path(args.output_dir), extensions,
                    args.bundle_name, args.display_name)
        size_kb = out.stat().st_size / 1024
        # relative_to() lève ValueError dès que --output-dir sort du dépôt : le
        # bundle est déjà écrit à ce stade, planter sur son affichage donnerait
        # à croire que la construction a échoué.
        try:
            shown = out.relative_to(ROOT)
        except ValueError:
            shown = out
        print(f"  ✓ {shown}  ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
