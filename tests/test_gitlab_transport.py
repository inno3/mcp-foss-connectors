# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs GitLab : nommées, codées, et disant l'état de l'écriture.

Même défaut que celui remonté en production sur Dolibarr le 08/09/2026 puis sur
Kanboard le 24/09/2026 : `_format_error` finissait par `return f"Erreur : {exc}"`
et `_api_request` n'attrapait que `ConnectError` / `ReadTimeout` / `WriteTimeout`.
Une exception httpx sans message — `ReadError`, `ReadTimeout` nu,
`RemoteProtocolError` — rendait donc « Erreur : » suivi de rien, et l'appelant
ne pouvait distinguer un jeton refusé, une ressource absente et une défaillance
passagère.

Ces tests figent cinq garanties :

1. aucun message nu — le type d'exception et un libellé de famille remplacent
   le vide ;
2. toute la famille `httpx.TransportError` est traitée, pas trois classes ;
3. le code d'erreur de l'API (le statut HTTP, et le corps GitLab quand il en
   porte un) est rendu ;
4. une écriture n'est JAMAIS rejouée automatiquement une fois la requête
   partie, et l'erreur dit ce qu'il advient de cette écriture : « non
   appliquée » quand la requête n'a pas atteint GitLab, « inconnu » quand la
   réponse s'est perdue ;
5. les écritures déjà appliquées plus tôt dans le même appel d'outil sont
   nommées, pour que l'appelant sache quoi relire avant de rejouer.
"""

import json
import os

import httpx
import pytest

os.environ.setdefault("GITLAB_URL", "https://gitlab.example.test")
os.environ.setdefault("GITLAB_TOKEN", "glpat-test")

from gitlab_mcp import server  # noqa: E402
from gitlab_mcp.server import (  # noqa: E402
    GitLabAPIError,
    GitLabTransportError,
    _api_request,
    _format_error,
    _write_journal,
)

_URL = "https://gitlab.example.test/api/v4/projects/42"


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


def _resp(status: int, *, json_body=None, text: str = "", method: str = "GET") -> httpx.Response:
    request = httpx.Request(method, _URL)
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
    """Le journal d'écritures est porté par la tâche asyncio courante.

    pytest-asyncio crée une tâche par test, donc l'isolation est acquise ; ce
    filet ne sert qu'aux tests synchrones, qui partagent la tâche nulle.
    """
    yield
    server._applied_writes.set(None)


# ---------------------------------------------------------------------------
# 1. Le message n'est jamais nu
# ---------------------------------------------------------------------------


class TestMessageJamaisNu:
    def test_read_error_vide_est_nomme(self) -> None:
        msg = _format_error(httpx.ReadError(""))
        assert msg.strip() != "Erreur :"
        assert "ReadError" in msg
        assert "connexion coupée" in msg

    def test_read_timeout_vide_est_nomme(self) -> None:
        msg = _format_error(httpx.ReadTimeout(""))
        assert "ReadTimeout" in msg and "délai" in msg

    def test_remote_protocol_error_vide_est_nomme(self) -> None:
        msg = _format_error(httpx.RemoteProtocolError(""))
        assert "RemoteProtocolError" in msg and "mal formée" in msg

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
            "Erreur 401 : token GitLab invalide")

    def test_403(self) -> None:
        assert _format_error(_http_error(403)).startswith(
            "Erreur 403 : permissions insuffisantes")

    def test_404(self) -> None:
        assert _format_error(_http_error(404)).startswith(
            "Erreur 404 : ressource non trouvée")

    def test_422_rend_le_corps(self) -> None:
        msg = _format_error(_http_error(422, "branch already exists"))
        assert "422" in msg and "branch already exists" in msg

    def test_statut_inconnu_sans_corps_ne_finit_pas_nu(self) -> None:
        msg = _format_error(_http_error(418, ""))
        assert msg.rstrip().endswith(("sans message.", "."))
        assert msg.strip() != "Erreur HTTP 418 :"


# ---------------------------------------------------------------------------
# 2. Toute la famille TransportError est traitée
# ---------------------------------------------------------------------------


class TestFamilleTransport:
    async def test_read_error_en_lecture_est_rejoue_puis_habille(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("GET", "projects/42")
        assert len(calls) == 3  # rejeu jusqu'à épuisement
        assert info.value.kind == "ReadError"
        assert info.value.attempts == 3

    async def test_read_error_en_lecture_finit_par_reussir(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _resp(200, json_body={"id": 42}))
        assert await _api_request("GET", "projects/42") == {"id": 42}
        assert len(calls) == 2

    async def test_proxy_error_est_habillee(self, scripted) -> None:
        scripted(httpx.ProxyError(""), httpx.ProxyError(""), httpx.ProxyError(""))
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("GET", "projects/42")
        assert info.value.kind == "ProxyError"
        assert "proxy" in _format_error(info.value)

    async def test_corps_non_json_en_200_est_diagnostiquable(self, scripted) -> None:
        page = _resp(200, text="<html>502 Bad Gateway</html>")
        scripted(page, page, page)
        with pytest.raises(GitLabAPIError) as info:
            await _api_request("GET", "projects/42")
        msg = _format_error(info.value)
        assert "non-JSON" in msg and "502 Bad Gateway" in msg


# ---------------------------------------------------------------------------
# 3. Le code d'erreur de l'API est rendu
# ---------------------------------------------------------------------------


class TestCodeRendu:
    async def test_le_statut_http_traverse(self, scripted) -> None:
        scripted(_resp(404, json_body={"message": "404 Project Not Found"}))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("GET", "projects/42")
        assert info.value.response.status_code == 404

    def test_le_corps_gitlab_est_rendu_sur_un_statut_generique(self) -> None:
        msg = _format_error(_http_error(409, '{"message":"already exists"}'))
        assert "409" in msg and "already exists" in msg


# ---------------------------------------------------------------------------
# 4. Une écriture n'est pas rejouée, et l'erreur dit son état
# ---------------------------------------------------------------------------


class TestEcritureNonRejouee:
    async def test_read_timeout_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""))
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("POST", "projects/42/issues", json={"title": "x"})
        assert len(calls) == 1, "une écriture partie ne doit jamais être rejouée"
        assert info.value.write_state == "inconnu"

    async def test_connect_error_sur_post_est_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ConnectError(""), httpx.ConnectError(""),
                         httpx.ConnectError(""))
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("POST", "projects/42/issues", json={"title": "x"})
        assert len(calls) == 3, "une connexion jamais établie n'a rien écrit"
        assert info.value.write_state == "non_appliquee"

    async def test_5xx_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad gateway"))
        with pytest.raises(httpx.HTTPStatusError):
            await _api_request("POST", "projects/42/issues", json={"title": "x"})
        assert len(calls) == 1

    async def test_5xx_sur_get_est_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad"), _resp(200, json_body={"id": 42}))
        assert await _api_request("GET", "projects/42") == {"id": 42}
        assert len(calls) == 2

    async def test_corps_non_json_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(_resp(200, text="<html>proxy</html>"))
        with pytest.raises(GitLabAPIError):
            await _api_request("POST", "projects/42/issues", json={"title": "x"})
        assert len(calls) == 1


class TestWriteState:
    async def test_inconnu_demande_de_relire_avant_de_rejouer(self, scripted) -> None:
        scripted(httpx.ReadError(""))
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("PUT", "projects/42/issues/1", json={})
        msg = _format_error(info.value)
        assert "inconnu" in msg
        assert "relire" in msg

    async def test_non_appliquee_autorise_le_rejeu(self, scripted) -> None:
        scripted(httpx.ConnectError(""), httpx.ConnectError(""), httpx.ConnectError(""))
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("POST", "projects/42/issues", json={})
        msg = _format_error(info.value)
        assert "non appliquée" in msg
        assert "sans risque" in msg

    async def test_lecture_est_sans_objet(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("GET", "projects/42")
        assert info.value.write_state == "sans_objet"
        msg = _format_error(info.value)
        assert "Écriture" not in msg and "écriture" not in msg

    async def test_4xx_sur_ecriture_dit_refusee_avant_execution(self, scripted) -> None:
        scripted(_resp(400, text="invalid title"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("POST", "projects/42/issues", json={})
        msg = _format_error(info.value)
        assert "non appliquée" in msg
        assert "corriger l'appel" in msg

    async def test_5xx_sur_ecriture_laisse_l_etat_inconnu(self, scripted) -> None:
        scripted(_resp(503, text="unavailable"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("POST", "projects/42/issues", json={})
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_401_sur_ecriture_garde_son_message_exact(self, scripted) -> None:
        scripted(_resp(401, text="unauthorized"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("POST", "projects/42/issues", json={})
        assert _format_error(info.value) == (
            "Erreur 401 : token GitLab invalide ou manquant (GITLAB_TOKEN).")

    async def test_4xx_sur_lecture_ne_parle_pas_d_ecriture(self, scripted) -> None:
        scripted(_resp(400, text="bad"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _api_request("GET", "projects/42")
        assert "écriture" not in _format_error(info.value)


# ---------------------------------------------------------------------------
# 5. Le journal des écritures déjà appliquées
# ---------------------------------------------------------------------------


class TestJournalDesEcritures:
    async def test_une_ecriture_reussie_puis_un_echec_nomme_la_premiere(
            self, scripted) -> None:
        scripted(
            _resp(201, json_body={"iid": 7}),          # POST issue : appliqué
            httpx.ReadError(""),                        # PUT labels : perdu
        )
        await _api_request("POST", "projects/42/issues", json={"title": "x"})
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("PUT", "projects/42/issues/7", json={})
        msg = _format_error(info.value)
        assert "POST projects/42/issues" in msg
        assert "déjà appliquées" in msg

    async def test_une_lecture_ne_rentre_pas_au_journal(self, scripted) -> None:
        scripted(_resp(200, json_body={"id": 42}),
                 httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        await _api_request("GET", "projects/42")
        with pytest.raises(GitLabTransportError) as info:
            await _api_request("GET", "projects/42")
        assert "déjà appliquées" not in _format_error(info.value)

    async def test_le_journal_est_propre_a_l_appel_d_outil(self, scripted) -> None:
        scripted(_resp(201, json_body={"iid": 7}))
        await _api_request("POST", "projects/42/issues", json={"title": "x"})
        assert _write_journal() == ["POST projects/42/issues"]

        # Une autre tâche asyncio, c'est un autre appel d'outil : elle ne doit
        # pas hériter des écritures de celle-ci.
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
        assert await _api_request("DELETE", "projects/42/issues/7") == {}

    async def test_le_json_traverse_inchange(self, scripted) -> None:
        scripted(_resp(200, json_body=[{"id": 1}, {"id": 2}]))
        assert await _api_request("GET", "projects") == [{"id": 1}, {"id": 2}]

    def test_json_decode_error_nue_reste_lisible(self) -> None:
        exc = json.JSONDecodeError("Expecting value", "", 0)
        msg = _format_error(exc)
        assert msg.strip() != "Erreur :"
        assert "JSON" in msg
