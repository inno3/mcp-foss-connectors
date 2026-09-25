# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs n8n : nommées, codées, et disant l'état de l'écriture.

Même défaut que celui remonté en production sur Dolibarr le 08/09/2026 puis sur
Kanboard le 24/09/2026 : `_format_error` finissait par `return f"Erreur : {exc}"`
et `_api_request` n'attrapait que `ConnectError` / `ReadTimeout` / `WriteTimeout`.
Une exception httpx sans message — `ReadError`, `ReadTimeout` nu,
`RemoteProtocolError` — rendait donc « Erreur : » suivi de rien.

Le risque propre à n8n : les écritures y sont lourdes et enchaînées. Mettre à
jour un workflow puis le réactiver, c'est deux requêtes ; rejouer la première
après une réponse perdue écrase une version que l'appelant croit encore en
place. Ces tests figent cinq garanties :

1. aucun message nu ;
2. toute la famille `httpx.TransportError` est traitée ;
3. le code d'erreur de l'API est rendu ;
4. une écriture n'est JAMAIS rejouée automatiquement une fois la requête
   partie, et l'erreur dit ce qu'il advient de cette écriture ;
5. les écritures déjà appliquées dans le même appel d'outil sont nommées.
"""

import json
import os

import httpx
import pytest

os.environ.setdefault("N8N_BASE_URL", "https://n8n.example.test")
os.environ.setdefault("N8N_API_KEY", "n8n-test")

from n8n_mcp import server  # noqa: E402
from n8n_mcp.server import (  # noqa: E402
    N8nAPIError,
    N8nTransportError,
    _api_request,
    _format_error,
    _write_journal,
)

_URL = "https://n8n.example.test/api/v1/workflows"


class _ScriptedClient:
    """httpx.AsyncClient minimal : rejoue une séquence d'exceptions ou de réponses."""

    def __init__(self, script: list, calls: list):
        self.script = script
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _resp(status: int, *, json_body=None, text: str = "") -> httpx.Response:
    request = httpx.Request("GET", _URL)
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=request)
    return httpx.Response(status, text=text, request=request)


def _http_error(status: int, text: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _URL)
    response = httpx.Response(status, text=text, request=request)
    return httpx.HTTPStatusError("erreur", request=request, response=response)


@pytest.fixture
def scripted(monkeypatch):
    """Installe un client scénarisé et supprime les délais de backoff."""
    calls: list = []

    def install(*script):
        steps = list(script)
        monkeypatch.setattr(server.httpx, "AsyncClient",
                            lambda **kw: _ScriptedClient(steps, calls))
        return calls

    monkeypatch.setattr(server, "_RETRY_DELAY", 0)
    return install


@pytest.fixture(autouse=True)
def journal_propre():
    """Filet pour les tests synchrones, qui partagent la tâche nulle."""
    yield
    server._applied_writes.set(None)


# ---------------------------------------------------------------------------
# 1. Le message n'est jamais nu
# ---------------------------------------------------------------------------


class TestMessageJamaisNu:
    def test_read_error_vide_est_nomme(self) -> None:
        msg = _format_error(httpx.ReadError(""))
        assert msg.strip() != "Erreur :"
        assert "ReadError" in msg and "connexion coupée" in msg

    def test_read_timeout_vide_est_nomme(self) -> None:
        msg = _format_error(httpx.ReadTimeout(""))
        assert "ReadTimeout" in msg and "délai" in msg

    def test_exception_generique_sans_message_porte_son_type(self) -> None:
        assert _format_error(RuntimeError("")) == "Erreur RuntimeError : sans message."

    def test_exception_generique_avec_message_inchangee(self) -> None:
        assert _format_error(RuntimeError("boum")) == "Erreur : boum"

    def test_aucune_famille_de_transport_ne_rend_un_message_nu(self) -> None:
        familles = [
            httpx.ReadError(""), httpx.WriteError(""), httpx.ConnectError(""),
            httpx.ReadTimeout(""), httpx.WriteTimeout(""), httpx.ConnectTimeout(""),
            httpx.PoolTimeout(""), httpx.RemoteProtocolError(""),
            httpx.LocalProtocolError(""), httpx.ProxyError(""),
        ]
        for exc in familles:
            msg = _format_error(exc)
            assert msg.strip() not in ("Erreur :", "Erreur réseau :"), exc
            assert type(exc).__name__ in msg


class TestMessagesHttpConnus:
    """Compatibilité : les messages sur lesquels les outils s'appuient."""

    def test_401(self) -> None:
        assert _format_error(_http_error(401)).startswith(
            "Erreur 401 : clé API n8n invalide")

    def test_403(self) -> None:
        assert _format_error(_http_error(403)).startswith(
            "Erreur 403 : permissions insuffisantes")

    def test_404(self) -> None:
        assert _format_error(_http_error(404)).startswith(
            "Erreur 404 : ressource non trouvée dans n8n")

    def test_400_rend_le_corps(self) -> None:
        msg = _format_error(_http_error(400, "request/body must have property nodes"))
        assert "400" in msg and "must have property nodes" in msg

    def test_statut_inconnu_sans_corps_ne_finit_pas_nu(self) -> None:
        assert _format_error(_http_error(418, "")).strip() != "Erreur HTTP 418 :"


# ---------------------------------------------------------------------------
# 2. Toute la famille TransportError est traitée
# ---------------------------------------------------------------------------


class TestFamilleTransport:
    async def test_read_error_en_lecture_est_rejoue_puis_habille(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(N8nTransportError) as info:
            await _api_request("GET", "workflows")
        assert len(calls) == 3
        assert info.value.kind == "ReadError" and info.value.attempts == 3

    async def test_read_error_en_lecture_finit_par_reussir(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _resp(200, json_body={"data": []}))
        assert await _api_request("GET", "workflows") == {"data": []}
        assert len(calls) == 2

    async def test_pool_timeout_est_habille(self, scripted) -> None:
        scripted(httpx.PoolTimeout(""), httpx.PoolTimeout(""), httpx.PoolTimeout(""))
        with pytest.raises(N8nTransportError) as info:
            await _api_request("GET", "workflows")
        assert info.value.kind == "PoolTimeout"
        assert "connexion" in _format_error(info.value)

    async def test_corps_non_json_en_200_est_diagnostiquable(self, scripted) -> None:
        page = _resp(200, text="<html>502 Bad Gateway</html>")
        scripted(page, page, page)
        with pytest.raises(N8nAPIError) as info:
            await _api_request("GET", "workflows")
        msg = _format_error(info.value)
        assert "non-JSON" in msg and "502 Bad Gateway" in msg


# ---------------------------------------------------------------------------
# 3. Le code d'erreur de l'API est rendu
# ---------------------------------------------------------------------------


class TestCodeRendu:
    async def test_le_statut_http_traverse(self, scripted) -> None:
        scripted(_resp(404, json_body={"message": "workflow not found"}))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("GET", "workflows/7")
        assert info.value.response.status_code == 404

    def test_le_corps_n8n_est_rendu_sur_un_statut_generique(self) -> None:
        msg = _format_error(_http_error(409, '{"message":"workflow is active"}'))
        assert "409" in msg and "workflow is active" in msg


# ---------------------------------------------------------------------------
# 4. Une écriture n'est pas rejouée, et l'erreur dit son état
# ---------------------------------------------------------------------------


class TestEcritureNonRejouee:
    async def test_read_timeout_sur_put_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""))
        with pytest.raises(N8nTransportError) as info:
            await _api_request("PUT", "workflows/7", json={"name": "x"})
        assert len(calls) == 1, "un workflow réécrit deux fois écrase une version"
        assert info.value.write_state == "inconnu"

    async def test_connect_error_sur_put_est_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ConnectError(""), httpx.ConnectError(""),
                         httpx.ConnectError(""))
        with pytest.raises(N8nTransportError) as info:
            await _api_request("PUT", "workflows/7", json={"name": "x"})
        assert len(calls) == 3
        assert info.value.write_state == "non_appliquee"

    async def test_5xx_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad gateway"))
        with pytest.raises(httpx.HTTPStatusError):
            await _api_request("POST", "workflows/7/activate", json={})
        assert len(calls) == 1

    async def test_5xx_sur_get_est_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad"), _resp(200, json_body={"id": "7"}))
        assert await _api_request("GET", "workflows/7") == {"id": "7"}
        assert len(calls) == 2

    async def test_patch_et_delete_sont_des_ecritures(self, scripted) -> None:
        for method in ("PATCH", "DELETE"):
            calls = scripted(httpx.ReadTimeout(""))
            with pytest.raises(N8nTransportError) as info:
                await _api_request(method, "executions/1")
            assert len(calls) == 1, method
            assert info.value.write_state == "inconnu", method
            calls.clear()


class TestWriteState:
    async def test_inconnu_demande_de_relire_avant_de_rejouer(self, scripted) -> None:
        scripted(httpx.ReadError(""))
        with pytest.raises(N8nTransportError) as info:
            await _api_request("PUT", "workflows/7", json={})
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_non_appliquee_autorise_le_rejeu(self, scripted) -> None:
        scripted(httpx.ConnectError(""), httpx.ConnectError(""), httpx.ConnectError(""))
        with pytest.raises(N8nTransportError) as info:
            await _api_request("POST", "workflows", json={})
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "sans risque" in msg

    async def test_lecture_est_sans_objet(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(N8nTransportError) as info:
            await _api_request("GET", "workflows")
        assert info.value.write_state == "sans_objet"
        assert "criture" not in _format_error(info.value)

    async def test_4xx_sur_ecriture_dit_refusee_avant_execution(self, scripted) -> None:
        scripted(_resp(400, text="must have property nodes"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("PUT", "workflows/7", json={})
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "corriger l'appel" in msg

    async def test_5xx_sur_ecriture_laisse_l_etat_inconnu(self, scripted) -> None:
        scripted(_resp(503, text="unavailable"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("PUT", "workflows/7", json={})
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_401_sur_ecriture_garde_son_message_exact(self, scripted) -> None:
        scripted(_resp(401, text="unauthorized"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("POST", "workflows", json={})
        assert _format_error(info.value) == "Erreur 401 : clé API n8n invalide ou manquante."

    async def test_4xx_sur_lecture_ne_parle_pas_d_ecriture(self, scripted) -> None:
        scripted(_resp(400, text="bad"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("GET", "workflows")
        assert "criture" not in _format_error(info.value)


# ---------------------------------------------------------------------------
# 5. Le journal des écritures déjà appliquées
# ---------------------------------------------------------------------------


class TestJournalDesEcritures:
    async def test_une_ecriture_reussie_puis_un_echec_nomme_la_premiere(
            self, scripted) -> None:
        # Le cas réel : réécrire le workflow, puis le réactiver. Si la seconde
        # requête se perd, l'appelant doit savoir que la première a passé.
        scripted(_resp(200, json_body={"id": "7"}), httpx.ReadError(""))
        await _api_request("PUT", "workflows/7", json={"name": "x"})
        with pytest.raises(N8nTransportError) as info:
            await _api_request("POST", "workflows/7/activate", json={})
        msg = _format_error(info.value)
        assert "PUT workflows/7" in msg and "déjà appliquées" in msg

    async def test_une_lecture_ne_rentre_pas_au_journal(self, scripted) -> None:
        scripted(_resp(200, json_body={"data": []}),
                 httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        await _api_request("GET", "workflows")
        with pytest.raises(N8nTransportError) as info:
            await _api_request("GET", "workflows")
        assert "déjà appliquées" not in _format_error(info.value)

    async def test_le_journal_est_propre_a_l_appel_d_outil(self, scripted) -> None:
        scripted(_resp(200, json_body={"id": "7"}))
        await _api_request("PUT", "workflows/7", json={})
        assert _write_journal() == ["PUT workflows/7"]

        async def autre_appel() -> list[str]:
            return list(_write_journal())

        import asyncio
        assert await asyncio.create_task(autre_appel()) == []


# ---------------------------------------------------------------------------
# 6. Compatibilité des schémas de réponse
# ---------------------------------------------------------------------------


class TestCompatibilite:
    async def test_204_rend_toujours_un_dict_vide(self, scripted) -> None:
        scripted(_resp(204, text=""))
        assert await _api_request("DELETE", "executions/1") == {}

    async def test_le_json_traverse_inchange(self, scripted) -> None:
        scripted(_resp(200, json_body={"data": [{"id": "1"}], "nextCursor": None}))
        assert await _api_request("GET", "workflows") == {
            "data": [{"id": "1"}], "nextCursor": None}

    def test_json_decode_error_nue_reste_lisible(self) -> None:
        msg = _format_error(json.JSONDecodeError("Expecting value", "", 0))
        assert msg.strip() != "Erreur :" and "JSON" in msg
