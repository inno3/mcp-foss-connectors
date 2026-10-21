# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour Dolibarr ERP.

Expose les données de l'ERP Dolibarr (projets, factures, tiers, tickets,
comptes-rendus de réunion, banque et règlements) via le protocole MCP, pour
Claude Desktop et tout autre client MCP.

Configuration : DOLIBARR_URL (URL de base) + DOLIBARR_API_KEY (header DOLAPIKEY).
Optionnel : DOLIBARR_WEB_LOGIN / DOLIBARR_WEB_PASSWORD, requis uniquement par
les écritures que l'API REST n'expose pas : à ce jour le seul rapprochement
bancaire (cf. dolibarr_set_bank_statement).
Compatible Dolibarr 16+ (API REST).
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dolibarr-mcp")

__version__ = "1.8.8"

mcp = FastMCP("dolibarr", log_level="WARNING")
mcp._mcp_server.version = __version__

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

# Plafond de sûreté sur les notes d'une lecture de détail (get_proposal,
# get_invoice, get_supplier_invoice, get_thirdparty, get_project). En deçà, la
# note est rendue entière ; au-delà, la coupe est ANNONCÉE par
# `<champ>_truncated` et `<champ>_length`, jamais muette. Les `[:300]` et
# `[:500]` historiques coupaient sans le dire : constaté le 14/09/2026 sur la
# propale 172, dont la note publique revenait à 300 caractères exactement,
# avant comme après un update_proposal réussi, ce qui se lisait comme une
# perte en base.
DETAIL_NOTE_MAX_CHARS = 20_000

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


#: Ce que signifie chaque famille d'exception httpx quand elle arrive sans
#: message : c'est le cas courant (ReadError depuis une fin de flux, ReadTimeout
#: depuis un TimeoutError nu), et « Erreur :  » suivi de rien était tout ce que
#: l'appelant recevait.
_TRANSPORT_DEFAULT_DETAIL = {
    "ReadError": "connexion coupée par le serveur avant la réponse",
    "ReadTimeout": "délai de lecture dépassé",
    "WriteTimeout": "délai d'écriture dépassé",
    "ConnectTimeout": "délai de connexion dépassé",
    "ConnectError": "connexion impossible",
    "PoolTimeout": "délai d'attente d'une connexion dépassé",
    "RemoteProtocolError": "réponse HTTP mal formée ou interrompue",
}


class DolibarrTransportError(DolibarrAPIError):
    """Échec réseau définitif d'un appel, une fois les rejeux épuisés.

    Porte le type d'exception httpx d'origine, la route, le nombre de tentatives
    et si l'appel a été rejoué. Sans elle, un `httpx.ReadError` vide ou un
    `ReadTimeout` sans message se rendaient à l'appelant en « Erreur : » nu, et
    rien ne distinguait un compte rendu absent d'une connexion coupée : constaté
    le 08/09/2026 sur trois `dolibarr_get_meetingnote` rejoués avec succès à
    l'identique.
    """

    def __init__(self, kind: str, method: str, endpoint: str, attempts: int,
                 replayable: bool, detail: str = ""):
        self.kind = kind
        self.method = method
        self.endpoint = endpoint
        self.attempts = attempts
        self.replayable = replayable
        self.detail = detail or _TRANSPORT_DEFAULT_DETAIL.get(kind, "sans message")
        if replayable:
            tail = (f" après {attempts} tentatives : rejouer l'appel plus tard, et "
                    "vérifier la disponibilité de Dolibarr si l'erreur persiste.")
        else:
            tail = (" : écriture non rejouée automatiquement, Dolibarr a pu la traiter "
                    "avant de perdre la réponse ; relire l'objet avant de rejouer.")
        super().__init__(0, f"{kind} sur {method} {endpoint} ({self.detail}){tail}")


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
                if resp.status_code < 300:
                    return _parse_json_or_raise(resp, method, url)

                # 3xx : Restler rend un 304 « nothing done » quand une écriture
                # n'a rien changé. Le laisser passer pour un succès ferait
                # rapporter une imputation ou une mise à jour qui n'a rien écrit.
                if resp.status_code < 400:
                    resp.raise_for_status()

                # Erreurs client 4xx : lever immédiatement sans inspecter le corps
                if resp.status_code < 500:
                    resp.raise_for_status()

                # Erreurs serveur 5xx : bug connu de l'API REST Dolibarr : certains endpoints
                # (contacts, thirdparties) retournent HTTP 500 avec un corps JSON valide (entier
                # ou tableau) à la place d'un HTTP 200. On parse le corps avant de décider.
                # Une vraie erreur Dolibarr a toujours la forme {"error": {"code":…, "message":…}}.
                try:
                    body = resp.json()
                    if not (isinstance(body, dict) and "error" in body):
                        logger.warning(
                            "HTTP %d avec corps JSON valide (non-erreur) pour %s %s "
                            ": bug API Dolibarr, traité comme succès",
                            resp.status_code, method, url,
                        )
                        return body
                except Exception:
                    pass  # Corps non-JSON → on lève l'erreur HTTP normalement

                resp.raise_for_status()

        except httpx.TransportError as exc:
            # Deux règles pour toutes les erreurs réseau :
            # - la requête n'est jamais partie (ConnectError, ConnectTimeout) :
            #   rejouable même en écriture ;
            # - la requête est partie et la réponse s'est perdue (ReadTimeout,
            #   ReadError, RemoteProtocolError, PoolTimeout…) : rejouable en
            #   lecture seulement, Dolibarr a peut-être déjà créé l'objet et un
            #   rejeu ferait un doublon.
            # Avant, seuls ConnectError et Read/WriteTimeout étaient traités : un
            # ReadError (connexion coupée par le serveur, message vide) remontait
            # tel quel, et l'appelant lisait « Erreur : » sans rien derrière
            # (dolibarr_get_meetingnote, 08/09/2026, trois CR distincts, tous
            # rejoués avec succès à l'identique).
            never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            replayable = never_sent or idempotent
            last_exc = exc
            if replayable and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s in %.1fs (%s: %s)", method, url, delay,
                               type(exc).__name__, exc)
                await asyncio.sleep(delay)
            else:
                raise DolibarrTransportError(
                    type(exc).__name__, method.upper(), endpoint, attempt + 1,
                    replayable, str(exc).strip(),
                ) from exc
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
            # côté PHP-FPM / proxy. On retry comme pour un timeout : donc jamais
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
                    logger.info("404 'no results' pour %s : traité comme liste vide", endpoint)
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


def _ts_to_datetime(value: Any) -> str:
    """Convertit un timestamp Unix Dolibarr en 'YYYY-MM-DD HH:MM:SS' GMT.

    Les écritures de temps portent une heure de la journée
    (llx_element_time.element_datehour) que la date seule perdrait. L'API
    interprète et rend ces horodatages en GMT : on les formate donc en GMT,
    sans quoi un aller-retour lecture/écriture décalerait l'écriture.
    Retourne '' si la valeur n'est pas un timestamp.
    """
    if value in (None, ""):
        return ""
    from datetime import datetime, timezone
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


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
    visible : d'où cette conversion systématique.

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


def _id_int(value: Any) -> int | None:
    """Renvoie un identifiant Dolibarr en entier, ou None.

    Dolibarr rend la plupart des identifiants sous forme de chaîne ("221").
    Distinct de _seconds_int, qui porte la même mécanique mais une tout autre
    sémantique : mélanger les deux rend le code illisible à la relecture.
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
    """Formate une erreur HTTP en message lisible.

    Jamais un « Erreur : » nu : une exception sans message (httpx.ReadError,
    ReadTimeout, asyncio.TimeoutError) est rendue avec son type, et une erreur
    réseau dit sur quelle route, si l'appel a été rejoué et s'il peut l'être.
    """
    if isinstance(exc, DolibarrTransportError):
        return f"Erreur réseau {exc}"
    if isinstance(exc, DolibarrAPIError):
        return f"Erreur Dolibarr : {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "Erreur 401 : clef API Dolibarr invalide ou manquante."
        if status == 403:
            return "Erreur 403 : permissions insuffisantes pour cette ressource."
        if status == 404:
            # Dolibarr explique souvent PRÉCISÉMENT ce qu'il n'a pas trouvé
            # (« not found or not allowed for modulepart = supplier_invoice »).
            # L'écraser par un message générique coûte des heures de diagnostic.
            detail = _error_body(exc)
            return (
                f"Erreur 404 : {detail}" if detail
                else "Erreur 404 : ressource non trouvée dans Dolibarr."
            )
        if status == 500:
            return f"Erreur 500 : erreur interne Dolibarr. Détail : {exc.response.text[:500]}"
        return f"Erreur HTTP {status} : {exc.response.text[:500]}"
    if isinstance(exc, json.JSONDecodeError):
        # Filet de sécurité : ne devrait plus se produire grâce à _parse_json_or_raise
        return f"Erreur JSON Dolibarr : {exc}"
    if isinstance(exc, httpx.TransportError):
        kind = type(exc).__name__
        detail = str(exc).strip() or _TRANSPORT_DEFAULT_DETAIL.get(kind, "sans message")
        return f"Erreur réseau {kind} : {detail}. Rejouer l'appel."
    text = str(exc).strip()
    if not text:
        return f"Erreur {type(exc).__name__} : sans message."
    return f"Erreur : {text}"


def _dumps(data: Any) -> str:
    """Sérialise en JSON compact avec support UTF-8."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _clamp_limit(limit: int) -> int:
    """Borne la limite entre 1 et MAX_LIMIT."""
    return max(1, min(limit, MAX_LIMIT))


# Contrat commun des exports. Le plafond borne les appels HTTP, pas le rendu.
EXPORT_SCAN_PAGE = 100
EXPORT_SCAN_MAX_PAGES = 100


class ExportRows(list):
    """Lignes collectées et preuve de fin de balayage."""

    complete = True
    scan_note = ""


def _unique_rows(rows: list, identity: str = "") -> list:
    """Conserve la première occurrence de chaque identifiant connu."""
    seen = set()
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Export invalide : un objet attendu par ligne.")
        keys = (identity,) if identity else ("id", "rowid", "entry_id", "carnet_id", "card_id", "link_id")
        value = next((row[k] for k in keys if row.get(k) is not None), None)
        if value is not None:
            token = str(value)
            if token in seen:
                continue
            seen.add(token)
        result.append(row)
    return result


async def _scan_list(endpoint: str, params: dict | None = None, *, fetch=None) -> ExportRows:
    """Lit des pages stables jusqu'à la fin, sans certifier un parcours interrompu.

    Les routes sans pagination ne doivent pas passer par ce helper.
    Une API qui ignore page est détectée au premier lot sans nouvel ID.
    """
    fetch = fetch or _api_get_list
    base = dict(params or {})
    base.pop("page", None)
    base["limit"] = EXPORT_SCAN_PAGE
    field = base.get("sortfield", "t.rowid")
    if "rowid" not in field:
        field += "," + (field.split(".")[0] + "." if "." in field else "") + "rowid"
    base["sortfield"] = field
    result = ExportRows()
    fingerprints = set()
    for page in range(EXPORT_SCAN_MAX_PAGES):
        chunk = await fetch(endpoint, dict(base, page=page))
        if not isinstance(chunk, list):
            raise ValueError("Export invalide : la route ne renvoie pas une liste.")
        fingerprint = _dumps(chunk)
        if chunk and fingerprint in fingerprints:
            result.complete = False
            result.scan_note = "Page répétée par le serveur. Restreindre les filtres."
            return result
        fingerprints.add(fingerprint)
        merged = _unique_rows([*result, *chunk])
        if chunk and len(merged) == len(result):
            result.complete = False
            result.scan_note = "Aucun nouvel identifiant. Restreindre les filtres."
            return result
        result[:] = merged
        if len(chunk) < EXPORT_SCAN_PAGE:
            return result
    result.complete = False
    result.scan_note = "Plafond de balayage atteint. Restreindre les filtres."
    return result


def _cursor_offset(cursor: str, offset: int = 0) -> int:
    if cursor:
        if not cursor.isascii() or not cursor.isdecimal():
            raise ValueError("Curseur invalide : reprendre next_cursor avec les mêmes filtres.")
        return int(cursor)
    return max(0, offset)


def _export_payload(payload: dict, key: str, limit: int, cursor: str = "",
                    *, scan=None, offset: int = 0, identity: str = "", max_limit: int = MAX_LIMIT) -> dict:
    """Pagination du résultat filtré. total=null signifie total non certifié."""
    rows = _unique_rows(payload[key], identity)
    start = _cursor_offset(cursor, offset)
    size = max(1, min(limit, max_limit))
    shown = rows[start:start + size]
    complete = getattr(scan, "complete", True)
    more = start + len(shown) < len(rows)
    payload.update({
        key: shown, "total": len(rows) if complete else None,
        "total_exact": complete, "total_lower_bound": len(rows),
        "count": len(shown), "returned": len(shown),
        "truncated": more or not complete, "scan_truncated": not complete,
        "next_cursor": str(start + len(shown)) if more else None,
        "limit": size, "offset": start,
    })
    if not complete:
        payload["scan_note"] = getattr(scan, "scan_note", "Balayage incomplet.")
    return payload


def _first_present(row: dict, *keys: str):
    """La présence d'une clef prime sur la vérité de sa valeur, même null ou 0."""
    return next((row[key] for key in keys if key in row), None)


def _invoice_due_date(invoice: dict) -> str | None:
    """Même échéance en liste, en détail et en projection, y compris si elle est nulle."""
    value = _first_present(invoice, "date_echeance", "date_lim_reglement")
    return _ts_to_date(value) if value is not None else None


def _financial_fields(row: dict, *, proposal: bool = False) -> dict:
    """Projection financière et distinction entre un champ absent et null."""
    fields = {
        "total_ht": ("total_ht",), "total_ttc": ("total_ttc",),
        "status": ("fk_statut", "status", "statut"),
    }
    if proposal:
        fields["date_validity"] = ("fin_validite", "date_fin_validite", "date_validity")
    else:
        fields.update({
            "date_due": ("date_echeance", "date_lim_reglement"),
            "remaining_to_pay": ("remaintopay", "remaining_to_pay"),
            "source_invoice_id": ("fk_facture_source",),
            "type": ("type",),
        })
    result = {"unavailable_fields": []}
    for field, aliases in fields.items():
        value = _first_present(row, *aliases)
        if not any(alias in row for alias in aliases):
            result["unavailable_fields"].append(field)
        if value is not None:
            if field == "date_due":
                value = _invoice_due_date(row)
            elif field == "date_validity":
                value = _ts_to_date(value)
            elif field == "status":
                value = str(value)
            else:
                value = _num(value)
        result[field] = value
    status = result["status"]
    result["status_label"] = (_format_proposal_status(status) if proposal
                              else _format_invoice_status(status, row.get("paye")))
    return result


async def _with_invoice_payments(row: dict, supplier: bool, include_payments: bool) -> None:
    """Une lecture refusée ne signifie jamais absence de paiement."""
    row["payments"] = None
    row["payments_total"] = None
    if not include_payments:
        row["payments_error"] = "Lecture non demandée (include_payments=false)."
    elif not row.get("id"):
        row["payments_error"] = "Identifiant de facture absent."
    else:
        try:
            payments = await _invoice_payments(int(row["id"]), supplier)
            row["payments"] = payments
            if all(p.get("amount") is not None for p in payments):
                row["payments_total"] = round(sum(float(p["amount"]) for p in payments), 2)
            return
        except Exception as exc:
            row["payments_error"] = _format_error(exc)
    row.setdefault("unavailable_fields", []).append("payments")


PAYMENT_TERMS_NOTE = (
    "Une échéance à date de facture + 1 jour correspond notamment à la condition "
    "« À réception » (RECEP, nbjour = 1), parfois utilisée par défaut Dolibarr. "
    "Elle ne prouve pas une absence de condition. Vérifier la condition négociée "
    "avant une relance : si elle prévoit un délai plus long, "
    "days_late surévalue le retard de règlement."
)


def _due_date_is_default(date_invoice: str, date_due: str) -> bool | None:
    """Détecte l'intervalle +1 jour, notamment « À réception » (RECEP, nbjour = 1).

    Cette heuristique ne prouve ni l'absence de condition ni un retard réel.
    Une date absente ou illisible rend le résultat inconnu (None).
    """
    from datetime import date

    try:
        return (date.fromisoformat(date_due) - date.fromisoformat(date_invoice)).days == 1
    except (TypeError, ValueError):
        return None


def _payment_term_values(row: dict, dictionnaire: dict) -> dict:
    """Résout la condition portée par l'objet, sans hériter de celle d'un autre objet."""
    identifiant = _first_present(row, "cond_reglement_id", "cond_reglement", "fk_cond_reglement")
    code = _first_present(row, "cond_reglement_code") or None
    libelle = _first_present(row, "cond_reglement_label", "cond_reglement_doc") or None
    valeurs = {
        "cond_reglement_id": _id_int(identifiant) or None,
        "cond_reglement_code": code,
        "cond_reglement_label": libelle,
    }
    entree = dictionnaire["by_id"].get(str(valeurs["cond_reglement_id"]))
    if entree is None and code:
        entree = dictionnaire["by_code"].get(str(code).upper())
    if entree:
        for champ in ("id", "code", "label"):
            clef = "cond_reglement_" + champ
            if valeurs[clef] is None:
                valeurs[clef] = entree[champ] or None
    # L'absence de condition sur un objet lu est connue. Une condition partielle
    # reste en revanche incomplète si le dictionnaire ne permet pas de la résoudre.
    valeurs["unavailable_fields"] = (
        [clef for clef, valeur in valeurs.items() if valeur is None]
        if identifiant not in (None, "", 0, "0") or code or libelle else []
    )
    return valeurs


async def _creation_payment_terms(
    thirdparty_id: int | None, cond_reglement_id: int | None, proposal: dict | None = None,
    *, configuration: str | None = None,
) -> dict:
    """Choisit la condition à envoyer : paramètre validé, devis, puis tiers.

    À la création d'un tiers, configuration remplace l'héritage devis/tiers :
    code du dictionnaire ou identifiant positif, chaîne vide pour aucun défaut.

    Les lectures complémentaires restent facultatives ; leurs erreurs sont
    rendues sans empêcher la création. Le dictionnaire utilise le cache commun.
    """
    resultat = {
        "cond_reglement_id": None, "code": None, "label": None,
        "source": "aucune", "error": None,
    }
    if configuration == "" and cond_reglement_id is None:
        return resultat

    dictionnaire = await _payment_terms()
    resultat["error"] = dictionnaire["error"]
    if cond_reglement_id is not None:
        identifiant, source = cond_reglement_id, "parametre"
    elif configuration is not None:
        source = "configuration"
        try:
            identifiant = int(configuration)
        except ValueError:
            identifiant = dictionnaire["by_code"].get(configuration.upper(), {}).get("id")
        if not identifiant or identifiant <= 0:
            resultat["error"] = "; ".join(filter(None, (
                resultat["error"],
                f"DOLIBARR_THIRDPARTY_PAYMENT_TERM : impossible de résoudre {configuration!r} "
                "en identifiant entier strictement positif.",
            )))
            return resultat
    else:
        identifiant = _payment_term_values(proposal or {}, dictionnaire)["cond_reglement_id"]
        source = "devis"
        if not identifiant or identifiant <= 0:
            try:
                if not thirdparty_id:
                    raise ValueError("Identifiant de tiers absent ou illisible.")
                tiers = await api_get(f"thirdparties/{thirdparty_id}")
                if not isinstance(tiers, dict) or "error" in tiers:
                    raise ValueError("Réponse tiers illisible.")
                identifiant = _payment_term_values(tiers, dictionnaire)["cond_reglement_id"]
                source = "tiers"
            except Exception as exc:
                resultat["error"] = "; ".join(filter(None, (
                    resultat["error"], f"Condition du tiers : {_format_error(exc)}",
                )))
                return resultat

    if identifiant and identifiant > 0:
        entree = dictionnaire["by_id"].get(str(identifiant), {})
        resultat.update({
            "cond_reglement_id": identifiant, "code": entree.get("code"),
            "label": entree.get("label"), "source": source,
        })
    return resultat


async def _payment_terms_fields(row: dict, thirdparty_cache: dict, include: bool) -> dict:
    """Une lecture complémentaire refusée ne doit pas masquer la facture elle-même."""
    champs = ("cond_reglement_id", "cond_reglement_code", "cond_reglement_label")
    resultat = dict.fromkeys((*champs, *("thirdparty_" + clef for clef in champs),
                              "date_due_is_default"))
    resultat["unavailable_fields"] = []
    if not include:
        resultat["unavailable_fields"] = [c for c in resultat if c != "unavailable_fields"]
        resultat["payment_terms_error"] = "Lecture non demandée (include_payment_terms=false)."
        return resultat

    dictionnaire = await _payment_terms()
    resultat.update(_payment_term_values(row, dictionnaire))
    if dictionnaire["error"]:
        resultat["payment_terms_unavailable"] = dictionnaire["error"]
    resultat["date_due_is_default"] = _due_date_is_default(
        _ts_to_date(_first_present(row, "date", "datef")),
        _invoice_due_date(row),
    )
    if resultat["date_due_is_default"] is None:
        resultat["unavailable_fields"].append("date_due_is_default")
    elif resultat["date_due_is_default"]:
        resultat["payment_terms_note"] = PAYMENT_TERMS_NOTE

    identifiant_tiers = _id_int(_first_present(row, "socid", "thirdparty_id", "fk_soc"))
    if not identifiant_tiers:
        resultat["thirdparty_payment_terms_error"] = "Identifiant de tiers absent ou illisible."
    else:
        if identifiant_tiers not in thirdparty_cache:
            try:
                tiers = await api_get(f"thirdparties/{identifiant_tiers}")
                if not isinstance(tiers, dict) or "error" in tiers:
                    raise ValueError("Réponse tiers illisible.")
                thirdparty_cache[identifiant_tiers] = (
                    _payment_term_values(tiers, dictionnaire), None,
                )
            except Exception as exc:
                thirdparty_cache[identifiant_tiers] = (None, _format_error(exc))
        valeurs, erreur = thirdparty_cache[identifiant_tiers]
        if erreur:
            resultat["thirdparty_payment_terms_error"] = erreur
        else:
            resultat.update({"thirdparty_" + clef: valeurs[clef] for clef in champs})
            resultat["unavailable_fields"].extend(
                "thirdparty_" + clef for clef in valeurs["unavailable_fields"]
            )
    if "thirdparty_payment_terms_error" in resultat:
        resultat["unavailable_fields"].extend("thirdparty_" + clef for clef in champs)
    return resultat


async def _with_invoice_payment_terms(resultat: dict, source: dict, cache: dict,
                                      include: bool) -> None:
    """Préserve les indisponibilités financières lors de l'ajout des conditions."""
    champs = await _payment_terms_fields(source, cache, include)
    resultat.setdefault("unavailable_fields", []).extend(champs.pop("unavailable_fields"))
    resultat.update(champs)


async def _invoice_export(payload: dict, limit: int, cursor: str, scan,
                          supplier: bool, include_payments: bool,
                          include_payment_terms: bool) -> str:
    payload = _export_payload(payload, "invoices", limit, cursor, scan=scan)
    cache_tiers = {}
    for row in payload["invoices"]:
        # Garder la source jusqu'ici évite de perdre ses alias avant la pagination.
        source = row.pop("_payment_terms_source")
        await _with_invoice_payment_terms(row, source, cache_tiers, include_payment_terms)
        if row.get("payment_terms_unavailable"):
            payload["payment_terms_unavailable"] = row["payment_terms_unavailable"]
        await _with_invoice_payments(row, supplier, include_payments)
    payload["date_due_default_count"] = sum(
        row["date_due_is_default"] is True for row in payload["invoices"]
    )
    if payload["date_due_default_count"]:
        payload["payment_terms_note"] = PAYMENT_TERMS_NOTE
    if not supplier:
        credits = [r for r in payload["invoices"]
                   if str(r.get("type")) == "2" or (r.get("total_ttc") or 0) < 0]
        payload["credit_notes"] = credits
        payload["credit_notes_count"] = len(credits)
        payload["credit_notes_scope"] = "page rendue, avec les mêmes filtres que invoices"
    return _dumps(payload)


def _sf_escape(value: str) -> str:
    """Escape a string value for use in Dolibarr sqlfilters (escapes single quotes)."""
    return str(value).replace("'", "''")


def _sf_like(value: str) -> str:
    """Prépare une valeur pour un critère `like` de sqlfilters.

    Distinct de _sf_escape, et pas interchangeable avec lui : le callback
    Dolibarr n'accepte une valeur LIKE que sous la forme '…' SANS apostrophe
    interne (`preg_match("/^'([^']*)'$/")`). Doubler les apostrophes, comme le
    fait _sf_escape, ferait échouer ce motif et laisserait la valeur brute,
    quotes comprises, partir dans le SQL. On les retire donc, avec les
    parenthèses et l'antislash que la grammaire des critères interdit.
    """
    return "".join(c for c in str(value) if c not in "'()\\")


def _sf_and(*clauses: str) -> str:
    """Assemble des critères sqlfilters par un ET logique, en ignorant les vides."""
    return " and ".join(c for c in clauses if c)


def _sf_date_range(date_start: str, date_end: str, field: str) -> str:
    """Critères sqlfilters bornant `field` à une plage de dates (bornes incluses)."""
    return _sf_and(
        f"({field}:>=:'{_sf_escape(date_start)}')" if date_start else "",
        f"({field}:<=:'{_sf_escape(date_end)}')" if date_end else "",
    )


def _sf_ref_search(search: str, fields: "tuple[str, ...]") -> str:
    """Critère de recherche partielle sur une ou plusieurs colonnes de référence."""
    needle = _sf_like(search)
    if not needle:
        return ""
    parts = [f"({f}:like:'%{needle}%')" for f in fields]
    return parts[0] if len(parts) == 1 else "(" + " or ".join(parts) + ")"


def _sort_order(sort: str) -> str:
    """'asc' → 'ASC', tout le reste → 'DESC' (défaut : plus récent d'abord)."""
    return "ASC" if str(sort).lower().startswith("a") else "DESC"


def _valid_dates(**dates: str) -> str:
    """Valide des dates YYYY-MM-DD. Retourne '' si tout va bien, sinon le message."""
    for name, value in dates.items():
        if not value:
            continue
        try:
            _date_str_to_ts(value)
        except ValueError:
            return f"Format {name} invalide : '{value}'. Attendu : YYYY-MM-DD."
    return ""


async def _gather_limited(factories: list, concurrency: int = 8) -> list:
    """Exécute des coroutines en parallèle borné, en rendant les exceptions.

    Les enrichissements par ligne (liens bancaires, notamment) coûtent un appel
    chacun : les enchaîner rend l'outil inutilisable, les lâcher tous d'un coup
    sature l'instance. Les exceptions sont retournées plutôt que levées, pour
    qu'une ligne en échec n'emporte pas le reste du lot.
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _run(factory):
        async with sem:
            return await factory()

    return await asyncio.gather(*[_run(f) for f in factories], return_exceptions=True)


# ---------------------------------------------------------------------------
# Repli formulaire web
# ---------------------------------------------------------------------------
#
# Quelques écritures n'ont AUCUNE route REST dans Dolibarr 24 : le
# rapprochement bancaire en est une (cf. dolibarr_set_bank_statement). Le
# connecteur ne bascule sur le formulaire web que pour celles-là, et seulement
# si un compte web est explicitement configuré. Sans DOLIBARR_WEB_LOGIN /
# DOLIBARR_WEB_PASSWORD, les outils concernés refusent en le disant : jamais de
# mode dégradé silencieux. Une instance derrière un SSO qui n'expose pas le
# formulaire de login natif ne peut pas utiliser ce repli : l'échec est alors
# constaté à la relecture, pas supposé.

WEB_LOGIN = os.environ.get("DOLIBARR_WEB_LOGIN", "")
WEB_PASSWORD = os.environ.get("DOLIBARR_WEB_PASSWORD", "")

#: Marqueur du formulaire de login Dolibarr. Sa présence dans une réponse
#: signifie que la session n'est pas (ou plus) authentifiée.
_LOGIN_FORM_MARKER = 'name="actionlogin"'


def _extract_csrf_token(html: str) -> str:
    """Extrait le jeton anti-CSRF d'une page Dolibarr (<input name="token">).

    L'ordre des attributs n'est pas garanti d'une page à l'autre : on isole
    d'abord la balise portant name="token", puis sa valeur.
    """
    import re

    for tag in re.findall(r"<input\b[^>]*>", html or "", re.IGNORECASE):
        if re.search(r'name\s*=\s*"token"', tag, re.IGNORECASE):
            match = re.search(r'value\s*=\s*"([^"]*)"', tag, re.IGNORECASE)
            if match and match.group(1):
                return match.group(1)
    return ""


async def _web_login(client: "httpx.AsyncClient") -> None:
    """Authentifie une session sur l'interface web Dolibarr."""
    page = await client.get(f"{DOLIBARR_URL}/index.php")
    resp = await client.post(f"{DOLIBARR_URL}/index.php", data={
        "token": _extract_csrf_token(page.text),
        "actionlogin": "login",
        "loginfunction": "loginfunction",
        "username": WEB_LOGIN,
        "password": WEB_PASSWORD,
    })
    if _LOGIN_FORM_MARKER in (resp.text or ""):
        raise DolibarrAPIError(
            resp.status_code,
            "Connexion web Dolibarr refusée pour DOLIBARR_WEB_LOGIN : identifiants "
            "invalides, compte désactivé, ou instance derrière un SSO qui n'expose "
            "pas le formulaire de login natif.",
        )


async def _web_post(path: str, data: "list[tuple[str, str]]", query: str = "") -> str:
    """Poste un formulaire Dolibarr avec une session authentifiée et son jeton CSRF.

    Réservé aux écritures que l'API REST n'expose pas. Le jeton est relu sur la
    page cible juste avant l'envoi : Dolibarr le renouvelle à chaque affichage
    quand MAIN_SECURITY_CSRF_WITH_TOKEN est actif.
    """
    if not DOLIBARR_URL:
        raise DolibarrAPIError(0, "DOLIBARR_URL n'est pas configurée.")
    if not WEB_LOGIN or not WEB_PASSWORD:
        raise DolibarrAPIError(
            0,
            "DOLIBARR_WEB_LOGIN / DOLIBARR_WEB_PASSWORD ne sont pas configurés : "
            "le repli formulaire est désactivé.",
        )

    url = f"{DOLIBARR_URL}/{path.lstrip('/')}" + (f"?{query}" if query else "")
    async with httpx.AsyncClient(verify=True, timeout=30, follow_redirects=True) as client:
        await _web_login(client)

        page = await client.get(url)
        token = _extract_csrf_token(page.text)
        if not token:
            raise DolibarrAPIError(
                page.status_code,
                f"Jeton CSRF introuvable sur {path} (HTTP {page.status_code}) : page "
                "inaccessible pour ce compte, ou formulaire modifié par une mise à jour.",
            )

        logger.info("POST web %s (%d champs)", url, len(data) + 1)
        resp = await client.post(url, data=[("token", token), *data])
        resp.raise_for_status()
        return resp.text or ""


# ---------------------------------------------------------------------------
# Tools : Projets
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_projects(
    status: str='1',
    thirdparty_id: int=0,
    search: str='',
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les projets Dolibarr.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - status : 0=brouillon, 1=ouvert, 2=fermé (défaut : 1 = ouvert)
    - thirdparty_id : filtrer par tiers (0 = tous)
    - search : mot-clé dans le titre ou la référence
    - limit : nombre max de résultats (défaut 20, max 100)

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

        projects = await _scan_list("projects", params)

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

        return _dumps(_export_payload({"count": len(results), "projects": results}, "projects", limit, cursor, scan=projects))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_get_project(project_id: int = 0, ref: str = "") -> str:
    """Détail d'un projet par ID numérique ou par référence.

    Fournir soit project_id, soit ref. Retourne toutes les infos du projet
    incluant tâches et contacts associés.

    Description et note publique : `description` (texte brut) et `note_public`
    (HTML nettoyé) sont rendues entières jusqu'à DETAIL_NOTE_MAX_CHARS (20 000)
    caractères. Au-delà, le champ est coupé à ce plafond et la coupe est
    annoncée : `<champ>_truncated` passe à true et `<champ>_length` donne la
    longueur réelle. Ces champs sont toujours présents. Les deux étaient
    coupés à 500 caractères sans le dire.
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
        desc_text, desc_truncated, desc_length = _bounded_text(p.get("description"))
        note_text, note_truncated, note_length = _bounded_note(p.get("note_public"))
        result = {
            "id": p.get("id"),
            "ref": p.get("ref"),
            "title": p.get("title"),
            "status": p.get("fk_statut") or p.get("status"),
            "thirdparty": p.get("thirdparty_name") or p.get("socid"),
            "date_start": _ts_to_date(p.get("date_start")),
            "date_end": _ts_to_date(p.get("date_end")),
            "budget": p.get("budget_amount"),
            "description": desc_text,
            "description_length": desc_length,
            "description_truncated": desc_truncated,
            "note_public": note_text,
            "note_public_length": note_length,
            "note_public_truncated": note_truncated,
            "usage_bill_time": p.get("usage_bill_time"),
        }
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_list_tasks(
    project_id: int=0,
    assigned_to: int=0,
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les tâches d'un projet Dolibarr.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - project_id : ID du projet (obligatoire)
    - assigned_to : filtrer par utilisateur assigné (ID)
    - limit : nombre max de résultats

    Retourne : ref, label, progression, dates, charge prévue et temps passé.

    Durées : Dolibarr les renvoie en SECONDES ; chacune est exposée deux fois :
    - planned_hours / spent_hours   : heures, arrondies à 2 décimales
    - planned_seconds / spent_seconds : valeur brute Dolibarr, sans perte
    Une tâche portant 32,5 h ressort donc à spent_hours=32.5 et
    spent_seconds=117000. Ne jamais lire spent_seconds comme des heures.
    """
    try:
        if not project_id:
            return "Veuillez fournir un project_id."

        params: dict[str, Any] = {}
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

        return _dumps(_export_payload({"count": len(results), "tasks": results}, "tasks", limit, cursor))

    except Exception as exc:
        return _format_error(exc)

# Routes du temps passé, vérifiées contre le swagger servi par l'API elle-même
# (Dolibarr v23). Plusieurs routes se ressemblent, une seule accepte un POST :
# les autres rendent un 404 sec, indiscernable d'un problème de droits ou
# d'identifiant.
#   POST   tasks/{id}/addtimespent            → ajout (cassé en v23, cf. plus bas)
#   GET    tasks/{id}/timespent               → lecture seule, un POST ici rend 404
#   PUT    tasks/{id}/timespent/{entry_id}    → correction d'une écriture
#   DELETE tasks/{id}/timespent/{entry_id}    → suppression d'une écriture
#   .../projects/tasks/{id}/…                 → n'existe pas : la classe Tasks est
#                                               montée à la racine, jamais sous
#                                               /projects
TIMESPENT_ENDPOINT = "tasks/{task_id}/addtimespent"
TIMESPENT_LIST_ENDPOINT = "tasks/{task_id}/timespent"
TIMESPENT_ITEM_ENDPOINT = "tasks/{task_id}/timespent/{entry_id}"

# Création d'une tâche : POST tasks valide `ref`, `label` et `fk_project` dans
# _validate() AVANT d'exécuter quoi que ce soit d'autre, y compris son propre
# bloc d'auto-numérotation. Omettre `ref` rend donc « 400 ref field missing »
# alors même que l'API sait la générer : il faut la lui demander explicitement
# avec la valeur sentinelle ci-dessous. Ce trio est inchangé de la 16 à la 23.
# Piège jumeau : le champ attendu est `fk_project`, pas `fk_projet` (le nom de
# la colonne SQL) ; un `fk_projet` passe la validation nulle part et, s'il la
# passait, Task::create() ne le lirait pas.
TASK_REF_AUTO = "auto"

# Intervenants d'une tâche : llx_element_contact, élément « project_task ».
TASK_CONTACTS_ENDPOINT = "tasks/{task_id}/contacts"
TASK_CONTACT_ITEM_ENDPOINT = "tasks/{task_id}/contacts/{contact_id}/{role}"
CONTACT_TYPES_ENDPOINT = "setup/dictionary/contact_types"
TASK_CONTACT_ELEMENT = "project_task"

# Le code du rôle, jamais son identifiant : le rowid de llx_c_type_contact varie
# d'une instance à l'autre (constaté à 181 là où l'installation de référence
# amont pose une valeur bien plus basse). Dolibarr résout le code lui-même.
TASK_ROLE_CONTRIBUTOR = "TASKCONTRIBUTOR"  # libellé standard : « Intervenant »
TASK_ROLE_MANAGER = "TASKEXECUTIVE"        # libellé standard : « Responsable »

# Bug amont Dolibarr 23.0.x : POST tasks/{id}/addtimespent tue le process PHP
# avant même d'exécuter la méthode. Le bloc de doc de Tasks::addTimeSpent()
# déclare deux types union, « @param datetime|string $date » et
# « @param int|null $progress », que le CommentParser de Restler rend sous
# forme de tableau ; Validator.php ligne 427 s'en sert comme clé de tableau
# (isset($preFilters[$info->type])), ce qui lève un TypeError fatal sous PHP 8 :
#     Cannot access offset of type array in isset or empty
# La fatale se produit pendant la validation des paramètres, donc AVANT le
# fetch de la tâche : aucun contenu de requête ne permet de l'éviter, et le
# corps de la réponse est vide (HTTP 500, content-type text/html, 0 octet).
# Dolibarr 22 déclarait « @param datetime $date » sans union et sans $progress :
# la route y fonctionne. C'est donc une régression de la 23.
_UPSTREAM_BUG_ADDTIMESPENT = (
    "Signature du bug amont connu sur CERTAINS builds Dolibarr 23.0.x, pas un "
    "problème de configuration : POST tasks/{id}/addtimespent lève une fatale "
    "PHP (TypeError « Cannot access offset of type array in isset or empty », "
    "includes/restler/framework/Luracast/Restler/Data/Validator.php ligne 427) "
    "pendant la validation des paramètres, avant toute lecture de la tâche. Sur "
    "une instance atteinte, aucun contenu de requête ne contourne la fatale et "
    "l'imputation doit passer par l'interface web (projet/tasks/time.php). "
    "La production inno³, elle, écrit par cette route : elle tourne en "
    "Dolibarr 24.0.1 (relevé le 25/09/2026 sur la mire), où la régression de "
    "la 23 n'est plus là. Les "
    "routes de LECTURE et de CORRECTION du temps, elles, fonctionnent dans les "
    "deux cas : dolibarr_list_time_entries, dolibarr_update_time_entry, "
    "dolibarr_delete_time_entry."
)


def _is_empty_bodied_500(exc: httpx.HTTPStatusError) -> bool:
    """Signature d'une fatale PHP : HTTP 500 et pas un octet de corps.

    Une erreur applicative Dolibarr rend toujours un JSON
    {"error": {"code":…, "message":…}} ; un corps vide veut dire que le process
    est mort avant d'écrire quoi que ce soit (display_errors off).
    """
    return exc.response.status_code == 500 and not (exc.response.text or "").strip()


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


async def _user_exists(user_id: int) -> bool | None:
    """True/False selon que l'utilisateur Dolibarr existe, None si indéterminé.

    Un identifiant de contact (llx_socpeople) ou de salarié passé à la place
    d'un identifiant d'utilisateur (llx_user) ne remonte aucune erreur parlante
    côté imputation : on le vérifie explicitement.
    """
    try:
        return bool(await api_get(f"users/{user_id}"))
    except httpx.HTTPStatusError as exc:
        return False if exc.response.status_code == 404 else None
    except Exception:
        return None


def _format_task_contact(raw: dict) -> dict:
    """Normalise une ligne de tasks/{id}/contacts.

    `id` est un identifiant d'utilisateur quand source vaut « internal », et un
    identifiant de contact tiers quand elle vaut « external » : les deux ne se
    mélangent pas, d'où deux clés distinctes plutôt qu'une seule ambiguë.
    """
    source = raw.get("source")
    contact_id = _id_int(raw.get("id"))
    name = " ".join(
        p for p in (raw.get("firstname"), raw.get("lastname")) if p
    ).strip() or raw.get("nom") or ""
    return {
        "contact_id": contact_id,
        "user_id": contact_id if source == "internal" else None,
        "source": source,
        "name": name,
        "login": raw.get("login"),
        "email": raw.get("email"),
        "role_code": raw.get("code"),
        "role_label": raw.get("libelle"),
        "role_id": raw.get("fk_c_type_contact"),
        "link_id": raw.get("rowid"),
    }


async def _fetch_task_contacts(task_id: int, *, strict: bool = False) -> list[dict] | None:
    """Intervenants d'une tâche, ou None si la lecture échoue.

    Utilisé aussi bien par le tool de lecture que par le diagnostic d'échec :
    c'est ce qui permet de ne parler d'affectation que quand elle a vraiment
    été vérifiée.
    """
    try:
        raw = await api_get(TASK_CONTACTS_ENDPOINT.format(task_id=task_id))
    except Exception:
        if strict:
            raise
        return None
    if not isinstance(raw, list):
        return None
    return [_format_task_contact(c) for c in raw if isinstance(c, dict)]


async def _log_time_error(
    exc: Exception,
    task_id: int,
    endpoint: str,
    user_id: int = 0,
) -> str:
    """Explique l'échec d'une imputation de temps, cause par cause.

    Un « 404 : ressource non trouvée » nu est indébogable côté client, et un
    diagnostic inventé est pire encore : la version précédente affirmait
    « utilisateur non affecté » sur tout 5xx alors que l'API n'exige AUCUNE
    affectation pour imputer (Tasks::addTimeSpent ne contrôle que le droit
    projet->creer et l'accès au projet). On ne nomme donc que ce qui a été
    réellement vérifié, et le reste est rendu tel quel dans `checks`.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return _format_error(exc)

    status = exc.response.status_code
    body = _error_body(exc)
    payload: dict[str, Any] = {
        "success": False,
        "task_id": task_id,
        "endpoint": endpoint,
        "http_status": status,
        "dolibarr_error": body or None,
        "dolibarr_error_body_empty": not body,
    }
    checks: dict[str, Any] = {
        "tache_existe": None,
        "utilisateur_existe": None,
        "utilisateur_affecte": None,
    }
    payload["checks"] = checks

    # Fatale PHP : la cause est connue et n'a rien à voir avec l'appelant.
    if _is_empty_bodied_500(exc) and endpoint.endswith("/addtimespent"):
        payload["cause"] = "bug_dolibarr_addtimespent"
        payload["hint"] = _UPSTREAM_BUG_ADDTIMESPENT
        return _dumps(payload)

    if status == 304:
        payload["cause"] = "aucun_changement"
        payload["hint"] = (
            "Dolibarr a répondu « nothing done » : rien n'a été écrit. Vérifier "
            "que la durée est bien strictement positive."
        )
        return _dumps(payload)

    if status in (401, 403):
        payload["cause"] = "droits_insuffisants"
        payload["hint"] = (
            "Dolibarr refuse l'accès. L'imputation exige que l'utilisateur de "
            "la CLÉ API (distinct de l'utilisateur imputé) porte les droits "
            "projet->lire et projet->creer, et ait accès à ce projet. "
            "L'affectation de l'utilisateur imputé, elle, n'est pas contrôlée "
            "par cette route."
        )
        return _dumps(payload)

    checks["tache_existe"] = await _task_exists(task_id)
    if user_id:
        checks["utilisateur_existe"] = await _user_exists(user_id)
        contacts = await _fetch_task_contacts(task_id)
        if contacts is not None:
            checks["utilisateur_affecte"] = any(
                c["user_id"] == user_id for c in contacts
            )
            checks["roles_utilisateur"] = [
                c["role_code"] for c in contacts if c["user_id"] == user_id
            ]

    if checks["tache_existe"] is False:
        payload["cause"] = "tache_inexistante"
        payload["hint"] = (
            f"La tâche #{task_id} est introuvable. Lister les tâches du projet "
            "avec dolibarr_list_tasks pour récupérer un id valide."
        )
    elif checks["utilisateur_existe"] is False:
        payload["cause"] = "utilisateur_inconnu"
        payload["hint"] = (
            f"Aucun utilisateur Dolibarr #{user_id}. user_id attend un id de "
            "llx_user, pas un id de contact tiers ni de salarié."
        )
    elif status == 404:
        # La tâche répond : ce n'est donc pas elle qui manque, c'est la route.
        payload["cause"] = "route_ou_methode_invalide"
        payload["hint"] = (
            f"La tâche #{task_id} existe, mais {endpoint} rend un 404 : la route "
            "ou le verbe ne correspond pas à cette version de Dolibarr. "
            "L'ajout de temps est un POST sur tasks/{id}/addtimespent : "
            "tasks/{id}/timespent est en lecture seule."
        )
    elif status == 400:
        payload["cause"] = "parametres_refuses"
        payload["hint"] = (
            "Dolibarr a rejeté les paramètres de l'appel. Le message ci-dessus "
            "(dolibarr_error) nomme le champ en cause."
        )
    elif body:
        payload["cause"] = "erreur_interne_dolibarr"
        payload["hint"] = (
            "Dolibarr a renvoyé une erreur interne avec un message : c'est lui "
            "qui fait foi (dolibarr_error), pas une hypothèse du connecteur."
        )
    else:
        payload["cause"] = "erreur_interne_sans_corps"
        payload["hint"] = (
            f"HTTP {status} avec un corps vide : Dolibarr n'a rien dit. Le "
            "détail n'existe que côté serveur, dans le log PHP de l'instance "
            "(une fatale PHP ne laisse aucune trace dans le log applicatif "
            "Dolibarr). Aucune cause ne peut être affirmée d'ici."
        )
    return _dumps(payload)


@mcp.tool()
async def dolibarr_log_time(
    task_id: int | str = 0,
    duration: float = 0,
    date: str = "",
    note: str = "",
    user_id: int = 0,
    time: str = "",
    progress: int = -1,
) -> str:
    """Saisir du temps passé sur une tâche Dolibarr.

    La route POST tasks/{id}/addtimespent fonctionne sur la production inno³,
    qui tourne en Dolibarr 24.0.1 : 17 imputations écrites puis relues le
    18/09/2026, et une écriture de contrôle écrite, relue et supprimée le
    25/09/2026. Cette docstring la donnait pour morte sur toute Dolibarr 23 ;
    la fatale est bien réelle, mais c'est une régression de la 23.0.x, et la
    limite périmée a fait envisager un contournement navigateur pour rien.

    Limite qui subsiste, et que le connecteur ne peut pas lever : sur les
    builds atteints, l'appel meurt dans une fatale PHP du validateur de Restler
    avant même de lire la tâche, et rend un HTTP 500 au corps vide quel qu'en
    soit le contenu. Le connecteur reconnaît cette signature et la NOMME
    (`cause` = `bug_dolibarr_addtimespent`) au lieu d'inventer un diagnostic ;
    le détail est dans le champ `hint` de l'erreur retournée. Sur une instance
    atteinte, l'imputation passe par l'interface web (projet/tasks/time.php) ;
    la LECTURE et la CORRECTION du temps restent disponibles par l'API
    (dolibarr_list_time_entries, dolibarr_update_time_entry,
    dolibarr_delete_time_entry).

    Paramètres :
    - task_id : ID de la tâche (obligatoire)
    - duration : durée en heures (ex: 1.5 pour 1h30)
    - date : date au format YYYY-MM-DD (défaut: aujourd'hui)
    - note : commentaire sur le temps passé
    - user_id : ID utilisateur Dolibarr imputé (défaut: utilisateur de la clé API)
    - time : heure de la journée HH:MM ou HH:MM:SS (défaut: 09:00:00)
    - progress : avancement 0-100 à appliquer au passage ; -1 (défaut) laisse
      l'avancement inchangé, comme le fait l'API amont

    La durée est saisie en HEURES et convertie en secondes pour l'API, qui
    n'accepte que des secondes (1.5 → 5400).

    Dolibarr stocke une date ET une heure (llx_element_time.element_datehour) :
    le connecteur envoie donc toujours un horodatage complet, jamais une date
    seule. Cet horodatage est interprété en GMT par l'API.

    Retourne : confirmation avec le temps total passé sur la tâche, en heures
    (`total_spent_hours`) et en secondes brutes (`total_spent_seconds`).
    En cas d'échec, retourne la cause identifiée (`cause`), ce que le
    connecteur a réellement vérifié (`checks`) et le corps d'erreur Dolibarr
    quand il y en a un. Jamais un diagnostic supposé.
    """
    try:
        task_id = int(task_id) if task_id else 0
        if not task_id:
            return "Veuillez fournir un task_id."
        if duration <= 0:
            return "La durée doit être supérieure à 0."
        if progress != -1 and not 0 <= progress <= 100:
            return "progress doit valoir -1 (inchangé) ou un entier de 0 à 100."

        from datetime import datetime

        base = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
        hour, minute, second = 9, 0, 0
        if time:
            try:
                parts = [int(p) for p in time.strip().split(":")]
            except ValueError:
                return "time doit être au format HH:MM ou HH:MM:SS."
            if not 2 <= len(parts) <= 3:
                return "time doit être au format HH:MM ou HH:MM:SS."
            hour, minute = parts[0], parts[1]
            second = parts[2] if len(parts) == 3 else 0
        dt = base.replace(hour=hour, minute=minute, second=second, microsecond=0)

        duration_seconds = int(duration * 3600)

        # POST tasks/{id}/addtimespent, champs date/duration/user_id/note/
        # progress. La date passe par dol_stringtotime() → format
        # "YYYY-MM-DD HH:MI:SS" attendu, PAS un timestamp Unix.
        # progress est transmis systématiquement, comme le fait le formulaire
        # web : -1 est la valeur que l'API amont interprète comme « ne pas
        # toucher à l'avancement » (elle n'applique que 0 ≤ progress ≤ 100).
        # Les Dolibarr antérieurs à la 23 ignorent la clé, qu'ils ne déclarent
        # pas.
        data: dict[str, Any] = {
            "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": duration_seconds,
            "note": note,
            "progress": progress,
        }
        # user_id absent = utilisateur de la clé API (envoyer None ferait échouer
        # la validation Restler du paramètre entier).
        if user_id:
            data["user_id"] = user_id

        endpoint = TIMESPENT_ENDPOINT.format(task_id=task_id)
        try:
            await api_post(endpoint, data)
        except Exception as exc:
            return await _log_time_error(exc, task_id, endpoint, user_id)

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
            "datehour": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "note": note,
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools : intervenants d'une tâche
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_task_contacts(
    task_id: int,
    role: str='',
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les intervenants affectés à une tâche Dolibarr.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - task_id : ID de la tâche (obligatoire)
    - role : filtrer sur un code de rôle (ex: TASKCONTRIBUTOR), vide = tous

    Retourne, pour chaque intervenant : `user_id` (renseigné pour les
    intervenants internes uniquement), `contact_id` brut, `source`
    (internal = utilisateur Dolibarr, external = contact d'un tiers), nom,
    login, email, et le rôle sous ses deux formes : `role_code`
    (TASKCONTRIBUTOR / TASKEXECUTIVE, stable d'une instance à l'autre) et
    `role_label` (« Intervenant » / « Responsable », traduit et
    personnalisable). `role_id` est le rowid local du type de contact : il
    change d'une instance à l'autre, ne jamais s'en servir comme référence.

    Note : l'API d'imputation n'exige AUCUNE affectation pour enregistrer du
    temps ; l'affectation ne conditionne que la saisie par l'interface web.
    """
    try:
        if not task_id:
            return "Le task_id est obligatoire."

        endpoint = TASK_CONTACTS_ENDPOINT.format(task_id=task_id)
        params = {"type": role.strip()} if role.strip() else None
        raw = await api_get(endpoint, params)
        contacts = [_format_task_contact(c) for c in raw if isinstance(c, dict)]

        return _dumps(_export_payload({
            "task_id": task_id,
            "count": len(contacts),
            "contacts": contacts,
        }, "contacts", limit, cursor))

    except Exception as exc:
        return _format_error(exc)

async def _task_role_codes() -> list[dict] | None:
    """Codes de rôle valides pour une tâche sur CETTE instance, ou None.

    Lus dans le dictionnaire llx_c_type_contact plutôt que codés en dur : les
    libellés dépendent de la langue et les rowid de l'instance.
    """
    try:
        raw = await api_get(
            CONTACT_TYPES_ENDPOINT,
            {"type": TASK_CONTACT_ELEMENT, "limit": MAX_LIMIT},
        )
    except Exception:
        return None
    if not isinstance(raw, list):
        return None
    return [t for t in raw if isinstance(t, dict) and t.get("type") == TASK_CONTACT_ELEMENT]


@mcp.tool()
async def dolibarr_assign_task_user(
    task_id: int,
    user_id: int,
    role: str = TASK_ROLE_CONTRIBUTOR,
) -> str:
    """Affecte un utilisateur Dolibarr comme intervenant d'une tâche.

    Confirmation utilisateur requise avant execution.

    Paramètres :
    - task_id : ID de la tâche (obligatoire)
    - user_id : ID de l'utilisateur Dolibarr, llx_user (obligatoire)
    - role : code du rôle, TASKCONTRIBUTOR (« Intervenant », défaut) ou
      TASKEXECUTIVE (« Responsable »)

    Le rôle est toujours désigné par son CODE, jamais par un identifiant
    numérique : le rowid du type de contact varie d'une instance à l'autre.
    Le code est validé contre le dictionnaire de l'instance avant l'appel,
    parce qu'un code inconnu fait répondre Dolibarr par un 500 au message vide.

    Retourne la liste des intervenants APRÈS l'opération, pas un simple booléen.
    """
    try:
        if not task_id:
            return "Le task_id est obligatoire."
        if not user_id:
            return "Le user_id est obligatoire (identifiant llx_user)."

        code = (role or TASK_ROLE_CONTRIBUTOR).strip().upper()
        known = await _task_role_codes()
        if known is not None:
            valid = sorted({
                t["code"] for t in known
                if t.get("source") == "internal" and t.get("code")
            })
            if valid and code not in valid:
                return _dumps({
                    "success": False,
                    "cause": "role_inconnu",
                    "role": code,
                    "roles_valides": valid,
                    "hint": (
                        "Rôle inconnu sur cette instance. Utiliser un des codes "
                        "ci-dessus (lus dans le dictionnaire des types de "
                        "contact), jamais un identifiant numérique."
                    ),
                })

        before = await _fetch_task_contacts(task_id) or []
        if any(c["user_id"] == user_id and c["role_code"] == code for c in before):
            return _dumps({
                "success": True,
                "already_assigned": True,
                "task_id": task_id,
                "user_id": user_id,
                "role": code,
                "message": f"L'utilisateur #{user_id} est déjà {code} sur la tâche.",
                "contacts": before,
            })

        endpoint = TASK_CONTACTS_ENDPOINT.format(task_id=task_id)
        await api_post(endpoint, {
            "fk_socpeople": user_id,
            "type_contact": code,
            "source": "internal",
        })

        contacts = await _fetch_task_contacts(task_id)
        return _dumps({
            "success": True,
            "already_assigned": False,
            "task_id": task_id,
            "user_id": user_id,
            "role": code,
            "count": len(contacts) if contacts is not None else None,
            "contacts": contacts,
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_unassign_task_user(
    task_id: int,
    user_id: int,
    role: str = "",
) -> str:
    """Retire un utilisateur des intervenants d'une tâche Dolibarr.

    Confirmation utilisateur requise avant execution.
    IRRÉVERSIBLE : le lien est supprimé, pas désactivé. Le temps déjà imputé
    par cet utilisateur n'est pas touché, mais il ne pourra plus saisir de
    temps sur la tâche depuis l'interface web.

    Paramètres :
    - task_id : ID de la tâche (obligatoire)
    - user_id : ID de l'utilisateur Dolibarr (obligatoire)
    - role : code du rôle à retirer ; vide = le rôle unique de cet utilisateur
      sur la tâche, refusé s'il en porte plusieurs

    Retourne la liste des intervenants APRÈS l'opération.
    """
    try:
        if not task_id:
            return "Le task_id est obligatoire."
        if not user_id:
            return "Le user_id est obligatoire (identifiant llx_user)."

        contacts = await _fetch_task_contacts(task_id, strict=True)
        if contacts is None:
            return _dumps({
                "success": False,
                "cause": "affectations_illisibles",
                "task_id": task_id,
                "hint": (
                    "Impossible de lire les intervenants de la tâche : on ne "
                    "supprime pas à l'aveugle. Vérifier task_id et les droits."
                ),
            })

        held = [c["role_code"] for c in contacts if c["user_id"] == user_id]
        code = (role or "").strip().upper()
        if not code:
            if len(held) != 1:
                return _dumps({
                    "success": False,
                    "cause": "role_ambigu" if held else "utilisateur_non_affecte",
                    "task_id": task_id,
                    "user_id": user_id,
                    "roles_actuels": held,
                    "hint": (
                        "Préciser le rôle à retirer."
                        if held
                        else f"L'utilisateur #{user_id} n'est pas affecté à cette tâche."
                    ),
                })
            code = held[0]
        elif code not in held:
            return _dumps({
                "success": False,
                "cause": "utilisateur_non_affecte",
                "task_id": task_id,
                "user_id": user_id,
                "role": code,
                "roles_actuels": held,
                "hint": f"L'utilisateur #{user_id} ne porte pas le rôle {code}.",
            })

        await api_delete(TASK_CONTACT_ITEM_ENDPOINT.format(
            task_id=task_id, contact_id=user_id, role=code,
        ))

        after = await _fetch_task_contacts(task_id)
        return _dumps({
            "success": True,
            "task_id": task_id,
            "user_id": user_id,
            "role": code,
            "count": len(after) if after is not None else None,
            "contacts": after,
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools : écritures de temps (lecture et correction)
# ---------------------------------------------------------------------------


def _gmt_day(value: Any) -> str:
    """Jour GMT (YYYY-MM-DD) d'un horodatage Dolibarr, ou '' s'il manque.

    Une écriture de temps porte une date et une heure lues en GMT, comme
    `datehour`. La date doit être le jour de CE même instant : la lire dans le
    fuseau local du poste qui fait tourner le connecteur la décalait d'un jour
    dès 22:00 GMT vue de Paris (25/09/2026, écriture 3936), et d'un jour en
    arrière à l'ouest de l'UTC. Une chaîne déjà au format date est rendue
    telle quelle.
    """
    return _ts_to_datetime(value)[:10] or _ts_to_date(value)


def _format_time_entry(raw: dict) -> dict:
    """Normalise une ligne de tasks/{id}/timespent.

    Dolibarr préfixe tout de `timespent_line_` et rend la durée en secondes,
    sous forme de chaîne : on expose les deux unités, comme partout ailleurs.
    """
    seconds = raw.get("timespent_line_duration")
    return {
        "entry_id": _id_int(raw.get("timespent_line_id")),
        "task_id": _id_int(raw.get("fk_task")),
        "task_ref": raw.get("task_ref"),
        "task_label": raw.get("task_label"),
        "project_id": _id_int(raw.get("fk_project")),
        "project_ref": raw.get("project_ref"),
        # Même instant, même fuseau que datehour : sinon le filtre par date,
        # qui porte sur ce champ, manque les écritures du soir.
        "date": (_gmt_day(raw.get("timespent_line_datehour"))
                 or _gmt_day(raw.get("timespent_line_date"))),
        "datehour": _ts_to_datetime(raw.get("timespent_line_datehour")),
        # Timestamp brut conservé : c'est lui qu'on renvoie tel quel lors d'une
        # correction, pour qu'une simple retouche de note ne décale pas l'heure.
        "datehour_ts": _seconds_int(raw.get("timespent_line_datehour")),
        "hours": _seconds_to_hours(seconds),
        "seconds": _seconds_int(seconds),
        "user_id": _id_int(raw.get("timespent_line_fk_user")),
        "product_id": _id_int(raw.get("timespent_line_fk_product")),
        "note": raw.get("timespent_line_note"),
    }


async def _fetch_time_entries(task_id: int) -> list[dict]:
    """Écritures de temps d'une tâche, normalisées. Liste vide si aucune."""
    raw = await api_get(TIMESPENT_LIST_ENDPOINT.format(task_id=task_id))
    if not isinstance(raw, list):
        raise ValueError("Reponse de temps illisible : liste attendue.")
    return [_format_time_entry(e) for e in raw if isinstance(e, dict)]


@mcp.tool()
async def dolibarr_list_time_entries(
    task_id: int = 0,
    project_id: int = 0,
    user_id: int = 0,
    date_start: str = "",
    date_end: str = "",
    limit: int = DEFAULT_LIMIT,
    cursor: str = "",
    task_limit: int = 20,
    time_budget: float = 20.0,
) -> str:
    """Liste les temps avec pagination par tache et budget global explicite.

    task_id ou project_id obligatoire. Filtres user_id et dates YYYY-MM-DD
    incluses. limit borne les ecritures rendues (100 maximum), task_limit les
    taches lues (20 par defaut, 100 maximum). time_budget inclut la decouverte
    des taches, les requetes et leurs reprises : 20 s par defaut, 25 s maximum.

    Reprendre next_cursor avec les memes filtres. Ordre par ID de tache puis
    ID d'ecriture. Une tache terminee n'est pas relue ; la tache partiellement
    rendue est relue a la page suivante. Le curseur suppose des temps stables
    pendant le parcours. Les anciens curseurs numeriques ne sont plus valides.

    truncated annonce toute suite, y compris apres erreur ou expiration.
    tasks_scanned compte les taches lues dans CET appel, reason explique l'arret.
    total est inconnu tant que le projet entier n'a pas ete lu dans cet appel.
    Les agregats portent sur les taches lues, y compris la partie non rendue
    d'une tache tronquee : ne pas additionner les totaux de pages qui relisent
    la meme tache. aggregates_complete dit si ces totaux couvrent le perimetre.
    Pour un export, additionner les ecritures rendues, dedupliquees par entry_id.
    total_exact suit aggregates_complete ; total_lower_bound compte les lignes
    des tâches lues dans cet appel. scan_truncated signale ces agrégats partiels,
    même en dernière page. offset compte les écritures rendues avant cette page.

    Chaque ligne expose entry_id, task_id, date, hours, seconds, user_id et note.
    Aucune ecriture distante. Une erreur conserve les resultats deja collectes.

    Dates et heures sont en GMT, comme l'API les rend et comme
    dolibarr_log_time les ecrit : `date` est le jour GMT de `datehour`, et
    date_start / date_end filtrent sur ce jour-la. Une ecriture faite le 25 a
    23:59 GMT est donc du 25, meme si l'interface Dolibarr, qui affiche
    l'heure de Paris, la montre au 26 a 01:59. Jusqu'a la 1.8.8, `date` etait
    lu dans le fuseau du poste du connecteur : il pouvait differer d'un jour
    de `datehour`, et le filtre manquait l'ecriture.
    """
    import base64
    import math

    try:
        if not task_id and not project_id:
            return "Fournir un task_id ou un project_id."
        invalid = _valid_dates(date_start=date_start, date_end=date_end)
        if invalid:
            return invalid
        if not math.isfinite(time_budget) or time_budget <= 0:
            raise ValueError("time_budget doit etre un nombre positif fini.")
        budget = min(float(time_budget), 25.0)
        deadline = time.monotonic() + budget
        capped = _clamp_limit(limit)
        task_limit = max(1, min(int(task_limit), 100))
        scope = [task_id, project_id, user_id, date_start, date_end]
        current, offset, emitted = 0, 0, 0
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError()
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
                if decoded["v"] != 2 or decoded["scope"] != scope:
                    raise ValueError()
                current, offset = decoded["task"], decoded["offset"]
                emitted = decoded["emitted"]
                if any(type(v) is not int or v < 0 for v in (current, offset, emitted)):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise ValueError("Curseur invalide : reprendre next_cursor avec les memes filtres.") from None

        def next_token(tid: int, position: int) -> str:
            return base64.urlsafe_b64encode(json.dumps({
                "v": 2, "scope": scope, "task": tid, "offset": position,
                "emitted": emitted + len(entries),
            }, separators=(",", ":")).encode()).decode()

        async def read(awaitable):
            return await asyncio.wait_for(awaitable, max(0, deadline - time.monotonic()))

        entries, scanned_entries = [], []
        scanned = 0
        task_ids = []
        reason, error, next_cursor = None, None, None
        try:
            if task_id:
                task_ids = [int(task_id)]
            else:
                tasks = await read(api_get(f"projects/{project_id}/tasks", {}))
                if not isinstance(tasks, list):
                    raise ValueError("Liste des taches illisible.")
                task_ids = sorted({int(t["id"]) for t in tasks if isinstance(t, dict) and t.get("id")})
        except TimeoutError:
            reason = "time_budget"
            next_cursor = next_token(current, offset)
        except Exception as exc:
            reason, error = "task_list_error", _format_error(exc)
            next_cursor = next_token(current, offset)

        remaining_tasks = [tid for tid in task_ids if tid >= current]
        if offset and current not in task_ids and reason is None:
            raise ValueError("Curseur invalide : la tache partiellement lue a disparu.")
        for index, tid in enumerate(remaining_tasks) if reason is None else []:
            position = offset if tid == current else 0
            if scanned >= task_limit or time.monotonic() >= deadline:
                reason = "task_limit" if scanned >= task_limit else "time_budget"
                next_cursor = next_token(tid, position)
                break
            try:
                task_entries = await read(_fetch_time_entries(tid))
            except TimeoutError:
                reason, next_cursor = "time_budget", next_token(tid, position)
                break
            except Exception as exc:
                reason, error, next_cursor = "task_error", _format_error(exc), next_token(tid, position)
                break
            scanned += 1
            task_entries = [e for e in task_entries
                            if (not user_id or e["user_id"] == user_id)
                            and (not date_start or (e["date"] and e["date"] >= date_start))
                            and (not date_end or (e["date"] and e["date"] <= date_end))]
            task_entries = sorted(_unique_rows(task_entries, identity="entry_id"), key=lambda e: e["entry_id"] or 0)
            scanned_entries.extend(task_entries)
            shown = task_entries[position:position + capped - len(entries)]
            entries.extend(shown)
            if position + len(shown) < len(task_entries):
                reason, next_cursor = "limit", next_token(tid, position + len(shown))
                break
            if len(entries) >= capped and index + 1 < len(remaining_tasks):
                reason, next_cursor = "limit", next_token(remaining_tasks[index + 1], 0)
                break

        aggregates_complete = bool(task_ids) and scanned == len(task_ids) and current in (0, task_ids[0])
        if not task_ids and reason is None:
            aggregates_complete = True
        by_user, by_task = {}, {}
        for entry in scanned_entries:
            seconds = entry["seconds"] or 0
            uid = str(entry["user_id"])
            key = entry["task_ref"] or str(entry["task_id"])
            by_user[uid] = by_user.get(uid, 0) + seconds
            by_task[key] = by_task.get(key, 0) + seconds
        total_seconds = sum(e["seconds"] or 0 for e in scanned_entries)
        result = {
            "task_id": task_id or None, "project_id": project_id or None,
            "tasks_scanned": scanned, "tasks_total": None if not task_ids and reason is not None else len(task_ids),
            "count": len(entries), "returned": len(entries), "matched": len(scanned_entries),
            "total": len(scanned_entries) if aggregates_complete else None,
            "total_exact": aggregates_complete, "total_lower_bound": len(scanned_entries),
            "scan_truncated": not aggregates_complete, "limit": capped, "offset": emitted,
            "truncated": next_cursor is not None, "next_cursor": next_cursor,
            "reason": reason or "complete", "time_budget": budget,
            "elapsed_seconds": round(time.monotonic() - (deadline - budget), 3),
            "aggregates_scope": "scanned_tasks", "aggregates_complete": aggregates_complete,
            "total_hours": _seconds_to_hours(total_seconds), "total_seconds": total_seconds,
            "by_user": {k: {"hours": _seconds_to_hours(v), "seconds": v} for k, v in by_user.items()},
            "by_task": {k: {"hours": _seconds_to_hours(v), "seconds": v} for k, v in by_task.items()},
            "entries": entries,
        }
        if error:
            result["scan_error"] = error
        if not aggregates_complete:
            result["scan_note"] = (
                "Agrégats limités aux tâches lues dans cet appel. "
                "Reconstituer le total depuis les écritures exportées, sans sommer les agrégats de pages."
            )
        return _dumps(result)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_time_entry(
    task_id: int,
    entry_id: int,
    duration: float = -1,
    date: str = "",
    time: str = "",
    note: str = "",
    user_id: int = 0,
) -> str:
    """Corrige une écriture de temps existante sur une tâche Dolibarr.

    Confirmation utilisateur requise avant execution.

    Paramètres :
    - task_id : ID de la tâche portant l'écriture (obligatoire)
    - entry_id : ID de l'écriture, `entry_id` rendu par
      dolibarr_list_time_entries (obligatoire)
    - duration : nouvelle durée en HEURES ; -1 (défaut) = inchangée
    - date : nouvelle date YYYY-MM-DD ; vide = inchangée
    - time : nouvelle heure HH:MM ou HH:MM:SS ; vide = inchangée
    - note : nouveau commentaire ; vide = inchangé
    - user_id : nouvel utilisateur imputé ; 0 = inchangé

    L'API amont ne fait PAS de mise à jour partielle : elle réécrit la ligne
    avec ce qu'on lui passe, et ses valeurs par défaut sont destructrices :
    un `note` omis vide le commentaire, et un `user_id` omis réaffecte
    l'écriture à l'utilisateur 0. Le connecteur relit donc l'écriture et
    renvoie tous les champs, en ne remplaçant que ceux demandés.

    Retourne l'écriture avant/après, durées en heures et en secondes.
    """
    try:
        if not task_id:
            return "Le task_id est obligatoire."
        if not entry_id:
            return "L'entry_id est obligatoire."
        if duration != -1 and duration <= 0:
            return "La durée doit être supérieure à 0 (ou -1 pour la laisser inchangée)."

        entries = await _fetch_time_entries(task_id)
        current = next((e for e in entries if e["entry_id"] == int(entry_id)), None)
        if current is None:
            return _dumps({
                "success": False,
                "cause": "ecriture_introuvable",
                "task_id": task_id,
                "entry_id": entry_id,
                "hint": (
                    f"Aucune écriture #{entry_id} sur la tâche #{task_id}. "
                    "Lister les écritures avec dolibarr_list_time_entries."
                ),
            })

        from datetime import datetime, timezone

        # L'horodatage stocké est relu en GMT puis renvoyé en GMT : sans cela,
        # une simple correction de note décalerait la date de l'écriture.
        stored = current.get("datehour_ts")
        base = (
            datetime.fromtimestamp(stored, timezone.utc)
            if stored
            else datetime.now(timezone.utc)
        )
        if date:
            d = datetime.strptime(date, "%Y-%m-%d")
            base = base.replace(year=d.year, month=d.month, day=d.day)
        if time:
            try:
                parts = [int(p) for p in time.strip().split(":")]
            except ValueError:
                return "time doit être au format HH:MM ou HH:MM:SS."
            if not 2 <= len(parts) <= 3:
                return "time doit être au format HH:MM ou HH:MM:SS."
            base = base.replace(
                hour=parts[0], minute=parts[1],
                second=parts[2] if len(parts) == 3 else 0, microsecond=0,
            )

        seconds = int(duration * 3600) if duration != -1 else current["seconds"]
        payload: dict[str, Any] = {
            "date": base.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": seconds,
            "user_id": user_id or current["user_id"],
            "note": note if note else (current["note"] or ""),
        }
        if current["product_id"]:
            payload["product_id"] = current["product_id"]

        endpoint = TIMESPENT_ITEM_ENDPOINT.format(task_id=task_id, entry_id=entry_id)
        try:
            await api_put(endpoint, payload)
        except Exception as exc:
            return await _log_time_error(exc, task_id, endpoint, payload["user_id"])

        after = await _fetch_time_entries(task_id)
        updated = next((e for e in after if e["entry_id"] == int(entry_id)), None)

        return _dumps({
            "success": True,
            "task_id": task_id,
            "entry_id": int(entry_id),
            "before": current,
            "after": updated,
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_time_entry(task_id: int, entry_id: int) -> str:
    """Supprime une écriture de temps sur une tâche Dolibarr.

    Confirmation utilisateur requise avant execution.
    IRRÉVERSIBLE : la ligne de llx_element_time est détruite, sans corbeille
    ni historique. Le temps total de la tâche est recalculé en conséquence.

    Paramètres :
    - task_id : ID de la tâche portant l'écriture (obligatoire)
    - entry_id : ID de l'écriture (obligatoire)

    L'écriture est relue AVANT la suppression, pour que la réponse dise ce qui
    a disparu (durée, date, utilisateur, note), et non un simple booléen.
    Exige le droit projet->supprimer sur la clé API (l'ajout et la correction
    n'exigent que projet->creer).
    """
    try:
        if not task_id:
            return "Le task_id est obligatoire."
        if not entry_id:
            return "L'entry_id est obligatoire."

        entries = await _fetch_time_entries(task_id)
        deleted = next((e for e in entries if e["entry_id"] == int(entry_id)), None)
        if deleted is None:
            return _dumps({
                "success": False,
                "cause": "ecriture_introuvable",
                "task_id": task_id,
                "entry_id": entry_id,
                "hint": (
                    f"Aucune écriture #{entry_id} sur la tâche #{task_id} : rien "
                    "n'a été supprimé. Lister les écritures avec "
                    "dolibarr_list_time_entries."
                ),
            })

        endpoint = TIMESPENT_ITEM_ENDPOINT.format(task_id=task_id, entry_id=entry_id)
        try:
            await api_delete(endpoint)
        except Exception as exc:
            return await _log_time_error(exc, task_id, endpoint, deleted["user_id"] or 0)

        task = await api_get(f"tasks/{task_id}")
        return _dumps({
            "success": True,
            "task_id": task_id,
            "entry_id": int(entry_id),
            "deleted": deleted,
            "total_spent_hours": _seconds_to_hours(task.get("duration_effective")),
            "total_spent_seconds": _seconds_int(task.get("duration_effective")),
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
    ref: str = TASK_REF_AUTO,
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
    - ref          : référence de la tâche ; "auto" (défaut) la fait générer
      par le numéroteur de l'instance (PROJECT_TASK_ADDON), ce qui donne une
      valeur du type TK2608-0258

    Retourne l'ID et la référence de la tâche créée.
    """
    try:
        if not project_id:
            return "Le project_id est obligatoire."
        if not label:
            return "Le label est obligatoire."

        from datetime import datetime

        # Trois champs sont obligatoires côté API et validés AVANT tout le
        # reste : ref, label, fk_project (cf. TASK_REF_AUTO). En omettre un
        # rend un 400 sec, et « fk_projet », le nom de la COLONNE SQL, n'est
        # pas le nom du champ attendu par l'API.
        data: dict[str, Any] = {
            "ref": ref.strip() or TASK_REF_AUTO,
            "fk_project": project_id,
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

        # L'API ne rend que l'id : relire la tâche pour rapporter la référence
        # réellement attribuée par le numéroteur, qu'on ne connaît pas d'avance.
        created: dict[str, Any] = {}
        try:
            created = await api_get(f"tasks/{result}") or {}
        except Exception:
            pass

        return _dumps({
            "success": True,
            "task_id": result,
            "ref": created.get("ref"),
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
# Tools : Facturation
# ---------------------------------------------------------------------------


#: Correspondance statut demandé → llx_facture.fk_statut. 'all' (ou toute autre
#: valeur) ne filtre pas.
INVOICE_STATUS_FILTER = {"draft": "0", "validated": "1", "paid": "2", "unpaid": "1"}


@mcp.tool()
async def dolibarr_list_invoices(
    status: str='unpaid',
    thirdparty_id: int=0,
    min_amount: float=0,
    limit: int=DEFAULT_LIMIT,
    date_start: str='',
    date_end: str='',
    sort: str='desc',
    search_ref: str='',
    cursor: str='',
    include_payments: bool=True,
    include_payment_terms: bool=True,
) -> str:
    """Liste les factures clients.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    include_payments : vrai par défaut, lit les paiements des factures rendues.
    Une lecture refusée est signalée, jamais remplacée par une liste vide.

    include_payment_terms : vrai par défaut, rend cond_reglement_id, _code et
    _label de la facture et thirdparty_cond_reglement_id, _code et _label du tiers.
    Lit le dictionnaire en cache et une fois chaque tiers de la page rendue.
    À false, aucun appel complémentaire et champs null avec la raison.
    Une lecture impossible est signalée dans unavailable_fields avec son erreur.
    Hors de ces erreurs, null sur les deux conditions signifie qu'aucune condition
    de règlement n'est paramétrée dans Dolibarr.

    date_due à date de facture + 1 jour correspond notamment à la condition
    « À réception » (RECEP, nbjour = 1), parfois appliquée par défaut Dolibarr.
    date_due_is_default signale cet intervalle (null si inconnu), sans prouver
    l'absence de condition. Vérifier la condition négociée avant une relance :
    days_late peut surévaluer le retard si le délai réel est plus long.

    Paramètres :
    - status : 'unpaid' (impayées), 'paid' (payées), 'draft' (brouillon), 'all'
    - thirdparty_id : filtrer par tiers
    - min_amount : montant TTC minimum. Laissé à 0, il ne filtre RIEN : surtout
      pas les avoirs, dont le total est négatif par construction.
    - limit : nombre max de résultats
    - date_start / date_end : bornes sur la DATE DE FACTURE (YYYY-MM-DD, incluses)
    - sort : 'desc' (défaut, plus récentes d'abord) ou 'asc'
    - search_ref : fragment de référence, recherche partielle (ex. '2608-0939'
      ou 'FA2608'). Sert à retrouver une facture depuis le libellé d'un virement
      lu sur un relevé bancaire.

    Le tri porte sur la date de facture, celle-là même que bornent date_start et
    date_end. Jusqu'à la v1.4 le tri était croissant et sans filtre de période :
    `status='all'` avec limit=40 rendait les factures de 2021 et jamais les
    récentes. Le retard de paiement reste exposé par `days_late`, qui permet de
    reclasser côté appelant sans revenir à ce tri.

    Les AVOIRS (références AV…) figurent dans la liste avec un montant négatif :
    les omettre ferait surestimer le chiffre d'affaires de qui additionne cette
    liste. `credit_notes_count` les dénombre dans la réponse.

    Retourne : ref, tiers, montant TTC, date échéance, statut, jours de retard.
    """
    try:
        invalid = _valid_dates(date_start=date_start, date_end=date_end)
        if invalid:
            return invalid

        clamp = _clamp_limit(limit)
        sqlfilters = _sf_and(
            f"(t.fk_statut:=:{INVOICE_STATUS_FILTER[status]})" if status in INVOICE_STATUS_FILTER else "",
            _sf_date_range(date_start, date_end, "t.datef"),
            _sf_ref_search(search_ref, ("t.ref",)),
        )

        params: dict[str, Any] = {
            "sortfield": "t.datef",
            "sortorder": _sort_order(sort),
            # min_amount est filtré côté client : élargir la limite serveur, sinon
            # elle tronque avant le filtrage et des factures manquent.
            "limit": CLIENT_FILTER_SCAN_LIMIT if min_amount else clamp,
        }
        if sqlfilters:
            params["sqlfilters"] = sqlfilters
        if thirdparty_id:
            params["thirdparty_ids"] = str(thirdparty_id)

        invoices = await _scan_list("invoices", params)

        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        for inv in invoices:
            total_ttc = float(inv.get("total_ttc") or 0)
            # `min_amount` ne filtre QUE s'il est fourni. Comparer à 0 par défaut
            # écartait silencieusement tout total négatif, c'est-à-dire chaque
            # AVOIR : 13 des 100 dernières factures de l'instance inno³
            # disparaissaient de la liste sans le moindre signal, et le CA
            # reconstitué depuis cette liste s'en trouvait faussé à la hausse.
            if min_amount and total_ttc < min_amount:
                continue

            date_lim = _invoice_due_date(inv)
            date_inv = _ts_to_date(_first_present(inv, "date", "datef"))
            inv_status = str(inv.get("fk_statut") or inv.get("status") or inv.get("statut") or "")
            days_late = 0
            if date_lim and date_lim < today and inv_status == "1":
                try:
                    d_lim = datetime.strptime(date_lim[:10], "%Y-%m-%d")
                    days_late = (datetime.now() - d_lim).days
                except ValueError:
                    pass

            results.append({
                "_payment_terms_source": inv,
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
                **_financial_fields(inv, proposal=False),
            })

        return await _invoice_export({
            "count": len(results),
            "sort": _sort_order(sort).lower(),
            "date_start": date_start or None,
            "date_end": date_end or None,
            "credit_notes_count": sum(1 for r in results if (r["total_ttc"] or 0) < 0),
            "invoices": results,
        }, limit, cursor, invoices, False, include_payments, include_payment_terms)

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_list_supplier_invoices(
    status: str='unpaid',
    thirdparty_id: int=0,
    limit: int=DEFAULT_LIMIT,
    date_start: str='',
    date_end: str='',
    sort: str='desc',
    search_ref: str='',
    cursor: str='',
    include_payments: bool=True,
    include_payment_terms: bool=True,
) -> str:
    """Liste les factures fournisseur.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    include_payments : vrai par défaut, lit les paiements des factures rendues.
    Une lecture refusée est signalée, jamais remplacée par une liste vide.

    include_payment_terms : vrai par défaut, rend cond_reglement_id, _code et
    _label de la facture et thirdparty_cond_reglement_id, _code et _label du tiers.
    Lit le dictionnaire en cache et une fois chaque tiers de la page rendue.
    À false, aucun appel complémentaire et champs null avec la raison.
    Une lecture impossible est signalée dans unavailable_fields avec son erreur.
    Hors de ces erreurs, null sur les deux conditions signifie qu'aucune condition
    de règlement n'est paramétrée dans Dolibarr.

    date_due à date de facture + 1 jour correspond notamment à la condition
    « À réception » (RECEP, nbjour = 1), parfois appliquée par défaut Dolibarr.
    date_due_is_default signale cet intervalle (null si inconnu), sans prouver
    l'absence de condition. Vérifier la condition négociée avant une relance :
    days_late peut surévaluer le retard si le délai réel est plus long.

    Paramètres :
    - status : 'unpaid', 'paid', 'draft', 'all'
    - thirdparty_id : filtrer par fournisseur
    - limit : nombre max de résultats
    - date_start / date_end : bornes sur la DATE DE FACTURE (YYYY-MM-DD, incluses)
    - sort : 'desc' (défaut, plus récentes d'abord) ou 'asc'
    - search_ref : fragment de référence, cherché À LA FOIS dans la référence
      interne Dolibarr (SI2602-0084) et dans la référence du fournisseur :
      c'est cette dernière qu'on lit sur un relevé ou une relance.

    Mêmes corrections que pour les factures clients : tri décroissant par défaut
    et filtre de période, faute de quoi une limite basse ne rend que les plus
    anciennes factures.

    Retourne : ref, ref fournisseur, tiers, montant TTC, échéance, statut.
    """
    try:
        invalid = _valid_dates(date_start=date_start, date_end=date_end)
        if invalid:
            return invalid

        sqlfilters = _sf_and(
            f"(t.fk_statut:=:{INVOICE_STATUS_FILTER[status]})" if status in INVOICE_STATUS_FILTER else "",
            _sf_date_range(date_start, date_end, "t.datef"),
            _sf_ref_search(search_ref, ("t.ref", "t.ref_supplier")),
        )

        params: dict[str, Any] = {
            "sortfield": "t.datef",
            "sortorder": _sort_order(sort),
            "limit": _clamp_limit(limit),
        }
        if sqlfilters:
            params["sqlfilters"] = sqlfilters
        if thirdparty_id:
            params["thirdparty_ids"] = str(thirdparty_id)

        invoices = await _scan_list("supplierinvoices", params)

        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        for inv in invoices:
            date_lim = _invoice_due_date(inv)
            date_inv = _ts_to_date(_first_present(inv, "date", "datef"))
            inv_status = str(inv.get("fk_statut") or inv.get("status") or inv.get("statut") or "")
            days_late = 0
            if date_lim and date_lim < today and inv_status == "1":
                try:
                    d_lim = datetime.strptime(date_lim[:10], "%Y-%m-%d")
                    days_late = (datetime.now() - d_lim).days
                except ValueError:
                    pass

            results.append({
                "_payment_terms_source": inv,
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
                **_financial_fields(inv, proposal=False),
            })

        return await _invoice_export({
            "count": len(results),
            "sort": _sort_order(sort).lower(),
            "date_start": date_start or None,
            "date_end": date_end or None,
            "invoices": results,
        }, limit, cursor, invoices, True, include_payments, include_payment_terms)

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_list_proposals(
    status: str='',
    thirdparty_id: int=0,
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les propositions commerciales (devis).

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

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

        proposals = await _scan_list("proposals", params)

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
                **_financial_fields(p, proposal=True),
            })

        return _dumps(_export_payload({"count": len(results), "proposals": results}, "proposals", limit, cursor, scan=proposals))

    except Exception as exc:
        return _format_error(exc)

# ---------------------------------------------------------------------------
# Tools : Tiers
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_thirdparties(
    type_filter: str='',
    search: str='',
    search_email: str='',
    search_code: str='',
    category: int=0,
    limit: int=DEFAULT_LIMIT,
    offset: int=0,
    cursor: str='',
) -> str:
    """Liste les tiers (clients, fournisseurs, prospects).

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

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
            "page": 0,
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

        thirdparties = await _scan_list("thirdparties", params)

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

        return _dumps(_export_payload({"count": len(results), "thirdparties": results}, "thirdparties", limit, cursor, scan=thirdparties, offset=offset))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_get_thirdparty(thirdparty_id: int) -> str:
    """Détail complet d'un tiers par son ID.

    Retourne toutes les informations : coordonnées, catégories, contacts,
    conditions de paiement, etc.

    cond_reglement_id, cond_reglement_code et cond_reglement_label décrivent
    la condition du tiers : null sur les trois signifie qu'aucune condition
    n'est paramétrée sur le tiers, ce qui produit des échéances par défaut sur
    ses factures. Une résolution impossible est signalée dans unavailable_fields
    et un dictionnaire illisible dans payment_terms_unavailable.

    Note publique : `note_public` est rendue entière, HTML nettoyé, jusqu'à
    DETAIL_NOTE_MAX_CHARS (20 000) caractères. Au-delà, elle est coupée à ce
    plafond et la coupe est annoncée : `note_public_truncated` passe à true et
    `note_public_length` donne la longueur réelle du texte nettoyé. Ces deux
    champs sont toujours présents. Elle était coupée à 300 caractères sans le
    dire.
    """
    try:
        tp = await api_get(f"thirdparties/{thirdparty_id}")
        # Filtrer les champs utiles pour économiser les tokens
        note_text, note_truncated, note_length = _bounded_note(tp.get("note_public"))
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
            "note_public": note_text,
            "note_public_length": note_length,
            "note_public_truncated": note_truncated,
        }
        dictionnaire = await _payment_terms()
        result.update(_payment_term_values(tp, dictionnaire))
        if dictionnaire["error"]:
            result["payment_terms_unavailable"] = dictionnaire["error"]
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

    Anti-doublon : vérifie l'email (1) sur le tiers cible : doublon strict : et
    (2) sur tous les autres tiers : doublon potentiel (même personne rattachée
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
    cond_reglement_id: Annotated[int, Field(strict=True, gt=0)] | None = None,
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
    - cond_reglement_id : entier strictement positif, prioritaire sur le réglage
      DOLIBARR_THIRDPARTY_PAYMENT_TERM. Omis ou None : utiliser ce réglage.

    DOLIBARR_THIRDPARTY_PAYMENT_TERM est lu à chaque appel : code du dictionnaire
    insensible à la casse (ex. 30D) ou identifiant entier positif. Vide ou absent :
    aucun défaut. Un code inconnu ou un dictionnaire illisible laisse créer sans
    condition, avec l'erreur dans la réponse. Un identifiant peut être envoyé
    sans dictionnaire. La condition ne s'applique qu'aux clients et prospects
    (client 1, 2 ou 3), jamais à un tiers uniquement fournisseur.

    La condition est envoyée dans le POST, puis vérifiée par relecture du tiers.
    Sans condition envoyée, pas de relecture du tiers. payment_terms_applied
    rend cond_reglement_id, code, label, source (parametre, configuration, aucune
    ou non_applicable), verified et error. verified compare la valeur brute relue
    à celle envoyée ; None si rien n'est envoyé ou si la relecture échoue.
    Une relecture impossible ou différente est signalée sans annuler le succès
    de création ni rejouer le POST. L'anti-doublon précède le dictionnaire.

    Retourne l'ID du tiers créé, ou un signalement de doublon.
    """
    try:
        if not name:
            return "Le nom (name) est obligatoire."
        if cond_reglement_id is not None and (
            type(cond_reglement_id) is not int or cond_reglement_id <= 0
        ):
            return _dumps({
                "success": False,
                "error": "cond_reglement_id doit être un entier strictement positif.",
            })

        # Anti-doublon : nom identique (insensible à la casse) ou email identique.
        # En cas d'erreur de recherche (ex. 404 "no third parties found"), on
        # n'empêche pas la création : le garde-fou est best-effort.
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

        if client_val in (1, 2, 3):
            payment_terms = await _creation_payment_terms(
                None, cond_reglement_id,
                configuration=os.environ.get("DOLIBARR_THIRDPARTY_PAYMENT_TERM", "").strip(),
            )
        else:
            payment_terms = {
                "cond_reglement_id": None, "code": None, "label": None,
                "source": "non_applicable", "error": None,
            }
        payment_terms["verified"] = None
        if payment_terms["cond_reglement_id"] is not None:
            data["cond_reglement_id"] = payment_terms["cond_reglement_id"]

        result = await api_post("thirdparties", data)

        if payment_terms["cond_reglement_id"] is not None:
            try:
                tiers = await api_get(f"thirdparties/{result}")
                if (not isinstance(tiers, dict) or "error" in tiers
                        or str(tiers.get("id")) != str(result)):
                    raise ValueError("Le tiers relu est absent ou son identifiant ne correspond pas.")
                raw_id = _first_present(tiers, "cond_reglement_id", "cond_reglement", "fk_cond_reglement")
                payment_terms["verified"] = str(raw_id) == str(payment_terms["cond_reglement_id"])
                if not payment_terms["verified"]:
                    payment_terms["error"] = "; ".join(filter(None, (
                        payment_terms["error"],
                        "La condition relue ne correspond pas à la condition demandée.",
                    )))
            except Exception as exc:
                payment_terms["error"] = "; ".join(filter(None, (
                    payment_terms["error"], f"Relecture du tiers : {_format_error(exc)}",
                )))

        return _dumps({
            "success": True,
            "thirdparty_id": result,
            "name": name,
            "type": type_tiers,
            "payment_terms_applied": payment_terms,
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
    cond_reglement_id: Annotated[int, Field(strict=True, gt=0)] | None = None,
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
    - cond_reglement_id : entier strictement positif du dictionnaire des conditions
      de règlement ; None conserve la condition existante.

    Avec cond_reglement_id, le tiers est relu après toute tentative de PUT,
    même en cas d'erreur. write_state distingue accepted, rejected et unknown ;
    write_may_have_succeeded indique si l'écriture a pu passer. readback contient
    les champs relus, verified compare la condition demandée à celle relue.
    success exige un PUT accepté et cette comparaison réussie. Une erreur de
    relecture ne signifie jamais que l'écriture a échoué. Aucun rejeu automatique.
    """
    try:
        if not thirdparty_id:
            return "Veuillez fournir un thirdparty_id."
        if cond_reglement_id is not None and (
            type(cond_reglement_id) is not int or cond_reglement_id <= 0
        ):
            return _dumps({
                "success": False, "write_state": "not_attempted",
                "write_may_have_succeeded": False, "verified": False, "readback": None,
                "error": "cond_reglement_id doit être un entier strictement positif.",
            })

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
        if cond_reglement_id is not None:
            data["cond_reglement_id"] = cond_reglement_id

        if not data:
            return "Aucun champ à mettre à jour."

        if cond_reglement_id is not None:
            response: dict[str, Any] = {
                "success": False, "thirdparty_id": thirdparty_id,
                "updated_fields": list(data.keys()), "write_state": "unknown",
                "write_may_have_succeeded": True, "verified": False, "readback": None,
            }
            try:
                await api_put(f"thirdparties/{thirdparty_id}", data)
                response["write_state"] = "accepted"
            except Exception as write_exc:
                response["error"] = _format_error(write_exc)
                status_code = (
                    write_exc.response.status_code if isinstance(write_exc, httpx.HTTPStatusError)
                    else getattr(write_exc, "status", 0)
                )
                if 300 <= status_code < 500:
                    response["write_state"] = "rejected"
                    response["write_may_have_succeeded"] = False
            try:
                tp = await api_get(f"thirdparties/{thirdparty_id}")
                if not isinstance(tp, dict) or str(tp.get("id")) != str(thirdparty_id):
                    raise ValueError("Le tiers relu est absent ou son identifiant ne correspond pas.")
                readback = {key: tp.get(key) for key in data}
                response["readback"] = readback
                dictionnaire = await _payment_terms()
                readback.update(_payment_term_values(tp, dictionnaire))
                if dictionnaire["error"]:
                    readback["payment_terms_unavailable"] = dictionnaire["error"]
                raw_id = _first_present(tp, "cond_reglement_id", "cond_reglement", "fk_cond_reglement")
                readback["cond_reglement_id_raw"] = raw_id
                response["verified"] = str(raw_id) == str(cond_reglement_id)
                if not response["verified"]:
                    response["payment_terms_warning"] = (
                        "La condition relue ne correspond pas à la condition demandée."
                    )
            except Exception as read_exc:
                response["refetch_warning"] = _format_error(read_exc)
            response["success"] = response["write_state"] == "accepted" and response["verified"]
            response["message"] = (
                f"Condition de règlement du tiers {thirdparty_id} écrite et relue."
                if response["success"] else
                "Mise à jour non confirmée : consulter write_state, readback et les avertissements avant tout nouvel essai."
            )
            return _dumps(response)

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
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les contacts rattachés à un tiers.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - thirdparty_id : ID du tiers (obligatoire)
    - limit : nombre max de résultats

    Retourne : id, prénom, nom, email, téléphone, fonction, statut.
    """
    try:
        if not thirdparty_id:
            return "Veuillez fournir un thirdparty_id."

        contacts = await _scan_list("contacts", {
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

        return _dumps(_export_payload({"thirdparty_id": thirdparty_id, "count": len(results), "contacts": results}, "contacts", limit, cursor, scan=contacts))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_search_contacts(
    search: str = "",
    email: str = "",
    limit: int = DEFAULT_LIMIT,
    cursor: str = "",
) -> str:
    """Cherche un contact par email ou nom à travers tous les tiers.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

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

        contacts = await _scan_list("contacts", {
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

        return _dumps(_export_payload({"contacts": results}, "contacts", limit, cursor, scan=contacts))

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
# Tools : Tickets
# ---------------------------------------------------------------------------

#: Libellés FR des statuts de ticket (colonne llx_ticket.fk_statut), d'après les
#: constantes Ticket::STATUS_* de Dolibarr : les codes 4 et 6 n'existent pas.
#: L'API REST renvoie status_label=null (libellé calculé par l'UI, jamais
#: sérialisé) : on le reconstitue côté connecteur.
TICKET_STATUS_LABELS = {
    "0": "Non lu",
    "1": "Lu",
    "2": "Assigné",
    "3": "En cours",
    "5": "En attente (retour demandeur)",
    "7": "Suspendu",
    "8": "Fermé",
    "9": "Annulé",
}


def _ticket_status_label(ticket: dict) -> str:
    """Libellé de statut d'un ticket : status_label si l'API le fournit un jour,
    sinon reconstitué depuis fk_statut (ou status) via TICKET_STATUS_LABELS."""
    label = ticket.get("status_label")
    if label:
        return str(label)
    code = ticket.get("fk_statut")
    if code in (None, ""):
        code = ticket.get("status")
    return TICKET_STATUS_LABELS.get(str(code), "")


async def _fetch_ticket(ticket_id: int, ref: str) -> "dict[str, Any] | str":
    """Résout un ticket par ID ou par référence.

    Retourne le dict du ticket, ou un message d'erreur (str) à renvoyer tel
    quel à l'appelant. La résolution par ref passe par la liste filtrée :
    l'objet rendu est allégé (pas d'historique de messages) mais porte bien
    id, ref, track_id et fk_statut.
    """
    if ticket_id:
        return await api_get(f"tickets/{ticket_id}")
    if ref:
        tickets = await _api_get_list(
            "tickets",
            {"sqlfilters": f"(t.ref:=:'{_sf_escape(ref)}')", "limit": 1},
        )
        if not tickets:
            return f"Ticket '{ref}' introuvable."
        return tickets[0]
    return "Veuillez fournir ticket_id ou ref."


@mcp.tool()
async def dolibarr_list_tickets(
    status: str='',
    thirdparty_id: int=0,
    severity: str='',
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les tickets de support.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - status : 'not_read', 'read', 'assigned', 'in_progress', 'waiting' (=en
      attente d'infos du demandeur), 'suspended', 'closed', 'canceled',
      vide=ouverts (tout sauf fermés/annulés)
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
                "waiting": "5",      # STATUS_NEED_MORE_INFO (alias historique)
                "need_info": "5",
                "suspended": "7",    # STATUS_WAITING
                "closed": "8",
                "canceled": "9",
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

        tickets = await _scan_list("tickets", params)

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
                "status_label": _ticket_status_label(t),
                "severity": t.get("severity_code"),
                "category": t.get("category_code") or t.get("type_code"),
                "date_creation": _ts_to_date(t.get("datec")),
                "date_last_msg": t.get("date_last_msg_sent"),
                "assigned_to": t.get("fk_user_assign"),
            })

        return _dumps(_export_payload({"count": len(results), "tickets": results}, "tickets", limit, cursor, scan=tickets))

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
        ticket = await _fetch_ticket(ticket_id, ref)
        if isinstance(ticket, str):
            return ticket
        if not ticket_id:
            # La résolution par ref rend un objet de liste sans l'historique :
            # relire par ID pour obtenir les messages du fil.
            ticket = await api_get(f"tickets/{int(ticket.get('id'))}")

        # GET /tickets/{id} sérialise le fil sous `messages` (l'API le remplit
        # depuis cache_msgs_ticket) ; absent sur les vieux Dolibarr → [].
        messages = []
        for msg in ticket.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            messages.append({
                "id": msg.get("id"),
                "author_user_id": msg.get("fk_user_author") or msg.get("fk_user_action"),
                "date": _ts_to_datetime(msg.get("datec")),
                "private": msg.get("private"),
                "message": _strip_html(msg.get("message"))[:500],
            })

        return _dumps({
            "id": ticket.get("id"),
            "ref": ticket.get("ref"),
            "track_id": ticket.get("track_id"),
            "subject": ticket.get("subject"),
            "message": ticket.get("message"),
            "status": ticket.get("fk_statut"),
            "status_label": _ticket_status_label(ticket),
            "severity": ticket.get("severity_code"),
            "type": ticket.get("type_code"),
            "category": ticket.get("category_code"),
            "thirdparty_id": ticket.get("fk_soc"),
            "thirdparty": ticket.get("thirdparty_name"),
            "project_id": ticket.get("fk_project"),
            "assigned_to": ticket.get("fk_user_assign"),
            "date_creation": _ts_to_date(ticket.get("datec")),
            "date_close": _ts_to_date(ticket.get("date_close")),
            "date_last_msg": ticket.get("date_last_msg_sent"),
            "nb_messages": ticket.get("nb_public_messages"),
            "messages": messages,
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Effets de bord d'une création de ticket
# ---------------------------------------------------------------------------


#: Observateurs appelés après une création de ticket réussie.
#:
#: Créer un ticket n'est pas une écriture isolée : un module branché sur le
#: trigger Dolibarr TICKET_CREATE peut écrire ailleurs dans la foulée, et aucun
#: connecteur générique ne peut deviner lesquels ni où. Une extension qui, elle,
#: connaît le module s'inscrit ici pour aller relire ce qui a été écrit et le
#: faire dire à l'outil. Constaté le 18/09/2026 sur la production inno³ : onze
#: tickets créés par dolibarr_create_ticket ont débité onze unités sur les
#: carnets de support du client (module maison `supportcredits`) sans que le
#: retour de l'outil en souffle mot ; l'écriture ne s'est vue qu'au refus de
#: l'imputation suivante, « statut=2, restant=0 ».
#:
#: Signature attendue :
#:     async def observer(ticket_id: int, thirdparty_id: int) -> dict | None
#: Le dict rendu est ajouté à `side_effects` ; sa clé `message`, si elle existe,
#: est concaténée au message lisible du retour. None veut dire « rien à
#: signaler » et n'ajoute aucune ligne.
TICKET_CREATE_OBSERVERS: list = []


def register_ticket_create_observer(observer) -> None:
    """Inscrit un observateur d'effets de bord de création de ticket.

    Idempotent : une extension dont le `register()` serait appelé deux fois ne
    doit pas faire compter le débit deux fois.
    """
    if observer not in TICKET_CREATE_OBSERVERS:
        TICKET_CREATE_OBSERVERS.append(observer)


async def _ticket_create_side_effects(ticket_id: int, thirdparty_id: int) -> list:
    """Collecte ce que les observateurs ont trouvé, sans jamais faire échouer.

    Le ticket, lui, est créé : une panne d'observateur ne doit pas se lire comme
    un échec de création. Elle est rendue telle quelle dans la liste, parce
    qu'un observateur qui n'a rien vu et un observateur cassé ne disent pas la
    même chose.
    """
    found: list = []
    for observer in TICKET_CREATE_OBSERVERS:
        try:
            result = await observer(ticket_id, thirdparty_id)
        except Exception as exc:
            found.append({
                "observer": getattr(observer, "__name__", repr(observer)),
                "error": f"{type(exc).__name__}: {exc}",
                "message": (
                    "Un observateur d'effets de bord a échoué : ce que ce module "
                    "a pu écrire au passage n'a PAS été vérifié."
                ),
            })
            continue
        if result:
            found.append(result)
    return found


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

    ATTENTION : la création d'un ticket n'est pas forcément une écriture
    isolée. Un module branché sur le trigger Dolibarr TICKET_CREATE peut
    écrire ailleurs dans la foulée, et le connecteur ne peut pas l'en
    empêcher : c'est du code serveur, déclenché par l'écriture elle-même.
    Cas connu sur les instances inno³ (module maison `supportcredits`) :
    créer un ticket rattaché à un tiers DÉBITE un carnet de support de ce
    client : le plus ancien carnet actif, non périmé, ayant assez de solde.
    Le comportement dépend de deux réglages du module, côté serveur :
    SUPPORTCREDITS_CONSUME_MODE (`auto` débite, `suggest` et `manual` non) et
    SUPPORTCREDITS_CONSUME_TIMING (`create` débite ici, `close` à la clôture).
    Aucun paramètre de cet outil ne les surcharge. Constaté le 18/09/2026 :
    onze tickets, onze unités débitées sur deux carnets, sans un mot dans le
    retour.

    Paramètres :
    - subject       : sujet du ticket (obligatoire)
    - message       : message / description du problème (obligatoire)
    - thirdparty_id : ID du tiers concerné (optionnel)
    - project_id    : ID du projet associé (optionnel)
    - severity      : 'low', 'normal', 'high', 'critical' (défaut: 'normal').
      Sur une instance `supportcredits` en débit automatique, ce paramètre
      décide AUSSI du nombre d'unités prélevées au client, sans que rien ne le
      dise dans l'interface : 'low' → 1 unité, 'high' et 'critical' → 5
      unités, 'normal' → 1 unité par défaut de correspondance (le module ne
      connaît pas le code NORMAL et retombe sur « simple », en journalisant un
      avertissement). Un ticket créé en 'critical' coûte donc cinq fois un
      ticket créé en 'low'.
    - type_code     : 'QUESTION', 'INCIDENT', 'REQUEST', 'SUPPORT' (défaut: 'QUESTION')

    Retourne l'ID et la référence du ticket créé, plus :
    - `side_effects_checked` : le connecteur a-t-il su REGARDER les effets de
      bord. Faux quand aucune extension ne lui apprend à les lire : « je n'ai
      pas regardé » n'est pas « il ne s'est rien passé » ;
    - `side_effects` : ce qui a été écrit au passage, une entrée par module.
      Sur une instance `supportcredits`, l'entrée nomme le carnet débité, les
      unités et le solde restant. Une entrée portant `error` signale un
      observateur en panne : le ticket est créé, mais l'effet de bord n'a pas
      pu être vérifié.

    Annuler une imputation non voulue ne se fait pas d'ici : passer par
    l'onglet « Crédit support » du ticket dans Dolibarr.
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

        try:
            new_ticket_id = int(result)
        except (TypeError, ValueError):
            # L'API rend normalement l'id nu ; si elle rend autre chose, on
            # préfère un 0 explicite à une exception qui masquerait la création.
            new_ticket_id = 0

        side_effects = await _ticket_create_side_effects(new_ticket_id, thirdparty_id)
        message = f"Ticket créé avec succès (ID {result})."
        reported = " ".join(
            str(effect["message"]) for effect in side_effects if effect.get("message")
        )
        if reported:
            message = f"{message} {reported}"

        return _dumps({
            "success": True,
            "ticket_id": result,
            "subject": subject,
            "severity": severity_code,
            "type": type_code,
            "side_effects_checked": bool(TICKET_CREATE_OBSERVERS),
            "side_effects": side_effects,
            "message": message,
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_close_ticket(
    ticket_id: int = 0,
    ref: str = "",
    status: int = 8,
    message: str = "",
) -> str:
    """Ferme un ticket de support (statut 8 = fermé par défaut).

    Confirmation utilisateur requise avant exécution.

    L'API REST Dolibarr n'a pas d'endpoint de clôture : l'outil passe par
    PUT /tickets/{id}. Piège couvert : Ticket::update() écrit la colonne
    fk_statut depuis $this->status (Dolibarr récents), si bien qu'un PUT ne
    portant que fk_statut répond 200 sans rien écrire : on envoie les deux
    clés puis on RELIT le ticket pour vérifier que le statut a changé.
    Pour un statut 8 ou 9, on reproduit Ticket::close() : date_close = now
    et progress = 100.

    Paramètres :
    - ticket_id : ID numérique du ticket (ou ref)
    - ref       : référence du ticket (ex: 'TS2608-0042')
    - status    : statut cible (défaut 8=fermé ; 9=annulé ; les autres codes
                  Dolibarr sont acceptés pour rouvrir : 1=lu, 3=en cours…)
    - message   : message de clôture optionnel, ajouté au fil du ticket AVANT
                  le changement de statut (POST /tickets/newmessage)

    Retourne l'ancien et le nouveau statut du ticket.
    """
    try:
        status_key = str(status)
        if status_key not in TICKET_STATUS_LABELS:
            valid = ", ".join(
                f"{k}={v}"
                for k, v in sorted(TICKET_STATUS_LABELS.items(), key=lambda kv: int(kv[0]))
            )
            return f"Statut {status} invalide. Statuts Dolibarr : {valid}."

        ticket = await _fetch_ticket(ticket_id, ref)
        if isinstance(ticket, str):
            return ticket
        tid = int(ticket.get("id"))
        old_status = str(ticket.get("fk_statut"))

        message_posted = False
        if message:
            track_id = ticket.get("track_id")
            if not track_id:
                return (
                    f"Ticket {tid} sans track_id : impossible d'ajouter le message "
                    "(requis par POST /tickets/newmessage). Fermer sans message."
                )
            await api_post("tickets/newmessage", {"track_id": track_id, "message": message})
            message_posted = True

        if old_status == status_key:
            return _dumps({
                "success": True,
                "ticket_id": tid,
                "ref": ticket.get("ref"),
                "status": status,
                "status_label": TICKET_STATUS_LABELS[status_key],
                "message_posted": message_posted,
                "message": f"Ticket déjà au statut « {TICKET_STATUS_LABELS[status_key]} » : statut inchangé.",
            })

        payload: dict[str, Any] = {"status": status, "fk_statut": status}
        if status_key in ("8", "9"):
            payload["date_close"] = int(time.time())
            payload["progress"] = 100
        await api_put(f"tickets/{tid}", payload)

        # Relecture de contrôle : sans elle, l'échec silencieux décrit plus
        # haut passerait pour un succès.
        updated = await api_get(f"tickets/{tid}")
        new_status = str(updated.get("fk_statut"))
        if new_status != status_key:
            return (
                f"Échec silencieux : le PUT a répondu sans erreur mais le ticket {tid} "
                f"est resté au statut {new_status} ({_ticket_status_label(updated)}). "
                "Vérifier la version de Dolibarr et les droits du compte API."
            )

        return _dumps({
            "success": True,
            "ticket_id": tid,
            "ref": updated.get("ref"),
            "previous_status": int(old_status) if old_status.isdigit() else old_status,
            "previous_status_label": TICKET_STATUS_LABELS.get(old_status, ""),
            "status": status,
            "status_label": _ticket_status_label(updated),
            "date_close": _ts_to_date(updated.get("date_close")),
            "message_posted": message_posted,
            "message": f"Ticket {updated.get('ref')} basculé au statut « {TICKET_STATUS_LABELS[status_key]} ».",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_add_ticket_message(
    ticket_id: int = 0,
    ref: str = "",
    message: str = "",
) -> str:
    """Ajoute un message au fil d'un ticket de support.

    Confirmation utilisateur requise avant exécution.

    Le message est posté via POST /tickets/newmessage (identifié par le
    track_id du ticket) et apparaît dans l'historique du ticket.

    Paramètres :
    - ticket_id : ID numérique du ticket (ou ref)
    - ref       : référence du ticket (ex: 'TS2608-0042')
    - message   : contenu du message (obligatoire)
    """
    try:
        if not message:
            return "Le message est obligatoire."

        ticket = await _fetch_ticket(ticket_id, ref)
        if isinstance(ticket, str):
            return ticket
        track_id = ticket.get("track_id")
        if not track_id:
            return f"Ticket {ticket.get('id')} sans track_id : POST /tickets/newmessage impossible."

        await api_post("tickets/newmessage", {"track_id": track_id, "message": message})

        return _dumps({
            "success": True,
            "ticket_id": ticket.get("id"),
            "ref": ticket.get("ref"),
            "message": f"Message ajouté au ticket {ticket.get('ref')}.",
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_update_ticket(
    ticket_id: int = 0,
    ref: str = "",
    subject: str = "",
    severity: str = "",
    type_code: str = "",
    project_id: int = 0,
    thirdparty_id: int = 0,
    assigned_user_id: int = 0,
) -> str:
    """Met à jour un ticket de support : sujet, sévérité, type, rattachements.

    Confirmation utilisateur requise avant exécution.

    Seuls les paramètres fournis sont modifiés. Pour changer le STATUT,
    utiliser dolibarr_close_ticket (qui gère aussi la réouverture).

    Paramètres :
    - ticket_id        : ID numérique du ticket (ou ref)
    - ref              : référence du ticket (ex: 'TS2608-0042')
    - subject          : nouveau sujet
    - severity         : 'low', 'normal', 'high', 'critical'
    - type_code        : 'QUESTION', 'INCIDENT', 'REQUEST', 'SUPPORT'
    - project_id       : rattacher au projet (fk_project)
    - thirdparty_id    : rattacher au tiers (fk_soc)
    - assigned_user_id : assigner à l'utilisateur (fk_user_assign brut : ne
                         crée pas le contact et ne passe pas le statut à
                         « Assigné », contrairement à l'assignation via l'UI)
    """
    try:
        ticket = await _fetch_ticket(ticket_id, ref)
        if isinstance(ticket, str):
            return ticket
        tid = int(ticket.get("id"))

        payload: dict[str, Any] = {}
        if subject:
            payload["subject"] = subject
        if severity:
            severity_map = {
                "low": "LOW", "normal": "NORMAL",
                "high": "HIGH", "critical": "CRITICAL",
            }
            code = severity_map.get(severity.lower())
            if not code:
                return f"Sévérité '{severity}' invalide (low, normal, high, critical)."
            payload["severity_code"] = code
        if type_code:
            payload["type_code"] = type_code.upper()
        if project_id:
            payload["fk_project"] = project_id
        if thirdparty_id:
            payload["fk_soc"] = thirdparty_id
        if assigned_user_id:
            payload["fk_user_assign"] = assigned_user_id
        if not payload:
            return "Aucun champ à modifier : fournir au moins un paramètre."

        await api_put(f"tickets/{tid}", payload)
        updated = await api_get(f"tickets/{tid}")

        return _dumps({
            "success": True,
            "ticket_id": tid,
            "ref": updated.get("ref"),
            "subject": updated.get("subject"),
            "severity": updated.get("severity_code"),
            "type": updated.get("type_code"),
            "project_id": updated.get("fk_project"),
            "thirdparty_id": updated.get("fk_soc"),
            "assigned_to": updated.get("fk_user_assign"),
            "status_label": _ticket_status_label(updated),
            "updated_fields": sorted(payload),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_ticket(ticket_id: int = 0, ref: str = "") -> str:
    """Supprime définitivement un ticket de support (DELETE /tickets/{id}).

    Confirmation utilisateur requise avant exécution : suppression
    IRRÉVERSIBLE, fil de messages compris. Pour clore proprement un ticket,
    préférer dolibarr_close_ticket.

    Paramètres :
    - ticket_id : ID numérique du ticket (ou ref)
    - ref       : référence du ticket (ex: 'TS2608-0042')
    """
    try:
        ticket = await _fetch_ticket(ticket_id, ref)
        if isinstance(ticket, str):
            return ticket
        tid = int(ticket.get("id"))
        tref = ticket.get("ref")

        await api_delete(f"tickets/{tid}")

        return _dumps({
            "success": True,
            "ticket_id": tid,
            "ref": tref,
            "message": f"Ticket {tref} (ID {tid}) supprimé définitivement.",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools : Résolution et synthèse
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_resolve_email(email_address: str) -> str:
    """Résout une adresse email vers un utilisateur, un contact ou un tiers Dolibarr.

    C'est LE tool de corrélation : il permet de faire le lien entre
    un participant CalDAV, un expéditeur IMAP, et un tiers Dolibarr.

    Paramètres :
    - email_address : adresse email à résoudre

    Trois pistes sont suivies, et elles ne s'excluent pas :
    - `user` : un UTILISATEUR interne de l'instance. Une adresse interne n'est
      ni un tiers ni un contact ; la chercher uniquement dans ces deux tables
      la déclarait introuvable et interdisait de désigner un responsable
      d'action autrement qu'en nom libre ;
    - `contact` : un contact, avec le tiers auquel il est rattaché ;
    - `thirdparty` : un tiers, par son adresse ou à défaut par son domaine.

    `found` vaut True dès qu'une des trois pistes aboutit ; `matched_as` dit
    laquelle, pour que l'appelant sache à quoi il a affaire.
    """
    try:
        email_lower = email_address.strip().lower()
        domain = email_lower.split("@")[1] if "@" in email_lower else ""

        result: dict[str, Any] = {"email": email_lower, "found": False}
        matched: list[str] = []

        # 0. Utilisateurs internes. `GET /users/email/{email}` est la route
        # dédiée ; elle répond 404 quand l'adresse n'appartient à personne, ce
        # qui n'est pas une erreur mais une réponse.
        try:
            # L'adresse voyage dans le CHEMIN : une adresse à sous-adressage
            # (« nom+tag@domaine ») y verrait son « + » lu comme une espace.
            from urllib.parse import quote

            user = await api_get(f"{USERS_ENDPOINT}/email/{quote(email_lower, safe='@.')}")
            if isinstance(user, dict) and (user.get("id") or user.get("login")):
                result["found"] = True
                matched.append("user")
                result["user"] = {k: v for k, v in {
                    "id": _id_int(user.get("id") or user.get("rowid")),
                    "login": user.get("login"),
                    "firstname": user.get("firstname"),
                    "lastname": user.get("lastname"),
                    "email": user.get("email"),
                    "job": user.get("job") or None,
                    "enabled": str(user.get("statut") or user.get("status") or "1") == "1",
                }.items() if v not in (None, "")}
        except Exception:
            pass  # 404 = adresse non interne, cas nominal

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
                matched.append("contact")
                result["matched_as"] = matched
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
                matched.append("thirdparty")
                result["matched_as"] = matched
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
                    matched.append("domain")
                    result["matched_as"] = matched
                    return _dumps(result)
            except Exception:
                pass

        result["matched_as"] = matched
        if not result["found"]:
            result["suggestion"] = (
                f"Aucun utilisateur, tiers ni contact trouvé pour '{email_lower}'."
            )
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


#: Taille de page des balayages de factures pour le CA. La version précédente
#: demandait 500 factures d'un coup, sans filtre de date et triées par rowid
#: croissant : passé 500 factures validées, l'instance ne rendait plus que les
#: plus ANCIENNES et le CA du mois courant tombait à zéro sans le dire.
REVENUE_SCAN_PAGE = 100
#: Garde-fou de boucle : 50 pages = 5 000 factures sur la période demandée.
REVENUE_SCAN_MAX_PAGES = 50
#: Plafond d'écritures bancaires inspectées pour le CA encaissé. Au-delà, la
#: réponse porte `truncated: true` : jamais de troncature muette.
REVENUE_CASH_SCAN_MAX = 400


async def _invoices_in_range(date_start: str, date_end: str) -> list:
    """Factures clients validées ou payées d'une période, toutes pages lues.

    Le filtre de statut est écrit `>= 1 and <= 2` plutôt qu'avec l'opérateur
    `in` : la grammaire des sqlfilters interdit les parenthèses dans une valeur,
    donc `(t.fk_statut:in:(1,2))` est refusé à l'analyse.
    """
    sqlfilters = _sf_and(
        "(t.fk_statut:>=:1)",
        "(t.fk_statut:<=:2)",
        _sf_date_range(date_start, date_end, "t.datef"),
    )
    rows: list = []
    for page in range(REVENUE_SCAN_MAX_PAGES):
        chunk = await _api_get_list("invoices", {
            "sortfield": "t.datef,t.rowid",
            "sortorder": "DESC",
            "limit": REVENUE_SCAN_PAGE,
            "page": page,
            "sqlfilters": sqlfilters,
            "properties": "id,ref,total_ht,total_ttc,date,fk_statut,paye",
        })
        if not isinstance(chunk, list):
            raise ValueError("Balayage financier incomplet : réponse invalide.")
        merged = _unique_rows([*rows, *chunk])
        if chunk and len(merged) == len(rows):
            raise ValueError("Balayage financier incomplet : page répétée.")
        rows = merged
        if len(chunk) < REVENUE_SCAN_PAGE:
            if any(r.get("total_ht") is None or r.get("total_ttc") is None
                   or not _ts_to_date(r.get("date")) for r in rows):
                raise ValueError("Calcul incomplet : montant ou date de facture absent.")
            return rows
    raise ValueError("Balayage financier incomplet : plafond atteint, aucun total certifié.")


async def _customer_cash_in(date_start: str, date_end: str) -> dict:
    """Encaissements clients d'une période, reconstitués depuis les comptes bancaires.

    Il n'existe pas de route REST listant les règlements : la seule vue
    transversale des encaissements est le journal bancaire. On y retient les
    écritures créditrices portant un lien `payment`, celui que Dolibarr pose
    entre un règlement de facture client et sa ligne de banque.
    """
    accounts = await _scan_list(BANK_ACCOUNTS_ENDPOINT, {"limit": MAX_LIMIT})
    scanned: list[tuple[int, dict]] = []
    used_accounts = []
    for raw in accounts if isinstance(accounts, list) else []:
        acc = _format_bank_account(raw)
        if not acc.get("id") or acc.get("closed") or acc.get("type") == BANK_ACCOUNT_TYPE_CASH:
            continue
        used_accounts.append(acc["id"])
        lines = await _bank_lines(
            acc["id"], _bank_date_filter(after=date_start, before=date_end)
        )
        scanned.extend(
            (acc["id"], line)
            for line in lines
            if float(_num(line.get("amount")) or 0) > 0
            and _id_int(line.get("id") or line.get("rowid")) is not None
        )

    truncated = len(scanned) > REVENUE_CASH_SCAN_MAX or not accounts.complete
    kept = scanned[:REVENUE_CASH_SCAN_MAX]

    links = await _gather_limited([
        (lambda a=acc_id, ln=line: _bank_line_links(a, _id_int(ln.get("id") or ln.get("rowid"))))
        for acc_id, line in kept
    ])

    payments = []
    unresolved = 0
    for (acc_id, line), resolved in zip(kept, links):
        if isinstance(resolved, Exception):
            unresolved += 1
            truncated = True
            logger.warning("Liens illisibles sur l'écriture %s : %s", line.get("id"), resolved)
            continue
        sources = resolved.get("sources") or []
        if not any(src.get("type") == "payment" for src in sources):
            continue
        payments.append({
            "account_id": acc_id,
            "entry_id": _id_int(line.get("id") or line.get("rowid")),
            "date": _ts_to_date(line.get("dateo")),
            "amount_ttc": float(_num(line.get("amount")) or 0),
            "thirdparty": (resolved.get("thirdparty") or {}).get("name"),
        })

    return {
        "payments": payments,
        "unresolved_entries": unresolved,
        "accounts_complete": accounts.complete,
        "accounts_scanned": used_accounts,
        "entries_scanned": len(kept),
        "entries_matched": len(scanned),
        "truncated": truncated,
    }


@mcp.tool()
async def dolibarr_get_revenue_summary(
    year: int = 0,
    month: int = 0,
    basis: str = "emises",
) -> str:
    """Synthèse du chiffre d'affaires depuis les données Dolibarr.

    Paramètres :
    - year  : année (défaut : année en cours)
    - month : mois 1-12 (défaut : mois en cours)
    - basis : assiette du calcul, et c'est le paramètre déterminant :
        * 'emises' (défaut) : toutes les factures VALIDÉES, payées ou non
          (fk_statut 1 et 2), comptées à leur date de facture. C'est le CA
          commercial : une facture émise dans le mois y figure le jour de son
          émission, sans attendre son encaissement. Le total est NET des
          avoirs : dont le montant est négatif : et le détail est rendu à part
          (`month_gross_ht`, `month_credit_notes_ht`, `month_nb_credit_notes`)
          pour qu'une facture annulée par un avoir de même montant se lise, au
          lieu de sembler avoir disparu.
        * 'encaissees' : les encaissements clients réellement constatés en
          banque sur la période, à leur date d'opération. Montants TTC : ce
          sont des mouvements de compte, pas des bases HT.
      L'assiette retenue est toujours rappelée dans la réponse (`basis`,
      `basis_label`) : les deux chiffres sont légitimes et ne se comparent pas.

    Retourne, selon l'assiette : CA du mois et cumul annuel, nombre de factures
    ou d'encaissements, et pour 'emises' la comparaison au même mois de N-1.

    'encaissees' reconstruit les encaissements depuis le journal bancaire, faute
    de route REST listant les règlements : les comptes clos et les caisses sont
    exclus, et le balayage est plafonné à REVENUE_CASH_SCAN_MAX écritures : la
    réponse porte alors `truncated: true`.
    """
    from datetime import datetime

    now = datetime.now()
    year = year or now.year
    month = month or now.month
    if not 1 <= month <= 12:
        return f"Mois invalide : {month}. Attendu : 1 à 12."

    assiette = str(basis or "emises").strip().lower()
    if assiette not in ("emises", "encaissees"):
        return f"basis invalide : '{basis}'. Valeurs acceptées : 'emises', 'encaissees'."

    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    month_prefix = f"{year}-{month:02d}"

    try:
        if assiette == "encaissees":
            cash = await _customer_cash_in(year_start, year_end)
            in_month = [p for p in cash["payments"] if p["date"].startswith(month_prefix)]
            result: dict[str, Any] = {
                "year": year,
                "month": month,
                "basis": "encaissees",
                "definition": {"date_basis": "bank_operation", "amount_basis": "TTC",
                               "date_start": year_start, "date_end": year_end,
                               "ytd_scope": "année entière, nom historique"},
                "basis_label": (
                    "encaissements clients constatés en banque, à la date d'opération (TTC)"
                ),
                "month_ca_ttc": round(sum(p["amount_ttc"] for p in in_month), 2),
                "month_nb_payments": len(in_month),
                "ytd_ca_ttc": round(sum(p["amount_ttc"] for p in cash["payments"]), 2),
                "ytd_nb_payments": len(cash["payments"]),
                "accounts_scanned": cash["accounts_scanned"],
                "entries_scanned": cash["entries_scanned"],
                "unresolved_entries": cash["unresolved_entries"],
                "accounts_complete": cash["accounts_complete"],
            }
            if cash["truncated"]:
                result["truncated"] = True
                result["truncation_note"] = (
                    f"{cash['entries_matched']} écritures créditrices sur la période, "
                    f"{cash['entries_scanned']} inspectées (plafond REVENUE_CASH_SCAN_MAX). "
                    f"{cash['unresolved_entries']} liens illisibles. "
                    "Montants incomplets : vérifier les droits et restreindre la période."
                )
            return _dumps(result)

        invoices = await _invoices_in_range(year_start, year_end)
        month_invoices = [
            inv for inv in invoices if _ts_to_date(inv.get("date")).startswith(month_prefix)
        ]

        def _split(rows: list) -> tuple:
            """Sépare factures et avoirs. Un avoir a un total NÉGATIF."""
            credits = [r for r in rows if float(r.get("total_ht") or 0) < 0]
            return [r for r in rows if r not in credits], credits

        month_gross, month_credits = _split(month_invoices)
        year_gross, year_credits = _split(invoices)

        result = {
            "year": year,
            "month": month,
            "basis": "emises",
            "definition": {"date_basis": "invoice_date", "credit_note_date": "own_date",
                           "statuses": [1, 2], "date_start": year_start,
                           "date_end": year_end, "bounds": "inclusive",
                           "ytd_scope": "année entière, nom historique"},
            "basis_label": (
                "factures validées ou payées (fk_statut 1 et 2), à la date de facture, "
                "NET des avoirs de la période"
            ),
            "month_ca_ht": round(sum(float(i.get("total_ht") or 0) for i in month_invoices), 2),
            "month_ca_ttc": round(sum(float(i.get("total_ttc") or 0) for i in month_invoices), 2),
            "month_nb_invoices": len(month_invoices),
            "ytd_ca_ht": round(sum(float(i.get("total_ht") or 0) for i in invoices), 2),
            "ytd_ca_ttc": round(sum(float(i.get("total_ttc") or 0) for i in invoices), 2),
            "ytd_nb_invoices": len(invoices),
            # Le CA ci-dessus est NET d'avoirs. Sans ce détail, une facture
            # annulée le jour même par un avoir de même montant fait disparaître
            # les deux d'un coup d'œil, et le chiffre paraît faux alors qu'il est
            # juste. Vérifié en production le 31/08/2026 : FA2608-0940 (46 050 €)
            # émise et créditée le 19/08 par AV2608-0651 et AV2608-0652.
            "month_gross_ht": round(sum(float(i.get("total_ht") or 0) for i in month_gross), 2),
            "month_credit_notes_ht": round(
                sum(float(i.get("total_ht") or 0) for i in month_credits), 2
            ),
            "month_nb_credit_notes": len(month_credits),
            "ytd_gross_ht": round(sum(float(i.get("total_ht") or 0) for i in year_gross), 2),
            "ytd_credit_notes_ht": round(
                sum(float(i.get("total_ht") or 0) for i in year_credits), 2
            ),
            "ytd_nb_credit_notes": len(year_credits),
        }

        prev = await _invoices_in_range(
            f"{year - 1}-{month:02d}-01", _month_last_day(year - 1, month)
        )
        prev_ca = round(sum(float(i.get("total_ht") or 0) for i in prev), 2)
        result["n_minus_1_month_ca_ht"] = prev_ca
        if prev_ca > 0:
            result["variation_vs_n1_pct"] = round(
                ((result["month_ca_ht"] - prev_ca) / prev_ca) * 100, 1
            )

        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


def _month_last_day(year: int, month: int) -> str:
    """Dernier jour d'un mois, au format YYYY-MM-DD."""
    import calendar

    return f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


# ---------------------------------------------------------------------------
# Tools : Actions / Agenda
# ---------------------------------------------------------------------------


@mcp.tool()
async def dolibarr_list_actions_open(
    project_id: int=0,
    thirdparty_id: int=0,
    assigned_to: int=0,
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les actions ouvertes (agenda, CR, suivi) dans Dolibarr.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

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

        actions = await _scan_list("agendaevents", params)

        if not isinstance(actions, list):
            return _dumps(_export_payload({"count": 0, "actions": []}, "actions", limit, cursor, scan=actions))

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

        return _dumps(_export_payload({"count": len(results), "actions": results}, "actions", limit, cursor, scan=actions))

    except Exception as exc:
        return _format_error(exc)

# ---------------------------------------------------------------------------
# Tools : Dashboard
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

    # Projets ouverts : ne charger que id+ref pour compter
    try:
        projects = await api_get("projects", {
            "sqlfilters": "(t.fk_statut:=:1)",
            "limit": 200,
            "properties": "id",
        })
        dashboard["projects_open"] = len(projects) if isinstance(projects, list) else "?"
    except Exception as exc:
        dashboard["projects_open"] = f"Erreur : {exc}"

    # Factures impayées : ne charger que les champs nécessaires au calcul
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
# Tools : Projets (création/modification)
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
                        "  1. FK enfant non gérée par Project::delete() : Dolibarr abandonne.\n"
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
                        "  c. En dernier recours (irréversible : backup DB avant !) :\n"
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
    cond_reglement_id: Annotated[int, Field(strict=True, gt=0)] | None = None,
) -> str:
    """Crée une proposition commerciale dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - thirdparty_id : ID du tiers (obligatoire)
    - title         : titre de la proposition (optionnel)
    - project_id    : ID du projet associé (optionnel)
    - date          : date de création (format YYYY-MM-DD, optionnel : défaut : aujourd'hui)
    - date_validity : date de fin de validité (format YYYY-MM-DD, optionnel).
                      Sans ce paramètre, Dolibarr peut afficher 15/02/1970
                      (bug timestamp : duree_validite*86400 interprété comme epoch)
                      quand la date de création est absente. Recommandé : toujours
                      fournir cette date, ex. aujourd'hui + 45 jours.
    - note_public   : note publique (optionnel)
    - note_private  : note privée (optionnel)
    - cond_reglement_id : entier strictement positif, prioritaire sur la condition
      du tiers. Omis ou None : lire le tiers avant création et reprendre sa condition.

    Retourne l'ID et payment_terms_applied (cond_reglement_id, code, label,
    source : parametre, tiers ou aucune, error). Sans condition sur le tiers ou
    si sa lecture échoue, aucun identifiant n'est envoyé ; l'erreur est rendue.
    """
    try:
        if not thirdparty_id:
            return "Le thirdparty_id est obligatoire."
        if cond_reglement_id is not None and (
            type(cond_reglement_id) is not int or cond_reglement_id <= 0
        ):
            return _dumps({
                "success": False,
                "error": "cond_reglement_id doit être un entier strictement positif.",
            })

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

        payment_terms = await _creation_payment_terms(thirdparty_id, cond_reglement_id)
        if payment_terms["cond_reglement_id"] is not None:
            data["cond_reglement_id"] = payment_terms["cond_reglement_id"]
        result = await api_post("proposals", data)

        return _dumps({
            "success": True,
            "proposal_id": result,
            "thirdparty_id": thirdparty_id,
            "date_validity": date_validity or "(défaut Dolibarr)",
            "status": "brouillon",
            "payment_terms_applied": payment_terms,
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
            return "La liste lines est vide : aucune ligne à ajouter."

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
            #   foreach ($request_data as $TData) : itère sur les clés du dict
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
# Tools : Détail facture / propal + validate + download PDF
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


def _bounded_text(text: Any, limit: int = DETAIL_NOTE_MAX_CHARS) -> "tuple[str, bool, int]":
    """Borne un texte brut sans jamais le couper en silence. Retourne
    (texte, truncated, length) : `length` est la longueur du texte entier,
    `truncated` vaut True si `texte` en est une coupe. None devient "".

    Une lecture de détail rend ainsi le texte entier jusqu'à `limit`, et dit
    quand elle a coupé : l'appelant peut comparer ce qu'il a écrit à ce qu'il
    relit, au lieu de conclure à une perte en base.
    """
    s = "" if text is None else str(text)
    return s[:limit], len(s) > limit, len(s)


def _bounded_note(text: Any, limit: int = DETAIL_NOTE_MAX_CHARS) -> "tuple[str, bool, int]":
    """Nettoie une note HTML (cf. _strip_html) puis la borne comme _bounded_text :
    la longueur annoncée est celle du texte nettoyé, pas celle du HTML brut."""
    return _bounded_text(_strip_html(text), limit)


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
async def dolibarr_get_invoice(
    invoice_id: int=0,
    ref: str='',
    include_payments: bool=True,
    include_payment_terms: bool=True,
) -> str:
    """Détail d'une facture client par ID numérique ou par référence.

    include_payments : vrai par défaut, lit les paiements des factures rendues.
    Une lecture refusée est signalée, jamais remplacée par une liste vide.

    include_payment_terms : vrai par défaut, rend cond_reglement_id, _code et
    _label de la facture et thirdparty_cond_reglement_id, _code et _label du tiers.
    Lit le dictionnaire en cache et une fois chaque tiers de la page rendue.
    À false, aucun appel complémentaire et champs null avec la raison.
    Une lecture impossible est signalée dans unavailable_fields avec son erreur.
    Hors de ces erreurs, null sur les deux conditions signifie qu'aucune condition
    de règlement n'est paramétrée dans Dolibarr.

    date_due à date de facture + 1 jour correspond notamment à la condition
    « À réception » (RECEP, nbjour = 1), parfois appliquée par défaut Dolibarr.
    date_due_is_default signale cet intervalle (null si inconnu), sans prouver
    l'absence de condition. Vérifier la condition négociée avant une relance :
    days_late peut surévaluer le retard si le délai réel est plus long.

    Fournir soit invoice_id, soit ref. Retourne les informations complètes :
    ref, tiers, montants HT/TTC/TVA, statut, échéance, paiements associés
    (si paye=1, indique le montant payé), et TOUTES les lignes détaillées
    (id, type produit/service, produit lié, libellé, quantité, prix, remise %,
    TVA, totaux, dates de prestation).

    Paramètres :
    - invoice_id : ID de la facture
    - ref : référence (ex: FA2605-0001, ou (PROV1234) pour un brouillon)

    Note publique : `note_public` est rendue entière, HTML nettoyé, jusqu'à
    DETAIL_NOTE_MAX_CHARS (20 000) caractères. Au-delà, elle est coupée à ce
    plafond et la coupe est annoncée : `note_public_truncated` passe à true et
    `note_public_length` donne la longueur réelle du texte nettoyé. Ces deux
    champs sont toujours présents. Elle était coupée à 300 caractères sans le
    dire.
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

        note_text, note_truncated, note_length = _bounded_note(inv.get("note_public"))

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
            "date_invoice": _ts_to_date(_first_present(inv, "date", "datef")),
            "date_due": _ts_to_date(inv.get("date_lim_reglement")),
            "fk_project": inv.get("fk_project"),
            "ref_customer": inv.get("ref_client"),
            "note_public": note_text,
            "note_public_length": note_length,
            "note_public_truncated": note_truncated,
            "model_pdf": inv.get("model_pdf"),
            "lines_count": len(lines_raw),
            "lines": lines,
        }
        result.update(_financial_fields(inv, proposal=False))
        await _with_invoice_payment_terms(result, inv, {}, include_payment_terms)
        await _with_invoice_payments(result, False, include_payments)
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_proposal(proposal_id: int = 0, ref: str = "") -> str:
    """Détail d'une proposition commerciale par ID numérique ou par référence.

    Fournir soit proposal_id, soit ref. Retourne ref, tiers, montants, statut,
    date de validité, la note publique, et TOUTES les lignes détaillées (id,
    type produit/service, produit lié, libellé, quantité, prix, remise %, TVA,
    totaux, dates).

    Note publique : `note_public` est rendue entière, HTML nettoyé, jusqu'à
    DETAIL_NOTE_MAX_CHARS (20 000) caractères. Au-delà, elle est coupée à ce
    plafond et la coupe est annoncée : `note_public_truncated` passe à true et
    `note_public_length` donne la longueur réelle du texte nettoyé. Ces deux
    champs sont toujours présents : une note écrite par dolibarr_update_proposal
    se vérifie en la relisant. Elle était coupée à 300 caractères sans le dire
    (constat du 14/09/2026 sur la propale 172).

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
        note_text, note_truncated, note_length = _bounded_note(p.get("note_public"))

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
            "note_public": note_text,
            "note_public_length": note_length,
            "note_public_truncated": note_truncated,
            "model_pdf": p.get("model_pdf"),
            "lines_count": len(lines_raw),
            "lines": lines,
        }
        result.update(_financial_fields(p, proposal=True))
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_get_supplier_invoice(
    invoice_id: int=0,
    ref: str='',
    include_payments: bool=True,
    include_payment_terms: bool=True,
) -> str:
    """Détail d'une facture FOURNISSEUR par ID numérique ou par référence.

    include_payments : vrai par défaut, lit les paiements des factures rendues.
    Une lecture refusée est signalée, jamais remplacée par une liste vide.

    include_payment_terms : vrai par défaut, rend cond_reglement_id, _code et
    _label de la facture et thirdparty_cond_reglement_id, _code et _label du tiers.
    Lit le dictionnaire en cache et une fois chaque tiers de la page rendue.
    À false, aucun appel complémentaire et champs null avec la raison.
    Une lecture impossible est signalée dans unavailable_fields avec son erreur.
    Hors de ces erreurs, null sur les deux conditions signifie qu'aucune condition
    de règlement n'est paramétrée dans Dolibarr.

    date_due à date de facture + 1 jour correspond notamment à la condition
    « À réception » (RECEP, nbjour = 1), parfois appliquée par défaut Dolibarr.
    date_due_is_default signale cet intervalle (null si inconnu), sans prouver
    l'absence de condition. Vérifier la condition négociée avant une relance :
    days_late peut surévaluer le retard si le délai réel est plus long.

    Pendant fournisseur de dolibarr_get_invoice. Fournir soit invoice_id, soit
    ref (ref interne Dolibarr type SI2602-0084). Retourne ref interne, ref
    fournisseur, tiers, libellé, montants HT/TVA/TTC, statut, échéance, projet
    lié, et TOUTES les lignes détaillées (mêmes champs que get_invoice).

    Paramètres :
    - invoice_id : ID de la facture fournisseur
    - ref : référence interne Dolibarr (ex: SI2602-0084)

    Note publique : `note_public` est rendue entière, HTML nettoyé, jusqu'à
    DETAIL_NOTE_MAX_CHARS (20 000) caractères. Au-delà, elle est coupée à ce
    plafond et la coupe est annoncée : `note_public_truncated` passe à true et
    `note_public_length` donne la longueur réelle du texte nettoyé. Ces deux
    champs sont toujours présents. Elle était coupée à 300 caractères sans le
    dire.
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

        note_text, note_truncated, note_length = _bounded_note(inv.get("note_public"))

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
            "date_invoice": _ts_to_date(_first_present(inv, "date", "datef")),
            "date_due": _ts_to_date(
                inv.get("date_echeance") or inv.get("date_lim_reglement")
            ),
            "fk_project": inv.get("fk_project"),
            "note_public": note_text,
            "note_public_length": note_length,
            "note_public_truncated": note_truncated,
            "model_pdf": inv.get("model_pdf"),
            "lines_count": len(lines_raw),
            "lines": lines,
        }
        result.update(_financial_fields(inv, proposal=False))
        await _with_invoice_payment_terms(result, inv, {}, include_payment_terms)
        await _with_invoice_payments(result, True, include_payments)
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
    - project_id   : rattacher la facture à ce projet : champ API `fk_project`.
                     0 = ne pas toucher au lien projet.
    - ref_customer : référence client (champ API `ref_client`)
    - note_public  : note publique
    - note_private : note privée
    - date_due     : date limite de règlement (format YYYY-MM-DD)

    Statut : vérifié en production le 11/08/2026 sur FA2608-0939 (Dolibarr
    23.0.0) : un PUT sur une facture **validée non payée** (fk_statut=1,
    paye=0) renvoie HTTP 200 et le champ est persisté. `Facture::update()`
    écrit `fk_projet` sans aucun garde de statut, et `api_invoices::put()`
    n'en pose pas non plus. Le rattachement projet ne demande donc pas de
    repasser la facture en brouillon.

    Effet de bord mesuré sur ce même essai : `fk_user_modif` prend l'id de
    l'utilisateur de la clé API et le trigger FACTURE_MODIFY se déclenche.
    Aucun autre champ ne bouge : `Facture::update()` réécrit toute la ligne,
    mais à partir de l'objet relu juste avant, donc à valeurs identiques.

    Piège d'interface, sans rapport avec l'API : dans `card.php`, le sélecteur
    de projet d'une facture **ne liste pas les projets fermés**. Un champ qui
    paraît vide dans l'UI ne signifie donc pas « aucun projet rattaché » :
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
    - project_id    : rattacher la propal à ce projet : champ API `fk_project`.
                      0 = ne pas toucher au lien projet.
    - title         : titre/objet de la proposition (champ `titre`)
    - date          : date de la proposition (format YYYY-MM-DD)
    - date_validity : date de fin de validité (format YYYY-MM-DD)
    - note_public   : note publique
    - note_private  : note privée
    - ref_customer  : référence client (champ API `ref_client`)

    Lien projet : vérifié en prod (Dolibarr 23.0.0) : l'API REST expose la
    propriété `fk_project` (PAS `fk_projet`, qui est l'orthographe de la colonne
    SQL) ; `Propal::update()` persiste bien le lien. Un PUT {"fk_project":"258"}
    relu via GET renvoie fk_project="258".

    Statut : l'update est autorisé quel que soit le statut : rattacher un projet
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
    - force       : défaut False : seuls les BROUILLONS (statut 0) sont
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
                        f"Proposition {proposal_id} introuvable (404) : rien à supprimer."
                    ),
                })
            if get_exc.response.status_code in (401, 403):
                return _format_error(get_exc)
            snapshot_error = _format_error(get_exc)
        except Exception as get_exc:
            snapshot_error = str(get_exc)

        # Garde-fou : seuls les brouillons sont supprimables, sauf force=True.
        # (Cohérent avec update/delete_proposal_line ; évite de détruire une
        # propale engagée : validée/signée/facturée : sur un simple appel.)
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
                        "  c. En dernier recours (irréversible : backup DB avant !) :\n"
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

    Exemple : porter une remise de 15 % sur une ligne existante (au lieu d'une
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
    (Dolibarr les verrouille sinon : voir dolibarr_update_proposal_line). Workflow
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
# Tools : Banque : comptes, écritures, rapprochement
# ---------------------------------------------------------------------------

#: Routes REST du module Banque (vérifiées sur api_bankaccounts.class.php 24.0.0).
#: Attention : `BANK_LINE_ENDPOINT` n'est PAS imbriquée sous le compte : la route
#: déclarée est `GET /bankaccounts/lines/{line_id}`, sans identifiant de compte.
BANK_ACCOUNTS_ENDPOINT = "bankaccounts"
BANK_ACCOUNT_ENDPOINT = "bankaccounts/{account_id}"
BANK_LINES_ENDPOINT = "bankaccounts/{account_id}/lines"
BANK_LINE_ENDPOINT = "bankaccounts/lines/{line_id}"
BANK_ACCOUNT_LINE_ENDPOINT = "bankaccounts/{account_id}/lines/{line_id}"
BANK_LINE_LINKS_ENDPOINT = "bankaccounts/{account_id}/lines/{line_id}/links"
BANK_BALANCE_ENDPOINT = "bankaccounts/{account_id}/balance"
PAYMENT_TYPES_ENDPOINT = "setup/dictionary/payment_types"
PAYMENT_TERMS_ENDPOINT = "setup/dictionary/payment_terms"

#: llx_bank_account.courant : Account::TYPE_SAVINGS / TYPE_CURRENT / TYPE_CASH.
BANK_ACCOUNT_TYPES = {"0": "épargne", "1": "compte courant", "2": "caisse"}
BANK_ACCOUNT_TYPE_CASH = "2"
#: llx_bank_account.clos : Account::STATUS_OPEN / STATUS_CLOSED.
BANK_ACCOUNT_STATUS = {"0": "ouvert", "1": "clos"}
BANK_ACCOUNT_STATUS_OPEN = "0"

#: Fenêtre appliquée à dolibarr_list_bank_entries quand aucune date de début
#: n'est fournie. `GET /bankaccounts/{id}/lines` n'accepte NI limit NI page :
#: Dolibarr instancie un AccountLine par ligne retournée, donc une requête sans
#: borne basse relit tout l'historique du compte et finit en timeout. La fenêtre
#: est renvoyée dans la réponse (`date_start`) : jamais appliquée en silence.
BANK_ENTRIES_DEFAULT_WINDOW_DAYS = 90

#: Types de lien `llx_bank_url` qui désignent l'objet à l'origine de l'écriture.
#: 'company' est traité à part : c'est le tiers, pas l'objet source.
BANK_LINK_SOURCE_LABELS = {
    "payment": "règlement facture client",
    "payment_supplier": "règlement facture fournisseur",
    "payment_various": "paiement divers",
    "payment_sc": "règlement charge sociale",
    "payment_vat": "règlement TVA",
    "payment_salary": "règlement salaire",
    "payment_loan": "règlement emprunt",
    "payment_donation": "règlement don",
    "payment_expensereport": "règlement note de frais",
    "banktransfert": "virement interne",
    "withdraw": "prélèvement",
    "project": "projet",
}

#: Modes de règlement, indexés par code et par id. Rempli au premier appel :
#: le dictionnaire c_paiement ne bouge pas en cours de session. Les tests le
#: remettent à None pour repartir d'un cache vide.
_PAYMENT_TYPES_CACHE: "dict[str, dict] | None" = None

# Un refus de ce dictionnaire facultatif doit aussi être mémorisé : le répéter
# pour chaque facture ralentirait les lectures sans rendre l'échéance plus fiable.
_PAYMENT_TERMS_CACHE: dict | None = None


async def _payment_terms() -> dict:
    """Mémorise les conditions, même si la route manque ou refuse l'accès."""
    global _PAYMENT_TERMS_CACHE
    if _PAYMENT_TERMS_CACHE is not None:
        return _PAYMENT_TERMS_CACHE

    par_code: dict[str, dict] = {}
    par_id: dict[str, dict] = {}
    erreur = None
    try:
        # Une facture ancienne peut encore porter une condition devenue inactive.
        lignes = await api_get(PAYMENT_TERMS_ENDPOINT, {"limit": 100})
        if not isinstance(lignes, list) or any(not isinstance(ligne, dict) for ligne in lignes):
            raise ValueError("Dictionnaire des conditions de règlement illisible.")
        for ligne in lignes:
            entree = {
                "id": _id_int(_first_present(ligne, "id", "rowid")),
                "code": ligne.get("code") or None,
                "label": (ligne.get("label") or ligne.get("libelle")
                          or ligne.get("libelle_facture") or None),
            }
            if entree["code"]:
                par_code[str(entree["code"]).upper()] = entree
            if entree["id"] is not None:
                par_id[str(entree["id"])] = entree
    except Exception as exc:
        par_code, par_id = {}, {}
        erreur = _format_error(exc)
    _PAYMENT_TERMS_CACHE = {"by_code": par_code, "by_id": par_id, "error": erreur}
    return _PAYMENT_TERMS_CACHE


async def _payment_types() -> dict:
    """Charge (et mémorise) les modes de règlement actifs de Dolibarr.

    Retourne {"by_code": {"VIR": {...}}, "by_id": {"4": {...}}}.
    """
    global _PAYMENT_TYPES_CACHE
    if _PAYMENT_TYPES_CACHE is not None:
        return _PAYMENT_TYPES_CACHE

    rows = await api_get(PAYMENT_TYPES_ENDPOINT, {"limit": 100, "active": 1})
    by_code: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        entry = {
            "id": _id_int(row.get("id")),
            "code": str(row.get("code") or ""),
            "label": row.get("label") or row.get("libelle") or "",
        }
        if entry["code"]:
            by_code[entry["code"].upper()] = entry
        if entry["id"] is not None:
            by_id[str(entry["id"])] = entry
    _PAYMENT_TYPES_CACHE = {"by_code": by_code, "by_id": by_id}
    return _PAYMENT_TYPES_CACHE


async def _resolve_payment_type(value: Any) -> dict:
    """Résout un mode de règlement donné par code ('VIR') ou par id ('4').

    Les deux formes circulent : l'API des règlements attend l'ID numérique
    (`paymentid`) alors que l'API des écritures bancaires attend le CODE
    (`type`). On résout une fois, et chaque appelant prend ce dont il a besoin.

    Lève ValueError avec la liste des codes valides si la valeur est inconnue :
    Dolibarr, lui, accepte un id inexistant et écrit une ligne sans mode.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Mode de règlement manquant (ex. 'VIR', 'CHQ', 'LIQ', 'CB', 'PRE').")

    types = await _payment_types()
    found = types["by_id"].get(raw) if raw.isdigit() else types["by_code"].get(raw.upper())
    if found:
        return found

    known = ", ".join(sorted(types["by_code"])) or "(dictionnaire vide)"
    raise ValueError(
        f"Mode de règlement inconnu : {raw!r}. Codes actifs sur cette instance : {known}."
    )


def _document_module(module: str) -> str:
    """Traduit un nom de module REST vers le nom d'élément attendu par Dolibarr."""
    key = str(module or "").strip().lower()
    return DOCUMENT_MODULE_ALIASES.get(key, key)


def _document_ref(ref: str) -> str:
    """Normalise une référence pour le stockage des pièces jointes.

    Une référence provisoire est rendue par Dolibarr sous la forme `(PROV92)`,
    parenthèses comprises, mais `dol_sanitizeFileName()` les retire pour bâtir
    le dossier : le répertoire réel est `PROV92`. Passer la forme parenthésée
    telle quelle fait répondre au serveur une page non-JSON, remontée comme une
    banale erreur de parsage sans indiquer le champ fautif.
    """
    cleaned = str(ref or "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    return cleaned.replace("(", "").replace(")", "")


def _format_bank_account(raw: dict) -> dict:
    """Formate un compte bancaire Dolibarr en dict lisible.

    `reconcilable` reproduit le test que Dolibarr applique lui-même pour
    décider qu'un compte est rapprochable (Account::load_board) :
    `rappro = 1 AND courant != TYPE_CASH`, complété par « compte non clos ».
    Le champ brut `rappro` est conservé à côté : c'est lui que bascule
    dolibarr_toggle_bank_reconciliation.
    """
    if not isinstance(raw, dict):
        return {}
    acc_type = str(raw.get("type") if raw.get("type") is not None else raw.get("courant") or "")
    status = str(raw.get("status") if raw.get("status") is not None else raw.get("clos") or "0")
    rappro = _id_int(raw.get("rappro"))
    out = {
        "id": _id_int(raw.get("id") or raw.get("rowid")),
        "ref": raw.get("ref"),
        "label": raw.get("label"),
        "bank": raw.get("bank"),
        "number": raw.get("number"),
        "iban": raw.get("iban") or raw.get("iban_prefix"),
        "bic": raw.get("bic"),
        "currency_code": raw.get("currency_code"),
        "type": acc_type,
        "type_label": BANK_ACCOUNT_TYPES.get(acc_type, acc_type),
        "status": status,
        "status_label": BANK_ACCOUNT_STATUS.get(status, status),
        "closed": status != BANK_ACCOUNT_STATUS_OPEN,
        "rappro": rappro,
        "reconcilable": bool(
            rappro == 1
            and acc_type != BANK_ACCOUNT_TYPE_CASH
            and status == BANK_ACCOUNT_STATUS_OPEN
        ),
        "balance": _num(raw.get("balance")),
        "accountancy_code": raw.get("account_number") or None,
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def _format_bank_line(raw: dict) -> dict:
    """Formate une écriture bancaire (AccountLine) en dict lisible.

    `amount` est SIGNÉ dans llx_bank : positif = entrée d'argent (crédit),
    négatif = sortie (débit). On expose les deux lectures : `amount` signé et
    le couple `debit`/`credit` : parce qu'une erreur de signe sur une écriture
    bancaire est silencieuse et coûteuse.
    """
    if not isinstance(raw, dict):
        return {}
    amount = float(_num(raw.get("amount")) or 0)
    out = {
        "id": _id_int(raw.get("id") or raw.get("rowid") or raw.get("ref")),
        "account_id": _id_int(raw.get("fk_account")),
        "date_operation": _ts_to_date(raw.get("dateo")),
        "date_value": _ts_to_date(raw.get("datev")),
        "label": _strip_html(raw.get("label")),
        "payment_type": raw.get("fk_type") or None,
        "num_payment": raw.get("num_chq") or None,
        "amount": amount,
        "debit": round(-amount, 2) if amount < 0 else 0,
        "credit": round(amount, 2) if amount > 0 else 0,
        "num_releve": raw.get("num_releve") or None,
        "reconciled": bool(_id_int(raw.get("rappro"))),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def _format_bank_links(links: Any) -> dict:
    """Extrait d'une liste de liens `llx_bank_url` le tiers et l'objet source.

    Une écriture bancaire ne porte NI socid NI référence d'objet : le
    rattachement passe uniquement par llx_bank_url, d'où un appel par ligne.
    """
    thirdparty: dict | None = None
    sources: list[dict] = []
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict):
            continue
        ltype = str(link.get("type") or "")
        entry = {
            "type": ltype,
            "type_label": BANK_LINK_SOURCE_LABELS.get(ltype, ltype),
            "id": _id_int(link.get("url_id")),
            "label": _strip_html(link.get("label")) or None,
        }
        if ltype == "company":
            thirdparty = {"id": entry["id"], "name": entry["label"]}
        else:
            sources.append({k: v for k, v in entry.items() if v not in (None, "")})
    out: dict[str, Any] = {}
    if thirdparty and thirdparty.get("id"):
        out["thirdparty"] = thirdparty
    if sources:
        out["sources"] = sources
    return out


def _today_iso() -> str:
    """Date du jour au format YYYY-MM-DD (fuseau serveur)."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def _shift_days(date_str: str, days: int) -> str:
    """Décale une date YYYY-MM-DD de `days` jours. Lève ValueError si invalide."""
    from datetime import datetime, timedelta

    d = datetime.strptime(date_str.strip(), "%Y-%m-%d") + timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def _bank_date_filter(after: str = "", before: str = "", strict_after: bool = False) -> str:
    """Construit le sqlfilters d'une plage de dates sur llx_bank.

    Piège : `GET /bankaccounts/{id}/lines` construit son SQL sur
    `FROM llx_bank` SANS alias. Un critère préfixé `t.` : la forme employée
    partout ailleurs dans ce serveur : produit un « unknown table t » et un
    HTTP 503. Les critères doivent donc porter le nom de colonne nu.
    """
    clauses = []
    if after:
        clauses.append(f"(dateo:{'>' if strict_after else '>='}:'{_sf_escape(after)}')")
    if before:
        clauses.append(f"(dateo:<=:'{_sf_escape(before)}')")
    return " and ".join(clauses)


async def _bank_lines(account_id: int, sqlfilters: str = "") -> list:
    """Lignes brutes d'un compte, filtrées côté serveur (tri client ensuite).

    L'endpoint trie par rowid et n'accepte ni limit ni page : la pagination et
    le tri par date sont nécessairement faits ici.
    """
    params: dict[str, Any] = {}
    if sqlfilters:
        params["sqlfilters"] = sqlfilters
    lines = await api_get(BANK_LINES_ENDPOINT.format(account_id=account_id), params)
    return [line for line in (lines or []) if isinstance(line, dict)]


def _sum_amounts(lines: list) -> float:
    """Somme signée des montants d'un lot de lignes bancaires."""
    return round(sum(float(_num(line.get("amount")) or 0) for line in lines), 2)


def _sort_bank_lines(lines: list, descending: bool) -> list:
    """Trie des lignes bancaires par date d'opération puis par rowid.

    Le rowid départage les écritures du même jour : sans lui l'ordre dépend de
    l'ordre de retour SQL et le solde cumulé devient non reproductible.
    """
    return sorted(
        lines,
        key=lambda ln: (_ts_to_date(ln.get("dateo")), _id_int(ln.get("id") or ln.get("rowid")) or 0),
        reverse=descending,
    )


@mcp.tool()
async def dolibarr_list_bank_accounts(
    include_closed: bool=False,
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les comptes bancaires avec leur solde courant.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    C'est la donnée qui alimente un suivi de trésorerie : libellé, type, numéro,
    solde, statut ouvert/clos et indicateur de rapprochement.

    Paramètres :
    - include_closed : inclure les comptes clos (défaut : non)

    Retourne pour chaque compte : id, ref, label, banque, numéro, IBAN/BIC,
    devise, type (`épargne` / `compte courant` / `caisse`), statut, `balance`,
    `rappro` (drapeau brut) et `reconcilable` (compte réellement rapprochable :
    rappro actif, type ≠ caisse, compte non clos).

    Le solde renvoyé est celui que Dolibarr calcule lui-même (`Account::solde(1)`)
    : il EXCLUT les écritures postdatées, pour coller au solde réel du compte.
    """
    try:
        accounts = await _scan_list(BANK_ACCOUNTS_ENDPOINT, {"limit": MAX_LIMIT})
        results = []
        total = 0.0
        for raw in accounts if isinstance(accounts, list) else []:
            acc = _format_bank_account(raw)
            if not acc:
                continue
            if acc.get("closed") and not include_closed:
                continue
            results.append(acc)
            total += float(acc.get("balance") or 0)

        return _dumps(_export_payload({
            "count": len(results),
            "total_balance": round(total, 2),
            "include_closed": include_closed,
            "accounts": results,
        }, "accounts", limit, cursor, scan=accounts))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_list_bank_entries(
    account_id: int,
    date_start: str = "",
    date_end: str = "",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    sort: str = "desc",
    with_links: bool = True,
    reconciled: str = "",
    cursor: str = "",
) -> str:
    """Écritures d'un compte bancaire sur une période, solde cumulé compris.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - account_id : ID du compte (obligatoire, cf. dolibarr_list_bank_accounts)
    - date_start : début de période (YYYY-MM-DD, incluse). Par défaut, 90 jours
      avant date_end : l'API ne sait pas paginer les lignes et une requête sans
      borne basse relit tout l'historique. La fenêtre effectivement appliquée
      est toujours renvoyée dans `date_start`.
    - date_end   : fin de période (YYYY-MM-DD, incluse). Défaut : aucune borne.
    - limit / offset : pagination, appliquée après tri
    - sort : 'desc' (défaut, plus récentes d'abord) ou 'asc'
    - with_links : résoudre le tiers et l'objet source de chaque écriture
      (facture, règlement, paiement divers…). Coûte un appel par écriture
      renvoyée : passer False pour un simple relevé.
    - reconciled : 'no' pour ne garder que les écritures NON rapprochées (celles
      qui restent à traiter), 'yes' pour l'inverse, vide pour toutes.

    Retourne par écriture : `id` (rowid, celui qu'attend
    dolibarr_set_bank_statement), date d'opération, date de valeur, libellé,
    mode de règlement, `debit`/`credit`, `amount` signé, `balance_after`
    (solde cumulé), numéro de relevé, état de rapprochement, tiers et sources.

    Le solde cumulé est ancré sur le solde renvoyé par Dolibarr, en remontant
    les écritures postérieures à la fenêtre. Il est omis (avec
    `running_balance_note`) si la fenêtre contient des écritures postdatées :
    le solde de référence les exclut, un cumul serait alors faux sans le dire.
    """
    try:
        if not account_id:
            return "Veuillez fournir un account_id (cf. dolibarr_list_bank_accounts)."

        for name, value in (("date_start", date_start), ("date_end", date_end)):
            if value:
                try:
                    _date_str_to_ts(value)
                except ValueError:
                    return f"Format {name} invalide : '{value}'. Attendu : YYYY-MM-DD."

        flags = {"": "", "yes": "(rappro:=:1)", "no": "(rappro:=:0)"}
        wanted = str(reconciled or "").strip().lower()
        if wanted not in flags:
            return f"reconciled invalide : '{reconciled}'. Valeurs acceptées : 'yes', 'no', ou vide."

        today = _today_iso()
        window_start = date_start
        window_defaulted = False
        if not window_start:
            window_start = _shift_days(date_end or today, -BANK_ENTRIES_DEFAULT_WINDOW_DAYS)
            window_defaulted = True

        window = await _bank_lines(
            account_id,
            _sf_and(_bank_date_filter(after=window_start, before=date_end), flags[wanted]),
        )

        window = _unique_rows(window)
        response: dict[str, Any] = {
            "account_id": account_id,
            "date_start": window_start,
            "date_end": date_end or None,
            "sort": "asc" if str(sort).lower().startswith("a") else "desc",
            "matched": len(window),
        }
        if window_defaulted:
            response["date_start_note"] = (
                f"Aucune date de début fournie : fenêtre par défaut de "
                f"{BANK_ENTRIES_DEFAULT_WINDOW_DAYS} jours appliquée."
            )
        if not window:
            response.update(_export_payload({"entries": []}, "entries", limit, cursor))
            return _dumps(response)

        ascending = _sort_bank_lines(window, descending=False)
        last_date = _ts_to_date(ascending[-1].get("dateo"))

        # Ancrage du solde cumulé : Dolibarr ne sait donner que le solde courant
        # (somme des écritures dont dateo <= maintenant). On remonte donc de ce
        # solde en retirant les écritures postérieures à la fenêtre. Impossible
        # si la fenêtre déborde dans le futur : ces écritures-là sont déjà hors
        # du solde de référence.
        balances: dict[int, float] = {}
        running_note: str | None = None
        if wanted:
            running_note = (
                "Solde cumulé non calculé : le filtre `reconciled` retire des écritures de "
                "la fenêtre, un cumul portant sur un sous-ensemble serait faux."
            )
        elif last_date > today:
            running_note = (
                "Solde cumulé non calculé : la fenêtre contient des écritures postdatées, "
                "que le solde de référence Dolibarr exclut."
            )
        else:
            try:
                balance_now = float(
                    _num(await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))) or 0
                )
                after = await _bank_lines(
                    account_id, _bank_date_filter(after=last_date, before=today, strict_after=True)
                )
                cumulative = round(balance_now - _sum_amounts(after), 2)
                for line in reversed(ascending):
                    line_id = _id_int(line.get("id") or line.get("rowid"))
                    if line_id is not None:
                        balances[line_id] = cumulative
                    cumulative = round(cumulative - float(_num(line.get("amount")) or 0), 2)
            except Exception as exc:  # solde indisponible : on rend les lignes sans cumul
                logger.warning("Solde cumulé indisponible pour le compte %s : %s", account_id, exc)
                running_note = f"Solde cumulé non calculé : {_format_error(exc)}"

        ordered = _sort_bank_lines(window, descending=not str(sort).lower().startswith("a"))
        paging = _export_payload({"entries": ordered}, "entries", limit, cursor, offset=offset)
        start = paging["offset"]
        page = paging.pop("entries")
        response.update(paging)

        entries = []
        for raw in page:
            entry = _format_bank_line(raw)
            if entry.get("id") in balances:
                entry["balance_after"] = balances[entry["id"]]
            entries.append(entry)

        identified = [e for e in entries if e.get("id")]
        if with_links and identified:
            resolved = await _gather_limited([
                (lambda e=entry: _bank_line_links(account_id, e["id"])) for entry in identified
            ])
            for entry, links in zip(identified, resolved):
                if isinstance(links, Exception):
                    entry["links_error"] = _format_error(links)
                else:
                    entry.update(links)

        response.update({"count": len(entries), "offset": start, "entries": entries})
        if running_note:
            response["running_balance_note"] = running_note
        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


async def _bank_account_detail(account_id: int) -> dict:
    """Lit un compte ET son solde réel.

    `GET /bankaccounts/{id}` ne renseigne PAS `balance` : seule la route de
    liste appelle `Account::solde(1)`. L'objet rendu porte donc la valeur par
    défaut de la classe, soit 0 : trompeur au possible dans une réponse dont
    l'objet affiché est justement le compte relu. On complète explicitement.
    """
    account = _format_bank_account(await api_get(BANK_ACCOUNT_ENDPOINT.format(account_id=account_id)))
    try:
        account["balance"] = _num(
            await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))
        )
    except Exception as exc:
        logger.warning("Solde indisponible pour le compte %s : %s", account_id, exc)
        account.pop("balance", None)
    return account


async def _bank_line_links(account_id: int, line_id: int) -> dict:
    """Tiers et objets sources d'une écriture bancaire (llx_bank_url)."""
    links = await api_get(
        BANK_LINE_LINKS_ENDPOINT.format(account_id=account_id, line_id=line_id)
    )
    return _format_bank_links(links)


@mcp.tool()
async def dolibarr_get_bank_balance(account_id: int, date: str = "") -> str:
    """Solde d'un compte bancaire, à aujourd'hui ou à une date donnée.

    Sert à confronter un relevé papier au solde Dolibarr sans sommer les lignes
    à la main.

    Paramètres :
    - account_id : ID du compte (obligatoire)
    - date : date d'arrêté (YYYY-MM-DD). Vide = solde courant.

    Dolibarr n'expose que le solde courant (`GET /bankaccounts/{id}/balance`,
    somme des écritures dont la date d'opération est passée). Pour une autre
    date, ce solde est reporté en ajoutant ou retranchant les seules écritures
    situées entre cette date et aujourd'hui : jamais en resommant tout
    l'historique, que l'API ne sait pas agréger.

    Retourne : solde, date d'arrêté, nombre d'écritures prises dans le report
    et la méthode employée.
    """
    try:
        if not account_id:
            return "Veuillez fournir un account_id."
        if date:
            try:
                _date_str_to_ts(date)
            except ValueError:
                return f"Format date invalide : '{date}'. Attendu : YYYY-MM-DD."

        account = await api_get(BANK_ACCOUNT_ENDPOINT.format(account_id=account_id))
        balance_now = float(
            _num(await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))) or 0
        )

        today = _today_iso()
        result: dict[str, Any] = {
            "account_id": account_id,
            "account_label": (account or {}).get("label") if isinstance(account, dict) else None,
            "currency_code": (account or {}).get("currency_code") if isinstance(account, dict) else None,
            "date": date or today,
        }

        if not date or date == today:
            result.update({
                "balance": round(balance_now, 2),
                "method": "solde courant Dolibarr (écritures postdatées exclues)",
                "entries_applied": 0,
            })
            return _dumps(result)

        if date > today:
            ahead = await _bank_lines(
                account_id, _bank_date_filter(after=today, before=date, strict_after=True)
            )
            balance = round(balance_now + _sum_amounts(ahead), 2)
            method = "solde courant + écritures postdatées jusqu'à la date demandée"
            applied = len(ahead)
        else:
            since = await _bank_lines(
                account_id, _bank_date_filter(after=date, before=today, strict_after=True)
            )
            balance = round(balance_now - _sum_amounts(since), 2)
            method = "solde courant − écritures postérieures à la date demandée"
            applied = len(since)

        result.update({"balance": balance, "method": method, "entries_applied": applied})
        return _dumps(result)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_create_bank_entry(
    account_id: int,
    date: str,
    label: str,
    amount: float,
    direction: str,
    payment_type: str = "VIR",
    num_payment: str = "",
    project_id: int = 0,
) -> str:
    """Crée une opération diverse sur un compte bancaire.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - account_id   : ID du compte (obligatoire)
    - date         : date d'opération (YYYY-MM-DD, obligatoire)
    - label        : libellé de l'écriture (obligatoire)
    - amount       : montant, TOUJOURS positif : le sens vient de `direction`
    - direction    : 'credit' = entrée d'argent, 'debit' = sortie
    - payment_type : mode de règlement, code ou id (VIR, CHQ, LIQ, CB, PRE…)
    - num_payment  : numéro de chèque / référence de règlement (optionnel)
    - project_id   : projet à rattacher (optionnel)

    Sens du montant : le piège à ne pas rejouer. Dans llx_bank le montant est
    SIGNÉ : positif = crédit (entrée), négatif = débit (sortie). Le formulaire
    `compta/bank/various_payment/card.php` expose ce signe via un champ `sens`
    dont la valeur 1 vaut Crédit et 0 Débit (vérifié en production le
    31/08/2026, et confirmé par PaymentVarious::create, qui applique
    `sign = ($sens == '0' ? -1 : 1)`). Cet outil ne prend donc jamais un montant
    négatif : il refuse, plutôt que de risquer une double négation muette.

    Écriture posée via `POST /bankaccounts/{id}/lines` : l'API REST expose la
    route, aucun formulaire web n'est sollicité. Nuance à connaître : cette
    route appelle `Account::addline()` et crée une ligne de journal bancaire,
    pas un objet « paiement divers » (llx_payment_various). L'écriture apparaît
    donc au relevé et dans le solde, mais pas dans la liste des paiements
    divers, et elle ne porte pas de ventilation comptable. Le rattachement au
    projet est posé comme lien `llx_bank_url` : le seul emplacement dont
    dispose une ligne de journal, qui n'a pas de champ projet.

    Retourne l'écriture RELUE depuis Dolibarr, avec le solde du compte avant et
    après : c'est la vérification qui rend une erreur de signe visible.
    """
    try:
        if not account_id:
            return "Veuillez fournir un account_id."
        if not label:
            return "Le libellé (label) est obligatoire."
        try:
            date_ts = _date_str_to_ts(date)
        except ValueError:
            return f"Format date invalide : '{date}'. Attendu : YYYY-MM-DD."

        way = str(direction or "").strip().lower()
        if way not in ("credit", "debit"):
            return "direction doit valoir 'credit' (entrée d'argent) ou 'debit' (sortie)."
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return f"Montant invalide : {amount!r}."
        if value <= 0:
            return (
                f"Le montant doit être strictement positif (reçu : {value}). "
                "Le sens de l'écriture se donne par `direction`, jamais par le signe."
            )

        try:
            mode = await _resolve_payment_type(payment_type)
        except ValueError as exc:
            return str(exc)

        signed = value if way == "credit" else -value

        balance_before = _num(
            await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))
        )

        # `date` et `datev` sont déclarés {@type timestamp} et castés en (int)
        # côté Dolibarr : une date ISO y deviendrait l'année seule, donc 1970.
        payload = {
            "date": date_ts,
            "datev": date_ts,
            "type": mode["code"],
            "label": label,
            "amount": signed,
            "cheque_number": num_payment or "",
        }
        line_id = _id_int(
            await api_post(BANK_LINES_ENDPOINT.format(account_id=account_id), payload)
        )
        if not line_id:
            return _dumps({
                "success": False,
                "message": "Dolibarr n'a pas renvoyé d'identifiant d'écriture : rien de fiable à relire.",
            })

        project_warning: str | None = None
        if project_id:
            try:
                project = await api_get(f"projects/{project_id}")
                caption = (project or {}).get("ref") or (project or {}).get("title") \
                    if isinstance(project, dict) else None
            except Exception:
                caption = None
            try:
                # Dolibarr concatène `url` et `url_id` pour bâtir le href, et
                # stocke ses propres liens préfixés de DOL_URL_ROOT. La forme
                # ci-dessous vise donc une instance servie à la racine du
                # domaine ; sur une installation en sous-répertoire le lien
                # cliquable sera faux, mais `url_id` reste exploitable.
                await api_post(
                    BANK_LINE_LINKS_ENDPOINT.format(account_id=account_id, line_id=line_id),
                    {
                        "url_id": project_id,
                        "url": "/projet/card.php?id=",
                        "label": caption or f"Projet {project_id}",
                        "type": "project",
                    },
                )
            except Exception as exc:
                project_warning = (
                    f"Écriture créée (id={line_id}) mais rattachement au projet {project_id} "
                    f"échoué : {_format_error(exc)}"
                )
                logger.warning("dolibarr_create_bank_entry project link failed: %s", exc)

        created = await api_get(BANK_LINE_ENDPOINT.format(line_id=line_id))
        balance_after = _num(
            await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))
        )

        entry = _format_bank_line(created if isinstance(created, dict) else {})
        try:
            entry.update(await _bank_line_links(account_id, line_id))
        except Exception:
            pass

        response: dict[str, Any] = {
            "success": True,
            "entry_id": line_id,
            "direction": way,
            "amount_signed": signed,
            "payment_type": mode["code"],
            "balance_before": balance_before,
            "balance_after": balance_after,
            "balance_delta": (
                round(float(balance_after) - float(balance_before), 2)
                if balance_before is not None and balance_after is not None
                else None
            ),
            "entry": entry,
            "message": (
                f"Écriture {line_id} créée sur le compte {account_id} : "
                f"{'+' if signed > 0 else ''}{signed} ({way})."
            ),
        }
        if project_warning:
            response["project_warning"] = project_warning
        return _dumps({k: v for k, v in response.items() if v is not None})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_bank_entry(account_id: int, entry_id: int) -> str:
    """Supprime une écriture bancaire isolée (opération diverse).

    Confirmation utilisateur requise avant exécution (action destructive).

    Paramètres :
    - account_id : ID du compte (obligatoire)
    - entry_id   : rowid de l'écriture (champ `id` de dolibarr_list_bank_entries)

    Deux refus systématiques, parce que la ligne bancaire n'est pas toujours
    l'objet d'origine :
    1. une écriture RAPPROCHÉE n'est pas supprimée : elle est adossée à un
       relevé, et la retirer désaccorde le rapprochement sans prévenir ;
    2. une écriture PORTANT UN LIEN de règlement (facture client ou
       fournisseur, paiement divers, virement interne, prélèvement…) n'est pas
       supprimée non plus : le règlement, lui, survivrait, orphelin de sa ligne
       de banque. C'est le règlement qu'il faut annuler, depuis Dolibarr.

    Restent donc supprimables les écritures saisies directement, typiquement
    celles créées par dolibarr_create_bank_entry : dont les écritures de test.

    Retourne le solde du compte avant et après : la variation doit être
    exactement l'opposé du montant supprimé.
    """
    try:
        if not account_id or not entry_id:
            return "Veuillez fournir un account_id et un entry_id."

        raw = await api_get(BANK_LINE_ENDPOINT.format(line_id=entry_id))
        line = _format_bank_line(raw if isinstance(raw, dict) else {})
        if not line.get("id"):
            return f"Écriture {entry_id} introuvable."
        if line.get("account_id") != account_id:
            return _dumps({
                "success": False,
                "message": (
                    f"L'écriture {entry_id} appartient au compte {line.get('account_id')}, "
                    f"pas au compte {account_id}. Rien n'a été supprimé."
                ),
            })
        if line.get("reconciled"):
            return _dumps({
                "success": False,
                "entry_id": entry_id,
                "num_releve": line.get("num_releve"),
                "message": (
                    f"L'écriture {entry_id} est rapprochée sur le relevé "
                    f"{line.get('num_releve')} : la supprimer désaccorderait ce relevé. "
                    "La déconsolider d'abord depuis Dolibarr."
                ),
            })

        links = await _bank_line_links(account_id, entry_id)
        sources = links.get("sources") or []
        if sources:
            return _dumps({
                "success": False,
                "entry_id": entry_id,
                "sources": sources,
                "message": (
                    f"L'écriture {entry_id} est rattachée à "
                    + ", ".join(s.get("type_label") or s.get("type") for s in sources)
                    + " : supprimer la ligne laisserait le règlement orphelin. "
                    "Annuler le règlement depuis Dolibarr, ce qui retirera la ligne."
                ),
            })

        balance_before = _num(await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id)))
        await api_delete(
            BANK_ACCOUNT_LINE_ENDPOINT.format(account_id=account_id, line_id=entry_id)
        )
        balance_after = _num(await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id)))

        delta = (
            round(float(balance_after) - float(balance_before), 2)
            if balance_before is not None and balance_after is not None else None
        )
        expected = round(-float(line.get("amount") or 0), 2)
        report: dict[str, Any] = {
            "success": True,
            "entry_id": entry_id,
            "account_id": account_id,
            "deleted_amount": line.get("amount"),
            "balance_before": balance_before,
            "balance_after": balance_after,
            "balance_delta": delta,
            "expected_delta": expected,
            "message": f"Écriture {entry_id} supprimée du compte {account_id}.",
        }
        if delta is not None and abs(delta - expected) > 0.01:
            report["success"] = False
            report["balance_warning"] = (
                f"Le solde a varié de {delta}, alors que la suppression de "
                f"{line.get('amount')} attendait {expected}."
            )
        return _dumps(report)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_set_bank_statement(
    account_id: int,
    entry_ids: list[int],
    num_releve: str,
) -> str:
    """Affecte un numéro de relevé à un lot d'écritures et les marque rapprochées.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - account_id : ID du compte (obligatoire)
    - entry_ids  : rowid des écritures (champ `id` de dolibarr_list_bank_entries)
    - num_releve : numéro de relevé à poser (ex. '2026-08')

    Pourquoi cet outil ne passe pas par l'API REST. Dolibarr 24 n'expose
    AUCUNE route de rapprochement : `PUT /bankaccounts/{id}/lines/{line_id}`
    n'écrit que le libellé (`AccountLine::updateLabel()`), et `num_releve` /
    `rappro` ne sont accessibles que par `AccountLine::update_conciliation()`,
    joignable seulement depuis `compta/bank/bankentries_list.php`. Le repli sur
    le formulaire est donc contraint, et il est explicite : il exige
    DOLIBARR_WEB_LOGIN et DOLIBARR_WEB_PASSWORD dans la configuration du
    connecteur (compte disposant du droit « banque : rapprocher »). Sans eux,
    l'outil refuse : il ne prétend jamais avoir rapproché.

    Garde-fous, dans l'ordre :
    1. le compte doit être rapprochable (`rappro` actif, hors caisse, non clos)
       : sinon échec explicite, sans aucune écriture tentée ;
    2. chaque écriture doit appartenir à ce compte ;
    3. après le POST, chaque écriture est RELUE par l'API REST et son
       `num_releve` comparé à la valeur demandée. Un formulaire qui change de
       forme d'une version à l'autre produit alors un échec constaté, jamais un
       succès silencieux.
    """
    try:
        if not account_id:
            return "Veuillez fournir un account_id."
        ids = [i for i in (_id_int(e) for e in (entry_ids or [])) if i]
        if not ids:
            return "Veuillez fournir au moins un entry_id (champ `id` des écritures)."
        if not str(num_releve or "").strip():
            return "Le numéro de relevé (num_releve) est obligatoire."
        num_releve = str(num_releve).strip()

        account = await _bank_account_detail(account_id)
        if not account.get("reconcilable"):
            return _dumps({
                "success": False,
                "account_id": account_id,
                "rappro": account.get("rappro"),
                "type_label": account.get("type_label"),
                "status_label": account.get("status_label"),
                "message": (
                    f"Le compte {account_id} n'est pas rapprochable "
                    f"(rappro={account.get('rappro')}, type={account.get('type_label')}, "
                    f"statut={account.get('status_label')}). Aucune écriture n'a été touchée. "
                    "Activez la fonction avec dolibarr_toggle_bank_reconciliation."
                ),
            })

        before: dict[int, dict] = {}
        for line_id in ids:
            raw = await api_get(BANK_LINE_ENDPOINT.format(line_id=line_id))
            line = _format_bank_line(raw if isinstance(raw, dict) else {})
            if line.get("account_id") != account_id:
                return _dumps({
                    "success": False,
                    "message": (
                        f"L'écriture {line_id} appartient au compte "
                        f"{line.get('account_id')}, pas au compte {account_id}. "
                        "Aucune écriture n'a été touchée."
                    ),
                })
            before[line_id] = line

        if not WEB_LOGIN or not WEB_PASSWORD:
            return _dumps({
                "success": False,
                "account_id": account_id,
                "entry_ids": ids,
                "num_releve": num_releve,
                "message": (
                    "Rapprochement impossible : Dolibarr 24 n'expose pas de route REST pour "
                    "écrire num_releve / rappro, et les identifiants web ne sont pas configurés. "
                    "Renseignez DOLIBARR_WEB_LOGIN et DOLIBARR_WEB_PASSWORD (compte avec le "
                    "droit « banque : rapprocher ») pour activer ce repli, ou posez le numéro de "
                    "relevé depuis compta/bank/bankentries_list.php?action=reconcile&id="
                    f"{account_id}. Aucune écriture n'a été touchée."
                ),
            })

        await _web_post(
            "compta/bank/bankentries_list.php",
            data=[
                ("action", "reconcile"),
                ("contextpage", "banktransactionlist"),
                ("id", str(account_id)),
                ("search_account", str(account_id)),
                ("num_releve", num_releve),
                ("confirm_reconcile", "1"),
                *[("rowid[]", str(i)) for i in ids],
            ],
            query=f"action=reconcile&contextpage=banktransactionlist&id={account_id}"
                  f"&search_account={account_id}",
        )

        # Vérification : c'est elle qui distingue un rapprochement effectif d'un
        # formulaire qui a répondu 200 sans rien écrire.
        applied, missed = [], []
        for line_id in ids:
            raw = await api_get(BANK_LINE_ENDPOINT.format(line_id=line_id))
            line = _format_bank_line(raw if isinstance(raw, dict) else {})
            record = {
                "entry_id": line_id,
                "num_releve": line.get("num_releve"),
                "reconciled": line.get("reconciled", False),
                "num_releve_before": before[line_id].get("num_releve"),
            }
            if line.get("num_releve") == num_releve and line.get("reconciled"):
                applied.append(record)
            else:
                missed.append(record)

        return _dumps({
            "success": not missed,
            "account_id": account_id,
            "num_releve": num_releve,
            "applied_count": len(applied),
            "applied": applied,
            "not_applied": missed,
            "message": (
                f"{len(applied)}/{len(ids)} écriture(s) rapprochée(s) sur le relevé {num_releve}."
                + (
                    " Les écritures listées dans not_applied ont été relues sans le numéro "
                    "attendu : le formulaire n'a pas abouti (droit « banque : rapprocher » "
                    "manquant, session refusée, ou formulaire modifié par une mise à jour)."
                    if missed else ""
                )
            ),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_toggle_bank_reconciliation(account_id: int, enabled: bool) -> str:
    """Active ou désactive la fonction rapprochement d'un compte bancaire.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - account_id : ID du compte (obligatoire)
    - enabled    : True pour activer le rapprochement, False pour le désactiver

    Le champ visible dans `compta/bank/card.php` s'appelle `norappro` et est
    inversé par rapport à son nom ; la colonne réellement stockée est `rappro`
    (1 = rapprochement actif). Cet outil écrit `rappro` via
    `PUT /bankaccounts/{id}` : `enabled=True` pose bien rappro=1.

    Retourne le compte RELU après écriture, `reconcilable` compris : un compte
    de type caisse ou clos reste non rapprochable même avec rappro=1, et c'est
    visible dans la réponse plutôt que découvert au rapprochement suivant.
    """
    try:
        if not account_id:
            return "Veuillez fournir un account_id."

        before = await _bank_account_detail(account_id)
        target = 1 if enabled else 0
        await api_put(
            BANK_ACCOUNT_ENDPOINT.format(account_id=account_id), {"rappro": target}
        )
        after = await _bank_account_detail(account_id)

        persisted = after.get("rappro") == target
        return _dumps({
            "success": persisted,
            "account_id": account_id,
            "rappro_before": before.get("rappro"),
            "rappro_after": after.get("rappro"),
            "account": after,
            "message": (
                f"Rapprochement {'activé' if enabled else 'désactivé'} sur le compte "
                f"{account_id} ({after.get('label')})."
                if persisted else
                f"Le PUT a répondu sans erreur mais rappro vaut toujours "
                f"{after.get('rappro')} après relecture : écriture non prise en compte "
                "(droit « banque : configurer » manquant ?)."
            ),
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools : Règlements et lettrage
# ---------------------------------------------------------------------------

INVOICE_PAYMENTS_ENDPOINT = "invoices/{invoice_id}/payments"
INVOICE_PAYMENTS_DISTRIBUTED_ENDPOINT = "invoices/paymentsdistributed"
SUPPLIER_INVOICE_PAYMENTS_ENDPOINT = "supplierinvoices/{invoice_id}/payments"
SUPPLIER_INVOICES_ENDPOINT = "supplierinvoices"
SUPPLIER_INVOICE_LINES_ENDPOINT = "supplierinvoices/{invoice_id}/lines"

#: `SupplierInvoices::postLine()` lit ses arguments en propriétés d'objet sans
#: aucun `isset()`. Une clé absente déclenche un warning PHP et arrive en null
#: dans `addline()` : la ligne est créée, mais amputée. On envoie donc toujours
#: le jeu complet, avec des valeurs neutres explicites.
SUPPLIER_LINE_DEFAULTS: dict[str, Any] = {
    "description": "",
    "pu_ht": 0,
    "tva_tx": 0,
    "localtax1_tx": 0,
    "localtax2_tx": 0,
    "qty": 1,
    "fk_product": 0,
    "remise_percent": 0,
    "date_start": "",
    "date_end": "",
    "fk_code_ventilation": 0,
    "info_bits": 0,
    "price_base_type": "HT",
    "product_type": 1,
    "rang": -1,
    "array_options": [],
    "fk_unit": None,
    "origin_id": 0,
    "multicurrency_subprice": 0,
    "ref_supplier": "",
    "special_code": 0,
}


def _format_payment(raw: dict) -> dict:
    """Formate un règlement rendu par `GET /invoices/{id}/payments`.

    `fk_bank_line` est le rowid de l'écriture bancaire : c'est la clé du
    lettrage, celle qui relie une facture à une ligne de relevé.
    Le même tableau porte aussi les avoirs et acomptes imputés, qui n'ont pas
    d'écriture bancaire : d'où `has_bank_line`, pour ne pas les confondre.
    """
    if not isinstance(raw, dict):
        return {}
    bank_line = _id_int(raw.get("fk_bank_line")) or None
    out = {
        "id": _id_int(raw.get("id") or raw.get("rowid")),
        "ref": raw.get("ref"),
        "date": _ts_to_date(raw.get("date")),
        "amount": _num(raw.get("amount")),
        "payment_type": raw.get("type"),
        "num_payment": raw.get("num") or None,
        "kind": raw.get("typeline") or "imputation",
        "bank_line_id": bank_line,
        "has_bank_line": bank_line is not None,
    }
    result = {k: v for k, v in out.items() if v not in (None, "")}
    result["amount"] = _num(raw.get("amount"))
    result["unavailable_fields"] = [] if "amount" in raw else ["amount"]
    return result


async def _payable_invoice(invoice_id: int, supplier: bool) -> dict:
    """Relit une facture avant règlement et vérifie qu'elle peut en recevoir un.

    Dolibarr n'interdit pas, côté API, d'imputer un règlement sur un BROUILLON :
    l'interface web, elle, ne le propose jamais. Un règlement posé là reste
    invisible du suivi et fausse le rapprochement, sans qu'aucune erreur ne
    remonte. On refuse donc en amont.

    Retourne {"error": "…"} si la facture n'est pas réglable, sinon son contexte.
    """
    endpoint = f"supplierinvoices/{invoice_id}" if supplier else f"invoices/{invoice_id}"
    raw = await api_get(endpoint)
    if not isinstance(raw, dict):
        return {"error": f"Facture {invoice_id} introuvable ou réponse inattendue."}

    status = str(raw.get("fk_statut") or raw.get("status") or "")
    label = _format_invoice_status(status, raw.get("paye"))
    if status == "0":
        return {"error": (
            f"La facture {raw.get('ref') or invoice_id} est en brouillon : un règlement "
            "n'y a aucun sens et resterait invisible du suivi. La valider d'abord."
        )}
    return {
        "ref": raw.get("ref"),
        "status_label": label,
        "total_ttc": _num(raw.get("total_ttc")),
        "remaining_before": _num(raw.get("remaintopay")),
    }


def _payment_key(payment: dict) -> tuple:
    """Identité d'un règlement, pour repérer celui qu'un appel vient de créer.

    L'API rend un identifiant de paiement, jamais l'écriture bancaire associée :
    le règlement créé se retrouve par différence avec la liste lue juste avant.
    L'identité ne peut pas se réduire à `bank_line_id`, absent des avoirs et
    acomptes imputés : deux d'entre eux se confondraient.
    """
    return (
        payment.get("ref"),
        payment.get("date"),
        payment.get("amount"),
        payment.get("bank_line_id"),
        payment.get("kind"),
    )


async def _invoice_payments(invoice_id: int, supplier: bool) -> list:
    """Règlements d'une facture, client ou fournisseur, déjà formatés."""
    endpoint = (
        SUPPLIER_INVOICE_PAYMENTS_ENDPOINT if supplier else INVOICE_PAYMENTS_ENDPOINT
    ).format(invoice_id=invoice_id)
    rows = await api_get(endpoint)
    if not isinstance(rows, list):
        raise ValueError("Réponse de paiements invalide : liste attendue.")
    formatted = (_format_payment(r) for r in _unique_rows(rows))
    return [p for p in formatted if p]


@mcp.tool()
async def dolibarr_list_invoice_payments(
    invoice_id: int,
    supplier: bool=False,
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Règlements rattachés à une facture, et écriture bancaire de chacun.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - invoice_id : ID de la facture (obligatoire)
    - supplier   : False (défaut) = facture client, True = facture fournisseur

    Retourne pour chaque règlement : date, montant, mode, numéro, rowid de
    l'écriture bancaire (`bank_line_id`) et, quand cette écriture existe, le
    compte bancaire mouvementé : savoir qu'une facture est payée ne dit pas sur
    quelle ligne de relevé elle a été lettrée.

    Le tableau inclut aussi les avoirs et acomptes imputés sur la facture :
    ils portent `has_bank_line: false`, n'ayant mouvementé aucun compte.
    """
    try:
        if not invoice_id:
            return "Veuillez fournir un invoice_id."

        payments = await _invoice_payments(invoice_id, supplier)

        # Le compte bancaire n'est pas dans la réponse : on le lit sur la ligne.
        for payment in payments:
            line_id = payment.get("bank_line_id")
            if not line_id:
                continue
            try:
                line = await api_get(BANK_LINE_ENDPOINT.format(line_id=line_id))
                if isinstance(line, dict):
                    payment["bank_account_id"] = _id_int(line.get("fk_account"))
                    payment["bank_num_releve"] = line.get("num_releve") or None
                    payment["bank_reconciled"] = bool(_id_int(line.get("rappro")))
            except Exception as exc:
                payment["bank_line_error"] = _format_error(exc)

        total = (round(sum(float(p["amount"]) for p in payments), 2)
                 if all(p.get("amount") is not None for p in payments) else None)
        return _dumps(_export_payload({
            "invoice_id": invoice_id,
            "supplier": supplier,
            "count": len(payments),
            "total_paid": total,
            "payments": payments,
        }, "payments", limit, cursor))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_add_invoice_payment(
    invoice_id: int,
    date: str,
    amount: float,
    payment_type: str,
    account_id: int,
    num_payment: str = "",
    close_if_paid: bool = True,
    comment: str = "",
) -> str:
    """Enregistre un règlement sur une facture client et crée l'écriture bancaire.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - invoice_id   : ID de la facture client (obligatoire, facture validée)
    - date         : date de règlement (YYYY-MM-DD, obligatoire)
    - amount       : montant encaissé, positif (obligatoire)
    - payment_type : mode de règlement, code ou id (VIR, CHQ, LIQ, CB, PRE…)
    - account_id   : compte bancaire crédité (obligatoire, module Banque actif)
    - num_payment  : numéro de chèque / référence de virement (optionnel)
    - close_if_paid: solder la facture si le règlement l'apure (défaut : oui)
    - comment      : note privée portée sur le règlement (optionnel)

    Route employée : `POST /invoices/paymentsdistributed`, et non
    `POST /invoices/{id}/payments`. Cette seconde route ignore tout montant :
    elle règle systématiquement la totalité du reste à payer, ce qui rend tout
    encaissement partiel impossible. La route distribuée est la seule à prendre
    un montant, et elle refuse d'elle-même un montant supérieur au reste dû.

    Retourne le règlement RELU depuis la facture (montant, écriture bancaire
    créée, compte mouvementé) avec le solde bancaire avant et après.
    """
    try:
        if not invoice_id:
            return "Veuillez fournir un invoice_id."
        if not account_id:
            return "Veuillez fournir un account_id (compte bancaire à créditer)."
        try:
            _date_str_to_ts(date)
        except ValueError:
            return f"Format date invalide : '{date}'. Attendu : YYYY-MM-DD."
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return f"Montant invalide : {amount!r}."
        if value <= 0:
            return f"Le montant doit être strictement positif (reçu : {value})."

        try:
            mode = await _resolve_payment_type(payment_type)
        except ValueError as exc:
            return str(exc)

        context = await _payable_invoice(invoice_id, supplier=False)
        if context.get("error"):
            return _dumps({"success": False, "invoice_id": invoice_id, "message": context["error"]})

        balance_before = _num(
            await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))
        )
        known_before = {_payment_key(p) for p in await _invoice_payments(invoice_id, False)}

        payment_id = _id_int(await api_post(INVOICE_PAYMENTS_DISTRIBUTED_ENDPOINT, {
            "arrayofamounts": {
                str(invoice_id): {"amount": f"{value:.2f}", "multicurrency_amount": ""}
            },
            "datepaye": date,
            "paymentid": mode["id"],
            "closepaidinvoices": "yes" if close_if_paid else "no",
            "accountid": account_id,
            "num_payment": num_payment or "",
            "comment": comment or "",
        }))

        return _dumps(await _payment_write_report(
            invoice_id=invoice_id,
            supplier=False,
            payment_id=payment_id,
            account_id=account_id,
            balance_before=balance_before,
            known_before=known_before,
            amount=value,
            context=context,
        ))

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_add_supplier_invoice_payment(
    supplier_invoice_id: int,
    date: str,
    amount: float,
    payment_type: str,
    account_id: int,
    num_payment: str = "",
    close_if_paid: bool = True,
    comment: str = "",
) -> str:
    """Enregistre un règlement sur une facture fournisseur et l'écriture bancaire.

    Confirmation utilisateur requise avant exécution.

    Paramètres : identiques au pendant client, `supplier_invoice_id` remplaçant
    `invoice_id`. Le compte bancaire est DÉBITÉ : l'écriture créée par Dolibarr
    porte un montant négatif, et c'est le comportement attendu.

    Ici la route nominale `POST /supplierinvoices/{id}/payments` accepte bien un
    `amount` : contrairement à son équivalent client. Elle est donc employée
    telle quelle, sans passer par une route distribuée.

    Retourne le règlement RELU depuis la facture, avec le solde bancaire avant
    et après.
    """
    try:
        if not supplier_invoice_id:
            return "Veuillez fournir un supplier_invoice_id."
        if not account_id:
            return "Veuillez fournir un account_id (compte bancaire à débiter)."
        try:
            date_ts = _date_str_to_ts(date)
        except ValueError:
            return f"Format date invalide : '{date}'. Attendu : YYYY-MM-DD."
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return f"Montant invalide : {amount!r}."
        if value <= 0:
            return f"Le montant doit être strictement positif (reçu : {value})."

        try:
            mode = await _resolve_payment_type(payment_type)
        except ValueError as exc:
            return str(exc)

        context = await _payable_invoice(supplier_invoice_id, supplier=True)
        if context.get("error"):
            return _dumps({
                "success": False,
                "supplier_invoice_id": supplier_invoice_id,
                "message": context["error"],
            })

        balance_before = _num(
            await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))
        )
        endpoint = SUPPLIER_INVOICE_PAYMENTS_ENDPOINT.format(invoice_id=supplier_invoice_id)
        known_before = {
            _payment_key(p) for p in await _invoice_payments(supplier_invoice_id, True)
        }

        # `datepaye` est déclaré {@type timestamp} sur la route fournisseur et
        # affecté tel quel à PaiementFourn::datepaye : une date ISO donnerait
        # un règlement daté de 1970.
        payment_id = _id_int(await api_post(endpoint, {
            "datepaye": date_ts,
            "payment_mode_id": mode["id"],
            "closepaidinvoices": "yes" if close_if_paid else "no",
            "accountid": account_id,
            "num_payment": num_payment or "",
            "comment": comment or "",
            "amount": value,
        }))

        return _dumps(await _payment_write_report(
            invoice_id=supplier_invoice_id,
            supplier=True,
            payment_id=payment_id,
            account_id=account_id,
            balance_before=balance_before,
            known_before=known_before,
            amount=value,
            context=context,
        ))

    except Exception as exc:
        return _format_error(exc)


async def _payment_write_report(
    invoice_id: int,
    supplier: bool,
    payment_id: int | None,
    account_id: int,
    balance_before: Any,
    known_before: set,
    amount: float,
    context: dict | None = None,
) -> dict:
    """Relit la facture après règlement et compose le compte rendu d'écriture.

    Le règlement créé est identifié par différence avec les règlements connus
    avant l'appel : l'API rend un id de paiement, pas l'écriture bancaire, et
    c'est cette dernière qui porte le lettrage.
    """
    payments = await _invoice_payments(invoice_id, supplier)
    created = [p for p in payments if _payment_key(p) not in known_before]

    balance_after = _num(await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id)))
    delta = (
        round(float(balance_after) - float(balance_before), 2)
        if balance_before is not None and balance_after is not None
        else None
    )
    expected = round(-amount if supplier else amount, 2)

    bank_entry = None
    if created and created[0].get("bank_line_id"):
        try:
            raw = await api_get(BANK_LINE_ENDPOINT.format(line_id=created[0]["bank_line_id"]))
            bank_entry = _format_bank_line(raw if isinstance(raw, dict) else {})
        except Exception as exc:
            logger.warning("Relecture de l'écriture bancaire impossible : %s", exc)

    report: dict[str, Any] = {
        "success": bool(payment_id),
        "invoice_id": invoice_id,
        "invoice_ref": (context or {}).get("ref"),
        "supplier": supplier,
        "payment_id": payment_id,
        "account_id": account_id,
        "amount_requested": round(amount, 2),
        "balance_before": balance_before,
        "balance_after": balance_after,
        "balance_delta": delta,
        "expected_delta": expected,
        "created_payments": created,
        "bank_entry": bank_entry,
        "total_paid": round(sum(float(p.get("amount") or 0) for p in payments), 2),
    }
    if delta is not None and abs(delta - expected) > 0.01:
        report["balance_warning"] = (
            f"Le solde du compte {account_id} a varié de {delta}, alors que le règlement "
            f"attendait {expected}. À vérifier avant de considérer le lettrage acquis."
        )
    report["message"] = (
        f"Règlement {payment_id} enregistré sur la facture {invoice_id} "
        f"({'fournisseur' if supplier else 'client'}) : {amount} sur le compte {account_id}."
        if payment_id else
        "Dolibarr n'a pas renvoyé d'identifiant de règlement."
    )
    return {k: v for k, v in report.items() if v is not None}


@mcp.tool()
async def dolibarr_create_supplier_invoice(
    thirdparty_id: int,
    ref_supplier: str,
    date: str = "",
    due_date: str = "",
    lines: list[dict] | None = None,
    project_id: int = 0,
    label: str = "",
    note_private: str = "",
) -> str:
    """Crée une facture fournisseur (brouillon) dans Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - thirdparty_id : ID du fournisseur (obligatoire)
    - ref_supplier  : référence de la facture chez le fournisseur (obligatoire)
    - date          : date de facture (YYYY-MM-DD, défaut : aujourd'hui)
    - due_date      : date d'échéance (YYYY-MM-DD, optionnel)
    - lines         : lignes, chacune un dict avec
        - description : libellé (obligatoire)
        - qty         : quantité (défaut 1)
        - pu_ht       : prix unitaire HT (obligatoire ; `subprice` accepté)
        - tva_tx      : taux de TVA en % (défaut 0)
        - product_type: 0 produit, 1 service (défaut 1)
        - fk_product  : ID produit/service Dolibarr (optionnel)
    - project_id    : projet à rattacher (optionnel)
    - label         : libellé de la facture (optionnel)
    - note_private  : note privée (optionnel)

    La facture est créée en BROUILLON : elle reste modifiable et ne prend sa
    référence définitive qu'à la validation, faite depuis Dolibarr.

    Piège encodé ici : `POST /supplierinvoices/{id}/lines` lit ses paramètres en
    propriétés d'objet sans `isset()`. Toute clé omise arrive en null dans
    `addline()` et la ligne est créée amputée, sans erreur. Le connecteur envoie
    donc systématiquement le jeu complet de champs attendus.

    Retourne la facture RELUE (ref provisoire, montants, lignes).
    """
    try:
        if not thirdparty_id:
            return "Le thirdparty_id est obligatoire."
        if not str(ref_supplier or "").strip():
            return "La ref_supplier (référence chez le fournisseur) est obligatoire."
        for name, value in (("date", date), ("due_date", due_date)):
            if value:
                try:
                    _date_str_to_ts(value)
                except ValueError:
                    return f"Format {name} invalide : '{value}'. Attendu : YYYY-MM-DD."

        payload: dict[str, Any] = {
            "socid": thirdparty_id,
            "ref_supplier": str(ref_supplier).strip(),
            "date": date or _today_iso(),
        }
        if due_date:
            payload["date_echeance"] = due_date
        if project_id:
            payload["fk_project"] = project_id
        if label:
            payload["label"] = label
        if note_private:
            payload["note_private"] = note_private

        invoice_id = _id_int(await api_post(SUPPLIER_INVOICES_ENDPOINT, payload))
        if not invoice_id:
            return _dumps({
                "success": False,
                "message": "Dolibarr n'a pas renvoyé d'identifiant de facture fournisseur.",
            })

        lines_added, line_warnings = 0, []
        for i, line in enumerate(lines or []):
            desc = line.get("description") or line.get("desc") or ""
            if not desc:
                line_warnings.append(f"Ligne {i + 1} ignorée : description manquante.")
                continue
            body = dict(SUPPLIER_LINE_DEFAULTS)
            body.update({
                "description": desc,
                "qty": _line_number(line, ("qty",), 1),
                "pu_ht": _line_number(line, ("pu_ht", "subprice", "price"), 0),
                "tva_tx": _line_number(line, ("tva_tx",), 0),
                "remise_percent": _line_number(line, ("remise_percent",), 0),
                "product_type": int(_line_number(line, ("product_type",), 1)),
                "ref_supplier": str(ref_supplier).strip(),
            })
            if line.get("fk_product"):
                body["fk_product"] = line["fk_product"]
            try:
                await api_post(
                    SUPPLIER_INVOICE_LINES_ENDPOINT.format(invoice_id=invoice_id), body
                )
                lines_added += 1
            except Exception as exc:
                line_warnings.append(f"Ligne {i + 1} ({desc[:40]}) refusée : {_format_error(exc)}")

        reread = await api_get(f"{SUPPLIER_INVOICES_ENDPOINT}/{invoice_id}")
        inv = reread if isinstance(reread, dict) else {}
        response: dict[str, Any] = {
            "success": True,
            "supplier_invoice_id": invoice_id,
            "ref": inv.get("ref"),
            "ref_supplier": inv.get("ref_supplier"),
            "thirdparty_id": _id_int(inv.get("socid")),
            "status_label": _format_invoice_status(
                inv.get("fk_statut") or inv.get("status"), inv.get("paye")
            ),
            "total_ht": _num(inv.get("total_ht")),
            "total_ttc": _num(inv.get("total_ttc")),
            "date_invoice": _ts_to_date(inv.get("date") or inv.get("datef")),
            "date_due": _ts_to_date(inv.get("date_echeance") or inv.get("date_lim_reglement")),
            "lines_requested": len(lines or []),
            "lines_added": lines_added,
            "lines": [_format_doc_line(ln) for ln in (inv.get("lines") or [])],
            "message": (
                f"Facture fournisseur {inv.get('ref') or invoice_id} créée en brouillon "
                f"({lines_added} ligne(s)). À valider depuis Dolibarr."
            ),
        }
        if line_warnings:
            response["line_warnings"] = line_warnings
        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools : Facturation client : création, lignes, reprise d'une propale
# ---------------------------------------------------------------------------

INVOICES_ENDPOINT = "invoices"
INVOICE_LINES_ENDPOINT = "invoices/{invoice_id}/lines"
INVOICE_LINE_ENDPOINT = "invoices/{invoice_id}/lines/{line_id}"

#: `Invoices::postLine()` déréférence chacun de ses champs en propriété d'objet
#: sans `isset()` : même piège que côté fournisseur. Une clé absente arrive en
#: null dans `Facture::addline()` : la ligne est créée, mais amputée, et sans
#: erreur. Le jeu ci-dessous reprend l'exemple de charge utile documenté en
#: amont, avec des valeurs neutres explicites.
INVOICE_LINE_DEFAULTS: dict[str, Any] = {
    "desc": "",
    "subprice": 0,
    "qty": 1,
    "tva_tx": 0,
    "localtax1_tx": 0,
    "localtax2_tx": 0,
    "fk_product": 0,
    "remise_percent": 0,
    "date_start": "",
    "date_end": "",
    "fk_code_ventilation": 0,
    "info_bits": 0,
    "fk_remise_except": None,
    "price_base_type": "HT",
    "product_type": 1,
    "rang": -1,
    "special_code": 0,
    "origin": "",
    "origin_id": 0,
    "fk_parent_line": None,
    "fk_fournprice": None,
    "pa_ht": 0,
    "label": "",
    "array_options": [],
    "situation_percent": 100,
    "fk_prev_id": None,
    "fk_unit": None,
}


def _line_number(line: dict, keys: "tuple[str, ...]", default: float) -> float:
    """Lit un champ numérique de ligne parmi plusieurs alias, sinon le défaut.

    Une clé PRÉSENTE mais nulle vaut « absente » : les lignes recopiées d'un
    autre document portent volontiers `tva_tx: None`, et un `float(None)` y
    ferait échouer toute la ligne : donc une facture vide, assortie d'un
    avertissement, là où l'appelant attend une reprise fidèle.
    """
    for key in keys:
        value = line.get(key)
        if value not in (None, ""):
            return float(value)
    return default


def _invoice_line_payload(line: dict) -> "dict[str, Any] | str":
    """Convertit une ligne appelant en charge utile complète, ou rend l'erreur."""
    desc = line.get("description") or line.get("desc") or line.get("label") or ""
    if not desc:
        return "description manquante"
    try:
        qty = _line_number(line, ("qty",), 1)
        subprice = _line_number(line, ("subprice", "pu_ht", "price"), 0)
        tva_tx = _line_number(line, ("tva_tx",), 0)
        remise = _line_number(line, ("remise_percent",), 0)
        product_type = int(_line_number(line, ("product_type",), 1))
    except (TypeError, ValueError) as exc:
        return f"valeur numérique invalide ({exc})"

    payload = dict(INVOICE_LINE_DEFAULTS)
    payload.update({
        "desc": desc,
        "qty": qty,
        "subprice": subprice,
        "tva_tx": tva_tx,
        "remise_percent": remise,
        "product_type": product_type,
    })
    if line.get("fk_product"):
        payload["fk_product"] = line["fk_product"]
    for key in ("date_start", "date_end"):
        if line.get(key):
            payload[key] = line[key]
    return payload


async def _invoice_is_draft(invoice_id: int) -> "tuple[bool, str]":
    """Retourne (is_draft, status_label) pour une facture client.

    Garde-fou des éditions de ligne, comme _proposal_is_draft pour les propales :
    `Facture::addline()` refuse toute ligne hors brouillon, mais l'API répond
    alors par une erreur générique où l'on cherche longtemps la cause.
    """
    try:
        inv = await api_get(f"{INVOICES_ENDPOINT}/{invoice_id}")
        status = (inv.get("fk_statut") or inv.get("status")) if isinstance(inv, dict) else "0"
    except Exception:
        return True, "?"
    return str(status) == "0", _format_invoice_status(status, (inv or {}).get("paye"))


@mcp.tool()
async def dolibarr_create_invoice(
    thirdparty_id: int,
    date: str = "",
    due_date: str = "",
    project_id: int = 0,
    ref_customer: str = "",
    note_public: str = "",
    note_private: str = "",
    lines: list[dict] | None = None,
    invoice_type: int = 0,
    cond_reglement_id: Annotated[int, Field(strict=True, gt=0)] | None = None,
) -> str:
    """Crée une facture client (brouillon), avec ses lignes.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - thirdparty_id : ID du client (obligatoire)
    - date          : date de facture (YYYY-MM-DD, défaut : aujourd'hui)
    - due_date      : échéance (YYYY-MM-DD), envoyée dans date_lim_reglement.
                      Elle prime toujours sur la condition de règlement, héritée
                      ou forcée. Sans elle, Dolibarr calcule l'échéance.
    - project_id    : projet à rattacher (optionnel)
    - ref_customer  : référence côté client, bon de commande… (optionnel)
    - note_public / note_private : notes (optionnel)
    - lines         : lignes, chacune un dict avec
        - description : libellé (obligatoire)
        - qty         : quantité (défaut 1)
        - subprice    : prix unitaire HT (`pu_ht` accepté)
        - tva_tx      : taux de TVA en % (défaut 0)
        - remise_percent : remise en % (défaut 0)
        - product_type : 0 produit, 1 service (défaut 1)
        - fk_product : ID produit/service Dolibarr (optionnel)
        - date_start / date_end : période de prestation (optionnel)
    - invoice_type  : 0 standard, 1 remplacement, 2 avoir, 3 acompte (défaut 0)
    - cond_reglement_id : entier strictement positif, prioritaire sur la condition
      du tiers. Omis ou None : lire le tiers avant création et reprendre sa condition.

    La facture est créée en BROUILLON, avec une ref provisoire `(PROVxxx)` :
    elle reste modifiable, et ne prend sa référence définitive qu'à la
    validation (dolibarr_validate_invoice).

    Retourne la facture RELUE : ref, montants, statut, lignes, et la condition
    envoyée dans payment_terms_applied (cond_reglement_id, code, label,
    source : parametre, tiers ou aucune, error). Sans condition sur le tiers ou
    si sa lecture échoue, aucun identifiant n'est envoyé ; l'erreur est rendue.
    """
    try:
        if not thirdparty_id:
            return "Le thirdparty_id est obligatoire."
        if cond_reglement_id is not None and (
            type(cond_reglement_id) is not int or cond_reglement_id <= 0
        ):
            return _dumps({
                "success": False,
                "error": "cond_reglement_id doit être un entier strictement positif.",
            })
        invalid = _valid_dates(date=date, due_date=due_date)
        if invalid:
            return invalid

        payload: dict[str, Any] = {
            "socid": thirdparty_id,
            "type": invoice_type,
            "date": date or _today_iso(),
        }
        # `date_lim_reglement` est repris par Invoices::post() comme argument
        # $forceduedate de Facture::create() : c'est le seul moyen d'imposer une
        # échéance, prioritaire sur la condition héritée ou forcée ci-dessous.
        if due_date:
            payload["date_lim_reglement"] = due_date
        if project_id:
            payload["fk_project"] = project_id
        if ref_customer:
            payload["ref_client"] = ref_customer
        if note_public:
            payload["note_public"] = note_public
        if note_private:
            payload["note_private"] = note_private

        payment_terms = await _creation_payment_terms(thirdparty_id, cond_reglement_id)
        if payment_terms["cond_reglement_id"] is not None:
            payload["cond_reglement_id"] = payment_terms["cond_reglement_id"]
        invoice_id = _id_int(await api_post(INVOICES_ENDPOINT, payload))
        if not invoice_id:
            return _dumps({
                "success": False,
                "message": "Dolibarr n'a pas renvoyé d'identifiant de facture.",
                "payment_terms_applied": payment_terms,
            })

        added, warnings = await _push_invoice_lines(invoice_id, lines or [])
        report = await _invoice_write_report(
            invoice_id, requested=len(lines or []), added=added, warnings=warnings,
            created=True,
        )
        report["payment_terms_applied"] = payment_terms
        return _dumps(report)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_add_invoice_lines(invoice_id: int, lines: list[dict]) -> str:
    """Ajoute des lignes à une facture client BROUILLON.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - invoice_id : ID de la facture (obligatoire, doit être en brouillon)
    - lines      : mêmes champs que dolibarr_create_invoice

    `Facture::addline()` refuse toute ligne sur une facture validée : l'outil
    vérifie donc le statut AVANT d'écrire, plutôt que de laisser remonter une
    erreur générique ligne par ligne.

    Retourne la facture relue avec ses lignes et ses totaux recalculés.
    """
    try:
        if not invoice_id:
            return "Veuillez fournir un invoice_id."
        if not lines:
            return "La liste lines est vide : aucune ligne à ajouter."

        is_draft, status_label = await _invoice_is_draft(invoice_id)
        if not is_draft:
            return _dumps({
                "success": False,
                "invoice_id": invoice_id,
                "status_label": status_label,
                "message": (
                    f"La facture {invoice_id} est « {status_label} » : Dolibarr verrouille "
                    "ses lignes hors brouillon. La repasser en brouillon depuis l'interface, "
                    "ou créer un avoir."
                ),
            })

        added, warnings = await _push_invoice_lines(invoice_id, lines)
        return _dumps(await _invoice_write_report(
            invoice_id, requested=len(lines), added=added, warnings=warnings, created=False,
        ))

    except Exception as exc:
        return _format_error(exc)


async def _push_invoice_lines(invoice_id: int, lines: list) -> "tuple[int, list[str]]":
    """Pousse un lot de lignes sur une facture. Retourne (posées, avertissements)."""
    added, warnings = 0, []
    for i, line in enumerate(lines):
        payload = _invoice_line_payload(line if isinstance(line, dict) else {})
        if isinstance(payload, str):
            warnings.append(f"Ligne {i + 1} ignorée : {payload}.")
            continue
        try:
            await api_post(INVOICE_LINES_ENDPOINT.format(invoice_id=invoice_id), payload)
            added += 1
        except Exception as exc:
            warnings.append(
                f"Ligne {i + 1} ({str(payload['desc'])[:40]}) refusée : {_format_error(exc)}"
            )
    return added, warnings


async def _invoice_write_report(
    invoice_id: int, requested: int, added: int, warnings: list, created: bool
) -> dict:
    """Relit une facture après écriture et compose le compte rendu."""
    raw = await api_get(f"{INVOICES_ENDPOINT}/{invoice_id}")
    inv = raw if isinstance(raw, dict) else {}
    report: dict[str, Any] = {
        "success": True,
        "invoice_id": invoice_id,
        "ref": inv.get("ref"),
        "thirdparty_id": _id_int(inv.get("socid")),
        "status_label": _format_invoice_status(
            inv.get("fk_statut") or inv.get("status"), inv.get("paye")
        ),
        "total_ht": _num(inv.get("total_ht")),
        "total_tva": _num(inv.get("total_tva")),
        "total_ttc": _num(inv.get("total_ttc")),
        "date_invoice": _ts_to_date(inv.get("date")),
        "date_due": _ts_to_date(inv.get("date_lim_reglement")),
        "fk_project": _id_int(inv.get("fk_project")),
        "lines_requested": requested,
        "lines_added": added,
        "lines": [_format_doc_line(ln) for ln in (inv.get("lines") or [])],
        "message": (
            f"Facture {inv.get('ref') or invoice_id} "
            f"{'créée en brouillon' if created else 'complétée'} ({added} ligne(s))."
            + (" À valider avec dolibarr_validate_invoice." if created else "")
        ),
    }
    if warnings:
        report["line_warnings"] = warnings
    return report


@mcp.tool()
async def dolibarr_delete_invoice_line(invoice_id: int, line_id: int) -> str:
    """Supprime une ligne d'une facture client BROUILLON.

    Confirmation utilisateur requise avant exécution (action destructive).

    Paramètres :
    - invoice_id : ID de la facture (obligatoire, doit être en brouillon)
    - line_id    : ID de la ligne (champ `line_id` de dolibarr_get_invoice)

    Pendant facture de dolibarr_delete_proposal_line, avec la même précaution :
    hors brouillon, Dolibarr verrouille les lignes et l'appel n'a aucun effet.

    Retourne la facture relue, avec ses totaux recalculés.
    """
    try:
        if not invoice_id or not line_id:
            return "Veuillez fournir un invoice_id et un line_id."

        is_draft, status_label = await _invoice_is_draft(invoice_id)
        if not is_draft:
            return _dumps({
                "success": False,
                "invoice_id": invoice_id,
                "status_label": status_label,
                "message": (
                    f"La facture {invoice_id} est « {status_label} » : ses lignes sont "
                    "verrouillées. Aucune suppression tentée."
                ),
            })

        before = await api_get(f"{INVOICES_ENDPOINT}/{invoice_id}")
        count_before = len((before or {}).get("lines") or []) if isinstance(before, dict) else None

        await api_delete(INVOICE_LINE_ENDPOINT.format(invoice_id=invoice_id, line_id=line_id))

        report = await _invoice_write_report(
            invoice_id, requested=0, added=0, warnings=[], created=False
        )
        count_after = len(report.get("lines") or [])
        report["lines_before"] = count_before
        report["lines_after"] = count_after
        report["success"] = count_before is None or count_after < count_before
        report["message"] = (
            f"Ligne {line_id} supprimée de la facture {report.get('ref') or invoice_id}."
            if report["success"] else
            f"L'appel a répondu sans erreur mais la facture compte toujours "
            f"{count_after} ligne(s) : la suppression n'a pas pris."
        )
        return _dumps(report)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_delete_invoice(invoice_id: int, supplier: bool = False) -> str:
    """Supprime une facture BROUILLON, client ou fournisseur.

    Confirmation utilisateur requise avant exécution (action destructive).

    Paramètres :
    - invoice_id : ID de la facture (obligatoire)
    - supplier   : False (défaut) = facture client, True = facture fournisseur

    Seuls les BROUILLONS sont supprimables, et c'est délibéré : une facture
    validée a consommé un numéro de la séquence comptable, que sa suppression
    laisserait manquant. Une facture validée se neutralise par un avoir, ou par
    une mise en abandon depuis Dolibarr.

    Retourne la confirmation, et vérifie par relecture que l'objet a bien
    disparu : un DELETE accepté sans effet est signalé comme un échec.
    """
    try:
        if not invoice_id:
            return "Veuillez fournir un invoice_id."

        base = SUPPLIER_INVOICES_ENDPOINT if supplier else INVOICES_ENDPOINT
        raw = await api_get(f"{base}/{invoice_id}")
        if not isinstance(raw, dict) or not raw.get("id"):
            return f"Facture {invoice_id} introuvable."

        status = str(raw.get("fk_statut") or raw.get("status") or "")
        label = _format_invoice_status(status, raw.get("paye"))
        ref = raw.get("ref")
        if status != "0":
            return _dumps({
                "success": False,
                "invoice_id": invoice_id,
                "ref": ref,
                "status_label": label,
                "message": (
                    f"La facture {ref or invoice_id} est « {label} » : elle a consommé un "
                    "numéro de séquence, que sa suppression laisserait manquant. "
                    "La neutraliser par un avoir, ou l'abandonner depuis Dolibarr."
                ),
            })

        await api_delete(f"{base}/{invoice_id}")

        gone = True
        try:
            again = await api_get(f"{base}/{invoice_id}")
            gone = not (isinstance(again, dict) and again.get("id"))
        except httpx.HTTPStatusError as exc:
            gone = exc.response.status_code == 404

        return _dumps({
            "success": gone,
            "invoice_id": invoice_id,
            "ref": ref,
            "supplier": supplier,
            "message": (
                f"Facture {ref or invoice_id} supprimée." if gone else
                f"Le DELETE a répondu sans erreur mais la facture {ref or invoice_id} "
                "est toujours lisible : suppression non effective."
            ),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_validate_supplier_invoice(supplier_invoice_id: int) -> str:
    """Valide une facture fournisseur brouillon (statut 0 → 1).

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - supplier_invoice_id : ID de la facture fournisseur (obligatoire)

    Pendant fournisseur de dolibarr_validate_invoice, qui manquait : le cycle
    fournisseur s'arrêtait au brouillon et imposait un passage par l'interface.
    La validation remplace la référence provisoire `(PROVxxx)` par la référence
    définitive et rend la facture réglable.

    Retourne la facture RELUE : nouvelle ref, statut, montants.
    """
    try:
        if not supplier_invoice_id:
            return "Veuillez fournir un supplier_invoice_id."

        await api_post(f"{SUPPLIER_INVOICES_ENDPOINT}/{supplier_invoice_id}/validate", {})

        raw = await api_get(f"{SUPPLIER_INVOICES_ENDPOINT}/{supplier_invoice_id}")
        inv = raw if isinstance(raw, dict) else {}
        status = str(inv.get("fk_statut") or inv.get("status") or "")
        return _dumps({
            "success": status != "0",
            "supplier_invoice_id": supplier_invoice_id,
            "ref": inv.get("ref"),
            "ref_supplier": inv.get("ref_supplier"),
            "status_label": _format_invoice_status(status, inv.get("paye")),
            "total_ht": _num(inv.get("total_ht")),
            "total_ttc": _num(inv.get("total_ttc")),
            "message": (
                f"Facture fournisseur validée : {inv.get('ref')}." if status != "0" else
                "L'appel a répondu sans erreur mais la facture est toujours en brouillon."
            ),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_create_invoice_from_proposal(
    proposal_id: int,
    date: str = "",
    due_date: str = "",
    mark_proposal_invoiced: bool = False,
    cond_reglement_id: Annotated[int, Field(strict=True, gt=0)] | None = None,
) -> str:
    """Crée une facture client en reprenant une proposition commerciale.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - proposal_id : ID de la proposition à facturer (obligatoire)
    - date        : date de facture (YYYY-MM-DD, défaut : aujourd'hui)
    - due_date    : échéance (YYYY-MM-DD, optionnel), envoyée dans date_lim_reglement.
      Elle prime toujours sur la condition de règlement, héritée ou forcée.
    - mark_proposal_invoiced : passer la propale au statut « facturée »
      (défaut : non). L'interface Dolibarr le fait à la facturation ; l'outil
      ne touche pas au statut sans demande explicite.
    - cond_reglement_id : entier strictement positif, prioritaire. Omis ou None :
      reprendre la condition du devis, sinon lire celle du tiers avant création.

    Dolibarr n'expose de reprise que depuis une COMMANDE ou un CONTRAT
    (`POST /invoices/createfromorder`, `/createfromcontract`) : rien pour une
    proposition. Le connecteur relit donc la propale et recopie tiers, projet,
    référence client et lignes une à une. Conséquence à connaître : les lignes
    sont recréées, pas déplacées, et la facture est liée à la propale via
    `linked_objects` : le lien remonte dans l'onglet « Éléments liés ».

    Retourne la facture créée (brouillon), relue avec ses lignes, et la condition
    envoyée dans payment_terms_applied (cond_reglement_id, code, label,
    source : parametre, devis, tiers ou aucune, error). Sans condition ou si la
    lecture du tiers échoue, aucun identifiant n'est envoyé ; l'erreur est rendue.
    """
    try:
        if not proposal_id:
            return "Veuillez fournir un proposal_id."
        if cond_reglement_id is not None and (
            type(cond_reglement_id) is not int or cond_reglement_id <= 0
        ):
            return _dumps({
                "success": False,
                "error": "cond_reglement_id doit être un entier strictement positif.",
            })
        invalid = _valid_dates(date=date, due_date=due_date)
        if invalid:
            return invalid

        raw = await api_get(f"proposals/{proposal_id}")
        if not isinstance(raw, dict) or not raw.get("id"):
            return f"Proposition {proposal_id} introuvable."

        source_lines = raw.get("lines") or []
        if not source_lines:
            return _dumps({
                "success": False,
                "proposal_id": proposal_id,
                "message": (
                    f"La proposition {raw.get('ref') or proposal_id} n'a aucune ligne : "
                    "il n'y a rien à facturer."
                ),
            })

        payload: dict[str, Any] = {
            "socid": _id_int(raw.get("socid")),
            "type": 0,
            "date": date or _today_iso(),
            # Lien 'propal' → Facture::create() appelle add_object_linked() et
            # la facture apparaît dans les éléments liés de la proposition.
            "linked_objects": {"propal": proposal_id},
        }
        if due_date:
            payload["date_lim_reglement"] = due_date
        if raw.get("fk_project"):
            payload["fk_project"] = _id_int(raw.get("fk_project"))
        if raw.get("ref_client"):
            payload["ref_client"] = raw.get("ref_client")

        payment_terms = await _creation_payment_terms(payload["socid"], cond_reglement_id, raw)
        if payment_terms["cond_reglement_id"] is not None:
            payload["cond_reglement_id"] = payment_terms["cond_reglement_id"]
        invoice_id = _id_int(await api_post(INVOICES_ENDPOINT, payload))
        if not invoice_id:
            return _dumps({
                "success": False,
                "message": "Dolibarr n'a pas renvoyé d'identifiant de facture.",
                "payment_terms_applied": payment_terms,
            })

        lines = [
            {
                "description": ln.get("desc") or ln.get("description") or ln.get("label") or "",
                "qty": ln.get("qty"),
                "subprice": ln.get("subprice"),
                "tva_tx": ln.get("tva_tx"),
                "remise_percent": ln.get("remise_percent"),
                "product_type": ln.get("product_type", 1),
                "fk_product": ln.get("fk_product"),
                "date_start": _ts_to_date(ln.get("date_start")),
                "date_end": _ts_to_date(ln.get("date_end")),
            }
            for ln in source_lines
            if isinstance(ln, dict)
        ]
        added, warnings = await _push_invoice_lines(invoice_id, lines)

        report = await _invoice_write_report(
            invoice_id, requested=len(lines), added=added, warnings=warnings, created=True,
        )
        report["payment_terms_applied"] = payment_terms
        report["proposal_id"] = proposal_id
        report["proposal_ref"] = raw.get("ref")
        report["proposal_total_ht"] = _num(raw.get("total_ht"))

        if added != len(lines):
            report["success"] = False
            report["message"] = (
                f"Facture {report.get('ref') or invoice_id} créée mais INCOMPLÈTE : "
                f"{added}/{len(lines)} ligne(s) reprises de {raw.get('ref')}. "
                "Compléter ou supprimer le brouillon avant de valider."
            )
        elif report.get("total_ht") != report.get("proposal_total_ht"):
            report["total_warning"] = (
                f"Total HT facture ({report.get('total_ht')}) différent du total de la "
                f"proposition ({report.get('proposal_total_ht')}) : remises ou arrondis à vérifier."
            )

        if mark_proposal_invoiced:
            try:
                await api_post(f"proposals/{proposal_id}/setinvoiced", {})
                report["proposal_status"] = "facturée"
            except Exception as exc:
                report["proposal_status_warning"] = (
                    f"Facture créée, mais le passage de la proposition en « facturée » "
                    f"a échoué : {_format_error(exc)}"
                )

        return _dumps(report)

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools : Pièces jointes et génération de PDF
# ---------------------------------------------------------------------------

#: `fetchObjectByElement()`, qui résout l'objet destinataire côté Dolibarr,
#: raisonne en NOMS D'ÉLÉMENT internes, pas en noms de module REST. Les alias
#: lisibles annoncés dans la documentation de l'API ('supplier_invoice') n'y
#: sont pas reconnus et produisent un 404 : vérifié en production le
#: 31/08/2026 sur SI2603-0085, facture fournisseur pourtant validée.
DOCUMENT_MODULE_ALIASES = {
    "supplier_invoice": "facture_fournisseur",
    "supplier_order": "commande_fournisseur",
    "invoice": "facture",
    "proposal": "propal",
    "order": "commande",
    "thirdparty": "societe",
    "member": "adherent",
    "project_task": "task",
}

DOCUMENTS_ENDPOINT = "documents"
DOCUMENTS_UPLOAD_ENDPOINT = "documents/upload"
DOCUMENTS_BUILDDOC_ENDPOINT = "documents/builddoc"

#: Taille maximale d'une pièce jointe envoyée par le connecteur. Le contenu
#: transite en base64 dans un corps JSON : au-delà, c'est la mémoire de PHP et
#: la limite `post_max_size` du serveur qui décident, avec un message opaque.
DOCUMENT_UPLOAD_MAX_BYTES = 12 * 1024 * 1024


@mcp.tool()
async def dolibarr_list_documents(
    module: str,
    object_id: int=0,
    ref: str='',
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les pièces jointes d'un objet Dolibarr.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - module    : type d'objet : 'invoice', 'supplier_invoice', 'proposal',
                  'order', 'project', 'project_task', 'thirdparty', 'member'…
    - object_id : ID de l'objet (fournir object_id OU ref)
    - ref       : référence de l'objet (ex. 'FA2608-0939')
    - limit     : nombre max de fichiers

    Seuls les modulepart que l'API documents du cœur Dolibarr connaît sont
    servis : leur liste y est codée en dur. Les modules maison n'en font pas
    partie, en particulier « meetingnotes » (comptes rendus), qui n'est pas
    supporté tant que ce modulepart n'existe pas côté Dolibarr : l'API répond
    500 « Modulepart meetingnotes not implemented yet ». Pour les comptes
    rendus, utiliser dolibarr_list_meetingnote_documents (extension inno³,
    module meetingnotes 4.45.0-alpha.77 ou plus récent).

    Retourne nom, chemin relatif, taille, type et date de chaque fichier. Le
    chemin relatif est celui qu'attend dolibarr_build_document.
    """
    try:
        if not module:
            return "Le paramètre module est obligatoire (ex. 'invoice', 'supplier_invoice')."
        if not object_id and not ref:
            return "Veuillez fournir object_id ou ref."

        params: dict[str, Any] = {
            "modulepart": _document_module(module),
        }
        # `id` est plus sûr que `ref` : il évite la résolution par référence, qui
        # échoue sur les brouillons dont la ref porte des parenthèses.
        if object_id:
            params["id"] = object_id
        elif ref:
            params["ref"] = _document_ref(ref)

        try:
            rows = await api_get(DOCUMENTS_ENDPOINT, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # 404 ici veut dire « objet introuvable », pas « aucun fichier ».
                # Les confondre ferait passer une erreur d'adressage pour un
                # dossier vide.
                return _dumps({
                    "success": False,
                    "module": module,
                    "modulepart_sent": params["modulepart"],
                    "object_id": object_id or None,
                    "ref": ref or None,
                    "message": (
                        f"Objet introuvable pour modulepart={params['modulepart']!r}. "
                        f"Détail Dolibarr : {_error_body(exc)}"
                    ),
                })
            if exc.response.status_code == 500 and "not implemented" in str(_error_body(exc)):
                # Le cœur ne connaît pas ce modulepart : ce n'est ni un objet
                # absent ni une panne, c'est une capacité manquante de l'API.
                return _dumps({
                    "success": False,
                    "module": module,
                    "modulepart_sent": params["modulepart"],
                    "unsupported_modulepart": True,
                    "message": (
                        f"L'API documents de Dolibarr ne sait pas servir "
                        f"modulepart={params['modulepart']!r}. Un module maison peut exposer "
                        "sa propre route : pour les comptes rendus, "
                        "dolibarr_list_meetingnote_documents. "
                        f"Détail Dolibarr : {_error_body(exc)}"
                    ),
                })
            raise
        documents = [
            {
                "name": row.get("name"),
                "relative_path": row.get("relativename") or row.get("name"),
                "size": _num(row.get("size")),
                "type": row.get("type"),
                "date": _ts_to_date(row.get("date")),
            }
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        ]
        return _dumps(_export_payload({
            "success": True,
            "module": module,
            "modulepart_sent": params["modulepart"],
            "object_id": object_id or None,
            "ref": ref or None,
            "count": len(documents),
            "documents": documents,
        }, "documents", limit, cursor, identity="relative_path"))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_upload_document(
    file_path: str,
    module: str,
    ref: str = "",
    subdir: str = "",
    overwrite: bool = False,
) -> str:
    """Attache un fichier local à un objet Dolibarr.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - file_path : chemin du fichier SUR LA MACHINE qui exécute le connecteur
    - module    : type d'objet : 'invoice', 'supplier_invoice', 'proposal',
                  'order', 'project', 'project_task', 'expensereport'…
    - ref       : référence de l'objet destinataire (ex. 'SI2608-0012'). C'est
                  elle qui détermine le sous-dossier de stockage.
    - subdir    : sous-dossier explicite, à la place de ref (rarement utile)
    - overwrite : écraser un fichier de même nom (défaut : non)

    Usage principal : joindre le PDF reçu du fournisseur à la facture
    fournisseur saisie par dolibarr_create_supplier_invoice.

    Le fichier est lu localement et transmis en base64. Taille plafonnée à
    12 Mo : au-delà, c'est `post_max_size` côté serveur qui refuse, avec un
    message que rien ne rattache au fichier envoyé.

    Retourne le nom stocké et le contenu de la liste des pièces jointes après
    envoi : la vérification que le fichier est bien arrivé.
    """
    try:
        import base64
        import os.path

        if not module:
            return "Le paramètre module est obligatoire."
        if not ref and not subdir:
            return "Veuillez fournir ref (référence de l'objet) ou subdir."
        if not os.path.isfile(file_path):
            return f"Fichier introuvable : {file_path}"

        size = os.path.getsize(file_path)
        if size > DOCUMENT_UPLOAD_MAX_BYTES:
            return (
                f"Fichier trop volumineux ({round(size / 1048576, 1)} Mo) : la limite du "
                f"connecteur est {DOCUMENT_UPLOAD_MAX_BYTES // 1048576} Mo. Déposer le "
                "fichier directement depuis l'interface Dolibarr."
            )

        with open(file_path, "rb") as handle:
            content = base64.b64encode(handle.read()).decode("ascii")

        filename = os.path.basename(file_path)
        stored_ref = _document_ref(ref)
        await api_post(DOCUMENTS_UPLOAD_ENDPOINT, {
            "filename": filename,
            "modulepart": _document_module(module),
            "ref": stored_ref,
            "subdir": subdir,
            "filecontent": content,
            "fileencoding": "base64",
            "overwriteifexists": 1 if overwrite else 0,
        })

        # Relecture : c'est elle qui distingue un dépôt réel d'un POST accepté
        # sans effet. Elle peut échouer sans que le dépôt ait échoué : on le dit
        # alors, plutôt que de rendre un `documents_after` nul et muet.
        listed, check_note = None, None
        if stored_ref:
            try:
                after = json.loads(
                    await dolibarr_list_documents(module=module, ref=stored_ref)
                )
                listed = after.get("documents")
                if listed is None:
                    check_note = after.get("message")
                elif not any(d.get("name") == filename for d in listed):
                    check_note = (
                        f"{filename} n'apparaît pas dans les pièces jointes relues : "
                        "dépôt à vérifier depuis Dolibarr."
                    )
            except Exception as exc:
                check_note = f"Relecture des pièces jointes impossible : {_format_error(exc)}"

        response = {
            "success": True,
            "filename": filename,
            "size": size,
            "module": module,
            "ref": stored_ref or None,
            "ref_requested": ref or None,
            "documents_after": listed,
            "message": f"{filename} ({round(size / 1024)} Ko) attaché à {stored_ref or subdir}.",
        }
        if check_note:
            response["verification_warning"] = check_note
        return _dumps({k: v for k, v in response.items() if v is not None})

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_build_document(
    module: str,
    ref: str,
    original_file: str = "",
    template: str = "",
    langcode: str = "",
) -> str:
    """(Re)génère le PDF d'un document commercial Dolibarr.

    Confirmation utilisateur requise avant exécution (écrase le PDF existant).

    Paramètres :
    - module        : 'invoice', 'supplier_invoice', 'proposal', 'order', 'contract'…
    - ref           : référence du document (ex. 'FA2608-0939')
    - original_file : chemin relatif du fichier, si différent de `<ref>/<ref>.pdf`
    - template      : modèle de document (ex. 'crabe'). Vide = modèle par défaut.
    - langcode      : langue du document (ex. 'fr_FR'). Vide = langue par défaut.

    Utile après dolibarr_validate_invoice : la validation renomme la facture
    mais le PDF n'est régénéré que par l'interface. Sans cette régénération, le
    PDF servi reste celui du brouillon, sous l'ancienne référence.

    Le contenu binaire renvoyé par Dolibarr est VOLONTAIREMENT écarté de la
    réponse : un PDF encodé en base64 saturerait la fenêtre de contexte pour
    rien. Seuls le nom, le type et la taille sont rendus ; le fichier se
    récupère par dolibarr_get_invoice_pdf_url.
    """
    try:
        if not module or not ref:
            return "Les paramètres module et ref sont obligatoires."

        stored_ref = _document_ref(ref)
        path = original_file or f"{stored_ref}/{stored_ref}.pdf"
        payload: dict[str, Any] = {
            "modulepart": _document_module(module),
            "original_file": path,
        }
        if template:
            payload["doctemplate"] = template
        if langcode:
            payload["langcode"] = langcode

        result = await api_put(DOCUMENTS_BUILDDOC_ENDPOINT, payload)
        built = result if isinstance(result, dict) else {}
        return _dumps({
            "success": True,
            "module": module,
            "ref": ref,
            "filename": built.get("filename"),
            "content_type": built.get("content-type"),
            "filesize": _num(built.get("filesize")),
            "template": built.get("template") or template or "(défaut)",
            "langcode": built.get("langcode") or langcode or "(défaut)",
            "message": f"PDF régénéré pour {ref} ({built.get('filename') or path}).",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools : Référentiels (utilisateurs, produits, modes de règlement)
# ---------------------------------------------------------------------------

USERS_ENDPOINT = "users"
PRODUCTS_ENDPOINT = "products"


@mcp.tool()
async def dolibarr_list_users(
    search: str='',
    include_disabled: bool=False,
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les utilisateurs Dolibarr (identifiants internes).

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - search : filtre sur login, nom ou prénom (recherche partielle)
    - include_disabled : inclure les comptes désactivés (défaut : non)
    - limit : nombre max de résultats

    Sert à récupérer le `user_id` qu'attendent dolibarr_log_time,
    dolibarr_assign_task_user et dolibarr_list_time_entries : ces outils
    demandent un identifiant numérique que rien n'exposait jusqu'ici.

    Retourne : id, login, nom, prénom, email, statut, et si le compte est
    employé.
    """
    try:
        params: dict[str, Any] = {
            "sortfield": "t.lastname",
            "sortorder": "ASC",
            "limit": CLIENT_FILTER_SCAN_LIMIT if search else _clamp_limit(limit),
        }
        if not include_disabled:
            params["sqlfilters"] = "(t.statut:=:1)"

        rows = await _scan_list(USERS_ENDPOINT, params)
        needle = search.strip().lower()
        results = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            user = {
                "id": _id_int(row.get("id") or row.get("rowid")),
                "login": row.get("login"),
                "lastname": row.get("lastname"),
                "firstname": row.get("firstname"),
                "email": row.get("email"),
                "job": row.get("job") or None,
                "enabled": str(row.get("statut") or row.get("status") or "1") == "1",
                "employee": str(row.get("employee") or "0") == "1",
            }
            if needle:
                haystack = " ".join(
                    str(user.get(k) or "") for k in ("login", "lastname", "firstname", "email")
                ).lower()
                if needle not in haystack:
                    continue
            results.append({k: v for k, v in user.items() if v not in (None, "")})

        return _dumps(_export_payload({"count": len(results), "users": results}, "users", limit, cursor, scan=rows))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_list_products(
    search: str='',
    kind: str='',
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les produits et services du catalogue Dolibarr.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - search : filtre sur la référence ou le libellé (recherche partielle)
    - kind   : 'product' (biens), 'service' (prestations), vide = les deux
    - limit  : nombre max de résultats

    Sert à récupérer le `fk_product` des lignes de facture ou de proposition,
    et le prix catalogue associé.

    Retourne : id, ref, libellé, type, prix de vente HT, taux de TVA, statut
    de vente.
    """
    try:
        kinds = {"": 0, "product": 1, "service": 2, "produit": 1, "prestation": 2}
        wanted = str(kind or "").strip().lower()
        if wanted not in kinds:
            return f"kind invalide : '{kind}'. Valeurs acceptées : 'product', 'service', ou vide."

        params: dict[str, Any] = {
            "sortfield": "t.ref",
            "sortorder": "ASC",
            "limit": _clamp_limit(limit),
            "mode": kinds[wanted],
        }
        if search:
            needle = _sf_like(search)
            params["sqlfilters"] = f"((t.ref:like:'%{needle}%') or (t.label:like:'%{needle}%'))"

        rows = await _scan_list(PRODUCTS_ENDPOINT, params)
        results = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            results.append({k: v for k, v in {
                "id": _id_int(row.get("id") or row.get("rowid")),
                "ref": row.get("ref"),
                "label": row.get("label"),
                "type": _line_type_label(row.get("type")),
                "price_ht": _num(row.get("price")),
                "price_ttc": _num(row.get("price_ttc")),
                "tva_tx": _num(row.get("tva_tx")),
                "on_sell": str(row.get("status") or "0") == "1",
            }.items() if v not in (None, "")})

        return _dumps(_export_payload({"count": len(results), "kind": wanted or "tous", "products": results}, "products", limit, cursor, scan=rows))

    except Exception as exc:
        return _format_error(exc)

@mcp.tool()
async def dolibarr_list_payment_types(
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Liste les modes de règlement actifs de l'instance (dictionnaire c_paiement).

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    limit et cursor paginent les résultats. Retourne pour chacun son code ('VIR', 'CHQ', 'LIQ', 'CB',
    'PRE'…), son identifiant numérique et son libellé.

    Les deux formes servent : les outils de règlement attendent l'identifiant,
    les écritures bancaires attendent le code. Le connecteur accepte l'une ou
    l'autre partout et convertit : cet outil sert à savoir ce qui existe sur
    l'instance, dont la liste est configurable.
    """
    try:
        types = await _payment_types()
        rows = sorted(types["by_code"].values(), key=lambda t: t["code"])
        return _dumps(_export_payload({"count": len(rows), "payment_types": rows}, "payment_types", limit, cursor))

    except Exception as exc:
        return _format_error(exc)

# ---------------------------------------------------------------------------
# Tools : Trésorerie : prévisionnel, virements internes, relevés
# ---------------------------------------------------------------------------

BANK_TRANSFER_ENDPOINT = "bankaccounts/transfer"

#: Fenêtre remontée par défaut pour recenser les relevés existants.
BANK_STATEMENTS_DEFAULT_WINDOW_DAYS = 365


@mcp.tool()
async def dolibarr_cash_forecast(horizon_days: int = 90, include_supplier: bool = True) -> str:
    """Prévisionnel de trésorerie : soldes, encaissements et décaissements attendus.

    Paramètres :
    - horizon_days : profondeur du prévisionnel en jours (défaut 90)
    - include_supplier : intégrer les factures fournisseur à payer (défaut oui)

    Assemble en une réponse ce qu'il fallait jusqu'ici recouper à la main :
    - le solde de chaque compte ouvert hors caisse, et leur total ;
    - les factures clients validées non payées, avec leur reste à encaisser ;
    - les factures fournisseur validées non payées, avec leur reste à payer ;
    - une ventilation par échéance : en retard, 0-30 j, 31-60 j, 61-90 j, au-delà ;
    - le solde projeté à l'horizon.

    Le solde projeté est une PROJECTION D'ÉCHÉANCES, pas un engagement : il
    suppose que chaque facture est réglée à sa date d'échéance. Les factures
    déjà en retard y sont comptées à l'échéance dépassée, donc immédiatement.

    Le reste à payer vient de `remaintopay` quand Dolibarr le fournit, et du
    total TTC sinon : un règlement partiel non reflété gonflerait la prévision.
    """
    try:
        from datetime import datetime

        horizon = max(1, int(horizon_days))
        today = _today_iso()
        horizon_date = _shift_days(today, horizon)

        accounts = await api_get(BANK_ACCOUNTS_ENDPOINT, {"limit": MAX_LIMIT})
        cash = []
        for raw in accounts if isinstance(accounts, list) else []:
            acc = _format_bank_account(raw)
            if not acc.get("id") or acc.get("closed") or acc.get("type") == BANK_ACCOUNT_TYPE_CASH:
                continue
            cash.append({
                "account_id": acc["id"],
                "label": acc.get("label"),
                "balance": float(acc.get("balance") or 0),
            })
        opening = round(sum(a["balance"] for a in cash), 2)

        async def _pending(endpoint: str) -> list:
            rows = await _api_get_list(endpoint, {
                "sortfield": "t.date_lim_reglement",
                "sortorder": "ASC",
                "limit": CLIENT_FILTER_SCAN_LIMIT,
                "sqlfilters": "(t.fk_statut:=:1)",
            })
            return rows if isinstance(rows, list) else []

        def _due_bucket(due: str) -> str:
            if not due:
                return "sans échéance"
            if due < today:
                return "en retard"
            delta = (
                datetime.strptime(due, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")
            ).days
            if delta <= 30:
                return "0-30 j"
            if delta <= 60:
                return "31-60 j"
            if delta <= 90:
                return "61-90 j"
            return "au-delà"

        def _digest(rows: list, supplier: bool) -> tuple:
            items, buckets = [], {}
            for inv in rows:
                due = _invoice_due_date(inv)
                total = float(inv.get("total_ttc") or 0)
                remaining = inv.get("remaintopay")
                amount = float(remaining) if remaining not in (None, "") else total
                if round(amount, 2) == 0:
                    continue
                bucket = _due_bucket(due)
                buckets[bucket] = round(buckets.get(bucket, 0) + amount, 2)
                items.append({
                    "id": _id_int(inv.get("id")),
                    "ref": inv.get("ref"),
                    "ref_supplier": inv.get("ref_supplier") if supplier else None,
                    "thirdparty": inv.get("thirdparty_name") or inv.get("socid"),
                    "amount": round(amount, 2),
                    "date_due": due or None,
                    "bucket": bucket,
                })
            items.sort(key=lambda i: (i["date_due"] or "9999-12-31"))
            return items, buckets

        receivable, in_buckets = _digest(await _pending("invoices"), supplier=False)
        payable, out_buckets = ([], {})
        if include_supplier:
            payable, out_buckets = _digest(await _pending("supplierinvoices"), supplier=True)

        def _within(items: list) -> float:
            return round(sum(
                i["amount"] for i in items
                if not i["date_due"] or i["date_due"] <= horizon_date
            ), 2)

        cash_in = _within(receivable)
        cash_out = _within(payable)

        return _dumps({
            "today": today,
            "horizon_days": horizon,
            "horizon_date": horizon_date,
            "opening_balance": opening,
            "accounts": cash,
            "expected_in": cash_in,
            "expected_out": cash_out,
            "projected_balance": round(opening + cash_in - cash_out, 2),
            "receivable_total": round(sum(i["amount"] for i in receivable), 2),
            "payable_total": round(sum(i["amount"] for i in payable), 2),
            "receivable_by_due": in_buckets,
            "payable_by_due": out_buckets,
            "overdue_in": in_buckets.get("en retard", 0),
            "overdue_out": out_buckets.get("en retard", 0),
            "receivable": receivable[:MAX_LIMIT],
            "payable": payable[:MAX_LIMIT],
            "basis_note": (
                "Projection à l'échéance : chaque facture est comptée à sa date "
                "d'échéance, les retards à effet immédiat. Reste dû lu sur "
                "`remaintopay`, à défaut sur le total TTC."
            ),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_transfer_between_accounts(
    from_account_id: int,
    to_account_id: int,
    date: str,
    amount: float,
    description: str,
) -> str:
    """Enregistre un virement interne entre deux comptes bancaires.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - from_account_id : compte débité (obligatoire)
    - to_account_id   : compte crédité (obligatoire)
    - date            : date d'opération (YYYY-MM-DD, obligatoire)
    - amount          : montant, positif (obligatoire)
    - description     : libellé porté sur les deux écritures (obligatoire)

    Dolibarr crée les DEUX écritures et les relie entre elles
    (`POST /bankaccounts/transfer`) : ne pas les saisir à la main avec
    dolibarr_create_bank_entry, qui produirait deux lignes sans lien.

    Retourne le solde des deux comptes avant et après. Un virement laisse la
    trésorerie totale inchangée : c'est le contrôle à lire en premier.
    """
    try:
        if not from_account_id or not to_account_id:
            return "Veuillez fournir from_account_id et to_account_id."
        if from_account_id == to_account_id:
            return "Les comptes source et destination sont identiques."
        if not str(description or "").strip():
            return "La description est obligatoire (elle sert de libellé aux deux écritures)."
        try:
            date_ts = _date_str_to_ts(date)
        except ValueError:
            return f"Format date invalide : '{date}'. Attendu : YYYY-MM-DD."
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return f"Montant invalide : {amount!r}."
        if value <= 0:
            return f"Le montant doit être strictement positif (reçu : {value})."

        async def _balance(account_id: int) -> float:
            return float(
                _num(await api_get(BANK_BALANCE_ENDPOINT.format(account_id=account_id))) or 0
            )

        from_before, to_before = await _balance(from_account_id), await _balance(to_account_id)

        await api_post(BANK_TRANSFER_ENDPOINT, {
            "bankaccount_from_id": from_account_id,
            "bankaccount_to_id": to_account_id,
            "date": date_ts,
            "description": str(description).strip(),
            "amount": value,
        })

        from_after, to_after = await _balance(from_account_id), await _balance(to_account_id)
        moved_out = round(from_before - from_after, 2)
        moved_in = round(to_after - to_before, 2)

        report: dict[str, Any] = {
            "success": True,
            "amount": round(value, 2),
            "from": {
                "account_id": from_account_id,
                "balance_before": round(from_before, 2),
                "balance_after": round(from_after, 2),
                "delta": round(from_after - from_before, 2),
            },
            "to": {
                "account_id": to_account_id,
                "balance_before": round(to_before, 2),
                "balance_after": round(to_after, 2),
                "delta": round(to_after - to_before, 2),
            },
            "total_delta": round((from_after + to_after) - (from_before + to_before), 2),
            "message": (
                f"Virement de {value} du compte {from_account_id} vers le compte "
                f"{to_account_id} : {description}."
            ),
        }
        if abs(moved_out - value) > 0.01 or abs(moved_in - value) > 0.01:
            report["success"] = False
            report["balance_warning"] = (
                f"Les soldes n'ont pas bougé du montant attendu ({value}) : "
                f"−{moved_out} sur le compte source, +{moved_in} sur le compte cible."
            )
        return _dumps(report)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def dolibarr_list_bank_statements(
    account_id: int,
    date_start: str='',
    date_end: str='',
    limit: int=DEFAULT_LIMIT,
    cursor: str='',
) -> str:
    """Recense les relevés déjà posés sur un compte, et les écritures restantes.

    Export : total, returned, count, truncated et next_cursor. Passer cursor
    avec les mêmes filtres pour continuer. total=null si le parcours est incomplet.

    Paramètres :
    - account_id : ID du compte (obligatoire)
    - date_start : début de la période inspectée (YYYY-MM-DD). Par défaut,
      365 jours en arrière : l'API ne sait pas agréger, chaque écriture de la
      période est relue.
    - date_end   : fin de la période inspectée (YYYY-MM-DD, optionnel)

    Retourne, par numéro de relevé : nombre d'écritures, première et dernière
    date, total mouvementé. Plus `unassigned`, le compte des écritures encore
    sans relevé : celles que dolibarr_set_bank_statement reste à traiter.
    """
    try:
        if not account_id:
            return "Veuillez fournir un account_id."
        invalid = _valid_dates(date_start=date_start, date_end=date_end)
        if invalid:
            return invalid

        window_start = date_start or _shift_days(
            date_end or _today_iso(), -BANK_STATEMENTS_DEFAULT_WINDOW_DAYS
        )
        lines = await _bank_lines(
            account_id, _bank_date_filter(after=window_start, before=date_end)
        )

        statements: dict[str, dict] = {}
        unassigned, unreconciled = 0, 0
        for line in lines:
            amount = float(_num(line.get("amount")) or 0)
            date = _ts_to_date(line.get("dateo"))
            num = str(line.get("num_releve") or "").strip()
            if not _id_int(line.get("rappro")):
                unreconciled += 1
            if not num:
                unassigned += 1
                continue
            entry = statements.setdefault(
                num, {"num_releve": num, "entries": 0, "total": 0.0,
                      "first_date": date, "last_date": date}
            )
            entry["entries"] += 1
            entry["total"] = round(entry["total"] + amount, 2)
            entry["first_date"] = min(entry["first_date"] or date, date or "9999-12-31")
            entry["last_date"] = max(entry["last_date"] or date, date or "")

        ordered = sorted(statements.values(), key=lambda s: s["last_date"], reverse=True)
        return _dumps(_export_payload({
            "account_id": account_id,
            "date_start": window_start,
            "date_end": date_end or None,
            "entries_scanned": len(lines),
            "count": len(ordered),
            "unassigned_entries": unassigned,
            "unreconciled_entries": unreconciled,
            "last_statement": ordered[0]["num_releve"] if ordered else None,
            "statements": ordered,
        }, "statements", limit, cursor, identity="num_releve"))

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
