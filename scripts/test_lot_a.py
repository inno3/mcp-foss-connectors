"""Harnais local du lot A, sans installation et sans bytecode dans les bundles."""
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]="1"
sys.path[:0]=[os.getcwd(),str(Path(__file__).resolve().parents[1])]
cache=Path("/home/jean/.cache/uv/archive-v0")
for module in ("pytest","_pytest","pluggy","iniconfig","pygments","packaging","pytest_asyncio"):
    paths=sorted(cache.glob("*/"+module+"/__init__.py"))
    if paths:
        sys.path.append(str(paths[0].parent.parent))
sys.path.append("/home/jean/.config/Claude/Claude Extensions/local.mcpb.inno.caldav-bluemind/src/lib")
if (Path.cwd() / "inno3_mcp_ext").is_dir():
    sys.path.append("/home/jean/.config/Claude/Claude Extensions/local.mcpb.inno.dolibarr-erp-inno3-v2/src/lib")
sys.path.append("/home/jean/.config/Claude/Claude Extensions/local.mcpb.inno.carddav-bluemind/src/lib")
os.environ["DOLIBARR_URL"]="https://erp.example.test"
os.environ["DOLIBARR_API_KEY"]="test-key"
import pytest  # noqa: E402

raise SystemExit(pytest.main(["-p","pytest_asyncio.plugin","-o","asyncio_mode=auto"]+sys.argv[1:]))
