"""Une meme echeance doit etre lue pareil par la liste et la projection."""

import json

import pytest

from dolibarr_mcp import server


@pytest.mark.parametrize("field", ["date_echeance", "date_lim_reglement"])
async def test_echeances_fournisseurs_identiques(monkeypatch, field):
    rows = [{"id": i, "ref": ref, field: due, "total_ttc": 12, "remaintopay": 12, "statut": 1}
            for i, (ref, due) in enumerate([
                ("SI-TEST-1", "2026-02-26"), ("SI-TEST-2", "2026-03-19"),
                ("SI-TEST-3", "2026-03-27"),
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


@pytest.mark.parametrize("supplier", [False, True])
@pytest.mark.parametrize("dates,expected", [
    ({"date_echeance": "2026-08-10", "date_lim_reglement": "2026-08-20"}, "2026-08-10"),
    ({"date_echeance": None, "date_lim_reglement": "2026-08-20"}, None),
    ({"date_lim_reglement": "2026-08-20"}, "2026-08-20"),
])
async def test_priorite_des_alias_commune_aux_listes_details_et_projection(monkeypatch, supplier, dates, expected):
    row = {"id": 1, "ref": "FACTURE-TEST", "total_ttc": 12,
           "remaintopay": 12, "statut": 1, **dates}
    endpoint = "supplierinvoices" if supplier else "invoices"

    async def get(path, params=None):
        if path == endpoint:
            return [row] if not (params or {}).get("page") else []
        if path == endpoint + "/1":
            return row
        return []

    monkeypatch.setattr(server, "api_get", get)
    name = "supplier_invoice" if supplier else "invoice"
    options = {"include_payments": False, "include_payment_terms": False}
    listed = json.loads(await getattr(server, "dolibarr_list_" + name + "s")(**options))
    detail = json.loads(await getattr(server, "dolibarr_get_" + name)(invoice_id=1, **options))
    forecast = json.loads(await server.dolibarr_cash_forecast())
    assert listed["invoices"][0]["date_due"] == detail["date_due"] == expected
    assert forecast["payable" if supplier else "receivable"][0]["date_due"] == expected
