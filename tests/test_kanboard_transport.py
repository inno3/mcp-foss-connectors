# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Erreurs Kanboard : nommées, codées, et disant l'état de l'écriture.

Constat du 24/09/2026 : `kanboard_add_comment` puis `kanboard_get_task` ont
répondu « Erreur : » sans code ni message, deux fois sur ~25 appels d'une même
session, en écriture comme en lecture ; les deux appels rejoués à l'identique
ont réussi. Une chaîne vide ne distingue pas un défaut d'authentification, un
objet absent, un dépassement de taille et une défaillance passagère : l'appelant
ne peut pas décider s'il faut rejouer, corriger l'appel ou s'arrêter.

Ces tests figent quatre corrections :

1. aucun message nu — le type d'exception et un libellé remplacent le vide ;
2. le code d'erreur JSON-RPC (-32602, -32603...) n'est plus jeté ;
3. une écriture n'est JAMAIS rejouée automatiquement une fois la requête
   partie (le rejeu aveugle posait deux commentaires), et l'erreur dit si
   l'opération a pu être appliquée : « non appliquée » quand la requête n'a pas
   atteint Kanboard, « inconnu » quand la réponse s'est perdue ;
4. les écritures déjà appliquées plus tôt dans le même appel d'outil sont
   nommées, pour que l'appelant sache quoi relire avant de rejouer.
"""

import asyncio
import os

import httpx
import pytest

os.environ.setdefault("KANBOARD_URL", "https://kb.example.test/jsonrpc.php")
os.environ.setdefault("KANBOARD_USER", "alice")
os.environ.setdefault("KANBOARD_TOKEN", "tok-primary")
os.environ.setdefault("KANBOARD_USER_ALT", "bot")
os.environ.setdefault("KANBOARD_TOKEN_ALT", "tok-agent")

from kanboard_mcp import server  # noqa: E402
from kanboard_mcp.server import (  # noqa: E402
    KanboardAPIError,
    KanboardTransportError,
    _format_error,
    _is_mutating_method,
    kanboard_add_comment,
    kb_call,
)

_URL = "https://kb.example.test/jsonrpc.php"


class _ScriptedClient:
    """httpx.AsyncClient minimal : rejoue une séquence d'exceptions ou de réponses."""

    def __init__(self, script: list, calls: list):
        self.script = script
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((kwargs.get("json") or {}).get("method", "?"))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _rpc_ok(result) -> httpx.Response:
    request = httpx.Request("POST", _URL)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result},
                          request=request)


def _rpc_err(error) -> httpx.Response:
    request = httpx.Request("POST", _URL)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": error},
                          request=request)


def _http(status: int, text: str = "boom") -> httpx.Response:
    request = httpx.Request("POST", _URL)
    return httpx.Response(status, text=text, request=request)


def _http_403() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", _URL)
    return httpx.HTTPStatusError(
        "Forbidden", request=request, response=httpx.Response(403, request=request)
    )


@pytest.fixture(autouse=True)
def _identite_deja_etablie(monkeypatch):
    """Ici le transport est teste apres les sondes getMe, couvertes ailleurs.

    Reprise de la fixture 0.16, avec les cles (URL, login, token) de la 0.17.1.
    Une sonde ne doit pas consommer une etape du scenario HTTP de transport.
    """
    monkeypatch.setattr(server, "_identity_cache", {
        server._identity_key("primary"): {
            "user_id": 4, "username": server.KANBOARD_USER, "name": "Synthetic Primary",
        },
        server._identity_key("agent"): {
            "user_id": 84, "username": server.KANBOARD_USER_ALT, "name": "Synthetic Agent",
        },
    })
    monkeypatch.setattr(server, "_my_id_cache", {})
    monkeypatch.setattr(server, "_my_name_cache", {})


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

    def test_exception_generique_sans_message_porte_son_type(self) -> None:
        assert _format_error(RuntimeError("")) == "Erreur RuntimeError : sans message."

    def test_exception_generique_avec_message_inchangee(self) -> None:
        assert _format_error(RuntimeError("boum")) == "Erreur : boum"

    def test_les_messages_http_connus_sont_inchanges(self) -> None:
        # Compatibilité : trois messages sur lesquels des appelants s'appuient.
        assert _format_error(_http_403()) == "Erreur 403 : permissions insuffisantes."


# ---------------------------------------------------------------------------
# 2. L'erreur applicative JSON-RPC porte son code
# ---------------------------------------------------------------------------


class TestCodeJsonRpc:
    async def test_le_code_et_le_message_sont_rendus(self, scripted) -> None:
        scripted(_rpc_err({"code": -32602, "message": "Too many arguments"}))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("getProjectActivity", {"project_id": 262})
        assert info.value.code == -32602
        msg = _format_error(info.value)
        assert "-32602" in msg and "Too many arguments" in msg
        assert "paramètres" in msg  # le libellé du code, pour un lecteur humain

    async def test_un_code_sans_message_reste_diagnostiquable(self, scripted) -> None:
        scripted(_rpc_err({"code": -32603}))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("getTask", {"task_id": 7911})
        msg = _format_error(info.value)
        assert "-32603" in msg and "interne" in msg
        assert msg.strip() != "Erreur :"

    async def test_une_erreur_non_structuree_est_conservee(self, scripted) -> None:
        scripted(_rpc_err("boum"))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("getTask", {"task_id": 1})
        assert "boum" in str(info.value)

    async def test_le_marqueur_de_permission_reste_detectable(self, scripted) -> None:
        # _is_permission_error lit le texte : un refus applicatif doit rester
        # reconnaissable, sinon getAllProjects ne retombe plus sur getMyProjects.
        scripted(_rpc_err({"code": -32000, "message": "Access Forbidden"}))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("getAllProjects")
        assert server._is_permission_error(info.value) is True

    async def test_un_corps_non_json_est_nomme(self, scripted) -> None:
        scripted(_http(200, "<html>502 Bad Gateway</html>"),
                 _http(200, "<html>502 Bad Gateway</html>"),
                 _http(200, "<html>502 Bad Gateway</html>"))
        with pytest.raises(Exception) as info:
            await kb_call("getTask", {"task_id": 1})
        msg = _format_error(info.value)
        assert "JSON" in msg or "json" in msg
        assert msg.strip() != "Erreur :"


# ---------------------------------------------------------------------------
# 3. Rejeu et état de l'écriture
# ---------------------------------------------------------------------------


class TestClassementDesMethodes:
    @pytest.mark.parametrize("method", [
        "getTask", "getAllProjects", "getMe", "getProjectActivity", "getBoard",
    ])
    def test_les_lectures_sont_reconnues(self, method) -> None:
        assert _is_mutating_method(method) is False

    @pytest.mark.parametrize("method", [
        "createComment", "updateTask", "removeTask", "moveTaskPosition",
        "closeTask", "openTask", "addProjectUser", "changeColumnPosition",
        "enableSwimlane", "disableSwimlane", "methodeInconnue",
    ])
    def test_tout_le_reste_est_traite_comme_une_ecriture(self, method) -> None:
        # Le défaut conservateur : une écriture prise pour une lecture serait
        # rejouée et ferait un doublon, l'inverse ne coûte qu'un rejeu perdu.
        assert _is_mutating_method(method) is True


class TestRejeu:
    async def test_lecture_rejouee_apres_read_error(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _rpc_ok({"id": 7911}))
        assert await kb_call("getTask", {"task_id": 7911}) == {"id": 7911}
        assert len(calls) == 2

    async def test_lecture_rejouee_apres_remote_protocol_error(self, scripted) -> None:
        calls = scripted(
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            _rpc_ok([1, 2]),
        )
        assert await kb_call("getAllTasks", {"project_id": 262}) == [1, 2]
        assert len(calls) == 2

    async def test_lecture_abandonnee_apres_les_tentatives(self, scripted) -> None:
        calls = scripted(httpx.ReadTimeout(""), httpx.ReadTimeout(""), httpx.ReadTimeout(""))
        with pytest.raises(KanboardTransportError) as info:
            await kb_call("getTask", {"task_id": 7911})
        assert len(calls) == server._MAX_RETRIES + 1
        assert info.value.attempts == 3
        assert info.value.write_state == "sans_objet"
        msg = _format_error(info.value)
        assert "getTask" in msg and "rejouer" in msg.lower()

    async def test_ecriture_jamais_rejouee_une_fois_partie(self, scripted) -> None:
        calls = scripted(httpx.ReadError(""), _rpc_ok(2511))
        with pytest.raises(KanboardTransportError) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        assert len(calls) == 1, "une écriture partie ne doit pas être renvoyée"
        assert info.value.write_state == "inconnu"
        msg = _format_error(info.value)
        assert "createComment" in msg
        assert "inconnu" in msg and "relire" in msg.lower()

    async def test_ecriture_rejouee_si_la_connexion_n_a_jamais_abouti(self, scripted) -> None:
        calls = scripted(httpx.ConnectTimeout(""), _rpc_ok(2511))
        assert await kb_call("createComment", {"task_id": 7580, "content": "x"}) == 2511
        assert len(calls) == 2

    async def test_ecriture_non_partie_le_dit(self, scripted) -> None:
        calls = scripted(httpx.ConnectError(""), httpx.ConnectError(""), httpx.ConnectError(""))
        with pytest.raises(KanboardTransportError) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        assert len(calls) == 3
        assert info.value.write_state == "non_appliquee"
        msg = _format_error(info.value)
        assert "non appliquée" in msg
        assert "doublon" in msg  # le rejeu est explicitement déclaré sans risque

    async def test_5xx_rejoue_en_lecture(self, scripted) -> None:
        calls = scripted(_http(500), _rpc_ok({"id": 1}))
        assert await kb_call("getTask", {"task_id": 1}) == {"id": 1}
        assert len(calls) == 2

    async def test_5xx_non_rejoue_en_ecriture(self, scripted) -> None:
        calls = scripted(_http(500), _rpc_ok(2511))
        with pytest.raises(Exception):
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        assert len(calls) == 1

    async def test_5xx_en_ecriture_dit_l_etat_inconnu(self, scripted) -> None:
        scripted(_http(500, "Internal Server Error"))
        with pytest.raises(Exception) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        msg = _format_error(info.value)
        assert "500" in msg
        assert "inconnu" in msg and "relire" in msg.lower()

    async def test_401_en_ecriture_reste_un_message_d_authentification(self, scripted) -> None:
        scripted(_http(401, "Unauthorized"))
        with pytest.raises(Exception) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        # Une auth refusée n'écrit rien : pas de doute à semer sur l'état.
        assert _format_error(info.value) == "Erreur 401 : authentification Kanboard invalide."

    async def test_413_en_ecriture_dit_que_rien_n_a_ete_ecrit(self, scripted) -> None:
        # Le cas « dépassement de taille » : un commentaire trop long refusé par
        # le proxy ne doit pas envoyer relire la carte pour rien.
        scripted(_http(413, "Request Entity Too Large"))
        with pytest.raises(Exception) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x" * 10**6})
        msg = _format_error(info.value)
        assert "413" in msg
        assert "non appliquée" in msg and "corriger l'appel" in msg
        assert "relire" not in msg.lower()

    async def test_les_phrases_ne_se_soudent_pas(self, scripted) -> None:
        # « Internal error État de l'écriture … » : deux phrases collées se
        # lisent comme une seule, et le lecteur saute l'avertissement.
        calls = scripted(_http(500, "Internal Server Error"))
        with pytest.raises(httpx.HTTPStatusError) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        assert len(calls) == 1, "sans cela, un script épuisé leverait un IndexError"
        msg = _format_error(info.value)
        assert "Internal Server Error. État de l'écriture" in msg
        assert "Error État" not in msg

    async def test_un_code_sans_message_ne_repete_pas_son_libelle(self, scripted) -> None:
        scripted(_rpc_err({"code": -32601}))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("getTruc")
        msg = _format_error(info.value)
        assert msg.count("méthode introuvable") == 1
        assert "sans message de Kanboard" in msg

    async def test_le_prefixe_d_erreur_applicative_ne_se_double_pas(self, scripted) -> None:
        scripted(_rpc_err({"code": -32602, "message": "Too many arguments"}))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("getProjectActivity", {"project_id": 262})
        assert _format_error(info.value).startswith("Erreur Kanboard -32602")

    async def test_erreur_applicative_sur_ecriture_dit_le_refus(self, scripted) -> None:
        scripted(_rpc_err({"code": -32602, "message": "Too many arguments"}))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        assert info.value.write_state == "non_appliquee"
        assert "non appliquée" in _format_error(info.value)

    async def test_erreur_interne_sur_ecriture_laisse_l_etat_inconnu(self, scripted) -> None:
        scripted(_rpc_err({"code": -32603, "message": "Internal error"}))
        with pytest.raises(KanboardAPIError) as info:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})
        assert info.value.write_state == "inconnu"
        assert "relire" in _format_error(info.value).lower()

    async def test_l_erreur_d_origine_est_chainee(self, scripted) -> None:
        scripted(httpx.ReadError("reset"))
        with pytest.raises(KanboardTransportError) as info:
            await kb_call("createComment", {"task_id": 1, "content": "x"})
        assert isinstance(info.value.__cause__, httpx.ReadError)
        assert info.value.detail == "reset"


# ---------------------------------------------------------------------------
# 4. Les écritures déjà appliquées dans le même appel
# ---------------------------------------------------------------------------


class TestEcrituresDejaAppliquees:
    async def test_une_ecriture_reussie_est_nommee_dans_l_erreur_suivante(self, scripted) -> None:
        scripted(_rpc_ok(2511), httpx.ReadTimeout(""), httpx.ReadTimeout(""),
                 httpx.ReadTimeout(""))
        await kb_call("createComment", {"task_id": 7580, "content": "x"})
        with pytest.raises(Exception) as info:
            await kb_call("getTask", {"task_id": 7580})
        msg = _format_error(info.value)
        assert "déjà appliquées" in msg and "createComment" in msg

    async def test_sans_ecriture_prealable_rien_n_est_annonce(self, scripted) -> None:
        scripted(httpx.ReadTimeout(""), httpx.ReadTimeout(""), httpx.ReadTimeout(""))
        with pytest.raises(Exception) as info:
            await kb_call("getTask", {"task_id": 7580})
        msg = _format_error(info.value)
        assert "ReadTimeout" in msg and "getTask" in msg  # le message reste complet
        assert "déjà appliquées" not in msg

    async def test_une_lecture_reussie_n_est_pas_comptee(self, scripted) -> None:
        scripted(_rpc_ok({"id": 1}), httpx.ReadTimeout(""), httpx.ReadTimeout(""),
                 httpx.ReadTimeout(""))
        await kb_call("getTask", {"task_id": 1})
        with pytest.raises(Exception) as info:
            await kb_call("getBoard", {"project_id": 262})
        msg = _format_error(info.value)
        assert "ReadTimeout" in msg and "getBoard" in msg
        assert "déjà appliquées" not in msg

    async def test_ce_qu_une_tache_ecrit_ne_remonte_pas_a_sa_soeur(self, scripted) -> None:
        # Un appel d'outil MCP = une tâche asyncio. Une tâche écrit dans SA copie
        # du contexte : l'écriture de la première ne doit pas suivre la seconde.
        scripted(_rpc_ok(2511), httpx.ReadTimeout(""), httpx.ReadTimeout(""),
                 httpx.ReadTimeout(""))

        async def ecrit() -> None:
            await kb_call("createComment", {"task_id": 7580, "content": "x"})

        async def echoue() -> str:
            try:
                await kb_call("getTask", {"task_id": 7580})
            except Exception as exc:
                return _format_error(exc)
            return ""

        await asyncio.create_task(ecrit())
        msg = await asyncio.create_task(echoue())
        assert "ReadTimeout" in msg and "getTask" in msg  # l'échec attendu, pas un autre
        assert "déjà appliquées" not in msg

    async def test_une_tache_fille_n_herite_pas_du_journal(self, scripted) -> None:
        # Le chemin que le test précédent ne couvre PAS : ici l'écriture a lieu
        # dans la tâche courante, dont la fille reçoit une copie du contexte,
        # journal compris. Seul le contrôle d'identité de tâche l'empêche de
        # s'attribuer l'écriture de son parent.
        scripted(_rpc_ok(2511), httpx.ReadTimeout(""), httpx.ReadTimeout(""),
                 httpx.ReadTimeout(""))
        await kb_call("createComment", {"task_id": 7580, "content": "x"})
        assert server._write_journal() == ["createComment"]

        async def fille() -> str:
            assert server._write_journal() == []
            try:
                await kb_call("getTask", {"task_id": 7580})
            except Exception as exc:
                return _format_error(exc)
            return ""

        msg = await asyncio.create_task(fille())
        assert "déjà appliquées" not in msg
        # Et le parent garde le sien : la fille ne l'a pas vidé.
        assert server._write_journal() == ["createComment"]


# ---------------------------------------------------------------------------
# 5. Bout en bout : le cas remonté le 24/09/2026
# ---------------------------------------------------------------------------


class TestBoutEnBout:
    async def test_add_comment_dit_ce_qu_il_ne_sait_pas(self, scripted, monkeypatch) -> None:
        monkeypatch.setattr(server, "_my_id_cache", {"agent": 84})
        scripted(httpx.ReadError(""))
        out = await kanboard_add_comment(task_id=7580, comment="texte", as_user="agent")
        assert out.strip() != "Erreur :"
        assert "createComment" in out
        assert "inconnu" in out and "relire" in out.lower()

    async def test_get_task_nomme_la_defaillance(self, scripted, monkeypatch) -> None:
        scripted(httpx.ReadTimeout(""), httpx.ReadTimeout(""), httpx.ReadTimeout(""))
        out = await server.kanboard_get_task(task_id=7911)
        assert out.strip() != "Erreur :"
        assert "ReadTimeout" in out and "getTask" in out
