"""Un modulepart inconnu du cœur Dolibarr se dit comme tel (1.8.7).

Constat du 16/10/2026 : ``dolibarr_list_documents(module="meetingnotes",
object_id=186)`` rendait une erreur 500 brute, « Modulepart meetingnotes not
implemented yet ». L'API documents du cœur code en dur la liste des modulepart
qu'elle sert ; un module maison n'y figure pas. Ce n'est ni un objet absent ni
une panne : l'outil le dit et oriente vers la route du module.
"""

import json
import os

import httpx

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server  # noqa: E402


def _http_error(status: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://erp.example.test/api/index.php/documents")
    body = json.dumps({"error": {"code": status, "message": message}})
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


async def test_un_modulepart_non_implemente_est_nomme(monkeypatch):
    async def get(endpoint, params=None):
        raise _http_error(500, "Internal Server Error: Modulepart meetingnotes not implemented yet.")

    monkeypatch.setattr(server, "api_get", get)
    out = json.loads(await server.dolibarr_list_documents(module="meetingnotes", object_id=186))

    assert out["success"] is False
    assert out["unsupported_modulepart"] is True
    assert "dolibarr_list_meetingnote_documents" in out["message"]
    assert "not implemented yet" in out["message"]


async def test_une_autre_erreur_500_reste_une_erreur(monkeypatch):
    async def get(endpoint, params=None):
        raise _http_error(500, "Internal Server Error: database is locked")

    monkeypatch.setattr(server, "api_get", get)
    out = await server.dolibarr_list_documents(module="invoice", object_id=1)
    assert "unsupported_modulepart" not in out


def test_la_docstring_dit_que_meetingnotes_nest_pas_supporte():
    doc = server.dolibarr_list_documents.__doc__
    assert "« meetingnotes »" in doc
    assert "n'est pas" in doc and "supporté" in doc
    assert "dolibarr_list_meetingnote_documents" in doc
