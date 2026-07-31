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
import json
import logging
import os
import unicodedata
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

KANBOARD_URL = os.environ.get("KANBOARD_URL", "")

# --- Comptes Kanboard (multi-compte) ---------------------------------------
# Le serveur supporte 2 jeux de credentials simultanement :
#   - "primary" : compte humain principal — defaut pour la lecture
#   - "agent"   : compte dedie a l'assistant IA — defaut pour les ecritures
# Si KANBOARD_USER_ALT/KANBOARD_TOKEN_ALT sont vides, "agent" retombe sur "primary"
# (retrocompat totale avec une config a un seul compte).
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

# Caches de resolution d'IDs (evite N appels API par requete) — clefs par compte
_user_cache: dict[str, dict[int, str]] = {"primary": {}, "agent": {}}
_my_id_cache: dict[str, int] = {}              # as_user -> user_id resolu
_my_name_cache: dict[str, str] = {}            # as_user -> display name
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


def _get_credentials(as_user: str) -> tuple[str, str, str]:
    """Retourne (user, token, effective_account) pour le compte demande.

    Si "agent" est demande mais ALT est vide, fallback transparent sur primary.
    """
    account = _resolve_account(as_user)
    if account == "agent":
        if KANBOARD_USER_ALT and KANBOARD_TOKEN_ALT:
            return KANBOARD_USER_ALT, KANBOARD_TOKEN_ALT, "agent"
        # Fallback retrocompat
        return KANBOARD_USER, KANBOARD_TOKEN, "primary"
    return KANBOARD_USER, KANBOARD_TOKEN, "primary"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def kb_call(method: str, params: dict | None = None, as_user: str = "") -> Any:
    """Appel JSON-RPC 2.0 a l'API Kanboard avec retry et backoff exponentiel.

    Parametres :
    - method : nom de la methode JSON-RPC Kanboard
    - params : parametres de la methode (dict)
    - as_user : compte a utiliser ("primary" | "agent" | "" pour defaut)

    Retente automatiquement sur les erreurs réseau (ConnectError, ReadTimeout)
    et les erreurs HTTP 5xx. N'effectue pas de retry sur les erreurs 4xx.
    """
    global _request_id
    _request_id += 1

    user, token, account = _get_credentials(as_user)
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
                data = resp.json()

            if "error" in data and data["error"] is not None:
                err = data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise Exception(f"Kanboard API error: {msg}")
            return data.get("result")

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry RPC %s dans %.1fs : %s", method, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry RPC %s (HTTP %d) dans %.1fs", method, exc.response.status_code, delay)
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


def _format_error(exc: Exception) -> str:
    """Formate une erreur en message lisible."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "Erreur 401 : authentification Kanboard invalide."
        if status == 403:
            return "Erreur 403 : permissions insuffisantes."
        if status == 404:
            return "Erreur 404 : endpoint Kanboard introuvable. Verifier KANBOARD_URL."
        return f"Erreur HTTP {status} : {exc.response.text[:500]}"
    return f"Erreur : {exc}"


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


def _fold(value: Any) -> str:
    """Replie une chaine en minuscules sans accents, pour comparaison souple.

    Les noms de projets Kanboard sont saisis a la main ("Pilotage",
    "Référentiel…") : sans repli, un filtre tape sans accent ne trouverait rien.
    """
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


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


async def _get_user_name(user_id: Any, as_user: str = "") -> str:
    """Resout un user_id en nom d'utilisateur (avec cache par compte)."""
    try:
        uid = int(user_id or 0)
    except (ValueError, TypeError):
        return ""
    if uid <= 0:
        return ""
    account = _resolve_account(as_user)
    bucket = _user_cache.setdefault(account, {})
    if uid in bucket:
        return bucket[uid]
    try:
        user = await kb_call("getUser", {"user_id": uid}, as_user=account)
        name = (user.get("name") or user.get("username", "")) if user else ""
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
            _column_cache[pid] = {int(c["id"]): c.get("title", "") for c in (cols or [])}
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
            _swimlane_cache[pid] = {int(s["id"]): s.get("name", "") for s in (sws or [])}
        except Exception:
            _swimlane_cache[pid] = {}
    return _swimlane_cache.get(pid, {}).get(sid, "")


async def _get_my_user_id(as_user: str = "") -> tuple[int, str]:
    """Retourne (user_id, erreur) de l'utilisateur authentifie pour ce compte.

    Retourne (uid > 0, "") en succes, ou (0, message_erreur) en echec.
    Essaie getMe() d'abord (tokens personnels), puis getUserByLoginName()
    en fallback (admin), puis getAllUsers() en dernier recours.

    Cache par compte effectif (primary/agent).
    """
    user, _, account = _get_credentials(as_user)
    if account in _my_id_cache:
        return _my_id_cache[account], ""

    errors: list[str] = []

    # Essai 1 : getMe — fonctionne avec les tokens utilisateur personnels
    try:
        me = await kb_call("getMe", as_user=account)
        if me:
            uid = int(me.get("id", 0))
            if uid > 0:
                _my_id_cache[account] = uid
                _my_name_cache[account] = me.get("name") or me.get("username", "") or ""
                return uid, ""
    except Exception as e:
        errors.append(f"getMe: {e}")

    # Essai 2 : getUserByLoginName — fonctionne avec les tokens admin
    try:
        u = await kb_call("getUserByLoginName", {"login": user}, as_user=account)
        if u:
            uid = int(u.get("id", 0))
            if uid > 0:
                _my_id_cache[account] = uid
                _my_name_cache[account] = u.get("name") or u.get("username", "") or ""
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
                        _my_name_cache[account] = u.get("name") or u.get("username", "") or ""
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
    account = _resolve_account(as_user)
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
            # Projet visible mais inaccessible (403, timeout, etc.) — on saute
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


async def _task_summary(t: dict, as_user: str = "") -> dict:
    """Extrait les champs essentiels d'une tache, en resolvant les IDs en noms."""
    pid = t.get("project_id")

    # Assignee : privilegier les champs enrichis, sinon resoudre owner_id
    assignee = t.get("assignee_name") or t.get("assignee_username") or ""
    if not assignee:
        assignee = await _get_user_name(t.get("owner_id"), as_user=as_user)

    # Colonne
    col_name = t.get("column_name") or ""
    if not col_name:
        col_name = await _get_column_name(pid, t.get("column_id"))

    # Swimlane
    sw_name = t.get("swimlane_name") or ""
    if not sw_name:
        sw_name = await _get_swimlane_name(pid, t.get("swimlane_id"))

    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "project_id": pid,
        "project_name": t.get("project_name", ""),
        "column_name": col_name,
        "swimlane_name": sw_name,
        "assignee": assignee,
        "priority": t.get("priority"),
        "due_date": _ts_to_date(t.get("date_due")),
        "is_active": t.get("is_active"),
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
    """
    try:
        tasks, err = await _get_my_tasks(status_id=1, as_user=as_user)
        if err:
            return f"Erreur : {err}"
        by_project: dict[str, list] = {}
        for t in tasks:
            pname = t.get("project_name", f"Projet {t.get('project_id')}")
            by_project.setdefault(pname, []).append(await _task_summary(t, as_user=as_user))
        return _dumps({"as_user": _resolve_account(as_user), "task_count": len(tasks), "by_project": by_project})
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
      insensible a la casse et aux accents (ex. "pilotage" trouve
      "[Pilotage] Coordination")

    Retourne : total_available (avant pagination), offset, count, has_more,
    scope, et projects (id, nom, identifiant, description tronquee a 200
    caracteres, is_active, nb_open_tasks).

    `scope` vaut "all" (tous les projets de l'instance) ou "member" quand le
    compte n'est pas administrateur : la liste se limite alors aux projets dont
    il est membre, et n'est donc pas l'inventaire complet.
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
                "name": p.get("name"),
                "identifier": p.get("identifier", ""),
                "description": (p.get("description") or "")[:200],
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
) -> str:
    """Liste les taches d'un projet Kanboard.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - status : 1=actives (defaut), 0=fermees
    - limit : nombre max de resultats (defaut 20, max 100)

    Retourne : id, titre, colonne, assignee, priorite, date d'echeance.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        tasks = await kb_call("getAllTasks", {"project_id": project_id, "status_id": status})
        if not tasks:
            return _dumps([])
        results = [await _task_summary(t) for t in tasks[:_clamp_limit(limit)]]
        return _dumps(results)
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
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        task = await kb_call("getTask", {"task_id": task_id})
        if not task:
            return "Tache introuvable."

        # Recuperer commentaires et sous-taches en parallele
        comments_raw = await kb_call("getAllComments", {"task_id": task_id})
        subtasks_raw = await kb_call("getAllSubtasks", {"task_id": task_id})

        comments = []
        for c in (comments_raw or []):
            content = c.get("comment") or ""
            entry: dict[str, Any] = {
                "id": c.get("id"),
                "user": c.get("name") or c.get("username", ""),
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
                "title": s.get("title"),
                "assignee": s.get("name") or s.get("username", ""),
                "status": s.get("status_name", s.get("status")),
            })

        pid = task.get("project_id")

        # Resoudre les IDs en noms
        assignee = task.get("assignee_name") or task.get("assignee_username") or ""
        if not assignee:
            assignee = await _get_user_name(task.get("owner_id"))

        creator = task.get("creator_name") or task.get("creator_username") or ""
        if not creator:
            creator = await _get_user_name(task.get("creator_id"))

        col_name = task.get("column_name") or ""
        if not col_name:
            col_name = await _get_column_name(pid, task.get("column_id"))

        sw_name = task.get("swimlane_name") or ""
        if not sw_name:
            sw_name = await _get_swimlane_name(pid, task.get("swimlane_id"))

        detail = {
            "id": task.get("id"),
            "title": task.get("title"),
            "description": (task.get("description") or "")[:2000],
            "project_id": pid,
            "project_name": task.get("project_name", ""),
            "column_name": col_name,
            "swimlane_name": sw_name,
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
) -> str:
    """Recherche de taches par mot-cle dans tous les projets accessibles.

    Kanboard n'expose pas de recherche globale en JSON-RPC : le connecteur
    balaie les projets un par un et filtre cote client. Ce balayage s'arrete
    des que `limit` est atteint, donc une absence de resultat ne prouve rien
    tant que `projects_scanned` est inferieur a `projects_total`.

    Parametres :
    - query : texte a rechercher (insensible a la casse)
    - limit : nombre max de resultats (defaut 20, max 100)

    Retourne : count, scope, has_more, projects_scanned, projects_total, et
    results (taches dont le titre ou la description contient le texte).

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
        scanned = 0

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
                    results.append(await _task_summary(t))
                    if len(results) >= limit_val:
                        break
            if len(results) >= limit_val:
                break

        return _dumps({
            "count": len(results),
            "scope": scope,
            # Le balayage s'arrete a `limit` : sans ce drapeau, une recherche
            # coupee en cours de route se lit comme exhaustive.
            "has_more": scanned < len(active),
            "projects_scanned": scanned,
            "projects_total": len(active),
            "results": results,
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_my_overdue(as_user: str = "") -> str:
    """Liste mes taches en retard dans tous les projets.

    Retourne les taches actives assignees a l'utilisateur courant
    dont la date d'echeance est depassee, triees par anciennete.

    Parametres :
    - as_user : compte cible ("primary" = humain, "agent" = assistant IA, "" = defaut)
    """
    try:
        tasks, err = await _get_my_tasks(status_id=1, as_user=as_user)
        if err:
            return f"Erreur : {err}"
        now_ts = int(datetime.now().timestamp())
        overdue = []
        for t in tasks:
            due = t.get("date_due")
            if due and due != "0":
                try:
                    if int(due) < now_ts and int(due) > 0:
                        summary = await _task_summary(t, as_user=as_user)
                        summary["days_overdue"] = (now_ts - int(due)) // 86400
                        overdue.append(summary)
                except (ValueError, TypeError):
                    pass

        # Trier par date d'echeance croissante (plus ancien en premier)
        overdue.sort(key=lambda x: x.get("due_date", ""))
        return _dumps({"as_user": _resolve_account(as_user), "overdue_count": len(overdue), "tasks": overdue})
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Board
# ---------------------------------------------------------------------------


@mcp.tool()
async def kanboard_get_board(project_id: int = 0, tasks_per_column: int = 10) -> str:
    """Etat du tableau Kanboard d'un projet.

    Parametres :
    - project_id : ID du projet (obligatoire)
    - tasks_per_column : nombre max de cartes detaillees par colonne
      (defaut 10, max 100)

    Retourne les swimlanes et leurs colonnes ; chaque carte porte id, titre,
    assignee, echeance (due_date), priorite et categorie. Une colonne dont les
    cartes ont ete tronquees porte truncated=true avec shown/total.
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        board = await kb_call("getBoard", {"project_id": project_id})
        if not board:
            return "Tableau introuvable."

        per_column = _clamp_limit(tasks_per_column)
        result = []
        for swimlane in board:
            sw_name = swimlane.get("name", "Default")
            columns = []
            for col in swimlane.get("columns", []):
                tasks_in_col = col.get("tasks", [])
                shown = tasks_in_col[:per_column]  # Limiter pour les tokens
                entry = {
                    "id": col.get("id"),
                    "name": col.get("title", ""),
                    "task_count": len(tasks_in_col),
                    "tasks": [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                            "assignee": t.get("assignee_name", ""),
                            "due_date": _ts_to_date(t.get("date_due")),
                            "priority": t.get("priority"),
                            "category_name": t.get("category_name", ""),
                        }
                        for t in shown
                    ],
                }
                # Sans ce marqueur, une colonne tronquee est indiscernable d'une
                # colonne qui contient exactement per_column cartes.
                if len(shown) < len(tasks_in_col):
                    entry["truncated"] = True
                    entry["shown"] = len(shown)
                    entry["total"] = len(tasks_in_col)
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

        # Chercher la colonne par nom (insensible a la casse)
        target_col = None
        col_lower = column_name.lower()
        for c in columns:
            if (c.get("title") or "").lower() == col_lower:
                target_col = c
                break

        if not target_col:
            available = [c.get("title") for c in columns]
            return f"Colonne '{column_name}' introuvable. Colonnes disponibles : {available}"

        col_id = int(target_col["id"])

        # Resoudre le swimlane par nom si fourni
        if swimlane_name:
            swimlanes = await kb_call("getActiveSwimlanes", {"project_id": pid}, as_user=as_user)
            sw_lower = swimlane_name.lower()
            target_sw = None
            for s in (swimlanes or []):
                if (s.get("name") or "").lower() == sw_lower:
                    target_sw = s
                    break
            if not target_sw:
                available_sw = [s.get("name") for s in (swimlanes or [])]
                return f"Swimlane '{swimlane_name}' introuvable. Swimlanes disponibles : {available_sw}"
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
            return _dumps(result)
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
    - Recherche insensible a la casse dans les membres du projet (nom affiche ET login).
    - Erreur explicite si aucune correspondance ou ambiguite.
    - L'utilisateur doit etre membre du projet ; sinon erreur avec suggestion
      d'utiliser kanboard_assign_project_user.

    Exemples :
    - kanboard_assign_task(task_id=1234, user_id=4)             -> assigne user 4
    - kanboard_assign_task(task_id=1234, user_name="alice")     -> resolution auto
    - kanboard_assign_task(task_id=1234, user_id=0)             -> desassigne
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

            name_lower = user_name.lower().strip()
            matches: list[dict] = []
            for m in member_list:
                uid = m["id"]
                # Comparer login Kanboard
                if (m.get("username") or "").lower() == name_lower:
                    matches.append(m)
                    continue
                # Comparer nom affiche (appel getUser, cached)
                display = await _get_user_name(uid, as_user=as_user)
                if display.lower() == name_lower:
                    matches.append({**m, "display_name": display})

            if len(matches) == 0:
                member_names = []
                for m in member_list:
                    dn = await _get_user_name(m["id"], as_user=as_user)
                    member_names.append(dn or m.get("username", f"uid={m['id']}"))
                return (
                    f"Aucun membre du projet {pid} ne correspond a '{user_name}'. "
                    f"Membres disponibles : {member_names}"
                )
            if len(matches) > 1:
                return (
                    f"Ambiguite : '{user_name}' correspond a plusieurs membres : "
                    f"{[m.get('display_name') or m.get('username') for m in matches]}. "
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
        # Recuperer l'ID de l'utilisateur courant pour ce compte
        uid, err = await _get_my_user_id(as_user=as_user)
        if uid <= 0:
            return f"Erreur : impossible de recuperer l'utilisateur courant. {err}"

        comment_id = await kb_call("createComment", {
            "task_id": task_id,
            "user_id": uid,
            "content": comment,
        }, as_user=as_user)

        if comment_id:
            return _dumps({
                "success": True,
                "comment_id": comment_id,
                "task_id": task_id,
                "as_user": _resolve_account(as_user),
                "author_user_id": uid,
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

    La tache passe en statut inactif (is_active=0).
    Retourne True en cas de succes.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        success = await kb_call("closeTask", {"task_id": task_id}, as_user=as_user)
        if success:
            return _dumps({"success": True, "task_id": task_id, "action": "closed"})
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

    La tache passe en statut actif (is_active=1).
    Retourne True en cas de succes.
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        success = await kb_call("openTask", {"task_id": task_id}, as_user=as_user)
        if success:
            return _dumps({"success": True, "task_id": task_id, "action": "opened"})
        else:
            return "Echec de la reouverture de la tache."
    except Exception as exc:
        return _format_error(exc)


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
    - tags : liste de tags libres a associer (ex: ["urgent","backend"])
    - as_user : compte qui cree la tache ("" = defaut, "primary"/"agent")

    Retourne l'ID de la tache creee.
    """
    project_id = int(project_id) if project_id else 0
    if not project_id:
        return "Erreur : project_id est obligatoire."
    if not title:
        return "Erreur : title est obligatoire."
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

        # Resolve column name to ID if provided
        if column_name:
            columns = await kb_call("getColumns", {"project_id": project_id}, as_user=as_user)
            col_id = None
            for col in (columns or []):
                if col.get("title", "").lower().strip() == column_name.lower().strip():
                    col_id = int(col["id"])
                    break
            if col_id:
                params["column_id"] = col_id
            else:
                available = [c.get("title") for c in (columns or [])]
                return f"Colonne '{column_name}' introuvable. Colonnes disponibles : {available}"

        task_id = await kb_call("createTask", params, as_user=as_user)

        if task_id:
            return _dumps({"success": True, "task_id": task_id, "project_id": project_id, "title": title})
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

    Retourne True en cas de succes.
    """
    task_id = int(task_id) if task_id else 0
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        # Fetch task info before deletion for confirmation
        task = await kb_call("getTask", {"task_id": task_id}, as_user=as_user)
        if not task:
            return f"Tache #{task_id} introuvable."

        task_title = task.get("title", "?")
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
            return _dumps({"success": True, "project_id": project_id, "name": name})
        return "Echec de la creation du projet."
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def kanboard_get_project_metadata(project_id: int = 0) -> str:
    """Detail complet d'un projet Kanboard (description, dates, owner...).

    Plus complet que kanboard_list_projects (description integrale).

    Parametres :
    - project_id : ID du projet (obligatoire)
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        project = await kb_call("getProjectById", {"project_id": project_id})
        if not project:
            return f"Projet #{project_id} introuvable."
        result = {
            "id": project.get("id"),
            "name": project.get("name"),
            "identifier": project.get("identifier", ""),
            "description": project.get("description") or "",
            "is_active": project.get("is_active"),
            "is_public": project.get("is_public"),
            "is_private": project.get("is_private"),
            "owner_id": project.get("owner_id"),
            "start_date": project.get("start_date", ""),
            "end_date": project.get("end_date", ""),
            "date_created": _ts_to_datetime(project.get("last_modified")),
            "default_swimlane": project.get("default_swimlane", ""),
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
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        cols = await kb_call("getColumns", {"project_id": project_id})
        if not cols:
            return _dumps([])
        results = [{
            "id": c.get("id"),
            "title": c.get("title"),
            "position": c.get("position"),
            "task_limit": c.get("task_limit", 0),
            "description": (c.get("description") or "")[:500],
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
            return _dumps({"success": True, "column_id": col_id, "project_id": project_id, "title": title})
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
            "name": s.get("name"),
            "position": s.get("position", ""),
            "is_active": s.get("is_active", 1),
            "description": (s.get("description") or "")[:500],
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
            return _dumps({"success": True, "swimlane_id": sw_id, "project_id": project_id, "name": name})
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
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        cats = await kb_call("getAllCategories", {"project_id": project_id})
        if not cats:
            return _dumps([])
        results = [{
            "id": c.get("id"),
            "name": c.get("name"),
            "color_id": c.get("color_id", ""),
            "description": (c.get("description") or "")[:500],
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
            return _dumps({"success": True, "category_id": cat_id, "project_id": project_id, "name": name})
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

    ATTENTION — plafond impose par l'API : Kanboard ne rend jamais plus de 50
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
                "user": e.get("author_name") or e.get("author_username", ""),
                "task_id": task.get("id"),
                "task_title": task.get("title", ""),
                "summary": (e.get("event_title") or "")[:300],
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
    """
    if not project_id:
        return "Erreur : project_id est obligatoire."
    try:
        # Recuperer infos avant suppression pour le retour
        project = await kb_call("getProjectById", {"project_id": project_id}, as_user=as_user)
        name = project.get("name", "?") if project else "?"
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

    Retourne : {user_id: role}. Roles possibles : project-manager, project-member,
    project-viewer (Kanboard 1.2+).
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
            results = [{"user_id": int(uid), "name": name} for uid, name in members.items()]
        else:
            results = [{"user_id": m.get("id"), "name": m.get("name") or m.get("username", "")} for m in members]
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
            return _dumps({"success": True, "subtask_id": sid, "task_id": task_id, "title": title})
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
    """
    row = {k: raw.get(k) for k in _COMMENT_FIELDS}
    row["date_creation"] = _ts_to_datetime(raw.get("date_creation"))
    row["date_modification"] = _ts_to_datetime(raw.get("date_modification"))
    return row


def _other_account(account: str) -> str:
    """Retourne l'autre compte du connecteur ('primary' <-> 'agent')."""
    return "primary" if account == "agent" else "agent"


def _comment_denied(action: str, comment_id: int, raw: dict, account: str, detail: str = "") -> str:
    """Message d'echec explicite quand Kanboard refuse une ecriture sur commentaire.

    Kanboard verifie l'auteur : hors admin, un compte ne peut modifier ou
    supprimer que ses propres commentaires. L'API se contente d'un `false`,
    qui ne dit pas lequel des deux comptes du connecteur a poste le
    commentaire — indebogable cote client. On nomme donc l'auteur et,
    quand un second compte existe, celui a reessayer.
    """
    author = raw.get("name") or raw.get("username") or f"user_id={raw.get('user_id')}"
    hint = (
        f"Kanboard n'autorise que l'auteur du commentaire (ou un administrateur) "
        f"a le {action}. Le commentaire #{comment_id} a ete poste par {author}, "
        f"l'appel a ete signe par le compte '{account}'. "
    )
    if KANBOARD_USER_ALT and KANBOARD_TOKEN_ALT:
        hint += f"Reessayer avec as_user='{_other_account(account)}'."
    else:
        # Sans second compte configure, suggerer "l'autre compte" enverrait
        # sur une fausse piste : tout retombe sur primary.
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
    """
    if not task_id:
        return "Erreur : task_id est obligatoire."
    try:
        comments = await kb_call("getAllComments", {"task_id": task_id}, as_user=as_user)
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
    """
    if not comment_id:
        return "Erreur : comment_id est obligatoire."
    try:
        raw = await kb_call("getComment", {"comment_id": comment_id}, as_user=as_user)
        if not raw:
            return f"Commentaire #{comment_id} introuvable."
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
    """
    if not comment_id:
        return "Erreur : comment_id est obligatoire."
    if not comment:
        return "Erreur : comment est obligatoire."
    try:
        # Compte *effectif* : si "agent" n'est pas configure, l'ecriture est
        # signee par primary. Diagnostiquer sur le compte demande enverrait
        # sur une fausse piste ("reessayer avec primary" alors qu'on y etait).
        _, _, account = _get_credentials(as_user)

        # Relecture prealable : sans elle, l'ancien texte est perdu sans recours.
        raw = await kb_call("getComment", {"comment_id": comment_id}, as_user=as_user)
        if not raw:
            return f"Commentaire #{comment_id} introuvable."
        previous = raw.get("comment") or ""

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
    """
    if not comment_id:
        return "Erreur : comment_id est obligatoire."
    try:
        # Compte effectif (cf. kanboard_update_comment).
        _, _, account = _get_credentials(as_user)

        raw = await kb_call("getComment", {"comment_id": comment_id}, as_user=as_user)
        if not raw:
            return f"Commentaire #{comment_id} introuvable."

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
            "deleted_comment": raw.get("comment") or "",
            "deleted_comment_author": raw.get("name") or raw.get("username", ""),
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools -- Diagnostic (multi-compte)
# ---------------------------------------------------------------------------


async def _visible_projects(as_user: str) -> tuple[dict[str, str], str]:
    """Retourne ({project_id: nom}, erreur) visibles par un compte.

    Passe par getMyProjectsList, qui ne demande aucun droit admin — contrairement
    a getAllProjects, refuse aux comptes non-administrateurs.
    """
    try:
        raw = await kb_call("getMyProjectsList", as_user=as_user)
        return {str(k): str(v) for k, v in (raw or {}).items()}, ""
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

    Retourne le user_id, username, nom et les erreurs eventuelles.
    """
    user, _, account = _get_credentials(as_user)
    results: dict[str, Any] = {"as_user_requested": as_user or "(default)", "effective_account": account}

    # Essai getMe
    try:
        me = await kb_call("getMe", as_user=account)
        results["getMe"] = {
            "ok": True,
            "user_id": me.get("id") if me else None,
            "username": me.get("username") if me else None,
            "name": me.get("name") if me else None,
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
            "username": u.get("username") if u else None,
        }
    except Exception as e:
        results["getUserByLoginName"] = {"ok": False, "login_queried": user, "error": str(e)}

    # Resultat final
    uid, err = await _get_my_user_id(as_user=account)
    results["resolved_user_id"] = uid
    if err:
        results["resolution_error"] = err

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
            "status": "fallback_to_primary",
            "note": ("Le compte 'agent' n'est pas configure. Tout appel as_user='agent' "
                     "utilisera le compte primary. Pour activer un compte separe : definir "
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
