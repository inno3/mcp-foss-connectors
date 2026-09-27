"""Chaque outil d'écriture rend explicitement un refus du compte API."""

import inspect

import httpx
import pytest

from dolibarr_mcp import server

WRITE_VERBS = {"log", "assign", "unassign", "update", "delete", "create", "close", "add",
               "validate", "set", "toggle", "upload", "build", "transfer"}
WRITES = [name for name, value in vars(server).items()
          if name.startswith("dolibarr_") and inspect.iscoroutinefunction(value)
          and name.split("_")[1] in WRITE_VERBS]

OVERRIDES = {
    "upload_document": {"ref": "DEMO-1"},
    "log_time": {"task_id": 1, "duration": 1},
    "update_time_entry": {"note": "Exemple"},
    "update_thirdparty": {"name": "Exemple"},
    "update_contact": {"firstname": "Exemple"},
    "close_ticket": {"ticket_id": 1},
    "delete_ticket": {"ticket_id": 1},
    "add_ticket_message": {"ticket_id": 1, "message": "Exemple"},
    "update_ticket": {"ticket_id": 1, "subject": "Exemple"},
    "update_project": {"title": "Exemple"},
    "update_invoice": {"note_public": "Exemple"},
    "update_proposal": {"title": "Exemple"},
    "update_proposal_line": {"fields": {"qty": 2}},
    "create_bank_entry": {"direction": "credit"},
    "transfer_between_accounts": {"to_account_id": 2},
}


@pytest.mark.parametrize("name", WRITES)
async def test_each_write_reports_forbidden(monkeypatch, tmp_path, name):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(args)
        response = httpx.Response(403, request=httpx.Request("POST", "https://erp.example.test/api"))
        response.raise_for_status()

    for helper in ("api_get", "api_post", "api_put", "api_delete", "_web_post"):
        monkeypatch.setattr(server, helper, forbidden)
    tool = getattr(server, name)
    values = {"lines": [{"desc": "Exemple", "qty": 1, "subprice": 10, "tva_tx": 20}],
              "entry_ids": [1], "date": "2026-09-28", "direction": "credit",
              "payment_type": "VIR", "module": "invoice", "ref": "DEMO-1"}
    path = tmp_path / "exemple.txt"
    path.write_text("Exemple fictif")
    values["file_path"] = str(path)
    kwargs = {}
    for param in inspect.signature(tool).parameters.values():
        if param.default is inspect.Parameter.empty:
            kwargs[param.name] = values.get(param.name, 1 if param.annotation in (int, float, bool) else "Exemple")
    kwargs.update(OVERRIDES.get(name.removeprefix("dolibarr_"), {}))
    output = await tool(**kwargs)
    assert calls, output
    assert "403" in output and ("permission" in output.lower() or "droit" in output.lower()), output


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_permission_denied_is_never_retried(monkeypatch, method):
    calls = []

    async def request(self, method, url, **kwargs):
        calls.append(method)
        return httpx.Response(403, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    with pytest.raises(httpx.HTTPStatusError):
        await server._api_request(method, "example")
    assert calls == [method]
