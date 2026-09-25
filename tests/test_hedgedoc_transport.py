# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs HedgeDoc : nommées, codées, et disant l'état de l'écriture.

Même défaut que celui remonté en production sur Dolibarr le 08/09/2026 puis sur
Kanboard le 24/09/2026 : `_format_error` finissait par `return f"Erreur : {exc}"`
et `_hd_request` n'attrapait que `ConnectError` / `ReadTimeout` / `WriteTimeout`.
Une exception httpx sans message — `ReadError`, `ReadTimeout` nu,
`RemoteProtocolError` — rendait donc « Erreur : » suivi de rien.

Le risque propre à HedgeDoc : `POST /new` crée une note à chaque appel. Rejouer
cette requête après une réponse perdue laisse deux notes au contenu identique
mais aux identifiants différents, et rien ne le signale — l'appelant repart avec
l'URL de la seconde en croyant qu'il n'y en a qu'une.

Ces tests figent cinq garanties :

1. aucun message nu ;
2. toute la famille `httpx.TransportError` est traitée ;
3. le statut HTTP et le corps de la réponse sont rendus ;
4. une écriture n'est JAMAIS rejouée automatiquement une fois la requête
   partie, et l'erreur dit ce qu'il advient de cette écriture ;
5. les écritures déjà appliquées dans le même appel d'outil sont nommées.

Ils figent aussi ce qui ne doit PAS changer : `_hd_request` rend toujours la
réponse brute, un 4xx n'est pas levé mais rendu, et un 401 ou 403 déclenche
toujours un re-login suivi d'une seule reprise.
"""

import os

import httpx
import pytest

# Mêmes valeurs que tests/test_hedgedoc.py : le module serveur lit ces
# variables une seule fois, à l'import. Si ce fichier-ci est importé le premier
# — c'est le cas dès qu'on le passe seul à pytest — un jeu différent laisserait
# le connecteur en mode anonyme pour toute la session de test.
os.environ.setdefault("HEDGEDOC_URL", "https://pad.example.test")
os.environ.setdefault("HEDGEDOC_API_TOKEN", "hd-token-xxxx")
os.environ.setdefault("HEDGEDOC_USER", "tester@example.test")
os.environ.setdefault("HEDGEDOC_PASSWORD", "test-password")

from hedgedoc_mcp import server  # noqa: E402
from hedgedoc_mcp.server import (  # noqa: E402
    HedgeDocTransportError,
    _format_error,
    _hd_request,
    _write_journal,
)

_URL = "https://pad.example.test/new"


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


def _resp(status: int, *, text: str = "", headers: dict | None = None) -> httpx.Response:
    request = httpx.Request("GET", _URL)
    return httpx.Response(status, text=text, headers=headers or {}, request=request)


def _http_error(status: int, text: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _URL)
    response = httpx.Response(status, text=text, request=request)
    return httpx.HTTPStatusError("erreur", request=request, response=response)


@pytest.fixture
def scripted(monkeypatch):
    """Installe un client scénarisé, neutralise le login et le backoff."""
    calls: list = []

    async def _no_session() -> None:
        return None

    def install(*script):
        steps = list(script)
        monkeypatch.setattr(server.httpx, "AsyncClient",
                            lambda **kw: _ScriptedClient(steps, calls))
        return calls

    monkeypatch.setattr(server, "_RETRY_DELAY", 0)
    monkeypatch.setattr(server, "_ensure_session", _no_session)
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
            "Erreur 401 : session HedgeDoc expirée")

    def test_403_garde_son_diagnostic_complet(self) -> None:
        msg = _format_error(_http_error(403))
        assert "allowFreeURL" in msg and "hedgedoc_me()" in msg

    def test_404(self) -> None:
        assert _format_error(_http_error(404)).startswith(
            "Erreur 404 : note non trouvée")

    def test_500_rend_le_corps(self) -> None:
        msg = _format_error(_http_error(500, "Internal Server Error"))
        assert "500" in msg and "Internal Server Error" in msg

    def test_statut_inconnu_sans_corps_ne_finit_pas_nu(self) -> None:
        assert _format_error(_http_error(418, "")).strip() != "Erreur HTTP 418 :"


# ---------------------------------------------------------------------------
# 2. Toute la famille TransportError est traitée
# ---------------------------------------------------------------------------


class TestFamilleTransport:
    async def test_read_error_en_lecture_est_rejoue_puis_habille(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("GET", "/history", content_type="")
        assert len(calls) == 3
        assert info.value.kind == "ReadError" and info.value.attempts == 3

    async def test_read_error_en_lecture_finit_par_reussir(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _resp(200, text="{}"))
        resp = await _hd_request("GET", "/history", content_type="")
        assert resp.status_code == 200
        assert len(calls) == 2

    async def test_remote_protocol_error_est_habillee(self, scripted) -> None:
        scripted(httpx.RemoteProtocolError(""), httpx.RemoteProtocolError(""),
                 httpx.RemoteProtocolError(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("GET", "/me", content_type="")
        assert "mal formée" in _format_error(info.value)


# ---------------------------------------------------------------------------
# 3. Le statut et le corps sont rendus
# ---------------------------------------------------------------------------


class TestCodeRendu:
    async def test_5xx_epuise_leve_une_httpstatuserror(self, scripted) -> None:
        scripted(_resp(503, text="unavailable"), _resp(503, text="unavailable"),
                 _resp(503, text="unavailable"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _hd_request("GET", "/history", content_type="")
        assert info.value.response.status_code == 503
        assert "unavailable" in _format_error(info.value)


# ---------------------------------------------------------------------------
# 4. Une écriture n'est pas rejouée, et l'erreur dit son état
# ---------------------------------------------------------------------------


class TestEcritureNonRejouee:
    async def test_read_timeout_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("POST", "/new", content=b"# titre")
        assert len(calls) == 1, "POST /new rejoué laisse deux notes identiques"
        assert info.value.write_state == "inconnu"

    async def test_connect_error_sur_post_est_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ConnectError(""), httpx.ConnectError(""),
                         httpx.ConnectError(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("POST", "/new", content=b"# titre")
        assert len(calls) == 3
        assert info.value.write_state == "non_appliquee"

    async def test_5xx_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad gateway"))
        with pytest.raises(httpx.HTTPStatusError):
            await _hd_request("POST", "/new", content=b"# titre")
        assert len(calls) == 1

    async def test_5xx_sur_get_est_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad"), _resp(200, text="ok"))
        resp = await _hd_request("GET", "/history", content_type="")
        assert resp.status_code == 200
        assert len(calls) == 2

    async def test_delete_est_une_ecriture(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("DELETE", "/history/abc", content_type="")
        assert len(calls) == 1
        assert info.value.write_state == "inconnu"


class TestWriteState:
    async def test_inconnu_demande_de_relire_avant_de_rejouer(self, scripted) -> None:
        scripted(httpx.ReadError(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("POST", "/new", content=b"x")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_non_appliquee_autorise_le_rejeu(self, scripted) -> None:
        scripted(httpx.ConnectError(""), httpx.ConnectError(""), httpx.ConnectError(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("POST", "/new", content=b"x")
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "sans risque" in msg

    async def test_lecture_est_sans_objet(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("GET", "/history", content_type="")
        assert info.value.write_state == "sans_objet"
        assert "criture" not in _format_error(info.value)

    async def test_5xx_sur_ecriture_laisse_l_etat_inconnu(self, scripted) -> None:
        scripted(_resp(503, text="unavailable"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _hd_request("POST", "/new", content=b"x")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_5xx_sur_lecture_ne_parle_pas_d_ecriture(self, scripted) -> None:
        scripted(_resp(503, text="x"), _resp(503, text="x"), _resp(503, text="x"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _hd_request("GET", "/history", content_type="")
        assert "criture" not in _format_error(info.value)


# ---------------------------------------------------------------------------
# 5. Le journal des écritures déjà appliquées
# ---------------------------------------------------------------------------


class TestJournalDesEcritures:
    async def test_une_creation_reussie_puis_un_echec_nomme_la_premiere(
            self, scripted) -> None:
        scripted(_resp(302, headers={"location": "/abc123"}), httpx.ReadError(""))
        await _hd_request("POST", "/new", content=b"# titre")
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("DELETE", "/history/abc123", content_type="")
        msg = _format_error(info.value)
        assert "POST /new" in msg and "déjà appliquées" in msg

    async def test_une_ecriture_refusee_ne_rentre_pas_au_journal(self, scripted) -> None:
        # 403 sur POST /new/<alias> : l'instance refuse les URL libres, aucune
        # note n'a été créée. L'annoncer comme appliquée serait un mensonge.
        scripted(_resp(403, text="Forbidden"), httpx.ReadError(""))
        resp = await _hd_request("POST", "/new/mon-alias", content=b"x",
                                 retry_on_401=False)
        assert resp.status_code == 403
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("POST", "/new", content=b"x")
        assert "déjà appliquées" not in _format_error(info.value)

    async def test_une_lecture_ne_rentre_pas_au_journal(self, scripted) -> None:
        scripted(_resp(200, text="[]"),
                 httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        await _hd_request("GET", "/history", content_type="")
        with pytest.raises(HedgeDocTransportError) as info:
            await _hd_request("GET", "/history", content_type="")
        assert "déjà appliquées" not in _format_error(info.value)

    async def test_le_journal_est_propre_a_l_appel_d_outil(self, scripted) -> None:
        scripted(_resp(302, headers={"location": "/abc"}))
        await _hd_request("POST", "/new", content=b"x")
        assert _write_journal() == ["POST /new"]

        async def autre_appel() -> list[str]:
            return list(_write_journal())

        import asyncio
        assert await asyncio.create_task(autre_appel()) == []


# ---------------------------------------------------------------------------
# 6. Ce qui ne doit pas changer
# ---------------------------------------------------------------------------


class TestCompatibilite:
    async def test_la_reponse_brute_est_rendue(self, scripted) -> None:
        scripted(_resp(200, text="# markdown"))
        resp = await _hd_request("GET", "/abc/download", content_type="")
        assert isinstance(resp, httpx.Response)
        assert resp.text == "# markdown"

    async def test_un_4xx_est_rendu_et_non_leve(self, scripted) -> None:
        # Les outils lisent eux-mêmes le statut : hedgedoc_create_note distingue
        # un 403 d'alias refusé, hedgedoc_update_note un 404 d'un 405.
        scripted(_resp(404, text="Cannot PUT /abc"))
        resp = await _hd_request("PUT", "/abc", content=b"x", retry_on_401=False)
        assert resp.status_code == 404

    async def test_401_declenche_un_relogin_et_une_seule_reprise(
            self, scripted, monkeypatch) -> None:
        tentatives: list[int] = []

        async def _login_factice() -> bool:
            tentatives.append(1)
            return True

        monkeypatch.setattr(server, "HEDGEDOC_USER", "alice")
        monkeypatch.setattr(server, "_login", _login_factice)
        calls = scripted(_resp(401, text="no session"), _resp(200, text="ok"))
        resp = await _hd_request("GET", "/history", content_type="")
        assert resp.status_code == 200
        assert tentatives == [1], "un seul re-login, pas une boucle"
        assert len(calls) == 2
