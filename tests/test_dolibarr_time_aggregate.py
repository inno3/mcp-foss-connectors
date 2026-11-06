"""Totaux agrégés et colonnes de dolibarr_list_time_entries, hors ligne."""

import base64
import json
import os
import time

import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server

JANVIER = 1767225600  # 2026-01-01 00:00:00 GMT
FEVRIER = 1770854400  # 2026-02-12 00:00:00 GMT
FIN_2025 = 1767225540  # 2025-12-31 23:59:00 GMT, déjà 2026 à Paris
NOTE = "Compte rendu détaillé de la réunion, souvent plusieurs paragraphes"


def line(ident, task=42, user=7, seconds=3600, ts=FEVRIER, **over):
    """Écriture telle que tasks/{id}/timespent la rend, task_ref nul comme en production."""
    raw = {
        "fk_project": "9", "project_ref": "PJ2602-0009",
        "fk_task": str(task), "task_ref": None, "task_label": "Audit",
        "timespent_line_id": str(ident),
        "timespent_line_date": ts, "timespent_line_datehour": ts,
        "timespent_line_duration": str(seconds),
        "timespent_line_fk_user": str(user),
        "timespent_line_fk_product": None,
        "timespent_line_note": NOTE,
    }
    raw.update(over)
    return raw


def api(monkeypatch, rows=None, n=7):
    """Une seule tâche à `rows` lignes, ou un projet 42 de n tâches à trois lignes."""
    calls = []

    async def get(endpoint, params=None):
        calls.append(endpoint)
        if endpoint == "projects/42/tasks":
            return [{"id": i} for i in range(1, n + 1)]
        task = int(endpoint.split("/")[1])
        if rows is not None:
            return rows
        return [line(task * 10 + i, task=task, seconds=60) for i in range(3)]

    monkeypatch.setattr(server, "api_get", get)
    return calls


async def listed(**kwargs):
    return json.loads(await server.dolibarr_list_time_entries(**kwargs))


# ---------------------------------------------------------------------------
# Colonnes
# ---------------------------------------------------------------------------


async def test_note_retiree_par_defaut_et_signalee(monkeypatch):
    api(monkeypatch, [line(1)])
    raw = await server.dolibarr_list_time_entries(task_id=42)
    data = json.loads(raw)
    assert "note" not in data["entries"][0]
    assert NOTE not in raw
    assert data["omitted_fields"] == ["note"]
    assert data["entries"][0]["entry_id"] == 1
    assert data["entries"][0]["hours"] == 1.0


async def test_note_rendue_sur_demande(monkeypatch):
    api(monkeypatch, [line(1)])
    data = await listed(task_id=42, fields="entry_id,note")
    assert data["entries"] == [{"entry_id": 1, "note": NOTE}]
    assert "omitted_fields" not in data


async def test_fields_all_rend_toutes_les_colonnes(monkeypatch):
    api(monkeypatch, [line(1)])
    data = await listed(task_id=42, fields="all")
    assert set(data["entries"][0]) == set(server._format_time_entry({}))
    assert data["entries"][0]["note"] == NOTE
    assert "omitted_fields" not in data


async def test_fields_limite_les_lignes_sans_toucher_aux_totaux(monkeypatch):
    api(monkeypatch, [line(1), line(2, user=9)])
    data = await listed(task_id=42, fields="hours")
    assert data["entries"] == [{"hours": 1.0}, {"hours": 1.0}]
    assert data["total_seconds"] == 7200
    assert data["by_user"]["9"]["seconds"] == 3600
    assert data["by_task"]["42"]["seconds"] == 7200
    assert data["returned"] == 2


async def test_fields_tolere_espaces_et_doublons(monkeypatch):
    api(monkeypatch, [line(1)])
    data = await listed(task_id=42, fields=" hours , entry_id,hours ,")
    assert list(data["entries"][0]) == ["hours", "entry_id"]


@pytest.mark.parametrize("fields", ["hours,inconnue", "Note", "*"])
async def test_colonne_inconnue_refusee_avant_tout_appel(monkeypatch, fields):
    calls = api(monkeypatch, [line(1)])
    message = await server.dolibarr_list_time_entries(task_id=42, fields=fields)
    assert "fields" in message and "entry_id" in message and "all" in message
    assert calls == []


async def test_curseur_traverse_les_pages_avec_des_colonnes_reduites(monkeypatch):
    api(monkeypatch)
    ids, cursor = [], ""
    for _ in range(30):
        data = await listed(project_id=42, limit=4, task_limit=2, cursor=cursor,
                            fields="entry_id")
        ids += [r["entry_id"] for r in data["entries"]]
        cursor = data["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 21


async def test_curseur_emis_avant_cette_version_reste_valide(monkeypatch):
    api(monkeypatch)
    ancien = base64.urlsafe_b64encode(json.dumps({
        "v": 2, "scope": [0, 42, 0, "", ""], "task": 2, "offset": 1, "emitted": 4,
    }).encode()).decode()
    data = await listed(project_id=42, limit=1, cursor=ancien)
    assert data["entries"][0]["entry_id"] == 21
    assert data["offset"] == 4


# ---------------------------------------------------------------------------
# Agrégats
# ---------------------------------------------------------------------------


MELANGE = [
    line(1, ts=JANVIER),
    line(2, ts=FEVRIER, seconds=1800),
    line(3, ts=FEVRIER, user=9, seconds=5400),
    line(4, ts=FIN_2025, user=9),
]


@pytest.mark.parametrize("aggregate, key, expected", [
    ("user", "by_user", {"7": 5400, "9": 9000}),
    ("project", "by_project", {"PJ2602-0009": 14400}),
    ("month", "by_month", {"2025-12": 3600, "2026-01": 3600, "2026-02": 7200}),
    ("year", "by_year", {"2025": 3600, "2026": 10800}),
])
async def test_totaux_par_axe(monkeypatch, aggregate, key, expected):
    api(monkeypatch, MELANGE)
    data = await listed(task_id=42, aggregate=aggregate)
    assert data["aggregate"] == aggregate
    assert {k: v["seconds"] for k, v in data[key].items()} == expected
    assert list(data[key]) == sorted(expected)
    assert data[key][next(iter(expected))]["hours"] == expected[next(iter(expected))] / 3600
    assert data["total_seconds"] == 14400 and data["total_hours"] == 4.0
    assert data["matched"] == 4
    assert data["aggregates_complete"] is True and data["truncated"] is False
    assert data["next_cursor"] is None and data["reason"] == "complete"


@pytest.mark.parametrize("aggregate", ["user", "project", "month", "year"])
async def test_agregat_ne_rend_aucune_ligne_ni_autre_ventilation(monkeypatch, aggregate):
    api(monkeypatch, MELANGE)
    raw = await server.dolibarr_list_time_entries(task_id=42, aggregate=aggregate)
    data = json.loads(raw)
    assert NOTE not in raw
    for absent in ("entries", "count", "returned", "limit", "offset", "omitted_fields",
                   *{"by_user", "by_task", "by_project", "by_month", "by_year"} - {f"by_{aggregate}"}):
        assert absent not in data


async def test_agregat_insensible_a_la_casse_et_aux_espaces(monkeypatch):
    api(monkeypatch, MELANGE)
    data = await listed(task_id=42, aggregate=" Month ")
    assert data["aggregate"] == "month" and "by_month" in data


async def test_filtres_appliques_avant_le_cumul(monkeypatch):
    api(monkeypatch, MELANGE)
    data = await listed(task_id=42, aggregate="month", user_id=9, date_start="2026-01-01")
    assert {k: v["seconds"] for k, v in data["by_month"].items()} == {"2026-02": 5400}
    assert data["matched"] == 1


async def test_limit_ne_coupe_pas_un_agregat(monkeypatch):
    api(monkeypatch, [line(i) for i in range(1, 51)])
    data = await listed(task_id=42, aggregate="user", limit=1)
    assert data["total_seconds"] == 180000
    assert data["aggregates_complete"] is True and data["next_cursor"] is None


async def test_ecritures_dedupliquees_avant_le_cumul(monkeypatch):
    api(monkeypatch, [line(1), line(1)])
    data = await listed(task_id=42, aggregate="user")
    assert data["total_seconds"] == 3600


@pytest.fixture
def poste_a_paris():
    avant = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Paris"
    time.tzset()
    yield
    if avant is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = avant
    time.tzset()


async def test_mois_et_annee_lus_en_gmt_comme_le_filtre(monkeypatch, poste_a_paris):
    api(monkeypatch, [line(1, ts=FIN_2025)])
    assert "2025-12" in (await listed(task_id=42, aggregate="month"))["by_month"]
    assert "2025" in (await listed(task_id=42, aggregate="year"))["by_year"]


async def test_ligne_sans_date_comptee_et_nommee(monkeypatch):
    api(monkeypatch, [line(1), line(2, timespent_line_date=None, timespent_line_datehour=None)])
    data = await listed(task_id=42, aggregate="month")
    assert data["by_month"]["sans_date"]["seconds"] == 3600
    assert data["total_seconds"] == 7200


@pytest.mark.parametrize("scope, over, key", [
    ({"task_id": 42}, {"project_ref": None}, "9"),
    ({"project_id": 42}, {"project_ref": None, "fk_project": None}, "42"),
    ({"task_id": 42}, {"project_ref": None, "fk_project": None}, "sans_projet"),
])
async def test_projet_sans_reference(monkeypatch, scope, over, key):
    api(monkeypatch, [line(1, **over)], n=1)
    data = await listed(aggregate="project", **scope)
    assert data["by_project"] == {key: {"hours": 1.0, "seconds": 3600}}


async def test_agregat_exige_toujours_un_perimetre(monkeypatch):
    calls = api(monkeypatch)
    assert "project_id" in await server.dolibarr_list_time_entries(aggregate="project")
    assert calls == []


async def test_axe_inconnu_refuse_avant_tout_appel(monkeypatch):
    calls = api(monkeypatch)
    message = await server.dolibarr_list_time_entries(task_id=42, aggregate="semaine")
    assert "aggregate" in message and "month" in message and "semaine" in message
    assert calls == []


async def test_fields_refuse_avec_aggregate(monkeypatch):
    calls = api(monkeypatch)
    message = await server.dolibarr_list_time_entries(task_id=42, aggregate="user", fields="hours")
    assert "fields" in message and "aggregate" in message
    assert calls == []


# ---------------------------------------------------------------------------
# Agrégats sur plusieurs appels
# ---------------------------------------------------------------------------


async def test_le_curseur_cumule_jusqu_au_total_complet(monkeypatch):
    calls = api(monkeypatch)
    cursor, pages = "", 0
    while True:
        data = await listed(project_id=42, aggregate="user", task_limit=2, cursor=cursor)
        pages += 1
        assert data["tasks_scanned"] <= 2
        cursor = data["next_cursor"]
        if cursor is None:
            break
        assert data["truncated"] is True and data["aggregates_complete"] is False
        assert data["reason"] == "task_limit" and data["scan_note"]
    assert pages == 4
    assert data["aggregates_complete"] is True and "scan_note" not in data
    assert data["by_user"] == {"7": {"hours": 0.35, "seconds": 1260}}
    assert data["total_seconds"] == 1260 and data["matched"] == 21
    assert calls.count("tasks/1/timespent") == 1

    d_un_coup = await listed(project_id=42, aggregate="user", task_limit=100)
    assert d_un_coup["by_user"] == data["by_user"]
    assert d_un_coup["matched"] == data["matched"]


async def test_premiere_page_annoncee_partielle(monkeypatch):
    api(monkeypatch)
    data = await listed(project_id=42, aggregate="year", task_limit=2)
    assert data["total_seconds"] == 360
    assert data["aggregates_complete"] is False and data["truncated"] is True


async def test_erreur_de_tache_garde_le_cumul_et_reprend_sans_doublon(monkeypatch):
    api(monkeypatch)
    get = server.api_get

    async def failing(endpoint, params=None):
        if endpoint == "tasks/2/timespent":
            raise RuntimeError("indisponible")
        return await get(endpoint, params)

    monkeypatch.setattr(server, "api_get", failing)
    data = await listed(project_id=42, aggregate="month")
    assert data["reason"] == "task_error" and data["scan_error"]
    assert data["total_seconds"] == 180 and data["aggregates_complete"] is False

    monkeypatch.setattr(server, "api_get", get)
    resumed = await listed(project_id=42, aggregate="month", cursor=data["next_cursor"])
    assert resumed["total_seconds"] == 1260 and resumed["aggregates_complete"] is True


async def test_decouverte_en_echec_sur_une_reprise_garde_le_cumul(monkeypatch):
    api(monkeypatch)
    get = server.api_get
    first = await listed(project_id=42, aggregate="user", task_limit=2)

    async def failing(endpoint, params=None):
        raise RuntimeError("indisponible")

    monkeypatch.setattr(server, "api_get", failing)
    data = await listed(project_id=42, aggregate="user", task_limit=2, cursor=first["next_cursor"])
    assert data["reason"] == "task_list_error" and data["scan_error"]
    assert data["total_seconds"] == 360 and data["matched"] == 6
    assert data["aggregates_complete"] is False and data["next_cursor"]

    monkeypatch.setattr(server, "api_get", get)
    last = await listed(project_id=42, aggregate="user", cursor=data["next_cursor"])
    assert last["total_seconds"] == 1260 and last["aggregates_complete"] is True


async def test_budget_epuise_rend_un_total_partiel_annonce(monkeypatch):
    import asyncio

    api(monkeypatch)
    get = server.api_get

    async def slow(endpoint, params=None):
        if endpoint.startswith("tasks/2"):
            await asyncio.sleep(10)
        return await get(endpoint, params)

    monkeypatch.setattr(server, "api_get", slow)
    data = await listed(project_id=42, aggregate="user", time_budget=0.05)
    assert data["reason"] == "time_budget" and data["next_cursor"]
    assert data["total_seconds"] == 180 and data["aggregates_complete"] is False


async def test_curseur_lie_au_mode_et_a_l_axe(monkeypatch):
    api(monkeypatch)
    agregat = await listed(project_id=42, aggregate="user", task_limit=1)
    liste = await listed(project_id=42, limit=1)
    for kwargs in (
        {"cursor": agregat["next_cursor"]},
        {"cursor": agregat["next_cursor"], "aggregate": "month"},
        {"cursor": liste["next_cursor"], "aggregate": "user"},
    ):
        message = await server.dolibarr_list_time_entries(project_id=42, **kwargs)
        assert "curseur" in message.lower()


@pytest.mark.parametrize("sums", [None, [], {"7": -1}, {"7": "60"}, {"7": True}])
async def test_curseur_d_agregat_altere_refuse(monkeypatch, sums):
    api(monkeypatch)
    altere = base64.urlsafe_b64encode(json.dumps({
        "v": 2, "scope": [0, 42, 0, "", "", "user"], "task": 2, "offset": 0,
        "emitted": 3, "sums": sums,
    }).encode()).decode()
    message = await server.dolibarr_list_time_entries(project_id=42, aggregate="user", cursor=altere)
    assert "curseur" in message.lower()


async def test_dix_ans_de_mois_tiennent_dans_le_curseur(monkeypatch):
    mois = [line(i, task=1, seconds=60, ts=JANVIER - i * 2_678_400) for i in range(1, 151)]
    calls = []

    async def get(endpoint, params=None):
        calls.append(endpoint)
        if endpoint == "projects/42/tasks":
            return [{"id": 1}, {"id": 2}]
        return mois if endpoint == "tasks/1/timespent" else []

    monkeypatch.setattr(server, "api_get", get)
    first = await listed(project_id=42, aggregate="month", task_limit=1)
    assert first["next_cursor"] and len(first["next_cursor"]) > 2048
    last = await listed(project_id=42, aggregate="month", task_limit=1, cursor=first["next_cursor"])
    assert last["aggregates_complete"] is True
    assert last["total_seconds"] == 9000 and last["by_month"] == first["by_month"]


async def test_les_limites_sont_documentees():
    doc = server.dolibarr_list_time_entries.__doc__ or ""
    for mot in ("aggregate", "fields", "omitted_fields", "sans_date", "une seule ligne"):
        assert mot in doc
