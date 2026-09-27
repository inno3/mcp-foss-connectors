"""Contrat d'export sur données fictives, sans service distant."""

import json

import pytest

from dolibarr_mcp import server

PAGED_LISTS = [
    ("projects", "projects", {}),
    ("invoices", "invoices", {"status": "all"}),
    ("supplier_invoices", "invoices", {"status": "all"}),
    ("proposals", "proposals", {}),
    ("thirdparties", "thirdparties", {}),
    ("contacts", "contacts", {"thirdparty_id": 7}),
    ("tickets", "tickets", {}),
    ("actions_open", "actions", {}),
    ("users", "users", {}),
    ("products", "products", {}),
    ("bank_accounts", "accounts", {}),
]


@pytest.mark.parametrize("name,key,kwargs", PAGED_LISTS)
async def test_export_250_objects_in_three_pages(monkeypatch, name, key, kwargs):
    rows = [{"id": i, "ref": f"DEMO-{i}", "total_ttc": "12", "type": "1"}
            for i in range(1, 251)]

    async def get(endpoint, params=None):
        params = params or {}
        start = params.get("page", 0) * params.get("limit", 100)
        return rows[start:start + params.get("limit", 100)]

    monkeypatch.setattr(server, "api_get", get)
    tool = getattr(server, "dolibarr_list_" + name)
    cursor = ""
    union = []
    for expected in (100, 100, 50):
        result = json.loads(await tool(limit=100, cursor=cursor, **kwargs))
        assert result["total"] == 250
        assert result["returned"] == result["count"] == expected
        assert len(result[key]) == expected
        assert result["truncated"] is (expected == 100)
        union.extend(row["id"] for row in result[key])
        cursor = result["next_cursor"]
    assert cursor is None
    assert len(union) == len(set(union)) == 250


async def test_export_filters_after_scan_and_deduplicates(monkeypatch):
    rows = [{"id": i, "title": "retenu" if i > 100 else "écarté"} for i in range(1, 251)]
    pages = [rows[:100], rows[99:199], rows[199:]]

    async def get(endpoint, params=None):
        return pages[params["page"]] if params["page"] < len(pages) else []

    monkeypatch.setattr(server, "api_get", get)
    data = json.loads(await server.dolibarr_list_projects(search="retenu", limit=100))
    assert data["total"] == 150
    assert data["returned"] == 100
    data = json.loads(await server.dolibarr_list_projects(search="retenu", limit=100,
                                                         cursor=data["next_cursor"]))
    assert data["returned"] == 50
    assert data["projects"][0]["id"] == 201


async def test_repeated_page_never_claims_exact_total(monkeypatch):
    async def get(endpoint, params=None):
        return [{"id": i} for i in range(100)]

    monkeypatch.setattr(server, "api_get", get)
    data = json.loads(await server.dolibarr_list_proposals(limit=100))
    assert data["total"] is None
    assert data["scan_truncated"] is True
    assert data["truncated"] is True
    assert data["next_cursor"] is None


async def test_source_error_is_not_an_empty_export(monkeypatch):
    async def get(endpoint, params=None):
        return {"error": "interdit"}

    monkeypatch.setattr(server, "api_get", get)
    assert "Erreur" in await server.dolibarr_list_proposals()
