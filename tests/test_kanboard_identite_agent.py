# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Une écriture « agent » ne part jamais sous l'identité du compte primary.

Constat du 16/09/2026 : des écritures demandées avec `as_user="agent"` se sont
retrouvées enregistrées sous le compte humain. La réponse annonçait pourtant
`as_user: agent`.

Cause : `_get_credentials("agent")` retombait en silence sur les identifiants
primary dès que `KANBOARD_USER_ALT` ou `KANBOARD_TOKEN_ALT` manquait, et rendait
le libellé « primary » que personne ne lisait ; les outils construisaient leur
réponse avec `_resolve_account(as_user)`, c'est-à-dire avec ce qui avait été
DEMANDÉ, pas avec ce qui avait servi. Un poste mal configuré signait donc du nom
de Benjamin Jean des écritures attribuées à l'assistant, sans un mot.

Ces tests figent quatre règles :

1. une identité agent qui ne peut pas être établie interdit l'écriture, sans
   repli d'aucune sorte, et aucune requête ne part ;
2. des identifiants agent qui désignent en réalité le compte primary sont un
   refus, pas une configuration acceptable ;
3. l'identité est vérifiée par `getMe` avant la première écriture d'un processus,
   puis gardée en cache ;
4. la réponse d'une écriture rend le compte réellement utilisé et l'auteur
   réellement enregistré.
"""

import json
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
    KanboardIdentityError,
    _get_credentials,
    kanboard_add_comment,
    kanboard_create_task,
    kanboard_list_accounts,
    kanboard_who_am_i,
    kb_call,
)

_URL = "https://kb.example.test/jsonrpc.php"


def _rpc_ok(result) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "result": result},
        request=httpx.Request("POST", _URL),
    )


def _rpc_err(code: int, message: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}},
        request=httpx.Request("POST", _URL),
    )


class _Wire:
    """httpx.AsyncClient minimal qui relève les méthodes et les comptes appelés.

    `by_method` donne la réponse à rendre pour une méthode JSON-RPC. Une méthode
    absente du script fait échouer le test : c'est la façon de constater qu'une
    écriture est partie alors qu'elle n'aurait pas dû.
    """

    def __init__(self, by_method: dict, log: list):
        self.by_method = by_method
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        payload = kwargs.get("json") or {}
        method = payload.get("method", "?")
        auth = (kwargs.get("headers") or {}).get("Authorization", "")
        self.log.append((method, auth))
        if method not in self.by_method:
            raise AssertionError(f"requête inattendue sur le réseau : {method}")
        step = self.by_method[method]
        return step(self) if callable(step) else step


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Caches vidés et configuration nominale rétablie avant chaque test."""
    server._my_id_cache.clear()
    server._my_name_cache.clear()
    server._identity_cache.clear()
    monkeypatch.setattr(server, "KANBOARD_USER", "alice")
    monkeypatch.setattr(server, "KANBOARD_TOKEN", "tok-primary")
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "bot")
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", "tok-agent")
    yield
    server._my_id_cache.clear()
    server._my_name_cache.clear()
    server._identity_cache.clear()


def _wire(monkeypatch, by_method: dict) -> list:
    log: list = []
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda **kw: _Wire(by_method, log)
    )
    return log


# ---------------------------------------------------------------------------
# 1. Identifiants agent absents : refus, pas de repli
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_alt, token_alt",
    [("", "tok-agent"), ("bot", ""), ("", "")],
)
def test_credentials_agent_absents_refusent_sans_repli(monkeypatch, user_alt, token_alt):
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", user_alt)
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", token_alt)
    with pytest.raises(KanboardIdentityError) as exc:
        _get_credentials("agent")
    # Le message doit nommer les deux variables à définir : un refus qui ne dit
    # pas quoi corriger se contourne en repassant en primary.
    assert "KANBOARD_USER_ALT" in str(exc.value)
    assert "KANBOARD_TOKEN_ALT" in str(exc.value)


def test_credentials_primary_reste_servi_quand_agent_absent(monkeypatch):
    """Le compte primary n'est pas la victime du verrou posé sur l'agent."""
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "")
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", "")
    assert _get_credentials("primary") == ("alice", "tok-primary", "primary")


async def test_ecriture_agent_sans_identifiants_ne_part_pas(monkeypatch):
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "")
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", "")
    log = _wire(monkeypatch, {})  # tout appel réseau fait échouer le test
    with pytest.raises(KanboardIdentityError):
        await kb_call("createComment", {"task_id": 1}, as_user="agent")
    assert log == []


async def test_lecture_agent_sans_identifiants_ne_part_pas_non_plus(monkeypatch):
    """Une lecture signée du mauvais compte fuite aussi : même refus."""
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "")
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", "")
    log = _wire(monkeypatch, {})
    with pytest.raises(KanboardIdentityError):
        await kb_call("getTask", {"task_id": 1}, as_user="agent")
    assert log == []


# ---------------------------------------------------------------------------
# 2. Identifiants agent qui désignent le compte primary
# ---------------------------------------------------------------------------


async def test_login_agent_egal_au_login_primary_refuse_avant_tout_appel(monkeypatch):
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "alice")
    log = _wire(monkeypatch, {})
    with pytest.raises(KanboardIdentityError) as exc:
        await kb_call("createComment", {"task_id": 1}, as_user="agent")
    assert "alice" in str(exc.value)
    assert log == []


async def test_token_agent_appartenant_au_primary_refuse_l_ecriture(monkeypatch):
    """Le login déclaré dit « bot », le token authentifie « alice » : refus.

    C'est la forme la plus dangereuse de la panne : rien dans la configuration
    ne se voit, seul `getMe` dit qui parle réellement.
    """
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 4, "username": "alice", "name": "Benjamin Jean"}),
    })
    with pytest.raises(KanboardIdentityError) as exc:
        await kb_call("createComment", {"task_id": 1}, as_user="agent")
    message = str(exc.value)
    assert "bot" in message and "alice" in message
    assert [m for m, _ in log] == ["getMe"]


async def test_uid_agent_egal_a_l_uid_primary_refuse_l_ecriture(monkeypatch):
    """Même identifiant numérique sous deux logins : une seule personne."""
    server._my_id_cache["primary"] = 4
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 4, "username": "bot", "name": "agent"}),
    })
    with pytest.raises(KanboardIdentityError) as exc:
        await kb_call("createComment", {"task_id": 1}, as_user="agent")
    assert "4" in str(exc.value)
    assert [m for m, _ in log] == ["getMe"]


async def test_getme_muet_pour_l_agent_refuse_l_ecriture(monkeypatch):
    """Sans `getMe`, l'identité de l'agent n'est pas établie : pas d'écriture.

    Les replis `getUserByLoginName` et `getAllUsers` disent quel utilisateur
    porte ce login, jamais quel utilisateur le token authentifie. Ils ne peuvent
    donc pas fonder une traçabilité.
    """
    log = _wire(monkeypatch, {
        "getMe": _rpc_err(-32601, "Method not found"),
    })
    with pytest.raises(KanboardIdentityError):
        await kb_call("createComment", {"task_id": 1}, as_user="agent")
    assert [m for m, _ in log] == ["getMe"]


# ---------------------------------------------------------------------------
# 3. Cas nominal et cache
# ---------------------------------------------------------------------------


async def test_cas_nominal_verifie_puis_ecrit(monkeypatch):
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 84, "username": "bot", "name": "agent"}),
        "createComment": _rpc_ok(4242),
    })
    assert await kb_call("createComment", {"task_id": 1}, as_user="agent") == 4242
    assert [m for m, _ in log] == ["getMe", "createComment"]


async def test_identite_verifiee_une_fois_puis_gardee_en_cache(monkeypatch):
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 84, "username": "bot", "name": "agent"}),
        "createComment": _rpc_ok(1),
    })
    await kb_call("createComment", {"task_id": 1}, as_user="agent")
    await kb_call("createComment", {"task_id": 2}, as_user="agent")
    assert [m for m, _ in log] == ["getMe", "createComment", "createComment"]


async def test_lecture_agent_ne_paie_pas_la_verification(monkeypatch):
    """La vérification `getMe` est posée avant la première ÉCRITURE, pas avant
    chaque lecture : c'est l'attribution d'une écriture qui ne se rattrape pas.
    Une lecture signée du mauvais compte reste refusée en amont quand les
    identifiants agent manquent (cf. le test dédié), mais avec des identifiants
    propres elle part directement, sans appel supplémentaire."""
    log = _wire(monkeypatch, {"getTask": _rpc_ok({"id": 7})})
    assert await kb_call("getTask", {"task_id": 7}, as_user="agent") == {"id": 7}
    assert [m for m, _ in log] == ["getTask"]


async def test_primary_sans_getme_ecrit_quand_meme(monkeypatch):
    """Un token applicatif ne répond pas `getMe`. Le compte humain n'est pas
    bloqué pour autant : c'est l'agent dont l'usurpation est le risque, et la
    vérification reste déclarée non faite dans le diagnostic."""
    log = _wire(monkeypatch, {
        "getMe": _rpc_err(-32601, "Method not found"),
        "createComment": _rpc_ok(9),
    })
    assert await kb_call("createComment", {"task_id": 1}, as_user="primary") == 9
    assert [m for m, _ in log] == ["getMe", "createComment"]
    assert server._identity_cache["primary"]["verified"] is False


async def test_primary_authentifie_sous_un_autre_login_refuse(monkeypatch):
    """`getMe` répond « bot » alors que KANBOARD_USER dit « alice » : les
    écritures « primary » seraient attribuées à l'assistant. Refus."""
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 84, "username": "bot", "name": "agent"}),
    })
    with pytest.raises(KanboardIdentityError):
        await kb_call("createComment", {"task_id": 1}, as_user="primary")
    assert [m for m, _ in log] == ["getMe"]


# ---------------------------------------------------------------------------
# 4. Ce que la réponse annonce
# ---------------------------------------------------------------------------


async def test_add_comment_rend_le_compte_servi_et_l_auteur_enregistre(monkeypatch):
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 84, "username": "bot", "name": "agent"}),
        "createComment": _rpc_ok(555),
        "getComment": _rpc_ok({"id": 555, "user_id": "84", "task_id": 1}),
    })
    out = json.loads(await kanboard_add_comment(task_id=1, comment="temoin", as_user="agent"))
    assert out["as_user"] == "agent"
    assert out["author_user_id"] == 84
    # L'auteur relu depuis Kanboard, pas seulement celui qu'on croyait écrire.
    assert out["author_user_id_recorded"] == 84
    assert "getComment" in [m for m, _ in log]


async def test_add_comment_refuse_ne_rend_pas_as_user_agent(monkeypatch):
    """Le mensonge d'origine : la réponse annonçait `as_user: agent` pour une
    écriture partie en primary. Le refus ne doit surtout pas le reproduire."""
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "")
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", "")
    _wire(monkeypatch, {})
    out = await kanboard_add_comment(task_id=1, comment="temoin", as_user="agent")
    assert '"as_user"' not in out
    assert "success" not in out.lower()
    assert "KANBOARD_USER_ALT" in out


async def test_create_task_rend_l_auteur_reellement_enregistre(monkeypatch):
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 84, "username": "bot", "name": "agent"}),
        "createTask": _rpc_ok(31),
        "getTask": _rpc_ok({"id": 31, "creator_id": "84", "title": "t"}),
    })
    out = json.loads(await kanboard_create_task(project_id=262, title="t", as_user="agent"))
    assert out["task_id"] == 31
    assert out["as_user"] == "agent"
    assert out["creator_id"] == 84
    assert "author_mismatch" not in out
    assert "getTask" in [m for m, _ in log]


async def test_create_task_signale_un_auteur_enregistre_inattendu(monkeypatch):
    """Kanboard a enregistré un autre auteur que l'identité vérifiée : le dire.

    C'est le dernier filet. Si un jour un proxy, une clef partagée ou un réglage
    Kanboard réattribue l'écriture, la réponse doit le porter plutôt que de
    laisser croire à une écriture « agent ».
    """
    _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 84, "username": "bot", "name": "agent"}),
        "createTask": _rpc_ok(32),
        "getTask": _rpc_ok({"id": 32, "creator_id": "4", "title": "t"}),
    })
    out = json.loads(await kanboard_create_task(project_id=262, title="t", as_user="agent"))
    assert out["creator_id"] == 4
    assert "84" in out["author_mismatch"] and "4" in out["author_mismatch"]


# ---------------------------------------------------------------------------
# 5. Diagnostic
# ---------------------------------------------------------------------------


async def test_list_accounts_ne_dit_plus_fallback_to_primary(monkeypatch):
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "")
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", "")
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 4, "username": "alice", "name": "Benjamin Jean"}),
    })
    raw = await kanboard_list_accounts()
    out = json.loads(raw)
    assert "fallback_to_primary" not in raw
    assert out["accounts"]["agent"]["status"] == "unconfigured"
    # La note doit annoncer le refus, pas la substitution.
    assert "refus" in out["accounts"]["agent"]["note"].lower()
    assert log  # le compte primary reste diagnostiqué


async def test_who_am_i_dit_si_l_identite_est_verifiee(monkeypatch):
    log = _wire(monkeypatch, {
        "getMe": _rpc_ok({"id": 84, "username": "bot", "name": "agent"}),
        "getUserByLoginName": _rpc_err(-32601, "Method not found"),
    })
    out = json.loads(await kanboard_who_am_i(as_user="agent"))
    assert out["effective_account"] == "agent"
    assert out["identity_verified"] is True
    assert out["resolved_user_id"] == 84
    assert log


async def test_who_am_i_agent_non_configure_ne_prete_pas_l_identite_primary(monkeypatch):
    monkeypatch.setattr(server, "KANBOARD_USER_ALT", "")
    monkeypatch.setattr(server, "KANBOARD_TOKEN_ALT", "")
    _wire(monkeypatch, {})
    raw = await kanboard_who_am_i(as_user="agent")
    out = json.loads(raw)
    assert out["identity_verified"] is False
    assert "alice" not in raw
    assert "KANBOARD_USER_ALT" in out["identity_error"]
