# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Réglages WordPress : un gabarit `${user_config.x}` non résolu reste sans effet.

Constaté le 06/11/2026 dans l'environnement du processus installé (1.6.0) : Claude
Desktop transmet le gabarit tel quel quand un réglage optionnel sans valeur par
défaut n'est pas renseigné. `WP_PROD_SUBSITES` et `WP_TEST_SUBSITES` valaient
`${user_config.wp_prod_subsites}` et `${user_config.wp_test_subsites}`. Le serveur
les lisait comme un JSON invalide : sous-sites écartés, mais avertissement trompeur
à chaque démarrage, sans le nom de la variable, et qu'on ne distinguait plus d'une
vraie déclaration mal formée.

Les variables jouées sont celles que le manifeste transmet : un réglage ajouté
plus tard est couvert sans retoucher ce test. L'import est joué dans un processus
séparé, la configuration étant lue une seule fois, au chargement du module.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "wordpress_mcp" / "manifest.json").read_text())
SETTINGS = {
    name: match.group(1)
    for name, value in MANIFEST["server"]["mcp_config"]["env"].items()
    if (match := re.fullmatch(r"\$\{user_config\.([A-Za-z0-9_]+)\}", value))
}
READ_CONFIG = (
    "import json, wordpress_mcp.server as s; print(json.dumps(s._SERVER_CONFIGS))"
)


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


def test_le_manifeste_transmet_les_sous_sites() -> None:
    assert SETTINGS["WP_PROD_SUBSITES"] == "wp_prod_subsites"
    assert SETTINGS["WP_TEST_SUBSITES"] == "wp_test_subsites"


def test_gabarits_non_resolus_reprennent_les_valeurs_par_defaut(tmp_path) -> None:
    config, stderr = load_config(
        tmp_path, **{name: "${user_config.%s}" % key for name, key in SETTINGS.items()})
    empty = {"url": "", "user": "", "password": "", "verify_ssl": True,
             "server_auth_user": "", "server_auth_pass": "", "subsites": {}}
    assert config == {"prod": empty, "test": empty}
    assert "${user_config" not in json.dumps(config)
    for name in SETTINGS:
        assert name in stderr, f"{name} ignoré sans avertissement"
    assert "gabarit non résolu" in stderr
    assert "JSON invalide" not in stderr, "gabarit pris pour une déclaration mal formée"


def test_sous_sites_seuls_en_gabarit_comme_en_production(tmp_path) -> None:
    """Cas relevé le 06/11/2026 : seuls les deux réglages sans défaut restent en gabarit."""
    config, stderr = load_config(
        tmp_path,
        WP_PROD_URL="https://wp.example.test",
        WP_PROD_USER="alice",
        WP_PROD_APP_PASSWORD="x",
        WP_TEST_VERIFY_SSL="false",
        WP_PROD_SUBSITES="${user_config.wp_prod_subsites}",
        WP_TEST_SUBSITES="${user_config.wp_test_subsites}",
    )
    assert config["prod"]["subsites"] == config["test"]["subsites"] == {}
    assert config["prod"]["url"] == "https://wp.example.test"
    assert config["test"]["verify_ssl"] is False
    assert "WP_PROD_SUBSITES" in stderr and "WP_TEST_SUBSITES" in stderr
    assert "JSON invalide" not in stderr


def test_reglages_renseignes_restent_pris_en_compte(tmp_path) -> None:
    config, stderr = load_config(
        tmp_path,
        WP_PROD_URL="https://wp.example.test/",
        WP_PROD_USER="alice",
        WP_PROD_APP_PASSWORD="abcd efgh",
        WP_TEST_URL="https://recette.example.test",
        WP_TEST_USER="bob",
        WP_TEST_APP_PASSWORD="ijkl",
        WP_TEST_VERIFY_SSL="0",
        WP_TEST_SERVER_AUTH_USER="proxy",
        WP_TEST_SERVER_AUTH_PASS="secret",
        WP_PROD_SUBSITES='{"en": "/en"}',
        WP_TEST_SUBSITES='{"en": {"lang": "en"}}',
    )
    assert config["prod"]["url"] == "https://wp.example.test"
    assert (config["prod"]["user"], config["prod"]["password"]) == ("alice", "abcd efgh")
    assert config["prod"]["subsites"] == {"en": "/en"}
    assert config["test"] == {
        "url": "https://recette.example.test", "user": "bob", "password": "ijkl",
        "verify_ssl": False, "server_auth_user": "proxy", "server_auth_pass": "secret",
        "subsites": {"en": {"lang": "en"}},
    }
    assert "gabarit non résolu" not in stderr


def test_json_mal_forme_garde_son_avertissement(tmp_path) -> None:
    config, stderr = load_config(tmp_path, WP_PROD_SUBSITES='{"en": ')
    assert config["prod"]["subsites"] == {}
    assert "JSON invalide" in stderr
    assert "gabarit non résolu" not in stderr


def test_reglages_vides_gardent_les_defauts_sans_avertissement(tmp_path) -> None:
    config, stderr = load_config(tmp_path, **{name: "" for name in SETTINGS})
    assert config["test"]["verify_ssl"] is True
    assert config["prod"]["subsites"] == config["test"]["subsites"] == {}
    assert "ignoré" not in stderr
