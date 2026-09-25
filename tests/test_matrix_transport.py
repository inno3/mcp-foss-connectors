# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs Matrix : nommées, codées, et disant l'état de l'écriture.

Même défaut que celui remonté en production sur Dolibarr le 08/09/2026 puis sur
Kanboard le 24/09/2026, mais aggravé ici : `matrix_mcp` n'avait pas de
`_format_error` du tout. Onze outils portaient chacun leur propre
`return f"Erreur : {exc}"`, et `_request` n'attrapait que `ConnectError` /
`ReadTimeout` / `WriteTimeout`. Une exception httpx sans message — `ReadError`,
`ReadTimeout` nu, `RemoteProtocolError` — rendait donc « Erreur : » suivi de
rien, onze fois.

Le transport Matrix a deux particularités que ces tests figent, parce qu'elles
changent ce qui est rejouable :

- un `PUT` porte un identifiant de transaction (envoi, correction, suppression)
  ou pose un état à une valeur. Le homeserver déduplique sur cet identifiant, et
  il est fixé AVANT l'appel : un rejeu interne réutilise donc le même et ne
  duplique rien. Le `PUT` reste rejouable ;
- `POST /search` interroge, il n'écrit pas. `POST /createRoom` et
  `POST /rooms/{id}/invite`, eux, écrivent — et créer un salon deux fois n'est
  rattrapable qu'à la main.

Cinq garanties :

1. aucun message nu, dans les onze outils ;
2. toute la famille `httpx.TransportError` est traitée ;
3. le code d'erreur Matrix (`M_FORBIDDEN`, `M_LIMIT_EXCEEDED`…) est rendu ;
4. une écriture non déduplicable n'est JAMAIS rejouée une fois la requête
   partie, et l'erreur dit ce qu'il advient de cette écriture ;
5. les écritures déjà appliquées dans le même appel d'outil sont nommées.
"""

import os

import httpx
import pytest

os.environ.setdefault("MATRIX_HOMESERVER_URL", "https://matrix.example.test")
os.environ.setdefault("MATRIX_ACCESS_TOKEN", "syt-test")

from matrix_mcp import server  # noqa: E402
from matrix_mcp.server import (  # noqa: E402
    MatrixAPIError,
    MatrixTransportError,
    _format_error,
    _request,
    _write_journal,
)

_BASE = "https://matrix.example.test/_matrix/client/v3"
_SEND = f"{_BASE}/rooms/!abc:example.test/send/m.room.message/txn1"
_CREATE = f"{_BASE}/createRoom"
_JOINED = f"{_BASE}/joined_rooms"


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
    request = httpx.Request("GET", _JOINED)
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=request)
    return httpx.Response(status, text=text, request=request)


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

    def test_value_error_de_configuration_reste_lisible(self) -> None:
        # _headers() lève ValueError quand MATRIX_ACCESS_TOKEN manque.
        msg = _format_error(ValueError("MATRIX_ACCESS_TOKEN non configuré."))
        assert msg == "Erreur : MATRIX_ACCESS_TOKEN non configuré."

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


class TestLesOnzeOutils:
    """Aucun outil ne doit plus porter son propre « Erreur : {exc} »."""

    def test_plus_aucune_occurrence_du_message_nu(self) -> None:
        from pathlib import Path
        source = Path(server.__file__).read_text(encoding="utf-8")
        assert 'return f"Erreur : {exc}"' not in source

    def test_tous_les_outils_passent_par_le_formateur(self) -> None:
        from pathlib import Path
        source = Path(server.__file__).read_text(encoding="utf-8")
        assert source.count("return _format_error(exc)") == 11


# ---------------------------------------------------------------------------
# 2. Toute la famille TransportError est traitée
# ---------------------------------------------------------------------------


class TestFamilleTransport:
    async def test_read_error_en_lecture_est_rejoue_puis_habille(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("GET", _JOINED)
        assert len(calls) == 3
        assert info.value.kind == "ReadError" and info.value.attempts == 3

    async def test_read_error_en_lecture_finit_par_reussir(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _resp(200, json_body={"joined_rooms": []}))
        assert await _request("GET", _JOINED) == {"joined_rooms": []}
        assert len(calls) == 2

    async def test_proxy_error_est_habillee(self, scripted) -> None:
        scripted(httpx.ProxyError(""), httpx.ProxyError(""), httpx.ProxyError(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("GET", _JOINED)
        assert "proxy" in _format_error(info.value)


# ---------------------------------------------------------------------------
# 3. Le code d'erreur Matrix est rendu
# ---------------------------------------------------------------------------


class TestCodeRendu:
    async def test_le_errcode_matrix_est_rendu(self, scripted) -> None:
        scripted(_resp(403, json_body={"errcode": "M_FORBIDDEN",
                                       "error": "You are not invited"}))
        with pytest.raises(MatrixAPIError) as info:
            await _request("GET", _JOINED)
        msg = _format_error(info.value)
        assert "M_FORBIDDEN" in msg and "not invited" in msg

    async def test_le_statut_reste_dans_le_message(self, scripted) -> None:
        scripted(_resp(404, json_body={"errcode": "M_NOT_FOUND"}))
        with pytest.raises(MatrixAPIError) as info:
            await _request("GET", _JOINED)
        assert info.value.status == 404
        assert "Erreur Matrix 404" in str(info.value)

    async def test_un_corps_non_json_garde_son_extrait(self, scripted) -> None:
        scripted(_resp(502, text="<html>bad gateway</html>"),
                 _resp(502, text="<html>bad gateway</html>"),
                 _resp(502, text="<html>bad gateway</html>"))
        with pytest.raises(MatrixAPIError) as info:
            await _request("GET", _JOINED)
        assert "bad gateway" in str(info.value)

    async def test_un_corps_vide_ne_finit_pas_nu(self, scripted) -> None:
        scripted(_resp(418, text=""))
        with pytest.raises(MatrixAPIError) as info:
            await _request("GET", _JOINED)
        assert not str(info.value).rstrip().endswith(":")


# ---------------------------------------------------------------------------
# 4. Ce qui est rejouable, et ce qui ne l'est pas
# ---------------------------------------------------------------------------


class TestRejeu:
    async def test_post_createroom_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("POST", _CREATE, content=b"{}")
        assert len(calls) == 1, "un salon créé deux fois ne se rattrape qu'à la main"
        assert info.value.write_state == "inconnu"

    async def test_connect_error_sur_createroom_est_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ConnectError(""), httpx.ConnectError(""),
                         httpx.ConnectError(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("POST", _CREATE, content=b"{}")
        assert len(calls) == 3
        assert info.value.write_state == "non_appliquee"

    async def test_put_avec_identifiant_de_transaction_reste_rejouable(
            self, scripted) -> None:
        # Le homeserver déduplique sur le txnId, fixé avant l'appel : les trois
        # tentatives portent le même, donc aucun message en double.
        calls = scripted(httpx.ReadTimeout(""), _resp(200, json_body={"event_id": "$1"}))
        assert await _request("PUT", _SEND, content=b"{}") == {"event_id": "$1"}
        assert len(calls) == 2
        assert {url for _, url in calls} == {_SEND}, "le txnId doit être identique"

    async def test_post_search_est_une_lecture(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""), _resp(200, json_body={"search_categories": {}}))
        await _request("POST", f"{_BASE}/search", content=b"{}")
        assert len(calls) == 2

    async def test_post_search_ne_rentre_pas_au_journal(self, scripted) -> None:
        scripted(_resp(200, json_body={"search_categories": {}}))
        await _request("POST", f"{_BASE}/search", content=b"{}")
        assert _write_journal() == []

    async def test_5xx_sur_createroom_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad gateway"))
        with pytest.raises(MatrixAPIError):
            await _request("POST", _CREATE, content=b"{}")
        assert len(calls) == 1

    async def test_5xx_sur_lecture_est_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad"), _resp(200, json_body={"a": 1}))
        assert await _request("GET", _JOINED) == {"a": 1}
        assert len(calls) == 2


class TestWriteState:
    async def test_inconnu_demande_de_relire_avant_de_rejouer(self, scripted) -> None:
        scripted(httpx.ReadError(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("POST", _CREATE, content=b"{}")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_non_appliquee_autorise_le_rejeu(self, scripted) -> None:
        scripted(httpx.ConnectError(""), httpx.ConnectError(""), httpx.ConnectError(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("POST", _CREATE, content=b"{}")
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "sans risque" in msg

    async def test_lecture_est_sans_objet(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("GET", _JOINED)
        assert info.value.write_state == "sans_objet"
        assert "criture" not in _format_error(info.value)

    async def test_un_put_epuise_dit_que_le_txn_a_ete_conserve(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(MatrixTransportError) as info:
            await _request("PUT", _SEND, content=b"{}")
        msg = _format_error(info.value)
        assert info.value.write_state == "inconnu"
        assert "transaction" in msg, "le txnId est ce qui rend le rejeu sûr, le dire"

    async def test_4xx_sur_ecriture_dit_refusee_avant_execution(self, scripted) -> None:
        scripted(_resp(400, json_body={"errcode": "M_INVALID_PARAM",
                                       "error": "bad room alias"}))
        with pytest.raises(MatrixAPIError) as info:
            await _request("POST", _CREATE, content=b"{}")
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "corriger l'appel" in msg

    async def test_5xx_sur_ecriture_laisse_l_etat_inconnu(self, scripted) -> None:
        scripted(_resp(503, text="unavailable"))
        with pytest.raises(MatrixAPIError) as info:
            await _request("POST", _CREATE, content=b"{}")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_403_sur_ecriture_ne_discute_pas_de_l_ecriture(self, scripted) -> None:
        scripted(_resp(403, json_body={"errcode": "M_FORBIDDEN"}))
        with pytest.raises(MatrixAPIError) as info:
            await _request("POST", _CREATE, content=b"{}")
        msg = _format_error(info.value)
        assert "M_FORBIDDEN" in msg
        assert "non appliquée" not in msg

    async def test_4xx_sur_lecture_ne_parle_pas_d_ecriture(self, scripted) -> None:
        scripted(_resp(400, json_body={"errcode": "M_INVALID_PARAM"}))
        with pytest.raises(MatrixAPIError) as info:
            await _request("GET", _JOINED)
        assert "criture" not in _format_error(info.value)


# ---------------------------------------------------------------------------
# 5. Le journal des écritures déjà appliquées
# ---------------------------------------------------------------------------


class TestJournalDesEcritures:
    async def test_un_envoi_reussi_puis_un_echec_nomme_le_premier(self, scripted) -> None:
        # Le cas réel : téléverser puis envoyer, ou envoyer puis réagir.
        scripted(_resp(200, json_body={"event_id": "$1"}), httpx.ReadError(""))
        await _request("PUT", _SEND, content=b"{}")
        with pytest.raises(MatrixTransportError) as info:
            await _request("POST", _CREATE, content=b"{}")
        msg = _format_error(info.value)
        assert "déjà appliquées" in msg
        assert "rooms/!abc:example.test/send/m.room.message" in msg

    async def test_le_journal_nomme_la_route_sans_l_hote(self, scripted) -> None:
        scripted(_resp(200, json_body={"room_id": "!new"}))
        await _request("POST", _CREATE, content=b"{}")
        assert _write_journal() == ["POST createRoom"]

    async def test_une_lecture_ne_rentre_pas_au_journal(self, scripted) -> None:
        scripted(_resp(200, json_body={}),
                 httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        await _request("GET", _JOINED)
        with pytest.raises(MatrixTransportError) as info:
            await _request("GET", _JOINED)
        assert "déjà appliquées" not in _format_error(info.value)

    async def test_le_journal_est_propre_a_l_appel_d_outil(self, scripted) -> None:
        scripted(_resp(200, json_body={"room_id": "!new"}))
        await _request("POST", _CREATE, content=b"{}")
        assert _write_journal() == ["POST createRoom"]

        async def autre_appel() -> list[str]:
            return list(_write_journal())

        import asyncio
        assert await asyncio.create_task(autre_appel()) == []


# ---------------------------------------------------------------------------
# 6. Compatibilité
# ---------------------------------------------------------------------------


class TestCompatibilite:
    async def test_204_rend_toujours_un_dict_vide(self, scripted) -> None:
        scripted(_resp(204, text=""))
        assert await _request("PUT", _SEND, content=b"{}") == {}

    async def test_le_json_traverse_inchange(self, scripted) -> None:
        scripted(_resp(200, json_body={"chunk": [{"event_id": "$1"}], "end": "t1"}))
        assert await _request("GET", _JOINED) == {
            "chunk": [{"event_id": "$1"}], "end": "t1"}

    async def test_l_erreur_http_reste_une_runtimeerror(self, scripted) -> None:
        # Les outils qui n'ont pas de gestionnaire propre comptent dessus, et
        # tests/test_matrix.py simule des pannes avec de simples RuntimeError.
        scripted(_resp(404, json_body={"errcode": "M_NOT_FOUND"}))
        with pytest.raises(RuntimeError):
            await _request("GET", _JOINED)

    async def test_le_prefixe_du_message_http_est_conserve(self, scripted) -> None:
        scripted(_resp(404, json_body={"errcode": "M_NOT_FOUND"}))
        with pytest.raises(MatrixAPIError) as info:
            await _request("GET", _JOINED)
        assert str(info.value).startswith("Erreur Matrix 404 — GET ")
