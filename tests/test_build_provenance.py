"""La construction publique ne prend jamais un paquet dans une autre copie."""

from unittest.mock import patch

import pytest

from scripts import build_mcpb


def test_refuse_un_paquet_exterieur_avant_tout_pip(tmp_path):
    package = tmp_path / "gitlab_mcp"
    package.mkdir()
    (package / "manifest.json").write_text('{"name":"copie-privee"}')
    (package / "server.py").write_text("# copie ancienne\n")
    with patch.object(build_mcpb.subprocess, "run") as run:
        with pytest.raises(ValueError, match="canonique"):
            build_mcpb.build("gitlab", package, tmp_path / "out")
    run.assert_not_called()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("link", ["package", "server"])
def test_refuse_un_lien_vers_une_copie_exterieure(tmp_path, monkeypatch, link):
    root, other = tmp_path / "public", tmp_path / "private"
    root.mkdir()
    other.mkdir()
    (other / "manifest.json").write_text('{"name":"copie-privee"}')
    (other / "server.py").write_text("# copie ancienne\n")
    package = root / "gitlab_mcp"
    if link == "package":
        package.symlink_to(other, target_is_directory=True)
    else:
        package.mkdir()
        (package / "manifest.json").write_text('{"name":"gitlab-mcp"}')
        (package / "server.py").symlink_to(other / "server.py")
    monkeypatch.setattr(build_mcpb, "ROOT", root)
    with patch.object(build_mcpb.subprocess, "run") as run:
        with pytest.raises(ValueError, match="canonique"):
            build_mcpb.build("gitlab", package, tmp_path / "out")
    run.assert_not_called()
