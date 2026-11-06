# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Réglages HedgeDoc : un gabarit `${user_config.x}` non résolu reste sans effet.

Claude Desktop transmet le gabarit tel quel quand un réglage optionnel sans valeur
par défaut n'est pas renseigné (constaté le 06/11/2026 sur Nextcloud et WordPress).
Le manifeste du dépôt passe `hedgedoc_api_token` sans défaut : laissé vide, le jeton
valait `${user_config.hedgedoc_api_token}`. Le serveur le prenait pour un vrai
jeton : en-tête `Authorization: Bearer ${user_config.hedgedoc_api_token}` envoyé à
HedgeDoc tant qu'aucune session n'était ouverte, et tentative de connexion avant
chaque requête en mode anonyme.

Les variables jouées sont celles que le manifeste transmet. L'import est joué dans
un processus séparé, la configuration étant lue une seule fois, au chargement du
module.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "hedgedoc_mcp" / "manifest.json").read_text())
SETTINGS = {
    name: match.group(1)
    for name, value in MANIFEST["server"]["mcp_config"]["env"].items()
    if (match := re.fullmatch(r"\$\{user_config\.([A-Za-z0-9_]+)\}", value))
}
# `_login` remplacé : on compte les tentatives de connexion sans réseau.
READ_CONFIG = """
import asyncio, json
import hedgedoc_mcp.server as s
calls = []
async def fake_login():
    calls.append(1)
    return False
s._login = fake_login
asyncio.run(s._ensure_session())
print(json.dumps({'url': s.HEDGEDOC_URL, 'user': s.HEDGEDOC_USER,
                  'password': s.HEDGEDOC_PASSWORD, 'token': s.API_TOKEN,
                  'headers': s._build_headers(), 'login_calls': len(calls)}))
"""
PLAIN_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


def load_config(tmp_path: Path, **settings: str) -> tuple[dict, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "HOME": str(tmp_path),
        **settings,
    }
    run = subprocess.run([sys.executable, "-c", READ_CONFIG], env=env, cwd=tmp_path,
                         capture_output=True, text=True, check=True, timeout=60)
    return json.loads(run.stdout.splitlines()[-1]), run.stderr


def test_le_manifeste_transmet_le_jeton() -> None:
    assert SETTINGS["HEDGEDOC_API_TOKEN"] == "hedgedoc_api_token"


def test_gabarits_non_resolus_reprennent_les_valeurs_par_defaut(tmp_path) -> None:
    config, stderr = load_config(
        tmp_path, **{name: "${user_config.%s}" % key for name, key in SETTINGS.items()})
    assert config == {"url": "", "user": "", "password": "", "token": "",
                      "headers": PLAIN_HEADERS, "login_calls": 0}
    assert "${user_config" not in json.dumps(config)
    for name in SETTINGS:
        assert name in stderr, f"{name} ignoré sans avertissement"
    assert "gabarit non résolu" in stderr


def test_jeton_seul_en_gabarit_mode_anonyme(tmp_path) -> None:
    """Jeton laissé vide, sans compte : aucun faux jeton, aucune connexion tentée."""
    config, stderr = load_config(
        tmp_path,
        HEDGEDOC_URL="https://pad.example.test",
        HEDGEDOC_USER="",
        HEDGEDOC_PASSWORD="",
        HEDGEDOC_API_TOKEN="${user_config.hedgedoc_api_token}",
    )
    assert config["token"] == ""
    assert "Authorization" not in config["headers"]
    assert config["login_calls"] == 0
    assert "HEDGEDOC_API_TOKEN" in stderr


def test_jeton_seul_en_gabarit_avec_compte(tmp_path) -> None:
    """Jeton laissé vide, compte renseigné : connexion par cookie, sans faux jeton."""
    config, _ = load_config(
        tmp_path,
        HEDGEDOC_URL="https://pad.example.test",
        HEDGEDOC_USER="alice@example.test",
        HEDGEDOC_PASSWORD="x",
        HEDGEDOC_API_TOKEN="${user_config.hedgedoc_api_token}",
    )
    assert (config["user"], config["password"]) == ("alice@example.test", "x")
    assert "Authorization" not in config["headers"]
    assert config["login_calls"] == 1


def test_reglages_renseignes_restent_pris_en_compte(tmp_path) -> None:
    config, stderr = load_config(
        tmp_path,
        HEDGEDOC_URL="https://pad.example.test/",
        HEDGEDOC_USER="",
        HEDGEDOC_PASSWORD="",
        HEDGEDOC_API_TOKEN="hd-token",
    )
    assert config["url"] == "https://pad.example.test"
    assert config["token"] == "hd-token"
    assert config["headers"]["Authorization"] == "Bearer hd-token"
    assert config["login_calls"] == 1
    assert "gabarit non résolu" not in stderr


def test_reglages_vides_gardent_les_defauts_sans_avertissement(tmp_path) -> None:
    config, stderr = load_config(tmp_path, **{name: "" for name in SETTINGS})
    assert config == {"url": "", "user": "", "password": "", "token": "",
                      "headers": PLAIN_HEADERS, "login_calls": 0}
    assert "ignoré" not in stderr
