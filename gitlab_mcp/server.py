# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour GitLab (API REST v4).

Expose les ressources GitLab (projets, issues, merge requests, repository,
pipelines) via le protocole MCP pour Claude Desktop.

Instance : configurable via GITLAB_URL (ex: https://gitlab.com ou self-hosted)
Auth : PRIVATE-TOKEN header (Personal Access Token)
API : GitLab API v4
"""

import asyncio
import base64
import json
import logging
import os
import urllib.parse
from contextvars import ContextVar
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gitlab-mcp")

mcp = FastMCP("gitlab")

GITLAB_URL = os.environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")

HEADERS = {
    "PRIVATE-TOKEN": GITLAB_TOKEN,
    "Accept": "application/json",
}

# Limite maximale du contenu d'un fichier retourné (50 KB)
MAX_FILE_SIZE = 50 * 1024

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # secondes, doublé à chaque retry

# Méthodes rejouables une fois la requête émise. Rejouer une écriture après un
# timeout de lecture ou un 5xx crée des DOUBLONS (issues, commentaires, merge
# requests) car GitLab a pu traiter la requête avant de perdre la réponse.
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}

#: Ce que signifie chaque famille d'exception httpx quand elle arrive sans
#: message : c'est le cas courant (ReadError depuis une fin de flux, ReadTimeout
#: depuis un TimeoutError nu), et « Erreur : » suivi de rien était tout ce que
#: l'appelant recevait.
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

# MCP lance chaque appel d'outil dans une tâche distincte. Une tâche enfant
# hérite du contexte : l'identité de la tâche évite qu'elle hérite aussi des
# écritures du parent et les attribue à tort à son propre appel.
_applied_writes: ContextVar["tuple[asyncio.Task[Any], list[str]] | None"] = ContextVar(
    "gitlab_applied_writes", default=None,
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


def _append_sentence(base: str, extra: str) -> str:
    """Enchaîne deux phrases sans les souder : un détail GitLab finit rarement
    par un point, et « not found Etat de l'ecriture » se lit mal."""
    if not extra:
        return base
    base = base.rstrip()
    if base and base[-1] not in ".!?;:":
        base += "."
    return base + extra


def _write_state_message(write_state: str, label: str) -> str:
    """Précise le risque de rejouer la requête qui vient d'échouer."""
    if write_state == "non_appliquee":
        return (f" Écriture {label} non appliquée : rejouer cette requête est sans "
                "risque de doublon.")
    if write_state == "inconnu":
        return (f" État de l'écriture {label} inconnu : écriture non rejouée "
                "automatiquement, GitLab a pu la traiter avant de perdre la réponse ; "
                "relire l'objet avant de rejouer.")
    return ""


class GitLabAPIError(RuntimeError):
    """Réponse GitLab illisible, avec l'état de l'écriture concernée."""

    def __init__(self, status: int, message: str, label: str,
                 body_excerpt: str | None = None):
        self.status = status
        self.label = label
        self.body_excerpt = body_excerpt
        self.write_state = (
            "sans_objet" if label.split(" ", 1)[0] in _IDEMPOTENT_METHODS else "inconnu"
        )
        super().__init__(_append_sentence(
            message, _write_state_message(self.write_state, label)))


class GitLabTransportError(GitLabAPIError):
    """Échec réseau définitif, nommé même quand l'exception httpx est vide.

    Sans elle, un `httpx.ReadError` vide ou un `ReadTimeout` sans message se
    rendaient à l'appelant en « Erreur : » nu, et rien ne distinguait un jeton
    refusé d'une connexion coupée.
    """

    def __init__(self, kind: str, label: str, attempts: int,
                 replayable: bool, detail: str = ""):
        self.kind = kind
        self.attempts = attempts
        self.replayable = replayable
        self.detail = detail.strip() or _TRANSPORT_DEFAULT_DETAIL.get(
            kind, "échec du transport HTTP")
        idempotent = label.split(" ", 1)[0] in _IDEMPOTENT_METHODS
        self.status = 0
        self.label = label
        self.body_excerpt = None
        self.write_state = (
            "sans_objet" if idempotent
            else "non_appliquee" if replayable else "inconnu"
        )
        tail = _write_state_message(self.write_state, label)
        if self.write_state == "sans_objet":
            tail = (" Rejouer l'appel plus tard, et vérifier la disponibilité de "
                    "GitLab si l'erreur persiste.")
        count = "tentative" if attempts == 1 else "tentatives"
        RuntimeError.__init__(
            self, f"{kind} sur {label} ({self.detail}) après {attempts} {count}.{tail}")


def _parse_json_or_raise(resp: httpx.Response, label: str) -> Any:
    """Une page de proxy servie en HTTP 200 doit rester diagnostiquable.

    Sans ce filet, `resp.json()` remontait un `json.JSONDecodeError` dont le
    message parle de l'analyseur et jamais du serveur qui a répondu.
    """
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        body = resp.text or ""
        ct = resp.headers.get("content-type", "?")
        excerpt = body[:200] if body else "(corps vide)"
        logger.error(
            "Réponse non-JSON pour %s : HTTP=%d content-type=%s body_len=%d "
            "first_bytes=%r", label, resp.status_code, ct, len(body), excerpt,
        )
        raise GitLabAPIError(
            resp.status_code,
            f"Réponse non-JSON depuis GitLab (HTTP {resp.status_code}, "
            f"content-type={ct}, body={len(body)}o). Extrait : {excerpt!r}",
            label, body_excerpt=excerpt,
        ) from exc


def _encode_project_id(project_id: Any) -> str:
    """Encode l'identifiant de projet pour l'URL GitLab API.

    Accepte un entier (id numérique) ou une chaîne 'namespace/projet'
    qui sera encodée en URL (ex: 'mygroup/myproject' → 'mygroup%2Fmyproject').
    """
    s = str(project_id)
    # Si c'est un entier pur, pas besoin d'encoder
    if s.isdigit():
        return s
    return urllib.parse.quote(s, safe="")


async def _api_request(method: str, endpoint: str, **kwargs: Any) -> Any:
    """Appel HTTP GitLab API v4 avec rejeu et backoff exponentiel.

    Les lectures sont rejouées après toute erreur de transport, un HTTP 5xx ou
    un corps non-JSON. Une écriture n'est rejouée que si la connexion n'a jamais
    abouti (ConnectError, ConnectTimeout) : une fois la requête partie, GitLab a
    pu la traiter avant de perdre la réponse, et un rejeu poserait une issue,
    un commentaire ou une merge request en double.

    Limite : une écriture partie dont la réponse est perdue laisse un état que
    le client ne peut pas trancher. L'API v4 n'expose ni clef d'idempotence ni
    identifiant de requête à vérifier ; il faut relire l'objet avant de rejouer,
    et c'est exactement ce que veut dire « inconnu ». Le journal rappelle en
    outre les écritures déjà réussies dans le même appel d'outil.
    """
    url = f"{GITLAB_URL}/api/v4/{endpoint}"
    label = f"{method.upper()} {endpoint}"
    idempotent = method.upper() in _IDEMPOTENT_METHODS
    journal = _write_journal()
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=True, timeout=30) as client:
                logger.info("%s %s (attempt %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=HEADERS, **kwargs)
                resp.raise_for_status()
                # Certains endpoints retournent 204 No Content
                if resp.status_code == 204 or not resp.content:
                    data: Any = {}
                else:
                    data = _parse_json_or_raise(resp, label)
            if not idempotent:
                journal.append(label)
            return data
        except httpx.TransportError as exc:
            # Seul un échec de connexion prouve que GitLab n'a rien reçu. Après
            # l'envoi, rejouer une écriture peut créer un doublon. Toute la
            # famille est traitée : un ReadError, un RemoteProtocolError ou un
            # PoolTimeout traversaient sans rejeu ni habillage.
            never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            replayable = never_sent or idempotent
            last_exc = exc
            if replayable and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s in %.1fs (%s: %s)", method, url, delay,
                               type(exc).__name__, exc)
                await asyncio.sleep(delay)
            else:
                raise GitLabTransportError(
                    type(exc).__name__, label, attempt + 1, replayable, str(exc),
                ) from exc
        except httpx.HTTPStatusError as exc:
            # Les outils inspectent le statut HTTP : garder l'exception httpx
            # d'origine tout en donnant au formateur le contexte de l'écriture.
            exc.gl_label = label
            exc.gl_mutating = not idempotent
            if idempotent and exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    "Retry %s %s (HTTP %d) in %.1fs",
                    method, url, exc.response.status_code, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise
        except GitLabAPIError as exc:
            # Corps non-JSON sur une réponse 2xx : le plus souvent une page de
            # proxy. On rejoue comme une lecture, donc jamais une écriture, qui
            # a pu aboutir malgré la réponse illisible.
            last_exc = exc
            if idempotent and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s (corps non-JSON) in %.1fs", method, url, delay)
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


async def api_get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    """Appel GET à l'API GitLab avec retry."""
    return await _api_request("GET", endpoint, params=params or {})


async def api_post(endpoint: str, data: dict[str, Any] | None = None) -> Any:
    """Appel POST à l'API GitLab avec retry."""
    return await _api_request("POST", endpoint, json=data or {})


async def api_put(endpoint: str, data: dict[str, Any] | None = None) -> Any:
    """Appel PUT à l'API GitLab avec retry."""
    return await _api_request("PUT", endpoint, json=data or {})


async def api_delete(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    """Appel DELETE à l'API GitLab avec retry."""
    return await _api_request("DELETE", endpoint, params=params or {})


def _format_error(exc: Exception) -> str:
    """Nomme le défaut, l'état de l'écriture et les écritures déjà appliquées.

    Jamais un « Erreur : » nu : une exception sans message (httpx.ReadError,
    ReadTimeout, RemoteProtocolError) garde son type et le sens de sa famille,
    et l'appelant doit pouvoir distinguer une défaillance réseau d'un refus,
    puis décider quoi relire.
    """
    if isinstance(exc, GitLabTransportError):
        message = f"Erreur réseau {exc}"
    elif isinstance(exc, GitLabAPIError):
        message = f"Erreur GitLab : {exc}"
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            message = "Erreur 401 : token GitLab invalide ou manquant (GITLAB_TOKEN)."
        elif status == 403:
            message = "Erreur 403 : permissions insuffisantes pour cette ressource GitLab."
        elif status == 404:
            message = "Erreur 404 : ressource non trouvée dans GitLab (projet, issue, MR…)."
        elif status == 422:
            body = exc.response.text[:500].strip() or "sans message."
            message = f"Erreur 422 : données invalides. Détail : {body}"
        elif status == 500:
            body = exc.response.text[:500].strip() or "sans message."
            message = f"Erreur 500 : erreur interne GitLab. Détail : {body}"
        else:
            body = exc.response.text[:500].strip() or "sans message."
            message = f"Erreur HTTP {status} : {body}"
        if getattr(exc, "gl_mutating", False):
            label = getattr(exc, "gl_label", "inconnue")
            if status >= 500:
                message = _append_sentence(
                    message, _write_state_message("inconnu", label))
            elif status >= 400 and status not in (401, 403, 404):
                # 401, 403 et 404 gardent leur message exact : une auth refusée
                # n'écrit rien et n'a pas besoin qu'on en discute. Le reste
                # (422 sur un titre invalide, 400 d'un proxy) est refusé avant
                # exécution : le dire évite une relecture inutile, et un rejeu
                # à l'identique échouerait pareil.
                message = _append_sentence(
                    message,
                    f" Écriture {label} non appliquée : la requête a été refusée "
                    "avant exécution, corriger l'appel avant de rejouer.")
    elif isinstance(exc, json.JSONDecodeError):
        # Filet de sécurité : ne devrait plus se produire grâce à _parse_json_or_raise
        message = f"Erreur JSON GitLab : {exc}"
    elif isinstance(exc, httpx.TransportError):
        kind = type(exc).__name__
        detail = str(exc).strip() or _TRANSPORT_DEFAULT_DETAIL.get(
            kind, "échec du transport HTTP")
        message = f"Erreur réseau {kind} : {detail}."
    else:
        text = str(exc)
        message = (f"Erreur : {text}" if text.strip()
                   else f"Erreur {type(exc).__name__} : sans message.")
    journal = _write_journal()
    if journal:
        message = _append_sentence(
            message,
            f" Écritures déjà appliquées dans cet appel d'outil : {', '.join(journal)}. "
            "Relire les objets concernés avant de rejouer l'appel d'outil complet.")
    return message


def _dumps(data: Any) -> str:
    """Sérialise en JSON compact UTF-8 (économie de tokens)."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _user_summary(user: Any) -> dict[str, Any] | None:
    """Extrait les champs essentiels d'un objet utilisateur GitLab."""
    if not user:
        return None
    return {"id": user.get("id"), "username": user.get("username"), "name": user.get("name")}


# ---------------------------------------------------------------------------
# Tools — Projets
# ---------------------------------------------------------------------------


@mcp.tool()
async def gitlab_list_projects(
    search: Optional[str] = None,
    page: int = 1,
) -> str:
    """Liste les projets GitLab auxquels l'utilisateur appartient.

    Paramètres :
    - search : filtre optionnel sur le nom du projet
    - page : numéro de page (défaut 1, 20 projets par page)

    Retourne : id, name, path_with_namespace, description, web_url, last_activity_at.
    """
    params: dict[str, Any] = {
        "membership": "true",
        "per_page": 20,
        "page": page,
        "order_by": "last_activity_at",
        "sort": "desc",
    }
    if search:
        params["search"] = search
    try:
        data = await api_get("projects", params=params)
        result = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "path_with_namespace": p.get("path_with_namespace"),
                "description": p.get("description") or "",
                "web_url": p.get("web_url"),
                "last_activity_at": p.get("last_activity_at"),
            }
            for p in (data if isinstance(data, list) else [])
        ]
        return _dumps({"projects": result, "count": len(result), "page": page})
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_get_project(project_id: str) -> str:
    """Retourne les informations essentielles d'un projet GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet' (ex: 'mygroup/myproject')

    Retourne (champs filtrés pour économiser les tokens) : id, path_with_namespace,
    name, description, visibility, default_branch, web_url, http_url_to_repo,
    ssh_url_to_repo, archived, created_at, last_activity_at, namespace,
    open_issues_count, statistics, topics, license.
    """
    pid = _encode_project_id(project_id)
    try:
        data = await api_get(f"projects/{pid}")
        ns = data.get("namespace") or {}
        result = {
            "id": data.get("id"),
            "name": data.get("name"),
            "path_with_namespace": data.get("path_with_namespace"),
            "description": data.get("description"),
            "visibility": data.get("visibility"),
            "default_branch": data.get("default_branch"),
            "web_url": data.get("web_url"),
            "http_url_to_repo": data.get("http_url_to_repo"),
            "ssh_url_to_repo": data.get("ssh_url_to_repo"),
            "archived": data.get("archived"),
            "created_at": data.get("created_at"),
            "last_activity_at": data.get("last_activity_at"),
            "namespace": {"id": ns.get("id"), "name": ns.get("name"), "kind": ns.get("kind")},
            "open_issues_count": data.get("open_issues_count"),
            "statistics": data.get("statistics"),
            "topics": data.get("topics") or data.get("tag_list"),
            "license": (data.get("license") or {}).get("key") if isinstance(data.get("license"), dict) else data.get("license"),
        }
        return _dumps(result)
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Tools — Issues
# ---------------------------------------------------------------------------


@mcp.tool()
async def gitlab_list_issues(
    project_id: str,
    state: str = "opened",
    page: int = 1,
    labels: Optional[str] = None,
) -> str:
    """Liste les issues d'un projet GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - state : 'opened' | 'closed' | 'all' (défaut: 'opened')
    - page : numéro de page (défaut 1, 20 issues par page)
    - labels : filtre optionnel sur les labels (séparés par virgule, ex: 'bug,urgent')

    Retourne : iid, title, state, author, assignees, labels, created_at, updated_at.
    """
    pid = _encode_project_id(project_id)
    params: dict[str, Any] = {
        "state": state,
        "per_page": 20,
        "page": page,
        "order_by": "updated_at",
        "sort": "desc",
    }
    if labels:
        params["labels"] = labels
    try:
        data = await api_get(f"projects/{pid}/issues", params=params)
        result = [
            {
                "iid": i.get("iid"),
                "id": i.get("id"),
                "title": i.get("title"),
                "state": i.get("state"),
                "author": _user_summary(i.get("author")),
                "assignees": [_user_summary(a) for a in (i.get("assignees") or [])],
                "labels": i.get("labels") or [],
                "created_at": i.get("created_at"),
                "updated_at": i.get("updated_at"),
                "web_url": i.get("web_url"),
            }
            for i in (data if isinstance(data, list) else [])
        ]
        return _dumps({"issues": result, "count": len(result), "state": state, "page": page})
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_get_issue(project_id: str, issue_iid: int) -> str:
    """Retourne une issue complète avec sa description.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - issue_iid : numéro interne de l'issue dans le projet (IID, pas l'ID global)

    Retourne : tous les champs de l'issue, dont description, milestone, time_stats.
    """
    pid = _encode_project_id(project_id)
    try:
        data = await api_get(f"projects/{pid}/issues/{issue_iid}")
        return _dumps(data)
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_create_issue(
    project_id: str,
    title: str,
    description: Optional[str] = None,
    labels: Optional[str] = None,
    assignee_ids: Optional[list[int]] = None,
) -> str:
    """Crée une nouvelle issue dans un projet GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - title : titre de l'issue (obligatoire)
    - description : corps de l'issue en Markdown (optionnel)
    - labels : labels séparés par virgule (ex: 'bug,v2.0') (optionnel)
    - assignee_ids : liste d'IDs d'utilisateurs à assigner (optionnel)

    Retourne : l'issue créée avec son iid et web_url.
    """
    pid = _encode_project_id(project_id)
    payload: dict[str, Any] = {"title": title}
    if description is not None:
        payload["description"] = description
    if labels is not None:
        payload["labels"] = labels
    if assignee_ids:
        payload["assignee_ids"] = assignee_ids
    try:
        data = await api_post(f"projects/{pid}/issues", data=payload)
        return _dumps({
            "created": True,
            "iid": data.get("iid"),
            "id": data.get("id"),
            "title": data.get("title"),
            "state": data.get("state"),
            "web_url": data.get("web_url"),
        })
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_update_issue(
    project_id: str,
    issue_iid: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    state_event: Optional[str] = None,
    labels: Optional[str] = None,
) -> str:
    """Met à jour une issue existante dans GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - issue_iid : numéro interne de l'issue (IID)
    - title : nouveau titre (optionnel)
    - description : nouvelle description Markdown (optionnel)
    - state_event : 'close' pour fermer, 'reopen' pour rouvrir (optionnel)
    - labels : nouveaux labels séparés par virgule (optionnel, remplace les existants)

    Retourne : l'issue mise à jour.
    """
    pid = _encode_project_id(project_id)
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if state_event is not None:
        payload["state_event"] = state_event
    if labels is not None:
        payload["labels"] = labels
    if not payload:
        return _dumps({"error": "Aucun champ à mettre à jour fourni."})
    try:
        data = await api_put(f"projects/{pid}/issues/{issue_iid}", data=payload)
        return _dumps({
            "updated": True,
            "iid": data.get("iid"),
            "title": data.get("title"),
            "state": data.get("state"),
            "labels": data.get("labels"),
            "web_url": data.get("web_url"),
        })
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_add_issue_comment(
    project_id: str,
    issue_iid: int,
    body: str,
) -> str:
    """Ajoute un commentaire (note) sur une issue GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - issue_iid : numéro interne de l'issue (IID)
    - body : texte du commentaire en Markdown

    Retourne : la note créée avec son id et created_at.
    """
    pid = _encode_project_id(project_id)
    try:
        data = await api_post(f"projects/{pid}/issues/{issue_iid}/notes", data={"body": body})
        return _dumps({
            "created": True,
            "note_id": data.get("id"),
            "author": _user_summary(data.get("author")),
            "created_at": data.get("created_at"),
            "body_preview": (data.get("body") or "")[:200],
        })
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Tools — Merge Requests
# ---------------------------------------------------------------------------


@mcp.tool()
async def gitlab_list_merge_requests(
    project_id: str,
    state: str = "opened",
    page: int = 1,
) -> str:
    """Liste les merge requests d'un projet GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - state : 'opened' | 'closed' | 'merged' | 'all' (défaut: 'opened')
    - page : numéro de page (défaut 1, 20 MR par page)

    Retourne : iid, title, state, author, source_branch, target_branch, created_at.
    """
    pid = _encode_project_id(project_id)
    params: dict[str, Any] = {
        "state": state,
        "per_page": 20,
        "page": page,
        "order_by": "updated_at",
        "sort": "desc",
    }
    try:
        data = await api_get(f"projects/{pid}/merge_requests", params=params)
        result = [
            {
                "iid": mr.get("iid"),
                "id": mr.get("id"),
                "title": mr.get("title"),
                "state": mr.get("state"),
                "author": _user_summary(mr.get("author")),
                "source_branch": mr.get("source_branch"),
                "target_branch": mr.get("target_branch"),
                "created_at": mr.get("created_at"),
                "web_url": mr.get("web_url"),
            }
            for mr in (data if isinstance(data, list) else [])
        ]
        return _dumps({"merge_requests": result, "count": len(result), "state": state, "page": page})
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_get_merge_request(project_id: str, mr_iid: int) -> str:
    """Retourne un merge request complet avec tous ses détails.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - mr_iid : numéro interne du merge request dans le projet (IID)

    Retourne : tous les champs du MR, dont description, reviewers, diff_refs, pipeline.
    """
    pid = _encode_project_id(project_id)
    try:
        data = await api_get(f"projects/{pid}/merge_requests/{mr_iid}")
        return _dumps(data)
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_create_merge_request(
    project_id: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: Optional[str] = None,
) -> str:
    """Crée un nouveau merge request dans un projet GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - source_branch : branche source (celle à merger)
    - target_branch : branche cible (ex: 'main', 'develop')
    - title : titre du merge request
    - description : description en Markdown (optionnel)

    Retourne : le MR créé avec son iid et web_url.
    """
    pid = _encode_project_id(project_id)
    payload: dict[str, Any] = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title,
    }
    if description is not None:
        payload["description"] = description
    try:
        data = await api_post(f"projects/{pid}/merge_requests", data=payload)
        return _dumps({
            "created": True,
            "iid": data.get("iid"),
            "id": data.get("id"),
            "title": data.get("title"),
            "state": data.get("state"),
            "source_branch": data.get("source_branch"),
            "target_branch": data.get("target_branch"),
            "web_url": data.get("web_url"),
        })
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Tools — Repository
# ---------------------------------------------------------------------------


@mcp.tool()
async def gitlab_list_repo_tree(
    project_id: str,
    path: str = "",
    ref: Optional[str] = None,
) -> str:
    """Liste les fichiers et dossiers d'un répertoire du dépôt GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - path : chemin du répertoire à lister (défaut: racine '')
    - ref : branche, tag ou commit SHA (optionnel, utilise la branche par défaut si absent)

    Retourne : liste de {name, type ('blob'=fichier | 'tree'=dossier), path}.
    """
    pid = _encode_project_id(project_id)
    params: dict[str, Any] = {
        "path": path,
        "per_page": 50,
    }
    if ref:
        params["ref"] = ref
    try:
        data = await api_get(f"projects/{pid}/repository/tree", params=params)
        result = [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "path": item.get("path"),
            }
            for item in (data if isinstance(data, list) else [])
        ]
        return _dumps({"tree": result, "count": len(result), "path": path or "/"})
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_get_file(
    project_id: str,
    file_path: str,
    ref: str = "main",
) -> str:
    """Retourne le contenu d'un fichier du dépôt GitLab (décodé depuis base64).

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - file_path : chemin du fichier dans le dépôt (ex: 'src/main.py', 'README.md')
    - ref : branche, tag ou commit SHA (défaut: 'main')

    Retourne : contenu du fichier (limité à 50 KB), encodage, taille, dernier commit.
    """
    pid = _encode_project_id(project_id)
    # Le chemin du fichier doit aussi être URL-encodé
    encoded_path = urllib.parse.quote(file_path, safe="")
    params: dict[str, Any] = {"ref": ref}
    try:
        data = await api_get(f"projects/{pid}/repository/files/{encoded_path}", params=params)
        # Décoder le contenu base64
        raw_content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        content = ""
        if encoding == "base64" and raw_content:
            try:
                decoded_bytes = base64.b64decode(raw_content)
                if len(decoded_bytes) > MAX_FILE_SIZE:
                    content = decoded_bytes[:MAX_FILE_SIZE].decode("utf-8", errors="replace")
                    content += f"\n\n[... contenu tronqué à {MAX_FILE_SIZE // 1024} KB ...]"
                else:
                    content = decoded_bytes.decode("utf-8", errors="replace")
            except Exception:
                content = "[Contenu binaire non décodable en UTF-8]"
        else:
            content = raw_content
        return _dumps({
            "file_path": data.get("file_path"),
            "ref": data.get("ref"),
            "size": data.get("size"),
            "encoding": encoding,
            "last_commit_id": data.get("last_commit_id"),
            "content": content,
        })
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Tools — Pipelines
# ---------------------------------------------------------------------------


@mcp.tool()
async def gitlab_list_pipelines(
    project_id: str,
    page: int = 1,
) -> str:
    """Liste les pipelines CI/CD d'un projet GitLab.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - page : numéro de page (défaut 1, 10 pipelines par page)

    Retourne : id, status, ref (branche/tag), created_at, web_url.
    """
    pid = _encode_project_id(project_id)
    params: dict[str, Any] = {
        "per_page": 10,
        "page": page,
        "order_by": "id",
        "sort": "desc",
    }
    try:
        data = await api_get(f"projects/{pid}/pipelines", params=params)
        result = [
            {
                "id": p.get("id"),
                "status": p.get("status"),
                "ref": p.get("ref"),
                "sha": (p.get("sha") or "")[:8],
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
                "web_url": p.get("web_url"),
            }
            for p in (data if isinstance(data, list) else [])
        ]
        return _dumps({"pipelines": result, "count": len(result), "page": page})
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_get_pipeline(project_id: str, pipeline_id: int) -> str:
    """Retourne les détails complets d'un pipeline GitLab avec ses jobs.

    Paramètres :
    - project_id : identifiant numérique ou chemin 'namespace/projet'
    - pipeline_id : identifiant numérique du pipeline

    Retourne : infos du pipeline + liste des jobs (nom, stage, status, durée).
    """
    pid = _encode_project_id(project_id)
    try:
        # Récupérer le pipeline et ses jobs en parallèle
        pipeline_data, jobs_data = await asyncio.gather(
            api_get(f"projects/{pid}/pipelines/{pipeline_id}"),
            api_get(f"projects/{pid}/pipelines/{pipeline_id}/jobs", params={"per_page": 50}),
        )
        jobs = [
            {
                "id": j.get("id"),
                "name": j.get("name"),
                "stage": j.get("stage"),
                "status": j.get("status"),
                "duration": j.get("duration"),
                "web_url": j.get("web_url"),
            }
            for j in (jobs_data if isinstance(jobs_data, list) else [])
        ]
        return _dumps({
            "id": pipeline_data.get("id"),
            "status": pipeline_data.get("status"),
            "ref": pipeline_data.get("ref"),
            "sha": pipeline_data.get("sha"),
            "created_at": pipeline_data.get("created_at"),
            "updated_at": pipeline_data.get("updated_at"),
            "duration": pipeline_data.get("duration"),
            "web_url": pipeline_data.get("web_url"),
            "jobs": jobs,
        })
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Tools — Utilisateur
# ---------------------------------------------------------------------------


@mcp.tool()
async def gitlab_whoami() -> str:
    """Retourne les informations de l'utilisateur GitLab authentifié.

    Vérifie également que le token est valide et que la connexion fonctionne.
    Aucun paramètre requis.

    Retourne : id, username, name, email, state, avatar_url, web_url.
    """
    try:
        data = await api_get("user")
        return _dumps({
            "id": data.get("id"),
            "username": data.get("username"),
            "name": data.get("name"),
            "email": data.get("email"),
            "state": data.get("state"),
            "avatar_url": data.get("avatar_url"),
            "web_url": data.get("web_url"),
            "gitlab_url": GITLAB_URL,
        })
    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_create_file(
    project_id: str,
    file_path: str,
    branch: str,
    content: str,
    commit_message: str,
    author_email: str = "",
    author_name: str = "",
) -> str:
    """Crée un nouveau fichier dans un projet GitLab via POST /projects/{id}/repository/files.

    Confirmation utilisateur requise avant exécution (modification du repo).

    Paramètres :
    - project_id : ID numérique ou path URL-encodé (ex: 'mygroup/myrepo')
    - file_path : chemin du fichier dans le repo (ex: 'src/index.php' ou 'docs/README.md')
    - branch : branche cible (ex: 'main', 'develop'). Doit exister.
    - content : contenu textuel du fichier (sera UTF-8). Pour binaire, utiliser
      gitlab_create_file_base64 (non-implémenté ici — utiliser l'UI GitLab).
    - commit_message : message de commit (obligatoire)
    - author_email / author_name : optionnels — défaut = identité du token API

    Retourne : file_path, branch et hash du commit.

    Erreurs typiques :
    - 400 "A file with this name already exists" → utiliser gitlab_update_file
    - 400 "Branch ... does not exist" → créer la branche d'abord (UI ou git CLI)
    """
    try:
        if not project_id or not file_path or not branch or not commit_message:
            return "project_id, file_path, branch et commit_message sont obligatoires."

        pid = project_id if project_id.isdigit() else project_id.replace("/", "%2F")
        encoded_path = file_path.replace("/", "%2F")

        payload: dict[str, Any] = {
            "branch": branch,
            "content": content,
            "commit_message": commit_message,
        }
        if author_email:
            payload["author_email"] = author_email
        if author_name:
            payload["author_name"] = author_name

        result = await api_post(f"projects/{pid}/repository/files/{encoded_path}", data=payload)

        return _dumps({
            "success": True,
            "file_path": result.get("file_path", file_path),
            "branch": result.get("branch", branch),
            "commit_message": commit_message,
        })

    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_update_file(
    project_id: str,
    file_path: str,
    branch: str,
    content: str,
    commit_message: str,
    author_email: str = "",
    author_name: str = "",
    last_commit_id: str = "",
) -> str:
    """Met à jour un fichier existant dans un projet GitLab via PUT /projects/{id}/repository/files.

    Confirmation utilisateur requise avant exécution (modification du repo).

    Paramètres :
    - project_id : ID numérique ou path URL-encodé
    - file_path : chemin du fichier (doit déjà exister)
    - branch : branche cible
    - content : nouveau contenu textuel complet (PAS un diff)
    - commit_message : message de commit (obligatoire)
    - author_email / author_name : optionnels
    - last_commit_id : SHA du dernier commit connu sur ce fichier (optionnel mais
      RECOMMANDÉ — protection contre les écrasements concurrents). Récupérable
      via gitlab_get_file (champ last_commit_id).

    Retourne : file_path, branch, hash du commit.

    Erreurs typiques :
    - 400 "A file with this name doesn't exist" → utiliser gitlab_create_file
    - 400 "ref/last_commit_id mismatch" → fichier modifié depuis votre fetch,
      refaire un gitlab_get_file et retry avec le nouveau last_commit_id.
    """
    try:
        if not project_id or not file_path or not branch or not commit_message:
            return "project_id, file_path, branch et commit_message sont obligatoires."

        pid = project_id if project_id.isdigit() else project_id.replace("/", "%2F")
        encoded_path = file_path.replace("/", "%2F")

        payload: dict[str, Any] = {
            "branch": branch,
            "content": content,
            "commit_message": commit_message,
        }
        if author_email:
            payload["author_email"] = author_email
        if author_name:
            payload["author_name"] = author_name
        if last_commit_id:
            payload["last_commit_id"] = last_commit_id

        result = await api_put(f"projects/{pid}/repository/files/{encoded_path}", data=payload)

        return _dumps({
            "success": True,
            "file_path": result.get("file_path", file_path),
            "branch": result.get("branch", branch),
            "commit_message": commit_message,
        })

    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


@mcp.tool()
async def gitlab_list_branches(project_id: str, search: str = "", limit: int = 50) -> str:
    """Liste les branches d'un projet GitLab via GET /projects/{id}/repository/branches.

    Paramètres :
    - project_id : ID numérique ou path URL-encodé
    - search : filtre nom (substring)
    - limit : nombre max de branches retournées (défaut 50)

    Retourne : pour chaque branche : name, default, merged, protected, commit (sha + title).
    """
    try:
        pid = project_id if project_id.isdigit() else project_id.replace("/", "%2F")
        params: dict[str, Any] = {"per_page": min(max(1, limit), 100)}
        if search:
            params["search"] = search

        branches = await api_get(f"projects/{pid}/repository/branches", params=params)
        if not isinstance(branches, list):
            return _dumps({"error": "Réponse inattendue de l'API", "raw": str(branches)[:300]})

        result = [
            {
                "name": b.get("name"),
                "default": b.get("default"),
                "merged": b.get("merged"),
                "protected": b.get("protected"),
                "commit_sha": (b.get("commit") or {}).get("short_id"),
                "commit_title": (b.get("commit") or {}).get("title"),
            }
            for b in branches[:limit]
        ]
        return _dumps({"count": len(result), "branches": result})

    except Exception as exc:
        return _dumps({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
