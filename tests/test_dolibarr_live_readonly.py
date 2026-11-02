"""Régression en lecture seule sur une instance réelle, activée explicitement.

    DOLIBARR_LIVE_READONLY=1 DOLIBARR_URL=... DOLIBARR_API_KEY=... \
    DOLIBARR_LIVE_USER_SEARCH=<login> pytest tests/test_dolibarr_live_readonly.py

Aucune écriture. Chaque réponse HTTP est contrôlée avant les normalisations du
connecteur : tout code autre que 200 fait échouer la recette, même si le corps
JSON est accepté par le transport. Les corps et identifiants ne sont pas journalisés.
"""

import json
import os

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DOLIBARR_LIVE_READONLY") != "1",
    reason="régression réelle : poser DOLIBARR_LIVE_READONLY=1",
)


def _capture_readonly_requests(original, statuses):
    """Observe les statuts réels et refuse une méthode qui pourrait écrire."""
    async def request(client, method, *args, **kwargs):
        assert method.upper() == "GET", "La recette autorise seulement GET"
        response = await original(client, method, *args, **kwargs)
        statuses.append(response.status_code)
        return response
    return request


def _assert_readonly_statuses(statuses):
    assert statuses, "Aucune réponse HTTP observée"
    assert all(status == 200 for status in statuses), f"Statuts HTTP observés : {statuses}"


@pytest.fixture
def strict_http(monkeypatch):
    statuses = []
    monkeypatch.setattr(
        httpx.AsyncClient, "request",
        _capture_readonly_requests(httpx.AsyncClient.request, statuses),
    )
    yield statuses
    _assert_readonly_statuses(statuses)


def _rows(raw: str, key: str) -> list:
    assert not raw.startswith("Erreur"), "Erreur du connecteur pendant la recette"
    result = json.loads(raw)
    assert key in result, "Clé attendue absente de la réponse"
    return result[key]


async def test_actions_open_without_filter_answers(strict_http):
    from dolibarr_mcp import server

    _rows(await server.dolibarr_list_actions_open(limit=5), "actions")


async def test_actions_open_for_a_known_user_answers(strict_http):
    from dolibarr_mcp import server

    search = os.environ["DOLIBARR_LIVE_USER_SEARCH"]
    users = _rows(await server.dolibarr_list_users(search=search, limit=1), "users")
    assert users, "Aucun utilisateur pour le filtre configuré"

    actions = _rows(await server.dolibarr_list_actions_open(assigned_to=int(users[0]["id"]), limit=5), "actions")

    # Une action sans assigné revient à son auteur : seule la présence d'assignés se contrôle.
    for action in actions:
        assert not action["assigned_users"] or int(users[0]["id"]) in action["assigned_users"]


async def test_actions_open_stay_sorted_by_planned_date_across_pages(strict_http):
    from dolibarr_mcp import server

    # Avec le filtre par défaut, la liste peut être vide : le tri ne prouverait rien.
    first = json.loads(await server.dolibarr_list_actions_open(limit=50, include_system=True))
    rows = first["actions"]
    assert rows, "aucun événement à trier"
    if first["next_cursor"]:
        rows = rows + _rows(
            await server.dolibarr_list_actions_open(limit=50, include_system=True, cursor=first["next_cursor"]),
            "actions",
        )

    dates = [row["date"] for row in rows if row["date"]]
    assert dates == sorted(dates)
    assert len({row["id"] for row in rows}) == len(rows)
