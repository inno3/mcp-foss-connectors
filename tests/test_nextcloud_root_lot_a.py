"""Le diagnostic local est coherent meme si Downloads est un lien."""

import json
from unittest.mock import AsyncMock

from nextcloud_mcp import server


async def test_fichier_absent_annonce_racine_resolue_sans_requete(monkeypatch, tmp_path):
    real = tmp_path / "ssd" / "Downloads"
    real.mkdir(parents=True)
    alias = tmp_path / "Downloads"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(server, "_NC_UPLOAD_DIR", str(alias))
    client = AsyncMock(side_effect=AssertionError("pas de reseau"))
    monkeypatch.setattr(server, "_webdav", client)
    data = json.loads(await server.nextcloud_upload_file(path="/fictif.txt", local_path="absent.txt"))
    assert data["upload_root"] == str(real)
    assert data["upload_root_configured"] == str(alias)
    assert str(real / "absent.txt") in data["error"]
    client.assert_not_called()
