# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs WordPress : nommées, codées, et disant l'état de l'écriture.

Même défaut que celui remonté en production sur Dolibarr le 08/09/2026 puis sur
Kanboard le 24/09/2026 : `_format_error` finissait par `return f"Erreur : {exc}"`
et `_request` n'attrapait que `ConnectError` et `ReadTimeout`. Une exception
httpx sans message — `ReadError`, `ReadTimeout` nu, `RemoteProtocolError` —
rendait donc « Erreur : » suivi de rien.

Le risque propre à WordPress : publier deux fois. Un `POST /wp/v2/posts` rejoué
après une réponse perdue crée un second article, visible sur le site public, que
seule une relecture permet de découvrir. Et `wordpress_upload_media` enchaîne
deux écritures — déposer le fichier, puis lui poser son titre et son texte
alternatif : quand la seconde échoue, l'appelant doit savoir que le média est
déjà en médiathèque.

Ces tests figent cinq garanties :

1. aucun message nu ;
2. toute la famille `httpx.TransportError` est traitée ;
3. le code d'erreur de l'API est rendu, y compris le `code` REST de WordPress ;
4. une écriture n'est JAMAIS rejouée automatiquement une fois la requête
   partie, et l'erreur dit ce qu'il advient de cette écriture ;
5. les écritures déjà appliquées dans le même appel d'outil sont nommées.
"""

import json
import os

import httpx
import pytest

os.environ.setdefault("WP_PROD_URL", "https://example.test")
os.environ.setdefault("WP_PROD_USER", "tester")
os.environ.setdefault("WP_PROD_APP_PASSWORD", "xxxx xxxx xxxx xxxx xxxx xxxx")

import wordpress_mcp.server as wp  # noqa: E402
from wordpress_mcp.server import (  # noqa: E402
    WordPressTransportError,
    _format_error,
    _request,
    _write_journal,
)

_POSTS = "https://example.test/wp-json/wp/v2/posts"


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
    request = httpx.Request("GET", _POSTS)
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=request)
    return httpx.Response(status, text=text, request=request)


def _http_error(status: int, text: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _POSTS)
    response = httpx.Response(status, text=text, request=request)
    return httpx.HTTPStatusError("erreur", request=request, response=response)


def _http_error_json(status: int, body: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _POSTS)
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError("erreur", request=request, response=response)


@pytest.fixture
def scripted(monkeypatch):
    """Installe un client scénarisé et supprime les délais de backoff."""
    calls: list = []

    def install(*script):
        steps = list(script)
        monkeypatch.setattr(wp.httpx, "AsyncClient",
                            lambda **kw: _ScriptedClient(steps, calls))
        return calls

    monkeypatch.setattr(wp, "_RETRY_DELAY", 0)
    return install


@pytest.fixture(autouse=True)
def journal_propre():
    """Filet pour les tests synchrones, qui partagent la tâche nulle."""
    yield
    wp._applied_writes.set(None)


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
    """Compatibilité : la forme sur laquelle les outils s'appuient."""

    def test_le_message_json_de_wordpress_est_extrait(self) -> None:
        msg = _format_error(_http_error_json(400, {
            "code": "rest_invalid_param", "message": "Paramètre invalide : status"}))
        assert msg.startswith("Erreur HTTP 400 : ")
        assert "Paramètre invalide : status" in msg

    def test_corps_non_json_rend_le_texte(self) -> None:
        msg = _format_error(_http_error(502, "<html>bad gateway</html>"))
        assert "502" in msg and "bad gateway" in msg

    def test_corps_vide_ne_finit_pas_nu(self) -> None:
        assert _format_error(_http_error(418, "")).strip() != "Erreur HTTP 418 :"


# ---------------------------------------------------------------------------
# 2. Toute la famille TransportError est traitée
# ---------------------------------------------------------------------------


class TestFamilleTransport:
    async def test_read_error_en_lecture_est_rejoue_puis_habille(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "GET", _POSTS)
        assert len(calls) == 3
        assert info.value.kind == "ReadError" and info.value.attempts == 3

    async def test_write_timeout_en_lecture_est_rejoue(self, scripted) -> None:
        # WriteTimeout n'était pas attrapé du tout : il traversait sans rejeu.
        calls = scripted(httpx.WriteTimeout(""), _resp(200, json_body=[]))
        assert await _request("prod", "GET", _POSTS) == []
        assert len(calls) == 2

    async def test_remote_protocol_error_est_habillee(self, scripted) -> None:
        scripted(httpx.RemoteProtocolError(""), httpx.RemoteProtocolError(""),
                 httpx.RemoteProtocolError(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "GET", _POSTS)
        assert "mal formée" in _format_error(info.value)

    async def test_corps_non_json_en_200_reste_du_texte(self, scripted) -> None:
        # Compatibilité : _request rendait déjà resp.text sur un corps non-JSON,
        # et wordpress_check_content en dépend. Ce comportement ne change pas.
        scripted(_resp(200, text="pas du json"))
        assert await _request("prod", "GET", _POSTS) == "pas du json"


# ---------------------------------------------------------------------------
# 3. Le code d'erreur de l'API est rendu
# ---------------------------------------------------------------------------


class TestCodeRendu:
    async def test_le_statut_http_traverse(self, scripted) -> None:
        scripted(_resp(404, json_body={"code": "rest_post_invalid_id"}))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("prod", "GET", _POSTS + "/9999")
        assert info.value.response.status_code == 404

    def test_le_code_rest_est_rendu_a_cote_du_message(self) -> None:
        msg = _format_error(_http_error_json(403, {
            "code": "rest_cannot_edit", "message": "Désolé, vous n'avez pas le droit."}))
        assert "rest_cannot_edit" in msg
        assert "vous n'avez pas le droit" in msg


# ---------------------------------------------------------------------------
# 4. Une écriture n'est pas rejouée, et l'erreur dit son état
# ---------------------------------------------------------------------------


class TestEcritureNonRejouee:
    async def test_read_timeout_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "POST", _POSTS, content=b"{}")
        assert len(calls) == 1, "un article publié deux fois est visible du public"
        assert info.value.write_state == "inconnu"

    async def test_connect_error_sur_post_est_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ConnectError(""), httpx.ConnectError(""),
                         httpx.ConnectError(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "POST", _POSTS, content=b"{}")
        assert len(calls) == 3
        assert info.value.write_state == "non_appliquee"

    async def test_5xx_sur_post_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad gateway"))
        with pytest.raises(httpx.HTTPStatusError):
            await _request("prod", "POST", _POSTS, content=b"{}")
        assert len(calls) == 1

    async def test_5xx_sur_get_est_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad"), _resp(200, json_body=[{"id": 1}]))
        assert await _request("prod", "GET", _POSTS) == [{"id": 1}]
        assert len(calls) == 2

    async def test_delete_est_une_ecriture(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "DELETE", _POSTS + "/7")
        assert len(calls) == 1
        assert info.value.write_state == "inconnu"


class TestWriteState:
    async def test_inconnu_demande_de_relire_avant_de_rejouer(self, scripted) -> None:
        scripted(httpx.ReadError(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "POST", _POSTS, content=b"{}")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_non_appliquee_autorise_le_rejeu(self, scripted) -> None:
        scripted(httpx.ConnectError(""), httpx.ConnectError(""), httpx.ConnectError(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "POST", _POSTS, content=b"{}")
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "sans risque" in msg

    async def test_lecture_est_sans_objet(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "GET", _POSTS)
        assert info.value.write_state == "sans_objet"
        assert "criture" not in _format_error(info.value)

    async def test_4xx_sur_ecriture_dit_refusee_avant_execution(self, scripted) -> None:
        scripted(_resp(400, json_body={"code": "rest_invalid_param",
                                       "message": "Paramètre invalide"}))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("prod", "POST", _POSTS, content=b"{}")
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "corriger l'appel" in msg

    async def test_5xx_sur_ecriture_laisse_l_etat_inconnu(self, scripted) -> None:
        scripted(_resp(503, text="unavailable"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("prod", "POST", _POSTS, content=b"{}")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_401_sur_ecriture_ne_discute_pas_de_l_ecriture(self, scripted) -> None:
        scripted(_resp(401, json_body={"code": "rest_not_logged_in",
                                       "message": "Non connecté."}))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("prod", "POST", _POSTS, content=b"{}")
        msg = _format_error(info.value)
        assert "Non connecté." in msg
        assert "non appliquée" not in msg

    async def test_4xx_sur_lecture_ne_parle_pas_d_ecriture(self, scripted) -> None:
        scripted(_resp(400, text="bad"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("prod", "GET", _POSTS)
        assert "criture" not in _format_error(info.value)


# ---------------------------------------------------------------------------
# 5. Le journal des écritures déjà appliquées
# ---------------------------------------------------------------------------


class TestJournalDesEcritures:
    async def test_une_ecriture_reussie_puis_un_echec_nomme_la_premiere(
            self, scripted) -> None:
        # Le cas réel : déposer le média, puis lui poser titre et alt.
        media = "https://example.test/wp-json/wp/v2/media"
        scripted(_resp(201, json_body={"id": 12}), httpx.ReadError(""))
        await _request("prod", "POST", media, content=b"...")
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "PUT", media + "/12", content=b"{}")
        msg = _format_error(info.value)
        assert "POST wp/v2/media" in msg and "déjà appliquées" in msg

    async def test_le_journal_nomme_la_route_sans_l_hote(self, scripted) -> None:
        scripted(_resp(201, json_body={"id": 1}))
        await _request("prod", "POST", _POSTS, content=b"{}")
        assert _write_journal() == ["POST wp/v2/posts"]
        assert "example.test" not in _write_journal()[0]

    async def test_une_lecture_ne_rentre_pas_au_journal(self, scripted) -> None:
        scripted(_resp(200, json_body=[]),
                 httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        await _request("prod", "GET", _POSTS)
        with pytest.raises(WordPressTransportError) as info:
            await _request("prod", "GET", _POSTS)
        assert "déjà appliquées" not in _format_error(info.value)

    async def test_le_journal_est_propre_a_l_appel_d_outil(self, scripted) -> None:
        scripted(_resp(201, json_body={"id": 1}))
        await _request("prod", "POST", _POSTS, content=b"{}")
        assert _write_journal() == ["POST wp/v2/posts"]

        async def autre_appel() -> list[str]:
            return list(_write_journal())

        import asyncio
        assert await asyncio.create_task(autre_appel()) == []


# ---------------------------------------------------------------------------
# 6. Compatibilité des schémas de réponse
# ---------------------------------------------------------------------------


class TestCompatibilite:
    async def test_corps_vide_rend_un_dict_vide(self, scripted) -> None:
        scripted(_resp(204, text=""))
        assert await _request("prod", "DELETE", _POSTS + "/7") == {}

    async def test_le_json_traverse_inchange(self, scripted) -> None:
        scripted(_resp(200, json_body=[{"id": 1, "title": {"rendered": "a"}}]))
        assert await _request("prod", "GET", _POSTS) == [
            {"id": 1, "title": {"rendered": "a"}}]

    def test_json_decode_error_nue_reste_lisible(self) -> None:
        msg = _format_error(json.JSONDecodeError("Expecting value", "", 0))
        assert msg.strip() != "Erreur :" and "JSON" in msg

    def test_value_error_de_configuration_reste_lisible(self) -> None:
        # _get_auth_header lève ValueError : le message doit traverser tel quel.
        msg = _format_error(ValueError("Serveur inconnu : 'staging'."))
        assert msg == "Erreur : Serveur inconnu : 'staging'."
