"""Protection publique de 55e6278, adaptee aux deux sondes getMe de la 0.17.1."""

import asyncio
import base64
import json
import os

import httpx
import pytest

os.environ.setdefault("KANBOARD_URL", "https://kb.example.test/jsonrpc.php")
os.environ.setdefault("KANBOARD_USER", "human")
os.environ.setdefault("KANBOARD_TOKEN", "test-primary")

from kanboard_mcp import server  # noqa: E402


@pytest.fixture
def wire(monkeypatch):
    for name in ("_identity_cache", "_my_id_cache", "_my_name_cache"):
        monkeypatch.setattr(server, name, {})
    monkeypatch.setattr(server, "_identity_lock", asyncio.Lock())
    for name, value in {"KANBOARD_USER": "human", "KANBOARD_TOKEN": "test-primary",
                        "KANBOARD_USER_ALT": "bot", "KANBOARD_TOKEN_ALT": "test-agent"}.items():
        monkeypatch.setattr(server, name, value)
    state = {"calls": [], "probes": [], "identity": {"id": 84, "username": "bot"}, "author": 84}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            method = kwargs["json"]["method"]
            state["calls"].append(method)
            user = base64.b64decode(kwargs["headers"]["Authorization"].split()[1]).decode().split(":")[0]
            identity = state["identity"] if user == "bot" else {"id": 4, "username": "human"}
            if method == "getMe":
                state["probes"].append(user)
            results = {"getMe": identity, "createTask": 42,
                       "getTask": {"id": 42, "creator_id": state["author"], "title": "Test", "project_id": 1}}
            assert method in results, method
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": results[method]},
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kwargs: Client())
    return state


@pytest.mark.parametrize("missing", ["KANBOARD_USER_ALT", "KANBOARD_TOKEN_ALT"])
async def test_agent_absent_ne_declenche_aucune_ecriture(monkeypatch, wire, missing):
    monkeypatch.setattr(server, missing, "")
    result = await server.kanboard_create_task(project_id=1, title="Test", as_user="agent")
    assert "createTask" not in wire["calls"]
    assert "KANBOARD" in result


async def test_token_primary_refuse_avant_creation(wire):
    wire["identity"] = {"id": 4, "username": "human"}
    result = await server.kanboard_create_task(project_id=1, title="Test", as_user="agent")
    assert "createTask" not in wire["calls"]
    assert result.startswith("Refus")
    assert "identite incoherente" in result


async def test_getme_absent_refuse_avant_creation(wire):
    wire["identity"] = None
    await server.kanboard_create_task(project_id=1, title="Test", as_user="agent")
    assert "createTask" not in wire["calls"]


async def test_identite_verifiee_avant_creation_et_cachee(wire):
    for _ in range(2):
        out = json.loads(await server.kanboard_create_task(project_id=1, title="Test", as_user="agent"))
        assert out["creator_id"] == 84
        assert out["as_user"] == "agent"
    assert wire["calls"][0] == "getMe"
    assert wire["probes"] == ["bot", "human"]
    assert wire["calls"][:2] == ["getMe", "getMe"]
    assert wire["calls"].count("createTask") == 2


async def test_auteur_reel_est_relu_et_ecart_signale(wire):
    wire["author"] = 4
    out = json.loads(await server.kanboard_create_task(project_id=1, title="Test", as_user="agent"))
    assert out["creator_id"] == 4
    assert "author_mismatch" in out
