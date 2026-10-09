"""Pagination du projet 300 et annulation des lectures lentes, hors ligne."""

import asyncio
import json
import time

import pytest

from dolibarr_mcp import server


def row(task, ident):
    return {"timespent_line_id": ident, "fk_task": task, "timespent_line_duration": 60,
            "timespent_line_date": "2026-09-15", "timespent_line_fk_user": 7}


def api(monkeypatch, n=7, delay=0):
    calls = []

    async def get(endpoint, params=None):
        calls.append(endpoint)
        if endpoint == "projects/300/tasks":
            return [{"id": i} for i in range(1, n + 1)]
        task = int(endpoint.split('/')[1])
        await asyncio.sleep(delay)
        return [row(task, task * 10 + i) for i in range(3)]

    monkeypatch.setattr(server, "api_get", get)
    return calls


async def test_pages_par_tache_sans_rebalayer_les_precedentes(monkeypatch):
    calls = api(monkeypatch)
    ids, cursor = [], ""
    for _ in range(30):
        data = json.loads(await server.dolibarr_list_time_entries(
            project_id=300, limit=4, task_limit=2, cursor=cursor))
        assert data["tasks_scanned"] <= 2
        ids += [r["entry_id"] for r in data["entries"]]
        cursor = data["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 21
    assert calls.count("tasks/1/timespent") == 1
    assert not data["truncated"]


async def test_budget_coupe_requete_en_cours_et_garde_les_resultats(monkeypatch):
    api(monkeypatch, n=100, delay=0.02)
    start = time.monotonic()
    data = json.loads(await server.dolibarr_list_time_entries(project_id=300, time_budget=0.035))
    assert time.monotonic() - start < 0.2
    assert data["truncated"] and data["next_cursor"]
    assert data["reason"] == "time_budget"
    assert data["tasks_scanned"] == 1 and len(data["entries"]) == 3
    assert data["total"] is None and not data["aggregates_complete"]


async def test_decouverte_des_taches_dans_le_budget(monkeypatch):
    cancelled = []

    async def get(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(server, "api_get", get)
    data = json.loads(await server.dolibarr_list_time_entries(project_id=300, time_budget=0.01))
    assert data["reason"] == "time_budget" and data["tasks_scanned"] == 0
    assert data["next_cursor"] and cancelled


async def test_plafond_de_taches_meme_si_filtre_ne_renvoie_rien(monkeypatch):
    api(monkeypatch, n=100)
    data = json.loads(await server.dolibarr_list_time_entries(project_id=300, user_id=99, task_limit=2))
    assert data["entries"] == [] and data["tasks_scanned"] == 2
    assert data["truncated"] and data["next_cursor"]


async def test_curseur_refuse_si_perimetre_change(monkeypatch):
    api(monkeypatch)
    first = json.loads(await server.dolibarr_list_time_entries(project_id=300, limit=1))
    second = await server.dolibarr_list_time_entries(project_id=300, user_id=99, cursor=first["next_cursor"])
    assert "curseur" in second.lower() and "filtre" in second.lower()


async def test_erreur_de_tache_annoncee_et_reprise_sur_la_meme(monkeypatch):
    api(monkeypatch)
    get = server.api_get

    async def failing(endpoint, params=None):
        if endpoint == "tasks/2/timespent":
            raise RuntimeError("indisponible")
        return await get(endpoint, params)

    monkeypatch.setattr(server, "api_get", failing)
    data = json.loads(await server.dolibarr_list_time_entries(project_id=300))
    assert data["tasks_scanned"] == 1 and data["truncated"]
    assert data["reason"] == "task_error" and data["next_cursor"]
    monkeypatch.setattr(server, "api_get", get)
    resumed = json.loads(await server.dolibarr_list_time_entries(project_id=300, cursor=data["next_cursor"]))
    assert resumed["entries"][0]["task_id"] == 2


async def test_annulation_externe_propagee(monkeypatch):
    api(monkeypatch, delay=10)
    task = asyncio.create_task(server.dolibarr_list_time_entries(project_id=300))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
