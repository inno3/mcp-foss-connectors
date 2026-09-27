#!/usr/bin/env python3
"""Vérifie initialize et tools/list sans appeler un outil ni un service distant."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(args):
    if args.module:
        command_args = ["-c", "import importlib,sys; importlib.import_module(sys.argv[1]).main()",
                        args.module]
    else:
        command_args = [str(Path(args.server).resolve())]
    env = {"PATH": os.environ.get("PATH", ""), "DOLIBARR_URL": "https://erp.example.invalid",
           "DOLIBARR_API_KEY": "offline-check"}
    if args.lib:
        env["PYTHONPATH"] = str(Path(args.lib).resolve())
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        async with stdio_client(StdioServerParameters(command=args.python, args=command_args,
                                                       env=env), errlog=errors) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                listing = await session.list_tools()
                names = sorted(tool.name for tool in listing.tools)
        errors.seek(0)
        stderr = errors.read()
    report = {"server_version": initialized.serverInfo.version,
              "tools_count": len(names), "stderr_bytes": len(stderr.encode()),
              "unique_tools": len(set(names)) == len(names)}
    if args.require_tool:
        report["required_tools_present"] = all(name in names for name in args.require_tool)
    print(json.dumps(report, ensure_ascii=False))
    if stderr:
        print(stderr, file=sys.stderr, end="")
    if (stderr or len(names) != args.expected_tools or len(set(names)) != len(names)
            or any(name not in names for name in args.require_tool)
            or (args.version and initialized.serverInfo.version != args.version)):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--module")
    target.add_argument("--server")
    parser.add_argument("--lib")
    parser.add_argument("--expected-tools", type=int, required=True)
    parser.add_argument("--version")
    parser.add_argument("--require-tool", action="append", default=[])
    args = parser.parse_args()
    try:
        asyncio.run(asyncio.wait_for(check(args), timeout=30))
    except TimeoutError:
        print(json.dumps({"success": False, "error": "Délai MCP de 30 secondes dépassé."},
                         ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
