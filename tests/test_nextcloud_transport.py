# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs Nextcloud : nommées, codées, et disant l'état de l'écriture.

Même défaut que celui remonté en production sur Dolibarr le 08/09/2026 puis sur
Kanboard le 24/09/2026 : `_format_error` finissait par `return f"Erreur : {exc}"`
et `_request` n'attrapait que `ConnectError` et `ReadTimeout`. Une exception
httpx sans message - `ReadError`, `ReadTimeout` nu, `RemoteProtocolError` -
rendait donc « Erreur : » suivi de rien.

Le risque propre à Nextcloud tient à WebDAV, dont les verbes ne sont pas tous du
même bois :

- `PROPFIND` est une lecture, malgré son corps de requête. Il doit être rejoué
  comme un `GET`, sans quoi lister un dossier devient fragile pour rien ;
- `MOVE`, `DELETE` et `MKCOL` rejoués après une réponse perdue échouent en 404
  ou 405 sur une opération qui avait pourtant abouti, ce qui se lit comme un
  échec alors que c'est un succès ;
- `POST /ocs/.../shares` rejoué crée un SECOND lien de partage, avec son propre
  jeton. Le premier reste actif et personne ne le sait.

Cinq garanties :

1. aucun message nu ;
2. toute la famille `httpx.TransportError` est traitée, et PROPFIND compte
   comme une lecture ;
3. le code d'erreur est rendu, y compris le `<s:message>` de sabre/DAV ;
4. une écriture n'est JAMAIS rejouée automatiquement une fois la requête
   partie, et l'erreur dit ce qu'il advient de cette écriture ;
5. les écritures déjà appliquées dans le même appel d'outil sont nommées.
"""

import os

import httpx
import pytest

# Mêmes valeurs que tests/test_nextcloud.py : le module serveur lit ces
# variables une seule fois, à l'import, et l'ordre de collecte décide lequel
# des deux fichiers les fixe.
os.environ.setdefault("NEXTCLOUD_URL", "https://cloud.example.test")
os.environ.setdefault("NEXTCLOUD_USER", "alice")
os.environ.setdefault("NEXTCLOUD_APP_PASSWORD", "app-pass-xxxx")

from nextcloud_mcp import server  # noqa: E402
from nextcloud_mcp.server import (  # noqa: E402
    NextcloudTransportError,
    _format_error,
    _request,
    _write_journal,
)

_DAV = "https://cloud.example.test/remote.php/dav/files/alice/Projets/note.md"
_OCS = "https://cloud.example.test/ocs/v2.php/apps/files_sharing/api/v1/shares"

_DAV_404 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
    "<s:exception>Sabre\\DAV\\Exception\\NotFound</s:exception>"
    "<s:message>File with name /Projets/absent could not be located</s:message>"
    "</d:error>"
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


def _resp(status: int, *, json_body=None, text: str = "",
          content_type: str = "") -> httpx.Response:
    request = httpx.Request("GET", _DAV)
    headers = {"content-type": content_type} if content_type else {}
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=request)
    return httpx.Response(status, text=text, headers=headers, request=request)


def _http_error(status: int, text: str = "boom",
                content_type: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _DAV)
    headers = {"content-type": content_type} if content_type else {}
    response = httpx.Response(status, text=text, headers=headers, request=request)
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

    def test_value_error_de_configuration_reste_lisible(self) -> None:
        msg = _format_error(ValueError("NEXTCLOUD_USER doit être configuré."))
        assert msg == "Erreur : NEXTCLOUD_USER doit être configuré."

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

    def test_le_message_sabre_dav_reste_extrait(self) -> None:
        # Acquis de la version 1.3.0 : ne pas le perdre en portant le contrat.
        msg = _format_error(_http_error(404, _DAV_404, "application/xml"))
        assert msg.startswith("Erreur HTTP 404 : ")
        assert "could not be located" in msg
        assert "<s:message>" not in msg

    def test_le_message_json_ocs_est_extrait(self) -> None:
        request = httpx.Request("POST", _OCS)
        response = httpx.Response(
            403, json={"message": "Partage public interdit"}, request=request)
        exc = httpx.HTTPStatusError("erreur", request=request, response=response)
        assert "Partage public interdit" in _format_error(exc)

    def test_corps_vide_ne_finit_pas_nu(self) -> None:
        assert _format_error(_http_error(418, "")).strip() != "Erreur HTTP 418 :"


# ---------------------------------------------------------------------------
# 2. Toute la famille TransportError est traitée, PROPFIND compris
# ---------------------------------------------------------------------------


class TestFamilleTransport:
    async def test_read_error_sur_propfind_est_rejoue_puis_habille(
            self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("PROPFIND", _DAV)
        assert len(calls) == 3, "PROPFIND est une lecture, malgré son corps de requête"
        assert info.value.kind == "ReadError" and info.value.attempts == 3

    async def test_propfind_est_sans_objet_cote_ecriture(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("PROPFIND", _DAV)
        assert info.value.write_state == "sans_objet"

    async def test_write_timeout_en_lecture_est_rejoue(self, scripted) -> None:
        # WriteTimeout n'était pas attrapé du tout : il traversait sans rejeu.
        calls = scripted(httpx.WriteTimeout(""), _resp(200, json_body={"ok": 1}))
        assert await _request("GET", _DAV) == {"ok": 1}
        assert len(calls) == 2

    async def test_remote_protocol_error_est_habillee(self, scripted) -> None:
        scripted(httpx.RemoteProtocolError(""), httpx.RemoteProtocolError(""),
                 httpx.RemoteProtocolError(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("GET", _DAV)
        assert "mal formée" in _format_error(info.value)


# ---------------------------------------------------------------------------
# 3. Le code d'erreur est rendu
# ---------------------------------------------------------------------------


class TestCodeRendu:
    async def test_le_statut_http_traverse(self, scripted) -> None:
        # nextcloud_upload_file distingue 412, 404 et 409 sur l'exception httpx.
        scripted(_resp(412, text="Precondition Failed"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("PUT", _DAV, content=b"x")
        assert info.value.response.status_code == 412


# ---------------------------------------------------------------------------
# 4. Une écriture n'est pas rejouée, et l'erreur dit son état
# ---------------------------------------------------------------------------


class TestEcritureNonRejouee:
    async def test_read_timeout_sur_put_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("PUT", _DAV, content=b"x")
        assert len(calls) == 1
        assert info.value.write_state == "inconnu"

    async def test_read_timeout_sur_post_de_partage_n_est_pas_rejoue(
            self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("POST", _OCS, data={"path": "/x"})
        assert len(calls) == 1, "un partage rejoué crée un second lien public"
        assert info.value.write_state == "inconnu"

    async def test_connect_error_sur_post_est_rejoue(self, scripted) -> None:
        calls = scripted(httpx.ConnectError(""), httpx.ConnectError(""),
                         httpx.ConnectError(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("POST", _OCS, data={"path": "/x"})
        assert len(calls) == 3
        assert info.value.write_state == "non_appliquee"

    async def test_5xx_sur_move_n_est_pas_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad gateway"))
        with pytest.raises(httpx.HTTPStatusError):
            await _request("MOVE", _DAV)
        assert len(calls) == 1

    async def test_5xx_sur_propfind_est_rejoue(self, scripted) -> None:
        calls = scripted(_resp(502, text="bad"), _resp(207, text="<d:multistatus/>",
                                                       content_type="application/xml"))
        assert await _request("PROPFIND", _DAV) == "<d:multistatus/>"
        assert len(calls) == 2

    async def test_mkcol_delete_et_copy_sont_des_ecritures(self, scripted) -> None:
        for method in ("MKCOL", "DELETE", "COPY"):
            calls = scripted(httpx.ReadError(""))
            with pytest.raises(NextcloudTransportError) as info:
                await _request(method, _DAV)
            assert len(calls) == 1, method
            assert info.value.write_state == "inconnu", method
            calls.clear()


class TestWriteState:
    async def test_inconnu_demande_de_relire_avant_de_rejouer(self, scripted) -> None:
        scripted(httpx.ReadError(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("PUT", _DAV, content=b"x")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_non_appliquee_autorise_le_rejeu(self, scripted) -> None:
        scripted(httpx.ConnectError(""), httpx.ConnectError(""), httpx.ConnectError(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("PUT", _DAV, content=b"x")
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "sans risque" in msg

    async def test_lecture_est_sans_objet(self, scripted) -> None:
        scripted(httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        with pytest.raises(NextcloudTransportError) as info:
            await _request("GET", _DAV)
        assert info.value.write_state == "sans_objet"
        assert "criture" not in _format_error(info.value)

    async def test_4xx_sur_ecriture_dit_refusee_avant_execution(self, scripted) -> None:
        scripted(_resp(400, text="Bad Request"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("PUT", _DAV, content=b"x")
        msg = _format_error(info.value)
        assert "non appliquée" in msg and "corriger l'appel" in msg

    async def test_5xx_sur_ecriture_laisse_l_etat_inconnu(self, scripted) -> None:
        scripted(_resp(503, text="unavailable"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("PUT", _DAV, content=b"x")
        msg = _format_error(info.value)
        assert "inconnu" in msg and "relire" in msg

    async def test_401_sur_ecriture_garde_son_message(self, scripted) -> None:
        scripted(_resp(401, text="Unauthorized"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("PUT", _DAV, content=b"x")
        msg = _format_error(info.value)
        assert "401" in msg and "non appliquée" not in msg

    async def test_412_sur_un_put_reste_qualifiable_par_l_outil(self, scripted) -> None:
        # nextcloud_upload_file traite le 412 lui-même : « la cible existe »,
        # pas « rien n'a été déposé ». Le message générique ne doit pas le
        # contredire en affirmant que l'écriture n'a pas eu lieu.
        scripted(_resp(412, text="Precondition Failed"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await _request("PUT", _DAV, content=b"x")
        assert info.value.response.status_code == 412


# ---------------------------------------------------------------------------
# 5. Le journal des écritures déjà appliquées
# ---------------------------------------------------------------------------


class TestJournalDesEcritures:
    async def test_un_depot_reussi_puis_un_echec_nomme_le_premier(self, scripted) -> None:
        # Le cas réel : déposer le fichier, puis créer son lien de partage.
        scripted(_resp(201, text=""), httpx.ReadError(""))
        await _request("PUT", _DAV, content=b"x")
        with pytest.raises(NextcloudTransportError) as info:
            await _request("POST", _OCS, data={"path": "/x"})
        msg = _format_error(info.value)
        assert "déjà appliquées" in msg
        assert "PUT" in msg and "note.md" in msg

    async def test_le_journal_nomme_la_route_sans_l_hote(self, scripted) -> None:
        scripted(_resp(201, text=""))
        await _request("PUT", _DAV, content=b"x")
        assert _write_journal() == ["PUT /Projets/note.md"]

    async def test_un_propfind_ne_rentre_pas_au_journal(self, scripted) -> None:
        scripted(_resp(207, text="<d:multistatus/>", content_type="application/xml"),
                 httpx.ReadError(""), httpx.ReadError(""), httpx.ReadError(""))
        await _request("PROPFIND", _DAV)
        with pytest.raises(NextcloudTransportError) as info:
            await _request("GET", _DAV)
        assert "déjà appliquées" not in _format_error(info.value)

    async def test_le_journal_est_propre_a_l_appel_d_outil(self, scripted) -> None:
        scripted(_resp(201, text=""))
        await _request("PUT", _DAV, content=b"x")
        assert _write_journal() == ["PUT /Projets/note.md"]

        async def autre_appel() -> list[str]:
            return list(_write_journal())

        import asyncio
        assert await asyncio.create_task(autre_appel()) == []


# ---------------------------------------------------------------------------
# 6. Compatibilité des formes de retour
# ---------------------------------------------------------------------------


class TestCompatibilite:
    async def test_json_rendu_en_dict(self, scripted) -> None:
        scripted(_resp(200, json_body={"ocs": {"data": {"id": 1}}}))
        assert await _request("GET", _OCS) == {"ocs": {"data": {"id": 1}}}

    async def test_xml_rendu_en_texte(self, scripted) -> None:
        scripted(_resp(207, text="<d:multistatus/>", content_type="application/xml"))
        assert await _request("PROPFIND", _DAV) == "<d:multistatus/>"

    async def test_binaire_rendu_en_octets(self, scripted) -> None:
        scripted(_resp(200, text="contenu", content_type="application/octet-stream"))
        assert await _request("GET", _DAV) == b"contenu"

    async def test_corps_vide_rend_un_dict_vide(self, scripted) -> None:
        scripted(_resp(204, text=""))
        assert await _request("DELETE", _DAV) == {}

    async def test_raw_response_rend_la_reponse_brute(self, scripted) -> None:
        scripted(_resp(200, text="contenu"))
        resp = await _request("GET", _DAV, raw_response=True)
        assert isinstance(resp, httpx.Response) and resp.text == "contenu"

    async def test_une_ecriture_en_raw_response_rentre_au_journal(self, scripted) -> None:
        scripted(_resp(201, text=""))
        await _request("PUT", _DAV, raw_response=True, content=b"x")
        assert _write_journal() == ["PUT /Projets/note.md"]
