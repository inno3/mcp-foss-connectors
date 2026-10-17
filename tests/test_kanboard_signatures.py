"""Contrat public extrait de la 0.17.1 installee le 14/10/2026."""

import ast
import json
from pathlib import Path


def test_tools_and_signatures_match_installed_0171():
    root = Path(__file__).resolve().parents[1]
    reference = json.loads((root / "tests/fixtures/kanboard_0171_signatures.json").read_text())
    tree = ast.parse((root / "kanboard_mcp/server.py").read_text())
    actual = {
        node.name: f"({ast.unparse(node.args)}) -> {ast.unparse(node.returns)}"
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool" for dec in node.decorator_list
        )
    }
    assert len(reference["tools"]) == 53
    assert actual == reference["tools"]
