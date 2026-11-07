"""Filtre assigned_to de dolibarr_list_tasks, 1.8.18.

Jusqu'à la 1.8.17, le filtre comparait fk_user_resp, que ni projects/{id}/tasks ni
GET tasks ne rendent en production : il ne retenait aucune tâche, total certifié à 0.
Constaté le 05/11/2026 sur le projet 4, et le 07/11/2026 sur la tâche 44, qui porte
deux responsables internes dans tasks/44/contacts. L'affectation d'une tâche est un
contact interne (TASKEXECUTIVE ou TASKCONTRIBUTOR) : le filtre les lit tâche par tâche,
et ne certifie le total que si toutes ont été lues.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from dolibarr_mcp import server
from dolibarr_mcp.server import dolibarr_list_tasks

PROJECT = 4
USER = 5


def _http_error(status: int, message: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://erp.example.test/api/index.php/x")
    response = httpx.Response(
        status, json={"error": {"code": status, "message": message}}, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def _task(task_id: int, project_id: int = PROJECT) -> dict:
    return {"id": str(task_id), "ref": f"TK0000-{task_id:04d}", "label": "Tâche",
            "fk_project": str(project_id), "progress": "0", "duration_effective": "3600",
            "planned_workload": "0", "date_start": None, "date_end": None,
            "fk_user_creat": "9"}


def _contact(user_id: int, role: str = "TASKEXECUTIVE", source: str = "internal") -> dict:
    """Ligne de tasks/{id}/contacts telle que la production la rend (tâche 44)."""
    return {"id": str(user_id), "source": source, "firstname": "Prénom", "lastname": "Nom",
            "login": "login", "email": "x@example.test", "code": role,
            "libelle": "Responsable" if role == "TASKEXECUTIVE" else "Intervenant",
            "fk_c_type_contact": "180", "rowid": str(200 + user_id)}


class FakeDolibarr:
    def __init__(self, project_tasks=(), task_route=(), contacts=None, contact_errors=(),
                 project_id=PROJECT):
        self.project_tasks = list(project_tasks)
        self.task_route = list(task_route)
        self.contacts = contacts or {}
        self.contact_errors = set(contact_errors)
        self.project_id = project_id
        self.calls: list[str] = []

    async def get(self, endpoint: str, params: dict | None = None) -> object:
        self.calls.append(endpoint)
        if endpoint == f"projects/{self.project_id}/tasks":
            return list(self.project_tasks)
        if endpoint == "tasks":
            page = int((params or {}).get("page") or 0)
            size = int((params or {}).get("limit") or 100)
            return self.task_route[page * size:(page + 1) * size]
        if endpoint == "users/info":
            return {"id": "84", "socid": None,
                    "rights": {"projet": {"lire": 1}, "societe": {"client": {"voir": 1}}}}
        if endpoint.startswith("tasks/") and endpoint.endswith("/contacts"):
            task_id = int(endpoint.split("/")[1])
            if task_id in self.contact_errors:
                raise _http_error(500, "Erreur")
            rows = self.contacts.get(task_id, [])
            return list(rows) if isinstance(rows, list) else rows
        raise AssertionError(f"Route inattendue : {endpoint}")

    def contact_calls(self) -> list[str]:
        return [c for c in self.calls if c.endswith("/contacts")]


@pytest.fixture(autouse=True)
def _fresh_rights_cache():
    cache = getattr(server, "_TASK_ROUTE_SCOPE", None)
    if cache is not None:
        cache.clear()
    yield
    if cache is not None:
        cache.clear()


async def _list(fake: FakeDolibarr, **kwargs: object) -> dict:
    kwargs.setdefault("project_id", PROJECT)
    with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake.get)):
        out = await dolibarr_list_tasks(**kwargs)
    return json.loads(out)


class TestAssignedToReadsTaskContacts:
    async def test_keeps_tasks_where_the_user_is_an_internal_contact(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44), _task(45), _task(46)], contacts={
            44: [_contact(USER), _contact(16)],
            45: [_contact(16)],
            46: [_contact(USER, "TASKCONTRIBUTOR")],
        })
        data = await _list(fake, assigned_to=USER)
        assert [t["id"] for t in data["tasks"]] == ["44", "46"]
        assert data["total"] == 2 and data["total_exact"] is True
        assert [t["assigned_to"] for t in data["tasks"]] == [USER, USER]
        assert data["tasks"][0]["assigned_roles"] == ["TASKEXECUTIVE"]
        assert data["tasks"][1]["assigned_roles"] == ["TASKCONTRIBUTOR"]

    async def test_reports_every_role_of_the_user_once(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44)], contacts={
            44: [_contact(USER), _contact(USER, "TASKCONTRIBUTOR"), _contact(USER)]})
        data = await _list(fake, assigned_to=USER)
        assert data["tasks"][0]["assigned_roles"] == ["TASKEXECUTIVE", "TASKCONTRIBUTOR"]

    async def test_external_contact_with_the_same_id_is_not_the_user(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44)], contacts={
            44: [_contact(USER, source="external")]})
        data = await _list(fake, assigned_to=USER)
        assert data["tasks"] == [] and data["total"] == 0 and data["total_exact"] is True

    async def test_task_without_contacts_is_left_out_and_total_certified(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44), _task(45)], contacts={44: [_contact(USER)]})
        data = await _list(fake, assigned_to=USER)
        assert [t["id"] for t in data["tasks"]] == ["44"]
        assert data["total_exact"] is True
        assert data["assignment_check"] == {
            "user_id": USER, "tasks_checked": 2, "tasks_unverified": [], "exact": True,
            "time_budget": server.ASSIGNMENT_CHECK_BUDGET}

    async def test_works_on_tasks_recovered_by_get_tasks(self) -> None:
        fake = FakeDolibarr(task_route=[_task(3), _task(4)], contacts={4: [_contact(USER)]})
        data = await _list(fake, assigned_to=USER)
        assert [t["id"] for t in data["tasks"]] == ["4"]
        assert data["task_discovery"]["task_route"] == 2
        assert data["total_exact"] is True

    async def test_paginates_after_filtering(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(i) for i in (44, 45, 46)],
                            contacts={i: [_contact(USER)] for i in (44, 45, 46)})
        first = await _list(fake, assigned_to=USER, limit=2)
        assert [t["id"] for t in first["tasks"]] == ["44", "45"]
        assert first["total"] == 3 and first["next_cursor"] == "2"
        rest = await _list(fake, assigned_to=USER, limit=2, cursor=first["next_cursor"])
        assert [t["id"] for t in rest["tasks"]] == ["46"]


class TestAssignedToNeverCertifiesAnUnreadTask:
    async def test_contact_read_failure_leaves_total_uncertified(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44), _task(45)],
                            contacts={44: [_contact(USER)]}, contact_errors={45})
        data = await _list(fake, assigned_to=USER)
        assert [t["id"] for t in data["tasks"]] == ["44"]
        assert data["total"] is None and data["total_exact"] is False
        assert data["total_lower_bound"] == 1
        assert data["assignment_check"]["tasks_unverified"] == ["45"]
        assert data["assignment_check"]["exact"] is False
        assert "assignment_check" in data["scan_note"]

    async def test_exhausted_budget_leaves_remaining_tasks_unverified(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44), _task(45)],
                            contacts={44: [_contact(USER)], 45: [_contact(USER)]})
        with patch.object(server, "ASSIGNMENT_CHECK_BUDGET", 0.0):
            data = await _list(fake, assigned_to=USER)
        assert data["tasks"] == []
        assert data["total"] is None and data["total_exact"] is False
        assert data["assignment_check"]["tasks_unverified"] == ["44", "45"]
        assert data["assignment_check"]["tasks_checked"] == 0

    async def test_unreadable_contact_list_is_not_an_empty_one(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44)])
        fake.contacts = {44: {"error": "pas une liste"}}
        data = await _list(fake, assigned_to=USER)
        assert data["total_exact"] is False
        assert data["assignment_check"]["tasks_unverified"] == ["44"]

    async def test_recovered_task_with_unreadable_contacts(self) -> None:
        fake = FakeDolibarr(task_route=[_task(3)], contact_errors={3})
        data = await _list(fake, assigned_to=USER)
        assert data["task_discovery"]["exact"] is True
        assert data["total_exact"] is False
        assert data["assignment_check"]["tasks_unverified"] == ["3"]


class TestUnfilteredListIsUnchanged:
    async def test_no_contact_is_read_without_the_filter(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44), _task(45)], contacts={44: [_contact(USER)]})
        data = await _list(fake)
        assert fake.contact_calls() == []
        assert "assignment_check" not in data
        assert all("assigned_roles" not in t for t in data["tasks"])
        assert [t["assigned_to"] for t in data["tasks"]] == ["9", "9"]

    async def test_contacts_are_read_once_per_task(self) -> None:
        fake = FakeDolibarr(project_tasks=[_task(44), _task(45)], contacts={44: [_contact(USER)]})
        await _list(fake, assigned_to=USER)
        assert sorted(fake.contact_calls()) == ["tasks/44/contacts", "tasks/45/contacts"]


class TestContractIsDocumented:
    def test_docstring_describes_the_filter_and_its_cost(self) -> None:
        doc = dolibarr_list_tasks.__doc__
        assert "tasks/{id}/contacts" in doc
        assert "assignment_check" in doc
        assert "TASKEXECUTIVE" in doc and "TASKCONTRIBUTOR" in doc
        assert "ne retient rien" not in doc

    def test_version(self) -> None:
        assert server.__version__ == "1.8.18"
