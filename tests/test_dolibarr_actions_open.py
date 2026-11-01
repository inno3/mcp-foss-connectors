"""Contrat de dolibarr_list_actions_open sur données fictives, sans service distant.

La route agendaevents lit llx_actioncomm, dont la clé primaire est `id` : un tri
sur t.rowid y rend un HTTP 503. Ses lignes portent les propriétés de l'objet
ActionComm (userownerid, socid, percentage, datef) et non les colonnes SQL.
"""

import json
import os

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server

PRIMARY_KEYS = {
    "projects": "rowid", "invoices": "rowid", "supplierinvoices": "rowid",
    "proposals": "rowid", "thirdparties": "rowid", "contacts": "rowid",
    "tickets": "rowid", "agendaevents": "id", "users": "rowid", "products": "rowid",
    "bankaccounts": "rowid",
}

ROUTES = [
    ("projects", "projects", {}),
    ("invoices", "invoices", {"status": "all"}),
    ("supplier_invoices", "supplierinvoices", {"status": "all"}),
    ("proposals", "proposals", {}),
    ("thirdparties", "thirdparties", {}),
    ("contacts", "contacts", {"thirdparty_id": 7}),
    ("tickets", "tickets", {}),
    ("actions_open", "agendaevents", {}),
    ("users", "users", {}),
    ("products", "products", {}),
    ("bank_accounts", "bankaccounts", {}),
]


def _http_503(endpoint: str, column: str) -> httpx.HTTPStatusError:
    message = f"Service Unavailable: Unknown column '{column}' in 'order clause'"
    request = httpx.Request("GET", f"https://erp.example.test/api/index.php/{endpoint}")
    response = httpx.Response(503, request=request, json={"error": {"code": 503, "message": message}})
    return httpx.HTTPStatusError(message, request=request, response=response)


def _mysql_like(rows: list, calls: list | None = None):
    """Refuse comme la base un tri sur une clé que la table de la route n'a pas."""

    async def get(endpoint, params=None):
        params = params or {}
        if calls is not None:
            calls.append((endpoint, dict(params)))
        route = endpoint.split("/")[0]
        wrong = "rowid" if PRIMARY_KEYS.get(route, "rowid") == "id" else None
        for column in str(params.get("sortfield", "")).split(","):
            if wrong and column.rsplit(".", 1)[-1] == wrong:
                raise _http_503(endpoint, column)
        start = params.get("page", 0) * params.get("limit", 100)
        return rows[start:start + params.get("limit", 100)]

    return get


def _action(id_: int, owner: int = 0, assigned=None, **extra) -> dict:
    row = {
        "id": str(id_), "label": f"Action {id_}", "code": "AC_OTH", "type": "Autre",
        "percentage": "0", "datep": 1793500000 + id_, "datef": 1793503600 + id_,
        "socid": "12", "fk_project": "34", "location": "", "fulldayevent": "0",
        "userownerid": str(owner) if owner else None, "authorid": "9",
        "userassigned": assigned if assigned is not None else {},
    }
    row.update(extra)
    return row


async def test_without_filter_sorts_by_planned_date_then_id(monkeypatch):
    calls: list = []
    monkeypatch.setattr(server, "api_get", _mysql_like([_action(1, 5), _action(2, 6)], calls))

    result = json.loads(await server.dolibarr_list_actions_open(limit=5))

    assert [row["id"] for row in result["actions"]] == ["1", "2"]
    endpoint, params = calls[0]
    assert endpoint == "agendaevents"
    assert params["sortfield"] == "t.datep,t.id"
    assert params["sortorder"] == "ASC"
    assert params["sqlfilters"] == "(t.percent:<>:100)"


async def test_assigned_to_keeps_owner_and_coassigned(monkeypatch):
    rows = [
        _action(1, owner=5),
        _action(2, owner=6),
        _action(3, owner=6, assigned={"5": {"id": "5", "mandatory": 0}, "6": {"id": "6"}}),
        _action(4, owner=6, assigned=[{"id": 5, "mandatory": 0}]),
    ]
    monkeypatch.setattr(server, "api_get", _mysql_like(rows))

    result = json.loads(await server.dolibarr_list_actions_open(assigned_to=5, limit=20))

    assert [row["id"] for row in result["actions"]] == ["1", "3", "4"]
    assert result["total"] == 3


async def test_assigned_to_falls_back_on_author_without_any_assignee(monkeypatch):
    rows = [_action(1, authorid="5"), _action(2, authorid="6")]
    monkeypatch.setattr(server, "api_get", _mysql_like(rows))

    result = json.loads(await server.dolibarr_list_actions_open(assigned_to=5, limit=20))

    assert [row["id"] for row in result["actions"]] == ["1"]


async def test_fields_come_from_the_rest_properties(monkeypatch):
    rows = [_action(1, owner=5, assigned={"5": {"id": "5"}, "8": {"id": "8"}}, contact_id="77")]
    monkeypatch.setattr(server, "api_get", _mysql_like(rows))

    action = json.loads(await server.dolibarr_list_actions_open(limit=5))["actions"][0]

    assert action["percent"] == "0"
    assert action["date"] and action["date_end"]
    assert action["thirdparty_id"] == "12"
    assert action["project_id"] == "34"
    assert action["contact_id"] == "77"
    assert action["assigned_to"] == "5"
    assert action["assigned_users"] == [5, 8]


async def test_sql_column_names_remain_readable(monkeypatch):
    rows = [{"id": "1", "label": "x", "percent": "50", "datep": 1793500000, "datep2": 1793503600,
             "fk_soc": "12", "fk_project": "34", "fk_contact": "77", "fk_user_action": "5"}]
    monkeypatch.setattr(server, "api_get", _mysql_like(rows))

    action = json.loads(await server.dolibarr_list_actions_open(assigned_to=5, limit=5))["actions"][0]

    assert action["percent"] == "50"
    assert action["date_end"]
    assert action["thirdparty_id"] == "12"
    assert action["contact_id"] == "77"
    assert action["assigned_to"] == "5"


async def test_an_http_error_stays_an_error(monkeypatch):
    async def down(endpoint, params=None):
        raise _http_503(endpoint, "t.datep")

    monkeypatch.setattr(server, "api_get", down)

    result = await server.dolibarr_list_actions_open(limit=5)

    assert result.startswith("Erreur HTTP 503")


@pytest.mark.parametrize("name,route,kwargs", ROUTES)
async def test_every_scanned_route_breaks_ties_on_its_own_primary_key(monkeypatch, name, route, kwargs):
    calls: list = []
    monkeypatch.setattr(server, "api_get", _mysql_like([], calls))

    result = await getattr(server, "dolibarr_list_" + name)(limit=1, **kwargs)

    assert not result.startswith("Erreur"), result
    assert calls[0][0].split("/")[0] == route
    columns = [column.rsplit(".", 1)[-1] for column in calls[0][1]["sortfield"].split(",")]
    assert PRIMARY_KEYS[route] in columns


@pytest.mark.parametrize("sortfield,expected", [
    ("t.datep", "t.datep,t.id"),
    ("t.datep,t.id", "t.datep,t.id"),
    ("t.id", "t.id"),
    ("t.fk_id", "t.fk_id,t.id"),
])
async def test_scan_list_adds_the_route_key_once(monkeypatch, sortfield, expected):
    calls: list = []
    monkeypatch.setattr(server, "api_get", _mysql_like([], calls))

    await server._scan_list("agendaevents", {"sortfield": sortfield})

    assert calls[0][1]["sortfield"] == expected


async def test_scan_list_defaults_on_the_route_key(monkeypatch):
    calls: list = []
    monkeypatch.setattr(server, "api_get", _mysql_like([], calls))

    await server._scan_list("agendaevents", {})
    await server._scan_list("contacts", {})

    assert calls[0][1]["sortfield"] == "t.id"
    assert calls[1][1]["sortfield"] == "t.rowid"
