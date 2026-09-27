"""Contrat d'export sur données fictives, sans service distant."""

import json
import os

import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

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


@pytest.mark.parametrize("supplier", [False, True])
async def test_invoice_fields_keep_zero_null_and_missing(monkeypatch, supplier):
    rows = [
        {"id": 1, "total_ht": "10", "total_ttc": "12", "remaintopay": 0,
         "fk_statut": 0, "date_echeance": "2026-09-30", "date_lim_reglement": "2026-09-30"},
        {"id": 2, "total_ht": None, "total_ttc": None, "remaintopay": None},
        {"id": 3},
    ]

    async def get(endpoint, params=None):
        if endpoint.endswith("/payments"):
            return [{"id": 71, "amount": "12", "type": "VIR", "date": "2026-09-01"}]
        return rows

    monkeypatch.setattr(server, "api_get", get)
    tool = server.dolibarr_list_supplier_invoices if supplier else server.dolibarr_list_invoices
    data = json.loads(await tool(status="all"))
    first, null, missing = data["invoices"]
    assert first["remaining_to_pay"] == 0
    assert first["status"] == "0"
    assert first["date_due"] == "2026-09-30"
    assert first["payments"][0]["amount"] == 12
    assert null["total_ht"] is None
    assert "total_ht" not in null["unavailable_fields"]
    assert missing["total_ht"] is None
    assert "total_ht" in missing["unavailable_fields"]


async def test_supplier_due_date_alias_and_unavailable_payments(monkeypatch):
    import httpx

    async def get(endpoint, params=None):
        if endpoint.endswith("/payments"):
            response = httpx.Response(403, request=httpx.Request("GET", "https://erp.test/payments"))
            response.raise_for_status()
        return [{"id": 1, "date_echeance": "2026-10-15", "total_ttc": 50}]

    monkeypatch.setattr(server, "api_get", get)
    row = json.loads(await server.dolibarr_list_supplier_invoices())["invoices"][0]
    assert row["date_due"] == "2026-10-15"
    assert row["payments"] is None
    assert "403" in row["payments_error"]
    assert row["remaining_to_pay"] is None


async def test_proposal_preserves_draft_and_absent_amount(monkeypatch):
    async def get(endpoint, params=None):
        return [{"id": 1, "fk_statut": 0, "total_ttc": None, "date_validity": None}]

    monkeypatch.setattr(server, "api_get", get)
    row = json.loads(await server.dolibarr_list_proposals())["proposals"][0]
    assert str(row["status"]) == "0"
    assert row["total_ht"] is None
    assert row["total_ttc"] is None
    assert "total_ht" in row["unavailable_fields"]
    assert "total_ttc" not in row["unavailable_fields"]
    assert "date_validity" not in row["unavailable_fields"]


async def test_credit_notes_are_exported_as_objects(monkeypatch):
    async def get(endpoint, params=None):
        if endpoint.endswith("/payments"):
            return []
        return [{"id": 9, "type": "2", "total_ttc": -12, "fk_facture_source": 8}]

    monkeypatch.setattr(server, "api_get", get)
    data = json.loads(await server.dolibarr_list_invoices(status="all"))
    assert data["credit_notes"][0]["id"] == 9
    assert data["credit_notes"][0]["source_invoice_id"] == 8


async def test_revenue_scan_refuses_partial_total(monkeypatch):
    async def get(endpoint, params=None):
        return [{"id": i, "total_ht": 10, "date": "2026-09-01"} for i in range(100)]

    monkeypatch.setattr(server, "api_get", get)
    monkeypatch.setattr(server, "REVENUE_SCAN_MAX_PAGES", 1)
    data = await server.dolibarr_get_revenue_summary(year=2026, month=9)
    assert "incomplet" in data.lower()
    assert "ytd_ca_ht" not in data


async def test_protocol_initialize_and_list_tools_in_memory(capsys):
    import asyncio
    from contextlib import suppress

    import anyio
    from mcp import ClientSession

    client_write, server_read = anyio.create_memory_object_stream(10)
    server_write, client_read = anyio.create_memory_object_stream(10)
    task = asyncio.create_task(server.mcp._mcp_server.run(
        server_read, server_write, server.mcp._mcp_server.create_initialization_options()))
    try:
        async with asyncio.timeout(5):
            async with ClientSession(client_read, client_write) as session:
                result = await session.initialize()
                tools = await session.list_tools()
                assert result.serverInfo.version == "1.8.0"
                assert len(tools.tools) == 78
                assert len({tool.name for tool in tools.tools}) == 78
                proposal = next(t for t in tools.tools if t.name == "dolibarr_list_proposals")
                assert "cursor" in proposal.inputSchema["properties"]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert capsys.readouterr().err == ""


async def test_revenue_missing_amount_is_not_zero(monkeypatch):
    async def get(endpoint, params=None):
        return [{"id": 1, "date": "2026-09-01", "total_ttc": 12}]

    monkeypatch.setattr(server, "api_get", get)
    result = await server.dolibarr_get_revenue_summary(year=2026, month=9)
    assert "incomplet" in result.lower() and "ytd_ca_ht" not in result


async def test_cash_unreadable_payment_links_are_announced(monkeypatch):
    import httpx

    async def get(endpoint, params=None):
        if endpoint == "bankaccounts":
            return [{"id": 1, "courant": 1}]
        if endpoint.endswith("/lines"):
            return [{"id": 7, "amount": 12, "dateo": "2026-09-01"}]
        httpx.Response(403, request=httpx.Request("GET", "https://erp.example.test/links")).raise_for_status()

    monkeypatch.setattr(server, "api_get", get)
    result = json.loads(await server.dolibarr_get_revenue_summary(year=2026, month=9, basis="encaissees"))
    assert result["truncated"] is True
    assert result["unresolved_entries"] == 1


def test_payment_amount_distinguishes_null_and_absent():
    absent = server._format_payment({"id": 1})
    null = server._format_payment({"id": 2, "amount": None})
    assert absent["amount"] is null["amount"] is None
    assert "amount" in absent["unavailable_fields"]
    assert "amount" not in null["unavailable_fields"]


async def test_task_contact_dedup_preserves_distinct_roles(monkeypatch):
    async def get(endpoint, params=None):
        manager = {"id": 7, "rowid": 1, "source": "internal", "code": "TASKEXECUTIVE"}
        worker = {"id": 7, "rowid": 2, "source": "internal", "code": "TASKCONTRIBUTOR"}
        return [manager, manager, worker]

    monkeypatch.setattr(server, "api_get", get)
    data = json.loads(await server.dolibarr_list_task_contacts(task_id=1))
    assert data["total"] == 2
    assert [c["link_id"] for c in data["contacts"]] == [1, 2]
