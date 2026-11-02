"""Régression en lecture seule sur une instance réelle, activée explicitement.

    DOLIBARR_LIVE_READONLY=1 DOLIBARR_URL=... DOLIBARR_API_KEY=... \
    DOLIBARR_LIVE_USER_SEARCH=<login> pytest tests/test_dolibarr_live_readonly.py

Aucune écriture. Une réponse HTTP autre que 200 sort en texte « Erreur HTTP ... » :
le décodage JSON et la clé attendue la font échouer.
"""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DOLIBARR_LIVE_READONLY") != "1",
    reason="régression réelle : poser DOLIBARR_LIVE_READONLY=1",
)


def _rows(raw: str, key: str) -> list:
    assert not raw.startswith("Erreur"), raw
    return json.loads(raw)[key]


async def test_actions_open_without_filter_answers():
    from dolibarr_mcp import server

    _rows(await server.dolibarr_list_actions_open(limit=5), "actions")


async def test_actions_open_for_a_known_user_answers():
    from dolibarr_mcp import server

    search = os.environ["DOLIBARR_LIVE_USER_SEARCH"]
    users = _rows(await server.dolibarr_list_users(search=search, limit=1), "users")
    assert users, f"aucun utilisateur pour {search}"

    actions = _rows(await server.dolibarr_list_actions_open(assigned_to=int(users[0]["id"]), limit=5), "actions")

    # Une action sans assigné revient à son auteur : seule la présence d'assignés se contrôle.
    for action in actions:
        assert not action["assigned_users"] or int(users[0]["id"]) in action["assigned_users"]


async def test_actions_open_stay_sorted_by_planned_date_across_pages():
    from dolibarr_mcp import server

    first = json.loads(await server.dolibarr_list_actions_open(limit=50))
    rows = first["actions"]
    if first["next_cursor"]:
        rows = rows + _rows(await server.dolibarr_list_actions_open(limit=50, cursor=first["next_cursor"]), "actions")

    dates = [row["date"] for row in rows if row["date"]]
    assert dates == sorted(dates)
    assert len({row["id"] for row in rows}) == len(rows)
