"""Une meme echeance doit etre lue pareil par la liste et la projection."""

import json

import pytest

from dolibarr_mcp import server


@pytest.mark.parametrize("field", ["date_echeance", "date_lim_reglement"])
async def test_echeances_fournisseurs_identiques(monkeypatch, field):
    rows = [{"id": i, "ref": ref, field: due, "total_ttc": 12, "remaintopay": 12, "statut": 1}
            for i, (ref, due) in enumerate([
                ("SI2602-0084", "2026-02-26"), ("SI2603-0086", "2026-03-19"),
                ("SI2603-0085", "2026-03-27"),
            ], 1)]

    async def get(endpoint, params=None):
        if endpoint == "supplierinvoices":
            return rows if not (params or {}).get("page") else []
        return []

    monkeypatch.setattr(server, "api_get", get)
    monkeypatch.setattr(server, "_today_iso", lambda: "2026-10-07")
    listed = json.loads(await server.dolibarr_list_supplier_invoices(include_payments=False))
    forecast = json.loads(await server.dolibarr_cash_forecast())
    assert {i["ref"]: i["date_due"] for i in listed["invoices"]} == {
        i["ref"]: i["date_due"] for i in forecast["payable"]}
    assert all(i["bucket"] == "en retard" for i in forecast["payable"])
