# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Réglages Nextcloud : un gabarit `${user_config.x}` non résolu reste sans effet.

Constaté le 06/11/2026 après installation de la 1.5.0 : Claude Desktop transmet le
gabarit tel quel quand un réglage optionnel sans valeur par défaut n'est pas
renseigné. `NEXTCLOUD_DOWNLOAD_DIR` valait donc `${user_config.nextcloud_download_dir}`,
et les téléchargements partaient dans un dossier de ce nom sous le répertoire
courant du processus. Un plafond resté en gabarit aurait fait échouer `int()` à
l'import, donc le démarrage du connecteur.

L'import est joué dans un processus séparé : la configuration est lue une seule
fois, au chargement du module.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = {
    "NEXTCLOUD_DOWNLOAD_DIR": "nextcloud_download_dir",
    "NEXTCLOUD_SAVE_DIRS": "nextcloud_save_dirs",
    "NEXTCLOUD_UPLOAD_DIR": "nextcloud_upload_dir",
    "NEXTCLOUD_MAX_SAVE_BYTES": "nextcloud_max_save_bytes",
    "NEXTCLOUD_MAX_UPLOAD_BYTES": "nextcloud_max_upload_bytes",
}
READ_CONFIG = (
    "import json, nextcloud_mcp.server as s; print(json.dumps({"
    "'download': s._NC_DOWNLOAD_DIR, 'save_dirs': s._NC_SAVE_DIRS, 'upload': s._NC_UPLOAD_DIR,"
    "'max_save': s._MAX_SAVE_BYTES, 'max_upload': s._MAX_UPLOAD_BYTES,"
    "'roots': s._save_roots()}))"
)


def load_config(tmp_path: Path, **settings: str) -> tuple[dict, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "HOME": str(tmp_path),
        "NEXTCLOUD_URL": "https://cloud.example.test",
        "NEXTCLOUD_USER": "alice",
        "NEXTCLOUD_APP_PASSWORD": "x",
        **settings,
    }
    run = subprocess.run([sys.executable, "-c", READ_CONFIG], env=env, cwd=tmp_path,
                         capture_output=True, text=True, check=True, timeout=60)
    return json.loads(run.stdout.splitlines()[-1]), run.stderr


def test_gabarits_non_resolus_reprennent_les_valeurs_par_defaut(tmp_path) -> None:
    config, stderr = load_config(
        tmp_path, **{name: "${user_config.%s}" % key for name, key in SETTINGS.items()})
    downloads = str(tmp_path / "Downloads")
    assert config == {
        "download": downloads,
        "save_dirs": [],
        "upload": downloads,
        "max_save": 25 * 1024 * 1024,
        "max_upload": 25 * 1024 * 1024,
        "roots": [os.path.realpath(downloads)],
    }
    assert "${user_config" not in json.dumps(config)
    for name in SETTINGS:
        assert name in stderr, f"{name} ignoré sans avertissement"


def test_reglages_renseignes_restent_pris_en_compte(tmp_path) -> None:
    config, _ = load_config(
        tmp_path,
        NEXTCLOUD_DOWNLOAD_DIR=str(tmp_path / "dl"),
        NEXTCLOUD_SAVE_DIRS=os.pathsep.join([str(tmp_path / "a"), "~/b"]),
        NEXTCLOUD_UPLOAD_DIR="~/envoi",
        NEXTCLOUD_MAX_SAVE_BYTES="1000",
        NEXTCLOUD_MAX_UPLOAD_BYTES="2000",
    )
    assert config["download"] == str(tmp_path / "dl")
    assert config["save_dirs"] == [str(tmp_path / "a"), "~/b"]
    assert config["upload"] == "~/envoi"
    assert (config["max_save"], config["max_upload"]) == (1000, 2000)
    real = os.path.realpath(tmp_path)
    assert config["roots"] == [os.path.join(real, "dl"), os.path.join(real, "a"),
                               os.path.join(real, "b")]


def test_reglages_vides_gardent_les_defauts_sans_avertissement(tmp_path) -> None:
    config, stderr = load_config(tmp_path, **{name: "" for name in SETTINGS})
    assert config["download"] == str(tmp_path / "Downloads")
    assert config["max_save"] == 25 * 1024 * 1024
    assert "ignoré" not in stderr
