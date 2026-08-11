# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour Dolibarr ERP.

Expose les données de l'ERP Dolibarr (projets, factures, tiers, tickets,
comptes-rendus de réunion) via le protocole MCP, pour Claude Desktop et tout
autre client MCP.

Configuration : DOLIBARR_URL (URL de base) + DOLIBARR_API_KEY (header DOLAPIKEY).
Compatible Dolibarr 16+ (API REST).
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dolibarr-mcp")

mcp = FastMCP("dolibarr")

DOLIBARR_URL = os.environ.get("DOLIBARR_URL", "")
API_KEY = os.environ.get("DOLIBARR_API_KEY", "")

HEADERS = {
    "DOLAPIKEY": API_KEY,
    "Accept": "application/json",
}

# Limite par défaut pour les listes (garder petit pour économiser les tokens)
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Limite serveur à demander quand un filtre est appliqué côté client (search,
# min_amount) : sans elle, la limite demandée tronque AVANT le filtrage et des
# résultats manquent silencieusement.
CLIENT_FILTER_SCAN_LIMIT = 500

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # seconds, doubles each retry

# Méthodes rejouables une fois la requête émise. Rejouer une écriture après un
# timeout de lecture ou un 5xx crée des DOUBLONS (tickets, tiers, propositions,
# temps passé) car Dolibarr a pu traiter la requête avant de perdre la réponse.
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}


class DolibarrAPIError(RuntimeError):
    """Erreur explicite levée par le wrapper Dolibarr (status, body excerpt)."""

    def __init__(self, status: int, message: str, body_excerpt: str = ""):
        self.status = status
        self.body_excerpt = body_excerpt
        super().__init__(message)


def _parse_json_or_raise(resp: "httpx.Response", method: str, url: str) -> Any:
    """Parse un body en JSON, lève DolibarrAPIError lisible si échec."""
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        body = resp.text or ""
        ct = resp.headers.get("content-type", "?")
        cl = resp.headers.get("content-length", "?")
        logger.error(
            "Réponse non-JSON pour %s %s : HTTP=%d content-type=%s content-length=%s "
            "body_len=%d first_bytes=%r",
            method, url, resp.status_code, ct, cl, len(body), body[:200],
        )
        excerpt = body[:200] if body else "(corps vide)"
        raise DolibarrAPIError(
            resp.status_code,
            f"Réponse non-JSON depuis Dolibarr (HTTP {resp.status_code}, "
            f"content-type={ct}, body={len(body)}o). Extrait : {excerpt!r}",
            body_excerpt=excerpt,
        ) from exc


async def _api_request(method: str, endpoint: str, **kwargs: Any) -> Any:
    """Appel HTTP avec retry et backoff exponentiel."""
    url = f"{DOLIBARR_URL}/api/index.php/{endpoint}"
    idempotent = method.upper() in _IDEMPOTENT_METHODS
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=True, timeout=30) as client:
                logger.info("%s %s (attempt %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=HEADERS, **kwargs)

                # Cas normal : 2xx
                if resp.status_code < 400:
                    return _parse_json_or_raise(resp, method, url)

                # Erreurs client 4xx : lever immédiatement sans inspecter le corps
                if resp.status_code < 500:
                    resp.raise_for_status()

                # Erreurs serveur 5xx : bug connu de l'API REST Dolibarr — certains endpoints
                # (contacts, thirdparties) retournent HTTP 500 avec un corps JSON valide (entier
                # ou tableau) à la place d'un HTTP 200. On parse le corps avant de décider.
                # Une vraie erreur Dolibarr a toujours la forme {"error": {"code":…, "message":…}}.
                try:
                    body = resp.json()
                    if not (isinstance(body, dict) and "error" in body):
                        logger.warning(
                            "HTTP %d avec corps JSON valide (non-erreur) pour %s %s "
                            "— bug API Dolibarr, traité comme succès",
                            resp.status_code, method, url,
                        )
                        return body
                except Exception:
                    pass  # Corps non-JSON → on lève l'erreur HTTP normalement

                resp.raise_for_status()

        except httpx.ConnectError as exc:
            # La requête n'a jamais atteint Dolibarr : rejouable même en écriture.
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s in %.1fs: %s", method, url, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            # La requête est partie : côté écriture, Dolibarr a peut-être déjà
            # créé l'objet → rejouer ferait un doublon. On remonte l'erreur.
            last_exc = exc
            if idempotent and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s in %.1fs: %s", method, url, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            if idempotent and exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s (HTTP %d) in %.1fs", method, url, exc.response.status_code, delay)
                await asyncio.sleep(delay)
            else:
                raise
        except DolibarrAPIError as exc:
            # Corps non-JSON sur réponse 2xx : très probablement un hoquet transient
            # côté PHP-FPM / proxy. On retry comme pour un timeout — donc jamais
            # sur une écriture, qui a pu aboutir malgré la réponse illisible.
            last_exc = exc
            if idempotent and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s (corps non-JSON) in %.1fs", method, url, delay)
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


# Endpoints de liste pour lesquels Dolibarr renvoie HTTP 404 "Not Found: No X found"
# au lieu d'une liste vide. Ces endpoints utilisent _api_get_list().
_LIST_404_AS_EMPTY = {
    "thirdparties",
    "contacts",
    "invoices",
    "supplierinvoices",
    "proposals",
    "tickets",
    "agendaevents",
}


async def _api_get_list(endpoint: str, params: dict[str, Any] | None = None) -> list:
    """Appel GET retournant une liste, en traitant 404 'no results' comme [].

    Dolibarr est incohérent : /projects renvoie [], /thirdparties renvoie 404.
    Ce wrapper normalise pour les endpoints du set _LIST_404_AS_EMPTY.
    """
    try:
        data = await api_get(endpoint, params)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404 and endpoint.split("/")[0] in _LIST_404_AS_EMPTY:
            # Vérifier que c'est bien le pattern "No X found" et pas une vraie 404
            try:
                body = exc.response.json()
                msg = (body.get("error") or {}).get("message", "")
                if "No " in msg and "found" in msg.lower():
                    logger.info("404 'no results' pour %s — traité comme liste vide", endpoint)
                    return []
            except Exception:
                pass
        raise
    return data if isinstance(data, list) else data


async def api_get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    """Appel GET à l'API REST Dolibarr avec retry."""
    return await _api_request("GET", endpoint, params=params or {})


async def api_post(endpoint: str, data: "dict[str, Any] | list | None" = None) -> Any:
    """Appel POST à l'API REST Dolibarr avec retry.

    data peut être un dict (cas usuel) ou une liste (ex: POST proposals/{id}/lines
    qui attend un tableau JSON pour correctement sérialiser une ligne).
    """
    payload: Any = data if data is not None else {}
    return await _api_request("POST", endpoint, json=payload)


async def api_put(endpoint: str, data: dict[str, Any] | None = None) -> Any:
    """Appel PUT à l'API REST Dolibarr avec retry."""
    return await _api_request("PUT", endpoint, json=data or {})


async def api_delete(endpoint: str) -> Any:
    """Appel DELETE à l'API REST Dolibarr avec retry."""
    return await _api_request("DELETE", endpoint)


def _ts_to_date(value: Any) -> str:
    """Convertit une valeur Dolibarr (timestamp Unix int ou string, ou date ISO)
    en date ISO 8601 lisible (YYYY-MM-DD). Retourne '' si non convertible."""
    if not value and value != 0:
        return ""
    from datetime import datetime
    # Timestamp Unix (int ou string numérique)
    try:
        ts = int(value)
        if ts > 946684800:  # après 2000-01-01, c'est un timestamp
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        pass
    # Déjà une date ISO ou string lisible
    s = str(value)
    if len(s) >= 10 and s[4:5] == "-":
        return s[:10]
    return s


def _date_str_to_ts(date_str: str) -> int:
    """Convertit une date YYYY-MM-DD en timestamp Unix (minuit UTC).

    Utilisé pour les champs Dolibarr qui stockent des dates sous forme de
    timestamp entier (fin_validite, date_lim_reglement, etc.).

    Lève ValueError si le format est invalide.
    """
    from datetime import datetime, timezone
    d = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def _seconds_to_hours(value: Any) -> float | None:
    """Convertit une durée Dolibarr (secondes) en heures, arrondies à 2 décimales.

    Dolibarr stocke et renvoie TOUTES les durées en secondes, le plus souvent
    sous forme de chaîne ('14400'). Exposer cette valeur brute sous un nom en
    heures fait conclure à un temps 3600 fois trop élevé, sans aucune erreur
    visible — d'où cette conversion systématique.

    Retourne None si la valeur est absente ou non numérique : surtout pas 0,
    qui se lirait comme « aucun temps passé » alors que l'information manque.
    """
    if value in (None, ""):
        return None
    try:
        return round(float(value) / 3600, 2)
    except (TypeError, ValueError):
        return None


def _seconds_int(value: Any) -> int | None:
    """Renvoie la durée brute Dolibarr en secondes (entier), ou None.

    Conservée à côté de la valeur en heures pour ne rien perdre de la
    précision d'origine (l'arrondi à 2 décimales perd jusqu'à 18 secondes).
    """
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _error_body(exc: httpx.HTTPStatusError) -> str:
    """Extrait le message d'erreur renvoyé par Dolibarr, ou '' si illisible.

    Une erreur Dolibarr a la forme {"error": {"code":…, "message":…}} ; certains
    échecs (fatale PHP) ne rendent qu'un corps vide.
    """
    try:
        body = exc.response.json()
    except Exception:
        return (exc.response.text or "").strip()[:500]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message") or "")[:500]
    return json.dumps(body, ensure_ascii=False)[:500]


def _format_error(exc: Exception) -> str:
    """Formate une erreur HTTP en message lisible."""
    if isinstance(exc, DolibarrAPIError):
        return f"Erreur Dolibarr : {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "Erreur 401 : clé API Dolibarr invalide ou manquante."
        if status == 403:
            return "Erreur 403 : permissions insuffisantes pour cette ressource."
        if status == 404:
            return "Erreur 404 : ressource non trouvée dans Dolibarr."
        if status == 500:
            return f"Erreur 500 : erreur interne Dolibarr. Détail : {exc.response.text[:500]}"
        return f"Erreur HTTP {status} : {exc.response.text[:500]}"
    if isinstance(exc, json.JSONDecodeError):
        # Filet de sécurité : ne devrait plus se produire grâce à _parse_json_or_raise
        return f"Erreur JSON Dolibarr : {exc}"
    return f"Erreur : {exc}"


def _dumps(data: Any) -> str:
    """Sérialise en JSON compact avec support UTF-8."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _clamp_limit(limit: int) -> int:
    """Borne la limite entre 1 et MAX_LIMIT."""
    return max(1, min(limit, MAX_LIMIT))


def _sf_escape(value: str) -> str:
    """Escape a string value for use in Dolibarr sqlfilters (escapes single quotes)."""
    return str(value).replace("'", "''")


# ---------------------------------------------------------------------------
# Tools — Projets
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_projects(
    status: str = "1",
    thirdparty_id: int = 0,
    search: str = "",
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les projets Dolibarr.

    Paramètres :
    - status : 0=brouillon, 1=ouvert, 2=fermé (défaut : 1 = ouvert)
    - thirdparty_id : filtrer par tiers (0 = tous)
    - search : mot-clé dans le titre ou la référence
    - limit : nombre max de résultats (défaut 50, max 100)

    Retourne : ref, titre, tiers, statut, dates, budget.
    """
    try:
        clamp = _clamp_limit(limit)
        params: dict[str, Any] = {
            "sortfield": "t.ref",
            "sortorder": "ASC",
            # search est filtré côté client : élargir la limite serveur, sinon
            # elle tronque avant le filtrage et des projets manquent.
            "limit": CLIENT_FILTER_SCAN_LIMIT if search else clamp,
        }
        if status:
            params["sqlfilters"] = f"(t.fk_statut:=:{status})"
        if thirdparty_id:
            params["thirdparty_ids"] = str(thirdparty_id)

        projects = await _api_get_list("projects", params)

        results = []
        for p in projects:
            entry = {
                "id": p.get("id"),
                "ref": p.get("ref"),
                "title": p.get("title"),
                "status": p.get("fk_statut") or p.get("status"),
                "thirdparty": p.get("thirdparty_name") or p.get("socid"),
                "date_start": _ts_to_date(p.get("date_start")),
                "date_end": _ts_to_date(p.get("date_end")),
                "budget": p.get("budget_amount"),
                "description": (p.get("description") or "")[:200],
            }
            # Filtre mot-clé côté client si nécessaire
            if search:
                haystack = f"{entry['ref']} {entry['title']} {entry.get('description', '')}".lower()
                if search.lower() not in haystack:
                    continue
            results.append(entry)
            if len(results) >= clamp:
                break

        return _dumps({"count": len(results), "projects": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_project(project_id: int = 0, ref: str = "") -> str:
    """Détail d'un projet par ID numérique ou par référence.

    Fournir soit project_id, soit ref. Retourne toutes les infos du projet
    incluant tâches et contacts associés.
    """
    try:
        if ref and not project_id:
            # Recherche par ref
            projects = await api_get("projects", {"sqlfilters": f"(t.ref:=:'{_sf_escape(ref)}')"})
            if not projects:
                return f"Aucun projet trouvé avec la référence '{ref}'."
            project_id = projects[0]["id"]

        if not project_id:
            return "Veuillez fournir un project_id ou une ref."

        p = await api_get(f"projects/{project_id}")
        # Filtrer les champs utiles
        result = {
            "id": p.get("id"),
            "ref": p.get("ref"),
            "title": p.get("title"),
            "status": p.get("fk_statut") or p.get("status"),
            "thirdparty": p.get("thirdparty_name") or p.get("socid"),
            "date_start": _ts_to_date(p.get("date_start")),
            "date_end": _ts_to_date(p.get("date_end")),
            "budget": p.get("budget_amount"),
            "description": (p.get("description") or "")[:500],
            "note_public": _strip_html(p.get("note_public"))[:500],
            "usage_bill_time": p.get("usage_bill_time"),
        }
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_list_tasks(
    project_id: int = 0,
    assigned_to: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les tâches d'un projet Dolibarr.

    Paramètres :
    - project_id : ID du projet (obligatoire)
    - assigned_to : filtrer par utilisateur assigné (ID)
    - limit : nombre max de résultats

    Retourne : ref, label, progression, dates, charge prévue et temps passé.

    Durées — Dolibarr les renvoie en SECONDES ; chacune est exposée deux fois :
    - planned_hours / spent_hours   : heures, arrondies à 2 décimales
    - planned_seconds / spent_seconds : valeur brute Dolibarr, sans perte
    Une tâche portant 32,5 h ressort donc à spent_hours=32.5 et
    spent_seconds=117000. Ne jamais lire spent_seconds comme des heures.
    """
    try:
        if not project_id:
            return "Veuillez fournir un project_id."

        params: dict[str, Any] = {"limit": _clamp_limit(limit)}
        tasks = await api_get(f"projects/{project_id}/tasks", params)

        results = []
        for t in tasks:
            entry = {
                "id": t.get("id"),
                "ref": t.get("ref"),
                "label": t.get("label"),
                "progress": t.get("progress"),
                "date_start": _ts_to_date(t.get("date_start")),
                "date_end": _ts_to_date(t.get("date_end")),
                "planned_hours": _seconds_to_hours(t.get("planned_workload")),
                "planned_seconds": _seconds_int(t.get("planned_workload")),
                "spent_hours": _seconds_to_hours(t.get("duration_effective")),
                "spent_seconds": _seconds_int(t.get("duration_effective")),
                "assigned_to": t.get("fk_user_resp") or t.get("fk_user_creat"),
            }
            if assigned_to and str(entry.get("assigned_to")) != str(assigned_to):
                continue
            results.append(entry)

        return _dumps({"count": len(results), "tasks": results})

    except Exception as exc:
        return _format_error(exc)


# Route d'ajout de temps passé, vérifiée contre le swagger servi par l'API
# elle-même (Dolibarr v23). Trois routes se ressemblent, une seule accepte
# un POST — les deux autres rendent un 404 sec, indiscernable d'un problème
# de droits ou d'identifiant :
#   POST tasks/{id}/addtimespent   → la bonne
#   GET  tasks/{id}/timespent      → lecture seule, un POST ici rend 404
#   .../projects/tasks/{id}/…      → n'existe pas : la classe Tasks est montée
#                                    à la racine, jamais sous /projects
TIMESPENT_ENDPOINT = "tasks/{task_id}/addtimespent"


async def _task_exists(task_id: int) -> bool | None:
    """True/False selon que la tâche existe, None si le diagnostic échoue lui-même.

    Sert uniquement à trancher entre les causes d'un échec d'imputation ; on ne
    l'appelle jamais sur le chemin nominal.
    """
    try:
        return bool(await api_get(f"tasks/{task_id}"))
    except httpx.HTTPStatusError as exc:
        return False if exc.response.status_code == 404 else None
    except Exception:
        return None


async def _log_time_error(exc: Exception, task_id: int, endpoint: str) -> str:
    """Explique l'échec d'une imputation de temps, cause par cause.

    Un « 404 : ressource non trouvée » nu est indébogable côté client : il
    recouvre des causes qui n'appellent pas du tout la même correction. On
    interroge la tâche pour trancher, et on remonte toujours le corps d'erreur
    Dolibarr quand il y en a un.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return _format_error(exc)

    status = exc.response.status_code
    payload: dict[str, Any] = {
        "success": False,
        "task_id": task_id,
        "endpoint": endpoint,
        "http_status": status,
        "dolibarr_error": _error_body(exc) or None,
    }

    if status in (401, 403):
        payload["cause"] = "droits_insuffisants"
        payload["hint"] = (
            "Dolibarr refuse l'accès à cette tâche. Vérifier que la clé API "
            "porte le droit « créer/modifier » sur les projets, et que le "
            "user_id visé est bien affecté à la tâche."
        )
        return _dumps(payload)

    exists = await _task_exists(task_id)

    if exists is False:
        payload["cause"] = "tache_inexistante"
        payload["hint"] = (
            f"La tâche #{task_id} est introuvable. Lister les tâches du projet "
            "avec dolibarr_list_tasks pour récupérer un id valide."
        )
    elif status == 404:
        # La tâche répond : ce n'est donc pas elle qui manque, c'est la route.
        payload["cause"] = "route_ou_methode_invalide"
        payload["hint"] = (
            f"La tâche #{task_id} existe, mais {endpoint} rend un 404 : la route "
            "ou le verbe ne correspond pas à cette version de Dolibarr. "
            "L'ajout de temps est un POST sur tasks/{id}/addtimespent — "
            "tasks/{id}/timespent est en lecture seule."
        )
    elif exists is True:
        payload["cause"] = "utilisateur_non_affecte_ou_erreur_interne"
        payload["hint"] = (
            f"La tâche #{task_id} existe et la route répond. Cause la plus "
            "fréquente : l'utilisateur visé n'est pas affecté à la tâche "
            "(Dolibarr rejette l'imputation sans message explicite). "
            "Vérifier l'affectation, ou passer un user_id explicite."
        )
    else:
        payload["cause"] = "indetermine"
        payload["hint"] = (
            "L'appel a échoué et la tâche n'a pas pu être interrogée pour "
            "trancher. Vérifier la connectivité et les droits de la clé API."
        )
    return _dumps(payload)


@mcp.tool()
async def dolibarr_log_time(
    task_id: int | str = 0,
    duration: float = 0,
    date: str = "",
    note: str = "",
    user_id: int = 0,
) -> str:
    """Saisir du temps passé sur une tâche Dolibarr.

    Paramètres :
    - task_id : ID de la tâche (obligatoire)
    - duration : durée en heures (ex: 1.5 pour 1h30)
    - date : date au format YYYY-MM-DD (défaut: aujourd'hui)
    - note : commentaire sur le temps passé
    - user_id : ID utilisateur (défaut: utilisateur API)

    La durée est saisie en HEURES et convertie en secondes pour l'API, qui
    n'accepte que des secondes (1.5 → 5400).

    Retourne : confirmation avec le temps total passé sur la tâche, en heures
    (`total_spent_hours`) et en secondes brutes (`total_spent_seconds`).
    En cas d'échec, retourne la cause identifiée (`cause`) plutôt qu'un 404 nu.
    """
    try:
        task_id = int(task_id) if task_id else 0
        if not task_id:
            return "Veuillez fournir un task_id."
        if duration <= 0:
            return "La durée doit être supérieure à 0."

        from datetime import datetime

        if date:
            dt = datetime.strptime(date, "%Y-%m-%d")
        else:
            dt = datetime.now().replace(hour=9, minute=0, second=0)

        duration_seconds = int(duration * 3600)

        # Dolibarr v23 : POST tasks/{id}/addtimespent, champs date/duration/
        # user_id/note. La date passe par dol_stringtotime() → format
        # "YYYY-MM-DD HH:MI:SS" attendu, PAS un timestamp Unix.
        data: dict[str, Any] = {
            "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": duration_seconds,
            "note": note,
        }
        # user_id absent = utilisateur de la clé API (envoyer None ferait échouer
        # la validation Restler du paramètre entier).
        if user_id:
            data["user_id"] = user_id

        endpoint = TIMESPENT_ENDPOINT.format(task_id=task_id)
        try:
            await api_post(endpoint, data)
        except Exception as exc:
            return await _log_time_error(exc, task_id, endpoint)

        # Re-fetch task to get updated totals
        task = await api_get(f"tasks/{task_id}")

        return _dumps({
            "success": True,
            "logged_hours": duration,
            "logged_seconds": duration_seconds,
            "task_id": task_id,
            "task_ref": task.get("ref"),
            "task_label": task.get("label"),
            "total_spent_hours": _seconds_to_hours(task.get("duration_effective")),
            "total_spent_seconds": _seconds_int(task.get("duration_effective")),
            "planned_hours": _seconds_to_hours(task.get("planned_workload")),
            "planned_seconds": _seconds_int(task.get("planned_workload")),
            "date": dt.strftime("%Y-%m-%d"),
            "note": note,
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_create_task(
    project_id: int,
    label: str,
    planned_hours: float = 0,
    date_start: str = "",
    date_end: str = "",
    description: str = "",
    progress: int = 0,
) -> str:
    """Crée une tâche dans un projet Dolibarr.

    Paramètres :
    - project_id   : ID du projet (obligatoire)
    - label        : libellé de la tâche (obligatoire)
    - planned_hours: charge prévue en heures (ex: 4.5 → 4h30)
    - date_start   : date de début (format YYYY-MM-DD, optionnel)
    - date_end     : date de fin / échéance (format YYYY-MM-DD, optionnel)
    - description  : description longue (optionnel)
    - progress     : avancement en % 0-100 (défaut: 0)

    Retourne l'ID et la référence de la tâche créée.
    """
    try:
        if not project_id:
            return "Le project_id est obligatoire."
        if not label:
            return "Le label est obligatoire."

        from datetime import datetime

        data: dict[str, Any] = {
            "fk_projet": project_id,
            "label": label,
            "progress": max(0, min(100, int(progress))),
        }
        if planned_hours > 0:
            data["planned_workload"] = int(planned_hours * 3600)
        if date_start:
            data["date_start"] = int(datetime.strptime(date_start, "%Y-%m-%d").timestamp())
        if date_end:
            data["date_end"] = int(datetime.strptime(date_end, "%Y-%m-%d").timestamp())
        if description:
            data["description"] = description

        result = await api_post("tasks", data)

        return _dumps({
            "success": True,
            "task_id": result,
            "project_id": project_id,
            "label": label,
            "planned_hours": planned_hours or None,
            "message": f"Tâche '{label}' créée dans le projet {project_id}.",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_close_task(
    task_id: int,
    progress: int = 100,
) -> str:
    """Marque une tâche Dolibarr comme terminée (progress=100 par défaut).

    Paramètres :
    - task_id  : ID de la tâche (obligatoire)
    - progress : avancement en % à appliquer (défaut: 100 = terminée)

    Retourne les infos mises à jour de la tâche, temps passé en heures
    (`total_spent_hours`) et en secondes brutes (`total_spent_seconds`).
    """
    try:
        if not task_id:
            return "Le task_id est obligatoire."

        progress = max(0, min(100, int(progress)))
        await api_put(f"tasks/{task_id}", {"progress": progress})

        task = await api_get(f"tasks/{task_id}")

        return _dumps({
            "success": True,
            "task_id": task_id,
            "ref": task.get("ref"),
            "label": task.get("label"),
            "progress": progress,
            "total_spent_hours": _seconds_to_hours(task.get("duration_effective")),
            "total_spent_seconds": _seconds_int(task.get("duration_effective")),
            "status": "terminée" if progress == 100 else f"{progress}%",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Facturation
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_invoices(
    status: str = "unpaid",
    thirdparty_id: int = 0,
    min_amount: float = 0,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les factures clients.

    Paramètres :
    - status : 'unpaid' (impayées), 'paid' (payées), 'draft' (brouillon), 'all'
    - thirdparty_id : filtrer par tiers
    - min_amount : montant TTC minimum
    - limit : nombre max de résultats

    Retourne : ref, tiers, montant TTC, date échéance, statut, jours de retard.
    """
    try:
        clamp = _clamp_limit(limit)
        params: dict[str, Any] = {
            "sortfield": "t.date_lim_reglement",
            "sortorder": "ASC",
            # min_amount est filtré côté client : élargir la limite serveur, sinon
            # elle tronque avant le filtrage et des factures manquent.
            "limit": CLIENT_FILTER_SCAN_LIMIT if min_amount else clamp,
        }

        # Mapping statut
        status_map = {"draft": "0", "validated": "1", "paid": "2", "unpaid": "1"}
        if status in status_map:
            params["sqlfilters"] = f"(t.fk_statut:=:{status_map[status]})"
            if status == "unpaid":
                params["sqlfilters"] = "(t.fk_statut:=:1)"  # validée non payée
        if thirdparty_id:
            params["thirdparty_ids"] = str(thirdparty_id)

        invoices = await _api_get_list("invoices", params)

        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        for inv in invoices:
            total_ttc = float(inv.get("total_ttc") or 0)
            if total_ttc < min_amount:
                continue

            date_lim = _ts_to_date(inv.get("date_lim_reglement"))
            date_inv = _ts_to_date(inv.get("date"))
            inv_status = str(inv.get("fk_statut") or inv.get("status") or inv.get("statut") or "")
            days_late = 0
            if date_lim and date_lim < today and inv_status == "1":
                try:
                    d_lim = datetime.strptime(date_lim[:10], "%Y-%m-%d")
                    days_late = (datetime.now() - d_lim).days
                except ValueError:
                    pass

            results.append({
                "id": inv.get("id"),
                "ref": inv.get("ref"),
                "thirdparty": inv.get("thirdparty_name") or inv.get("socid"),
                "total_ttc": total_ttc,
                "total_ht": float(inv.get("total_ht") or 0),
                "date_invoice": date_inv,
                "date_due": date_lim,
                "status": inv_status,
                "status_label": _format_invoice_status(inv_status, inv.get("paye")),
                "days_late": days_late,
                "remaining_to_pay": float(inv.get("remaintopay") or total_ttc),
            })
            if len(results) >= clamp:
                break

        return _dumps({"count": len(results), "invoices": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_list_supplier_invoices(
    status: str = "unpaid",
    thirdparty_id: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les factures fournisseur.

    Paramètres :
    - status : 'unpaid', 'paid', 'draft', 'all'
    - thirdparty_id : filtrer par fournisseur
    - limit : nombre max de résultats

    Retourne : ref, fournisseur, montant TTC, date échéance, statut.
    """
    try:
        params: dict[str, Any] = {
            "sortfield": "t.date_lim_reglement",
            "sortorder": "ASC",
            "limit": _clamp_limit(limit),
        }

        status_map = {"draft": "0", "validated": "1", "paid": "2", "unpaid": "1"}
        if status in status_map:
            params["sqlfilters"] = f"(t.fk_statut:=:{status_map[status]})"
        if thirdparty_id:
            params["thirdparty_ids"] = str(thirdparty_id)

        invoices = await _api_get_list("supplierinvoices", params)

        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        for inv in invoices:
            date_lim = _ts_to_date(inv.get("date_lim_reglement"))
            date_inv = _ts_to_date(inv.get("date"))
            inv_status = str(inv.get("fk_statut") or inv.get("status") or inv.get("statut") or "")
            days_late = 0
            if date_lim and date_lim < today and inv_status == "1":
                try:
                    d_lim = datetime.strptime(date_lim[:10], "%Y-%m-%d")
                    days_late = (datetime.now() - d_lim).days
                except ValueError:
                    pass

            results.append({
                "id": inv.get("id"),
                "ref": inv.get("ref"),
                "ref_supplier": inv.get("ref_supplier"),
                "thirdparty": inv.get("thirdparty_name") or inv.get("socid"),
                "total_ttc": float(inv.get("total_ttc") or 0),
                "date_invoice": date_inv,
                "date_due": date_lim,
                "status": inv_status,
                "status_label": _format_invoice_status(inv_status, inv.get("paye")),
                "days_late": days_late,
            })

        return _dumps({"count": len(results), "invoices": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_list_proposals(
    status: str = "",
    thirdparty_id: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les propositions commerciales (devis).

    Paramètres :
    - status : 0=brouillon, 1=validé, 2=signé, 3=refusé, 4=facturé, vide=tous
    - thirdparty_id : filtrer par tiers
    - limit : nombre max de résultats

    Retourne : ref, tiers, montant, statut, date de validité.
    """
    try:
        params: dict[str, Any] = {
            "sortfield": "t.datep",
            "sortorder": "DESC",
            "limit": _clamp_limit(limit),
        }
        if status:
            params["sqlfilters"] = f"(t.fk_statut:=:{status})"
        if thirdparty_id:
            params["thirdparty_ids"] = str(thirdparty_id)

        proposals = await _api_get_list("proposals", params)

        results = []
        for p in proposals:
            results.append({
                "id": p.get("id"),
                "ref": p.get("ref"),
                "thirdparty": p.get("thirdparty_name") or p.get("socid"),
                "total_ht": float(p.get("total_ht") or 0),
                "total_ttc": float(p.get("total_ttc") or 0),
                "date_creation": _ts_to_date(p.get("datep") or p.get("date_creation")),
                "date_validity": _ts_to_date(p.get("fin_validite") or p.get("date_fin_validite")),
                "status": p.get("fk_statut") or p.get("status") or p.get("statut"),
                "status_label": _format_proposal_status(p.get("fk_statut") or p.get("status") or p.get("statut")),
            })

        return _dumps({"count": len(results), "proposals": results})

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Tiers
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_thirdparties(
    type_filter: str = "",
    search: str = "",
    search_email: str = "",
    search_code: str = "",
    category: int = 0,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """Liste les tiers (clients, fournisseurs, prospects).

    Paramètres :
    - type_filter : 'customer' (client), 'supplier' (fournisseur), 'prospect', vide=tous
    - search : mot-clé dans le nom du tiers
    - search_email : filtrer par email du tiers (correspondance partielle)
    - search_code : filtrer par code client ou fournisseur
    - category : ID de catégorie Dolibarr (0 = aucun filtre)
    - limit : nombre max de résultats
    - offset : décalage pour la pagination (ex: 20 pour la 2e page de 20)

    Retourne : id, nom, type, email, téléphone, ville.
    """
    try:
        clamp = _clamp_limit(limit)
        params: dict[str, Any] = {
            "sortfield": "t.nom",
            "sortorder": "ASC",
            "limit": clamp,
            "page": offset // clamp if offset > 0 else 0,
        }

        filters = []
        if type_filter == "customer":
            filters.append("(t.client:=:1)")
        elif type_filter == "supplier":
            filters.append("(t.fournisseur:=:1)")
        elif type_filter == "prospect":
            filters.append("(t.client:=:2)")
        if search:
            filters.append(f"(t.nom:like:'%{_sf_escape(search)}%')")
        if search_email:
            filters.append(f"(t.email:like:'%{_sf_escape(search_email)}%')")
        if search_code:
            filters.append(f"(t.code_client:like:'%{_sf_escape(search_code)}%')")
        if filters:
            params["sqlfilters"] = " AND ".join(filters)
        if category:
            params["category"] = category

        thirdparties = await _api_get_list("thirdparties", params)

        results = []
        for tp in thirdparties:
            results.append({
                "id": tp.get("id"),
                "name": tp.get("name"),
                "name_alias": tp.get("name_alias"),
                "email": tp.get("email"),
                "phone": tp.get("phone"),
                "town": tp.get("town"),
                "country": tp.get("country"),
                "is_customer": tp.get("client"),
                "is_supplier": tp.get("fournisseur"),
                "status": tp.get("status"),
            })

        return _dumps({"count": len(results), "thirdparties": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_thirdparty(thirdparty_id: int) -> str:
    """Détail complet d'un tiers par son ID.

    Retourne toutes les informations : coordonnées, catégories, contacts,
    conditions de paiement, etc.
    """
    try:
        tp = await api_get(f"thirdparties/{thirdparty_id}")
        # Filtrer les champs utiles pour économiser les tokens
        result = {
            "id": tp.get("id"),
            "name": tp.get("name"),
            "name_alias": tp.get("name_alias"),
            "email": tp.get("email"),
            "phone": tp.get("phone"),
            "fax": tp.get("fax"),
            "address": tp.get("address"),
            "zip": tp.get("zip"),
            "town": tp.get("town"),
            "country": tp.get("country"),
            "url": tp.get("url"),
            "is_customer": tp.get("client"),
            "is_supplier": tp.get("fournisseur"),
            "code_client": tp.get("code_client"),
            "code_fournisseur": tp.get("code_fournisseur"),
            "siret": tp.get("idprof2"),
            "tva_intra": tp.get("tva_intra"),
            "status": tp.get("status"),
            "note_public": _strip_html(tp.get("note_public"))[:300],
        }
        return _dumps(result)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_add_contact(
    thirdparty_id: int,
    firstname: str,
    lastname: str,
    email: str = "",
    phone: str = "",
    phone_mobile: str = "",
    job_title: str = "",
    note: str = "",
    force: bool = False,
) -> str:
    """Ajoute un contact à un tiers existant dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Anti-doublon : vérifie l'email (1) sur le tiers cible — doublon strict — et
    (2) sur tous les autres tiers — doublon potentiel (même personne rattachée
    ailleurs). Dans les deux cas, AUCUN contact n'est créé et l'id existant est
    renvoyé. Passer force=True pour créer malgré tout (ex. même personne
    légitimement contact de deux sociétés).

    Paramètres :
    - thirdparty_id : ID du tiers auquel rattacher le contact (obligatoire)
    - firstname : prénom du contact
    - lastname : nom de famille du contact
    - email : adresse email (optionnel)
    - phone : téléphone fixe (optionnel)
    - phone_mobile : téléphone mobile (optionnel)
    - job_title : fonction / poste (optionnel)
    - note : note libre (optionnel)
    - force : créer même si un contact au même email existe déjà (défaut: False)

    Retourne l'ID du contact créé, ou un signalement de doublon.
    """
    try:
        if not thirdparty_id:
            return "Veuillez fournir un thirdparty_id."
        if not lastname:
            return "Le nom de famille (lastname) est obligatoire."

        # Anti-doublon par email (best-effort : une erreur de recherche ne bloque pas)
        if email and not force:
            # 1) doublon strict : même email sur LE MÊME tiers
            try:
                existing = await api_get("contacts", {
                    "sqlfilters": f"(t.email:=:'{_sf_escape(email)}') AND (t.fk_soc:=:{thirdparty_id})",
                    "limit": 1,
                })
                if isinstance(existing, list) and existing:
                    return _dumps({
                        "success": False,
                        "error": "duplicate",
                        "existing_contact_id": existing[0].get("id"),
                        "message": f"Un contact avec l'email '{email}' existe déjà sur ce tiers.",
                    })
            except Exception:
                pass  # En cas d'erreur, on continue les vérifications
            # 2) doublon potentiel : même email sur un AUTRE tiers (cascade import)
            try:
                other = await api_get("contacts", {
                    "sqlfilters": f"(t.email:=:'{_sf_escape(email)}')",
                    "limit": 5,
                })
                if isinstance(other, list) and other:
                    matches = [
                        {
                            "id": o.get("id"),
                            "fk_soc": o.get("socid") or o.get("fk_soc"),
                            "name": f"{o.get('firstname', '')} {o.get('lastname', '')}".strip(),
                        }
                        for o in other
                    ]
                    return _dumps({
                        "success": False,
                        "error": "possible_duplicate_other_thirdparty",
                        "existing_contacts": matches,
                        "message": (
                            f"Un contact avec l'email '{email}' existe déjà sur un autre tiers "
                            f"({len(matches)} trouvé(s)). Aucun contact créé. Relancer avec "
                            f"force=True si c'est volontaire (même personne, deux sociétés)."
                        ),
                    })
            except Exception:
                pass  # En cas d'erreur, on continue la création

        data = {
            "socid": thirdparty_id,
            "firstname": firstname,
            "lastname": lastname,
        }
        if email:
            data["email"] = email
        if phone:
            data["phone_pro"] = phone
        if phone_mobile:
            data["phone_mobile"] = phone_mobile
        if job_title:
            data["poste"] = job_title
        if note:
            data["note_public"] = note

        result = await api_post("contacts", data)

        return _dumps({
            "success": True,
            "contact_id": result,
            "thirdparty_id": thirdparty_id,
            "name": f"{firstname} {lastname}",
            "message": f"Contact '{firstname} {lastname}' ajouté au tiers {thirdparty_id}.",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_create_thirdparty(
    name: str,
    name_alias: str = "",
    type_tiers: str = "prospect",
    email: str = "",
    phone: str = "",
    url: str = "",
    address: str = "",
    zip_code: str = "",
    town: str = "",
    country_code: str = "FR",
    note_private: str = "",
    note_public: str = "",
    force: bool = False,
) -> str:
    """Crée un nouveau tiers dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Anti-doublon : avant création, vérifie qu'aucun tiers existant ne porte le
    même nom (insensible à la casse) ni le même email. Si un doublon potentiel
    est trouvé, AUCUN tiers n'est créé et la réponse renvoie le(s) id(s)
    existant(s). Passer force=True pour créer malgré tout (homonyme légitime).
    Indispensable pour éviter qu'une boucle d'import recrée des tiers déjà
    présents (incident du 26/03/2026).

    Paramètres :
    - name : raison sociale (obligatoire)
    - name_alias : nom court / alias (optionnel)
    - type_tiers : 'customer', 'supplier', 'prospect', 'customer+supplier' (défaut: prospect)
    - email, phone, url, address, zip_code, town, country_code : coordonnées
    - note_private, note_public : notes
    - force : créer même si un doublon potentiel (nom/email) existe déjà (défaut: False)

    Retourne l'ID du tiers créé, ou un signalement de doublon.
    """
    try:
        if not name:
            return "Le nom (name) est obligatoire."

        # Anti-doublon : nom identique (insensible à la casse) ou email identique.
        # En cas d'erreur de recherche (ex. 404 "no third parties found"), on
        # n'empêche pas la création — le garde-fou est best-effort.
        if not force:
            try:
                clauses = [f"(t.nom:like:'{_sf_escape(name)}')"]
                if email:
                    clauses.append(f"(t.email:=:'{_sf_escape(email)}')")
                sqlf = ("(" + " OR ".join(clauses) + ")") if len(clauses) > 1 else clauses[0]
                existing = await api_get("thirdparties", {"sqlfilters": sqlf, "limit": 5})
                if isinstance(existing, list) and existing:
                    matches = [
                        {"id": e.get("id"), "name": e.get("name"), "email": e.get("email")}
                        for e in existing
                    ]
                    return _dumps({
                        "success": False,
                        "error": "duplicate",
                        "existing_thirdparties": matches,
                        "message": (
                            f"{len(matches)} tiers existant(s) avec un nom ou un email identique "
                            f"à '{name}'. Aucun tiers créé. Relancer avec force=True pour créer "
                            f"malgré tout (homonyme légitime)."
                        ),
                    })
            except Exception:
                pass

        # Mapping type → champs client/fournisseur Dolibarr
        client_val = 0
        fournisseur_val = 0
        if "customer" in type_tiers and "supplier" in type_tiers:
            client_val = 1
            fournisseur_val = 1
        elif "customer" in type_tiers:
            client_val = 1
        elif "supplier" in type_tiers:
            fournisseur_val = 1
        elif "prospect" in type_tiers:
            client_val = 2  # prospect

        data: dict[str, Any] = {
            "name": name,
            "client": str(client_val),
            "fournisseur": str(fournisseur_val),
            "country_code": country_code,
            "status": "1",  # ouvert
        }
        if name_alias:
            data["name_alias"] = name_alias
        if email:
            data["email"] = email
        if phone:
            data["phone"] = phone
        if url:
            data["url"] = url
        if address:
            data["address"] = address
        if zip_code:
            data["zip"] = zip_code
        if town:
            data["town"] = town
        if note_private:
            data["note_private"] = note_private
        if note_public:
            data["note_public"] = note_public

        result = await api_post("thirdparties", data)

        return _dumps({
            "success": True,
            "thirdparty_id": result,
            "name": name,
            "type": type_tiers,
            "message": f"Tiers '{name}' créé avec succès.",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_thirdparty(
    thirdparty_id: int,
    name: str = "",
    name_alias: str = "",
    email: str = "",
    phone: str = "",
    url: str = "",
    address: str = "",
    zip_code: str = "",
    town: str = "",
    client: int = -1,
    fournisseur: int = -1,
    note_private: str = "",
    note_public: str = "",
    status: int = -1,
) -> str:
    """Modifie un tiers existant dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Seuls les champs fournis (non vides) sont mis à jour.

    Paramètres :
    - thirdparty_id : ID du tiers (obligatoire)
    - name, name_alias, email, phone, url, address, zip_code, town
    - client : 0=ni client ni prospect, 1=client, 2=prospect, 3=client+prospect
    - fournisseur : 0 ou 1
    - note_private, note_public
    - status : 0=fermé, 1=ouvert
    """
    try:
        if not thirdparty_id:
            return "Veuillez fournir un thirdparty_id."

        data: dict[str, Any] = {}
        if name:
            data["name"] = name
        if name_alias:
            data["name_alias"] = name_alias
        if email:
            data["email"] = email
        if phone:
            data["phone"] = phone
        if url:
            data["url"] = url
        if address:
            data["address"] = address
        if zip_code:
            data["zip"] = zip_code
        if town:
            data["town"] = town
        if client >= 0:
            data["client"] = str(client)
        if fournisseur >= 0:
            data["fournisseur"] = str(fournisseur)
        if note_private:
            data["note_private"] = note_private
        if note_public:
            data["note_public"] = note_public
        if status >= 0:
            data["status"] = str(status)

        if not data:
            return "Aucun champ à mettre à jour."

        await api_put(f"thirdparties/{thirdparty_id}", data)

        return _dumps({
            "success": True,
            "thirdparty_id": thirdparty_id,
            "updated_fields": list(data.keys()),
            "message": f"Tiers {thirdparty_id} mis à jour.",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_list_contacts(
    thirdparty_id: int,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les contacts rattachés à un tiers.

    Paramètres :
    - thirdparty_id : ID du tiers (obligatoire)
    - limit : nombre max de résultats

    Retourne : id, prénom, nom, email, téléphone, fonction, statut.
    """
    try:
        if not thirdparty_id:
            return "Veuillez fournir un thirdparty_id."

        contacts = await _api_get_list("contacts", {
            "sortfield": "t.rowid",
            "sortorder": "ASC",
            "limit": _clamp_limit(limit),
            "sqlfilters": f"(t.fk_soc:=:{thirdparty_id})",
        })

        results = []
        for c in contacts:
            results.append({
                "id": c.get("id"),
                "firstname": c.get("firstname"),
                "lastname": c.get("lastname"),
                "email": c.get("email"),
                "phone": c.get("phone_pro"),
                "phone_mobile": c.get("phone_mobile"),
                "job_title": c.get("poste"),
                "status": c.get("statut"),
            })

        return _dumps({"thirdparty_id": thirdparty_id, "count": len(results), "contacts": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_search_contacts(
    search: str = "",
    email: str = "",
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Cherche un contact par email ou nom à travers tous les tiers.

    Paramètres :
    - search : mot-clé dans nom/prénom
    - email : recherche par email (match exact ou partiel)
    - limit : nombre max de résultats

    Retourne : id, nom, email, tiers rattaché, fonction.
    """
    try:
        if not search and not email:
            return "Veuillez fournir au moins 'search' ou 'email'."

        filters = []
        if email:
            filters.append(f"(t.email:like:'%{_sf_escape(email)}%')")
        if search:
            filters.append(f"(t.lastname:like:'%{_sf_escape(search)}%')")

        contacts = await _api_get_list("contacts", {
            "sortfield": "t.rowid",
            "sortorder": "ASC",
            "limit": _clamp_limit(limit),
            "sqlfilters": " OR ".join(filters) if len(filters) > 1 else filters[0],
        })

        results = []
        for c in contacts:
            results.append({
                "id": c.get("id"),
                "firstname": c.get("firstname"),
                "lastname": c.get("lastname"),
                "email": c.get("email"),
                "thirdparty_id": c.get("socid") or c.get("fk_soc"),
                "thirdparty_name": c.get("thirdparty_name"),
                "job_title": c.get("poste"),
            })

        return _dumps({"count": len(results), "contacts": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_contact(
    contact_id: int,
    firstname: str = "",
    lastname: str = "",
    email: str = "",
    phone: str = "",
    phone_mobile: str = "",
    job_title: str = "",
    note: str = "",
) -> str:
    """Modifie un contact existant dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - contact_id : ID du contact (obligatoire)
    - firstname, lastname, email, phone, phone_mobile, job_title, note :
      champs à modifier (seuls les non vides sont mis à jour)
    """
    try:
        if not contact_id:
            return "Veuillez fournir un contact_id."

        data: dict[str, Any] = {}
        if firstname:
            data["firstname"] = firstname
        if lastname:
            data["lastname"] = lastname
        if email:
            data["email"] = email
        if phone:
            data["phone_pro"] = phone
        if phone_mobile:
            data["phone_mobile"] = phone_mobile
        if job_title:
            data["poste"] = job_title
        if note:
            data["note_public"] = note

        if not data:
            return "Aucun champ à mettre à jour."

        await api_put(f"contacts/{contact_id}", data)

        return _dumps({
            "success": True,
            "contact_id": contact_id,
            "updated_fields": list(data.keys()),
            "message": f"Contact {contact_id} mis à jour.",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_contact(contact_id: int) -> str:
    """Supprime un contact dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - contact_id : ID du contact (obligatoire)
    """
    try:
        if not contact_id:
            return "Veuillez fournir un contact_id."

        await api_delete(f"contacts/{contact_id}")

        return _dumps({
            "success": True,
            "contact_id": contact_id,
            "message": f"Contact {contact_id} supprimé.",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Tickets
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_tickets(
    status: str = "",
    thirdparty_id: int = 0,
    severity: str = "",
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les tickets de support.

    Paramètres :
    - status : 'not_read', 'read', 'assigned', 'in_progress', 'waiting', 'closed', vide=ouverts
    - thirdparty_id : filtrer par tiers
    - severity : 'low', 'medium', 'high', 'critical'
    - limit : nombre max de résultats

    Retourne : ref, sujet, tiers, statut, priorité, date de création.
    """
    try:
        params: dict[str, Any] = {
            "sortfield": "t.datec",
            "sortorder": "DESC",
            "limit": _clamp_limit(limit),
        }

        filters = []
        # Par défaut, exclure les fermés
        if status:
            status_code_map = {
                "not_read": "0",
                "read": "1",
                "assigned": "2",
                "in_progress": "3",
                "waiting": "5",
                "closed": "8",
            }
            if status in status_code_map:
                filters.append(f"(t.fk_statut:=:{status_code_map[status]})")
        else:
            filters.append("(t.fk_statut:<:8)")  # tout sauf fermé

        if thirdparty_id:
            filters.append(f"(t.fk_soc:=:{thirdparty_id})")
        if severity:
            filters.append(f"(t.severity_code:=:'{_sf_escape(severity)}')")
        if filters:
            params["sqlfilters"] = " AND ".join(filters)

        tickets = await _api_get_list("tickets", params)

        results = []
        for t in tickets:
            results.append({
                "id": t.get("id"),
                "ref": t.get("ref"),
                "track_id": t.get("track_id"),
                "subject": t.get("subject"),
                "message": (t.get("message") or "")[:200],
                "thirdparty": t.get("thirdparty_name") or t.get("fk_soc"),
                "status": t.get("fk_statut"),
                "status_label": t.get("status_label"),
                "severity": t.get("severity_code"),
                "category": t.get("category_code") or t.get("type_code"),
                "date_creation": _ts_to_date(t.get("datec")),
                "date_last_msg": t.get("date_last_msg_sent"),
                "assigned_to": t.get("fk_user_assign"),
            })

        return _dumps({"count": len(results), "tickets": results})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_ticket(ticket_id: int = 0, ref: str = "") -> str:
    """Récupère le détail complet d'un ticket de support.

    Paramètres :
    - ticket_id : ID numérique du ticket (ou ref)
    - ref       : référence du ticket (ex: 'TK2026-0042')

    Retourne : sujet, message complet, statut, tiers, historique des messages.
    """
    try:
        if ticket_id:
            ticket = await api_get(f"tickets/{ticket_id}")
        elif ref:
            tickets = await api_get("tickets", {"sqlfilters": f"(t.ref:=:'{_sf_escape(ref)}')", "limit": 1})
            if not tickets:
                return f"Ticket '{ref}' introuvable."
            ticket = tickets[0]
        else:
            return "Veuillez fournir ticket_id ou ref."

        return _dumps({
            "id": ticket.get("id"),
            "ref": ticket.get("ref"),
            "track_id": ticket.get("track_id"),
            "subject": ticket.get("subject"),
            "message": ticket.get("message"),
            "status": ticket.get("fk_statut"),
            "status_label": ticket.get("status_label"),
            "severity": ticket.get("severity_code"),
            "type": ticket.get("type_code"),
            "category": ticket.get("category_code"),
            "thirdparty_id": ticket.get("fk_soc"),
            "thirdparty": ticket.get("thirdparty_name"),
            "project_id": ticket.get("fk_project"),
            "assigned_to": ticket.get("fk_user_assign"),
            "date_creation": _ts_to_date(ticket.get("datec")),
            "date_last_msg": ticket.get("date_last_msg_sent"),
            "nb_messages": ticket.get("nb_public_messages"),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_create_ticket(
    subject: str,
    message: str,
    thirdparty_id: int = 0,
    project_id: int = 0,
    severity: str = "normal",
    type_code: str = "QUESTION",
) -> str:
    """Crée un ticket de support dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - subject       : sujet du ticket (obligatoire)
    - message       : message / description du problème (obligatoire)
    - thirdparty_id : ID du tiers concerné (optionnel)
    - project_id    : ID du projet associé (optionnel)
    - severity      : 'low', 'normal', 'high', 'critical' (défaut: 'normal')
    - type_code     : 'QUESTION', 'INCIDENT', 'REQUEST', 'SUPPORT' (défaut: 'QUESTION')

    Retourne l'ID et la référence du ticket créé.
    """
    try:
        if not subject:
            return "Le sujet est obligatoire."
        if not message:
            return "Le message est obligatoire."

        severity_map = {
            "low": "LOW", "normal": "NORMAL",
            "high": "HIGH", "critical": "CRITICAL",
        }
        severity_code = severity_map.get(severity.lower(), "NORMAL")

        data: dict[str, Any] = {
            "subject": subject,
            "message": message,
            "severity_code": severity_code,
            "type_code": type_code.upper(),
        }
        if thirdparty_id:
            data["fk_soc"] = thirdparty_id
        if project_id:
            data["fk_project"] = project_id

        result = await api_post("tickets", data)

        return _dumps({
            "success": True,
            "ticket_id": result,
            "subject": subject,
            "severity": severity_code,
            "type": type_code,
            "message": f"Ticket créé avec succès (ID {result}).",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Résolution et synthèse
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_resolve_email(email_address: str) -> str:
    """Résout une adresse email vers un tiers et/ou contact Dolibarr.

    C'est LE tool de corrélation : il permet de faire le lien entre
    un participant CalDAV, un expéditeur IMAP, et un tiers Dolibarr.

    Paramètres :
    - email_address : adresse email à résoudre

    Retourne le tiers et le contact correspondants s'ils existent,
    ou une suggestion si seul le domaine matche.
    """
    try:
        email_lower = email_address.strip().lower()
        domain = email_lower.split("@")[1] if "@" in email_lower else ""

        result: dict[str, Any] = {"email": email_lower, "found": False}

        # 1. Chercher dans les contacts par email exact
        try:
            contacts = await api_get("contacts", {
                "sqlfilters": f"(t.email:=:'{_sf_escape(email_lower)}')",
                "limit": 5,
            })
            if isinstance(contacts, list) and contacts:
                c = contacts[0]
                result["found"] = True
                result["contact"] = {
                    "id": c.get("id"),
                    "firstname": c.get("firstname"),
                    "lastname": c.get("lastname"),
                    "email": c.get("email"),
                    "job_title": c.get("poste"),
                }
                # Récupérer le tiers associé
                socid = c.get("socid") or c.get("fk_soc")
                if socid:
                    try:
                        tp = await api_get(f"thirdparties/{socid}")
                        result["thirdparty"] = {
                            "id": tp.get("id"),
                            "name": tp.get("name"),
                            "email": tp.get("email"),
                            "is_customer": tp.get("client"),
                            "is_supplier": tp.get("fournisseur"),
                        }
                    except Exception:
                        result["thirdparty_id"] = socid

                if len(contacts) > 1:
                    result["other_matches"] = [
                        {"id": c2.get("id"), "name": f"{c2.get('firstname')} {c2.get('lastname')}",
                         "thirdparty_id": c2.get("socid")}
                        for c2 in contacts[1:]
                    ]
                return _dumps(result)
        except Exception:
            pass

        # 2. Chercher dans les tiers par email exact
        try:
            thirdparties = await api_get("thirdparties", {
                "sqlfilters": f"(t.email:=:'{_sf_escape(email_lower)}')",
                "limit": 5,
            })
            if isinstance(thirdparties, list) and thirdparties:
                tp = thirdparties[0]
                result["found"] = True
                result["thirdparty"] = {
                    "id": tp.get("id"),
                    "name": tp.get("name"),
                    "email": tp.get("email"),
                    "is_customer": tp.get("client"),
                    "is_supplier": tp.get("fournisseur"),
                }
                return _dumps(result)
        except Exception:
            pass

        # 3. Fallback : chercher par domaine dans les tiers
        if domain:
            try:
                thirdparties = await api_get("thirdparties", {
                    "sqlfilters": f"(t.email:like:'%@{_sf_escape(domain)}')",
                    "limit": 5,
                })
                if isinstance(thirdparties, list) and thirdparties:
                    result["domain_matches"] = [
                        {"id": tp.get("id"), "name": tp.get("name"), "email": tp.get("email")}
                        for tp in thirdparties
                    ]
                    result["suggestion"] = (
                        f"Aucun contact exact pour '{email_lower}', mais {len(thirdparties)} "
                        f"tiers avec le domaine @{domain}."
                    )
                    return _dumps(result)
            except Exception:
                pass

        result["suggestion"] = f"Aucun tiers ni contact trouvé pour '{email_lower}'."
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_revenue_summary(
    year: int = 0,
    month: int = 0,
) -> str:
    """Synthèse du chiffre d'affaires depuis les factures Dolibarr.

    Calcule le CA à partir des factures validées et payées.
    Utile pour le tableau de bord mensuel direction.

    Paramètres :
    - year : année (défaut : année en cours)
    - month : mois (1-12, défaut : mois en cours). Si 0, retourne le cumul annuel.

    Retourne : CA HT/TTC du mois, CA cumulé depuis début d'année,
    nombre de factures émises, comparaison N-1 si disponible.
    """
    from datetime import datetime

    now = datetime.now()
    if not year:
        year = now.year
    if not month:
        month = now.month

    try:
        result: dict[str, Any] = {"year": year, "month": month}

        # Factures validées + payées de l'année en cours
        # Statut 1 = validée, 2 = payée
        all_invoices = []
        for status_code in ("1", "2"):
            try:
                invs = await api_get("invoices", {
                    "sqlfilters": f"(t.fk_statut:=:{status_code})",
                    "limit": 500,
                    "properties": "id,total_ht,total_ttc,date,fk_statut",
                })
                if isinstance(invs, list):
                    all_invoices.extend(invs)
            except Exception:
                pass

        # Filtrer par année et calculer
        year_invoices = []
        month_invoices = []
        for inv in all_invoices:
            date_str = _ts_to_date(inv.get("date"))
            if not date_str or len(date_str) < 7:
                continue
            inv_year = int(date_str[:4])
            inv_month = int(date_str[5:7])
            if inv_year == year:
                year_invoices.append(inv)
                if inv_month == month:
                    month_invoices.append(inv)

        # CA du mois
        result["month_ca_ht"] = round(sum(float(i.get("total_ht") or 0) for i in month_invoices), 2)
        result["month_ca_ttc"] = round(sum(float(i.get("total_ttc") or 0) for i in month_invoices), 2)
        result["month_nb_invoices"] = len(month_invoices)

        # CA cumulé année
        result["ytd_ca_ht"] = round(sum(float(i.get("total_ht") or 0) for i in year_invoices), 2)
        result["ytd_ca_ttc"] = round(sum(float(i.get("total_ttc") or 0) for i in year_invoices), 2)
        result["ytd_nb_invoices"] = len(year_invoices)

        # Comparaison N-1 (même mois, année précédente)
        try:
            prev_year = year - 1
            prev_invoices = []
            for status_code in ("1", "2"):
                try:
                    invs = await api_get("invoices", {
                        "sqlfilters": f"(t.fk_statut:=:{status_code})",
                        "limit": 500,
                        "properties": "id,total_ht,date,fk_statut",
                    })
                    if isinstance(invs, list):
                        for inv in invs:
                            ds = _ts_to_date(inv.get("date"))
                            if ds and len(ds) >= 7:
                                if int(ds[:4]) == prev_year and int(ds[5:7]) == month:
                                    prev_invoices.append(inv)
                except Exception:
                    pass

            prev_ca = round(sum(float(i.get("total_ht") or 0) for i in prev_invoices), 2)
            result["n_minus_1_month_ca_ht"] = prev_ca
            if prev_ca > 0:
                variation = round(((result["month_ca_ht"] - prev_ca) / prev_ca) * 100, 1)
                result["variation_vs_n1_pct"] = variation
        except Exception:
            pass

        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Actions / Agenda
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_actions_open(
    project_id: int = 0,
    thirdparty_id: int = 0,
    assigned_to: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Liste les actions ouvertes (agenda, CR, suivi) dans Dolibarr.

    Retourne les actions non terminées (percent < 100), triées par date prévue ascendante.
    Utile pour le suivi des engagements client et des actions projet.

    Paramètres :
    - project_id : filtrer par projet (ID, 0 = tous)
    - thirdparty_id : filtrer par tiers (ID, 0 = tous)
    - assigned_to : filtrer par utilisateur assigné (ID Dolibarr, 0 = tous)
    - limit : nombre max de résultats (défaut 20, max 100)

    Retourne : id, label, code type, tiers, projet, date prévue, progression, assigné.
    """
    try:
        params: dict[str, Any] = {
            "sortfield": "t.datep",
            "sortorder": "ASC",
            "limit": _clamp_limit(limit),
        }

        # Build sqlfilters: open = percent < 100 (0, -1, or any in-progress value)
        sql_parts: list[str] = ["(t.percent:<>:100)"]
        if project_id:
            sql_parts.append(f"(t.fk_project:=:{project_id})")
        if thirdparty_id:
            sql_parts.append(f"(t.fk_soc:=:{thirdparty_id})")
        params["sqlfilters"] = " AND ".join(sql_parts)

        actions = await _api_get_list("agendaevents", params)

        if not isinstance(actions, list):
            return _dumps({"count": 0, "actions": []})

        results = []
        for a in actions:
            # Optional: client-side filter by assigned user
            if assigned_to:
                user_id = a.get("fk_user_action") or a.get("fk_user_author")
                if str(user_id) != str(assigned_to):
                    continue

            results.append({
                "id": a.get("id"),
                "label": a.get("label") or (a.get("note") or "")[:100],
                "code": a.get("code"),
                "type": a.get("type"),
                "percent": a.get("percent"),
                "date": _ts_to_date(a.get("datep")),
                "date_end": _ts_to_date(a.get("datep2")),
                "project_id": a.get("fk_project"),
                "thirdparty_id": a.get("fk_soc"),
                "contact_id": a.get("fk_contact"),
                "assigned_to": a.get("fk_user_action"),
                "location": a.get("location"),
                "fulldayevent": a.get("fulldayevent"),
            })

        return _dumps({"count": len(results), "actions": results})

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Dashboard
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_dashboard() -> str:
    """Tableau de bord de direction (synthèse multi-modules).

    Agrège les données clés depuis Dolibarr :
    - Nombre de projets ouverts
    - Factures impayées (nombre + montant total)
    - Propositions commerciales en attente
    - Tickets ouverts

    Utile pour un point rapide sur l'état de l'activité.
    """
    dashboard: dict[str, Any] = {}
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    # Projets ouverts — ne charger que id+ref pour compter
    try:
        projects = await api_get("projects", {
            "sqlfilters": "(t.fk_statut:=:1)",
            "limit": 200,
            "properties": "id",
        })
        dashboard["projects_open"] = len(projects) if isinstance(projects, list) else "?"
    except Exception as exc:
        dashboard["projects_open"] = f"Erreur : {exc}"

    # Factures impayées — ne charger que les champs nécessaires au calcul
    try:
        invoices = await api_get("invoices", {
            "sqlfilters": "(t.fk_statut:=:1)",
            "limit": 200,
            "properties": "id,total_ttc,remaintopay,date_lim_reglement",
        })
        if isinstance(invoices, list):
            total_unpaid = sum(float(inv.get("remaintopay") or inv.get("total_ttc") or 0) for inv in invoices)
            dashboard["invoices_unpaid_count"] = len(invoices)
            dashboard["invoices_unpaid_total_ttc"] = round(total_unpaid, 2)
            overdue = [inv for inv in invoices if (_ts_to_date(inv.get("date_lim_reglement")) or "9999") < today]
            dashboard["invoices_overdue_count"] = len(overdue)
            dashboard["invoices_overdue_total_ttc"] = round(
                sum(float(inv.get("remaintopay") or inv.get("total_ttc") or 0) for inv in overdue), 2
            )
    except Exception as exc:
        dashboard["invoices_unpaid"] = f"Erreur : {exc}"

    # Propositions en attente
    try:
        proposals = await api_get("proposals", {
            "sqlfilters": "(t.fk_statut:=:1)",
            "limit": 200,
            "properties": "id,total_ht",
        })
        if isinstance(proposals, list):
            dashboard["proposals_pending_count"] = len(proposals)
            dashboard["proposals_pending_total_ht"] = round(
                sum(float(p.get("total_ht") or 0) for p in proposals), 2
            )
    except Exception as exc:
        dashboard["proposals_pending"] = f"Erreur : {exc}"

    # Tickets ouverts
    try:
        tickets = await api_get("tickets", {
            "sqlfilters": "(t.fk_statut:<:8)",
            "limit": 200,
            "properties": "id",
        })
        dashboard["tickets_open_count"] = len(tickets) if isinstance(tickets, list) else "?"
    except Exception as exc:
        dashboard["tickets_open"] = f"Erreur : {exc}"

    return _dumps(dashboard)


# ---------------------------------------------------------------------------
# Tools — Projets (création/modification)
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_create_project(
    title: str,
    thirdparty_id: int = 0,
    description: str = "",
    date_start: str = "",
    date_end: str = "",
    status: int = 1,
    opp_status: str = "",
    opp_amount: int = 0,
    budget_amount: int = 0,
    note_public: str = "",
    note_private: str = "",
) -> str:
    """Crée un nouveau projet dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - title : titre du projet (obligatoire)
    - thirdparty_id : ID du tiers rattaché (optionnel)
    - description : description du projet (optionnel)
    - date_start : date de début (format YYYY-MM-DD, optionnel)
    - date_end : date de fin (format YYYY-MM-DD, optionnel)
    - status : 0=brouillon, 1=ouvert (défaut 1)
    - opp_status : statut opportunité (prospect, qualification, proposal, negotiation, won, lost)
    - opp_amount : montant opportunité (optionnel)
    - budget_amount : budget prévu (optionnel)
    - note_public : note publique (optionnel)
    - note_private : note privée (optionnel)

    Retourne l'ID du projet créé (+ ref auto-numérotée si status=1).

    Implémentation interne en 3 étapes (transparent à l'appelant) :
      1. POST /projects minimal (title + socid) en status=0 / ref="PROV"
         → contournement du bug PROV-1 (duplicate ref si payload lourd
            casse la transaction au moment du renommage auto).
      2. PUT /projects/{id} avec tous les champs d'enrichissement.
      3. Si status=1 demandé : POST /projects/{id}/validate
         → Dolibarr renomme PROV → PJ-YYMM-XXXX et passe en status 1.

    Si l'étape 1 échoue, l'erreur est remontée et les étapes 2-3 ne sont
    pas tentées. Si une étape ultérieure échoue, le projet reste créé
    et un champ `*_warning` est ajouté à la réponse.
    """
    try:
        if not title:
            return "Le titre (title) est obligatoire."

        # --- Étape 1 : création minimale (status=0, payload minimal) ---
        # Le bug PROV-1 vient du fait que Dolibarr réserve la ref temporaire "PROV"
        # à la création, puis la renomme en PJ-YYMM-XXXX au passage status 0→1.
        # Quand le payload est lourd (opp_status + montants + notes longues), la
        # transaction casse au moment du renommage → contrainte unique violée.
        # On crée donc en brouillon minimal pour ne pas déclencher le renommage.
        minimal: dict[str, Any] = {
            "ref": "PROV",
            "title": title,
            "fk_statut": "0",
        }
        if thirdparty_id:
            minimal["socid"] = str(thirdparty_id)

        logger.debug("dolibarr_create_project step 1 POST projects %s", minimal)
        project_id = await api_post("projects", minimal)

        # --- Étape 2 : enrichissement via PUT (si champs supplémentaires) ---
        enrich: dict[str, Any] = {}
        if description:
            enrich["description"] = description
        if date_start:
            enrich["date_start"] = date_start
        if date_end:
            enrich["date_end"] = date_end
        if opp_status:
            enrich["opp_status"] = opp_status
        if opp_amount:
            enrich["opp_amount"] = str(opp_amount)
        if budget_amount:
            enrich["budget_amount"] = str(budget_amount)
        if note_public:
            enrich["note_public"] = note_public
        if note_private:
            enrich["note_private"] = note_private

        enrich_warning: str | None = None
        if enrich:
            logger.debug("dolibarr_create_project step 2 PUT projects/%s %s", project_id, enrich)
            try:
                await api_put(f"projects/{project_id}", enrich)
            except Exception as enrich_exc:
                enrich_warning = (
                    f"Projet créé (id={project_id}) mais enrichissement partiel échoué : "
                    f"{enrich_exc}. À compléter via dolibarr_update_project."
                )
                logger.warning("dolibarr_create_project enrich failed: %s", enrich_exc)

        # --- Étape 3 : validation si status=1 demandé ---
        # POST /projects/{id}/validate appelle Project::setValid() côté Dolibarr,
        # ce qui renomme PROV → PJ-YYMM-XXXX et transitionne status 0→1.
        final_status = 0
        final_ref = "PROV"
        validation_warning: str | None = None

        if status == 1:
            logger.debug("dolibarr_create_project step 3 POST projects/%s/validate", project_id)
            try:
                # Dolibarr v23 exige notrigger en entier (idem validate_proposal).
                await api_post(f"projects/{project_id}/validate", {"notrigger": 0})
                final_status = 1
                # Récupérer la nouvelle ref auto-numérotée
                try:
                    project_info = await api_get(f"projects/{project_id}")
                    if isinstance(project_info, dict):
                        final_ref = project_info.get("ref") or "PROV"
                except Exception:
                    pass
            except Exception as val_exc:
                validation_warning = (
                    f"Projet créé en brouillon (id={project_id}, status=0, ref=PROV). "
                    f"Validation à effectuer manuellement dans l'UI Dolibarr "
                    f"(carte projet → Valider). Détail : {val_exc}"
                )
                logger.warning("dolibarr_create_project validate failed: %s", val_exc)

        response: dict[str, Any] = {
            "success": True,
            "project_id": project_id,
            "title": title,
            "status": final_status,
            "ref": final_ref,
            "message": f"Projet '{title}' créé (id={project_id}, ref={final_ref}, status={final_status}).",
        }
        if enrich_warning:
            response["enrich_warning"] = enrich_warning
        if validation_warning:
            response["validation_warning"] = validation_warning

        return _dumps(response)

    except Exception as exc:
        # Étape 1 a échoué (ou exception avant) → ne pas tenter les suivantes
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_project(
    project_id: int,
    title: str = "",
    thirdparty_id: int = 0,
    description: str = "",
    date_start: str = "",
    date_end: str = "",
    status: int = -1,
    opp_status: str = "",
    opp_amount: int = 0,
    budget_amount: int = 0,
    note_public: str = "",
    note_private: str = "",
) -> str:
    """Met à jour un projet existant dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - project_id : ID du projet (obligatoire)
    - Tous les autres champs sont optionnels (seuls les non-vides sont mis à jour)

    Retourne les détails du projet mis à jour.
    """
    try:
        if not project_id:
            return "Veuillez fournir un project_id."

        data: dict[str, Any] = {}

        if title:
            data["title"] = title
        if thirdparty_id:
            data["socid"] = str(thirdparty_id)
        if description:
            data["description"] = description
        if date_start:
            data["date_start"] = date_start
        if date_end:
            data["date_end"] = date_end
        if status >= 0:
            data["fk_statut"] = str(status)
        if opp_status:
            data["opp_status"] = opp_status
        if opp_amount:
            data["opp_amount"] = str(opp_amount)
        if budget_amount:
            data["budget_amount"] = str(budget_amount)
        if note_public:
            data["note_public"] = note_public
        if note_private:
            data["note_private"] = note_private

        if not data:
            return "Aucun champ à mettre à jour fourni."

        await api_put(f"projects/{project_id}", data)

        return _dumps({
            "success": True,
            "project_id": project_id,
            "message": f"Projet {project_id} mis à jour avec succès.",
            "updated_fields": list(data.keys()),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_project(project_id: int) -> str:
    """Supprime un projet Dolibarr via DELETE /api/projects/{id}.

    Confirmation utilisateur requise avant exécution (action destructive).

    Paramètres :
    - project_id : ID du projet à supprimer (obligatoire)

    Comportement :
    - En cas de succès → retourne `{success: true, project_id, message}`.
    - En cas de HTTP 500 (typique d'un projet PROV resté à mi-chemin du
      renommage, d'un trigger custom qui crashe, ou d'une FK orpheline) →
      retourne un diagnostic complet avec :
        - état courant du projet (ref, statut, socid)
        - nombre de tâches restantes
        - extrait du corps d'erreur Dolibarr (utile : message "Cannot
          delete project, child element exists")
        - pistes SQL pour nettoyer manuellement les FK orphelines
    """
    try:
        if not project_id:
            return "Veuillez fournir un project_id."

        # Snapshot état avant suppression (pour diagnostic si 500)
        snapshot: dict[str, Any] = {}
        try:
            p = await api_get(f"projects/{project_id}")
            snapshot = {
                "ref": p.get("ref"),
                "title": p.get("title"),
                "status": p.get("fk_statut") or p.get("status"),
                "socid": p.get("socid"),
            }
        except Exception:
            pass

        tasks_count: int | None = None
        try:
            tasks = await api_get(f"projects/{project_id}/tasks")
            tasks_count = len(tasks) if isinstance(tasks, list) else None
        except Exception:
            pass

        try:
            await api_delete(f"projects/{project_id}")
            return _dumps({
                "success": True,
                "project_id": project_id,
                "message": f"Projet {project_id} supprimé."
                + (f" (ref={snapshot.get('ref')})" if snapshot.get('ref') else ""),
            })
        except httpx.HTTPStatusError as http_exc:
            status = http_exc.response.status_code
            body = http_exc.response.text[:800]
            if status == 500:
                return _dumps({
                    "success": False,
                    "project_id": project_id,
                    "http_status": 500,
                    "project_state": snapshot,
                    "tasks_remaining": tasks_count,
                    "dolibarr_body": body,
                    "diagnostic": (
                        "HTTP 500 sur DELETE /projects/{id}. Causes typiques :\n"
                        "  1. FK enfant non gérée par Project::delete() — Dolibarr abandonne.\n"
                        "  2. Trigger PROJECT_DELETE custom qui lève une exception PHP.\n"
                        "  3. Projet PROV laissé à mi-chemin (renommage cassé).\n"
                        "Étapes recommandées :\n"
                        "  a. Vérifier le log d'erreur du serveur web / PHP (Apache ou nginx) :\n"
                        "     chercher 'project|fatal|error' dans le error.log de l'instance Dolibarr\n"
                        "  b. Si FK orpheline, nettoyer en SQL avant retenter :\n"
                        "     SELECT * FROM llx_element_element WHERE fk_source="
                        f"{project_id} AND sourcetype='project';\n"
                        "     SELECT * FROM llx_element_element WHERE fk_target="
                        f"{project_id} AND targettype='project';\n"
                        "     SELECT * FROM llx_projet_task_time WHERE fk_task IN "
                        f"(SELECT rowid FROM llx_projet_task WHERE fk_projet={project_id});\n"
                        "     SELECT * FROM llx_ecm_files WHERE filepath LIKE 'projet/%' "
                        f"AND src_object_id={project_id};\n"
                        "  c. En dernier recours (irréversible — backup DB avant !) :\n"
                        f"     DELETE FROM llx_projet_extrafields WHERE fk_object={project_id};\n"
                        f"     DELETE FROM llx_element_contact WHERE element_id={project_id} "
                        "AND element_type='project';\n"
                        f"     DELETE FROM llx_projet WHERE rowid={project_id};\n"
                    ),
                })
            # 4xx ou autre 5xx : remonter tel quel
            raise

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_create_proposal(
    thirdparty_id: int,
    title: str = "",
    project_id: int = 0,
    date: str = "",
    date_validity: str = "",
    note_public: str = "",
    note_private: str = "",
) -> str:
    """Crée une proposition commerciale dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - thirdparty_id : ID du tiers (obligatoire)
    - title         : titre de la proposition (optionnel)
    - project_id    : ID du projet associé (optionnel)
    - date          : date de création (format YYYY-MM-DD, optionnel — défaut : aujourd'hui)
    - date_validity : date de fin de validité (format YYYY-MM-DD, optionnel).
                      Sans ce paramètre, Dolibarr peut afficher 15/02/1970
                      (bug timestamp : duree_validite*86400 interprété comme epoch)
                      quand la date de création est absente. Recommandé : toujours
                      fournir cette date, ex. aujourd'hui + 45 jours.
    - note_public   : note publique (optionnel)
    - note_private  : note privée (optionnel)

    Retourne l'ID de la proposition créée.
    """
    try:
        if not thirdparty_id:
            return "Le thirdparty_id est obligatoire."

        data: dict[str, Any] = {
            "ref": "PROV",
            "socid": str(thirdparty_id),
            "statut": "0",  # brouillon par défaut
        }

        if title:
            data["titre"] = title
        if project_id:
            data["fk_project"] = str(project_id)
        if date:
            data["date"] = date
        if date_validity:
            try:
                # Dolibarr stocke fin_validite comme timestamp Unix entier.
                # Envoyer une chaîne ISO ou un nombre de jours provoque le bug
                # "15/02/1970" (duree_validite*86400 interprété comme epoch).
                data["fin_validite"] = _date_str_to_ts(date_validity)
            except ValueError:
                return f"Format date_validity invalide : '{date_validity}'. Attendu : YYYY-MM-DD."
        if note_public:
            data["note_public"] = note_public
        if note_private:
            data["note_private"] = note_private

        result = await api_post("proposals", data)

        return _dumps({
            "success": True,
            "proposal_id": result,
            "thirdparty_id": thirdparty_id,
            "date_validity": date_validity or "(défaut Dolibarr)",
            "status": "brouillon",
            "message": f"Proposition créée avec succès pour le tiers {thirdparty_id}. "
                       "Utilisez dolibarr_add_proposal_lines pour ajouter des lignes.",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_add_proposal_lines(
    proposal_id: int,
    lines: list[dict],
) -> str:
    """Ajoute des lignes à une proposition commerciale Dolibarr (brouillon).

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - proposal_id : ID de la proposition (obligatoire, doit être en brouillon)
    - lines       : liste de lignes, chaque ligne est un dict avec :
        - description : libellé de la ligne (obligatoire)
        - qty         : quantité (défaut: 1)
        - subprice    : prix unitaire HT (obligatoire)
        - tva_tx      : taux TVA en % (ex: 20.0, défaut: 0)
        - fk_product  : ID produit/service Dolibarr (optionnel)

    Exemple :
    lines=[
      {"description":"Prestation conseil","qty":3,"subprice":800,"tva_tx":20},
      {"description":"Frais","qty":1,"subprice":150,"tva_tx":20}
    ]

    Retourne : confirmation avec le nombre de lignes ajoutées et le total HT.
    """
    try:
        if not proposal_id:
            return "Le proposal_id est obligatoire."
        if not lines:
            return "La liste lines est vide — aucune ligne à ajouter."

        added = []
        total_ht = 0.0

        for i, line in enumerate(lines):
            desc = line.get("description") or line.get("desc") or ""
            if not desc:
                return f"Ligne {i+1} : description manquante."
            qty = float(line.get("qty", 1))
            subprice = float(line.get("subprice", line.get("price", 0)))
            tva_tx = float(line.get("tva_tx", 0))
            product_id = line.get("fk_product")

            line_data: dict[str, Any] = {
                "desc": desc,
                "qty": qty,
                "subprice": subprice,
                "tva_tx": tva_tx,
                "product_type": 1,  # service par défaut
            }
            if product_id:
                line_data["fk_product"] = product_id

            # Dolibarr v23 REST API : POST /proposals/{id}/lines attend un tableau JSON
            # [line_object], PAS un objet nu.
            #
            # Cause racine (api_proposals.class.php::postLines) :
            #   foreach ($request_data as $TData) — itère sur les clés du dict
            #   → iter 1 : $TData = valeur de "desc" (string) → foreach() warning, 0 ligne
            #   → iter 2..N : $TData = scalaire → empty($TData[0]) = true → wrappé en array
            #                 → (object)$scalaire → stdClass vide → addline(null,...) → ligne vide
            # Pattern : N clés → N-1 lignes vides (la valeur string du 1er champ saute).
            # Avec [line_data] : outer foreach sur 1 élément (le dict) → correct.
            await api_post(f"proposals/{proposal_id}/lines", [line_data])
            total_ht += qty * subprice
            added.append({"description": desc, "qty": qty, "subprice": subprice, "total": qty * subprice})

        return _dumps({
            "success": True,
            "proposal_id": proposal_id,
            "lines_added": len(added),
            "total_ht": round(total_ht, 2),
            "lines": added,
            "message": f"{len(added)} ligne(s) ajoutée(s) à la proposition {proposal_id}. Total HT : {round(total_ht, 2)} €.",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Détail facture / propal + validate + download PDF
# ---------------------------------------------------------------------------


def _format_invoice_status(fk_statut: Any, paye: Any) -> str:
    """Convertit (fk_statut, paye) d'une facture en label lisible."""
    s = str(fk_statut or "0")
    p = str(paye or "0")
    mapping = {"0": "brouillon", "1": "validée non payée", "2": "payée", "3": "abandonnée"}
    label = mapping.get(s, s)
    if s == "1" and p == "1":
        label = "payée"
    return label


def _format_proposal_status(fk_statut: Any) -> str:
    """Convertit fk_statut d'une propal en label lisible."""
    mapping = {"0": "brouillon", "1": "validée", "2": "signée", "3": "refusée", "4": "facturée"}
    return mapping.get(str(fk_statut or "0"), str(fk_statut))


async def _proposal_is_draft(proposal_id: int) -> "tuple[bool, str]":
    """Retourne (is_draft, status_label) pour une propale.

    Sert de garde-fou aux éditions de ligne : Dolibarr verrouille les lignes
    d'une propale non-brouillon et ignore SILENCIEUSEMENT un PUT/DELETE de ligne
    (réponse HTTP 200 sans effet). En cas d'échec de lecture, considère la propale
    comme brouillon (best-effort : ne pas bloquer sur un hoquet réseau)."""
    try:
        p = await api_get(f"proposals/{proposal_id}")
        st = (p.get("fk_statut") or p.get("status")) if isinstance(p, dict) else "0"
    except Exception:
        return True, "?"
    return str(st) == "0", _format_proposal_status(st)


def _strip_html(text: Any) -> str:
    """Nettoie un texte HTML Dolibarr (descriptions de lignes) : convertit les
    sauts de ligne (<br>, </p>…), supprime les balises, décode les entités
    (&nbsp;, &eacute;, &amp;…) et normalise les espaces. Retourne du texte brut."""
    import html as _html
    import re

    s = str(text or "")
    if not s:
        return ""
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*(?:p|div|li|tr|h[1-6])\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)          # autres balises HTML
    s = _html.unescape(s)                   # entités &nbsp; &amp; &eacute; …
    s = s.replace("\xa0", " ")             # nbsp résiduel (U+00A0)
    s = re.sub(r"[ \t]+", " ", s)          # espaces multiples → 1
    s = re.sub(r"\n[ \t]*\n+", "\n", s)    # lignes vides multiples → 1
    return s.strip()


def _num(value: Any) -> Any:
    """Convertit une valeur numérique Dolibarr (ex '1500.00000000') en int/float
    compact. Renvoie None si vide, ou la valeur d'origine si non numérique."""
    if value in (None, ""):
        return None
    try:
        f = float(value)
        return int(f) if f == int(f) else round(f, 4)
    except (TypeError, ValueError):
        return value


def _line_type_label(product_type: Any) -> str:
    """0 → 'produit', 1 → 'service' (autre valeur renvoyée telle quelle)."""
    return {"0": "produit", "1": "service", 0: "produit", 1: "service"}.get(
        product_type, str(product_type or "")
    )


def _format_doc_line(line: dict) -> dict:
    """Formate une ligne de document commercial (facture client/fournisseur,
    proposition, commande) en dict riche et compact.

    Inclut : identifiant de ligne, ordre (rang), type produit/service, produit
    lié (id/ref/label), libellé nettoyé du HTML, quantité, prix unitaire,
    remise %, taux de TVA, totaux HT/TVA/TTC et dates de prestation.
    Les champs vides (None / chaîne vide) sont omis pour limiter le bruit."""
    if not isinstance(line, dict):
        return {}
    fk_product = line.get("fk_product")
    out = {
        "line_id": _num(line.get("id") or line.get("rowid")),
        "rang": _num(line.get("rang")),
        "type": _line_type_label(line.get("product_type")),
        "product_id": _num(fk_product) if fk_product not in (None, "", "0", 0) else None,
        "product_ref": line.get("product_ref") or None,
        "product_label": line.get("product_label") or None,
        "label": _strip_html(
            line.get("desc") or line.get("description")
            or line.get("label") or line.get("libelle") or ""
        ),
        "qty": _num(line.get("qty")),
        "subprice": _num(line.get("subprice")),
        "remise_percent": _num(line.get("remise_percent")),
        "tva_tx": _num(line.get("tva_tx")),
        "total_ht": _num(line.get("total_ht")),
        "total_tva": _num(line.get("total_tva")),
        "total_ttc": _num(line.get("total_ttc")),
        "date_start": _ts_to_date(line.get("date_start")) or None,
        "date_end": _ts_to_date(line.get("date_end")) or None,
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


@mcp.tool()
async def dolibarr_get_invoice(invoice_id: int = 0, ref: str = "") -> str:
    """Détail d'une facture client par ID numérique ou par référence.

    Fournir soit invoice_id, soit ref. Retourne les informations complètes :
    ref, tiers, montants HT/TTC/TVA, statut, échéance, paiements associés
    (si paye=1, indique le montant payé), et TOUTES les lignes détaillées
    (id, type produit/service, produit lié, libellé, quantité, prix, remise %,
    TVA, totaux, dates de prestation).

    Paramètres :
    - invoice_id : ID de la facture
    - ref : référence (ex: FA2605-0001, ou (PROV1234) pour un brouillon)
    """
    try:
        if ref and not invoice_id:
            invoices = await api_get("invoices", {"sqlfilters": f"(t.ref:=:'{_sf_escape(ref)}')"})
            if not invoices:
                return f"Aucune facture trouvée avec la référence '{ref}'."
            invoice_id = invoices[0]["id"]

        if not invoice_id:
            return "Veuillez fournir un invoice_id ou une ref."

        inv = await api_get(f"invoices/{invoice_id}")
        if not isinstance(inv, dict):
            return f"Réponse inattendue de l'API : {inv}"

        # Lignes complètes et enrichies (id, type, produit, remise, dates…)
        lines_raw = inv.get("lines") or []
        lines = [_format_doc_line(ln) for ln in lines_raw]

        result = {
            "id": inv.get("id"),
            "ref": inv.get("ref"),
            "type": inv.get("type"),
            "thirdparty_id": inv.get("socid"),
            "thirdparty_name": inv.get("thirdparty_name") or inv.get("name_alias"),
            "status": inv.get("fk_statut") or inv.get("status"),
            "status_label": _format_invoice_status(inv.get("fk_statut") or inv.get("status"), inv.get("paye")),
            "paye": inv.get("paye"),
            "total_ht": inv.get("total_ht"),
            "total_tva": inv.get("total_tva"),
            "total_ttc": inv.get("total_ttc"),
            "date_invoice": _ts_to_date(inv.get("date")),
            "date_due": _ts_to_date(inv.get("date_lim_reglement")),
            "fk_project": inv.get("fk_project"),
            "ref_customer": inv.get("ref_client"),
            "note_public": _strip_html(inv.get("note_public"))[:300],
            "model_pdf": inv.get("model_pdf"),
            "lines_count": len(lines_raw),
            "lines": lines,
        }
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_proposal(proposal_id: int = 0, ref: str = "") -> str:
    """Détail d'une proposition commerciale par ID numérique ou par référence.

    Fournir soit proposal_id, soit ref. Retourne ref, tiers, montants, statut,
    date de validité, et TOUTES les lignes détaillées (id, type produit/service,
    produit lié, libellé, quantité, prix, remise %, TVA, totaux, dates).

    Paramètres :
    - proposal_id : ID de la propal
    - ref : référence (ex: PR2605-0001, ou (PROV137) pour un brouillon)
    """
    try:
        if ref and not proposal_id:
            propals = await api_get("proposals", {"sqlfilters": f"(t.ref:=:'{_sf_escape(ref)}')"})
            if not propals:
                return f"Aucune proposition trouvée avec la référence '{ref}'."
            proposal_id = propals[0]["id"]

        if not proposal_id:
            return "Veuillez fournir un proposal_id ou une ref."

        p = await api_get(f"proposals/{proposal_id}")
        if not isinstance(p, dict):
            return f"Réponse inattendue : {p}"

        lines_raw = p.get("lines") or []
        lines = [_format_doc_line(ln) for ln in lines_raw]

        result = {
            "id": p.get("id"),
            "ref": p.get("ref"),
            "thirdparty_id": p.get("socid"),
            "thirdparty_name": p.get("thirdparty_name") or p.get("name_alias"),
            "status": p.get("fk_statut") or p.get("status"),
            "status_label": _format_proposal_status(p.get("fk_statut") or p.get("status")),
            "total_ht": p.get("total_ht"),
            "total_tva": p.get("total_tva"),
            "total_ttc": p.get("total_ttc"),
            "date": _ts_to_date(p.get("date")),
            "date_validity": _ts_to_date(p.get("fin_validite") or p.get("date_validity")),
            "fk_project": p.get("fk_project"),
            "ref_customer": p.get("ref_client"),
            "note_public": _strip_html(p.get("note_public"))[:300],
            "model_pdf": p.get("model_pdf"),
            "lines_count": len(lines_raw),
            "lines": lines,
        }
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_supplier_invoice(invoice_id: int = 0, ref: str = "") -> str:
    """Détail d'une facture FOURNISSEUR par ID numérique ou par référence.

    Pendant fournisseur de dolibarr_get_invoice. Fournir soit invoice_id, soit
    ref (ref interne Dolibarr type SI2602-0084). Retourne ref interne, ref
    fournisseur, tiers, libellé, montants HT/TVA/TTC, statut, échéance, projet
    lié, et TOUTES les lignes détaillées (mêmes champs que get_invoice).

    Paramètres :
    - invoice_id : ID de la facture fournisseur
    - ref : référence interne Dolibarr (ex: SI2602-0084)
    """
    try:
        if ref and not invoice_id:
            invs = await api_get(
                "supplierinvoices",
                {"sqlfilters": f"(t.ref:=:'{_sf_escape(ref)}')"},
            )
            if not invs:
                return f"Aucune facture fournisseur trouvée avec la référence '{ref}'."
            invoice_id = invs[0]["id"]

        if not invoice_id:
            return "Veuillez fournir un invoice_id ou une ref."

        inv = await api_get(f"supplierinvoices/{invoice_id}")
        if not isinstance(inv, dict):
            return f"Réponse inattendue de l'API : {inv}"

        lines_raw = inv.get("lines") or []
        lines = [_format_doc_line(ln) for ln in lines_raw]

        result = {
            "id": inv.get("id"),
            "ref": inv.get("ref"),
            "ref_supplier": inv.get("ref_supplier"),
            "thirdparty_id": inv.get("socid"),
            "thirdparty_name": inv.get("thirdparty_name") or inv.get("name_alias"),
            "label": inv.get("libelle") or inv.get("label"),
            "status": inv.get("fk_statut") or inv.get("status"),
            "status_label": _format_invoice_status(
                inv.get("fk_statut") or inv.get("status"), inv.get("paye")
            ),
            "paye": inv.get("paye"),
            "total_ht": inv.get("total_ht"),
            "total_tva": inv.get("total_tva"),
            "total_ttc": inv.get("total_ttc"),
            "date_invoice": _ts_to_date(inv.get("date") or inv.get("datef")),
            "date_due": _ts_to_date(
                inv.get("date_echeance") or inv.get("date_lim_reglement")
            ),
            "fk_project": inv.get("fk_project"),
            "note_public": _strip_html(inv.get("note_public"))[:300],
            "model_pdf": inv.get("model_pdf"),
            "lines_count": len(lines_raw),
            "lines": lines,
        }
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_validate_invoice(invoice_id: int) -> str:
    """Valide une facture brouillon (statut 0 → 1) via POST /invoices/{id}/validate.

    Confirmation utilisateur requise avant exécution.

    Effets :
    - La référence `(PROVxxxx)` est remplacée par la ref définitive (FAxxxxx)
    - Le PDF est régénéré au nouveau chemin par `card.php` après validate
    - Les triggers BILL_VALIDATE puis FACTURE_BUILDDOC se déclenchent
      (utiles pour NextBarr : remplacement PROV → FA dans Nextcloud)

    Paramètres :
    - invoice_id : ID de la facture brouillon (obligatoire)

    Retourne : nouvelle ref, statut, montants. En cas d'échec, message
    typique : "ErrorObjectMustHaveLinesToBeValidated" (facture sans ligne)
    ou "Permission denied".
    """
    try:
        if not invoice_id:
            return "Veuillez fournir un invoice_id."

        await api_post(f"invoices/{invoice_id}/validate", {})

        # Re-fetch pour obtenir la nouvelle ref + statut
        try:
            inv = await api_get(f"invoices/{invoice_id}")
            return _dumps({
                "success": True,
                "invoice_id": invoice_id,
                "ref": inv.get("ref"),
                "status": inv.get("fk_statut") or inv.get("status"),
                "status_label": _format_invoice_status(inv.get("fk_statut") or inv.get("status"), inv.get("paye")),
                "total_ttc": inv.get("total_ttc"),
                "message": f"Facture validée : {inv.get('ref')}.",
            })
        except Exception:
            return _dumps({"success": True, "invoice_id": invoice_id, "message": "Validation OK (re-fetch échoué)."})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_invoice(
    invoice_id: int,
    project_id: int = 0,
    ref_customer: str = "",
    note_public: str = "",
    note_private: str = "",
    date_due: str = "",
) -> str:
    """Met à jour une facture existante dans Dolibarr (PUT /invoices/{id}).

    Confirmation utilisateur requise avant exécution. Seuls les champs non vides
    sont transmis.

    Paramètres :
    - invoice_id   : ID de la facture (obligatoire)
    - project_id   : rattacher la facture à ce projet — champ API `fk_project`.
                     0 = ne pas toucher au lien projet.
    - ref_customer : référence client (champ API `ref_client`)
    - note_public  : note publique
    - note_private : note privée
    - date_due     : date limite de règlement (format YYYY-MM-DD)

    Statut — vérifié en production le 11/08/2026 sur FA2608-0939 (Dolibarr
    23.0.0) : un PUT sur une facture **validée non payée** (fk_statut=1,
    paye=0) renvoie HTTP 200 et le champ est persisté. `Facture::update()`
    écrit `fk_projet` sans aucun garde de statut, et `api_invoices::put()`
    n'en pose pas non plus. Le rattachement projet ne demande donc pas de
    repasser la facture en brouillon.

    Effet de bord mesuré sur ce même essai : `fk_user_modif` prend l'id de
    l'utilisateur de la clé API et le trigger FACTURE_MODIFY se déclenche.
    Aucun autre champ ne bouge — `Facture::update()` réécrit toute la ligne,
    mais à partir de l'objet relu juste avant, donc à valeurs identiques.

    Piège d'interface, sans rapport avec l'API : dans `card.php`, le sélecteur
    de projet d'une facture **ne liste pas les projets fermés**. Un champ qui
    paraît vide dans l'UI ne signifie donc pas « aucun projet rattaché » —
    seule la valeur `fk_project` rendue ici fait foi.

    Retour JSON : success, invoice_id, ref, status, status_label, fk_project,
    updated_fields, et le cas échéant project_link_warning / refetch_warning.
    """
    try:
        if not invoice_id:
            return "Veuillez fournir un invoice_id."

        data: dict[str, Any] = {}
        if project_id:
            data["fk_project"] = str(project_id)
        if ref_customer:
            data["ref_client"] = ref_customer
        if note_public:
            data["note_public"] = note_public
        if note_private:
            data["note_private"] = note_private
        if date_due:
            try:
                # date_lim_reglement est stockée en timestamp Unix entier.
                data["date_lim_reglement"] = _date_str_to_ts(date_due)
            except ValueError:
                return f"Format date_due invalide : '{date_due}'. Attendu : YYYY-MM-DD."

        if not data:
            return "Aucun champ à mettre à jour fourni."

        await api_put(f"invoices/{invoice_id}", data)

        # Read-after-write : relire la facture pour (a) remonter ref/statut/lien
        # projet réels et (b) détecter un rattachement projet non persisté.
        response: dict[str, Any] = {
            "success": True,
            "invoice_id": invoice_id,
            "updated_fields": list(data.keys()),
        }
        try:
            inv = await api_get(f"invoices/{invoice_id}")
            if isinstance(inv, dict):
                response["ref"] = inv.get("ref")
                response["status"] = inv.get("fk_statut") or inv.get("status")
                response["status_label"] = _format_invoice_status(
                    inv.get("fk_statut") or inv.get("status"), inv.get("paye")
                )
                response["fk_project"] = inv.get("fk_project")
                if project_id and str(inv.get("fk_project") or "") != str(project_id):
                    response["project_link_warning"] = (
                        f"Le rattachement au projet {project_id} ne semble pas avoir été "
                        f"persisté (fk_project={inv.get('fk_project')!r}). Vérifier que le "
                        f"projet existe et les droits de l'utilisateur API."
                    )
        except Exception as refetch_exc:
            response["refetch_warning"] = (
                f"Mise à jour envoyée mais relecture échouée : {refetch_exc}"
            )

        response["message"] = (
            f"Facture {invoice_id} mise à jour ({', '.join(data.keys())})."
        )
        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_validate_proposal(proposal_id: int) -> str:
    """Valide une proposition commerciale brouillon via POST /proposals/{id}/validate.

    Confirmation utilisateur requise avant exécution.

    Effets : `(PROVxxxx)` → PRxxxxx, statut 0 → 1, PDF régénéré, triggers
    PROPAL_VALIDATE puis PROPAL_BUILDDOC déclenchés (NextBarr sync).

    Paramètres :
    - proposal_id : ID de la propal brouillon (obligatoire)

    Retourne : nouvelle ref, statut, montants.
    """
    try:
        if not proposal_id:
            return "Veuillez fournir un proposal_id."

        # Dolibarr v23 exige le paramètre notrigger en entier (0 ou 1).
        # Envoyer {} provoque HTTP 400 "Invalid value specified for `notrigger`".
        await api_post(f"proposals/{proposal_id}/validate", {"notrigger": 0})

        try:
            p = await api_get(f"proposals/{proposal_id}")
            return _dumps({
                "success": True,
                "proposal_id": proposal_id,
                "ref": p.get("ref"),
                "status": p.get("fk_statut") or p.get("status"),
                "status_label": _format_proposal_status(p.get("fk_statut") or p.get("status")),
                "total_ttc": p.get("total_ttc"),
                "message": f"Proposition validée : {p.get('ref')}.",
            })
        except Exception:
            return _dumps({"success": True, "proposal_id": proposal_id, "message": "Validation OK (re-fetch échoué)."})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_proposal(
    proposal_id: int,
    project_id: int = 0,
    title: str = "",
    date: str = "",
    date_validity: str = "",
    note_public: str = "",
    note_private: str = "",
    ref_customer: str = "",
) -> str:
    """Met à jour une proposition commerciale existante dans Dolibarr.

    Calqué sur dolibarr_update_project. Confirmation utilisateur requise avant
    exécution. Seuls les champs non vides sont transmis (PUT /proposals/{id}).

    Paramètres :
    - proposal_id   : ID de la proposition (obligatoire)
    - project_id    : rattacher la propal à ce projet — champ API `fk_project`.
                      0 = ne pas toucher au lien projet.
    - title         : titre/objet de la proposition (champ `titre`)
    - date          : date de la proposition (format YYYY-MM-DD)
    - date_validity : date de fin de validité (format YYYY-MM-DD)
    - note_public   : note publique
    - note_private  : note privée
    - ref_customer  : référence client (champ API `ref_client`)

    Lien projet — vérifié en prod (Dolibarr 23.0.0) : l'API REST expose la
    propriété `fk_project` (PAS `fk_projet`, qui est l'orthographe de la colonne
    SQL) ; `Propal::update()` persiste bien le lien. Un PUT {"fk_project":"258"}
    relu via GET renvoie fk_project="258".

    Statut — l'update est autorisé quel que soit le statut : rattacher un projet
    et éditer les notes restent possibles sur une propale *validée* (comme dans
    l'UI Dolibarr). Pour les propales validées/signées/facturées, Dolibarr peut
    cependant ignorer certains champs (dates, montants) ; la réponse relit la
    propale et signale via `project_link_warning` tout rattachement projet
    demandé qui n'aurait pas été persisté.

    Retour JSON : success, proposal_id, ref, status, status_label, fk_project,
    updated_fields, et le cas échéant project_link_warning / refetch_warning.
    """
    try:
        if not proposal_id:
            return "Veuillez fournir un proposal_id."

        data: dict[str, Any] = {}
        if project_id:
            data["fk_project"] = str(project_id)
        if title:
            data["titre"] = title
        if date:
            try:
                # Dolibarr stocke la date de propal en timestamp Unix entier
                # (cohérent avec dolibarr_create_proposal).
                data["date"] = _date_str_to_ts(date)
            except ValueError:
                return f"Format date invalide : '{date}'. Attendu : YYYY-MM-DD."
        if date_validity:
            try:
                # fin_validite = timestamp Unix (sinon bug d'affichage 15/02/1970).
                data["fin_validite"] = _date_str_to_ts(date_validity)
            except ValueError:
                return f"Format date_validity invalide : '{date_validity}'. Attendu : YYYY-MM-DD."
        if note_public:
            data["note_public"] = note_public
        if note_private:
            data["note_private"] = note_private
        if ref_customer:
            data["ref_client"] = ref_customer

        if not data:
            return "Aucun champ à mettre à jour fourni."

        await api_put(f"proposals/{proposal_id}", data)

        # Read-after-write : relire la propale pour (a) remonter ref/statut/lien
        # projet réels et (b) détecter un rattachement projet non persisté (cas
        # d'une propale verrouillée par son statut ou de droits insuffisants).
        response: dict[str, Any] = {
            "success": True,
            "proposal_id": proposal_id,
            "updated_fields": list(data.keys()),
        }
        try:
            p = await api_get(f"proposals/{proposal_id}")
            if isinstance(p, dict):
                response["ref"] = p.get("ref")
                response["status"] = p.get("fk_statut") or p.get("status")
                response["status_label"] = _format_proposal_status(
                    p.get("fk_statut") or p.get("status")
                )
                response["fk_project"] = p.get("fk_project")
                if project_id and str(p.get("fk_project") or "") != str(project_id):
                    response["project_link_warning"] = (
                        f"Le rattachement au projet {project_id} ne semble pas avoir été "
                        f"persisté (fk_project={p.get('fk_project')!r}). Vérifier le statut "
                        f"de la propale (verrouillage) ou les droits de l'utilisateur API."
                    )
        except Exception as refetch_exc:
            response["refetch_warning"] = (
                f"Mise à jour envoyée mais relecture échouée : {refetch_exc}"
            )

        response["message"] = (
            f"Proposition {proposal_id} mise à jour ({', '.join(data.keys())})."
        )
        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_proposal(proposal_id: int, force: bool = False) -> str:
    """Supprime une proposition commerciale via DELETE /api/proposals/{id}.

    Confirmation utilisateur requise avant exécution (action destructive).
    Pendant propal de dolibarr_delete_project.

    Paramètres :
    - proposal_id : ID de la proposition à supprimer (obligatoire)
    - force       : défaut False — seuls les BROUILLONS (statut 0) sont
                    supprimables. Passer force=True pour supprimer malgré tout
                    une propale validée/signée/refusée/facturée (assumé par
                    l'appelant : liens commandes/factures potentiellement
                    orphelins).

    Comportement :
    - Succès → `{success: true, proposal_id, ref, message}`. Vérifié en prod
      23.0.0 : la réponse Dolibarr est HTTP 200 + `{"success":{"code":200,
      "message":"Commercial Proposal deleted"}}`, et un GET ultérieur renvoie 404.
    - HTTP 500 (trigger custom qui crashe, FK orpheline, lien element_element non
      géré) → diagnostic complet : état courant (ref, statut, socid), nombre de
      lignes, extrait du corps d'erreur Dolibarr et pistes SQL de nettoyage.

    Garde-fou statut : si la propale n'est PAS en brouillon, l'outil REFUSE la
    suppression (`error: "proposal_not_draft"`) sauf force=True. Statut
    invérifiable (échec de lecture hors 404) → refus également (`error:
    "status_unverifiable"`) sauf force=True. Propale introuvable → `error:
    "not_found"` sans tenter le DELETE. Pour les propales engagées, préférer
    les classer (refusée) dans Dolibarr plutôt que les supprimer.
    """
    try:
        if not proposal_id:
            return "Veuillez fournir un proposal_id."

        # Snapshot état avant suppression (garde-fou statut + diagnostic si 500)
        snapshot: dict[str, Any] = {}
        snapshot_error: str | None = None
        lines_count: int | None = None
        try:
            p = await api_get(f"proposals/{proposal_id}")
            if isinstance(p, dict):
                snapshot = {
                    "ref": p.get("ref"),
                    "status": p.get("fk_statut") or p.get("status"),
                    "status_label": _format_proposal_status(
                        p.get("fk_statut") or p.get("status")
                    ),
                    "socid": p.get("socid"),
                    "fk_project": p.get("fk_project"),
                }
                lines = p.get("lines")
                lines_count = len(lines) if isinstance(lines, list) else None
        except httpx.HTTPStatusError as get_exc:
            if get_exc.response.status_code == 404:
                return _dumps({
                    "success": False,
                    "proposal_id": proposal_id,
                    "error": "not_found",
                    "message": (
                        f"Proposition {proposal_id} introuvable (404) — rien à supprimer."
                    ),
                })
            snapshot_error = str(get_exc)
        except Exception as get_exc:
            snapshot_error = str(get_exc)

        # Garde-fou : seuls les brouillons sont supprimables, sauf force=True.
        # (Cohérent avec update/delete_proposal_line ; évite de détruire une
        # propale engagée — validée/signée/facturée — sur un simple appel.)
        if not force:
            if snapshot_error is not None or not snapshot:
                return _dumps({
                    "success": False,
                    "proposal_id": proposal_id,
                    "error": "status_unverifiable",
                    "read_error": snapshot_error,
                    "message": (
                        f"Impossible de vérifier le statut de la proposition "
                        f"{proposal_id} avant suppression. Réessayer, ou relancer "
                        f"avec force=True en connaissance de cause."
                    ),
                })
            if str(snapshot.get("status")) != "0":
                return _dumps({
                    "success": False,
                    "proposal_id": proposal_id,
                    "ref": snapshot.get("ref"),
                    "error": "proposal_not_draft",
                    "status_label": snapshot.get("status_label"),
                    "message": (
                        f"La proposition {proposal_id} est "
                        f"« {snapshot.get('status_label')} » : suppression refusée "
                        f"(seuls les brouillons sont supprimables). Préférer la "
                        f"classer refusée dans Dolibarr, ou relancer avec force=True "
                        f"en connaissance de cause (liens commandes/factures "
                        f"potentiellement orphelins)."
                    ),
                })

        try:
            await api_delete(f"proposals/{proposal_id}")
            forced = bool(force and str(snapshot.get("status") or "") != "0")
            return _dumps({
                "success": True,
                "proposal_id": proposal_id,
                "ref": snapshot.get("ref"),
                "forced": forced,
                "message": f"Proposition {proposal_id} supprimée."
                + (f" (ref={snapshot.get('ref')})" if snapshot.get("ref") else "")
                + (" [force=True : propale non-brouillon supprimée]" if forced else ""),
            })
        except httpx.HTTPStatusError as http_exc:
            status = http_exc.response.status_code
            body = http_exc.response.text[:800]
            if status == 500:
                return _dumps({
                    "success": False,
                    "proposal_id": proposal_id,
                    "http_status": 500,
                    "proposal_state": snapshot,
                    "lines_count": lines_count,
                    "dolibarr_body": body,
                    "diagnostic": (
                        "HTTP 500 sur DELETE /proposals/{id}. Causes typiques :\n"
                        "  1. Lien element_element (propal↔projet/commande/facture) non géré.\n"
                        "  2. Trigger PROPAL_DELETE custom qui lève une exception PHP.\n"
                        "  3. Propale engagée (signée/facturée) avec dépendances.\n"
                        "Étapes recommandées :\n"
                        "  a. Log PHP du serveur (le log applicatif Dolibarr est\n"
                        "     souvent vide sur ce chemin) :\n"
                        "     `tail -200 <error.log> | grep -iE 'propal|fatal|error'`\n"
                        "  b. Inspecter les liens et lignes avant nettoyage SQL :\n"
                        "     SELECT * FROM llx_element_element WHERE fk_source="
                        f"{proposal_id} AND sourcetype='propal';\n"
                        "     SELECT * FROM llx_element_element WHERE fk_target="
                        f"{proposal_id} AND targettype='propal';\n"
                        "     SELECT rowid FROM llx_propaldet WHERE fk_propal="
                        f"{proposal_id};\n"
                        "  c. En dernier recours (irréversible — backup DB avant !) :\n"
                        f"     DELETE FROM llx_propal_extrafields WHERE fk_object={proposal_id};\n"
                        f"     DELETE FROM llx_propaldet WHERE fk_propal={proposal_id};\n"
                        f"     DELETE FROM llx_element_contact WHERE element_id={proposal_id} "
                        "AND element_type='propal';\n"
                        f"     DELETE FROM llx_propal WHERE rowid={proposal_id};\n"
                    ),
                })
            # 4xx ou autre 5xx : remonter tel quel
            raise

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_proposal_line(
    proposal_id: int,
    line_id: int,
    fields: dict,
) -> str:
    """Met à jour une ligne existante d'une proposition (PUT /proposals/{id}/lines/{line_id}).

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - proposal_id : ID de la proposition (obligatoire)
    - line_id     : ID de la ligne à modifier (= line_id/rowid renvoyé par
                    dolibarr_get_proposal, obligatoire)
    - fields      : dict des champs à modifier, parmi :
        - description / desc : libellé
        - qty                : quantité
        - subprice           : prix unitaire HT
        - remise_percent     : remise en % sur la ligne
        - tva_tx             : taux TVA en %
        - product_type       : 0=produit, 1=service
        - date_start / date_end : dates de prestation (timestamp Unix)

    ⚠ La propale doit être en BROUILLON (status 0). Dolibarr verrouille les
    lignes d'une propale validée/signée/facturée → l'API renvoie alors une
    erreur. Repasser la propale en brouillon avant (UI : bouton « Modifier »),
    puis revalider.

    Exemple — porter une remise de 15 % sur une ligne existante (au lieu d'une
    ligne de remise négative séparée) : fields={"remise_percent": 15}.

    Retourne : success, proposal_id, line_id, champs envoyés, et les totaux
    relus de la proposition (total_ht/total_ttc).
    """
    try:
        if not proposal_id or not line_id:
            return "Veuillez fournir proposal_id et line_id."
        if not fields:
            return "Aucun champ à mettre à jour (fields est vide)."

        # Garde-fou statut : sur une propale non-brouillon, Dolibarr accepte le PUT
        # (HTTP 200) mais N'APPLIQUE PAS la modification → faux succès. On refuse.
        is_draft, status_label = await _proposal_is_draft(proposal_id)
        if not is_draft:
            return _dumps({
                "success": False,
                "proposal_id": proposal_id,
                "line_id": line_id,
                "error": "proposal_not_draft",
                "status_label": status_label,
                "message": (
                    f"La proposition {proposal_id} est « {status_label} » : ses lignes "
                    f"sont verrouillées (Dolibarr ignore silencieusement l'édition). "
                    f"Repassez-la en brouillon (dolibarr_set_proposal_draft), éditez la "
                    f"ligne, puis revalidez (dolibarr_validate_proposal)."
                ),
            })

        allowed = {
            "description", "desc", "qty", "subprice", "remise_percent",
            "tva_tx", "product_type", "date_start", "date_end", "rang", "label",
        }
        line_data: dict[str, Any] = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            # Normaliser description → desc (champ attendu par l'API).
            line_data["desc" if k == "description" else k] = v
        if not line_data:
            return (
                "Aucun champ reconnu dans fields. Champs supportés : "
                + ", ".join(sorted(allowed))
            )

        # PUT /proposals/{id}/lines/{lineid} attend l'objet ligne directement
        # (pas le tableau attendu par POST /lines).
        await api_put(f"proposals/{proposal_id}/lines/{line_id}", line_data)

        response: dict[str, Any] = {
            "success": True,
            "proposal_id": proposal_id,
            "line_id": line_id,
            "updated_fields": list(line_data.keys()),
        }
        try:
            p = await api_get(f"proposals/{proposal_id}")
            if isinstance(p, dict):
                response["total_ht"] = p.get("total_ht")
                response["total_ttc"] = p.get("total_ttc")
        except Exception:
            pass
        response["message"] = (
            f"Ligne {line_id} de la proposition {proposal_id} mise à jour."
        )
        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_proposal_line(proposal_id: int, line_id: int) -> str:
    """Supprime une ligne d'une proposition (DELETE /proposals/{id}/lines/{line_id}).

    Confirmation utilisateur requise avant exécution (action destructive).

    Paramètres :
    - proposal_id : ID de la proposition (obligatoire)
    - line_id     : ID de la ligne à supprimer (obligatoire)

    ⚠ Même contrainte que dolibarr_update_proposal_line : la propale doit être
    en BROUILLON. Utile notamment pour retirer une ligne de remise négative
    après avoir porté la remise sur la ligne de prestation via remise_percent.

    Retourne : success, proposal_id, line_id, et les totaux relus (total_ht/ttc).
    """
    try:
        if not proposal_id or not line_id:
            return "Veuillez fournir proposal_id et line_id."

        # Même garde-fou que update_proposal_line : suppression de ligne ignorée
        # silencieusement par Dolibarr sur une propale non-brouillon.
        is_draft, status_label = await _proposal_is_draft(proposal_id)
        if not is_draft:
            return _dumps({
                "success": False,
                "proposal_id": proposal_id,
                "line_id": line_id,
                "error": "proposal_not_draft",
                "status_label": status_label,
                "message": (
                    f"La proposition {proposal_id} est « {status_label} » : ses lignes "
                    f"sont verrouillées. Repassez-la en brouillon "
                    f"(dolibarr_set_proposal_draft) avant de supprimer la ligne, "
                    f"puis revalidez (dolibarr_validate_proposal)."
                ),
            })

        await api_delete(f"proposals/{proposal_id}/lines/{line_id}")

        response: dict[str, Any] = {
            "success": True,
            "proposal_id": proposal_id,
            "line_id": line_id,
        }
        try:
            p = await api_get(f"proposals/{proposal_id}")
            if isinstance(p, dict):
                response["total_ht"] = p.get("total_ht")
                response["total_ttc"] = p.get("total_ttc")
                lines = p.get("lines")
                response["lines_remaining"] = (
                    len(lines) if isinstance(lines, list) else None
                )
        except Exception:
            pass
        response["message"] = (
            f"Ligne {line_id} supprimée de la proposition {proposal_id}."
        )
        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_set_proposal_draft(proposal_id: int) -> str:
    """Repasse une proposition en BROUILLON via POST /proposals/{id}/settodraft.

    Confirmation utilisateur requise avant exécution.

    Nécessaire pour éditer/supprimer les lignes d'une propale déjà validée
    (Dolibarr les verrouille sinon — voir dolibarr_update_proposal_line). Workflow
    type pour corriger une ligne d'une propale validée :
      1. dolibarr_set_proposal_draft(id)            → statut 1→0
      2. dolibarr_update_proposal_line / dolibarr_delete_proposal_line
      3. dolibarr_validate_proposal(id)             → statut 0→1, PDF régénéré

    La ref définitive (PRyymm-xxxx) est CONSERVÉE : settodraft ne reprovisionne
    pas la numérotation, et revalider une ref déjà définitive ne la renomme pas.
    Vérifié en prod 23.0.0 (round-trip complet, totaux et ref inchangés).

    Retourne : success, proposal_id, ref, status, status_label.
    """
    try:
        if not proposal_id:
            return "Veuillez fournir un proposal_id."

        # settodraft accepte un corps vide ; certaines versions tolèrent
        # {"notrigger": 0}. Le corps vide suffit en 23.0.0.
        await api_post(f"proposals/{proposal_id}/settodraft", {})

        try:
            p = await api_get(f"proposals/{proposal_id}")
            return _dumps({
                "success": True,
                "proposal_id": proposal_id,
                "ref": p.get("ref"),
                "status": p.get("fk_statut") or p.get("status"),
                "status_label": _format_proposal_status(p.get("fk_statut") or p.get("status")),
                "message": f"Proposition {proposal_id} repassée en brouillon.",
            })
        except Exception:
            return _dumps({
                "success": True,
                "proposal_id": proposal_id,
                "message": "settodraft OK (re-fetch échoué).",
            })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_invoice_pdf_url(invoice_id: int) -> str:
    """Retourne l'URL Dolibarr du PDF d'une facture (lien direct pour téléchargement
    via navigateur authentifié, ou via API REST avec DOLAPIKEY).

    Note : l'API Dolibarr REST `/documents/download` exige un module_part='facture'
    et un original_file path. Cette helper construit l'URL sans télécharger
    physiquement le contenu binaire (économise tokens). À ouvrir dans le
    navigateur connecté ou à utiliser comme paramètre dans un envoi email.

    Paramètres :
    - invoice_id : ID de la facture

    Retourne : url, ref, last_main_doc (chemin relatif Dolibarr).
    """
    try:
        if not invoice_id:
            return "Veuillez fournir un invoice_id."

        inv = await api_get(f"invoices/{invoice_id}")
        if not isinstance(inv, dict):
            return f"Réponse inattendue : {inv}"

        ref = inv.get("ref", "")
        last_main_doc = inv.get("last_main_doc", "")
        if not last_main_doc:
            return _dumps({
                "success": False,
                "invoice_id": invoice_id,
                "ref": ref,
                "error": "Aucun PDF généré pour cette facture. Lancer la génération via l'UI ou via une validation.",
            })

        # URL "document.php" qui sert les fichiers (auth user/cookie Dolibarr)
        ui_url = (
            f"{DOLIBARR_URL}/document.php?modulepart=facture"
            f"&file={httpx.URL('').copy_with(params={'f': last_main_doc}).params.get('f') or last_main_doc}"
        )
        # Plus simple : URL directe via le pattern Dolibarr
        ui_url = f"{DOLIBARR_URL}/document.php?modulepart=facture&file={last_main_doc.replace('facture/', '', 1)}"
        api_url = f"{DOLIBARR_URL}/api/index.php/documents/download?modulepart=facture&original_file={last_main_doc.replace('facture/', '', 1)}"

        return _dumps({
            "success": True,
            "invoice_id": invoice_id,
            "ref": ref,
            "last_main_doc": last_main_doc,
            "ui_download_url": ui_url,
            "api_download_url": api_url,
            "note": "ui_download_url nécessite une session Dolibarr (cookie navigateur). api_download_url nécessite un header DOLAPIKEY.",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


#: Entry-point group used to discover optional, third-party tool extensions.
#: A separately-installed package (e.g. organisation-specific tools that depend
#: on custom Dolibarr modules) advertises a ``register`` callable here, e.g. in
#: its ``pyproject.toml``::
#:
#:     [project.entry-points."dolibarr_mcp.extensions"]
#:     my_org = "my_org_pkg.extension:register"
#:
#: The callable receives the live ``FastMCP`` instance and registers extra
#: ``@mcp.tool()``-style tools on it. The public core ships **no** such entry
#: point, so a vanilla install stays strictly generic.
EXTENSION_ENTRY_POINT_GROUP = "dolibarr_mcp.extensions"


def _register_extensions() -> None:
    """Discover and load optional tool extensions advertised via entry points."""
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group=EXTENSION_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - Python <3.10 fallback
        eps = entry_points().get(EXTENSION_ENTRY_POINT_GROUP, [])
    for ep in eps:
        try:
            register = ep.load()
            register(mcp)
        except Exception as exc:  # never let a bad extension break the core
            sys.stderr.write(
                f"[dolibarr-mcp] failed to load extension '{ep.name}': {exc}\n"
            )


_register_extensions()


def main() -> None:
    """Point d'entrée pour le serveur MCP Dolibarr."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
