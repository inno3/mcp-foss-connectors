"""Tâches masquées par projects/{id}/tasks, sans service distant.

Constaté en réel le 05/11/2026, en 1.8.13, en lecture seule :
- dolibarr_list_tasks(project_id=13) rendait total=0 et total_exact=true ;
- dolibarr_list_time_entries(project_id=13) rendait tasks_total=0,
  aggregates_complete=true et reason=complete ;
- or la tâche 3 porte l'écriture 4, 1 h, rendue avec project_id=13.

Lu dans le cœur 24.0.1 : Project::getLinesArray($user) passe le compte de la clef
en $userp à Task::getTasksArray, qui écarte toutes les tâches d'un projet non
public dont ce compte n'est pas contact, admin et « projet all lire » compris.
GET tasks n'a pas ce filtre. Sans « societe client voir », il ne garde que les
projets des tiers dont le compte est commercial. Les deux filtres portent sur le
projet entier, jamais sur une tâche.
"""

import inspect
import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server
from dolibarr_mcp.server import (
    dolibarr_delete_project,
    dolibarr_get_project,
    dolibarr_list_tasks,
    dolibarr_list_time_entries,
)

PROJECT = 13

# Clés rendues par la 1.8.13 : aucune ne doit disparaître.
LIST_TASKS_KEYS_1_8_13 = {
    "count", "tasks", "total", "total_exact", "total_lower_bound", "returned",
    "truncated", "scan_truncated", "next_cursor", "limit", "offset",
}
TIME_ENTRIES_KEYS_1_8_13 = {
    "task_id", "project_id", "tasks_scanned", "tasks_total", "count", "returned",
    "matched", "total", "total_exact", "total_lower_bound", "scan_truncated", "limit",
    "offset", "truncated", "next_cursor", "reason", "time_budget", "elapsed_seconds",
    "aggregates_scope", "aggregates_complete", "total_hours", "total_seconds",
    "by_user", "by_task", "entries",
}


def _http_error(status: int, message: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://erp.example.test/api/index.php/x")
    response = httpx.Response(
        status, json={"error": {"code": status, "message": message}}, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def _task(task_id: int, project_id: int = PROJECT, **over: object) -> dict:
    """Tâche telle que GET tasks la rend : Task::fetch, avec fk_project et le créateur."""
    row = {
        "id": str(task_id), "ref": f"TK0000-{task_id:04d}", "label": "Tâche masquée",
        "fk_project": str(project_id), "progress": "0", "duration_effective": "3600",
        "planned_workload": "0", "date_start": 1675814400, "date_end": None,
        "fk_user_creat": "5",
    }
    row.update(over)
    return row


def _visible(task_id: int) -> dict:
    """Tâche telle que projects/{id}/tasks la rend : ni créateur ni responsable."""
    return {"id": str(task_id), "ref": f"TK0000-{task_id:04d}", "label": "Tâche visible",
            "progress": "0", "duration_effective": "108000", "planned_workload": "0",
            "date_start": 1686528000, "date_end": None, "fk_user_creat": None}


def _entry(entry_id: int, task_id: int = 3) -> dict:
    return {
        "fk_project": str(PROJECT), "project_ref": "PJ0000-0013", "fk_task": str(task_id),
        "task_ref": f"TK0000-{task_id:04d}", "task_label": "Tâche masquée",
        "timespent_line_id": str(entry_id), "timespent_line_date": 1675857600,
        "timespent_line_datehour": 1675857600, "timespent_line_duration": "3600",
        "timespent_line_fk_user": "1", "timespent_line_fk_product": None,
        "timespent_line_note": "Revue",
    }


def _me(voir: object = 1, socid: object = None) -> dict:
    rights = {"projet": {"lire": 1}, "societe": {"lire": 1}}
    if voir is not None:
        rights["societe"]["client"] = {"voir": voir}
    return {"id": "84", "login": "connecteur", "socid": socid, "rights": rights}


class FakeDolibarr:
    """Routes lues par la découverte des tâches, avec le relevé des appels."""

    def __init__(self, project_tasks=(), task_route=(), *, project_error=None,
                 task_error=None, me=None, me_error=None, entries=None,
                 ignore_page=False, project_id=PROJECT):
        self.project_tasks = list(project_tasks)
        self.task_route = list(task_route)
        self.project_error = project_error
        self.task_error = task_error
        self.me = _me() if me is None else me
        self.me_error = me_error
        self.entries = entries or {}
        self.ignore_page = ignore_page
        self.project_id = project_id
        self.calls: list[tuple[str, dict]] = []

    async def get(self, endpoint: str, params: dict | None = None) -> object:
        self.calls.append((endpoint, dict(params or {})))
        if endpoint == f"projects/{self.project_id}/tasks":
            if self.project_error:
                raise self.project_error
            return list(self.project_tasks)
        if endpoint == "tasks":
            if self.task_error:
                raise self.task_error
            size = int((params or {}).get("limit") or 100)
            page = 0 if self.ignore_page else int((params or {}).get("page") or 0)
            return self.task_route[page * size:(page + 1) * size]
        if endpoint == "users/info":
            if self.me_error:
                raise self.me_error
            return self.me
        if endpoint == f"projects/{self.project_id}":
            return {"id": str(self.project_id), "ref": "PJ0000-0013", "socid": "2"}
        if endpoint.startswith("tasks/") and endpoint.endswith("/timespent"):
            return list(self.entries.get(int(endpoint.split("/")[1]), []))
        raise AssertionError(f"Route inattendue : {endpoint}")

    def endpoints(self) -> list[str]:
        return [endpoint for endpoint, _ in self.calls]


@pytest.fixture(autouse=True)
def _fresh_rights_cache():
    """Le droit du compte est lu une fois par processus : chaque test repart à vide."""
    cache = getattr(server, "_TASK_ROUTE_SCOPE", None)
    if cache is not None:
        cache.clear()
    yield
    if cache is not None:
        cache.clear()


async def _list_tasks(fake: FakeDolibarr, **kwargs: object) -> dict:
    kwargs.setdefault("project_id", PROJECT)
    with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake.get)):
        out = await dolibarr_list_tasks(**kwargs)
    return json.loads(out)


async def _list_entries(fake: FakeDolibarr, **kwargs: object) -> dict:
    with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake.get)):
        out = await dolibarr_list_time_entries(**kwargs)
    return json.loads(out)


class TestListTasksRecoversMaskedTasks:
    async def test_project_13_masked_task_is_listed(self) -> None:
        data = await _list_tasks(FakeDolibarr(task_route=[_task(3)]), limit=1)

        assert [t["id"] for t in data["tasks"]] == ["3"]
        assert data["tasks"][0]["ref"] == "TK0000-0003"
        assert data["tasks"][0]["spent_hours"] == 1.0
        assert data["total"] == 1
        assert data["total_exact"] is True
        assert data["task_discovery"] == {"project_route": 0, "task_route": 1, "exact": True}
        assert "tasks_warning" not in data

    async def test_task_route_is_filtered_on_the_project_and_paged(self) -> None:
        fake = FakeDolibarr(task_route=[_task(3)])
        await _list_tasks(fake)

        params = [p for endpoint, p in fake.calls if endpoint == "tasks"]
        assert params == [{"sqlfilters": "(t.fk_projet:=:13)", "limit": 100,
                           "sortfield": "t.rowid", "page": 0}]

    async def test_visible_project_costs_a_single_request(self) -> None:
        fake = FakeDolibarr(project_tasks=[_visible(51), _visible(41)])
        data = await _list_tasks(fake)

        assert fake.endpoints() == [f"projects/{PROJECT}/tasks"]
        assert [t["id"] for t in data["tasks"]] == ["51", "41"]
        assert data["total"] == 2
        assert data["total_exact"] is True
        assert data["task_discovery"] == {"project_route": 2, "task_route": None, "exact": True}

    async def test_rows_of_other_projects_are_dropped(self) -> None:
        fake = FakeDolibarr(task_route=[_task(3), _task(7, project_id=14), _task(9)])
        data = await _list_tasks(fake)

        assert [t["id"] for t in data["tasks"]] == ["3", "9"]
        assert data["total"] == 2

    async def test_every_page_of_the_task_route_is_read(self) -> None:
        fake = FakeDolibarr(task_route=[_task(i) for i in range(1, 151)])
        data = await _list_tasks(fake, limit=100)

        assert fake.endpoints().count("tasks") == 2
        assert data["total"] == 150
        assert data["total_exact"] is True
        assert data["next_cursor"] == "100"

    async def test_the_creator_is_not_reported_as_assignee(self) -> None:
        """La route projet ne rend aucun responsable : une tâche retrouvée non plus."""
        data = await _list_tasks(FakeDolibarr(task_route=[_task(3, fk_user_creat="5")]))

        assert data["tasks"][0]["assigned_to"] is None

    async def test_the_export_keys_of_1_8_13_are_kept(self) -> None:
        data = await _list_tasks(FakeDolibarr(task_route=[_task(3)]))

        assert LIST_TASKS_KEYS_1_8_13 <= set(data)


class TestListTasksNeverCertifiesAPossibleMask:
    async def test_task_route_failure_leaves_an_empty_project_uncertified(self) -> None:
        fake = FakeDolibarr(task_error=_http_error(403, "Forbidden"))
        data = await _list_tasks(fake)

        assert data["tasks"] == []
        assert data["total"] is None
        assert data["total_exact"] is False
        assert data["total_lower_bound"] == 0
        assert data["scan_truncated"] is True
        assert data["task_discovery"]["exact"] is False
        assert data["task_discovery"]["task_route"] is None
        assert "403" in data["task_discovery"]["task_route_error"]
        assert "projects/13/tasks" in data["tasks_warning"]
        assert "GET tasks" in data["tasks_warning"]

    async def test_task_route_failure_keeps_visible_tasks_certified(self) -> None:
        """Le filtre de rôle porte sur le projet entier : une tâche visible les montre toutes."""
        fake = FakeDolibarr(project_tasks=[_visible(51)], task_error=_http_error(403))
        data = await _list_tasks(fake)

        assert data["total"] == 1
        assert data["total_exact"] is True

    async def test_an_incomplete_task_route_scan_is_not_certified(self) -> None:
        fake = FakeDolibarr(task_route=[_task(i) for i in range(1, 201)], ignore_page=True)
        data = await _list_tasks(fake)

        assert data["total"] is None
        assert data["total_exact"] is False
        assert data["total_lower_bound"] == 100
        assert data["tasks_warning"]

    async def test_a_no_task_found_404_reads_as_an_empty_task_route(self) -> None:
        """Des cœurs antérieurs rendent 404 « No task found » au lieu d'une liste vide."""
        fake = FakeDolibarr(task_error=_http_error(404, "Not Found: No task found"), me=_me(voir=1))
        data = await _list_tasks(fake)

        assert data["total"] == 0
        assert data["total_exact"] is True
        assert data["task_discovery"]["task_route"] == 0

    async def test_another_task_route_404_is_not_read_as_empty(self) -> None:
        fake = FakeDolibarr(task_error=_http_error(404, "Route not found"), me=_me(voir=1))
        data = await _list_tasks(fake)

        assert data["total_exact"] is False
        assert "404" in data["task_discovery"]["task_route_error"]

    async def test_an_empty_project_is_certified_when_the_account_sees_every_thirdparty(self) -> None:
        fake = FakeDolibarr(me=_me(voir=1))
        data = await _list_tasks(fake)

        assert data["total"] == 0
        assert data["total_exact"] is True
        assert fake.endpoints().count("users/info") == 1
        assert ("users/info", {"includepermissions": 1}) in fake.calls

    async def test_an_empty_project_stays_uncertified_without_the_thirdparty_right(self) -> None:
        data = await _list_tasks(FakeDolibarr(me=_me(voir=None)))

        assert data["total"] is None
        assert data["total_exact"] is False
        assert "societe client voir" in data["tasks_warning"]

    async def test_an_external_account_never_certifies_an_empty_project(self) -> None:
        data = await _list_tasks(FakeDolibarr(me=_me(voir=1, socid="12")))

        assert data["total_exact"] is False

    async def test_unreadable_rights_leave_the_zero_uncertified(self) -> None:
        data = await _list_tasks(FakeDolibarr(me_error=_http_error(403)))

        assert data["total"] is None
        assert data["total_exact"] is False

    async def test_rights_without_permissions_leave_the_zero_uncertified(self) -> None:
        data = await _list_tasks(FakeDolibarr(me={"id": "84", "socid": None}))

        assert data["total_exact"] is False

    async def test_rights_are_read_once_per_process(self) -> None:
        fake = FakeDolibarr(me=_me(voir=1))
        await _list_tasks(fake)
        await _list_tasks(fake)

        assert fake.endpoints().count("users/info") == 1

    async def test_a_failed_rights_read_is_not_remembered(self) -> None:
        fake = FakeDolibarr(me_error=_http_error(503))
        await _list_tasks(fake)
        await _list_tasks(fake)

        assert fake.endpoints().count("users/info") == 2

    async def test_an_unknown_project_still_reports_its_error(self) -> None:
        fake = FakeDolibarr(project_error=_http_error(404, "Project not found"))
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake.get)):
            out = await dolibarr_list_tasks(project_id=PROJECT)

        assert out.startswith("Erreur 404")
        assert "Project not found" in out

    async def test_a_project_route_error_is_bypassed_when_the_task_route_finds_tasks(self) -> None:
        fake = FakeDolibarr(project_error=_http_error(403), task_route=[_task(3)])
        data = await _list_tasks(fake)

        assert [t["id"] for t in data["tasks"]] == ["3"]
        assert data["total_exact"] is True
        assert data["task_discovery"]["project_route"] is None
        assert "403" in data["task_discovery"]["project_route_error"]

    async def test_a_task_route_row_without_project_is_not_trusted(self) -> None:
        data = await _list_tasks(FakeDolibarr(task_route=[{"id": "3", "ref": "TK"}]))

        assert data["total_exact"] is False
        assert data["task_discovery"]["task_route"] is None


class TestListTimeEntriesOnMaskedTasks:
    async def test_project_13_hours_are_counted(self) -> None:
        fake = FakeDolibarr(task_route=[_task(3)], entries={3: [_entry(4)]})
        data = await _list_entries(fake, project_id=PROJECT, limit=1, task_limit=100)

        assert data["tasks_total"] == 1
        assert data["total"] == 1
        assert data["total_exact"] is True
        assert data["aggregates_complete"] is True
        assert data["reason"] == "complete"
        assert data["total_seconds"] == 3600
        assert [e["entry_id"] for e in data["entries"]] == [4]
        assert data["task_discovery"] == {"project_route": 0, "task_route": 1, "exact": True}

    async def test_an_uncertified_discovery_never_reports_complete_aggregates(self) -> None:
        fake = FakeDolibarr(task_error=_http_error(403))
        data = await _list_entries(fake, project_id=PROJECT, limit=1, task_limit=100)

        assert data["tasks_total"] is None
        assert data["total"] is None
        assert data["total_exact"] is False
        assert data["aggregates_complete"] is False
        assert data["scan_truncated"] is True
        assert data["reason"] == "tasks_unverified"
        assert data["truncated"] is False
        assert data["next_cursor"] is None
        assert "GET tasks" in data["tasks_warning"]
        assert "tasks_warning" in data["scan_note"]

    async def test_an_empty_project_is_certified_with_the_thirdparty_right(self) -> None:
        data = await _list_entries(FakeDolibarr(me=_me(voir=1)), project_id=PROJECT)

        assert data["tasks_total"] == 0
        assert data["aggregates_complete"] is True
        assert data["reason"] == "complete"

    async def test_visible_tasks_keep_the_1_8_13_reading(self) -> None:
        fake = FakeDolibarr(project_tasks=[_visible(51)], entries={51: [_entry(9, 51)]})
        data = await _list_entries(fake, project_id=PROJECT)

        assert "tasks" not in fake.endpoints()
        assert data["aggregates_complete"] is True
        assert data["total_seconds"] == 3600
        assert TIME_ENTRIES_KEYS_1_8_13 <= set(data)

    async def test_the_task_mode_does_not_discover_anything(self) -> None:
        fake = FakeDolibarr(entries={3: [_entry(4)]})
        data = await _list_entries(fake, task_id=3)

        assert fake.endpoints() == ["tasks/3/timespent"]
        assert "task_discovery" not in data
        assert data["aggregates_complete"] is True

    async def test_a_cursor_resumes_on_recovered_tasks(self) -> None:
        fake = FakeDolibarr(task_route=[_task(3), _task(8)],
                            entries={3: [_entry(4)], 8: [_entry(11, 8)]})
        first = await _list_entries(fake, project_id=PROJECT, limit=1)
        second = await _list_entries(fake, project_id=PROJECT, limit=1, cursor=first["next_cursor"])

        assert [e["entry_id"] for e in first["entries"]] == [4]
        assert [e["entry_id"] for e in second["entries"]] == [11]
        assert second["next_cursor"] is None


class TestDeleteProjectDiagnostic:
    async def _delete(self, fake: FakeDolibarr) -> dict:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake.get)), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock,
                   side_effect=_http_error(500, "child element exists")):
            return json.loads(await dolibarr_delete_project(project_id=PROJECT))

    async def test_masked_tasks_are_counted(self) -> None:
        data = await self._delete(FakeDolibarr(task_route=[_task(3)]))

        assert data["tasks_remaining"] == 1

    async def test_a_possibly_masked_zero_is_not_announced(self) -> None:
        data = await self._delete(FakeDolibarr(task_error=_http_error(403)))

        assert data["tasks_remaining"] is None
        assert "GET tasks" in data["tasks_warning"]


class TestContractIsDocumented:
    def test_input_schemas_are_unchanged(self) -> None:
        assert list(inspect.signature(dolibarr_list_tasks).parameters) == [
            "project_id", "assigned_to", "limit", "cursor"]
        assert list(inspect.signature(dolibarr_list_time_entries).parameters) == [
            "task_id", "project_id", "user_id", "date_start", "date_end", "limit",
            "cursor", "task_limit", "time_budget"]

    @pytest.mark.parametrize("tool", [dolibarr_list_tasks, dolibarr_list_time_entries])
    def test_the_residual_limit_is_documented(self, tool) -> None:
        doc = inspect.getdoc(tool)
        assert "GET tasks" in doc
        assert "societe client voir" in doc
        assert "tasks_warning" in doc
        assert "task_discovery" in doc

    def test_get_project_does_not_promise_tasks(self) -> None:
        doc = inspect.getdoc(dolibarr_get_project)
        assert "incluant tâches" not in doc
        assert "dolibarr_list_tasks" in doc

    def test_the_version_is_bumped(self) -> None:
        assert server.__version__ == "1.8.15"
