# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour HedgeDoc (API 1.x).

Expose les notes collaboratives HedgeDoc (lecture, création, mise à jour,
historique, recherche) via le protocole MCP pour Claude Desktop.

Instance : https://pad.example.org (HedgeDoc 1.x)
Auth : login/mot de passe via POST /login → cookie de session (recommandé)
       Fallback : Bearer token via HEDGEDOC_API_TOKEN (si disponible)

HedgeDoc 1.x n'a pas d'API key — l'auth se fait par cookie de session,
obtenu via POST /login avec email + password.
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hedgedoc-mcp")

mcp = FastMCP("hedgedoc")

HEDGEDOC_URL = os.environ.get("HEDGEDOC_URL", "").rstrip("/")
# Auth par login/mot de passe (recommandé — HedgeDoc 1.x n'a pas d'API key)
HEDGEDOC_USER = os.environ.get("HEDGEDOC_USER", "")
HEDGEDOC_PASSWORD = os.environ.get("HEDGEDOC_PASSWORD", "")
# Fallback : Bearer token (non supporté nativement par HedgeDoc 1.x mais
# laissé pour compatibilité avec des versions patchées)
API_TOKEN = os.environ.get("HEDGEDOC_API_TOKEN", "")

# Limite par défaut pour l'historique
MAX_HISTORY = 50
# Limite de contenu retourné (en caractères)
MAX_CONTENT_CHARS = 10000

# ---------------------------------------------------------------------------
# Gestion de la session (cookie-auth)
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # secondes, doublé à chaque tentative

# Cookie de session obtenu après login. Persisté en mémoire pour la durée
# du processus. HedgeDoc invalide les sessions après inactivité prolongée.
_session_cookies: dict[str, str] = {}
_session_valid = False


async def _login() -> bool:
    """Authentifie sur HedgeDoc via POST /login et stocke le cookie de session.

    HedgeDoc 1.x utilise une authentification par formulaire (email + password).
    Le cookie connect.sid est retourné dans Set-Cookie et doit être réutilisé.

    Retourne True si le login a réussi, False sinon.
    """
    global _session_cookies, _session_valid

    if not HEDGEDOC_USER or not HEDGEDOC_PASSWORD:
        logger.info("Pas de credentials HedgeDoc — mode anonyme (notes publiques uniquement)")
        return False

    url = f"{HEDGEDOC_URL}/login"
    try:
        async with httpx.AsyncClient(verify=True, timeout=15, follow_redirects=False) as client:
            # HedgeDoc 1.x attend du form-data pour /login
            resp = await client.post(
                url,
                data={"email": HEDGEDOC_USER, "password": HEDGEDOC_PASSWORD},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            # Login réussi = 302 vers / ou 200 (selon la version)
            if resp.status_code in (200, 302, 301):
                cookies = dict(resp.cookies)
                if cookies:
                    _session_cookies = cookies
                    _session_valid = True
                    logger.info("HedgeDoc login OK — session cookie obtenu")
                    return True
                # Parfois les cookies sont dans les headers directement
                set_cookie = resp.headers.get("set-cookie", "")
                if "connect.sid" in set_cookie:
                    # Parser manuellement
                    for part in set_cookie.split(";"):
                        part = part.strip()
                        if "=" in part and not part.startswith(("Path", "Expires", "HttpOnly", "Secure", "SameSite")):
                            k, v = part.split("=", 1)
                            _session_cookies[k.strip()] = v.strip()
                    if _session_cookies:
                        _session_valid = True
                        logger.info("HedgeDoc login OK — cookie parsé depuis header")
                        return True
            logger.warning("HedgeDoc login échoué : HTTP %d", resp.status_code)
            return False
    except Exception as exc:
        logger.warning("HedgeDoc login erreur : %s", exc)
        return False


async def _ensure_session() -> None:
    """S'assure qu'une session valide est disponible (lazy login)."""
    global _session_valid
    if not _session_valid and (HEDGEDOC_USER or API_TOKEN):
        await _login()


def _build_headers(content_type: str = "application/json") -> dict[str, str]:
    """Construit les en-têtes HTTP."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    if API_TOKEN and not _session_valid:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    return headers


async def _hd_request(
    method: str,
    path: str,
    content_type: str = "application/json",
    follow_redirects: bool = False,
    retry_on_401: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Appel HTTP vers HedgeDoc avec session cookie + retry et backoff exponentiel.

    Retourne l'objet Response brut (le caller gère le parsing selon le type de
    réponse attendu : JSON, texte Markdown, ou redirection).

    Relance un login si la session est invalide. Le déclencheur couvre **401 et
    403** : HedgeDoc 1.x répond 403 (et non 401) sur les routes de l'espace
    utilisateur quand la session n'est pas établie — c'est ce qui faisait passer
    l'échec de `DELETE /history/<id>` pour un problème de droits sur la note
    (« note privée ») alors qu'il s'agissait d'une session absente.
    """
    await _ensure_session()

    url = f"{HEDGEDOC_URL}{path}"
    headers = _build_headers(content_type)
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                verify=True,
                timeout=30,
                follow_redirects=follow_redirects,
                cookies=_session_cookies,
            ) as client:
                logger.info("%s %s (tentative %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=headers, **kwargs)
                # Session expirée ou absente → re-login et retry une seule fois.
                # 403 inclus : c'est le code que renvoie HedgeDoc 1.x sur
                # /history et /me sans session valide.
                if (
                    resp.status_code in (401, 403)
                    and retry_on_401
                    and HEDGEDOC_USER
                ):
                    logger.info(
                        "Session HedgeDoc invalide (HTTP %d), re-login...",
                        resp.status_code,
                    )
                    global _session_valid
                    _session_valid = False
                    await _login()
                    return await _hd_request(
                        method, path, content_type, follow_redirects,
                        retry_on_401=False, **kwargs
                    )
                if resp.status_code >= 500:
                    resp.raise_for_status()
                return resp
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s dans %.1fs : %s", method, url, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    "Retry %s %s (HTTP %d) dans %.1fs",
                    method, url, exc.response.status_code, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


def _format_error(exc: Exception) -> str:
    """Formate une erreur HTTP en message lisible."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "Erreur 401 : session HedgeDoc expirée ou credentials invalides."
        if status == 403:
            # Ne plus affirmer « note privée » : sur HedgeDoc 1.x un 403 signale
            # aussi bien une session absente qu'une option d'instance
            # désactivée (allowFreeURL). Les outils concernés qualifient
            # l'erreur eux-mêmes via _hd_auth_state().
            return (
                "Erreur 403 : HedgeDoc refuse la requête. Causes possibles : "
                "session non établie, note privée, ou option d'instance "
                "désactivée (alias / allowFreeURL). Vérifier avec hedgedoc_me()."
            )
        if status == 404:
            return "Erreur 404 : note non trouvée dans HedgeDoc."
        if status == 500:
            return f"Erreur 500 : erreur interne HedgeDoc. Détail : {exc.response.text[:500]}"
        return f"Erreur HTTP {status} : {exc.response.text[:500]}"
    return f"Erreur : {exc}"


def _dumps(data: Any) -> str:
    """Sérialise en JSON compact avec support UTF-8."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


async def _hd_auth_state() -> dict[str, Any]:
    """État d'authentification courant, pour qualifier une erreur 403.

    Sans cette information, « permissions insuffisantes » est indiscernable de
    « pas connecté » : c'est exactement la confusion qui a fait chercher un
    problème de droits sur `hedgedoc_delete_history_entry` le 30/07/2026.
    """
    state: dict[str, Any] = {
        "credentials_configured": bool(HEDGEDOC_USER and HEDGEDOC_PASSWORD),
        "session_established": _session_valid,
        "logged_in": None,
        "login": None,
    }
    try:
        resp = await _hd_request("GET", "/me", content_type="", retry_on_401=False)
        if resp.status_code == 200:
            data = resp.json()
            state["logged_in"] = bool(data.get("isLoggedIn", True))
            state["login"] = data.get("name") or data.get("login") or None
        else:
            state["logged_in"] = False
            state["me_http_status"] = resp.status_code
    except Exception as exc:  # /me injoignable : on le dit, on ne devine pas
        state["logged_in"] = None
        state["me_error"] = str(exc)[:200]
    return state


def _hd_auth_hint(state: dict[str, Any]) -> str:
    """Phrase de diagnostic dérivée de l'état d'authentification."""
    if not state.get("credentials_configured"):
        return (
            "Aucun credential HedgeDoc configuré (HEDGEDOC_USER / "
            "HEDGEDOC_PASSWORD) : le serveur travaille en mode anonyme, les "
            "routes de l'espace utilisateur sont inaccessibles."
        )
    if state.get("logged_in") is False:
        return (
            "Session HedgeDoc non établie malgré des credentials configurés "
            "(GET /me ne confirme pas la connexion) : problème "
            "d'authentification, pas de droits."
        )
    if state.get("logged_in") is None:
        return "État de connexion indéterminé (GET /me injoignable)."
    return (
        f"Session HedgeDoc active (utilisateur « {state.get('login')} ») : "
        "l'échec porte donc bien sur les droits de la ressource, pas sur "
        "l'authentification."
    )


def _extract_note_id_from_url(location: str) -> str:
    """Extrait l'ID de note depuis une URL HedgeDoc de type /noteId ou /noteId#.

    Exemples :
    - /aBC123dEf       → aBC123dEf
    - /s/mon-alias     → s/mon-alias  (alias éditeur)
    - https://pad.example.org/xYz789  → xYz789
    """
    # Supprimer l'URL de base si présente
    path = location.replace(HEDGEDOC_URL, "").lstrip("/")
    # Supprimer les ancres (#...)
    path = path.split("#")[0].rstrip("/")
    return path


# ---------------------------------------------------------------------------
# Tools — Lecture de notes
# ---------------------------------------------------------------------------


@mcp.tool()
async def hedgedoc_get_note(note_id: str) -> str:
    """Télécharge le contenu Markdown d'une note HedgeDoc.

    Paramètre :
    - note_id : identifiant ou alias de la note (ex: 'aBC123', 'mon-alias')

    Retourne : contenu Markdown (tronqué à 10 000 caractères si nécessaire).
    """
    try:
        resp = await _hd_request("GET", f"/{note_id}/download", content_type="")
        if resp.status_code == 302:
            # Redirection inattendue vers la page HTML — on suit manuellement
            location = resp.headers.get("location", "")
            logger.info("Redirection download vers : %s", location)
            return _dumps({"error": f"Redirection inattendue vers {location}. Note peut-être privée."})

        if resp.status_code != 200:
            resp.raise_for_status()

        content = resp.text
        truncated = len(content) > MAX_CONTENT_CHARS
        return _dumps({
            "note_id": note_id,
            "content": content[:MAX_CONTENT_CHARS],
            "truncated": truncated,
            "length": len(content),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def hedgedoc_get_note_info(note_id: str) -> str:
    """Récupère les métadonnées d'une note HedgeDoc (titre, tags, dates, vues).

    Paramètre :
    - note_id : identifiant ou alias de la note (ex: 'aBC123', 'mon-alias')

    Retourne : title, tags, created_at, updated_at, viewcount.
    """
    try:
        resp = await _hd_request("GET", f"/{note_id}/info", content_type="")
        if resp.status_code == 302:
            location = resp.headers.get("location", "")
            return _dumps({"error": f"Redirection vers {location}. L'endpoint /info n'est pas disponible."})

        if resp.status_code != 200:
            resp.raise_for_status()

        data = resp.json()
        return _dumps({
            "note_id": note_id,
            "title": data.get("title", ""),
            "tags": data.get("tags", []),
            "created_at": data.get("createTime") or data.get("created", ""),
            "updated_at": data.get("updateTime") or data.get("lastchangeAt") or data.get("updated", ""),
            "viewcount": data.get("viewcount", 0),
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Création et mise à jour
# ---------------------------------------------------------------------------


@mcp.tool()
async def hedgedoc_create_note(content: str, alias: str = "") -> str:
    """Crée une nouvelle note HedgeDoc avec un contenu Markdown.

    Paramètres :
    - content : contenu Markdown de la note
    - alias : alias souhaité pour l'URL (optionnel, ex: 'ma-note-2024')

    Retourne : URL et ID de la note créée.
    Note : HedgeDoc 1.x répond par une redirection 302 vers la nouvelle note.

    ⚠️ `alias` dépend d'une option d'instance : `POST /new/<alias>` n'est
    autorisé que si `allowFreeURL` (variable `CMD_ALLOW_FREEURL`) est activé.
    Sur l'instance visée il répond 403. Dans ce cas l'outil le dit explicitement
    (`error: "hedgedoc_alias_forbidden"`) au lieu de laisser croire à un
    problème de droits, et il suffit de rappeler l'outil **sans** alias.
    """
    try:
        path = "/new"
        if alias:
            path = f"/new/{alias}"

        resp = await _hd_request(
            "POST",
            path,
            content_type="text/markdown",
            content=content.encode("utf-8"),
            follow_redirects=False,
        )

        # 403 sur /new/<alias> : l'instance refuse les URL libres. À distinguer
        # d'un souci de session, d'où la remontée de l'état d'authentification.
        if resp.status_code == 403 and alias:
            state = await _hd_auth_state()
            return _dumps({
                "success": False,
                "error": "hedgedoc_alias_forbidden",
                "alias": alias,
                "http_status": 403,
                "auth": state,
                "message": (
                    f"HedgeDoc refuse la création avec l'alias « {alias} » "
                    "(HTTP 403 sur POST /new/<alias>). Cause la plus probable : "
                    "l'option allowFreeURL (CMD_ALLOW_FREEURL) est désactivée "
                    "sur l'instance, qui n'autorise donc que les identifiants "
                    "générés. " + _hd_auth_hint(state)
                ),
                "workarounds": [
                    "hedgedoc_create_note(content=...) sans alias",
                    "Activer CMD_ALLOW_FREEURL côté configuration de l'instance",
                ],
            })

        # HedgeDoc 1.x répond 302 avec Location: /noteId
        if resp.status_code in (201, 302, 301, 200):
            location = resp.headers.get("location", "")
            if location:
                note_id = _extract_note_id_from_url(location)
                note_url = f"{HEDGEDOC_URL}/{note_id}"
                return _dumps({
                    "success": True,
                    "note_id": note_id,
                    "note_url": note_url,
                    "message": f"Note créée : {note_url}",
                })
            # 200 sans Location : body peut contenir l'ID
            if resp.status_code == 200 and resp.text:
                return _dumps({
                    "success": True,
                    "note_id": resp.text.strip(),
                    "note_url": f"{HEDGEDOC_URL}/{resp.text.strip()}",
                    "message": "Note créée.",
                })

        resp.raise_for_status()
        return _dumps({"success": False, "message": "Réponse inattendue de HedgeDoc."})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def hedgedoc_update_note(note_id: str, content: str) -> str:
    """Met à jour le contenu Markdown d'une note HedgeDoc existante.

    ⚠️ **Non supporté par HedgeDoc 1.x** (cas courant) : l'édition
    d'une note passe uniquement par le protocole temps réel (socket.io) de
    l'éditeur web. Il n'existe aucune route REST d'update — `/<noteId>` n'accepte
    que GET/HEAD, seul `POST /new` (création) est exposé. Vérifié en prod le
    26/07/2026 : `PUT /<noteId>` et `POST /<noteId>` renvoient tous deux
    « Cannot PUT/POST » (404 Express), même avec une session authentifiée.

    Le tool tente malgré tout le PUT (compatibilité avec une future instance
    exposant une API d'écriture) puis, en cas d'absence de route, renvoie une
    erreur explicite `hedgedoc_update_unsupported` plutôt qu'un « 404 note non
    trouvée » trompeur.

    Alternatives : `hedgedoc_create_note` pour publier une nouvelle version, ou
    édition manuelle de la note dans le navigateur.

    Paramètres :
    - note_id : identifiant ou alias de la note
    - content : nouveau contenu Markdown complet (remplace le contenu existant)

    Retourne : confirmation de mise à jour, ou erreur explicite si l'instance
    ne supporte pas l'édition via API.
    """
    try:
        resp = await _hd_request(
            "PUT",
            f"/{note_id}",
            content_type="text/markdown",
            content=content.encode("utf-8"),
        )

        if resp.status_code in (200, 204, 302):
            return _dumps({
                "success": True,
                "note_id": note_id,
                "message": f"Note '{note_id}' mise à jour avec succès.",
            })

        # 404/405 = la route d'update n'existe pas (HedgeDoc 1.x). On distingue
        # ce cas d'une note réellement absente en interrogeant /<noteId>/info.
        if resp.status_code in (404, 405):
            return _dumps(await _update_unsupported_payload(note_id, resp.status_code))

        resp.raise_for_status()
        return _dumps({"success": False, "message": "Réponse inattendue de HedgeDoc."})

    except Exception as exc:
        return _format_error(exc)


async def _update_unsupported_payload(note_id: str, status: int) -> dict[str, Any]:
    """Construit la réponse d'échec honnête de hedgedoc_update_note.

    Distingue « la note n'existe pas » de « HedgeDoc n'expose pas d'update »
    en vérifiant l'existence de la note via GET /<noteId>/info.
    """
    note_exists: bool | None = None
    try:
        info = await _hd_request("GET", f"/{note_id}/info", content_type="")
        note_exists = info.status_code == 200
    except Exception as exc:
        logger.warning("Vérification d'existence de la note %s impossible : %s", note_id, exc)

    if note_exists is False:
        return {
            "success": False,
            "error": "note_not_found",
            "note_id": note_id,
            "message": (
                f"Note '{note_id}' introuvable sur HedgeDoc (GET /{note_id}/info a échoué)."
            ),
        }

    return {
        "success": False,
        "error": "hedgedoc_update_unsupported",
        "note_id": note_id,
        "http_status": status,
        "note_exists": note_exists,
        "message": (
            "HedgeDoc 1.x ne permet pas l'édition d'une note via l'API REST : "
            f"la route d'update n'existe pas (HTTP {status} « Cannot PUT /{note_id} »), "
            "l'édition passe par le protocole temps réel (socket.io) de l'éditeur web. "
            "Utilisez hedgedoc_create_note pour publier une nouvelle version, "
            f"ou éditez la note dans le navigateur : {HEDGEDOC_URL}/{note_id}?both"
        ),
        "workarounds": [
            "hedgedoc_create_note(content=...) → publier une nouvelle note",
            f"Édition manuelle : {HEDGEDOC_URL}/{note_id}?both",
        ],
    }


# ---------------------------------------------------------------------------
# Tools — Historique et utilisateur
# ---------------------------------------------------------------------------


@mcp.tool()
async def hedgedoc_list_history() -> str:
    """Liste les notes récentes de l'utilisateur connecté (historique HedgeDoc).

    Requiert un token d'authentification valide (HEDGEDOC_API_TOKEN).
    Retourne jusqu'à 50 notes avec : id, title, tags, updated_at, pinned.
    """
    try:
        resp = await _hd_request("GET", "/history", content_type="")
        if resp.status_code == 401:
            return "Erreur 401 : token HedgeDoc requis pour accéder à l'historique."
        if resp.status_code != 200:
            resp.raise_for_status()

        data = resp.json()
        # L'historique HedgeDoc 1.x est sous la clé 'history'
        history = data.get("history", data) if isinstance(data, dict) else data
        if not isinstance(history, list):
            return _dumps({"error": "Format d'historique inattendu.", "raw": str(data)[:500]})

        results = []
        for entry in history[:MAX_HISTORY]:
            results.append({
                "id": entry.get("id", ""),
                "title": entry.get("text") or entry.get("title", "(sans titre)"),
                "tags": entry.get("tags", []),
                "updated_at": entry.get("time") or entry.get("updated_at", ""),
                "pinned": entry.get("pinned", False),
            })

        return _dumps({"count": len(results), "history": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def hedgedoc_me() -> str:
    """Retourne les informations de l'utilisateur HedgeDoc actuellement connecté.

    Requiert un token d'authentification valide (HEDGEDOC_API_TOKEN).
    Retourne : login, nom, email, photo de profil.
    """
    try:
        resp = await _hd_request("GET", "/me", content_type="")
        if resp.status_code == 401:
            return "Erreur 401 : token HedgeDoc requis. Vérifiez HEDGEDOC_API_TOKEN."
        if resp.status_code != 200:
            resp.raise_for_status()

        data = resp.json()
        return _dumps({
            "logged_in": data.get("isLoggedIn", True),
            "id": data.get("id", ""),
            "login": data.get("name") or data.get("login", ""),
            "email": data.get("email", ""),
            "photo": data.get("photo", ""),
            "provider": data.get("provider", ""),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def hedgedoc_search_notes(query: str) -> str:
    """Recherche des notes dans l'historique HedgeDoc par titre ou tag.

    Recherche côté client sur le résultat de /history (filtre local).
    Requiert un token d'authentification valide (HEDGEDOC_API_TOKEN).

    Paramètre :
    - query : terme à rechercher dans le titre ou les tags (insensible à la casse)

    Retourne : notes correspondantes avec id, title, tags, updated_at.
    """
    try:
        resp = await _hd_request("GET", "/history", content_type="")
        if resp.status_code == 401:
            return "Erreur 401 : token HedgeDoc requis pour accéder à l'historique."
        if resp.status_code != 200:
            resp.raise_for_status()

        data = resp.json()
        history = data.get("history", data) if isinstance(data, dict) else data
        if not isinstance(history, list):
            return _dumps({"error": "Format d'historique inattendu.", "raw": str(data)[:500]})

        q = query.lower()
        results = []
        for entry in history:
            title = (entry.get("text") or entry.get("title", "")).lower()
            tags = [t.lower() for t in entry.get("tags", [])]
            if q in title or any(q in tag for tag in tags):
                results.append({
                    "id": entry.get("id", ""),
                    "title": entry.get("text") or entry.get("title", "(sans titre)"),
                    "tags": entry.get("tags", []),
                    "updated_at": entry.get("time") or entry.get("updated_at", ""),
                    "pinned": entry.get("pinned", False),
                })

        return _dumps({"count": len(results), "query": query, "results": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def hedgedoc_delete_history_entry(note_id: str) -> str:
    """Supprime une note de l'historique HedgeDoc de l'utilisateur.

    Note : cette opération retire la note de l'historique personnel, elle ne
    supprime pas la note elle-même du serveur.
    Requiert un token d'authentification valide (HEDGEDOC_API_TOKEN).

    Paramètre :
    - note_id : identifiant de la note à retirer de l'historique

    Un échec 403 ici vient presque toujours d'une session non établie, pas des
    droits sur la note : la réponse d'erreur porte l'état de connexion
    (`auth`, issu de `/me`) pour trancher.
    """
    try:
        resp = await _hd_request("DELETE", f"/history/{note_id}", content_type="")
        if resp.status_code in (200, 204):
            return _dumps({
                "success": True,
                "note_id": note_id,
                "message": f"Note '{note_id}' retirée de l'historique.",
            })

        if resp.status_code in (401, 403):
            # Le re-login de _hd_request a déjà été tenté : si on est encore
            # ici, ce n'est pas un simple cookie expiré.
            state = await _hd_auth_state()
            return _dumps({
                "success": False,
                "error": "hedgedoc_history_delete_denied",
                "note_id": note_id,
                "http_status": resp.status_code,
                "auth": state,
                "message": (
                    f"HedgeDoc refuse DELETE /history/{note_id} "
                    f"(HTTP {resp.status_code}). " + _hd_auth_hint(state)
                ),
            })

        resp.raise_for_status()
        return _dumps({"success": False, "message": "Réponse inattendue de HedgeDoc."})

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


def main() -> None:
    """Point d'entrée pour le serveur MCP HedgeDoc."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
