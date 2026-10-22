# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP generique pour Kanboard (API JSON-RPC).

Expose la gestion des taches Kanboard via le protocole MCP pour Claude Desktop.
API Kanboard : JSON-RPC 2.0 sur HTTP.

Auth : Basic Auth (username:token)
"""

import asyncio
import base64
import difflib
import html
import json
import logging
import os
import unicodedata
from contextvars import ContextVar
from datetime import datetime
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("kanboard-mcp")

mcp = FastMCP("kanboard")

SERVER_VERSION = "0.18.0"
_identity_lock = asyncio.Lock()

def _identity_refusal(account: str, reason: str, remedy: str = "") -> None:
    """Refus type, rendu en « Refus » et write_state = non_appliquee."""
    raise KanboardIdentityError(account, reason, remedy)

def _author_legacy(
    obj: Any, field: str, expected_uid: int | None, read_error: str = ""
) -> dict[str, Any]:
    """Rend les champs d'auteur historiques, sans relecture supplementaire.

    La 0.16 exposait `creator_id` a la racine d'une tache creee,
    `author_user_id_recorded` pour un commentaire, `author_read_error` quand la
    relecture echoue et `author_mismatch` quand l'auteur enregistre differe.
    Des appelants les lisent : les retirer est une rupture de contrat, et c'est
    ce que la contre-lecture du 12/10/2026 a trouve. Ils completent donc
    `_author_fields` au lieu de le remplacer.
    """
    raw = obj.get(field) if isinstance(obj, dict) else None
    valid = type(raw) is int or (
        isinstance(raw, str) and raw.isascii() and raw.isdecimal()
    )
    if not valid:
        detail = read_error or f"{field} illisible ou absent : {raw!r}"
        return {"author_read_error": detail}
    uid = int(raw)
    out: dict[str, Any] = {
        "author_user_id_recorded" if field == "user_id" else field: uid
    }
    if expected_uid and uid != expected_uid:
        out["author_mismatch"] = (
            f"Kanboard a enregistre l'auteur {uid} alors que l'identite verifiee "
            f"est {expected_uid} : relire l'objet avant de s'appuyer sur cette ecriture."
        )
    return out

def _identity_key(account: str) -> tuple[str, str, str]:
    user, token, _ = _get_credentials(account, for_write=True)
    return KANBOARD_URL, user, token

async def _ensure_write_identity(as_user: str) -> dict[str, Any]:
    """Prouve l'identite par getMe avant ecriture, sans resolution par annuaire.

    Le cache est lie a l'URL et aux identifiants, en memoire seulement. Le
    verrou evite plusieurs sondes lors des premieres ecritures concurrentes.
    Agent et primary doivent avoir des logins ET des UID distincts. L'identite
    primary est donc aussi etablie, meme si aucune lecture ne l'a precedee.
    Aucun echec de verification n'est mis en cache.
    """
    user, _, account = _get_credentials(as_user, for_write=True)
    if account == "agent" and user.strip().casefold() == KANBOARD_USER.strip().casefold():
        _identity_refusal("agent", "les logins agent et primary sont identiques.")

    async def probe(which: str, *, required: bool) -> dict[str, Any] | None:
        """Rend l'identite prouvee, ou None quand elle n'est pas exigee.

        `required` est vrai pour une ecriture agent : c'est le cas que la garde
        existe pour couvrir. Il est faux pour primary, qui gardait en 0.16 le
        droit d'ecrire en declarant la verification manquante, et faux pour le
        sondage de primary qui ne sert qu'a exclure un UID partage. Un login
        different est toujours un refus, meme si la sonde est facultative.
        """
        key = _identity_key(which)
        if key in _identity_cache:
            return _identity_cache[key]
        try:
            me = await kb_call("getMe", as_user=which)
        except Exception as exc:
            if required:
                _identity_refusal(
                    which, "getMe indisponible, identite non etablie.",
                    "Verifier le token personnel du compte.",
                )
            logger.warning("Identite %s non verifiee : getMe indisponible (%s)", which, exc)
            return None
        raw_uid = me.get("id") if isinstance(me, dict) else None
        username = me.get("username") if isinstance(me, dict) else None
        display = me.get("name") if isinstance(me, dict) else None
        valid_uid = type(raw_uid) is int or (
            isinstance(raw_uid, str) and raw_uid.isascii() and raw_uid.isdecimal()
        )
        uid = int(raw_uid) if valid_uid else 0
        if uid <= 0 or not isinstance(username, str) or not username.strip():
            if required:
                _identity_refusal(
                    which, "identite incoherente ou absente dans getMe.",
                    "Verifier le login et le token personnel du compte.",
                )
            logger.warning("Identite %s non verifiee : getMe incoherent", which)
            return None
        if username.strip().casefold() != key[1].strip().casefold():
            _identity_refusal(
                which, "identite incoherente : le login rendu par getMe differe du login configure.",
                "Verifier le login et le token personnel du compte.",
            )
        # Le nom d'affichage de la 0.16 vient de getMe.name ; le login ne le
        # remplace pas, sinon list_accounts.resolved_name change apres ecriture.
        return {
            "user_id": uid, "username": username,
            "name": display if isinstance(display, str) and display.strip() else username,
        }

    async with _identity_lock:
        identity = await probe(account, required=(account == "agent"))
        if identity is None:
            # Ecriture primary dont getMe ne repond pas : comportement 0.16,
            # l'ecriture passe et la verification manquante est dite.
            return {
                "user_id": 0, "username": user, "name": user,
                "verified": False,
                "note": "Identite primary non verifiee : getMe indisponible.",
            }
        if account == "agent":
            primary = await probe("primary", required=False)
            if primary is None:
                logger.warning(
                    "UID primary inconnu : la comparaison avec l'agent n'a pas pu etre faite.")
            elif identity["user_id"] == primary["user_id"]:
                _identity_refusal("agent", "agent et primary ont le meme user_id.")
            else:
                _identity_cache[_identity_key("primary")] = primary
        _identity_cache[_identity_key(account)] = identity
        # Une ancienne resolution par login n'est pas une preuve du porteur du token.
        _my_id_cache[account] = identity["user_id"]
        # Ce cache sert au rendu, comme celui de _get_my_user_id. L'identite
        # conserve le nom brut pour ne jamais decoder deux fois apres ecriture.
        _my_name_cache[account] = _text(identity["name"])
        return identity

def _author_fields(obj: Any, field: str, expected_uid: int | None) -> dict[str, Any]:
    """Ne rend un auteur que s'il a ete relu sur l'objet cree."""
    raw = obj.get(field) if isinstance(obj, dict) else None
    valid_uid = type(raw) is int or (
        isinstance(raw, str) and raw.isascii() and raw.isdecimal()
    )
    uid = int(raw) if valid_uid else None
    result: dict[str, Any] = {
        "author_user_id": uid,
        "author_verified": uid is not None and uid == expected_uid,
    }
    if not result["author_verified"]:
        result["author_warning"] = (
            "Auteur enregistre non relu ; aucune identite supposee."
            if uid is None else "L'auteur enregistre differe de l'identite verifiee avant ecriture."
        )
    return result

def _creation_without_author(as_user: str) -> dict[str, Any]:
    """Ces objets ne portent pas de champ auteur ; un assigne n'est pas un auteur."""
    return {
        "as_user": _get_credentials(as_user, for_write=True)[2],
        "author_user_id": None,
        "author_verified": False,
        "author_verification_method": "not_exposed",
    }

async def _verify_task_state(
    task_id: int,
    expected: dict[str, Any],
    as_user: str = "",
    fields: tuple[str, ...] = ("id", "title", "column_id", "swimlane_id", "is_active", "project_id"),
) -> tuple[bool, dict[str, Any], list[str], str]:
    """Relit une tâche et compare son état aux valeurs attendues.

    Retourne (verified, post_state, mismatch, method). La comparaison se fait
    sur la représentation str des valeurs : Kanboard renvoie ses entiers tantôt
    en int tantôt en string selon la méthode appelée, comparer brut produirait
    de faux écarts.

    Une relecture impossible (tâche introuvable, API en erreur) donne
    verified=False avec la raison dans `method` : on ne renvoie jamais un
    verified=True par défaut.
    """
    try:
        task = await kb_call("getTask", {"task_id": task_id}, as_user=as_user)
    except Exception as exc:  # relecture impossible : on le dit, on ne suppose rien
        logger.warning("verified_write : relecture de la tache %s impossible : %s", task_id, exc)
        return False, {}, [], f"relecture impossible ({exc})"

    if not task:
        logger.warning("verified_write : tache %s introuvable apres ecriture", task_id)
        return False, {}, [], "relecture impossible (tache introuvable)"

    post_state = {k: task.get(k) for k in fields if k in task}
    mismatch = [
        key for key, want in expected.items()
        if str(task.get(key)) != str(want)
    ]
    if mismatch:
        logger.warning(
            "verified_write : ecart apres ecriture sur la tache %s - attendu %s, relu %s",
            task_id,
            {k: expected[k] for k in mismatch},
            {k: task.get(k) for k in mismatch},
        )
    return not mismatch, post_state, mismatch, "getTask"

def _verified_payload(
    base: dict[str, Any],
    verified: bool,
    post_state: dict[str, Any],
    mismatch: list[str],
    method: str,
) -> str:
    """Assemble la réponse d'une écriture vérifiée."""
    payload = dict(base)
    payload["verified"] = verified
    payload["verification_method"] = method
    if post_state:
        payload["post_state"] = post_state
    if mismatch:
        payload["mismatch"] = mismatch
        payload["warning"] = (
            "L'API a repondu succes mais la relecture ne confirme pas l'etat attendu "
            f"sur : {', '.join(mismatch)}. Verifier avant de considerer l'operation faite."
        )
    elif not verified:
        payload["warning"] = (
            "L'API a repondu succes mais l'etat n'a pas pu etre relu : "
            "le succes n'est pas confirme."
        )
    return _dumps(payload)

KANBOARD_URL = os.environ.get("KANBOARD_URL", "")

# --- Comptes Kanboard (multi-compte) ---------------------------------------
# Le serveur supporte 2 jeux de credentials simultanement :
#   - "primary" : compte humain principal - defaut pour la lecture
#   - "agent"   : compte dedie a l'assistant IA - defaut pour les ecritures
# Si KANBOARD_USER_ALT/KANBOARD_TOKEN_ALT sont vides, "agent" est REFUSE (0.16.0) :
# il retombait sur "primary" jusqu'a la 0.15.0, ce qui enregistrait sous le compte
# humain des ecritures annoncees comme celles de l'assistant. Une configuration a un
# seul compte reste valide : elle s'utilise avec as_user="primary" ou sans as_user.
KANBOARD_USER = os.environ.get("KANBOARD_USER", "jsonrpc")
KANBOARD_TOKEN = os.environ.get("KANBOARD_TOKEN", "")
KANBOARD_USER_ALT = os.environ.get("KANBOARD_USER_ALT", "")
KANBOARD_TOKEN_ALT = os.environ.get("KANBOARD_TOKEN_ALT", "")

# Default lorsque as_user n'est pas precise.
# Valeurs autorisees : "primary" | "agent"
KANBOARD_DEFAULT_AS_USER = (os.environ.get("KANBOARD_DEFAULT_AS_USER", "primary")
                             .strip().lower() or "primary")

# Limite par defaut pour les listes (garder petit pour economiser les tokens)
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Apercu des commentaires dans kanboard_get_task. Au-dela, la coupure est
# signalee (comment_truncated / comment_full_length) : voir kanboard_list_comments
# pour le contenu integral.
COMMENT_PREVIEW_CHARS = 500

# Plafond d'activite impose par l'API Kanboard, non configurable : la procedure
# getProjectActivity ne prend que project_id (tout parametre `limit` ressort en
# -32602 "Too many arguments"), et le modele coupe a 50 evenements. Le plafond
# est GLOBAL, pas par projet : getProjectActivities sur plusieurs projets rend
# 50 evenements au total, un projet peu actif pouvant n'en avoir aucun.
# Verifie sur Kanboard 1.2.53.
ACTIVITY_API_CAP = 50

# Retry
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # secondes, double à chaque tentative

_request_id = 0

# Caches de resolution d'IDs (evite N appels API par requete) - clefs par compte
_user_cache: dict[str, dict[int, str]] = {"primary": {}, "agent": {}}
_my_id_cache: dict[str, int] = {}              # as_user -> user_id resolu
_my_name_cache: dict[str, str] = {}            # as_user -> display name
# Identite etablie par getMe avant le premier appel d'un compte, puis gardee.
# (URL, login, token) -> identite verifiee ; cache en memoire uniquement.
_identity_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
_column_cache: dict[int, dict[int, str]] = {}   # project_id -> {col_id -> name}
_swimlane_cache: dict[int, dict[int, str]] = {}  # project_id -> {sw_id -> name}


def _resolve_account(as_user: str = "") -> str:
    """Normalise l'argument as_user en 'primary' ou 'agent'.

    Vide ou "default" -> defaut configure (KANBOARD_DEFAULT_AS_USER).
    Valeur non vide inconnue -> ValueError : pas de fallback silencieux sur
    "primary", qui signerait avec le compte humain une ecriture destinee au
    compte agent (tracabilite faussee).
    """
    val = (as_user or "").strip().lower()
    if val in ("primary", "agent"):
        return val
    if val in ("", "default"):
        return KANBOARD_DEFAULT_AS_USER if KANBOARD_DEFAULT_AS_USER in ("primary", "agent") else "primary"
    # Aliases pratiques
    if val in ("me", "human", "user"):
        return "primary"
    if val in ("bot", "ai", "alt", "claude"):
        return "agent"
    raise ValueError(
        f"as_user invalide : '{as_user}'. Valeurs acceptees : 'primary' "
        "(ou me/human/user), 'agent' (ou bot/ai/alt/claude), '' ou 'default' "
        "pour le compte par defaut."
    )


def _get_credentials(as_user: str, *, for_write: bool = False) -> tuple[str, str, str]:
    """Retourne (user, token, effective_account) pour le compte demande.

    Le repli sur primary est reserve aux lectures. Une ecriture agent exige
    ses propres identifiants, meme lorsque le defaut configure vaut agent.
    """
    account = _resolve_account(as_user)
    if account == "agent":
        if KANBOARD_USER_ALT and KANBOARD_TOKEN_ALT:
            return KANBOARD_USER_ALT, KANBOARD_TOKEN_ALT, "agent"
        if for_write:
            _identity_refusal(
                "agent",
                "KANBOARD_USER_ALT et KANBOARD_TOKEN_ALT ne designent pas le "
                "compte personnel de l'agent.",
                "Aucun repli sur primary : renseigner ces deux variables.",
            )
        # Compatibilite en lecture uniquement.
        return KANBOARD_USER, KANBOARD_TOKEN, "primary"
    return KANBOARD_USER, KANBOARD_TOKEN, "primary"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Sans message, les exceptions httpx masquaient jusqu'à la famille du défaut :
# une connexion coupée et un objet absent se lisaient tous deux « Erreur : ».
_TRANSPORT_DEFAULT_DETAIL = {
    "ReadError": "connexion coupée par le serveur avant la réponse",
    "ReadTimeout": "délai de lecture dépassé",
    "WriteTimeout": "délai d'écriture dépassé",
    "ConnectTimeout": "délai de connexion dépassé",
    "ConnectError": "connexion impossible",
    "PoolTimeout": "délai d'attente d'une connexion dépassé",
    "RemoteProtocolError": "réponse HTTP mal formée ou interrompue",
    "WriteError": "connexion coupée pendant l'envoi de la requête",
    "CloseError": "échec de fermeture de la connexion",
    "LocalProtocolError": "requête HTTP mal formée",
    "ProxyError": "échec de connexion au proxy",
    "UnsupportedProtocol": "protocole HTTP non pris en charge",
}

_JSONRPC_ERROR_DETAIL = {
    -32700: "JSON de la requête invalide",
    -32600: "requête JSON-RPC invalide",
    -32601: "méthode introuvable",
    -32602: "paramètres invalides",
    -32603: "erreur interne Kanboard",
}
_JSONRPC_REJECTED_CODES = {-32700, -32600, -32601, -32602}

# MCP lance chaque appel d'outil dans une tâche distincte. Une tâche enfant
# hérite du contexte : l'identité de la tâche évite qu'elle hérite aussi des
# écritures du parent et les attribue à tort à son propre appel.
_applied_writes: ContextVar[tuple[asyncio.Task[Any], list[str]] | None] = ContextVar(
    "kanboard_applied_writes", default=None,
)


def _write_journal() -> list[str]:
    """Journal propre à l'appel d'outil, vide hors d'une tâche asyncio."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return []
    if task is None:
        return []
    journal = _applied_writes.get()
    if journal is None or journal[0] is not task:
        journal = (task, [])
        _applied_writes.set(journal)
    return journal[1]


def _is_mutating_method(method: str) -> bool:
    """Les RPC get/search sont des lectures ; une methode inconnue est protegee."""
    return not method.startswith(("get", "search"))


def _append_sentence(base: str, extra: str) -> str:
    """Enchaine deux phrases sans les souder : un detail Kanboard finit rarement
    par un point, et « Internal error Etat de l'ecriture » se lit mal."""
    if not extra:
        return base
    base = base.rstrip()
    if base and base[-1] not in ".!?;:":
        base += "."
    return base + extra


def _write_state_message(write_state: str, method: str) -> str:
    """Précise le risque de rejouer la requête qui vient d'échouer."""
    if write_state == "non_appliquee":
        return (f" Écriture {method} non appliquée : rejouer cette requête est sans "
                "risque de doublon.")
    if write_state == "inconnu":
        return (f" État de l'écriture {method} inconnu : écriture non rejouée "
                "automatiquement, Kanboard a pu la traiter avant de perdre la réponse ; "
                "relire l'objet avant de rejouer.")
    return ""


class KanboardAPIError(RuntimeError):
    """Erreur JSON-RPC ou réponse illisible, avec l'état de l'écriture concernée."""

    def __init__(self, code: int | None, message: str, method: str,
                 body_excerpt: str | None = None):
        self.code = code
        self.method = method
        self.body_excerpt = body_excerpt
        self.write_state = (
            "sans_objet" if not _is_mutating_method(method)
            else "non_appliquee" if code in _JSONRPC_REJECTED_CODES else "inconnu"
        )
        label = _JSONRPC_ERROR_DETAIL.get(code, "erreur JSON-RPC")
        self.detail = message.strip() or (
            "sans message de Kanboard" if code is not None else "sans message")
        code_label = f" {code} ({label})" if code is not None else ""
        # Les replis de permission lisent encore le texte : conserver le préfixe
        # et le message Kanboard, notamment ses marqueurs « Forbidden ».
        super().__init__(_append_sentence(
            f"Kanboard{code_label} sur {method} : {self.detail}",
            _write_state_message(self.write_state, method),
        ))


class KanboardIdentityError(RuntimeError):
    """L'identité du compte demandé n'est pas établie : aucun appel n'est émis.

    Cette erreur remplace un repli. Une identité invalide n'est pas un
    désagrément de configuration : c'est une traçabilité fausse, et une écriture
    attribuée à quelqu'un d'autre ne se rattrape pas après coup. L'appel est donc
    refusé avant d'atteindre le réseau, et `write_state` vaut « non appliquée » :
    rejouer après correction est sans risque de doublon.
    """

    def __init__(self, account: str, reason: str, remedy: str = ""):
        self.account = account
        self.reason = reason
        self.write_state = "non_appliquee"
        super().__init__(_append_sentence(
            f"Identité du compte '{account}' non établie : {reason}",
            (" " + remedy) if remedy else "",
        ))


class KanboardTransportError(RuntimeError):
    """Échec réseau définitif, nommé même quand l'exception httpx est vide."""

    def __init__(self, kind: str, method: str, attempts: int,
                 replayable: bool, detail: str = ""):
        self.kind = kind
        self.method = method
        self.attempts = attempts
        self.replayable = replayable
        self.detail = detail.strip() or _TRANSPORT_DEFAULT_DETAIL.get(
            kind, "échec du transport HTTP",
        )
        self.write_state = (
            "sans_objet" if not _is_mutating_method(method)
            else "non_appliquee" if replayable else "inconnu"
        )
        tail = _write_state_message(self.write_state, method)
        if self.write_state == "sans_objet":
            tail = (" Rejouer l'appel plus tard, et vérifier la disponibilité de "
                    "Kanboard si l'erreur persiste.")
        count = "tentative" if attempts == 1 else "tentatives"
        super().__init__(
            f"{kind} sur {method} ({self.detail}) après {attempts} {count}.{tail}"
        )


def _parse_json_or_raise(resp: httpx.Response, method: str) -> Any:
    """Une page de proxy en HTTP 200 doit rester diagnostiquable et rejouable en lecture."""
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        body = resp.text or ""
        ct = resp.headers.get("content-type", "?")
        excerpt = body[:200] if body else "(corps vide)"
        logger.error(
            "Réponse non-JSON pour RPC %s : HTTP=%d content-type=%s body_len=%d "
            "first_bytes=%r", method, resp.status_code, ct, len(body), excerpt,
        )
        raise KanboardAPIError(
            None,
            f"Réponse non-JSON depuis Kanboard (HTTP {resp.status_code}, "
            f"content-type={ct}, body={len(body)}o). Extrait : {excerpt!r}",
            method, body_excerpt=excerpt,
        ) from exc


async def _recorded_author(
    read_method: str,
    params: dict,
    field: str,
    expected_uid: int,
    account: str,
) -> tuple[dict[str, Any], str]:
    """Relit l'auteur qu'un objet créé porte réellement côté Kanboard.

    Une écriture acceptée ne prouve pas sous quel nom elle a été enregistrée : le
    16/09/2026, la réponse annonçait l'auteur qu'on croyait écrire. Cette
    relecture est le dernier filet, et le seul qui ne repose pas sur nos propres
    suppositions.

    Rend (champs à fusionner dans la réponse, message d'écart éventuel). Une
    relecture impossible est dite (`author_read_error`) plutôt que silencieuse :
    elle n'infirme pas l'écriture, elle laisse seulement la preuve manquante.
    """
    try:
        obj = await kb_call(read_method, params, as_user=account)
    except Exception as exc:  # noqa: BLE001 - l'écriture, elle, a réussi
        return {"author_read_error": str(exc)}, ""
    if not isinstance(obj, dict) or obj.get(field) in (None, ""):
        return {"author_read_error": f"{read_method} n'a pas rendu {field}"}, ""
    try:
        recorded = int(obj.get(field) or 0)
    except (TypeError, ValueError):
        return {"author_read_error": f"{field} illisible : {obj.get(field)!r}"}, ""
    out = {f"{field}_recorded" if field != "creator_id" else field: recorded}
    if field == "user_id":
        out = {"author_user_id_recorded": recorded}
    mismatch = ""
    if expected_uid > 0 and recorded != expected_uid:
        mismatch = (f"Kanboard a enregistre l'auteur {recorded} alors que l'identite "
                    f"verifiee du compte '{account}' est {expected_uid} : relire "
                    "l'objet avant de s'appuyer sur cette ecriture.")
    return out, mismatch


async def _ensure_account_identity(account: str) -> dict[str, Any]:
    """Diagnostic compatible avec la garde d'ecriture de la 0.17."""
    try:
        identity = await _ensure_write_identity(account)
    except ValueError as exc:
        raise KanboardIdentityError(account, str(exc), "Verifier les tokens personnels.") from exc
    return {**identity, "verified": identity.get("verified", True), "fatal": False}


async def kb_call(method: str, params: dict | None = None, as_user: str = "") -> Any:
    """Appel JSON-RPC 2.0 à l'API Kanboard avec rejeu et backoff exponentiel.

    Paramètres :
    - method : nom de la méthode JSON-RPC Kanboard
    - params : paramètres de la méthode (dict)
    - as_user : compte à utiliser ("primary" | "agent" | "" pour défaut)

    Les lectures sont rejouées après toute erreur de transport, un HTTP 5xx ou
    un corps non-JSON. Une écriture n'est rejouée que si la connexion n'a jamais
    abouti (ConnectError, ConnectTimeout). Aucun rejeu sur les erreurs HTTP 4xx
    ou les refus JSON-RPC.

    Limite : une écriture partie dont la réponse est perdue laisse un état
    indécidable côté client. Il faut relire l'objet avant de rejouer ; le
    journal rappelle aussi les écritures déjà réussies dans cet appel d'outil.

    Avant la PREMIÈRE écriture d'un compte dans le processus, l'identité du token
    est sondée par `getMe` (cf. `_ensure_write_identity()`). Une identité agent
    non établie interdit l'écriture, sans repli. Primary peut écrire si sa sonde
    est indisponible, sans imposer d'auteur à la création ; un login différent
    reste un refus. Les lectures ne déclenchent pas cette vérification, elles
    gardent le repli vers primary si les identifiants agent manquent.
    """
    journal = _write_journal()
    mutating = _is_mutating_method(method)
    user, token, account = _get_credentials(as_user, for_write=mutating)
    if mutating:
        # La garde refuse une identité incohérente avant la requête d'écriture.
        identity = await _ensure_write_identity(as_user)
        author_field = {"createTask": "creator_id", "createComment": "user_id"}.get(method)
        if author_field:
            params = dict(params or {})
            if identity.get("verified", True) and identity["user_id"] > 0:
                params[author_field] = identity["user_id"]
            else:
                # Primary sans getMe : Kanboard attribue l'auteur, sans zéro
                # ni identifiant supposé fourni par un cache ou un appelant.
                params.pop(author_field, None)
    global _request_id
    _request_id += 1
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": _request_id}
    if params:
        payload["params"] = params

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=True, timeout=30) as client:
                # Seules les clefs en INFO : les valeurs portent titres, descriptions
                # et commentaires de cartes, qui n'ont rien a faire dans un log courant.
                logger.info("RPC %s as=%s param_keys=%s (attempt %d)",
                            method, account, sorted(params or {}), attempt + 1)
                logger.debug("RPC %s as=%s params=%s", method, account, params)
                resp = await client.post(
                    KANBOARD_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Basic {auth}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = _parse_json_or_raise(resp, method)

            if "error" in data and data["error"] is not None:
                err = data["error"]
                code = err.get("code") if isinstance(err, dict) else None
                msg = str(err.get("message") or "") if isinstance(err, dict) else str(err)
                raise KanboardAPIError(code, msg, method)
            result = data.get("result")
            # Kanboard signale aussi l'échec d'une écriture par un résultat faux,
            # sans erreur JSON-RPC : ne pas l'annoncer comme déjà appliquée.
            if mutating and result:
                journal.append(method)
            return result

        except httpx.TransportError as exc:
            # Seul un échec de connexion prouve que Kanboard n'a rien reçu.
            # Après l'envoi, rejouer une écriture peut créer un doublon.
            never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            replayable = never_sent or not mutating
            last_exc = exc
            if replayable and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry RPC %s dans %.1fs (%s : %s)", method, delay,
                               type(exc).__name__, exc)
                await asyncio.sleep(delay)
            else:
                raise KanboardTransportError(
                    type(exc).__name__, method, attempt + 1, replayable, str(exc),
                ) from exc
        except httpx.HTTPStatusError as exc:
            # Les outils inspectent le statut HTTP : garder l'exception httpx
            # d'origine tout en donnant au formateur le contexte de l'écriture.
            exc.kb_method = method
            exc.kb_mutating = mutating
            if not mutating and exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry RPC %s (HTTP %d) dans %.1fs", method, exc.response.status_code, delay)
                await asyncio.sleep(delay)
            else:
                raise
        except KanboardAPIError as exc:
            # Un corps illisible peut venir d'un proxy. Un refus JSON-RPC, lui,
            # ne se résout pas par le rejeu automatique des mêmes paramètres.
            last_exc = exc
            if exc.body_excerpt is not None and not mutating and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry RPC %s (corps non-JSON) dans %.1fs", method, delay)
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


def _format_error(exc: Exception) -> str:
    """Nomme le défaut, l'état de l'écriture et les écritures déjà appliquées.

    Une exception vide garde son type et un libellé : l'appelant doit pouvoir
    distinguer une défaillance réseau d'un refus et décider quoi relire.
    """
    if isinstance(exc, KanboardIdentityError):
        # Pas de « Erreur réseau » ni de conseil de rejeu : rien n'est parti, et
        # rejouer à l'identique redonnera le même refus jusqu'à correction.
        message = f"Refus : {exc}"
    elif isinstance(exc, KanboardTransportError):
        message = f"Erreur réseau {exc}"
    elif isinstance(exc, KanboardAPIError):
        message = f"Erreur {exc}"
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            message = "Erreur 401 : authentification Kanboard invalide."
        elif status == 403:
            message = "Erreur 403 : permissions insuffisantes."
        elif status == 404:
            message = "Erreur 404 : endpoint Kanboard introuvable. Verifier KANBOARD_URL."
        else:
            detail = exc.response.text[:500].strip() or "sans message."
            message = f"Erreur HTTP {status} : {detail}"
        if getattr(exc, "kb_mutating", False):
            method_name = getattr(exc, "kb_method", "inconnue")
            if status >= 500:
                message = _append_sentence(
                    message, _write_state_message("inconnu", method_name))
            elif status >= 400 and status not in (401, 403, 404):
                # 401, 403 et 404 gardent leur message exact : une auth refusee
                # n'ecrit rien et n'a pas besoin qu'on en discute. Le reste
                # (413 sur un commentaire trop long, 400 d'un proxy) est refuse
                # avant execution : le dire evite une relecture inutile, et un
                # rejeu a l'identique echouerait pareil.
                message = _append_sentence(
                    message,
                    f" Écriture {method_name} non appliquée : la requête a été "
                    "refusée avant exécution, corriger l'appel avant de rejouer.")
    elif isinstance(exc, httpx.TransportError):
        kind = type(exc).__name__
        detail = str(exc).strip() or _TRANSPORT_DEFAULT_DETAIL.get(kind, "échec du transport HTTP")
        message = f"Erreur réseau {kind} : {detail}."
    else:
        text = str(exc)
        message = f"Erreur : {text}" if text.strip() else f"Erreur {type(exc).__name__} : sans message."
    journal = _write_journal()
    if journal:
        message = _append_sentence(
            message,
            f" Écritures déjà appliquées dans cet appel d'outil : {', '.join(journal)}. "
            "Relire les objets concernés avant de rejouer l'appel d'outil complet.")
    return message


# Marqueurs textuels d'un refus Kanboard. Un refus ne remonte pas toujours un
# code HTTP : selon la procedure, il ressort en erreur JSON-RPC applicative.
_PERMISSION_MARKERS = (
    "forbidden", "not authorized", "unauthorized",
    "permission", "access denied", "denied",
)


def _is_permission_error(exc: Exception) -> bool:
    """Vrai si l'exception ressemble a un refus de permission Kanboard."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return True
    return any(marker in str(exc).lower() for marker in _PERMISSION_MARKERS)


async def _projects_for_account(as_user: str = "") -> tuple[list[dict], str]:
    """Retourne (projets, portee) pour un compte, sans exiger les droits admin.

    `getAllProjects` est reserve aux administrateurs : un compte agent non-admin
    y prend un 403 sec, qui cassait my_dashboard et my_overdue des qu'on passait
    as_user='agent'. On retombe alors sur `getMyProjects` (projets dont le compte
    est membre).

    La portee retournee ("all" ou "member") n'est pas cosmetique : "member" est
    un sous-ensemble, et un appelant qui le prendrait pour l'inventaire complet
    conclurait a tort a l'absence d'un projet.
    """
    try:
        projects = await kb_call("getAllProjects", as_user=as_user)
        return (projects or []), "all"
    except Exception as exc:
        if not _is_permission_error(exc):
            raise
        projects = await kb_call("getMyProjects", as_user=as_user)
        return (projects or []), "member"


def _dumps(data: Any) -> str:
    """Serialise en JSON compact avec support UTF-8."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _clamp_limit(limit: int) -> int:
    """Borne la limite entre 1 et MAX_LIMIT."""
    return max(1, min(limit, MAX_LIMIT))


LABEL_FIELDS = ("name", "title")


def _label_key(value: Any) -> str:
    """Cle de comparaison d'un libelle Kanboard (colonne, swimlane, categorie...).

    Un libelle arrive rarement tel qu'il est stocke : il a souvent transite par
    une couche HTML (compte rendu, page recopiee, export), qui rend "Missions &
    Hermine" sous la forme "Missions &amp; Hermine" et l'espace insecable sous
    la forme "&nbsp;". Compare brut, ce libelle ne se rapproche de rien, et
    l'appelant relit une liste ou il ne voit aucun ecart.

    Le repli applique, dans cet ordre :
    - decodage des entites HTML (`&amp;`, `&nbsp;`, `&#39;`...) ;
    - normalisation Unicode NFC (un "e" + accent combinant se lit comme "e") ;
    - reduction de toute suite d'espaces, insecables compris, a une espace, et
      suppression des espaces de bord ;
    - repli de casse.

    Ce qu'il ne fait PAS : effacer les accents. "Reunion" ne resout pas
    "Réunion" - deux libelles distincts resteraient distincts. L'ecart est
    seulement signale dans le message d'erreur.
    """
    text = html.unescape(str(value or ""))
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split()).casefold()


def _fold(value: Any) -> str:
    """Replie une chaine en minuscules sans accents, pour comparaison souple.

    Les noms de projets Kanboard sont saisis a la main ("Pilotage",
    "Référentiel…") : sans repli, un filtre tape sans accent ne trouverait rien.

    Repose sur `_label_key` (entites HTML, espaces, casse) puis efface les
    accents : c'est le repli le plus large du connecteur. Il sert aux filtres
    de sous-chaine et a la suggestion de valeur proche, jamais a decider qu'un
    libelle en designe un autre.
    """
    text = unicodedata.normalize("NFKD", _label_key(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _row_label(row: dict, fields: tuple[str, ...] = LABEL_FIELDS) -> str:
    """Libelle affichable d'une ligne Kanboard (`name` ou `title` selon l'objet)."""
    for field in fields:
        value = row.get(field)
        if value:
            return str(value)
    return ""


def _closest_hint(name: str, available: "list[str]") -> str:
    """Phrase designant la valeur disponible la plus proche, ou "" si aucune.

    Enumerer les valeurs ne suffit pas quand l'ecart tient a un caractere
    invisible a la lecture : "Missions &amp; Hermine" et "Missions & Hermine"
    se juxtaposent sans que rien ne saute aux yeux. La suggestion nomme la
    valeur candidate et, quand c'est le cas, dit que l'ecart se limite aux
    accents, a la casse ou a des variantes typographiques.

    Seule la valeur candidate interpolee est decodee pour affichage.
    Les cles de comparaison ne changent pas.
    """
    candidates = [v for v in available if v]
    if not candidates:
        return ""
    wanted = _label_key(name)
    folded = _fold(wanted)
    for value in candidates:
        if _fold(_label_key(value)) == folded:
            return (f" Valeur la plus proche : '{_text(value)}' : l'ecart ne porte que sur "
                    "les accents, la casse ou des variantes typographiques.")
    keys = {_label_key(v): v for v in candidates}
    close = difflib.get_close_matches(wanted, list(keys), n=1, cutoff=0.6)
    if close:
        return f" Valeur la plus proche : '{_text(keys[close[0]])}'."
    return ""


def _resolve_label(
    rows: "list[dict] | None",
    name: str,
    label: str,
    fields: tuple[str, ...] = LABEL_FIELDS,
) -> "tuple[dict | None, str]":
    """Rapproche un libelle fourni par l'appelant d'une des lignes Kanboard.

    Retourne (ligne, "") si le rapprochement est certain, (None, message) sinon.

    Trois passes, dans cet ordre :
    1. la comparaison historique (casse et espaces de bord ignores), pour que
       tout ce qui resolvait hier resolve encore, sur la meme ligne ;
    2. le repli complet de `_label_key` ;
    3. echec - le message enumere les valeurs disponibles et designe la plus
       proche.

    Une ligne exacte l'emporte donc sur une ligne qui ne correspond qu'apres
    repli. Si le repli seul fait correspondre plusieurs lignes, la resolution
    est refusee plutot qu'arbitrale : choisir a la place de l'appelant
    ecrirait dans un swimlane qu'il n'a pas demande.

    Les valeurs disponibles et la suggestion la plus proche sont decodees une
    fois pour l'affichage : une valeur decodee resout de toute facon, puisque
    la comparaison passe par `_label_key`, qui decode elle aussi.

    La liste d'AMBIGUITE, elle, reste brute, et c'est delibere : elle enumere
    des libelles que le repli confond justement. "Missions &amp; Hermine" et
    "Missions  &  Hermine" sont deux swimlanes distincts qui se decodent sur la
    meme chaine ; les decoder ici afficherait deux fois la meme valeur et
    priverait l'appelant du seul moyen de les distinguer.

    La valeur saisie, les lignes retournees et les comparaisons ne sont pas
    modifiees.
    """
    rows = list(rows or [])
    legacy = str(name).lower().strip()
    for row in rows:
        if _row_label(row, fields).lower().strip() == legacy:
            return row, ""

    wanted = _label_key(name)
    matches = [r for r in rows if wanted and _label_key(_row_label(r, fields)) == wanted]
    if len(matches) == 1:
        return matches[0], ""

    available = [_row_label(r, fields) for r in rows]
    if len(matches) > 1:
        # Libelles bruts : c'est l'encodage qui distingue ces lignes, le
        # decodage les rendrait identiques a l'ecran (voir la docstring).
        return None, (
            f"{label} '{name}' est ambigu : {[_row_label(r, fields) for r in matches]} "
            "se ressemblent une fois les entites HTML, les espaces et la casse "
            "replies. Preciser l'identifiant."
        )
    return None, (
        f"{label} '{name}' introuvable. Valeurs disponibles : {[_text(v) for v in available]}."
        f"{_closest_hint(name, available)}"
    )


def _ts_to_date(value: Any) -> str:
    """Convertit un timestamp Unix (string ou int) en date ISO. Retourne '' si vide."""
    if not value or value == "0":
        return ""
    try:
        ts = int(value)
        if ts > 0:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        pass
    return str(value)


def _ts_to_datetime(value: Any) -> str:
    """Convertit un timestamp Unix en datetime ISO. Retourne '' si vide."""
    if not value or value == "0":
        return ""
    try:
        ts = int(value)
        if ts > 0:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        pass
    return str(value)


def _text(value: Any) -> str:
    """Rend un texte Kanboard tel qu'il se lit, entites HTML decodees.

    Kanboard stocke le texte deja echappe : la carte 7970 du projet 262 a pour
    titre stocke "[Alice &amp; Bob] Proposer une intervention au ticket". Rendu
    brut, ce titre recopie dans un livrable en sort avec l'entite.

    Le decodage est applique UNE fois, jamais deux : "&amp;amp;" devient
    "&amp;", pas "&". Decoder davantage inventerait une donnee que Kanboard ne
    stocke pas.

    A n'appliquer QUE sur les chemins de lecture. Une valeur relue pour etre
    renvoyee a Kanboard (kanboard_update_swimlane, kanboard_update_category)
    doit rester brute, sans quoi la mise a jour reecrirait le libelle stocke.
    """
    return html.unescape(str(value or ""))


def _is_active_flag(value: Any) -> int | None:
    """Etat d'une carte : 1 ouverte, 0 fermee, None si Kanboard n'a rien dit.

    Kanboard rend tantot l'entier, tantot la chaine. "0" etant vrai en Python,
    une carte fermee relue sans conversion se lit ouverte.

    None n'est pas un repli de confort : il dit que le champ manque, et aucun
    filtre de statut ne peut alors ecarter la carte sans risquer de la perdre.
    """
    if value is None or value == "":
        return None
    try:
        return 1 if int(value) else 0
    except (TypeError, ValueError):
        return None


def _to_timestamp(value: Any) -> int | None:
    """Horodatage Unix exploitable, ou None (champ vide, nul ou illisible)."""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def _parse_date_bound(value: str, param: str) -> tuple[int | None, str | None]:
    """Convertit une borne de date en horodatage local. Retourne (ts, erreur).

    Accepte AAAA-MM-JJ, AAAA-MM-JJ HH:MM et AAAA-MM-JJTHH:MM. Une valeur vide
    vaut « pas de borne ». La borne s'entend dans le fuseau du serveur MCP,
    comme toutes les dates rendues par le connecteur.
    """
    text = (value or "").strip()
    if not text:
        return None, None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp()), None
        except ValueError:
            continue
    return None, (
        f"Erreur : {param} illisible ('{value}'). Format attendu : AAAA-MM-JJ, "
        "ou AAAA-MM-JJ HH:MM pour une borne a l'heure pres."
    )


async def _get_user_name(user_id: Any, as_user: str = "") -> str:
    """Resout un user_id en nom d'utilisateur (avec cache par compte).

    Le nom est decode (voir `_text`) : tous les appelants l'affichent, et la
    comparaison de libelle passe de toute facon par `_label_key`, qui decode
    aussi. Aucun appelant ne le renvoie a Kanboard.
    """
    try:
        uid = int(user_id or 0)
    except (ValueError, TypeError):
        return ""
    if uid <= 0:
        return ""
    account = _get_credentials(as_user)[2]
    bucket = _user_cache.setdefault(account, {})
    if uid in bucket:
        return bucket[uid]
    try:
        user = await kb_call("getUser", {"user_id": uid}, as_user=account)
        name = _text(user.get("name") or user.get("username", "")) if user else ""
        bucket[uid] = name
        return name
    except Exception:
        bucket[uid] = ""
        return ""


async def _get_column_name(project_id: Any, column_id: Any) -> str:
    """Resout un column_id en nom de colonne (avec cache par projet)."""
    try:
        pid, cid = int(project_id or 0), int(column_id or 0)
    except (ValueError, TypeError):
        return ""
    if pid <= 0 or cid <= 0:
        return ""
    if pid not in _column_cache:
        try:
            cols = await kb_call("getColumns", {"project_id": pid})
            _column_cache[pid] = {
                int(c["id"]): _text(c.get("title", "")) for c in (cols or [])
            }
        except Exception:
            _column_cache[pid] = {}
    return _column_cache.get(pid, {}).get(cid, "")


async def _get_swimlane_name(project_id: Any, swimlane_id: Any) -> str:
    """Resout un swimlane_id en nom de swimlane (avec cache par projet)."""
    try:
        pid, sid = int(project_id or 0), int(swimlane_id or 0)
    except (ValueError, TypeError):
        return ""
    if pid <= 0 or sid <= 0:
        return ""
    if pid not in _swimlane_cache:
        try:
            sws = await kb_call("getAllSwimlanes", {"project_id": pid})
            _swimlane_cache[pid] = {
                int(s["id"]): _text(s.get("name", "")) for s in (sws or [])
            }
        except Exception:
            _swimlane_cache[pid] = {}
    return _swimlane_cache.get(pid, {}).get(sid, "")


async def _get_my_user_id(as_user: str = "") -> tuple[int, str]:
    """Retourne (user_id, erreur) de l'utilisateur authentifie pour ce compte.

    Retourne (uid > 0, "") en succes, ou (0, message_erreur) en echec.
    Essaie getMe() d'abord (tokens personnels), puis getUserByLoginName()
    en fallback (admin), puis getAllUsers() en dernier recours.

    Cache par compte effectif (primary/agent).

    Le nom affiche mis en cache est decode une fois ; le login configure
    et sa comparaison restent bruts.
    """
    user, _, account = _get_credentials(as_user)
    if account in _my_id_cache:
        return _my_id_cache[account], ""

    errors: list[str] = []

    # Essai 1 : getMe - fonctionne avec les tokens utilisateur personnels
    try:
        me = await kb_call("getMe", as_user=account)
        if me:
            uid = int(me.get("id", 0))
            if uid > 0:
                _my_id_cache[account] = uid
                _my_name_cache[account] = _text(me.get("name") or me.get("username"))
                return uid, ""
    except Exception as e:
        errors.append(f"getMe: {e}")

    # Essai 2 : getUserByLoginName - fonctionne avec les tokens admin
    try:
        u = await kb_call("getUserByLoginName", {"login": user}, as_user=account)
        if u:
            uid = int(u.get("id", 0))
            if uid > 0:
                _my_id_cache[account] = uid
                _my_name_cache[account] = _text(u.get("name") or u.get("username"))
                return uid, ""
    except Exception as e:
        errors.append(f"getUserByLoginName({user!r}): {e}")

    # Essai 3 : getAllUsers + filtrage par username
    try:
        users = await kb_call("getAllUsers", as_user=account)
        if users:
            for u in users:
                if u.get("username") == user:
                    uid = int(u.get("id", 0))
                    if uid > 0:
                        _my_id_cache[account] = uid
                        _my_name_cache[account] = _text(u.get("name") or u.get("username"))
                        return uid, ""
    except Exception as e:
        errors.append(f"getAllUsers: {e}")

    err_msg = (
        f"Impossible de resoudre l'utilisateur '{user}' (compte={account}). "
        "Verifiez que le token API est le token PERSONNEL "
        "(Kanboard > Profil > Gestion des API), pas le token application. "
        f"Erreurs : {'; '.join(errors)}"
    )
    return 0, err_msg


async def _get_my_tasks(status_id: int = 1, as_user: str = "") -> tuple[list[dict], str]:
    """Retourne (taches, erreur) assignees a l'utilisateur authentifie pour ce compte.

    Retourne (liste, "") en succes, ou ([], message_erreur) en echec.
    Itere sur tous les projets actifs et filtre par owner_id.
    """
    account = _get_credentials(as_user)[2]
    my_uid, err = await _get_my_user_id(as_user=account)
    if my_uid <= 0:
        return [], err

    projects, _scope = await _projects_for_account(as_user=account)
    if not projects:
        return [], ""

    results: list[dict] = []
    for p in projects:
        if p.get("is_active") in (0, "0", False):
            continue
        try:
            tasks = await kb_call("getAllTasks", {"project_id": p["id"], "status_id": status_id}, as_user=account)
        except Exception:
            # Projet visible mais inaccessible (403, timeout, etc.) - on saute
            continue
        if not tasks:
            continue
        for t in tasks:
            try:
                if int(t.get("owner_id") or 0) == my_uid:
                    results.append(t)
            except (ValueError, TypeError):
                pass
    return results, ""


_PROJECT_NAMES: dict[int, str] = {}
_CATEGORY_NAMES: dict[int, dict] = {}


async def _get_project_name(project_id: Any) -> str:
    """Nom d'un projet, memorise le temps de la session."""
    pid = int(project_id) if str(project_id or "").isdigit() else 0
    if not pid:
        return ""
    if pid in _PROJECT_NAMES:
        return _PROJECT_NAMES[pid]
    try:
        p = await kb_call("getProjectById", {"project_id": pid})
        name = _text((p or {}).get("name"))
    except Exception:
        name = ""
    _PROJECT_NAMES[pid] = name
    return name


async def _get_category_name(project_id: Any, category_id: Any) -> str:
    """Nom d'une categorie, memorise par projet."""
    pid = int(project_id) if str(project_id or "").isdigit() else 0
    cid = int(category_id) if str(category_id or "").isdigit() else 0
    if not pid or not cid:
        return ""
    if pid not in _CATEGORY_NAMES:
        try:
            rows = await kb_call("getAllCategories", {"project_id": pid})
            _CATEGORY_NAMES[pid] = {
                int(c["id"]): _text(c.get("name")) for c in (rows or []) if c.get("id")
            }
        except Exception:
            _CATEGORY_NAMES[pid] = {}
    return _CATEGORY_NAMES[pid].get(cid, "")


async def _task_summary(t: dict, as_user: str = "") -> dict:
    """Extrait les champs essentiels d'une tache, en resolvant les IDs en noms."""
    pid = t.get("project_id")

    # Assignee : privilegier les champs enrichis, sinon resoudre owner_id
    assignee = _text(t.get("assignee_name") or t.get("assignee_username"))
    if not assignee:
        assignee = await _get_user_name(t.get("owner_id"), as_user=as_user)

    # Colonne
    col_name = _text(t.get("column_name"))
    if not col_name:
        col_name = await _get_column_name(pid, t.get("column_id"))

    # Swimlane
    sw_name = _text(t.get("swimlane_name"))
    if not sw_name:
        sw_name = await _get_swimlane_name(pid, t.get("swimlane_id"))

    # Projet : getAllTasks ne renseigne PAS project_name, il revenait donc
    # toujours vide et obligeait a un appel de plus pour situer une tache.
    proj_name = _text(t.get("project_name"))
    if not proj_name:
        proj_name = await _get_project_name(pid)

    # Categorie : absente de la sortie jusqu'ici, elle n'etait lisible que par
    # kanboard_get_board, dont la reponse complete depasse la limite de tokens.
    cat_name = _text(t.get("category_name"))
    if not cat_name:
        cat_name = await _get_category_name(pid, t.get("category_id"))

    return {
        "id": t.get("id"),
        "title": _text(t.get("title")),
        "project_id": pid,
        "project_name": proj_name,
        "column_name": col_name,
        "swimlane_name": sw_name,
        "category_name": cat_name,
        "assignee": assignee,
        "priority": t.get("priority"),
        "due_date": _ts_to_date(t.get("date_due")),
        # Rendu tel quel, "0" etait une chaine vraie : une carte fermee relue
        # par un appelant qui teste la verite se lisait ouverte.
        "is_active": _is_active_flag(t.get("is_active")),
        # La date de derniere modification n'etait lisible par aucun outil de
        # liste : il fallait un get_task par carte. Elle ne date pas tout signal
        # pour autant, un commentaire ne la fait pas avancer (voir la docstring
        # de kanboard_list_tasks).
        "date_created": _ts_to_datetime(t.get("date_creation")),
        "date_modified": _ts_to_datetime(t.get("date_modification")),
        "color_id": t.get("color_id", ""),
    }


# ---------------------------------------------------------------------------
# Tools -- Dashboard & Projets
# ---------------------------------------------------------------------------


@mcp.tool()
async def kanboard_my_dashboard(as_user: str = "") -> str:
    """Tableau de bord personnel Kanboard.

    Retourne les taches assignees a l'utilisateur courant,
    groupees par projet. Inclut les taches actives uniquement.

    Parametres :
    - as_user : compte cible ("primary" = humain, "agent" = assistant IA, "" = defaut)

    Les entites HTML des noms utilises comme cles de by_project sont
    decodees une fois, comme les textes des taches. Les identifiants et
    champs techniques ne sont pas decodes.
    """
    try:
        tasks, err = await _get_my_tasks(status_id=1, as_user=as_user)
        if err:
            return f"Erreur : {err}"
        by_project: dict[str, list] = {}
        for t in tasks:
            pname = _text(t.get("project_name", f"Projet {t.get('project_id')}"))
            by_project.setdefault(pname, []).append(await _task_summary(t, as_user=as_user))
        return _dumps({"as_user": _get_credentials(as_user)[2], "task_count": len(tasks), "by_project": by_project})
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_list_projects(
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    name_filter: str = "",
) -> str:
    """Liste les projets Kanboard accessibles.

    Parametres :
    - limit : nombre max de resultats (defaut 20, max 100)
    - offset : nombre de projets a sauter avant de decouper (pagination)
    - name_filter : sous-chaine filtrant le nom OU l'identifiant du projet,
      insensible a la casse, aux accents, aux entites HTML (`&amp;`) et aux
      espaces superflus (ex. "pilotage" trouve "[Pilotage] Coordination")

    Retourne : total_available (avant pagination), offset, count, has_more,
    scope, et projects (id, nom, identifiant, description tronquee a 200
    caracteres, is_active, nb_open_tasks).

    `scope` vaut "all" (tous les projets de l'instance) ou "member" quand le
    compte n'est pas administrateur : la liste se limite alors aux projets dont
    il est membre, et n'est donc pas l'inventaire complet.

    Les entites HTML de name, identifier et description sont decodees une
    fois avant la troncature. Les identifiants numeriques et etats ne sont
    pas decodes.
    """
    try:
        projects, scope = await _projects_for_account()

        # Filtrage cote MCP : getAllProjects ne prend aucun critere.
        needle = _fold(name_filter)
        if needle:
            projects = [
                p for p in projects
                if needle in _fold(p.get("name")) or needle in _fold(p.get("identifier"))
            ]

        # total_available et has_more sont mesures AVANT le decoupage : sans eux,
        # une instance a plus de `limit` projets se lit comme une liste complete.
        total = len(projects)
        start = max(0, offset)
        window = projects[start:start + _clamp_limit(limit)]

        results = [
            {
                "id": p.get("id"),
                "name": _text(p.get("name")),
                "identifier": _text(p.get("identifier", "")),
                "description": _text(p.get("description"))[:200],
                "is_active": p.get("is_active"),
                "nb_open_tasks": p.get("nb_open_tasks", ""),
            }
            for p in window
        ]
        return _dumps({
            "total_available": total,
            "offset": start,
            "count": len(results),
            "has_more": start + len(results) < total,
            "scope": scope,
            "projects": results,
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Taches
# ---------------------------------------------------------------------------


@mcp.tool()
async def kanboard_list_tasks(
    project_id: int = 0,
    status: int = 1,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    modified_before: str = "",
) -> str:
    """Liste les taches d'un projet Kanboard.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - status : 1=actives (defaut), 0=fermees
    - limit : nombre max de resultats (defaut 20, max 100)
    - offset : nombre de taches a sauter avant de decouper (pagination)
    - modified_before : ne garder que les cartes dont la derniere modification
      est STRICTEMENT anterieure a cette date (AAAA-MM-JJ, ou AAAA-MM-JJ HH:MM).
      Repere sans un appel par carte celles qu'on n'a ni editees ni deplacees
      depuis cette date. Ce n'est PAS « sans aucune activite » : un commentaire
      ne compte pas, voir « Ce que mesure date_modified » plus bas, ainsi que
      ce qu'il advient des cartes sans date lisible.

    Retourne : total_available (apres filtrage, avant pagination), offset,
    count, has_more, next_offset (present seulement quand has_more vaut true),
    et tasks (id, titre, projet, colonne, swimlane, categorie, porteur,
    priorite, echeance, is_active, date_created, date_modified).

    `is_active` vaut l'entier 1 ou 0, jamais la chaine "0" que rend parfois
    Kanboard et qui est vraie en Python. Les textes rendus (titre, libelles)
    sont decodes : Kanboard stocke "&amp;" la ou la carte affiche "&".

    Quand `modified_before` est fourni, la reponse porte en plus :
    - `modified_before` : la borne effectivement appliquee, telle que lue ;
    - `modified_before_unknown` : nombre de cartes ECARTEES faute de date de
      modification exploitable, present seulement s'il y en a. Une carte sans
      date n'est pas dormante, elle est indatable : elle est mise de cote
      plutot que comptee a tort dans un sens ou dans l'autre.
    Le filtre s'applique AVANT la pagination : `total_available` compte le
    perimetre filtre, pas le projet entier. La borne s'entend dans le fuseau
    du serveur MCP, comme toutes les dates rendues ici.

    Ce que mesure `date_modified` (champ `date_modification` de Kanboard), et
    donc `modified_before` :
    - la date AVANCE quand un champ de la carte est modifie (titre,
      description, echeance, porteur, categorie, priorite...), quand la carte
      change de colonne ou de swimlane, et quand elle est fermee ou rouverte ;
    - elle N'AVANCE PAS quand la carte est commentee, quand une sous-tache est
      creee, modifiee, cochee ou supprimee, quand un lien est ajoute ou retire,
      ni quand la carte est reordonnee dans sa colonne.
    Une carte qui ne vit que par ses commentaires sort donc comme non modifiee
    depuis longtemps. Constate sur l'instance le 29/09/2026 : la carte 7970,
    commentee le 27/09, gardait une modification au 24/09, et la 8012,
    commentee a 10 h 09, gardait sa date de creation de 9 h 22. Le reste est lu
    dans la source de Kanboard (TaskModificationModel, TaskPositionModel,
    TaskStatusModel, CommentModel, SubtaskModel, TaskLinkModel).
    Un critere de dormance qui tient compte des commentaires demande de relire
    ceux des cartes candidates par kanboard_list_comments, un appel par carte :
    l'API JSON-RPC ne rend pas le dernier commentaire de chaque carte en un
    appel, et kanboard_recent_activity plafonne a 50 evenements.

    La reponse etait auparavant une liste JSON nue, coupee a `limit` sans rien
    en dire : un projet de 109 taches fermees interroge a limit=100 se lisait
    comme un projet de 100 taches, et les 9 restantes n'etaient atteignables par
    aucun parametre. `total_available` compte AVANT le decoupage, `has_more` dit
    qu'il en reste, et `next_offset` donne la valeur d'`offset` a passer au
    prochain appel.

    Deux limites viennent de Kanboard et ne sont pas levables ici :
    - `getAllTasks` ne pagine pas cote serveur. Le connecteur recoit toujours
      toutes les taches du projet et decoupe ensuite : `offset` epargne la
      resolution des libelles, pas l'appel JSON-RPC ni son volume.
    - l'ordre est celui rendu par Kanboard (colonne, puis position dans la
      colonne), pas un tri stable garanti. Une carte deplacee entre deux appels
      change de rang : paginer un projet en cours de remaniement peut sauter ou
      repeter une ligne.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    bound, bound_error = _parse_date_bound(modified_before, "modified_before")
    if bound_error:
        return bound_error
    try:
        tasks = list(await kb_call(
            "getAllTasks", {"project_id": project_id, "status_id": status}
        ) or [])

        # Le filtre de dormance precede la pagination : applique apres, il
        # aurait rendu un total portant sur le projet entier et une liste
        # portant sur autre chose.
        undatable = 0
        if bound is not None:
            kept = []
            for t in tasks:
                ts = _to_timestamp(t.get("date_modification"))
                if ts is None:
                    undatable += 1
                elif ts < bound:
                    kept.append(t)
            tasks = kept

        # total_available et has_more sont mesures AVANT le decoupage : sans eux,
        # un projet coupe a `limit` se lit comme un projet complet.
        total = len(tasks)
        start = max(0, offset)
        # Les libelles ne sont resolus que sur la fenetre rendue : `_task_summary`
        # peut declencher un appel par tache (colonne, swimlane, projet,
        # categorie, porteur).
        window = tasks[start:start + _clamp_limit(limit)]
        results = [await _task_summary(t) for t in window]

        payload: dict[str, Any] = {
            "total_available": total,
            "offset": start,
            "count": len(results),
            "has_more": start + len(results) < total,
            "tasks": results,
        }
        if payload["has_more"]:
            payload["next_offset"] = start + len(results)
        if bound is not None:
            payload["modified_before"] = modified_before.strip()
            if undatable:
                payload["modified_before_unknown"] = undatable
        return _dumps(payload)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_get_task(task_id: int = 0, full_comments: bool = False) -> str:
    """Detail complet d'une tache par son ID.

    Parametres :
    - task_id : identifiant de la tache (obligatoire)
    - full_comments : True = commentaires en entier ; False (defaut) = apercu
      de 500 caracteres

    Retourne tous les champs : titre, description, colonne, assignee,
    priorite, dates, commentaires, sous-taches.

    Quand un apercu de commentaire est coupe, `comment_truncated` et
    `comment_full_length` l'indiquent ; leur absence signifie « contenu
    complet ». Pour un texte integral, utiliser full_comments=True ou
    kanboard_list_comments.

    Les entites HTML du titre, de la description, des libelles, des noms
    de personnes, des commentaires et des titres et libelles de statut des
    sous-taches sont decodees une fois. La description est ensuite limitee
    a 2000 caracteres ; apercus et longueurs des commentaires portent sur
    le texte decode. Le statut numerique de repli, les identifiants, les
    dates et color_id ne sont pas decodes.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        try:
            task = await kb_call("getTask", {"task_id": task_id})
        except Exception as exc:
            # Kanboard controle l'acces au PROJET de la tache : une tache
            # supprimee n'ayant plus de projet, il rend un refus de permission
            # la ou l'objet n'existe simplement plus. Annoncer « permissions
            # insuffisantes » envoie alors chercher un probleme de droits qui
            # n'existe pas.
            if _is_permission_error(exc):
                return _dumps({
                    "found": False,
                    "task_id": task_id,
                    "message": (
                        f"Tache {task_id} illisible : soit elle n'existe plus (Kanboard "
                        "rend un refus d'acces pour une tache supprimee, faute de projet "
                        "rattache), soit elle appartient a un projet hors de votre "
                        "perimetre. Les deux cas sont indiscernables via l'API."
                    ),
                })
            raise
        if not task:
            return _dumps({"found": False, "task_id": task_id,
                           "message": f"Tache {task_id} introuvable."})

        # Recuperer commentaires et sous-taches en parallele
        comments_raw = await kb_call("getAllComments", {"task_id": task_id})
        subtasks_raw = await kb_call("getAllSubtasks", {"task_id": task_id})

        comments = []
        for c in (comments_raw or []):
            content = _text(c.get("comment"))
            entry: dict[str, Any] = {
                "id": c.get("id"),
                "user": _text(c.get("name") or c.get("username", "")),
                "date": _ts_to_datetime(c.get("date_creation")),
                "content": content,
            }
            if not full_comments and len(content) > COMMENT_PREVIEW_CHARS:
                # Le `[:500]` muet faisait relire un commentaire tronque comme
                # s'il etait complet, donc reecrire par-dessus en perdant la fin.
                entry["content"] = content[:COMMENT_PREVIEW_CHARS]
                entry["comment_truncated"] = True
                entry["comment_full_length"] = len(content)
            comments.append(entry)

        subtasks = []
        for s in (subtasks_raw or []):
            subtasks.append({
                "id": s.get("id"),
                "title": _text(s.get("title")),
                "assignee": _text(s.get("name") or s.get("username", "")),
                # Le libelle traduit quand Kanboard en donne un, le code
                # numerique sinon : un status_name vide n'apprend rien.
                "status": _text(s["status_name"]) if s.get("status_name") else s.get("status"),
            })

        pid = task.get("project_id")

        # Resoudre les IDs en noms
        assignee = _text(task.get("assignee_name") or task.get("assignee_username"))
        if not assignee:
            assignee = await _get_user_name(task.get("owner_id"))

        creator = _text(task.get("creator_name") or task.get("creator_username"))
        if not creator:
            creator = await _get_user_name(task.get("creator_id"))

        col_name = _text(task.get("column_name"))
        if not col_name:
            col_name = await _get_column_name(pid, task.get("column_id"))

        sw_name = _text(task.get("swimlane_name"))
        if not sw_name:
            sw_name = await _get_swimlane_name(pid, task.get("swimlane_id"))

        detail = {
            "id": task.get("id"),
            "title": _text(task.get("title")),
            "description": _text(task.get("description"))[:2000],
            "project_id": pid,
            # Resolus comme dans search_tasks : getTask ne renseigne ni l'un ni
            # l'autre, et la categorie n'etait lisible que par get_board, dont
            # la sortie complete depasse la limite de tokens.
            "project_name": _text(task.get("project_name")) or await _get_project_name(pid),
            "column_name": col_name,
            "swimlane_name": sw_name,
            "category_name": _text(task.get("category_name"))
            or await _get_category_name(pid, task.get("category_id")),
            "category_id": task.get("category_id"),
            "assignee": assignee,
            "creator": creator,
            "priority": task.get("priority"),
            "color_id": task.get("color_id", ""),
            "due_date": _ts_to_date(task.get("date_due")),
            "date_started": _ts_to_datetime(task.get("date_started")),
            "date_created": _ts_to_datetime(task.get("date_creation")),
            "date_modified": _ts_to_datetime(task.get("date_modification")),
            "date_completed": _ts_to_datetime(task.get("date_completed")),
            "is_active": task.get("is_active"),
            "time_spent": task.get("time_spent"),
            "time_estimated": task.get("time_estimated"),
            "comments": comments,
            "subtasks": subtasks,
        }
        return _dumps(detail)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_search_tasks(
    query: str = "",
    limit: int = DEFAULT_LIMIT,
    scan_all: bool = False,
    offset: int = 0,
) -> str:
    """Recherche de taches par mot-cle dans tous les projets accessibles.

    Kanboard n'expose pas de recherche globale en JSON-RPC : le connecteur
    balaie les projets un par un et filtre cote client. Ce balayage s'arrete
    des que `limit` est atteint, donc une absence de resultat ne prouve rien
    tant que `projects_scanned` est inferieur a `projects_total`.

    Parametres :
    - query : texte a rechercher (insensible a la casse)
    - limit : nombre max de resultats (defaut 20, max 100)
    - scan_all : balayer TOUS les projets avant de rendre la main (defaut non).
      Le balayage par defaut s'arrete des que `limit` resultats sont trouves,
      ce qui biaise le resultat vers les premiers projets : sur cette instance,
      une recherche a limit=20 s'arretait au 27e projet sur 66, sans jamais
      regarder les 39 suivants. Avec scan_all, `total_matches` porte le compte
      REEL et la liste rendue reste plafonnee a `limit`.
    - offset : nombre de correspondances a sauter avant de commencer a rendre.
      Sans lui, `scan_all` corrigeait le COMPTE sans donner acces a la LISTE :
      195 correspondances annoncees, toujours les 20 memes rendues, et aucune
      des 175 autres nommable. A utiliser avec scan_all, sans quoi l'ordre de
      balayage change d'un appel a l'autre.

    Retourne : count, total_matches, scope, has_more, projects_scanned,
    projects_total, offset, has_more_results, next_offset, et results (taches
    dont le titre ou la description contient le texte). Chaque tache porte
    desormais son `project_name` et sa `category_name`, resolus.
    `scan_warning` apparait quand le balayage s'est arrete en chemin : une
    absence de resultat n'y prouve rien.

    `scope` vaut "all" ou "member" selon que le compte est administrateur ou
    non : en "member" le balayage ne couvre que les projets dont il est membre.
    """
    if not query:
        return "Erreur : query est obligatoire."
    try:
        # Kanboard n'a pas de recherche globale en JSON-RPC.
        # On itere sur les projets et filtre cote client.
        projects, scope = await _projects_for_account()
        active = [
            p for p in (projects or [])
            if p.get("is_active") and p.get("is_active") != "0"
        ]
        if not active:
            return _dumps({
                "count": 0, "scope": scope, "has_more": False,
                "projects_scanned": 0, "projects_total": 0, "results": [],
            })

        query_lower = query.lower()
        results: list[dict] = []
        limit_val = _clamp_limit(limit)
        skip = max(0, int(offset))
        scanned = 0
        matches = 0

        for p in active:
            scanned += 1
            try:
                tasks = await kb_call("getAllTasks", {"project_id": p["id"], "status_id": 1})
            except Exception:
                continue
            if not tasks:
                continue
            for t in tasks:
                title = (t.get("title") or "").lower()
                desc = (t.get("description") or "").lower()
                if query_lower in title or query_lower in desc:
                    matches += 1
                    if matches > skip and len(results) < limit_val:
                        results.append(await _task_summary(t))
                    elif len(results) >= limit_val and not scan_all:
                        break
            if not scan_all and len(results) >= limit_val:
                break

        stopped_early = scanned < len(active)
        payload: dict[str, Any] = {
            "count": len(results),
            # `total_matches` ne vaut le compte reel que si tout a ete balaye ;
            # sinon il ne compte que ce que le balayage a eu le temps de voir.
            "total_matches": matches,
            "scope": scope,
            # Le balayage s'arrete a `limit` : sans ce drapeau, une recherche
            # coupee en cours de route se lit comme exhaustive.
            "has_more": stopped_early,
            "projects_scanned": scanned,
            "projects_total": len(active),
            "offset": skip,
            # Deux causes de « il en reste » : des correspondances vues mais non
            # rendues, ET un balayage arrete avant la fin. Les conditionner a
            # scan_all faisait cohabiter dans la meme reponse un scan_warning
            # disant que tout n'a pas ete regarde et un booleen disant le
            # contraire. Un appelant qui se fie au booleen conclut a tort.
            "has_more_results": (skip + len(results)) < matches or stopped_early,
            "results": results,
        }
        if (skip + len(results)) < matches:
            payload["next_offset"] = skip + len(results)
        if stopped_early:
            payload["scan_warning"] = (
                f"Balayage arrete au projet {scanned} sur {len(active)} : les suivants "
                "n'ont PAS ete regardes. Une absence de resultat n'y prouve rien. "
                "Relancer avec scan_all=True pour un balayage exhaustif."
            )
        return _dumps(payload)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_my_overdue(as_user: str = "") -> str:
    """Liste mes taches en retard dans tous les projets.

    Retourne les taches actives assignees a l'utilisateur courant
    dont la date d'echeance est depassee, triees par anciennete.

    Parametres :
    - as_user : compte cible ("primary" = humain, "agent" = assistant IA, "" = defaut)

    Repose sur la procedure `getOverdueTasks`, qui rend en UN appel les taches
    en retard de toute l'instance : le balayage projet par projet coutait ~70
    appels `getAllTasks` pour la meme reponse. `getOverdueTasks` ne filtre pas
    par utilisateur (elle rend les taches de tout le monde), d'ou le filtrage
    sur owner_id cote client, apres coup.

    Si le compte n'a pas le droit d'appeler `getOverdueTasks` (procedure
    reservee aux administrateurs sur certaines instances), on retombe sur le
    balayage : mieux vaut une reponse lente qu'un 403 sec. Le champ `source`
    dit laquelle des deux voies a repondu.

    Les lignes de `getOverdueTasks` ne portent ni colonne, ni swimlane, ni
    categorie, ni priorite : ces champs ressortent vides sur cette voie. Les
    resoudre demanderait un appel par tache, ce qui annulerait le gain.
    """
    try:
        account = _get_credentials(as_user)[2]
        my_uid, err = await _get_my_user_id(as_user=account)
        if my_uid <= 0:
            return f"Erreur : {err}"

        source = "getOverdueTasks"
        try:
            tasks = await kb_call("getOverdueTasks", as_user=account) or []
        except Exception as exc:
            if not _is_permission_error(exc):
                raise
            source = "scan_projets"
            tasks, err = await _get_my_tasks(status_id=1, as_user=account)
            if err:
                return f"Erreur : {err}"

        now_ts = int(datetime.now().timestamp())
        overdue = []
        for t in tasks:
            try:
                if int(t.get("owner_id") or 0) != my_uid:
                    continue
            except (ValueError, TypeError):
                continue
            due = t.get("date_due")
            if not due or due == "0":
                continue
            try:
                due_ts = int(due)
            except (ValueError, TypeError):
                continue
            if not 0 < due_ts < now_ts:
                continue
            summary = await _task_summary(t, as_user=account)
            summary["days_overdue"] = (now_ts - due_ts) // 86400
            overdue.append(summary)

        # Trier par date d'echeance croissante (plus ancien en premier)
        overdue.sort(key=lambda x: x.get("due_date", ""))
        return _dumps({
            "as_user": account,
            "source": source,
            "overdue_count": len(overdue),
            "tasks": overdue,
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Board
# ---------------------------------------------------------------------------


@mcp.tool()
async def kanboard_get_board(
    project_id: int = 0,
    tasks_per_column: int = 10,
    status: int = 1,
) -> str:
    """Etat du tableau Kanboard d'un projet.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - tasks_per_column : nombre max de cartes detaillees par colonne
      (defaut 10, max 100)
    - status : perimetre des cartes. 1 = cartes actives (defaut, c'est ce
      qu'affiche un tableau Kanboard), 0 = cartes fermees seulement,
      -1 = toutes. -1 restitue le perimetre rendu par les versions
      anterieures a 0.17.2 dans le depot canonique (dont la 0.17.1 installee).

    PERIMETRE : `task_count`, `total` et la liste `tasks` d'une colonne portent
    tous sur le meme perimetre, celui demande par `status`. Ce n'etait pas le
    cas avant : la procedure getBoard de Kanboard ne filtre rien (BoardFormatter
    ne pose aucune condition sur is_active), et le connecteur rendait les cartes
    fermees avec exactement les memes champs que les actives. Releve sur le
    projet 262 le 29/09/2026 : 61 des 63 cartes fermees ressortaient, la somme
    des task_count valait 523 pour 460 cartes actives, et une file de travail
    construite sur cette sortie etait fausse d'un facteur proche de deux sans
    que rien ne le signale.

    Chaque carte porte id, titre, assignee, echeance (due_date), priorite,
    categorie, `is_active`, `date_created` et `date_modified`. `is_active` est
    rendu quelle que soit la valeur de `status`, pour que le perimetre d'une
    reponse se lise sur la carte et pas seulement sur l'appel.

    `date_modified` date la derniere edition, le dernier changement de colonne
    ou de swimlane, la derniere fermeture ou reouverture. Un commentaire, une
    sous-tache ou un lien ne la font pas avancer : ce n'est pas la date du
    dernier signal recu par la carte. Detail dans kanboard_list_tasks.

    Une colonne dont les cartes ont ete tronquees porte truncated=true avec
    shown/total, comptes sur le perimetre filtre.

    Une carte dont Kanboard n'a pas rendu is_active n'est JAMAIS ecartee : elle
    sort avec is_active=null quel que soit `status`, et sa colonne porte
    status_filter_incomplete=true. Filtrer sur un champ absent rejouerait a
    l'envers la perte silencieuse que ce parametre corrige.

    Limite non levable ici : getBoard ne pagine pas cote serveur. Le connecteur
    recoit toutes les cartes du projet, fermees comprises, et filtre ensuite.
    `status` epargne des tokens en sortie, pas le volume de l'appel JSON-RPC.

    Les textes rendus (titre, categorie, porteur, noms de colonne et de
    swimlane) sont decodes : Kanboard stocke "&amp;" la ou la carte affiche "&".
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    if status not in (1, 0, -1):
        return (
            f"Erreur : status invalide ({status}). Valeurs acceptees : 1 (cartes "
            "actives, defaut), 0 (cartes fermees), -1 (toutes)."
        )
    try:
        board = await kb_call("getBoard", {"project_id": project_id})
        if not board:
            return "Tableau introuvable."

        per_column = _clamp_limit(tasks_per_column)
        result = []
        for swimlane in board:
            sw_name = _text(swimlane.get("name", "Default"))
            columns = []
            for col in swimlane.get("columns", []):
                kept: list[tuple[dict, int | None]] = []
                incomplete = False
                for t in col.get("tasks", []):
                    flag = _is_active_flag(t.get("is_active"))
                    if flag is None:
                        incomplete = True
                    elif status != -1 and flag != status:
                        continue
                    kept.append((t, flag))
                shown = kept[:per_column]  # Limiter pour les tokens
                entry = {
                    "id": col.get("id"),
                    "name": _text(col.get("title", "")),
                    # Compte le perimetre rendu, et non le projet entier : un
                    # task_count plus large que la liste se lit comme une
                    # troncature et envoie chercher des cartes qui sont ailleurs.
                    "task_count": len(kept),
                    "tasks": [
                        {
                            "id": t.get("id"),
                            "title": _text(t.get("title")),
                            "assignee": _text(t.get("assignee_name", "")),
                            "due_date": _ts_to_date(t.get("date_due")),
                            "priority": t.get("priority"),
                            "category_name": _text(t.get("category_name", "")),
                            "is_active": flag,
                            "date_created": _ts_to_datetime(t.get("date_creation")),
                            "date_modified": _ts_to_datetime(t.get("date_modification")),
                        }
                        for t, flag in shown
                    ],
                }
                # Une carte muette sur son statut est gardee : le dire vaut
                # mieux que de la perdre ou de la compter a tort.
                if incomplete:
                    entry["status_filter_incomplete"] = True
                # Sans ce marqueur, une colonne tronquee est indiscernable d'une
                # colonne qui contient exactement per_column cartes.
                if len(shown) < len(kept):
                    entry["truncated"] = True
                    entry["shown"] = len(shown)
                    entry["total"] = len(kept)
                columns.append(entry)
            result.append({"swimlane": sw_name, "columns": columns})

        return _dumps(result)
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Actions (modifications)
# ---------------------------------------------------------------------------


@mcp.tool()
async def kanboard_move_task(
    task_id: int = 0,
    column_name: str = "",
    swimlane_name: str = "",
    position: int = 1,
    project_id: int = 0,
    as_user: str = "",
) -> str:
    """Deplace une tache vers une colonne (et/ou un swimlane) du tableau.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - column_name : nom de la colonne cible (ex: 'En cours', 'Done')
    - swimlane_name : nom du swimlane cible (vide = swimlane actuel inchange)
    - position : position dans la colonne (defaut: 1 = haut)
    - project_id : ID du projet (si omis, deduit de la tache)
    - as_user : compte qui execute le deplacement ("" = defaut, "primary"/"agent")

    Resout les noms de colonne et swimlane en IDs automatiquement.

    Apres succes de l'API, relit la tache via getTask avec le meme as_user.
    `success` conserve la reponse de l'API ; `verified` confirme la colonne et
    le swimlane attendus, pas la position dans la colonne. La reponse ajoute
    `verification_method`, `post_state` si disponible, et `mismatch`/`warning`
    en cas d'ecart. Une relecture impossible donne `verified=false` avec un
    avertissement, sans rejouer l'ecriture.

    Le rapprochement des noms replie les entites HTML (`&amp;` se lit `&`,
    `&nbsp;` se lit comme une espace), les suites d'espaces, les espaces de bord,
    la casse et les formes Unicode NFC/NFD : un nom recopie depuis une page HTML
    ou un compte rendu resout donc sans retouche. Les accents, eux, ne sont PAS
    replies : "Reunion" ne designe pas "Réunion". Un nom exact l'emporte toujours
    sur un nom qui ne correspond qu'apres repli ; si le repli seul fait
    correspondre plusieurs valeurs, la resolution est refusee et demande
    l'identifiant.

    Les valeurs disponibles et suggestions des erreurs de resolution sont
    decodees une fois. Les parametres envoyes, leur rappel dans les messages
    et les comparaisons ne sont pas modifies.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    if not column_name:
        return "Erreur : column_name est obligatoire."
    try:
        # Recuperer la tache pour connaitre le projet et le swimlane actuel
        task = await kb_call("getTask", {"task_id": task_id}, as_user=as_user)
        if not task:
            return "Tache introuvable."

        pid = project_id or int(task.get("project_id", 0))
        if not pid:
            return "Erreur : impossible de determiner le project_id."

        swimlane_id = int(task.get("swimlane_id", 0))

        # Recuperer les colonnes du projet
        columns = await kb_call("getColumns", {"project_id": pid}, as_user=as_user)
        if not columns:
            return "Erreur : aucune colonne trouvee pour ce projet."

        # Chercher la colonne par nom (entites HTML, espaces et casse replies)
        target_col, err = _resolve_label(columns, column_name, "Colonne")
        if target_col is None:
            return err
        col_id = int(target_col["id"])

        # Resoudre le swimlane par nom si fourni
        if swimlane_name:
            swimlanes = await kb_call("getActiveSwimlanes", {"project_id": pid}, as_user=as_user)
            target_sw, err = _resolve_label(swimlanes, swimlane_name, "Swimlane")
            if target_sw is None:
                return err
            swimlane_id = int(target_sw["id"])

        success = await kb_call("moveTaskPosition", {
            "project_id": pid,
            "task_id": task_id,
            "column_id": col_id,
            "position": max(1, position),
            "swimlane_id": swimlane_id,
        }, as_user=as_user)

        if success:
            result: dict[str, Any] = {"success": True, "task_id": task_id, "moved_to": column_name}
            if swimlane_name:
                result["swimlane"] = swimlane_name
            verified, post_state, mismatch, method = await _verify_task_state(
                task_id, {"column_id": col_id, "swimlane_id": swimlane_id}, as_user=as_user
            )
            return _verified_payload(result, verified, post_state, mismatch, method)
        else:
            return "Echec du deplacement (la tache est peut-etre deja dans cette colonne)."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_update_task(
    task_id: int = 0,
    title: str = "",
    description: str = "",
    priority: int = -1,
    due_date: str = "",
    owner_id: int = -1,
    category_id: int = -1,
    color_id: str = "",
    tags: list[str] | None = None,
    as_user: str = "",
) -> str:
    """Modifie une tache existante dans Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - title : nouveau titre (vide = inchange)
    - description : nouvelle description (vide = inchange)
    - priority : priorite 0-3 (-1 = inchange)
    - due_date : date d'echeance ISO (YYYY-MM-DD), vide = inchange
    - owner_id : ID du porteur de la tache (-1 = inchange, 0 = desassigner)
    - category_id : ID de la categorie (-1 = inchange, 0 = aucune)
    - color_id : couleur de la carte (ex: 'yellow', 'green', '' = inchange)
    - tags : liste de tags libres (null = inchange, [] = supprimer tous les tags)
    - as_user : compte qui modifie ("" = defaut, "primary"/"agent")

    Retourne les details de la tache mise a jour.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        params: dict[str, Any] = {"id": task_id}
        if title:
            params["title"] = title
        if description:
            params["description"] = description
        if priority >= 0:
            params["priority"] = priority
        if due_date:
            params["date_due"] = due_date
        if owner_id >= 0:
            params["owner_id"] = owner_id
        if category_id >= 0:
            params["category_id"] = category_id
        if color_id:
            params["color_id"] = color_id
        if tags is not None:
            params["tags"] = list(tags)

        if len(params) == 1:
            return "Erreur : aucun champ a modifier."

        success = await kb_call("updateTask", params, as_user=as_user)
        if success:
            # Recuperer la tache mise a jour
            task = await kb_call("getTask", {"task_id": task_id}, as_user=as_user)
            return _dumps(await _task_summary(task, as_user=as_user)) if task else _dumps({"success": True})
        else:
            return "Echec de la mise a jour."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_assign_task(
    task_id: int = 0,
    user_id: int = 0,
    user_name: str = "",
    as_user: str = "",
) -> str:
    """Assigne (ou desassigne) le porteur d'une tache existante.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - user_id : ID de l'utilisateur Kanboard (0 + user_name vide = desassigner)
    - user_name : nom de l'utilisateur (si user_id=0, resolution auto via membres du projet)
    - as_user : compte qui effectue la modification ("" = defaut, "primary"/"agent")

    Resolution par nom :
    - Recherche dans les membres du projet (nom affiche ET login), en repliant
      entites HTML, espaces superflus, casse et formes Unicode NFC/NFD - meme
      traitement que les noms de colonne, swimlane et categorie. Les accents ne
      sont pas replies.
    - Erreur explicite si aucune correspondance ou ambiguite.
    - L'utilisateur doit etre membre du projet ; sinon erreur avec suggestion
      d'utiliser kanboard_assign_project_user.

    Exemples :
    - kanboard_assign_task(task_id=1234, user_id=4)             -> assigne user 4
    - kanboard_assign_task(task_id=1234, user_name="alice")     -> resolution auto
    - kanboard_assign_task(task_id=1234, user_id=0)             -> desassigne

    owner_name utilise le nom deja decode par le cache, dans les deux
    cas de succes. Les noms des membres disponibles ou ambigus sont decodes
    une fois pour affichage. Les comparaisons, parametres envoyes et
    valeurs saisies rappelees dans les erreurs ne sont pas modifies.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."

    try:
        # Recuperer la tache pour connaitre le projet
        task = await kb_call("getTask", {"task_id": task_id}, as_user=as_user)
        if not task:
            return f"Tache {task_id} introuvable."

        pid = int(task.get("project_id", 0))
        if not pid:
            return "Erreur : impossible de determiner le projet de la tache."

        # Cas desassignation
        if user_id == 0 and not user_name:
            success = await kb_call("updateTask", {"id": task_id, "owner_id": 0}, as_user=as_user)
            if success:
                return _dumps({"success": True, "task_id": task_id, "owner_id": 0, "action": "deassigned"})
            return "Echec de la desassignation."

        resolved_uid = user_id

        # Resolution par nom si user_id non fourni
        if resolved_uid == 0 and user_name:
            members_raw = await kb_call("getProjectUsers", {"project_id": pid}, as_user=as_user)
            if not members_raw:
                return f"Projet {pid} : aucun membre trouve (verifiez les droits du compte)."

            # Kanboard retourne {uid_str: username} ; enrichir avec le nom affiche
            if isinstance(members_raw, dict):
                member_list = [{"id": int(k), "username": v} for k, v in members_raw.items()]
            else:
                member_list = [{"id": int(m.get("id", 0)), "username": m.get("username", "") or m.get("name", "")} for m in members_raw]

            # Meme repli que pour les colonnes et swimlanes : un nom affiche
            # recopie depuis une page HTML porte "&amp;" la ou Kanboard stocke "&".
            name_key = _label_key(user_name)
            matches: list[dict] = []
            for m in member_list:
                uid = m["id"]
                # Comparer login Kanboard
                if _label_key(m.get("username")) == name_key:
                    matches.append(m)
                    continue
                # Comparer nom affiche (appel getUser, cached)
                display = await _get_user_name(uid, as_user=as_user)
                if _label_key(display) == name_key:
                    matches.append({**m, "display_name": display})

            if len(matches) == 0:
                member_names = []
                for m in member_list:
                    dn = await _get_user_name(m["id"], as_user=as_user)
                    member_names.append(dn or _text(m.get("username", f"uid={m['id']}")))
                return (
                    f"Aucun membre du projet {pid} ne correspond a '{user_name}'. "
                    f"Membres disponibles : {member_names}"
                )
            if len(matches) > 1:
                return (
                    f"Ambiguite : '{user_name}' correspond a plusieurs membres : "
                    f"{[m.get('display_name') or _text(m.get('username')) for m in matches]}. "
                    "Preciser user_id."
                )
            resolved_uid = matches[0]["id"]

        # Verifier que l'utilisateur est bien membre du projet
        members_raw = await kb_call("getProjectUsers", {"project_id": pid}, as_user=as_user)
        if members_raw:
            if isinstance(members_raw, dict):
                member_ids = {int(k) for k in members_raw}
            else:
                member_ids = {int(m.get("id", 0)) for m in members_raw}
            if resolved_uid not in member_ids:
                return (
                    f"L'utilisateur {resolved_uid} n'est pas membre du projet {pid}. "
                    "Utiliser kanboard_assign_project_user pour l'ajouter d'abord."
                )

        # Idempotence : si deja assigne au meme utilisateur, ok
        current_owner = int(task.get("owner_id") or 0)
        if current_owner == resolved_uid:
            display = await _get_user_name(resolved_uid, as_user=as_user)
            return _dumps({
                "success": True,
                "task_id": task_id,
                "owner_id": resolved_uid,
                "owner_name": display,
                "note": "deja assigne a cet utilisateur",
            })

        success = await kb_call("updateTask", {"id": task_id, "owner_id": resolved_uid}, as_user=as_user)
        if success:
            display = await _get_user_name(resolved_uid, as_user=as_user)
            return _dumps({
                "success": True,
                "task_id": task_id,
                "owner_id": resolved_uid,
                "owner_name": display,
                "project_id": pid,
            })
        return "Echec de l'assignation."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_add_comment(
    task_id: int = 0,
    comment: str = "",
    as_user: str = "",
) -> str:
    """Ajoute un commentaire a une tache Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - comment : texte du commentaire (obligatoire)
    - as_user : auteur du commentaire ("" = defaut, "primary"/"agent")

    Retourne l'ID du commentaire cree (et le compte qui a ecrit).
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    if not comment:
        return "Erreur : comment est obligatoire."
    try:
        identity = await _ensure_write_identity(as_user)
        uid = identity["user_id"] if identity.get("verified", True) else None

        comment_id = await kb_call("createComment", {
            "task_id": task_id,
            "content": comment,
        }, as_user=as_user)

        if comment_id:
            read_error = ""
            try:
                recorded = await kb_call("getComment", {"comment_id": comment_id}, as_user=as_user)
            except Exception as exc:  # l'ecriture, elle, a reussi
                recorded, read_error = None, str(exc)
                logger.warning("Relecture de l'auteur du commentaire %s impossible : %s",
                               comment_id, exc)
            return _dumps({
                "success": True,
                "comment_id": comment_id,
                "task_id": task_id,
                "as_user": _get_credentials(as_user, for_write=True)[2],
                **_author_fields(recorded, "user_id", uid),
                **_author_legacy(recorded, "user_id", uid, read_error),
                "author_verification_method": "getComment",
            })
        else:
            return "Echec de la creation du commentaire."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_close_task(task_id: int = 0, as_user: str = "") -> str:
    """Ferme (clôture) une tâche dans Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - as_user : compte qui ferme la tache ("" = defaut, "primary"/"agent")

    Apres succes de l'API, relit la tache via getTask avec le meme as_user.
    `success` conserve la reponse de l'API ; `verified` confirme is_active=0.
    La reponse ajoute `verification_method`, `post_state` si disponible, et
    `mismatch`/`warning` en cas d'ecart. Une relecture impossible donne
    `verified=false` avec un avertissement, sans rejouer l'ecriture.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        success = await kb_call("closeTask", {"task_id": task_id}, as_user=as_user)
        if success:
            base = {"success": True, "task_id": task_id, "action": "closed"}
            verified, post_state, mismatch, method = await _verify_task_state(
                task_id, {"is_active": 0}, as_user=as_user
            )
            return _verified_payload(base, verified, post_state, mismatch, method)
        else:
            return "Echec de la fermeture de la tache."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_open_task(task_id: int = 0, as_user: str = "") -> str:
    """Rouvre une tâche dans Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - as_user : compte qui rouvre la tache ("" = defaut, "primary"/"agent")

    Apres succes de l'API, relit la tache via getTask avec le meme as_user.
    `success` conserve la reponse de l'API ; `verified` confirme is_active=1.
    La reponse ajoute `verification_method`, `post_state` si disponible, et
    `mismatch`/`warning` en cas d'ecart. Une relecture impossible donne
    `verified=false` avec un avertissement, sans rejouer l'ecriture.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        success = await kb_call("openTask", {"task_id": task_id}, as_user=as_user)
        if success:
            base = {"success": True, "task_id": task_id, "action": "opened"}
            verified, post_state, mismatch, method = await _verify_task_state(
                task_id, {"is_active": 1}, as_user=as_user
            )
            return _verified_payload(base, verified, post_state, mismatch, method)
        else:
            return "Echec de la reouverture de la tache."
    except Exception as exc:
        return _format_error(exc)


async def _resolve_by_name(
    method: str, project_id: int, name: str, label: str, as_user: str = ""
) -> "tuple[int | None, str]":
    """Traduit un nom de swimlane ou de categorie en identifiant.

    Creer une tache demandait de connaitre ces identifiants, donc un appel de
    recherche prealable pour chacun : sur cette instance, list_swimlanes et
    list_categories totalisaient 31 appels pour 25 creations. Les noms sont ce
    que l'appelant a sous la main.

    Retourne (id, "") si trouve, (None, message) sinon - le message enumere les
    valeurs disponibles plutot que de laisser chercher.

    Le rapprochement passe par `_resolve_label` : entites HTML, espaces et casse
    sont replies avant comparaison, et le message d'echec designe la valeur
    disponible la plus proche.
    """
    rows = await kb_call(method, {"project_id": project_id}, as_user=as_user)
    row, err = _resolve_label(rows, name, label)
    if row is None:
        return None, err
    return int(row["id"]), ""


@mcp.tool()
async def kanboard_create_task(
    project_id: int | str = 0,
    title: str = "",
    description: str = "",
    column_name: str = "",
    assignee_id: int = 0,
    priority: int = 0,
    due_date: str = "",
    swimlane_id: int = 0,
    category_id: int = 0,
    swimlane_name: str = "",
    category_name: str = "",
    tags: list[str] | None = None,
    as_user: str = "",
) -> str:
    """Cree une nouvelle tache dans un projet Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - title : titre de la tache (obligatoire)
    - description : description en Markdown (optionnel)
    - column_name : nom de la colonne cible (optionnel, defaut = premiere colonne)
    - assignee_id : ID utilisateur a assigner (optionnel)
    - priority : 0=aucune, 1=basse, 2=moyenne, 3=haute (defaut: 0)
    - due_date : date d'echeance YYYY-MM-DD (optionnel)
    - swimlane_id : ID du swimlane cible (0 = swimlane par defaut)
    - category_id : ID de la categorie / tag structure (0 = aucune)
    - swimlane_name : nom du swimlane, a la place de swimlane_id
    - category_name : nom de la categorie, a la place de category_id
    - tags : liste de tags libres a associer (ex: ["urgent","backend"])
    - as_user : compte qui cree la tache ("" = defaut, "primary"/"agent")

    Colonne, swimlane et categorie s'indiquent par leur NOM : les identifiants
    restent acceptes, mais ne sont plus a chercher au prealable. Un nom inconnu
    fait echouer la creation en enumerant les valeurs disponibles et en
    designant la plus proche, plutot que de creer la tache au mauvais endroit.

    Le rapprochement des noms replie les entites HTML (`&amp;` se lit `&`,
    `&nbsp;` se lit comme une espace), les suites d'espaces, les espaces de bord,
    la casse et les formes Unicode NFC/NFD : un nom recopie depuis une page HTML
    ou un compte rendu resout donc sans retouche. Les accents, eux, ne sont PAS
    replies : "Reunion" ne designe pas "Réunion". Un nom exact l'emporte toujours
    sur un nom qui ne correspond qu'apres repli ; si le repli seul fait
    correspondre plusieurs valeurs, la resolution est refusee et demande
    l'identifiant.

    Retourne l'ID de la tache creee.

    Les valeurs disponibles et suggestions des erreurs de resolution sont
    decodees une fois. Les parametres envoyes, leur rappel dans les messages
    et les comparaisons ne sont pas modifies.
    """
    project_id = int(project_id) if project_id else 0
    if not project_id:
        return "Erreur : project_id est obligatoire."
    if not title:
        return "Erreur : title est obligatoire."
    if swimlane_name and swimlane_id:
        return "Erreur : fournir swimlane_id OU swimlane_name, pas les deux."
    if category_name and category_id:
        return "Erreur : fournir category_id OU category_name, pas les deux."
    try:
        params: dict[str, Any] = {
            "project_id": project_id,
            "title": title,
        }
        if description:
            params["description"] = description
        if priority > 0:
            params["priority"] = priority
        if due_date:
            params["date_due"] = due_date
        if assignee_id:
            params["owner_id"] = assignee_id
        if swimlane_id:
            params["swimlane_id"] = swimlane_id
        if category_id:
            params["category_id"] = category_id
        if tags:
            params["tags"] = list(tags)

        if swimlane_name:
            resolved, err = await _resolve_by_name(
                "getAllSwimlanes", project_id, swimlane_name, "Swimlane", as_user
            )
            if resolved is None:
                return err
            params["swimlane_id"] = resolved
        if category_name:
            resolved, err = await _resolve_by_name(
                "getAllCategories", project_id, category_name, "Categorie", as_user
            )
            if resolved is None:
                return err
            params["category_id"] = resolved

        # Resolve column name to ID if provided
        if column_name:
            columns = await kb_call("getColumns", {"project_id": project_id}, as_user=as_user)
            col, err = _resolve_label(columns, column_name, "Colonne")
            if col is None:
                return err
            params["column_id"] = int(col["id"])

        task_id = await kb_call("createTask", params, as_user=as_user)

        if task_id:
            base = {
                "success": True,
                "task_id": task_id,
                "project_id": project_id,
                "title": title,
                "as_user": _get_credentials(as_user, for_write=True)[2],
            }
            # verified_write : createTask renvoie un ID, ce qui ne prouve ni que
            # la tâche existe, ni qu'elle a atterri dans le bon projet ou la
            # bonne colonne (un column_id refusé est ignoré en silence).
            expected: dict[str, Any] = {"title": title, "project_id": project_id}
            if params.get("column_id"):
                expected["column_id"] = params["column_id"]
            verified, post_state, mismatch, method = await _verify_task_state(
                int(task_id), expected, as_user=as_user,
                fields=("id", "title", "column_id", "swimlane_id", "is_active", "project_id", "creator_id"),
            )
            identity = _identity_cache.get(_identity_key(base["as_user"]), {})
            base.update(_author_fields(post_state, "creator_id", identity.get("user_id")))
            base.update(_author_legacy(
                post_state, "creator_id", identity.get("user_id"),
                "" if post_state else f"{method} n'a pas rendu la tache",
            ))
            base["author_verification_method"] = "getTask"
            return _verified_payload(base, verified, post_state, mismatch, method)
        else:
            return "Echec de la creation de la tache."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_delete_task(task_id: int | str = 0, as_user: str = "") -> str:
    """Supprime definitivement une tache Kanboard.

    ATTENTION : suppression irreversible. Preferer kanboard_close_task
    pour une fermeture douce (la tache reste consultable).

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache a supprimer (obligatoire)
    - as_user : compte qui supprime ("" = defaut, "primary"/"agent")

    Retourne un objet JSON en cas de succes.

    Le champ title relu avant suppression est decode une fois pour le
    retour JSON. Les parametres envoyes et champs techniques ne sont pas
    decodes.
    """
    task_id = int(task_id) if task_id else 0
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        # Fetch task info before deletion for confirmation
        task = await kb_call("getTask", {"task_id": task_id}, as_user=as_user)
        if not task:
            return f"Tache #{task_id} introuvable."

        task_title = _text(task.get("title", "?"))
        success = await kb_call("removeTask", {"task_id": task_id}, as_user=as_user)

        if success:
            return _dumps({"success": True, "task_id": task_id, "title": task_title, "action": "deleted"})
        else:
            return "Echec de la suppression de la tache."
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Structure projet (projets / colonnes / swimlanes / categories)
# ---------------------------------------------------------------------------

# Couleurs Kanboard valides pour les categories (et les taches).
_KANBOARD_COLORS = {
    "yellow", "blue", "green", "purple", "red", "orange", "grey",
    "brown", "deep_orange", "dark_grey", "pink", "teal", "cyan",
    "lime", "light_green", "amber",
}


@mcp.tool()
async def kanboard_create_project(name: str = "", description: str = "", as_user: str = "") -> str:
    """Cree un nouveau projet Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - name : nom du projet (obligatoire)
    - description : description (optionnel)
    - as_user : compte createur ("" = defaut, "primary"/"agent")

    Retourne l'ID du projet cree.
    """
    if not name:
        return "Erreur : name est obligatoire."
    try:
        params: dict[str, Any] = {"name": name}
        if description:
            params["description"] = description
        project_id = await kb_call("createProject", params, as_user=as_user)
        if project_id:
            return _dumps({**_creation_without_author(as_user), "success": True, "project_id": project_id, "name": name})
        return "Echec de la creation du projet."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_get_project_metadata(project_id: int = 0) -> str:
    """Detail complet d'un projet Kanboard (description, dates, owner...).

    Plus complet que kanboard_list_projects (description integrale).

    Parametres :
    - project_id : ID du projet (obligatoire)

    Les entites HTML de name, identifier, description et default_swimlane
    sont decodees une fois. Les identifiants, etats, dates et url ne sont
    pas decodes.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        project = await kb_call("getProjectById", {"project_id": project_id})
        if not project:
            return f"Projet #{project_id} introuvable."
        result = {
            "id": project.get("id"),
            "name": _text(project.get("name")),
            "identifier": _text(project.get("identifier", "")),
            "description": _text(project.get("description")),
            "is_active": project.get("is_active"),
            "is_public": project.get("is_public"),
            "is_private": project.get("is_private"),
            "owner_id": project.get("owner_id"),
            "start_date": project.get("start_date", ""),
            "end_date": project.get("end_date", ""),
            "date_created": _ts_to_datetime(project.get("last_modified")),
            "default_swimlane": _text(project.get("default_swimlane", "")),
            "url": project.get("url", {}),
        }
        return _dumps(result)
    except Exception as exc:
        return _format_error(exc)


# ----- Colonnes ------------------------------------------------------------


@mcp.tool()
async def kanboard_list_columns(project_id: int = 0) -> str:
    """Liste les colonnes d'un projet Kanboard.

    Parametres :
    - project_id : ID du projet (obligatoire)

    Retourne : id, title, position, task_limit, description.

    Les entites HTML de title et description sont decodees une fois,
    avant la limite de 500 caracteres de description. Les champs
    techniques ne sont pas decodes.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        cols = await kb_call("getColumns", {"project_id": project_id})
        if not cols:
            return _dumps([])
        results = [{
            "id": c.get("id"),
            "title": _text(c.get("title")),
            "position": c.get("position"),
            "task_limit": c.get("task_limit", 0),
            "description": _text(c.get("description"))[:500],
            "hide_in_dashboard": c.get("hide_in_dashboard", 0),
        } for c in cols]
        return _dumps(results)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_create_column(
    project_id: int = 0,
    title: str = "",
    task_limit: int = 0,
    description: str = "",
    as_user: str = "",
) -> str:
    """Cree une nouvelle colonne dans un projet Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - title : libelle de la colonne (obligatoire)
    - task_limit : nombre max de taches (0 = illimite)
    - description : description (optionnel)
    - as_user : compte qui cree ("" = defaut, "primary"/"agent")

    Retourne l'ID de la colonne creee.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    if not title:
        return "Erreur : title est obligatoire."
    try:
        params: dict[str, Any] = {"project_id": project_id, "title": title}
        if task_limit > 0:
            params["task_limit"] = task_limit
        if description:
            params["description"] = description
        col_id = await kb_call("addColumn", params, as_user=as_user)
        # Invalider le cache de noms de colonnes pour ce projet
        _column_cache.pop(int(project_id), None)
        if col_id:
            return _dumps({**_creation_without_author(as_user), "success": True, "column_id": col_id, "project_id": project_id, "title": title})
        return "Echec de la creation de la colonne."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_update_column(
    column_id: int = 0,
    title: str = "",
    task_limit: int = -1,
    description: str = "",
    hide_in_dashboard: int = -1,
    as_user: str = "",
) -> str:
    """Modifie une colonne existante.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - column_id : ID de la colonne (obligatoire)
    - title : nouveau titre (vide = inchange)
    - task_limit : nouvelle limite (-1 = inchange, 0 = illimite)
    - description : nouvelle description (vide = inchange)
    - hide_in_dashboard : 0 ou 1 (-1 = inchange)
    - as_user : compte qui modifie ("" = defaut, "primary"/"agent")

    Retourne True en cas de succes.
    """
    if not column_id:
        return "Erreur : column_id est obligatoire."
    try:
        params: dict[str, Any] = {"column_id": column_id}
        if title:
            params["title"] = title
        if task_limit >= 0:
            params["task_limit"] = task_limit
        if description:
            params["description"] = description
        if hide_in_dashboard in (0, 1):
            params["hide_in_dashboard"] = hide_in_dashboard
        if len(params) == 1:
            return "Erreur : aucun champ a modifier."
        success = await kb_call("updateColumn", params, as_user=as_user)
        # Vider entierement le cache colonnes (on ne connait pas le project_id)
        _column_cache.clear()
        if success:
            return _dumps({"success": True, "column_id": column_id})
        return "Echec de la mise a jour de la colonne."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_remove_column(column_id: int = 0, as_user: str = "") -> str:
    """Supprime definitivement une colonne d'un projet.

    ATTENTION : suppression irreversible. Les taches de la colonne sont
    supprimees ou deplacees selon la configuration Kanboard.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - column_id : ID de la colonne a supprimer (obligatoire)
    - as_user : compte qui supprime ("" = defaut, "primary"/"agent")
    """
    if not column_id:
        return "Erreur : column_id est obligatoire."
    try:
        success = await kb_call("removeColumn", {"column_id": column_id}, as_user=as_user)
        _column_cache.clear()
        if success:
            return _dumps({"success": True, "column_id": column_id, "action": "removed"})
        return "Echec de la suppression de la colonne."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_change_column_position(
    project_id: int = 0,
    column_id: int = 0,
    position: int = 0,
    as_user: str = "",
) -> str:
    """Change la position d'une colonne dans un projet (1-based).

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - column_id : ID de la colonne (obligatoire)
    - position : nouvelle position (>=1)
    - as_user : compte qui modifie ("" = defaut, "primary"/"agent")
    """
    if not project_id or not column_id or position < 1:
        return "Erreur : project_id, column_id et position (>=1) sont obligatoires."
    try:
        success = await kb_call("changeColumnPosition", {
            "project_id": project_id,
            "column_id": column_id,
            "position": position,
        }, as_user=as_user)
        if success:
            return _dumps({"success": True, "column_id": column_id, "new_position": position})
        return "Echec du changement de position."
    except Exception as exc:
        return _format_error(exc)


# ----- Swimlanes -----------------------------------------------------------


@mcp.tool()
async def kanboard_list_swimlanes(
    project_id: int = 0,
    include_inactive: bool = False,
) -> str:
    """Liste les swimlanes d'un projet Kanboard.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - include_inactive : True = inclure les swimlanes desactivees (defaut False)

    Retourne : id, name, position, is_active, description.

    Les entites HTML de name et description sont decodees une fois,
    avant la limite de 500 caracteres de description. Les champs
    techniques ne sont pas decodes.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        method = "getAllSwimlanes" if include_inactive else "getActiveSwimlanes"
        sw = await kb_call(method, {"project_id": project_id})
        if not sw:
            return _dumps([])
        # getActiveSwimlanes ne retourne pas tous les champs : normaliser
        results = [{
            "id": s.get("id"),
            "name": _text(s.get("name")),
            "position": s.get("position", ""),
            "is_active": s.get("is_active", 1),
            "description": _text(s.get("description"))[:500],
        } for s in sw]
        return _dumps(results)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_create_swimlane(
    project_id: int = 0,
    name: str = "",
    description: str = "",
    as_user: str = "",
) -> str:
    """Cree un nouveau swimlane dans un projet.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - name : nom du swimlane (obligatoire)
    - description : description (optionnel)
    - as_user : compte qui cree ("" = defaut, "primary"/"agent")

    Retourne l'ID du swimlane cree.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    if not name:
        return "Erreur : name est obligatoire."
    try:
        params: dict[str, Any] = {"project_id": project_id, "name": name}
        if description:
            params["description"] = description
        sw_id = await kb_call("addSwimlane", params, as_user=as_user)
        _swimlane_cache.pop(int(project_id), None)
        if sw_id:
            return _dumps({**_creation_without_author(as_user), "success": True, "swimlane_id": sw_id, "project_id": project_id, "name": name})
        return "Echec de la creation du swimlane."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_update_swimlane(
    project_id: int = 0,
    swimlane_id: int = 0,
    name: str = "",
    description: str = "",
    as_user: str = "",
) -> str:
    """Modifie un swimlane existant.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - swimlane_id : ID du swimlane (obligatoire)
    - name : nouveau nom (vide = inchange)
    - description : nouvelle description (vide = inchange)
    - as_user : compte qui modifie ("" = defaut, "primary"/"agent")
    """
    if not project_id or not swimlane_id:
        return "Erreur : project_id et swimlane_id sont obligatoires."
    if not name and not description:
        return "Erreur : aucun champ a modifier (name ou description)."
    try:
        # Kanboard exige `name` dans updateSwimlane meme si on ne change que la
        # description. Auto-fetch le nom courant si non fourni.
        if not name:
            all_sw = await kb_call("getAllSwimlanes", {"project_id": project_id}, as_user=as_user)
            current = next((s for s in (all_sw or []) if int(s.get("id", 0)) == int(swimlane_id)), None)
            if not current:
                return f"Erreur : swimlane #{swimlane_id} introuvable dans le projet #{project_id}."
            name = current.get("name", "")
        params: dict[str, Any] = {
            "project_id": project_id,
            "swimlane_id": swimlane_id,
            "name": name,
        }
        if description:
            params["description"] = description
        success = await kb_call("updateSwimlane", params, as_user=as_user)
        _swimlane_cache.pop(int(project_id), None)
        if success:
            return _dumps({"success": True, "swimlane_id": swimlane_id})
        return "Echec de la mise a jour du swimlane."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_remove_swimlane(
    project_id: int = 0,
    swimlane_id: int = 0,
    as_user: str = "",
) -> str:
    """Supprime definitivement un swimlane.

    ATTENTION : suppression irreversible.
    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - swimlane_id : ID du swimlane (obligatoire)
    - as_user : compte qui supprime ("" = defaut, "primary"/"agent")
    """
    if not project_id or not swimlane_id:
        return "Erreur : project_id et swimlane_id sont obligatoires."
    try:
        success = await kb_call("removeSwimlane", {
            "project_id": project_id,
            "swimlane_id": swimlane_id,
        }, as_user=as_user)
        _swimlane_cache.pop(int(project_id), None)
        if success:
            return _dumps({"success": True, "swimlane_id": swimlane_id, "action": "removed"})
        return ("Echec de la suppression du swimlane. Cause probable : il contient "
                "encore des taches. Deplacer ou supprimer les taches d'abord.")
    except Exception as exc:
        return _format_error(exc)


# ----- Categories ----------------------------------------------------------


@mcp.tool()
async def kanboard_list_categories(project_id: int = 0) -> str:
    """Liste les categories d'un projet Kanboard.

    Parametres :
    - project_id : ID du projet (obligatoire)

    Retourne : id, name, color_id, description.

    Les entites HTML de name et description sont decodees une fois,
    avant la limite de 500 caracteres de description. color_id et les
    identifiants ne sont pas decodes.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        cats = await kb_call("getAllCategories", {"project_id": project_id})
        if not cats:
            return _dumps([])
        results = [{
            "id": c.get("id"),
            "name": _text(c.get("name")),
            "color_id": c.get("color_id", ""),
            "description": _text(c.get("description"))[:500],
        } for c in cats]
        return _dumps(results)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_create_category(
    project_id: int = 0,
    name: str = "",
    color_id: str = "",
    as_user: str = "",
) -> str:
    """Cree une categorie (tag structure) pour un projet.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - name : nom de la categorie (obligatoire)
    - color_id : couleur (yellow, blue, green, purple, red, orange, grey,
      brown, deep_orange, dark_grey, pink, teal, cyan, lime, light_green, amber)
      vide = couleur par defaut Kanboard
    - as_user : compte qui cree ("" = defaut, "primary"/"agent")

    Retourne l'ID de la categorie creee.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    if not name:
        return "Erreur : name est obligatoire."
    if color_id and color_id not in _KANBOARD_COLORS:
        return f"Erreur : color_id invalide. Valeurs : {sorted(_KANBOARD_COLORS)}"
    try:
        params: dict[str, Any] = {"project_id": project_id, "name": name}
        if color_id:
            params["color_id"] = color_id
        cat_id = await kb_call("createCategory", params, as_user=as_user)
        if cat_id:
            return _dumps({**_creation_without_author(as_user), "success": True, "category_id": cat_id, "project_id": project_id, "name": name})
        return "Echec de la creation de la categorie."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_update_category(
    category_id: int = 0,
    name: str = "",
    color_id: str = "",
    as_user: str = "",
) -> str:
    """Modifie une categorie existante.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - category_id : ID de la categorie (obligatoire)
    - name : nouveau nom (vide = inchange)
    - color_id : nouvelle couleur (vide = inchange)
    - as_user : compte qui modifie ("" = defaut, "primary"/"agent")
    """
    if not category_id:
        return "Erreur : category_id est obligatoire."
    if color_id and color_id not in _KANBOARD_COLORS:
        return f"Erreur : color_id invalide. Valeurs : {sorted(_KANBOARD_COLORS)}"
    if not name and not color_id:
        return "Erreur : aucun champ a modifier (name ou color_id)."
    try:
        # Kanboard exige `name` dans updateCategory. Auto-fetch si non fourni.
        if not name:
            cat = await kb_call("getCategory", {"category_id": category_id}, as_user=as_user)
            if not cat:
                return f"Erreur : categorie #{category_id} introuvable."
            name = cat.get("name", "")
        params: dict[str, Any] = {"id": category_id, "name": name}
        if color_id:
            params["color_id"] = color_id
        success = await kb_call("updateCategory", params, as_user=as_user)
        if success:
            return _dumps({"success": True, "category_id": category_id})
        return "Echec de la mise a jour de la categorie."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_remove_category(category_id: int = 0, as_user: str = "") -> str:
    """Supprime definitivement une categorie.

    ATTENTION : suppression irreversible.
    Confirmation utilisateur requise avant execution.

    Parametres :
    - category_id : ID de la categorie (obligatoire)
    - as_user : compte qui supprime ("" = defaut, "primary"/"agent")
    """
    if not category_id:
        return "Erreur : category_id est obligatoire."
    try:
        success = await kb_call("removeCategory", {"category_id": category_id}, as_user=as_user)
        if success:
            return _dumps({"success": True, "category_id": category_id, "action": "removed"})
        return "Echec de la suppression de la categorie."
    except Exception as exc:
        return _format_error(exc)


# ----- Activite recente ----------------------------------------------------


@mcp.tool()
async def kanboard_recent_activity(
    project_id: int = 0,
    since_iso: str = "",
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Activite recente d'un projet (creations, mouvements, commentaires...).

    Utile pour faire un sync rapide en debut de session.

    ATTENTION - plafond impose par l'API : Kanboard ne rend jamais plus de 50
    evenements. La procedure getProjectActivity n'accepte que project_id ; tout
    parametre `limit` cote API ressort en erreur -32602. `limit` ici ne peut
    donc que reduire, jamais elargir. Sur un projet tres actif, ces 50
    evenements peuvent ne couvrir que quelques jours.

    Consequence sur since_iso : le filtre s'applique APRES ce plafond, donc une
    date anterieure au plus vieil evenement disponible ne ramene pas l'histoire
    manquante. Ce cas est signale par `window_incomplete`.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - since_iso : filtre date ISO (YYYY-MM-DD), vide = toute la fenetre disponible
    - limit : nombre max d'evenements a renvoyer (defaut 20 ; plafonne a 50 par l'API)

    Retourne : count, api_cap_reached, oldest_available, newest_available, et
    events (event_type, date, user, task_id, task_title, summary).

    Les entites HTML de user, task_title et summary sont decodees une
    fois, avant la limite de 300 caracteres de summary. event_type, les
    identifiants et les dates ne sont pas decodes.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        events = await kb_call("getProjectActivity", {"project_id": project_id})
        if not events:
            return _dumps({"count": 0, "api_cap_reached": False, "events": []})

        # Une fenetre pleine signifie « il y a probablement plus, hors de portee ».
        cap_reached = len(events) >= ACTIVITY_API_CAP

        timestamps = []
        for e in events:
            try:
                timestamps.append(int(e.get("date_creation") or 0))
            except (ValueError, TypeError):
                pass
        oldest_ts = min(timestamps) if timestamps else 0

        # Filtre date
        threshold_ts = 0
        if since_iso:
            try:
                threshold_ts = int(datetime.strptime(since_iso, "%Y-%m-%d").timestamp())
            except ValueError:
                return "Erreur : since_iso doit etre au format YYYY-MM-DD."

        results = []
        for e in events:
            try:
                ts = int(e.get("date_creation") or 0)
            except (ValueError, TypeError):
                ts = 0
            if threshold_ts and ts < threshold_ts:
                continue
            task = e.get("task") or {}
            results.append({
                "event_type": e.get("event_name", ""),
                "date": _ts_to_datetime(ts),
                "user": _text(e.get("author_name") or e.get("author_username", "")),
                "task_id": task.get("id"),
                "task_title": _text(task.get("title", "")),
                "summary": _text(e.get("event_title"))[:300],
            })
            if len(results) >= _clamp_limit(limit):
                break

        payload: dict[str, Any] = {
            "count": len(results),
            "api_cap_reached": cap_reached,
            "oldest_available": _ts_to_datetime(oldest_ts),
            "newest_available": _ts_to_datetime(max(timestamps) if timestamps else 0),
            "events": results,
        }
        if cap_reached:
            payload["api_cap"] = ACTIVITY_API_CAP
            payload["note"] = (
                f"L'API Kanboard plafonne a {ACTIVITY_API_CAP} evenements et n'expose "
                "aucun parametre pour l'elargir : l'historique anterieur a "
                f"{payload['oldest_available']} est hors de portee de ce tool."
            )
        # since_iso remonte plus haut que la fenetre : le resultat est forcement
        # partiel, et sans ce drapeau il se lirait comme exhaustif.
        if cap_reached and threshold_ts and oldest_ts and threshold_ts < oldest_ts:
            payload["window_incomplete"] = True
        return _dumps(payload)
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Projets : suppression, users, templates
# ---------------------------------------------------------------------------


@mcp.tool()
async def kanboard_delete_project(project_id: int = 0, as_user: str = "") -> str:
    """Supprime definitivement un projet Kanboard.

    ATTENTION : suppression irreversible. Toutes les taches, colonnes,
    swimlanes, categories, fichiers et commentaires sont supprimes.
    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - as_user : compte qui supprime ("" = defaut, "primary"/"agent")

    Le champ name relu avant suppression est decode une fois pour le
    retour JSON. Les parametres envoyes et champs techniques ne sont pas
    decodes.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        # Recuperer infos avant suppression pour le retour
        project = await kb_call("getProjectById", {"project_id": project_id}, as_user=as_user)
        name = _text(project.get("name", "?")) if project else "?"
        success = await kb_call("removeProject", {"project_id": project_id}, as_user=as_user)
        # Vider les caches lies
        _column_cache.pop(int(project_id), None)
        _swimlane_cache.pop(int(project_id), None)
        if success:
            return _dumps({"success": True, "project_id": project_id, "name": name, "action": "deleted"})
        return "Echec de la suppression du projet."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_list_project_users(project_id: int = 0, as_user: str = "") -> str:
    """Liste les utilisateurs assignes a un projet.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - as_user : compte qui interroge ("" = defaut)

    Retourne une liste de membres : user_id et name.

    Le champ name est decode une fois, que la reponse Kanboard soit un
    dictionnaire ou une liste. user_id ne subit aucun decodage HTML.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        # getProjectUsers retourne {id: name} ; getAssignableUsers + getProjectUserRole
        # est plus precis mais 2x plus d'appels. On commence par getProjectUsers.
        members = await kb_call("getProjectUsers", {"project_id": project_id}, as_user=as_user)
        # Normaliser : Kanboard renvoie {} ou {user_id: username}
        if not members:
            return _dumps([])
        # members peut etre dict (user_id -> name) ou list selon version
        if isinstance(members, dict):
            results = [{"user_id": int(uid), "name": _text(name)} for uid, name in members.items()]
        else:
            results = [{"user_id": m.get("id"), "name": _text(m.get("name") or m.get("username", ""))} for m in members]
        return _dumps(results)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_assign_project_user(
    project_id: int = 0,
    user_id: int = 0,
    role: str = "project-member",
    as_user: str = "",
) -> str:
    """Assigne un utilisateur a un projet avec un role.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - user_id : ID de l'utilisateur Kanboard (obligatoire)
    - role : 'project-manager' | 'project-member' (defaut) | 'project-viewer'
    - as_user : compte qui assigne ("" = defaut, "primary"/"agent")
    """
    if not project_id or not user_id:
        return "Erreur : project_id et user_id sont obligatoires."
    if role not in ("project-manager", "project-member", "project-viewer"):
        return "Erreur : role doit etre project-manager | project-member | project-viewer."
    try:
        success = await kb_call("addProjectUser", {
            "project_id": project_id,
            "user_id": user_id,
            "role": role,
        }, as_user=as_user)
        if success:
            return _dumps({"success": True, "project_id": project_id, "user_id": user_id, "role": role})
        return "Echec de l'assignation. L'utilisateur est peut-etre deja membre."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_remove_project_user(
    project_id: int = 0,
    user_id: int = 0,
    as_user: str = "",
) -> str:
    """Retire un utilisateur d'un projet.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - user_id : ID de l'utilisateur Kanboard (obligatoire)
    - as_user : compte qui retire ("" = defaut, "primary"/"agent")
    """
    if not project_id or not user_id:
        return "Erreur : project_id et user_id sont obligatoires."
    try:
        success = await kb_call("removeProjectUser", {
            "project_id": project_id,
            "user_id": user_id,
        }, as_user=as_user)
        if success:
            return _dumps({"success": True, "project_id": project_id, "user_id": user_id, "action": "removed"})
        return "Echec du retrait. L'utilisateur n'est peut-etre pas membre."
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Swimlanes : disable / enable / position
# ---------------------------------------------------------------------------


@mcp.tool()
async def kanboard_disable_swimlane(
    project_id: int = 0,
    swimlane_id: int = 0,
    as_user: str = "",
) -> str:
    """Desactive un swimlane (soft-delete : la lane reste mais devient invisible).

    Alternative non-destructive a kanboard_remove_swimlane. Les taches du
    swimlane restent accessibles.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - swimlane_id : ID du swimlane (obligatoire)
    - as_user : compte qui desactive ("" = defaut)
    """
    if not project_id or not swimlane_id:
        return "Erreur : project_id et swimlane_id sont obligatoires."
    try:
        success = await kb_call("disableSwimlane", {
            "project_id": project_id,
            "swimlane_id": swimlane_id,
        }, as_user=as_user)
        _swimlane_cache.pop(int(project_id), None)
        if success:
            return _dumps({"success": True, "swimlane_id": swimlane_id, "action": "disabled"})
        return "Echec de la desactivation du swimlane."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_enable_swimlane(
    project_id: int = 0,
    swimlane_id: int = 0,
    as_user: str = "",
) -> str:
    """Reactive un swimlane precedemment desactive.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - swimlane_id : ID du swimlane (obligatoire)
    - as_user : compte qui reactive ("" = defaut)
    """
    if not project_id or not swimlane_id:
        return "Erreur : project_id et swimlane_id sont obligatoires."
    try:
        success = await kb_call("enableSwimlane", {
            "project_id": project_id,
            "swimlane_id": swimlane_id,
        }, as_user=as_user)
        _swimlane_cache.pop(int(project_id), None)
        if success:
            return _dumps({"success": True, "swimlane_id": swimlane_id, "action": "enabled"})
        return "Echec de la reactivation du swimlane."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_change_swimlane_position(
    project_id: int = 0,
    swimlane_id: int = 0,
    position: int = 0,
    as_user: str = "",
) -> str:
    """Change la position d'un swimlane dans un projet (1-based).

    Le swimlane par defaut a position=0 et ne peut pas etre deplace ;
    n'utiliser cette fonction que pour les swimlanes additionnels.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - swimlane_id : ID du swimlane (obligatoire)
    - position : nouvelle position (>=1)
    - as_user : compte qui modifie ("" = defaut)
    """
    if not project_id or not swimlane_id or position < 1:
        return "Erreur : project_id, swimlane_id et position (>=1) sont obligatoires."
    try:
        success = await kb_call("changeSwimlanePosition", {
            "project_id": project_id,
            "swimlane_id": swimlane_id,
            "position": position,
        }, as_user=as_user)
        if success:
            return _dumps({"success": True, "swimlane_id": swimlane_id, "new_position": position})
        return "Echec du changement de position."
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Sous-taches (subtasks)
# ---------------------------------------------------------------------------

# Statut Kanboard : 0 = todo, 1 = in progress, 2 = done
_SUBTASK_STATUS_NAMES = {0: "todo", 1: "in_progress", 2: "done"}


@mcp.tool()
async def kanboard_create_subtask(
    task_id: int = 0,
    title: str = "",
    user_id: int = 0,
    time_estimated: float = 0.0,
    as_user: str = "",
) -> str:
    """Cree une sous-tache pour une tache existante.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - task_id : ID de la tache parente (obligatoire)
    - title : titre de la sous-tache (obligatoire)
    - user_id : ID utilisateur a assigner (0 = aucun)
    - time_estimated : temps estime en heures (0 = non defini)
    - as_user : compte qui cree ("" = defaut)

    Retourne l'ID de la sous-tache creee.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    if not title:
        return "Erreur : title est obligatoire."
    try:
        params: dict[str, Any] = {"task_id": task_id, "title": title}
        if user_id:
            params["user_id"] = user_id
        if time_estimated > 0:
            params["time_estimated"] = time_estimated
        sid = await kb_call("createSubtask", params, as_user=as_user)
        if sid:
            return _dumps({**_creation_without_author(as_user), "success": True, "subtask_id": sid, "task_id": task_id, "title": title})
        return "Echec de la creation de la sous-tache."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_update_subtask(
    subtask_id: int = 0,
    task_id: int = 0,
    title: str = "",
    status: int = -1,
    user_id: int = -1,
    time_spent: float = -1.0,
    time_estimated: float = -1.0,
    as_user: str = "",
) -> str:
    """Modifie une sous-tache existante.

    Confirmation utilisateur requise avant execution.

    Parametres :
    - subtask_id : ID de la sous-tache (obligatoire)
    - task_id : ID de la tache parente (obligatoire pour Kanboard)
    - title : nouveau titre (vide = inchange)
    - status : 0=todo, 1=in_progress, 2=done (-1 = inchange)
    - user_id : ID utilisateur (-1 = inchange, 0 = desassigner)
    - time_spent : temps passe en heures (-1 = inchange)
    - time_estimated : temps estime en heures (-1 = inchange)
    - as_user : compte qui modifie ("" = defaut)
    """
    if not subtask_id or not task_id:
        return "Erreur : subtask_id et task_id sont obligatoires."
    try:
        params: dict[str, Any] = {"id": subtask_id, "task_id": task_id}
        if title:
            params["title"] = title
        if status in (0, 1, 2):
            params["status"] = status
        if user_id >= 0:
            params["user_id"] = user_id
        if time_spent >= 0:
            params["time_spent"] = time_spent
        if time_estimated >= 0:
            params["time_estimated"] = time_estimated
        if len(params) == 2:
            return "Erreur : aucun champ a modifier."
        success = await kb_call("updateSubtask", params, as_user=as_user)
        if success:
            return _dumps({"success": True, "subtask_id": subtask_id})
        return "Echec de la mise a jour de la sous-tache."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_delete_subtask(
    subtask_id: int = 0,
    as_user: str = "",
) -> str:
    """Supprime definitivement une sous-tache.

    ATTENTION : suppression irreversible.
    Confirmation utilisateur requise avant execution.

    Parametres :
    - subtask_id : ID de la sous-tache (obligatoire)
    - as_user : compte qui supprime ("" = defaut)
    """
    if not subtask_id:
        return "Erreur : subtask_id est obligatoire."
    try:
        success = await kb_call("removeSubtask", {"subtask_id": subtask_id}, as_user=as_user)
        if success:
            return _dumps({"success": True, "subtask_id": subtask_id, "action": "deleted"})
        return "Echec de la suppression de la sous-tache."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_toggle_subtask_status(
    subtask_id: int = 0,
    as_user: str = "",
) -> str:
    """Bascule le statut d'une sous-tache (cycle todo -> in_progress -> done -> todo).

    Confirmation utilisateur requise avant execution.

    Parametres :
    - subtask_id : ID de la sous-tache (obligatoire)
    - as_user : compte qui bascule ("" = defaut)
    """
    if not subtask_id:
        return "Erreur : subtask_id est obligatoire."
    try:
        # Kanboard n'expose pas de toggle natif en JSON-RPC. On lit le statut
        # actuel via getSubtask, on cycle 0->1->2->0, puis on updateSubtask.
        sub = await kb_call("getSubtask", {"subtask_id": subtask_id}, as_user=as_user)
        if not sub:
            return f"Sous-tache #{subtask_id} introuvable."
        try:
            current = int(sub.get("status", 0))
        except (ValueError, TypeError):
            current = 0
        new_status = (current + 1) % 3
        task_id = sub.get("task_id")
        if not task_id:
            return "Erreur : impossible de recuperer task_id de la sous-tache."
        success = await kb_call("updateSubtask", {
            "id": subtask_id,
            "task_id": int(task_id),
            "status": new_status,
        }, as_user=as_user)
        if success:
            return _dumps({
                "success": True,
                "subtask_id": subtask_id,
                "previous_status": current,
                "previous_status_name": _SUBTASK_STATUS_NAMES.get(current, "?"),
                "new_status": new_status,
                "new_status_name": _SUBTASK_STATUS_NAMES[new_status],
            })
        return "Echec de la mise a jour du statut."
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Commentaires
# ---------------------------------------------------------------------------

# Champs exposes pour un commentaire, dans l'ordre de lecture utile.
_COMMENT_FIELDS = (
    "id", "task_id", "user_id", "username", "name",
    "date_creation", "date_modification", "comment",
)


def _comment_row(raw: dict) -> dict:
    """Projette un commentaire brut sur les champs exposes, sans troncature.

    Les deux dates sont converties en datetime lisible (convention du
    connecteur, qui ne remonte jamais de timestamp Unix brut), mais gardent
    leur nom d'origine cote Kanboard.

    username, name et comment sont decodes une fois ; les identifiants
    et les dates ne subissent aucun decodage HTML.
    """
    row = {k: raw.get(k) for k in _COMMENT_FIELDS}
    for field in ("username", "name", "comment"):
        row[field] = _text(raw.get(field))
    row["date_creation"] = _ts_to_datetime(raw.get("date_creation"))
    row["date_modification"] = _ts_to_datetime(raw.get("date_modification"))
    return row


def _other_account(account: str) -> str:
    """Retourne l'autre compte du connecteur ('primary' <-> 'agent')."""
    return "primary" if account == "agent" else "agent"


def _not_found_or_forbidden(kind: str, obj_id: int, account: str, detail: str = "") -> str:
    """Message pour un 403 rendu par une LECTURE de commentaire.

    Kanboard repond 403 - et non un resultat vide - quand l'identifiant
    n'existe pas : l'autorisation n'arrive pas a resoudre le projet de l'objet
    et refuse par defaut (verifie sur 1.2.53 avec un id volontairement absurde).
    Un « permissions insuffisantes » nu envoie donc chercher un probleme de
    droits qui n'existe pas, et il est de toute facon indiscernable d'un vrai.
    Les deux causes sont nommees, avec le tool qui tranche.
    """
    payload: dict[str, Any] = {
        "success": False,
        "error": "not_found_or_forbidden",
        f"{kind}_id": obj_id,
        "as_user": account,
        "hint": (
            f"Kanboard rend le meme 403 dans deux cas : le {kind}_id #{obj_id} n'existe "
            f"pas, ou il appartient a un projet hors de portee du compte '{account}'. "
            "Verifier l'identifiant, puis le perimetre du compte avec "
            "kanboard_check_project_access."
        ),
    }
    if detail:
        payload["api_detail"] = detail
    return _dumps(payload)


async def _fetch_comment(comment_id: int, as_user: str, account: str) -> tuple[dict | None, str]:
    """Relit un commentaire. Retourne (commentaire, message d'erreur pret a rendre)."""
    try:
        raw = await kb_call("getComment", {"comment_id": comment_id}, as_user=as_user)
    except Exception as exc:
        if _is_permission_error(exc):
            return None, _not_found_or_forbidden("comment", comment_id, account, str(exc))
        raise
    if not raw:
        return None, _not_found_or_forbidden("comment", comment_id, account)
    return raw, ""


def _comment_denied(action: str, comment_id: int, raw: dict, account: str, detail: str = "") -> str:
    """Message d'echec explicite quand Kanboard refuse une ecriture sur commentaire.

    Kanboard verifie l'auteur : hors admin, un compte ne peut modifier ou
    supprimer que ses propres commentaires. L'API se contente d'un `false`,
    qui ne dit pas lequel des deux comptes du connecteur a poste le
    commentaire - indebogable cote client. On nomme donc l'auteur et,
    quand un second compte existe, celui a reessayer.

    L'auteur est decode une fois, puis reutilise dans comment_author et
    hint. Les details de transport dans api_detail restent bruts.
    """
    author = _text(raw.get("name") or raw.get("username") or f"user_id={raw.get('user_id')}")
    hint = (
        f"Kanboard n'autorise que l'auteur du commentaire (ou un administrateur) "
        f"a le {action}. Le commentaire #{comment_id} a ete poste par {author}, "
        f"l'appel a ete signe par le compte '{account}'. "
    )
    if KANBOARD_USER_ALT and KANBOARD_TOKEN_ALT:
        hint += f"Reessayer avec as_user='{_other_account(account)}'."
    else:
        # Sans second compte configure, suggerer "l'autre compte" enverrait
        # sur une fausse piste : il n'existe pas.
        hint += (
            "Aucun second compte n'est configure (KANBOARD_USER_ALT/"
            "KANBOARD_TOKEN_ALT vides) : le commentaire appartient a un autre "
            "utilisateur Kanboard, hors de portee de ce connecteur."
        )
    payload: dict[str, Any] = {
        "success": False,
        "error": "permission_denied",
        "action": action,
        "comment_id": comment_id,
        "attempted_as_user": account,
        "comment_author": author,
        "comment_author_user_id": raw.get("user_id"),
        "hint": hint,
    }
    if detail:
        payload["api_detail"] = detail
    return _dumps(payload)


@mcp.tool()
async def kanboard_list_comments(task_id: int = 0, as_user: str = "") -> str:
    """Liste tous les commentaires d'une tache, contenu integral.

    C'est le tool de reference pour recuperer le texte complet d'un
    commentaire avant reecriture : contrairement a kanboard_get_task, il
    n'applique aucune troncature.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - as_user : compte de lecture ("" = defaut, "primary"/"agent")

    Retourne pour chaque commentaire : id, task_id, user_id, username, name,
    date_creation, date_modification, comment.

    username, name et comment sont decodes une fois. Les identifiants,
    les dates et les erreurs de transport ne sont pas decodes.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        try:
            comments = await kb_call("getAllComments", {"task_id": task_id}, as_user=as_user)
        except Exception as exc:
            if _is_permission_error(exc):
                _, _, account = _get_credentials(as_user)
                return _not_found_or_forbidden("task", task_id, account, str(exc))
            raise
        return _dumps([_comment_row(c) for c in (comments or [])])
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_get_comment(comment_id: int = 0, as_user: str = "") -> str:
    """Detail d'un commentaire par son ID, contenu integral (aucune troncature).

    Parametres :
    - comment_id : ID du commentaire (obligatoire)
    - as_user : compte de lecture ("" = defaut, "primary"/"agent")

    Retourne : id, task_id, user_id, username, name, date_creation,
    date_modification, comment.

    username, name et comment sont decodes une fois. Les identifiants,
    les dates et les erreurs de transport ne sont pas decodes.
    """
    if not comment_id:
        return "Erreur : comment_id est obligatoire."
    try:
        _, _, account = _get_credentials(as_user)
        raw, err = await _fetch_comment(comment_id, as_user, account)
        if err:
            return err
        return _dumps(_comment_row(raw))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_update_comment(
    comment_id: int = 0,
    comment: str = "",
    as_user: str = "",
) -> str:
    """Modifie le texte d'un commentaire existant.

    Confirmation utilisateur requise avant execution.

    Kanboard ne versionne pas les commentaires : la reecriture ecrase
    definitivement l'ancien texte. Il est donc relu avant l'ecriture et
    renvoye dans `previous_comment`, seule trace restante.

    Kanboard verifie l'auteur : hors administrateur, un compte ne peut
    modifier que ses propres commentaires. Un commentaire poste par
    'primary' n'est donc pas modifiable via 'agent', et inversement.

    Parametres :
    - comment_id : ID du commentaire (obligatoire)
    - comment : nouveau texte du commentaire (obligatoire)
    - as_user : compte qui modifie ("" = defaut, "primary"/"agent")

    Retourne l'ancien texte (`previous_comment`) et le nouveau.

    previous_comment et l'auteur d'un refus (comment_author et hint)
    sont decodes une fois. Le nouveau texte envoye et rendu dans
    new_comment ainsi que les erreurs de transport restent bruts.
    """
    if not comment_id:
        return "Erreur : comment_id est obligatoire."
    if not comment:
        return "Erreur : comment est obligatoire."
    try:
        # Compte *effectif*, seul nom qui puisse figurer dans le diagnostic.
        # Depuis la 0.16.0 il ne peut plus differer du compte demande : un "agent"
        # non configure leve avant d'arriver ici, au lieu de signer en primary.
        _, _, account = _get_credentials(as_user)

        # Relecture prealable : sans elle, l'ancien texte est perdu sans recours.
        raw, err = await _fetch_comment(comment_id, as_user, account)
        if err:
            return err
        previous = _text(raw.get("comment"))

        try:
            # L'API attend `content` ; le parametre expose s'appelle `comment`
            # pour rester aligne sur kanboard_add_comment.
            success = await kb_call("updateComment", {
                "id": comment_id,
                "content": comment,
            }, as_user=as_user)
        except Exception as exc:
            if _is_permission_error(exc):
                return _comment_denied("modifier", comment_id, raw, account, str(exc))
            raise

        if not success:
            return _comment_denied("modifier", comment_id, raw, account)

        return _dumps({
            "success": True,
            "comment_id": comment_id,
            "task_id": raw.get("task_id"),
            "as_user": account,
            "previous_comment": previous,
            "new_comment": comment,
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_remove_comment(comment_id: int = 0, as_user: str = "") -> str:
    """Supprime definitivement un commentaire.

    ATTENTION : suppression irreversible. Kanboard ne conserve aucune
    copie ; le contenu supprime est renvoye dans la reponse pour qu'il
    reste recuperable dans l'historique de conversation.

    Confirmation utilisateur requise avant execution.

    Kanboard verifie l'auteur : hors administrateur, un compte ne peut
    supprimer que ses propres commentaires.

    Parametres :
    - comment_id : ID du commentaire (obligatoire)
    - as_user : compte qui supprime ("" = defaut, "primary"/"agent")

    Retourne le contenu supprime (`deleted_comment`) et sa tache d'origine.

    deleted_comment, deleted_comment_author et l'auteur d'un refus
    (comment_author et hint) sont decodes une fois. Les parametres envoyes,
    champs techniques et erreurs de transport ne sont pas decodes.
    """
    if not comment_id:
        return "Erreur : comment_id est obligatoire."
    try:
        # Compte effectif (cf. kanboard_update_comment).
        _, _, account = _get_credentials(as_user)

        raw, err = await _fetch_comment(comment_id, as_user, account)
        if err:
            return err

        try:
            success = await kb_call(
                "removeComment", {"comment_id": comment_id}, as_user=as_user
            )
        except Exception as exc:
            if _is_permission_error(exc):
                return _comment_denied("supprimer", comment_id, raw, account, str(exc))
            raise

        if not success:
            return _comment_denied("supprimer", comment_id, raw, account)

        return _dumps({
            "success": True,
            "comment_id": comment_id,
            "task_id": raw.get("task_id"),
            "as_user": account,
            "action": "deleted",
            "deleted_comment": _text(raw.get("comment")),
            "deleted_comment_author": _text(raw.get("name") or raw.get("username", "")),
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Liens entre taches
# ---------------------------------------------------------------------------


def _link_row(raw: dict, types: dict[int, str] | None = None) -> dict:
    """Projette un lien brut de getAllTaskLinks sur des champs exploitables.

    Kanboard renvoie ici un alias trompeur : la colonne `task_id` porte en
    realite l'identifiant de la tache LIEE (`opposite_task_id AS task_id`), pas
    celui de la tache interrogee. On la reexpose sous `linked_task_id` pour que
    personne n'ait a connaitre ce detail.

    Les autres champs (titre, colonne, projet, echeance) decrivent eux aussi la
    tache liee : c'est ce qui rend la sortie lisible sans second appel.

    relation, title, column et project_name sont decodes une fois, y
    compris la relation issue de types. Les identifiants, le statut et
    la date ne subissent aucun decodage HTML.
    """
    link_id = _int_or_none(raw.get("link_id"))
    row = {
        # Identifiant de la LIGNE de lien : c'est lui qu'attend removeTaskLink,
        # surtout pas un task_id.
        "task_link_id": _int_or_none(raw.get("id")),
        "linked_task_id": _int_or_none(raw.get("task_id")),
        "relation": _text(raw.get("label")),
        "title": _text(raw.get("title")),
        "status": "open" if str(raw.get("is_active")) == "1" else "closed",
        "column": _text(raw.get("column_title")),
        "project_id": _int_or_none(raw.get("project_id")),
        "project_name": _text(raw.get("project_name")),
        "date_due": _ts_to_date(raw.get("date_due")),
    }
    if link_id is not None:
        row["link_id"] = link_id
        if types and link_id in types:
            row["relation"] = _text(types[link_id])
    return row


def _int_or_none(value: Any) -> int | None:
    """Convertit un identifiant Kanboard (souvent une chaine) en entier."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@mcp.tool()
async def kanboard_list_link_types(as_user: str = "") -> str:
    """Liste les types de liens entre taches configures sur l'instance.

    A appeler AVANT kanboard_create_task_link : les identifiants de types de
    liens varient d'une instance a l'autre selon la langue et les
    personnalisations. Ne jamais coder un id en dur - le resoudre ici par son
    libelle.

    Les liens de jalon sont une paire : un type « etape importante » et son
    oppose « vise l'etape importante ». Le libelle exact depend de la langue de
    l'instance.

    Parametres :
    - as_user : compte de lecture ("" = defaut, "primary"/"agent")

    Retourne pour chaque type : id, label, opposite_id et opposite_label (le
    libelle de l'oppose, resolu ici pour eviter un second appel).

    label et opposite_label sont decodes une fois. Les identifiants et
    les valeurs null des relations absentes ne sont pas modifies.
    """
    try:
        links = await kb_call("getAllLinks", as_user=as_user)
        by_id = {_int_or_none(link.get("id")): link.get("label") for link in (links or [])}
        rows = []
        for link in links or []:
            opposite_id = _int_or_none(link.get("opposite_id"))
            rows.append({
                "id": _int_or_none(link.get("id")),
                "label": _text(link.get("label")),
                # opposite_id vaut 0 pour un type symetrique (« est lie a »),
                # qui est son propre oppose : pas de libelle a resoudre.
                "opposite_id": opposite_id or None,
                "opposite_label": _text(by_id[opposite_id]) if opposite_id and opposite_id in by_id else None,
            })
        return _dumps({"count": len(rows), "link_types": rows})
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_list_task_links(task_id: int = 0, as_user: str = "") -> str:
    """Liste les liens d'une tache, avec le titre et le statut des taches liees.

    La sortie est exploitable sans second appel : chaque lien porte le titre, le
    statut (open/closed), la colonne, le projet et l'echeance de la tache liee.

    Parametres :
    - task_id : ID de la tache (obligatoire)
    - as_user : compte de lecture ("" = defaut, "primary"/"agent")

    Retourne pour chaque lien : task_link_id (identifiant de la LIGNE de lien,
    a passer a kanboard_remove_task_link), linked_task_id, relation, title,
    status, column, project_id, project_name, date_due.

    relation, title, column et project_name sont decodes une fois, meme
    si getAllLinks est indisponible. Les champs techniques et les erreurs
    de transport ne sont pas decodes.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        try:
            links = await kb_call("getAllTaskLinks", {"task_id": task_id}, as_user=as_user)
        except Exception as exc:
            if _is_permission_error(exc):
                _, _, account = _get_credentials(as_user)
                return _not_found_or_forbidden("task", task_id, account, str(exc))
            raise

        # Les libelles sont resolus depuis getAllLinks : un echec ici ne doit pas
        # faire perdre la liste des liens, on garde alors le libelle du lien.
        types: dict[int, str] = {}
        try:
            for link in (await kb_call("getAllLinks", as_user=as_user)) or []:
                lid = _int_or_none(link.get("id"))
                if lid is not None:
                    types[lid] = link.get("label")
        except Exception:
            logger.warning("getAllLinks indisponible : libelles issus des liens")

        rows = [_link_row(link, types) for link in (links or [])]
        return _dumps({"task_id": task_id, "count": len(rows), "links": rows})
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_create_task_link(
    task_id: int = 0,
    opposite_task_id: int = 0,
    link_id: int = 0,
    as_user: str = "",
) -> str:
    """Cree un lien entre deux taches (rattachement a un jalon, blocage...).

    Confirmation utilisateur requise avant execution.

    Kanboard cree AUTOMATIQUEMENT le lien inverse sur la tache opposee : la
    relation apparait des deux cotes apres un seul appel. Ce n'est pas un
    doublon, et il ne faut surtout pas rappeler ce tool en sens inverse.

    Le link_id doit etre resolu avec kanboard_list_link_types : les
    identifiants varient d'une instance a l'autre. Ne jamais en coder un en dur.

    Parametres :
    - task_id : ID de la tache source (obligatoire)
    - opposite_task_id : ID de la tache liee (obligatoire)
    - link_id : ID du type de lien (obligatoire, cf. kanboard_list_link_types)
    - as_user : compte qui cree le lien ("" = defaut, "primary"/"agent")

    Retourne l'ID du lien cree.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    if not opposite_task_id:
        return "Erreur : opposite_task_id est obligatoire."
    if not link_id:
        return (
            "Erreur : link_id est obligatoire. Recuperer l'identifiant du type "
            "de lien avec kanboard_list_link_types (il varie selon l'instance)."
        )
    if task_id == opposite_task_id:
        return "Erreur : une tache ne peut pas etre liee a elle-meme."
    try:
        try:
            created = await kb_call("createTaskLink", {
                "task_id": task_id,
                "opposite_task_id": opposite_task_id,
                "link_id": link_id,
            }, as_user=as_user)
        except Exception as exc:
            if _is_permission_error(exc):
                _, _, account = _get_credentials(as_user)
                return _not_found_or_forbidden("task", task_id, account, str(exc))
            raise

        if not created:
            return _dumps({
                "success": False,
                "task_id": task_id,
                "opposite_task_id": opposite_task_id,
                "link_id": link_id,
                "hint": (
                    "Kanboard a refuse la creation sans lever d'erreur. Verifier "
                    "que les deux taches existent et que le link_id figure bien "
                    "dans kanboard_list_link_types."
                ),
            })

        return _dumps({
            "success": True,
            "task_link_id": _int_or_none(created),
            **_creation_without_author(as_user),
            "task_id": task_id,
            "opposite_task_id": opposite_task_id,
            "link_id": link_id,
            "as_user": _get_credentials(as_user)[2],
            "note": ("Le lien inverse a ete cree automatiquement sur la tache "
                     f"#{opposite_task_id} : ce n'est pas un doublon."),
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_remove_task_link(task_link_id: int = 0, as_user: str = "") -> str:
    """Supprime un lien entre deux taches.

    ATTENTION : suppression irreversible. Kanboard retire le lien ET son
    inverse sur la tache opposee, sans confirmation ni historique.

    Confirmation utilisateur requise avant execution.

    Le parametre est l'identifiant de la LIGNE de lien (`task_link_id` rendu par
    kanboard_list_task_links), PAS un task_id : passer un identifiant de tache
    supprimerait un lien sans rapport, ou echouerait silencieusement.

    Parametres :
    - task_link_id : ID du lien (obligatoire, cf. kanboard_list_task_links)
    - as_user : compte qui supprime ("" = defaut, "primary"/"agent")

    Retourne la confirmation de suppression.
    """
    if not task_link_id:
        return (
            "Erreur : task_link_id est obligatoire. Le recuperer avec "
            "kanboard_list_task_links (champ task_link_id, pas linked_task_id)."
        )
    try:
        removed = await kb_call(
            "removeTaskLink", {"task_link_id": task_link_id}, as_user=as_user
        )
        return _dumps({
            "success": bool(removed),
            "task_link_id": task_link_id,
            "as_user": _get_credentials(as_user)[2],
            "note": ("Le lien inverse a ete retire en meme temps."
                     if removed else
                     "Kanboard a refuse la suppression : verifier le task_link_id."),
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Diagnostic (multi-compte)
# ---------------------------------------------------------------------------


async def _visible_projects(as_user: str) -> tuple[dict[str, str], str]:
    """Retourne ({project_id: nom}, erreur) visibles par un compte.

    Passe par getMyProjectsList, qui ne demande aucun droit admin - contrairement
    a getAllProjects, refuse aux comptes non-administrateurs.

    Les noms sont decodes une fois ; les identifiants et erreurs de
    transport ne sont pas decodes.
    """
    try:
        raw = await kb_call("getMyProjectsList", as_user=as_user)
        return {str(k): _text(v) for k, v in (raw or {}).items()}, ""
    except Exception as exc:
        return {}, _format_error(exc)


@mcp.tool()
async def kanboard_check_project_access(project_id: int = 0, limit: int = DEFAULT_LIMIT) -> str:
    """Diagnostic : quels projets chaque compte configure peut-il atteindre.

    A lancer AVANT une ecriture plutot que de decouvrir un 403 au moment de
    poster : les deux comptes n'ont pas le meme perimetre, et un compte agent
    non-admin n'est rattache qu'a une partie des projets.

    Parametres :
    - project_id : verifier un projet precis (0 = comparer les perimetres)
    - limit : nombre max de projets listes dans les ecarts (defaut 20, max 100)

    Retourne, par compte : nombre de projets visibles, droits admin
    (getAllProjects passe ou non), et les projets visibles par un seul des deux
    comptes. Avec project_id : `writable_by`, la liste des comptes utilisables
    comme as_user pour ce projet.

    project_name et les noms de only_primary et only_agent sont decodes
    une fois. Les identifiants, comptes et erreurs de transport ne sont
    pas decodes.
    """
    try:
        accounts = ["primary"]
        if KANBOARD_USER_ALT and KANBOARD_TOKEN_ALT:
            accounts.append("agent")

        report: dict[str, Any] = {"accounts_configured": accounts}
        visible: dict[str, dict[str, str]] = {}

        for acc in accounts:
            projects, err = await _visible_projects(acc)
            visible[acc] = projects
            entry: dict[str, Any] = {"visible_projects": len(projects)}
            if err:
                entry["error"] = err
            # Le droit admin change la portee de list_projects et search_tasks :
            # sans lui, elles retombent sur les projets dont le compte est membre.
            try:
                await kb_call("getAllProjects", as_user=acc)
                entry["is_admin"] = True
            except Exception as exc:
                entry["is_admin"] = False if _is_permission_error(exc) else None
            report[acc] = entry

        if project_id:
            pid = str(project_id)
            writable = [acc for acc in accounts if pid in visible.get(acc, {})]
            name = next((visible[acc][pid] for acc in writable), "")
            report["project_id"] = project_id
            report["project_name"] = name
            report["writable_by"] = writable
            if not writable:
                report["hint"] = (
                    f"Projet #{project_id} hors de portee de tous les comptes configures. "
                    "Rattacher le compte au projet (kanboard_assign_project_user) "
                    "ou verifier l'ID."
                )
            elif len(writable) < len(accounts):
                missing = [a for a in accounts if a not in writable]
                report["hint"] = (
                    f"Projet #{project_id} accessible uniquement via as_user="
                    f"'{writable[0]}'. Compte(s) sans acces : {', '.join(missing)} "
                    "-> une ecriture y prendrait un 403."
                )
        elif len(accounts) == 2:
            only_primary = sorted(set(visible["primary"]) - set(visible["agent"]))
            only_agent = sorted(set(visible["agent"]) - set(visible["primary"]))
            capped = _clamp_limit(limit)
            report["only_primary_count"] = len(only_primary)
            report["only_agent_count"] = len(only_agent)
            report["only_primary"] = [
                {"id": p, "name": visible["primary"][p]} for p in only_primary[:capped]
            ]
            report["only_agent"] = [
                {"id": p, "name": visible["agent"][p]} for p in only_agent[:capped]
            ]
            # Meme regle que partout : une liste coupee doit le dire.
            if len(only_primary) > capped or len(only_agent) > capped:
                report["truncated"] = True

        return _dumps(report)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_who_am_i(as_user: str = "") -> str:
    """Diagnostic : affiche l'identite de l'utilisateur authentifie pour un compte.

    Utile pour verifier que l'authentification fonctionne correctement
    et que le bon user_id est resolu pour les fonctions my_dashboard
    et my_overdue.

    Parametres :
    - as_user : "primary" (humain), "agent" (assistant IA), "" (defaut configure)

    Retourne le user_id, username, nom, l'etat de la verification d'identite
    (`identity_verified`) et les erreurs eventuelles. Un compte dont l'identite
    n'est pas etablie est dit tel quel : c'est un diagnostic, le compte effectif
    est annonce, y compris lors du repli en lecture vers primary.

    Les username et name lus dans les reponses Kanboard sont decodes une fois.
    login_queried, les identifiants et les erreurs de transport ne sont pas
    decodes.
    """
    try:
        user, _, account = _get_credentials(as_user)
    except KanboardIdentityError as exc:
        # Ne rien emettre : le diagnostic d'un compte non configure ne doit pas
        # rendre l'identite du compte humain, ce que faisait le repli.
        return _dumps({
            "as_user_requested": as_user or "(default)",
            "effective_account": _resolve_account(as_user),
            "identity_verified": False,
            "identity_error": str(exc),
        })
    results: dict[str, Any] = {"version": SERVER_VERSION, "as_user_requested": as_user or "(default)", "effective_account": account}

    # Essai getMe
    try:
        me = await kb_call("getMe", as_user=account)
        results["getMe"] = {
            "ok": True,
            "user_id": me.get("id") if me else None,
            "username": _text(me.get("username")) if me else None,
            "name": _text(me.get("name")) if me else None,
        }
    except Exception as e:
        results["getMe"] = {"ok": False, "error": str(e)}

    # Essai getUserByLoginName
    try:
        u = await kb_call("getUserByLoginName", {"login": user}, as_user=account)
        results["getUserByLoginName"] = {
            "ok": True,
            "login_queried": user,
            "user_id": u.get("id") if u else None,
            "username": _text(u.get("username")) if u else None,
        }
    except Exception as e:
        results["getUserByLoginName"] = {"ok": False, "login_queried": user, "error": str(e)}

    # Resultat final
    uid, err = await _get_my_user_id(as_user=account)
    results["resolved_user_id"] = uid
    if err:
        results["resolution_error"] = err

    # Ce que vaut cette identite pour une ecriture : c'est la question utile.
    try:
        identity = await _ensure_account_identity(account)
        results["identity_verified"] = bool(identity.get("verified"))
        if identity.get("note"):
            results["identity_note"] = identity["note"]
    except KanboardIdentityError as exc:
        results["identity_verified"] = False
        results["identity_error"] = str(exc)

    return _dumps(results)


@mcp.tool()
async def kanboard_list_accounts() -> str:
    """Liste les comptes Kanboard configures sur ce MCP (multi-compte).

    Retourne pour chaque compte :
    - login configure (KANBOARD_USER ou KANBOARD_USER_ALT)
    - presence du token
    - identite resolue cote Kanboard (user_id, name) si possible
    - statut (configured / fallback_to_primary / unconfigured)

    Indique aussi le compte par defaut (KANBOARD_DEFAULT_AS_USER).

    resolved_name utilise le nom decode une fois a la resolution. Les
    logins configures, identifiants, URL et erreurs ne sont pas decodes.
    """
    accounts: dict[str, dict[str, Any]] = {}

    # Primary
    primary_status = "configured" if (KANBOARD_USER and KANBOARD_TOKEN) else "unconfigured"
    primary_info: dict[str, Any] = {
        "login": KANBOARD_USER,
        "token_set": bool(KANBOARD_TOKEN),
        "status": primary_status,
    }
    if primary_status == "configured":
        try:
            uid, err = await _get_my_user_id(as_user="primary")
            if uid > 0:
                primary_info["resolved_user_id"] = uid
                primary_info["resolved_name"] = _my_name_cache.get("primary", "")
            elif err:
                primary_info["resolution_error"] = err
        except Exception as e:
            primary_info["resolution_error"] = str(e)
    accounts["primary"] = primary_info

    # Agent
    if KANBOARD_USER_ALT and KANBOARD_TOKEN_ALT:
        agent_info: dict[str, Any] = {
            "login": KANBOARD_USER_ALT,
            "token_set": True,
            "status": "configured",
        }
        try:
            uid, err = await _get_my_user_id(as_user="agent")
            if uid > 0:
                agent_info["resolved_user_id"] = uid
                agent_info["resolved_name"] = _my_name_cache.get("agent", "")
            elif err:
                agent_info["resolution_error"] = err
        except Exception as e:
            agent_info["resolution_error"] = str(e)
        accounts["agent"] = agent_info
    else:
        accounts["agent"] = {
            "login": KANBOARD_USER_ALT or "(non configure)",
            "token_set": bool(KANBOARD_TOKEN_ALT),
            "status": "read_fallback_only",
            "note": ("Le compte 'agent' n'est pas configure. Les lectures utilisent primary ; "
                     "les ecritures agent sont refusees. Pour activer un compte separe : definir "
                     "KANBOARD_USER_ALT et KANBOARD_TOKEN_ALT."),
        }

    return _dumps({
        "kanboard_url": KANBOARD_URL,
        "default_as_user": KANBOARD_DEFAULT_AS_USER,
        "accounts": accounts,
    })


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------


def main() -> None:
    """Point d'entree pour le serveur MCP Kanboard."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
