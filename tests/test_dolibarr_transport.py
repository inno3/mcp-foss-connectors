# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs réseau : nommées, et rejouées quand c'est sans risque.

Constat du 08/09/2026 : `dolibarr_get_meetingnote` répondait par intermittence
« Erreur : » sans code ni message, sur trois identifiants différents, lors
d'appels rapprochés depuis des sessions parallèles ; le même appel rejoué à
l'identique réussissait. Un `httpx.ReadError` (connexion coupée par le serveur)
porte un message vide et n'était pas rejoué : il traversait `_format_error`
tel quel. Ces tests figent les deux corrections : le message nomme le type, la
route et le rejeu ; les lectures sont rejouées, les écritures jamais une fois
parties.
"""

import os

import httpx
import pytest

os.environ.setdefault("DOLIBARR_API_KEY", "test-key-123")
os.environ.setdefault("DOLIBARR_URL", "https://dolibarr.test.local")

from dolibarr_mcp import server  # noqa: E402
from dolibarr_mcp.server import (  # noqa: E402
    DolibarrTransportError,
    _format_error,
    api_get,
    api_post,
)


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


def _ok(payload) -> httpx.Response:
    request = httpx.Request("GET", "https://dolibarr.test.local/api/index.php/x")
    return httpx.Response(200, json=payload, request=request)


@pytest.fixture
def scripted(monkeypatch):
    """Installe un client scénarisé et supprime les délais de backoff."""
    calls: list = []

    def install(*script):
        steps = list(script)
        monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kw: _ScriptedClient(steps, calls))
        return calls

    monkeypatch.setattr(server, "_RETRY_DELAY", 0)
    return install


# ---------------------------------------------------------------------------
# Le message
# ---------------------------------------------------------------------------


class TestMessageJamaisNu:
    def test_read_error_vide_est_nomme(self) -> None:
        msg = _format_error(httpx.ReadError(""))
        assert msg != "Erreur : "
        assert "ReadError" in msg
        assert "connexion coupée" in msg

    def test_read_timeout_vide_est_nomme(self) -> None:
        msg = _format_error(httpx.ReadTimeout(""))
        assert "ReadTimeout" in msg and "délai" in msg

    def test_exception_generique_sans_message_porte_son_type(self) -> None:
        msg = _format_error(RuntimeError(""))
        assert msg == "Erreur RuntimeError : sans message."

    def test_exception_generique_avec_message_inchangee(self) -> None:
        assert _format_error(RuntimeError("boum")) == "Erreur : boum"

    def test_erreur_definitive_nomme_route_type_et_tentatives(self) -> None:
        exc = DolibarrTransportError("ReadError", "GET", "meetingnotesapi/251", 3, True, "")
        msg = _format_error(exc)
        assert msg.startswith("Erreur réseau ReadError sur GET meetingnotesapi/251")
        assert "3 tentatives" in msg
        assert "rejouer" in msg.lower()

    def test_erreur_sur_ecriture_dit_quelle_n_a_pas_ete_rejouee(self) -> None:
        exc = DolibarrTransportError("ReadError", "POST", "meetingnotesapi/251/participants", 1, False)
        msg = _format_error(exc)
        assert "POST meetingnotesapi/251/participants" in msg
        assert "non rejouée" in msg
        assert "doublon" not in msg  # le message dit quoi faire, pas ce qu'on craint


# ---------------------------------------------------------------------------
# Le rejeu
# ---------------------------------------------------------------------------


class TestRejeu:
    async def test_lecture_rejouee_apres_read_error(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _ok({"id": 251}))
        assert await api_get("meetingnotesapi/251") == {"id": 251}
        assert len(calls) == 2

    async def test_lecture_rejouee_apres_remote_protocol_error(self, scripted) -> None:
        calls = scripted(
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            _ok([1, 2]),
        )
        assert await api_get("meetingnotesapi/actions") == [1, 2]
        assert len(calls) == 2

    async def test_lecture_abandonnee_apres_les_tentatives(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""), httpx.ReadTimeout(""), httpx.ReadTimeout(""))
        with pytest.raises(DolibarrTransportError) as info:
            await api_get("meetingnotesapi/251")
        assert len(calls) == server._MAX_RETRIES + 1
        assert info.value.attempts == 3 and info.value.replayable is True
        assert "GET meetingnotesapi/251" in str(info.value)

    async def test_ecriture_jamais_rejouee_une_fois_partie(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _ok({"id": 1}))
        with pytest.raises(DolibarrTransportError) as info:
            await api_post("meetingnotesapi/251/participants", {"nom_libre": "X"})
        assert len(calls) == 1, "un POST parti ne doit pas être renvoyé"
        assert info.value.replayable is False
        assert "non rejouée" in str(info.value)

    async def test_ecriture_rejouee_si_la_connexion_n_a_jamais_abouti(self, scripted) -> None:
        calls = scripted(httpx.ConnectTimeout(""), _ok({"id": 1}))
        assert await api_post("meetingnotesapi", {"titre": "X"}) == {"id": 1}
        assert len(calls) == 2

    async def test_l_erreur_d_origine_est_chainee(self, scripted) -> None:
        scripted(httpx.ReadError("reset"))
        with pytest.raises(DolibarrTransportError) as info:
            await api_post("meetingnotesapi", {})
        assert isinstance(info.value.__cause__, httpx.ReadError)
        assert info.value.detail == "reset"
