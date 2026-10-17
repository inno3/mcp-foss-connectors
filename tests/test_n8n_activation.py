"""Activation n8n : régressions de 8243999 et contrat de transport 1.3.0."""

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("N8N_BASE_URL", "https://n8n.example.test")
os.environ.setdefault("N8N_API_KEY", "n8n-test")

from n8n_mcp import server


class TestActivateDeactivateUsePost:
    async def test_activate_posts_to_activate_endpoint(self) -> None:
        mock = AsyncMock(return_value={"id": "abc", "name": "WF test", "active": True})
        with patch.object(server, "_api_request", mock):
            out = json.loads(await server.n8n_activate_workflow("abc"))
        mock.assert_awaited_once_with("POST", "workflows/abc/activate", json={})
        assert out == {"success": True, "workflow_id": "abc", "active": True,
                       "name": "WF test"}

    async def test_deactivate_posts_to_deactivate_endpoint(self) -> None:
        mock = AsyncMock(return_value={"id": "abc", "name": "WF test", "active": False})
        with patch.object(server, "_api_request", mock):
            out = json.loads(await server.n8n_deactivate_workflow("abc"))
        mock.assert_awaited_once_with("POST", "workflows/abc/deactivate", json={})
        assert out == {"success": True, "workflow_id": "abc", "active": False,
                       "name": "WF test"}

    async def test_activate_sends_empty_json_body(self) -> None:
        mock = AsyncMock(return_value={"active": True})
        with patch.object(server, "_api_request", mock):
            await server.n8n_activate_workflow("xyz")
        assert mock.call_args.kwargs["json"] == {}


@pytest.fixture
def http_transport(monkeypatch):
    """Passe par le vrai transport du connecteur, avec un serveur HTTP simulé."""
    client = httpx.AsyncClient
    calls = []

    def install(*responses):
        steps = iter(responses)

        def handle(request):
            calls.append((request.method, request.url.path, json.loads(request.content)))
            response = next(steps)
            if isinstance(response, Exception):
                raise response
            return response

        transport = httpx.MockTransport(handle)
        monkeypatch.setattr(server.httpx, "AsyncClient",
                            lambda **kwargs: client(transport=transport, **kwargs))
        return calls

    monkeypatch.setattr(server, "N8N_BASE_URL", "https://n8n.example.test")
    monkeypatch.setattr(server, "_RETRY_DELAY", 0)
    server._applied_writes.set(None)
    yield install
    server._applied_writes.set(None)


@pytest.mark.parametrize("action", ["activate", "deactivate"])
@pytest.mark.parametrize(("response", "attempts", "detail"), [
    (httpx.ReadError(""), 1, "inconnu"),
    (httpx.ReadTimeout(""), 1, "inconnu"),
    (httpx.ConnectError(""), 3, "non appliquée"),
    (httpx.Response(503, text="unavailable"), 1, "inconnu"),
    (httpx.Response(200, text="<html>proxy</html>"), 1, "inconnu"),
    (httpx.Response(400, text="invalid workflow"), 1, "non appliquée"),
    (httpx.Response(403, text="forbidden"), 1, "permissions insuffisantes"),
])
async def test_activation_preserves_transport_errors(
    http_transport, action, response, attempts, detail,
) -> None:
    calls = http_transport(*([response] * attempts))
    result = await getattr(server, f"n8n_{action}_workflow")("abc")
    assert calls == [("POST", f"/api/v1/workflows/abc/{action}", {})] * attempts
    assert result.startswith("Erreur")
    assert detail in result
    if detail == "inconnu":
        assert "relire" in result
        assert f"POST workflows/abc/{action}" in result


@pytest.mark.parametrize("action", ["activate", "deactivate"])
async def test_activation_preserves_applied_writes(http_transport, action) -> None:
    calls = http_transport(httpx.Response(200, json={"id": "abc"}), httpx.ReadError(""))
    await server.api_put("workflows/abc", {"name": "WF test"})
    result = await getattr(server, f"n8n_{action}_workflow")("abc")
    assert calls == [("PUT", "/api/v1/workflows/abc", {"name": "WF test"}),
                     ("POST", f"/api/v1/workflows/abc/{action}", {})]
    assert "inconnu" in result
    assert "déjà appliquées" in result
    assert "PUT workflows/abc" in result
