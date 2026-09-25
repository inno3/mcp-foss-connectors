# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour Matrix (API client-server).

Expose les salons et messages Matrix via le protocole MCP pour Claude Desktop :
liste des salons, lecture/envoi de messages, gestion des membres et des salons.

Instance : configurable via MATRIX_HOMESERVER_URL
Auth     : Bearer token (access token Matrix) via MATRIX_ACCESS_TOKEN
API      : Matrix Client-Server API v3 (/_matrix/client/v3/)
"""

import asyncio
import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("matrix-mcp")

mcp = FastMCP("matrix")

MATRIX_HOMESERVER_URL = os.environ.get("MATRIX_HOMESERVER_URL", "").rstrip("/")
MATRIX_ACCESS_TOKEN = os.environ.get("MATRIX_ACCESS_TOKEN", "")

# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2

#: Longueur retenue du corps d'un message dans une liste de résultats. Sans
#: plafond, une recherche à 50 résultats sur des messages de brief quotidien
#: sortait à 870 000 caractères — au-delà de ce qu'une session peut ingérer,
#: donc une recherche inutilisable précisément quand elle ramène beaucoup.
BODY_PREVIEW_CHARS = 500

#: Pages enchaînées au maximum pour réunir le nombre de messages demandé.
#: Relevé de 5 à 12 : sur un salon dont la tête de chronologie est faite de
#: blocs de suppressions — précisément les salons de notification, les plus
#: consultés — le budget sautait avant d'avoir trouvé le moindre message, et
#: l'outil rendait une page VIDE. Vérifié le 01/09/2026.
MESSAGE_FETCH_MAX_PAGES = 12
#: Multiplicateur appliqué au `limit` demandé pour la taille des pages brutes.
#: Le filtrage écarte réactions, éditions, états et suppressions : demander
#: exactement `limit` événements garantit d'en manquer.
MESSAGE_FETCH_OVERSAMPLE = 4

#: Pages enchaînées au maximum de CHAQUE côté d'un contexte d'événement.
#: `/context` ne prend qu'une limite d'événements BRUTS : quand la fenêtre
#: rendue par le serveur est saturée de suppressions, de réactions ou de
#: changements d'état, le filtrage la vidait et `before` sortait à [] sur un
#: salon qui compte des centaines de messages. Vérifié le 02/09/2026.
CONTEXT_FETCH_MAX_PAGES = 8

#: Salons pour lesquels matrix_list_unread accepte d'aller chercher, un par un,
#: ce que /sync ne donne pas : le dernier message et la date du repère de
#: lecture. Chaque salon coûte un appel : la borne évite qu'un compte à
#: plusieurs centaines de salons déclenche autant de requêtes.
UNREAD_LOOKUP_MAX = 25

#: Plafond dur du nombre de salons rendus par matrix_list_unread. L'ancien
#: plafond de 200 tronquait en silence un compte qui en compte davantage — et
#: c'est ce qui laissait `unread_after` à null après un marquage.
UNREAD_ROOMS_MAX = 1000

_RETRY_DELAY = 1.0

# ---------------------------------------------------------------------------
# Contrat d'erreur
# ---------------------------------------------------------------------------

#: Ce que signifie chaque famille d'exception httpx quand elle arrive sans
#: message : c'est le cas courant (ReadError depuis une fin de flux, ReadTimeout
#: depuis un TimeoutError nu), et « Erreur : » suivi de rien était tout ce que
#: l'appelant recevait — onze fois, une par outil.
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

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# MCP lance chaque appel d'outil dans une tâche distincte. Une tâche enfant
# hérite du contexte : l'identité de la tâche évite qu'elle hérite aussi des
# écritures du parent et les attribue à tort à son propre appel.
_applied_writes: ContextVar["tuple[asyncio.Task[Any], list[str]] | None"] = ContextVar(
    "matrix_applied_writes", default=None,
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


def _route(url: str) -> str:
    """Route sans l'hôte ni le préfixe d'API, pour nommer une requête.

    Un message d'erreur n'a pas à citer le homeserver, et la route seule reste
    lisible quand plusieurs écritures sont listées côte à côte.
    """
    path = urlparse(url).path
    for prefix in ("/_matrix/client/v3/", "/_matrix/client/v1/", "/_matrix/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path.lstrip("/")


def _is_read(method: str, url: str) -> bool:
    """`POST /search` interroge, il n'écrit pas.

    C'est la seule exception : `createRoom`, `invite` et `read_markers` passent
    aussi par POST et modifient bien quelque chose.
    """
    if method.upper() in _READ_METHODS:
        return True
    return method.upper() == "POST" and _route(url).split("?", 1)[0] == "search"


def _is_deduplicated_write(method: str, url: str) -> bool:
    """Un PUT Matrix peut être rejoué à l'identique sans rien dupliquer.

    Deux cas, et ils couvrent tous les PUT du connecteur :

    - envoi, correction et suppression portent un identifiant de transaction
      dans l'URL, fixé par l'appelant AVANT `_request`. La spécification
      Client-Server demande au homeserver de dédupliquer dessus, donc un rejeu
      interne réutilise le même identifiant et ne produit pas de second
      message ;
    - un événement d'état (`rooms/{id}/state/m.room.topic`) pose une valeur.
      L'appliquer deux fois laisse le même état.

    Attention : cela vaut pour un rejeu *interne*. Un nouvel appel d'outil tire
    un identifiant de transaction neuf, et là le doublon redevient possible.
    """
    return method.upper() == "PUT"


def _append_sentence(base: str, extra: str) -> str:
    """Enchaîne deux phrases sans les souder : un message Matrix finit rarement
    par un point, et « You are not invited Etat de l'ecriture » se lit mal."""
    if not extra:
        return base
    base = base.rstrip()
    if base and base[-1] not in ".!?;:":
        base += "."
    return base + extra


def _write_state_message(write_state: str, label: str, deduplicated: bool = False) -> str:
    """Précise le risque de rejouer la requête qui vient d'échouer."""
    if write_state == "non_appliquee":
        return (f" Écriture {label} non appliquée : rejouer cette requête est sans "
                "risque de doublon.")
    if write_state == "inconnu" and deduplicated:
        return (f" État de l'écriture {label} inconnu : les tentatives ont gardé le "
                "même identifiant de transaction, donc rien n'a été dupliqué ici, "
                "mais un nouvel appel d'outil en tirerait un neuf ; relire le salon "
                "avant de rejouer.")
    if write_state == "inconnu":
        return (f" État de l'écriture {label} inconnu : écriture non rejouée "
                "automatiquement, le homeserver a pu la traiter avant de perdre la "
                "réponse ; relire le salon avant de rejouer.")
    return ""


class MatrixAPIError(RuntimeError):
    """Refus ou panne côté homeserver, avec l'état de l'écriture concernée.

    Reste une `RuntimeError` et garde le préfixe « Erreur Matrix {status} — » :
    onze outils n'ont pas de gestionnaire propre et laissent l'exception
    remonter telle quelle.
    """

    def __init__(self, status: int, method: str, url: str, body: str,
                 errcode: str = "", write_state: str = "sans_objet"):
        self.status = status
        self.method = method.upper()
        self.label = f"{self.method} {_route(url)}"
        self.errcode = errcode
        self.body = body
        self.write_state = write_state
        detail = body.strip() or "sans message du homeserver"
        super().__init__(f"Erreur Matrix {status} — {method} {url} : {detail}")


class MatrixTransportError(RuntimeError):
    """Échec réseau définitif, nommé même quand l'exception httpx est vide.

    Sans elle, un `httpx.ReadError` vide ou un `ReadTimeout` sans message se
    rendaient à l'appelant en « Erreur : » nu, et rien ne distinguait un jeton
    d'accès révoqué d'une connexion coupée.
    """

    def __init__(self, kind: str, label: str, attempts: int, replayable: bool,
                 is_read: bool, deduplicated: bool, detail: str = ""):
        self.kind = kind
        self.label = label
        self.attempts = attempts
        self.replayable = replayable
        self.deduplicated = deduplicated
        self.detail = detail.strip() or _TRANSPORT_DEFAULT_DETAIL.get(
            kind, "échec du transport HTTP")
        self.write_state = (
            "sans_objet" if is_read
            else "non_appliquee" if not deduplicated and replayable else "inconnu"
        )
        tail = _write_state_message(self.write_state, label, deduplicated)
        if self.write_state == "sans_objet":
            tail = (" Rejouer l'appel plus tard, et vérifier la disponibilité du "
                    "homeserver si l'erreur persiste.")
        count = "tentative" if attempts == 1 else "tentatives"
        super().__init__(
            f"{kind} sur {label} ({self.detail}) après {attempts} {count}.{tail}")


def _errcode_and_body(resp: "httpx.Response") -> tuple[str, str]:
    """Extrait le `errcode` Matrix et un corps lisible.

    Le errcode — M_FORBIDDEN, M_LIMIT_EXCEEDED, M_UNKNOWN_TOKEN — dit
    précisément ce qui est refusé, là où le champ `error` se contente souvent
    d'une phrase. Le jeter coûtait un aller-retour de diagnostic.
    """
    raw = (resp.text or "")[:500]
    try:
        body = resp.json()
    except Exception:
        return "", raw
    if not isinstance(body, dict):
        return "", raw
    errcode = str(body.get("errcode") or "")
    error = str(body.get("error") or "")
    if errcode and error:
        return errcode, f"{errcode} — {error}"
    return errcode, errcode or error or raw


def _format_error(exc: Exception) -> str:
    """Nomme le défaut, l'état de l'écriture et les écritures déjà appliquées.

    Jamais un « Erreur : » nu : une exception sans message (httpx.ReadError,
    ReadTimeout, RemoteProtocolError) garde son type et le sens de sa famille,
    et l'appelant doit pouvoir distinguer une défaillance réseau d'un refus,
    puis décider quoi relire.
    """
    message = _format_error_body(exc)
    journal = _write_journal()
    if journal:
        message = _append_sentence(
            message,
            f" Écritures déjà appliquées dans cet appel d'outil : {', '.join(journal)}. "
            "Relire les objets concernés avant de rejouer l'appel d'outil complet.")
    return message


def _format_error_body(exc: Exception) -> str:
    """Corps du message, sans le rappel des écritures déjà appliquées."""
    if isinstance(exc, MatrixTransportError):
        return f"Erreur réseau {exc}"
    if isinstance(exc, MatrixAPIError):
        # Le message porte déjà son propre préfixe « Erreur Matrix {status} — ».
        message = str(exc)
        if exc.write_state == "inconnu":
            message = _append_sentence(
                message, _write_state_message("inconnu", exc.label))
        elif exc.write_state == "non_appliquee":
            # 401, 403 et 404 gardent leur message exact : une auth refusée
            # n'écrit rien et n'a pas besoin qu'on en discute. Le reste
            # (400 sur un alias invalide, 413 sur un média trop lourd) est
            # refusé avant exécution : le dire évite une relecture inutile,
            # et un rejeu à l'identique échouerait pareil.
            message = _append_sentence(
                message,
                f" Écriture {exc.label} non appliquée : la requête a été refusée "
                "avant exécution, corriger l'appel avant de rejouer.")
        return message
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        _, body = _errcode_and_body(exc.response)
        return f"Erreur HTTP {status} : {body.strip() or 'sans message.'}"
    if isinstance(exc, httpx.TransportError):
        kind = type(exc).__name__
        detail = str(exc).strip() or _TRANSPORT_DEFAULT_DETAIL.get(
            kind, "échec du transport HTTP")
        return f"Erreur réseau {kind} : {detail}."
    if isinstance(exc, json.JSONDecodeError):
        return f"Erreur JSON Matrix : {exc}"
    text = str(exc)
    return (f"Erreur : {text}" if text.strip()
            else f"Erreur {type(exc).__name__} : sans message.")


def _matrix_url(path: str) -> str:
    """Construit l'URL complète d'un endpoint Matrix.

    Un chemin commençant par `_matrix/` est pris tel quel : tout ne vit pas sur
    la Client-Server v3. Les relations d'un fil sont en v1, le téléversement de
    média sur l'API media. Sans cette échappatoire, il faudrait dupliquer le
    client HTTP pour chaque famille d'endpoints.
    """
    clean = path.lstrip("/")
    if clean.startswith("_matrix/"):
        return f"{MATRIX_HOMESERVER_URL}/{clean}"
    return f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/{clean}"


def _headers() -> dict:
    """Retourne les headers HTTP avec le Bearer token Matrix."""
    if not MATRIX_ACCESS_TOKEN:
        raise ValueError("MATRIX_ACCESS_TOKEN non configuré.")
    return {
        "Authorization": f"Bearer {MATRIX_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _request(method: str, url: str, **kwargs: Any) -> Any:
    """Appel HTTP Matrix avec rejeu et backoff exponentiel.

    Ce qui est rejouable tient à deux propriétés du protocole, pas au verbe
    HTTP seul :

    - `POST /search` interroge, il n'écrit pas, et se rejoue comme une lecture.
      Les autres POST — `createRoom`, `invite`, `read_markers` — écrivent ;
    - un `PUT` porte un identifiant de transaction fixé avant l'appel, que le
      homeserver déduplique, ou pose un état à une valeur. Le rejeu interne
      réutilise donc le même identifiant et ne produit pas de second message.

    Tout le reste n'est rejoué que si la connexion n'a jamais abouti
    (ConnectError, ConnectTimeout) : une fois la requête partie, le homeserver
    a pu la traiter avant de perdre la réponse, et rejouer `createRoom`
    laisserait un salon en double, qui ne se rattrape qu'à la main.

    Limite : une écriture partie dont la réponse est perdue laisse un état que
    le client ne peut pas trancher, et la déduplication par identifiant de
    transaction ne couvre PAS un nouvel appel d'outil, qui en tire un neuf. Il
    faut relire le salon avant de rejouer, et c'est exactement ce que veut dire
    « inconnu ». Le journal rappelle en outre les écritures déjà réussies dans
    le même appel d'outil.
    """
    is_read = _is_read(method, url)
    deduplicated = not is_read and _is_deduplicated_write(method, url)
    label = f"{method.upper()} {_route(url)}"
    journal = _write_journal()
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=True, timeout=30) as client:
                logger.info("%s %s (attempt %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=_headers(), **kwargs)
                resp.raise_for_status()
                if resp.status_code == 204 or not resp.content:
                    data: Any = {}
                else:
                    data = resp.json()
            if not is_read:
                journal.append(label)
            return data
        except httpx.TransportError as exc:
            # Toute la famille est traitée : un ReadError, un PoolTimeout ou un
            # RemoteProtocolError traversaient sans rejeu ni habillage, et
            # sortaient de l'outil en « Erreur : » nu.
            never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            replayable = never_sent or is_read or deduplicated
            last_exc = exc
            if replayable and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s in %.1fs (%s: %s)", method, url, delay,
                               type(exc).__name__, exc)
                await asyncio.sleep(delay)
            else:
                raise MatrixTransportError(
                    type(exc).__name__, label, attempt + 1, never_sent,
                    is_read, deduplicated, str(exc),
                ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if (is_read or deduplicated) and status >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s (HTTP %d) in %.1fs", url, status, delay)
                await asyncio.sleep(delay)
            else:
                errcode, body = _errcode_and_body(exc.response)
                if is_read:
                    write_state = "sans_objet"
                elif status >= 500:
                    write_state = "inconnu"
                elif status in (401, 403, 404):
                    # Une auth refusée n'écrit rien et n'a pas besoin qu'on en
                    # discute : le message garde sa forme habituelle.
                    write_state = "sans_objet"
                else:
                    write_state = "non_appliquee"
                raise MatrixAPIError(
                    status, method, url, body, errcode, write_state,
                ) from exc
    raise last_exc  # type: ignore[misc]


async def _get(path: str, params: dict | None = None) -> Any:
    """GET vers l'API Matrix."""
    return await _request("GET", _matrix_url(path), params=params or {})


async def _post(path: str, data: dict | None = None) -> Any:
    """POST vers l'API Matrix."""
    return await _request(
        "POST", _matrix_url(path),
        content=json.dumps(data or {}, separators=(",", ":"), ensure_ascii=False).encode(),
    )


async def _put(path: str, data: dict | None = None) -> Any:
    """PUT vers l'API Matrix."""
    return await _request(
        "PUT", _matrix_url(path),
        content=json.dumps(data or {}, separators=(",", ":"), ensure_ascii=False).encode(),
    )


async def _upload_media(payload: bytes, filename: str, mimetype: str) -> Any:
    """Téléverse un fichier sur le média du homeserver et rend son URI mxc://.

    Le corps part en octets bruts : impossible de passer par _post, qui
    sérialise en JSON et impose un Content-Type incompatible.
    """
    url = _matrix_url("_matrix/media/v3/upload")
    headers = {
        "Authorization": f"Bearer {MATRIX_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": mimetype,
    }
    async with httpx.AsyncClient(verify=True, timeout=120) as client:
        logger.info("POST %s (%s, %d octets)", url, filename, len(payload))
        resp = await client.post(
            url, headers=headers, params={"filename": filename}, content=payload
        )
        resp.raise_for_status()
        return resp.json()


def _compact(obj: Any) -> str:
    """Sérialise en JSON compact pour économiser les tokens."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _ts_to_iso(ts_ms: int | None) -> str | None:
    """Convertit un timestamp Matrix (millisecondes) en chaîne ISO 8601."""
    if ts_ms is None:
        return None
    import datetime
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Outils MCP
# ---------------------------------------------------------------------------


@mcp.tool()
async def matrix_whoami() -> str:
    """Retourne les informations du compte Matrix authentifié (user_id, device_id, server)."""
    data = await _get("account/whoami")
    result = {
        "user_id": data.get("user_id"),
        "device_id": data.get("device_id"),
        "is_guest": data.get("is_guest", False),
    }
    return _compact(result)


@mcp.tool()
async def matrix_list_rooms() -> str:
    """Liste tous les salons Matrix rejoints avec leur nom et sujet.

    Retourne : liste de {id, name, topic}.
    """
    data = await _get("joined_rooms")
    room_ids: list[str] = data.get("joined_rooms", [])

    rooms = []
    for room_id in room_ids:
        entry: dict = {"id": room_id, "name": None, "topic": None}
        # Récupération du nom
        try:
            name_data = await _get(f"rooms/{room_id}/state/m.room.name")
            entry["name"] = name_data.get("name")
        except Exception:
            pass
        # Récupération du sujet
        try:
            topic_data = await _get(f"rooms/{room_id}/state/m.room.topic")
            entry["topic"] = topic_data.get("topic")
        except Exception:
            pass
        rooms.append(entry)

    return _compact({"rooms": rooms, "count": len(rooms)})


@mcp.tool()
async def matrix_get_room_messages(
    room_id: str,
    limit: int = 20,
    from_token: str = "",
    direction: str = "b",
    since: str = "",
    until: str = "",
) -> str:
    """Lit les messages d'un salon Matrix, page par page.

    Args:
        room_id    : Identifiant du salon (ex: !abc123:matrix.example.org).
        limit      : Nombre de messages (défaut 20, max 100).
        from_token : Jeton `next_token` d'un appel précédent, pour continuer.
        direction  : 'b' pour remonter le temps (défaut), 'f' pour descendre.
        since / until : fenêtre de dates ISO (« 2026-08-01 »), filtrée côté
                   client comme pour la recherche — l'API ne sait pas borner
                   une chronologie dans le temps.

    Le jeton de pagination rendu par le serveur était jusqu'ici ignoré : passé
    la première page, il n'y avait aucun moyen de remonter plus loin dans un
    salon. `next_token` permet désormais d'enchaîner.

    `limit` compte des MESSAGES, pas des événements. Le plafond de l'API porte
    sur les événements bruts — réactions, éditions, changements d'état — qui
    disparaissent ensuite au filtrage : demander 10 pouvait n'en rendre que 3.
    L'outil enchaîne donc les pages jusqu'à réunir le compte demandé, et
    `pages_fetched` dit combien il lui en a fallu. Les messages supprimés sont
    écartés plutôt que rendus avec un corps nul.

    Ce chaînage est BORNÉ : au plus `max_pages` pages par appel (voir
    MESSAGE_FETCH_MAX_PAGES, 12 aujourd'hui), chacune d'au plus 100 événements
    bruts. Après une série de suppressions, lire 3 messages a demandé 6 pages
    et 72 événements balayés, contre 1 page et 12 en temps normal — la borne
    n'est donc pas théorique. `budget_exhausted` dit qu'elle a été atteinte
    sans réunir `limit` : relancer avec `next_token` reprend là où l'on
    s'est arrêté, chaque appel disposant d'un budget neuf.

    Retourne les messages, `next_token`, `pages_fetched`, `events_scanned`,
    `max_pages`, `budget_exhausted` et `has_more`.
    """
    limit = min(max(1, limit), 100)
    direction = direction if direction in ("b", "f") else "b"
    cursor = from_token
    messages: list[dict] = []
    pages = 0
    events_scanned = 0
    end_token = cursor
    last_chunk_empty = True

    # Le `limit` de l'API porte sur les ÉVÉNEMENTS BRUTS, pas sur les messages :
    # réactions, éditions, changements d'état et messages supprimés y comptent,
    # puis disparaissent au filtrage. Demander 10 pouvait n'en rendre que 3.
    # On enchaîne donc les pages jusqu'à réunir `limit` messages réels.
    while len(messages) < limit and pages < MESSAGE_FETCH_MAX_PAGES:
        pages += 1
        raw_limit = min(limit * MESSAGE_FETCH_OVERSAMPLE, 100)
        params: dict[str, Any] = {"dir": direction, "limit": str(raw_limit)}
        if cursor:
            params["from"] = cursor
        data = await _get(f"rooms/{quote(room_id)}/messages", params=params)

        chunk = data.get("chunk", []) or []
        last_chunk_empty = not chunk
        end_token = data.get("end")
        events_scanned += len(chunk)
        for event in chunk:
            if event.get("type") != "m.room.message":
                continue
            content = event.get("content") or {}
            # Un message supprimé n'a plus de contenu. Le rendre avec body:null
            # le fait passer pour un message vide.
            if not content:
                continue
            stamp = _ts_to_iso(event.get("origin_server_ts")) or ""
            if (since and stamp[:10] < since[:10]) or (until and stamp[:10] > until[:10]):
                continue
            if len(messages) >= limit:
                break
            messages.append({
                "event_id": event.get("event_id"),
                "room_id": room_id,
                "sender": event.get("sender"),
                "body": content.get("body"),
                "msgtype": content.get("msgtype"),
                "type": event.get("type"),
                "timestamp": stamp,
                "permalink": _permalink(room_id, event.get("event_id") or ""),
            })
        if not end_token or not chunk:
            break
        cursor = end_token

    payload = {
        "messages": messages,
        "count": len(messages),
        "next_token": end_token,
        "pages_fetched": pages,
        "events_scanned": events_scanned,
        # `end` absent signifie que le serveur n'a plus rien à donner. Le lier
        # au fait d'avoir rempli `limit` ferait passer un lot incomplet pour la
        # fin du salon.
        "has_more": bool(end_token) and not last_chunk_empty,
        "limit_reached": len(messages) >= limit,
        # La borne de pagination était tenue dans une constante du module,
        # invisible depuis l'appel. Un appelant qui reçoit moins que `limit`
        # doit pouvoir distinguer « le salon est épuisé » de « mon budget de
        # pages l'est ».
        "max_pages": MESSAGE_FETCH_MAX_PAGES,
        "budget_exhausted": pages >= MESSAGE_FETCH_MAX_PAGES and len(messages) < limit,
    }
    if not messages and payload["has_more"]:
        # Une page vide assortie d'un jeton se lit comme un salon vide. Dire
        # que le budget a sauté évite de conclure à tort.
        payload["empty_page_note"] = (
            f"Aucun message trouvé après {events_scanned} événements sur {pages} page(s) : "
            "la portion parcourue ne contient que des suppressions, réactions ou "
            "changements d'état. Relancer avec next_token pour continuer."
        )
    return _compact(payload)


@mcp.tool()
async def matrix_send_message(room_id: str, message: str) -> str:
    """Envoie un message texte brut dans un salon Matrix.

    Args:
        room_id : Identifiant du salon (ex: !abc123:matrix.example.org).
        message : Contenu du message à envoyer.

    Retourne : event_id et permalien du message envoyé.
    """
    txn_id = str(uuid.uuid4()).replace("-", "")
    data = await _put(
        f"rooms/{quote(room_id)}/send/m.room.message/{txn_id}",
        {"msgtype": "m.text", "body": message},
    )
    event_id = data.get("event_id")
    # Seul outil d'écriture à ne pas rendre de permalien : il fallait le
    # reconstruire à la main pour citer le message dans un CR ou une carte.
    return _compact({
        "event_id": event_id,
        "room_id": room_id,
        "permalink": _permalink(room_id, event_id or ""),
    })


@mcp.tool()
async def matrix_send_html_message(room_id: str, message: str, html_message: str) -> str:
    """Envoie un message HTML formaté dans un salon Matrix.

    Args:
        room_id      : Identifiant du salon (ex: !abc123:matrix.example.org).
        message      : Version texte brut du message (fallback pour clients sans HTML).
        html_message : Version HTML du message (ex: "<b>Bonjour</b>").

    Retourne : event_id du message envoyé.
    """
    txn_id = str(uuid.uuid4()).replace("-", "")
    data = await _put(
        f"rooms/{room_id}/send/m.room.message/{txn_id}",
        {
            "msgtype": "m.text",
            "body": message,
            "format": "org.matrix.custom.html",
            "formatted_body": html_message,
        },
    )
    return _compact({"event_id": data.get("event_id"), "room_id": room_id})


@mcp.tool()
async def matrix_search_messages(
    query: str,
    room_id: str = "",
    limit: int = 20,
    next_batch: str = "",
    sender: str = "",
    context_size: int = 0,
    body_chars: int = BODY_PREVIEW_CHARS,
    exclude_rooms: str = "",
    since: str = "",
    until: str = "",
) -> str:
    """Recherche des messages dans les salons Matrix.

    Args:
        query        : Terme de recherche.
        room_id      : Restreindre à un salon (optionnel).
        limit        : Nombre de résultats par page (défaut 20, max 100).
        next_batch   : Jeton rendu par un appel précédent, pour la page suivante.
        sender       : Restreindre à un expéditeur (ex: @bjean:matrix.inno3.eu).
        context_size : Nombre de messages à joindre avant ET après chaque
                       résultat (défaut 0). Un résultat isolé se comprend
                       rarement seul.

    Trois manques comblés. Le serveur renvoie un `next_batch` et un total, tous
    deux ignorés jusqu'ici : au-delà de la première page — dix résultats par
    défaut côté serveur — il n'existait aucun moyen de continuer, et rien ne
    disait qu'il en restait. Les résultats ne portaient que des `room_id`
    opaques, sans le nom du salon. Et le contexte, que l'API sait fournir,
    n'était pas demandé.

    - body_chars : longueur retenue de chaque corps de message (défaut 500,
      0 = entier). Une recherche à 50 résultats sur des messages longs sortait
      à 870 000 caractères, donc inexploitable en session. Les corps coupés
      portent `body_truncated` et `body_full_length`.
    - exclude_rooms : salons à écarter, séparés par des virgules. Les salons de
      notification automatique noyaient tout le reste.
    - since / until : fenêtre de dates ISO (« 2026-08-01 »). Filtrage effectué
      CÔTÉ CLIENT : l'API Matrix ne sait pas borner une recherche dans le temps.
      Le filtre ne porte donc que sur la page demandée, et `filtered_out` dit
      combien de résultats il a écartés — enchaîner sur `next_batch` pour
      remonter plus loin.

    Retourne : résultats, `index_matches`, `next_batch`, `has_more`.

    ⚠️ `index_matches` (anciennement `total_available`) est le total de l'INDEX
    du homeserver pour le terme cherché : il IGNORE les filtres `room_id`,
    `sender` et la fenêtre de dates, et compte encore les messages supprimés.
    Le lire comme un nombre de résultats disponibles induit en erreur — vérifié
    le 01/09/2026 : une recherche filtrée par expéditeur annonçait 109
    disponibles, en rendait 2, et déclarait n'avoir plus rien.
    """
    room_events: dict[str, Any] = {
        "search_term": query,
        "order_by": "recent",
        "filter": {"limit": min(max(1, limit), 100)},
    }
    if room_id:
        room_events["filter"]["rooms"] = [room_id]
    if exclude_rooms:
        room_events["filter"]["not_rooms"] = [
            r.strip() for r in exclude_rooms.split(",") if r.strip()
        ]
    if sender:
        room_events["filter"]["senders"] = [sender]
    if context_size:
        span = min(max(1, int(context_size)), 20)
        room_events["event_context"] = {
            "before_limit": span, "after_limit": span, "include_profile": False,
        }

    data = await _post(
        "search" + (f"?next_batch={quote(next_batch)}" if next_batch else ""),
        {"search_categories": {"room_events": room_events}},
    )

    category = (data.get("search_categories", {}) or {}).get("room_events", {}) or {}
    results = []
    filtered_out = 0
    for item in category.get("results", []):
        event = item.get("result", {}) or {}
        if since or until:
            stamp = _ts_to_iso(event.get("origin_server_ts")) or ""
            if (since and stamp[:10] < since[:10]) or (until and stamp[:10] > until[:10]):
                filtered_out += 1
                continue
        entry = _format_event(
            event, await _room_name(event.get("room_id") or ""), body_chars=body_chars
        )
        context = item.get("context") or {}
        if context_size and context:
            entry["context_before"] = [
                _format_event(e, body_chars=body_chars)
                for e in context.get("events_before", [])
            ]
            entry["context_after"] = [
                _format_event(e, body_chars=body_chars)
                for e in context.get("events_after", [])
            ]
        entry["rank"] = item.get("rank")
        results.append({k: v for k, v in entry.items() if v not in (None, "", [], False)})

    token = category.get("next_batch")
    payload = {
        "count": len(results),
        # Renommé : « total_available » se lisait comme un nombre de résultats
        # atteignables, ce qu'il n'est pas.
        "index_matches": category.get("count"),
        "index_matches_note": (
            "Total de l'index du homeserver. Il REFLÈTE les filtres de salon "
            "(room_id, exclude_rooms), appliqués côté serveur. Il IGNORE en "
            "revanche le filtre d'expéditeur et la fenêtre de dates, et compte "
            "les messages supprimés. Voir index_matches_reliable."
        ),
        # Un appelant automatique qui comparerait count à index_matches se
        # tromperait dès que l'un des filtres non répercutés est actif.
        "index_matches_reliable": not (sender or since or until),
        "next_batch": token,
        "has_more": bool(token),
        "results": results,
    }
    if since or until:
        payload["filtered_out"] = filtered_out
        payload["date_filter_note"] = (
            "Fenêtre de dates appliquée côté client : elle ne porte que sur cette "
            "page. Enchaîner sur next_batch pour remonter plus loin."
        )
    return _compact(payload)


@mcp.tool()
async def matrix_get_room_members(room_id: str) -> str:
    """Liste les membres d'un salon Matrix.

    Args:
        room_id : Identifiant du salon (ex: !abc123:matrix.example.org).

    Retourne : liste de {user_id, display_name, membership}.
    """
    data = await _get(f"rooms/{room_id}/members")

    members = []
    for event in data.get("chunk", []):
        if event.get("type") != "m.room.member":
            continue
        content = event.get("content", {})
        members.append({
            "user_id": event.get("state_key"),
            "display_name": content.get("displayname"),
            "membership": content.get("membership"),
        })

    # Trier : joined en premier, puis invited, puis left
    membership_order = {"join": 0, "invite": 1, "leave": 2, "ban": 3}
    members.sort(key=lambda m: membership_order.get(m.get("membership", ""), 99))

    return _compact({"members": members, "count": len(members)})


@mcp.tool()
async def matrix_create_room(name: str, topic: str = "", public: bool = False) -> str:
    """Crée un nouveau salon Matrix.

    Args:
        name   : Nom du salon.
        topic  : Sujet du salon (optionnel).
        public : Si True, le salon est public (preset public_chat). Sinon privé (défaut).

    Retourne : room_id du salon créé.
    """
    preset = "public_chat" if public else "private_chat"
    payload: dict = {
        "name": name,
        "preset": preset,
    }
    if topic:
        payload["topic"] = topic

    data = await _post("createRoom", payload)
    return _compact({"room_id": data.get("room_id"), "name": name, "preset": preset})


@mcp.tool()
async def matrix_invite_user(room_id: str, user_id: str) -> str:
    """Invite un utilisateur dans un salon Matrix.

    Args:
        room_id : Identifiant du salon (ex: !abc123:matrix.example.org).
        user_id : Identifiant Matrix de l'utilisateur à inviter (ex: @alice:matrix.example.org).

    Retourne : confirmation de l'invitation.
    """
    await _post(f"rooms/{room_id}/invite", {"user_id": user_id})
    return _compact({"status": "ok", "room_id": room_id, "invited": user_id})


@mcp.tool()
async def matrix_set_topic(room_id: str, topic: str) -> str:
    """Définit ou modifie le sujet (topic) d'un salon Matrix.

    Args:
        room_id : Identifiant du salon (ex: !abc123:matrix.example.org).
        topic   : Nouveau sujet du salon.

    Retourne : confirmation de la mise à jour.
    """
    await _put(f"rooms/{room_id}/state/m.room.topic", {"topic": topic})
    return _compact({"status": "ok", "room_id": room_id, "topic": topic})


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fils de discussion, réactions, corrections, pièces jointes
# ---------------------------------------------------------------------------

#: Taille maximale d'une pièce jointe envoyée par le connecteur. Le corps part
#: en octets bruts ; au-delà, c'est la limite du homeserver qui tranche, avec un
#: message que rien ne rattache au fichier envoyé.
MEDIA_UPLOAD_MAX_BYTES = 25 * 1024 * 1024

#: Types de message déduits de l'extension du fichier envoyé.
_MSGTYPE_BY_EXT = {
    ".png": "m.image", ".jpg": "m.image", ".jpeg": "m.image", ".gif": "m.image",
    ".webp": "m.image", ".svg": "m.image",
    ".mp4": "m.video", ".webm": "m.video",
    ".mp3": "m.audio", ".ogg": "m.audio", ".wav": "m.audio",
}


def _txn_id() -> str:
    """Identifiant de transaction, unique par envoi.

    Matrix déduplique les envois sur cet identifiant : le réutiliser ferait
    silencieusement ignorer le second message. Isolé dans un helper pour que
    les tests puissent le figer.
    """
    import uuid

    return uuid.uuid4().hex


async def _send_event(room_id: str, event_type: str, content: dict) -> str:
    """Envoie un événement dans un salon et retourne son event_id."""
    result = await _put(
        f"rooms/{quote(room_id)}/send/{event_type}/{_txn_id()}", content
    )
    return (result or {}).get("event_id", "")


def _permalink(room_id: str, event_id: str = "") -> str:
    """Lien matrix.to vers un salon ou un message précis.

    Sert à citer un échange depuis un compte rendu ou une carte Kanboard, ce
    qu'un simple event_id ne permet pas.
    """
    base = f"https://matrix.to/#/{quote(room_id)}"
    return f"{base}/{quote(event_id)}" if event_id else base


def _clip_body(text: Any, limit: int) -> "tuple[str, bool, int]":
    """Tronque un corps de message en signalant la coupe. (texte, coupé, taille)."""
    s = "" if text is None else str(text)
    if limit <= 0 or len(s) <= limit:
        return s, False, len(s)
    return s[:limit], True, len(s)


def _format_event(event: dict, room_name: str = "", body_chars: int = 0) -> dict:
    """Formate un événement Matrix en dict lisible et compact.

    `body_chars` > 0 borne le corps rendu. Un événement SUPPRIMÉ n'a plus de
    contenu : il ressort avec `redacted: true` plutôt qu'avec un corps nul, que
    l'appelant lirait comme un message vide.
    """
    if not isinstance(event, dict):
        return {}
    content = event.get("content") or {}
    relates = content.get("m.relates_to") or {}
    redacted = bool(event.get("unsigned", {}).get("redacted_because")) or (
        not content and event.get("type") == "m.room.message"
    )
    body, clipped, full_len = _clip_body(content.get("body"), body_chars)
    out = {
        "event_id": event.get("event_id"),
        "sender": event.get("sender"),
        "body": body or None,
        "body_truncated": True if clipped else None,
        "body_full_length": full_len if clipped else None,
        "redacted": True if redacted else None,
        "msgtype": content.get("msgtype"),
        # Sans le type, une ligne sans corps ne dit pas ce qu'elle est :
        # réaction ? suppression ? changement d'état ? Elle devient du bruit
        # qu'on ne peut ni lire ni écarter en connaissance de cause.
        "type": event.get("type"),
        "room_id": event.get("room_id"),
        "room_name": room_name or None,
        "timestamp": _ts_to_iso(event.get("origin_server_ts")),
        "thread_root": relates.get("event_id") if relates.get("rel_type") == "m.thread" else None,
        "edited": bool(relates.get("rel_type") == "m.replace"),
        "permalink": _permalink(event.get("room_id") or "", event.get("event_id") or "")
        if event.get("room_id") and event.get("event_id") else None,
    }
    return {k: v for k, v in out.items() if v not in (None, "", False)}


_ROOM_NAMES: dict[str, str] = {}


async def _room_name(room_id: str) -> str:
    """Nom lisible d'un salon, mémorisé le temps de la session.

    La recherche ne rend que des `room_id` opaques : sans cette résolution, un
    résultat ne dit pas dans quel salon il a été trouvé.
    """
    if room_id in _ROOM_NAMES:
        return _ROOM_NAMES[room_id]
    try:
        state = await _get(f"rooms/{quote(room_id)}/state/m.room.name")
        name = (state or {}).get("name") or ""
    except Exception:
        name = ""
    _ROOM_NAMES[room_id] = name
    return name


@mcp.tool()
async def matrix_reply_in_thread(
    room_id: str,
    thread_root_event_id: str,
    message: str,
    html_message: str = "",
) -> str:
    """Répond dans le FIL d'un message, au lieu d'écrire dans le salon.

    Confirmation utilisateur requise avant exécution.

    Args:
        room_id              : Salon concerné.
        thread_root_event_id : Message qui ouvre le fil.
        message              : Réponse en texte brut.
        html_message         : Version HTML, optionnelle.

    Une alerte postée par un workflow n8n appelle une réponse rattachée, pas un
    message de plus dans le salon : sans fil, le suivi d'un incident se perd
    entre les alertes suivantes. C'était la seule façon d'écrire jusqu'ici.

    Retourne l'event_id de la réponse et son permalien.
    """
    try:
        if not room_id or not thread_root_event_id:
            return "Veuillez fournir room_id et thread_root_event_id."
        if not str(message or "").strip():
            return "Le message est vide."

        content: dict[str, Any] = {
            "msgtype": "m.text",
            "body": message,
            "m.relates_to": {
                "rel_type": "m.thread",
                "event_id": thread_root_event_id,
                # `is_falling_back` indique que la citation ci-dessous n'est là
                # que pour les clients qui ignorent les fils : sans elle, ils
                # afficheraient une réponse citée en double.
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": thread_root_event_id},
            },
        }
        if html_message:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = html_message

        event_id = await _send_event(room_id, "m.room.message", content)
        return _compact({
            "success": bool(event_id),
            "event_id": event_id,
            "thread_root": thread_root_event_id,
            "permalink": _permalink(room_id, event_id),
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_get_thread(room_id: str, thread_root_event_id: str, limit: int = 50) -> str:
    """Lit toutes les réponses d'un fil de discussion.

    Args:
        room_id              : Salon concerné.
        thread_root_event_id : Message qui ouvre le fil.
        limit                : Nombre max de réponses (défaut 50).

    `matrix_get_room_messages` rend la chronologie du salon, dans laquelle les
    réponses d'un fil sont éparpillées entre les autres messages. Cet outil rend
    le fil seul, dans l'ordre.

    Retourne le message racine puis ses réponses, chacune avec son permalien.
    """
    try:
        if not room_id or not thread_root_event_id:
            return "Veuillez fournir room_id et thread_root_event_id."

        # Les relations vivent sur l'API v1, pas v3.
        data = await _get(
            f"_matrix/client/v1/rooms/{quote(room_id)}/relations/"
            f"{quote(thread_root_event_id)}/m.thread",
            {"limit": max(1, min(int(limit), 100)), "dir": "f"},
        )
        replies = [_format_event(e) for e in (data or {}).get("chunk", [])]

        root = {}
        try:
            root = _format_event(
                await _get(f"rooms/{quote(room_id)}/event/{quote(thread_root_event_id)}")
            )
        except Exception:
            pass

        return _compact({
            "room_id": room_id,
            "thread_root": thread_root_event_id,
            "root": root,
            "count": len(replies),
            "replies": replies,
            "next_batch": (data or {}).get("next_batch"),
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_react(room_id: str, event_id: str, emoji: str = "👍") -> str:
    """Pose une réaction sur un message.

    Confirmation utilisateur requise avant exécution.

    Args:
        room_id  : Salon concerné.
        event_id : Message à annoter.
        emoji    : Réaction (défaut « 👍 »).

    Sert à accuser réception d'une alerte sans encombrer le salon d'un message.

    Retourne l'event_id de la réaction — c'est lui, et non celui du message,
    qu'il faudra passer à matrix_redact_message pour la retirer.
    """
    try:
        if not room_id or not event_id:
            return "Veuillez fournir room_id et event_id."
        if not str(emoji or "").strip():
            return "L'emoji est vide."

        reaction_id = await _send_event(room_id, "m.reaction", {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": event_id,
                "key": emoji,
            }
        })
        return _compact({
            "success": bool(reaction_id),
            "reaction_event_id": reaction_id,
            "reacted_to": event_id,
            "emoji": emoji,
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_edit_message(
    room_id: str,
    event_id: str,
    message: str,
    html_message: str = "",
) -> str:
    """Corrige un message déjà envoyé.

    Confirmation utilisateur requise avant exécution.

    Args:
        room_id      : Salon concerné.
        event_id     : Message à corriger.
        message      : Nouveau texte.
        html_message : Nouvelle version HTML, optionnelle.

    Matrix ne remplace rien : il publie un événement de remplacement que les
    clients affichent à la place, en signalant la retouche. L'original reste
    dans l'historique du salon — une correction n'efface pas une erreur, elle
    la couvre. Pour faire disparaître un message, utiliser
    matrix_redact_message.

    Un message ne peut être corrigé que par son auteur : corriger celui d'un
    autre compte échoue côté serveur.
    """
    try:
        if not room_id or not event_id:
            return "Veuillez fournir room_id et event_id."
        if not str(message or "").strip():
            return "Le message est vide."

        new_content: dict[str, Any] = {"msgtype": "m.text", "body": message}
        if html_message:
            new_content["format"] = "org.matrix.custom.html"
            new_content["formatted_body"] = html_message

        content = {
            # Le corps préfixé d'une astérisque est la convention Matrix pour
            # les clients qui n'affichent pas les remplacements.
            "msgtype": "m.text",
            "body": f"* {message}",
            "m.new_content": new_content,
            "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
        }
        if html_message:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = f"* {html_message}"

        edit_id = await _send_event(room_id, "m.room.message", content)
        return _compact({
            "success": bool(edit_id),
            "edit_event_id": edit_id,
            "edited": event_id,
            "permalink": _permalink(room_id, event_id),
            "note": "L'original reste dans l'historique ; les clients affichent la version corrigée.",
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_redact_message(
    room_id: str, event_id: str, reason: str = "", include_edits: bool = True
) -> str:
    """Supprime un message d'un salon.

    Confirmation utilisateur requise avant exécution (action destructive).

    Args:
        room_id  : Salon concerné.
        event_id : Message à supprimer.
        reason   : Motif, visible des autres membres (optionnel).
        include_edits : supprimer aussi les CORRECTIONS de ce message
                   (défaut : oui). Une correction est un événement distinct :
                   supprimer la racine seule la laisse affichée, texte compris.
                   Constaté le 01/09/2026 — le remplacement survivait à la
                   suppression de son message et restait visible en
                   chronologie comme en recherche.

    La suppression est définitive et se propage aux autres serveurs. Elle retire
    le contenu, pas la trace : les membres voient qu'un message a été supprimé,
    et par qui. Supprimer le message d'un autre compte demande le droit de
    modération sur le salon.

    Retourne la confirmation, vérifiée en relisant l'événement — un contenu vide
    après coup est la marque d'une suppression effective.
    """
    try:
        if not room_id or not event_id:
            return "Veuillez fournir room_id et event_id."

        # Les corrections d'abord : supprimer la racine ne les emporte pas, et
        # une fois la racine partie elles restent seules et lisibles.
        edits_removed = []
        if include_edits:
            try:
                relations = await _get(
                    f"_matrix/client/v1/rooms/{quote(room_id)}/relations/"
                    f"{quote(event_id)}/m.replace",
                    {"limit": 50},
                )
                for e in (relations or {}).get("chunk", []):
                    eid = e.get("event_id") if isinstance(e, dict) else None
                    if not eid:
                        continue
                    await _put(
                        f"rooms/{quote(room_id)}/redact/{quote(eid)}/{_txn_id()}",
                        {"reason": reason} if reason else {},
                    )
                    edits_removed.append(eid)
            except Exception as exc:
                logger.warning("Corrections non supprimées pour %s : %s", event_id, exc)

        result = await _put(
            f"rooms/{quote(room_id)}/redact/{quote(event_id)}/{_txn_id()}",
            {"reason": reason} if reason else {},
        )

        redacted = None
        try:
            event = await _get(f"rooms/{quote(room_id)}/event/{quote(event_id)}")
            redacted = not (event or {}).get("content")
        except Exception:
            pass

        payload = {
            "success": redacted if redacted is not None else bool(result),
            "redaction_event_id": (result or {}).get("event_id"),
            "redacted": event_id,
            "verified": redacted,
            "reason": reason or None,
        }
        if edits_removed:
            payload["edits_redacted"] = edits_removed
        return _compact(payload)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_send_file(
    room_id: str,
    file_path: str,
    caption: str = "",
    thread_root_event_id: str = "",
) -> str:
    """Envoie un fichier local dans un salon Matrix.

    Confirmation utilisateur requise avant exécution.

    Args:
        room_id              : Salon destinataire.
        file_path            : Chemin du fichier SUR LA MACHINE qui exécute le
                               connecteur.
        caption              : Légende ; à défaut, le nom du fichier.
        thread_root_event_id : Poster dans un fil plutôt que dans le salon.

    Envoyer un rapport ou une capture demandait jusqu'ici de passer par un
    client Matrix. Le type est déduit de l'extension : image, vidéo, son, ou
    fichier générique.

    Le fichier est d'abord téléversé sur le média du homeserver, qui rend une
    URI `mxc://`, puis référencé dans le message. Taille plafonnée à 25 Mo côté
    connecteur ; le homeserver peut imposer plus bas.
    """
    try:
        import os.path

        if not room_id:
            return "Veuillez fournir un room_id."
        if not os.path.isfile(file_path):
            return f"Fichier introuvable : {file_path}"

        size = os.path.getsize(file_path)
        if size > MEDIA_UPLOAD_MAX_BYTES:
            return (
                f"Fichier trop volumineux ({round(size / 1048576, 1)} Mo) : la limite du "
                f"connecteur est {MEDIA_UPLOAD_MAX_BYTES // 1048576} Mo."
            )

        import mimetypes

        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()
        mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(file_path, "rb") as handle:
            payload = handle.read()

        upload = await _upload_media(payload, filename, mimetype)
        content_uri = (upload or {}).get("content_uri")
        if not content_uri:
            return _compact({
                "success": False,
                "message": "Le homeserver n'a pas renvoyé d'URI de média.",
            })

        content: dict[str, Any] = {
            "msgtype": _MSGTYPE_BY_EXT.get(ext, "m.file"),
            "body": caption or filename,
            "filename": filename,
            "url": content_uri,
            "info": {"mimetype": mimetype, "size": size},
        }
        if thread_root_event_id:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_root_event_id,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": thread_root_event_id},
            }

        event_id = await _send_event(room_id, "m.room.message", content)
        return _compact({
            "success": bool(event_id),
            "event_id": event_id,
            "filename": filename,
            "size": size,
            "msgtype": content["msgtype"],
            "content_uri": content_uri,
            "permalink": _permalink(room_id, event_id),
        })
    except Exception as exc:
        return _format_error(exc)


def _fill_side(
    kept: list, events: Any, want: int, room_name: str, include_bodyless: bool
) -> None:
    """Ajoute à `kept` les événements retenus, sans dépasser `want`.

    Le filtrage précède la coupe : trancher d'abord rendrait « 3 messages »
    dont deux réactions.
    """
    want = max(0, int(want))
    for e in (events or []):
        if len(kept) >= want:
            return
        entry = _format_event(e, room_name, body_chars=BODY_PREVIEW_CHARS)
        if not include_bodyless and not entry.get("body"):
            continue
        kept.append(entry)


async def _collect_context_side(
    room_id: str,
    seed_events: Any,
    token: Any,
    direction: str,
    want: int,
    room_name: str,
    include_bodyless: bool,
) -> "tuple[list, dict]":
    """Un côté du contexte, ÉLARGI auprès du serveur jusqu'au compte demandé.

    `/context` ne prend qu'une limite d'événements BRUTS, que le serveur
    répartit à sa guise entre les deux côtés. Suppressions, réactions et
    changements d'état y comptent, puis disparaissent au filtrage : demander
    3 messages avant pouvait n'en rendre aucun, sans que rien ne distingue
    « il n'y a rien avant » de « j'ai tout filtré ». C'est le même défaut que
    matrix_get_room_messages a déjà corrigé, et la même mécanique : on
    poursuit la chronologie à partir du jeton rendu par `/context` — `start`
    pour remonter, `end` pour descendre — jusqu'à réunir le compte ou épuiser
    le budget de pages.

    Rend (messages, compteurs) où compteurs porte `pages_fetched`,
    `events_scanned` et `complete`.
    """
    want = max(0, int(want))
    kept: list = []
    pages = 1                      # l'appel /context lui-même
    scanned = len(seed_events or [])
    _fill_side(kept, seed_events, want, room_name, include_bodyless)

    raw_limit = str(min(max(want * MESSAGE_FETCH_OVERSAMPLE, 10), 100))
    while len(kept) < want and token and pages < CONTEXT_FETCH_MAX_PAGES:
        pages += 1
        data = await _get(
            f"rooms/{quote(room_id)}/messages",
            params={"dir": direction, "limit": raw_limit, "from": token},
        )
        chunk = (data or {}).get("chunk") or []
        scanned += len(chunk)
        _fill_side(kept, chunk, want, room_name, include_bodyless)
        token = (data or {}).get("end")
        if not chunk:
            break

    return kept, {
        "pages_fetched": pages,
        "events_scanned": scanned,
        "complete": len(kept) >= want,
    }


@mcp.tool()
async def matrix_list_files(room_id: str, limit: int = 20, from_token: str = "") -> str:
    """Liste les fichiers et images partagés dans un salon.

    Args:
        room_id    : Salon concerné.
        limit      : Nombre max de pièces jointes (défaut 20, max 100).
        from_token : Jeton d'un appel précédent, pour continuer.

    La recherche n'indexe pas les corps des `m.image` et `m.file` : une capture
    ou un rapport posté dans un salon était introuvable autrement qu'en
    remontant la chronologie à la main. Cet outil interroge la chronologie avec
    le filtre `contains_url` du protocole, qui ne retient que les événements
    porteurs d'un média.

    Retourne pour chaque pièce : nom, type, taille, URI mxc://, expéditeur,
    date et permalien, plus `next_token` et `has_more` — comme
    matrix_get_room_messages et matrix_search_messages. `next_token` ne vaut
    quelque chose que s'il y a bien une suite : le serveur rend un jeton même
    quand il n'a plus rien, ce qui faisait boucler un appelant automatique.

    ⚠️ `limit` compte ici des ÉVÉNEMENTS bruts remontés par le filtre
    `contains_url`, pas des pièces jointes retenues : contrairement à
    matrix_get_room_messages, l'outil n'enchaîne pas les pages. Une page peut
    donc rendre moins que `limit` tout en ayant une suite.
    """
    try:
        if not room_id:
            return "Veuillez fournir un room_id."

        params: dict[str, Any] = {
            "dir": "b",
            "limit": str(min(max(1, limit), 100)),
            "filter": json.dumps(
                {"contains_url": True, "types": ["m.room.message"]},
                separators=(",", ":"),
            ),
        }
        if from_token:
            params["from"] = from_token

        data = await _get(f"rooms/{quote(room_id)}/messages", params=params)
        files = []
        for event in (data or {}).get("chunk", []):
            content = event.get("content") or {}
            if not content.get("url"):
                continue
            info = content.get("info") or {}
            files.append({k: v for k, v in {
                "event_id": event.get("event_id"),
                "filename": content.get("filename") or content.get("body"),
                "caption": content.get("body"),
                "msgtype": content.get("msgtype"),
                "mimetype": info.get("mimetype"),
                "size": info.get("size"),
                "content_uri": content.get("url"),
                "sender": event.get("sender"),
                "timestamp": _ts_to_iso(event.get("origin_server_ts")),
                "permalink": _permalink(room_id, event.get("event_id") or ""),
            }.items() if v not in (None, "")})

        chunk = (data or {}).get("chunk") or []
        end = (data or {}).get("end")
        # Un jeton rendu sur une page vide se lit comme « il reste des choses »
        # et fait boucler l'appelant. Le serveur en rend un dans tous les cas.
        has_more = bool(end) and bool(chunk)
        return _compact({
            "room_id": room_id,
            "count": len(files),
            "files": files,
            "next_token": end if has_more else None,
            "has_more": has_more,
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_get_event_context(
    room_id: str,
    event_id: str,
    before: int = 5,
    after: int = 5,
    include_bodyless: bool = False,
) -> str:
    """Lit les messages qui entourent un message donné.

    Args:
        room_id  : Salon concerné.
        event_id : Message autour duquel lire.
        before   : Nombre de messages avant (défaut 5).
        after    : Nombre de messages après (défaut 5).
        include_bodyless : conserver les événements sans corps — réactions,
                   éditions, messages supprimés (défaut : non). Le serveur les
                   compte dans sa fenêtre : juste après un ménage, un contexte
                   de 3 pouvait ne contenir que 3 suppressions, donc rien de
                   lisible.

    Un résultat de recherche isolé se comprend rarement seul. Cet outil rend
    l'échange autour, ce qui évite de relire tout le salon pour situer une
    phrase.

    `before` et `after` comptent des MESSAGES, pas des événements — comme
    `limit` dans matrix_get_room_messages. La limite de `/context` porte, elle,
    sur les événements BRUTS : suppressions, réactions et changements d'état y
    entrent, puis disparaissent au filtrage. Sur un salon où un ménage venait
    d'avoir lieu, `before` sortait à [] alors que des centaines de messages le
    précédaient. L'outil ÉLARGIT donc sa fenêtre auprès du serveur, côté par
    côté, jusqu'à réunir le compte demandé.

    Ce chaînage est borné à `max_pages` pages PAR CÔTÉ. Quand la borne tombe
    avant le compte, `complete` passe à false du côté concerné et
    `truncation_note` le dit : une amputation annoncée, jamais une liste vide
    muette.

    Retourne : `before`, `event`, `after`, `bodyless_included`, et pour chaque
    côté `pages_fetched`, `events_scanned` et `complete`.
    """
    try:
        if not room_id or not event_id:
            return "Veuillez fournir room_id et event_id."

        # L'API prend une seule limite, répartie de part et d'autre.
        span = max(1, min(int(before) + int(after) + 1, 100))
        data = await _get(
            f"rooms/{quote(room_id)}/context/{quote(event_id)}", {"limit": span}
        )
        name = await _room_name(room_id)
        data = data or {}
        # L'API ne prend qu'UNE limite, qu'elle répartit à sa guise : demander
        # 3 avant et 3 après rendait 3 et 4. On tranche côté client, sans quoi
        # les deux côtés ne sont pas comparables — puis on complète auprès du
        # serveur ce que le filtrage a retiré.
        before_events, before_stats = await _collect_context_side(
            room_id, data.get("events_before"), data.get("start"), "b",
            before, name, include_bodyless,
        )
        after_events, after_stats = await _collect_context_side(
            room_id, data.get("events_after"), data.get("end"), "f",
            after, name, include_bodyless,
        )

        payload = {
            "room_id": room_id,
            "room_name": name or None,
            "before": before_events,
            "event": _format_event(data.get("event") or {}, name,
                                   body_chars=BODY_PREVIEW_CHARS),
            "after": after_events,
            "bodyless_included": include_bodyless,
            "pages_fetched": {"before": before_stats["pages_fetched"],
                              "after": after_stats["pages_fetched"]},
            "events_scanned": {"before": before_stats["events_scanned"],
                               "after": after_stats["events_scanned"]},
            "complete": {"before": before_stats["complete"],
                         "after": after_stats["complete"]},
            "max_pages": CONTEXT_FETCH_MAX_PAGES,
        }
        manquants = [c for c in ("before", "after") if not payload["complete"][c]]
        if manquants:
            payload["truncation_note"] = (
                f"Côté(s) {', '.join(manquants)} incomplet(s) : la portion parcourue "
                "ne contenait pas assez de messages, soit que le salon commence là, "
                "soit que le budget de pages ait été atteint. Comparer "
                "events_scanned et pages_fetched à max_pages pour trancher."
            )
        return _compact(payload)
    except Exception as exc:
        return _format_error(exc)


#: Filtre de synchronisation réduit au strict nécessaire pour un état de
#: lecture : /sync rend sinon tout l'historique récent de tous les salons.
#: `m.fully_read` (account data DU salon) porte le repère de lecture, seul
#: moyen de confronter un compteur de notifications à ce qui a été lu.
#: `m.room.member` est chargé paresseusement pour nommer les conversations
#: directes, qui n'ont pas de `m.room.name`.
_UNREAD_SYNC_FILTER = {
    "presence": {"types": []},
    "account_data": {"types": ["m.direct"]},
    "room": {
        "timeline": {"limit": 1},
        "state": {
            "types": ["m.room.name", "m.room.member"],
            "lazy_load_members": True,
        },
        "ephemeral": {"types": []},
        "account_data": {"types": ["m.fully_read"]},
    },
}

#: Ce que les compteurs mesurent réellement, rendu à chaque appel : ils
#: viennent de `unread_notifications` du /sync, c'est-à-dire du décompte que
#: le homeserver tient des événements postérieurs au reçu de lecture et
#: retenus par les règles de notification (m.push_rules). Ce n'est pas
#: toujours ce qu'affiche un client : Element recalcule localement, compte les
#: fils séparément et peut décrémenter à l'affichage. Voir counter_suspect.
_UNREAD_COUNTERS_NOTE = (
    "unread et highlights sont les compteurs du homeserver (unread_notifications "
    "de /sync) : nombre d'événements postérieurs au reçu de lecture et retenus "
    "par vos règles de notification. Ils peuvent différer du badge d'un client "
    "Matrix, qui recalcule côté client, compte les fils à part et décrémente à "
    "l'affichage. Un compteur que le repère de lecture contredit porte "
    "counter_suspect ; total_unread_confirmed exclut ces salons."
)


def _hero_names(room: dict) -> str:
    """Nom d'une conversation directe, tiré des membres mis en avant.

    Un DM n'a pas de `m.room.name` : le serveur désigne à la place des
    « heroes ». Sans cette résolution, la conversation ressortait avec un
    identifiant opaque là où un client affiche « Karine Salazar ».
    """
    heroes = ((room.get("summary") or {}).get("m.heroes")) or []
    if not heroes:
        return ""
    noms = {}
    for event in ((room.get("state") or {}).get("events") or []):
        if event.get("type") == "m.room.member":
            display = (event.get("content") or {}).get("displayname")
            if display:
                noms[event.get("state_key")] = display
    return ", ".join(noms.get(h) or h.lstrip("@").split(":")[0] for h in heroes)


def _last_message_of(room: dict, name: str) -> dict | None:
    """Dernier message de la fenêtre de timeline rendue par /sync, s'il y en a."""
    for event in reversed(((room.get("timeline") or {}).get("events") or [])):
        if event.get("type") == "m.room.message" and (event.get("content") or {}):
            return _format_event(event, name, body_chars=200)
    return None


async def _fetch_last_message(room_id: str, name: str) -> dict | None:
    """Va chercher le dernier message d'un salon quand /sync n'en a pas rendu.

    La fenêtre de timeline est volontairement d'un seul événement : si c'est
    une réaction, une édition ou un changement d'état, le salon ressortait
    sans `last_message` — sur 150 salons sur 200 lors de la recette du
    02/09/2026, et précisément sur ceux qu'on veut lire en premier.
    """
    try:
        data = await _get(
            f"rooms/{quote(room_id)}/messages", params={"dir": "b", "limit": "20"}
        )
        for event in (data or {}).get("chunk", []) or []:
            if event.get("type") == "m.room.message" and (event.get("content") or {}):
                return _format_event(event, name, body_chars=200)
    except Exception as exc:
        logger.warning("Dernier message introuvable pour %s : %s", room_id, exc)
    return None


async def _event_timestamp(room_id: str, event_id: str) -> str | None:
    """Date d'un événement, pour situer un repère de lecture dans le temps."""
    try:
        data = await _get(f"rooms/{quote(room_id)}/event/{quote(event_id)}")
        return _ts_to_iso((data or {}).get("origin_server_ts"))
    except Exception as exc:
        logger.warning("Repère de lecture illisible dans %s : %s", room_id, exc)
        return None


async def _room_unread_counts(room_id: str) -> dict | None:
    """Compteurs de non-lus d'UN salon, par une synchronisation restreinte.

    Relire l'état d'un salon en listant tous les autres échouait dès que le
    compte dépassait le plafond de la liste : un salon qui vient d'être marqué
    lu tombe à zéro, donc en queue de tri, donc hors du lot rendu — et
    `unread_after` sortait à null. Un filtre `rooms` cible le seul salon
    concerné.
    """
    filtre = json.loads(json.dumps(_UNREAD_SYNC_FILTER))
    filtre["room"]["rooms"] = [room_id]
    data = await _get("sync", {
        "timeout": 0,
        "filter": json.dumps(filtre, separators=(",", ":")),
    })
    room = (((data or {}).get("rooms") or {}).get("join") or {}).get(room_id)
    if room is None:
        return None
    counts = room.get("unread_notifications") or {}
    return {
        "unread": int(counts.get("notification_count") or 0),
        "highlights": int(counts.get("highlight_count") or 0),
    }


@mcp.tool()
async def matrix_list_unread(only_unread: bool = True, limit: int = 50) -> str:
    """Salons ayant des messages non lus, avec leur compteur.

    Args:
        only_unread : n'afficher que les salons ayant du non-lu (défaut : oui).
        limit       : nombre max de salons rendus (défaut 50, plafond 1000).
                      L'ancien plafond de 200 tronquait en silence.

    Répond à « qu'est-ce qui est arrivé depuis mon dernier passage ». Rien ne
    le permettait : la recherche et la lecture d'un salon supposent de savoir
    OÙ regarder, ce qui est précisément l'information qui manquait.

    CE QUE LES COMPTEURS MESURENT — et c'est une limite qui ne peut pas être
    levée depuis un client MCP : `unread` et `highlights` sont les compteurs
    que le HOMESERVER tient (`unread_notifications` de /sync), soit le nombre
    d'événements postérieurs à votre reçu de lecture et retenus par vos règles
    de notification. Ce n'est pas nécessairement ce qu'affiche Element : un
    client recalcule localement, compte les fils séparément et décrémente à
    l'affichage. Recette du 02/09/2026 : sept salons sur dix-sept divergeaient,
    dans les deux sens.

    L'outil ne prétend donc plus les aligner, il les rend AUDITABLES :
    - `read_marker_event_id` / `read_marker_at` : jusqu'où vous avez lu ;
    - `counter_suspect` : le dernier message du salon est ANTÉRIEUR au repère
      de lecture, donc il n'y a rien à lire après la marque — c'est le cas d'un
      salon éteint depuis des années qui annonçait 42 non-lus et 3 mentions,
      près de la moitié du total. Le drapeau ne retire rien, il signale ;
    - `total_unread_confirmed` / `total_highlights_confirmed` : les totaux
      débarrassés de ces salons. `total_unread` reste le total brut.

    `last_message` est rendu pour tous les salons : quand la fenêtre de /sync
    ne contient qu'une réaction ou un changement d'état, il est cherché dans la
    chronologie, au plus pour `UNREAD_LOOKUP_MAX` salons par appel
    (`lookups_spent` dit combien l'ont été, `lookups_budget` la borne). Les
    salons non lus sont servis en premier. Les conversations directes sont
    nommées d'après leur interlocuteur, et portent `direct: true`.

    Les salons sont rendus les plus sollicitants en tête.
    """
    try:
        data = await _get("sync", {
            "timeout": 0,
            "filter": json.dumps(_UNREAD_SYNC_FILTER, separators=(",", ":")),
        })
        joined = ((data or {}).get("rooms") or {}).get("join") or {}
        directs: set[str] = set()
        for event in ((data or {}).get("account_data") or {}).get("events", []) or []:
            if event.get("type") == "m.direct":
                for ids in ((event.get("content") or {}).values()):
                    directs.update(ids or [])

        rooms = []
        for room_id, room in joined.items():
            counts = room.get("unread_notifications") or {}
            unread = int(counts.get("notification_count") or 0)
            highlights = int(counts.get("highlight_count") or 0)
            if only_unread and not unread and not highlights:
                continue

            name = ""
            for event in ((room.get("state") or {}).get("events") or []):
                if event.get("type") == "m.room.name":
                    name = (event.get("content") or {}).get("name") or ""
            heroes = _hero_names(room)
            direct = room_id in directs or (
                not name and bool(heroes)
                and int((room.get("summary") or {}).get("m.joined_member_count") or 0) <= 2
            )
            if not name:
                name = heroes or await _room_name(room_id)

            marker = ""
            for event in ((room.get("account_data") or {}).get("events") or []):
                if event.get("type") == "m.fully_read":
                    marker = (event.get("content") or {}).get("event_id") or ""

            rooms.append({
                "room_id": room_id,
                "room_name": name or None,
                "direct": True if direct else None,
                "unread": unread,
                "highlights": highlights,
                "last_message": _last_message_of(room, name),
                "read_marker_event_id": marker or None,
                "permalink": _permalink(room_id),
            })

        rooms.sort(key=lambda r: (-r.get("highlights", 0), -r.get("unread", 0)))
        capped = rooms[:max(1, min(int(limit), UNREAD_ROOMS_MAX))]

        # Ce que /sync ne donne pas se cherche salon par salon, donc sous
        # budget : les salons non lus d'abord, ce sont eux qu'on veut lire.
        budget = UNREAD_LOOKUP_MAX
        besoins = sorted(capped, key=lambda r: -(r["unread"] + r["highlights"]))

        # Dater le repère de lecture passe AVANT le reste : c'est lui qui
        # permet de contredire un compteur. Un compte à plusieurs centaines de
        # salons épuisait sinon le budget sur des salons calmes, et le seul
        # contrôle disponible sur les compteurs ne s'exécutait jamais.
        a_dater = [r for r in besoins
                   if r["read_marker_event_id"] and (r["unread"] or r["highlights"])
                   ][:budget]
        if a_dater:
            dates = await asyncio.gather(*[
                _event_timestamp(r["room_id"], r["read_marker_event_id"])
                for r in a_dater
            ], return_exceptions=True)
            for salon, date in zip(a_dater, dates):
                if isinstance(date, str):
                    salon["read_marker_at"] = date
        budget -= len(a_dater)

        a_chercher = [r for r in besoins if not r["last_message"]][:max(0, budget)]
        if a_chercher:
            trouves = await asyncio.gather(*[
                _fetch_last_message(r["room_id"], r["room_name"] or "")
                for r in a_chercher
            ], return_exceptions=True)
            for salon, trouve in zip(a_chercher, trouves):
                if isinstance(trouve, dict):
                    salon["last_message"] = trouve

        suspects = 0
        for salon in capped:
            marque = salon.get("read_marker_at")
            dernier = (salon.get("last_message") or {}).get("timestamp")
            # Un compteur qui annonce du non-lu alors que le dernier MESSAGE
            # du salon précède le repère de lecture n'a rien à compter après la
            # marque. Réserve faite d'un événement notifiant qui ne soit pas un
            # message — assez rare pour que le signalement vaille mieux qu'un
            # total gonflé en silence, et c'est un drapeau, pas un effacement.
            if (salon["unread"] or salon["highlights"]) and marque and dernier \
                    and dernier <= marque:
                salon["counter_suspect"] = True
                suspects += 1

        suspects_ids = {r["room_id"] for r in capped if r.get("counter_suspect")}
        rendus = [
            {k: v for k, v in r.items() if v not in (None, "")} for r in capped
        ]
        return _compact({
            "count": len(rendus),
            "total_rooms": len(rooms),
            "rooms_with_unread": sum(1 for r in rooms if r.get("unread")),
            "total_unread": sum(r.get("unread", 0) for r in rooms),
            "total_highlights": sum(r.get("highlights", 0) for r in rooms),
            "total_unread_confirmed": sum(
                r.get("unread", 0) for r in rooms if r["room_id"] not in suspects_ids
            ),
            "total_highlights_confirmed": sum(
                r.get("highlights", 0) for r in rooms if r["room_id"] not in suspects_ids
            ),
            "suspect_rooms": suspects,
            "only_unread": only_unread,
            "truncated": len(rooms) > len(capped),
            "lookups_budget": UNREAD_LOOKUP_MAX,
            "lookups_spent": len(a_chercher) + len(a_dater),
            "counters_note": _UNREAD_COUNTERS_NOTE,
            "rooms": rendus,
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_mark_read(room_id: str, event_id: str = "") -> str:
    """Marque un salon comme lu jusqu'à un message donné.

    Confirmation utilisateur requise avant exécution.

    Args:
        room_id  : Salon à marquer.
        event_id : Message jusqu'auquel marquer. Vide = le dernier message du
                   salon, résolu par une lecture préalable.

    Remet à zéro le compteur de matrix_list_unread, et le fait aussi dans vos
    autres clients Matrix : l'état de lecture est partagé côté serveur. Ce
    n'est donc pas une opération locale — marquer lu ici efface le badge
    partout.

    Retourne le message retenu comme repère (`marked_up_to`) et le compteur
    relu après coup (`unread_after`, `highlights_after`), entiers, zéro
    compris. La relecture interroge le SEUL salon concerné : la relire dans la
    liste générale la faisait échouer précisément dans le cas le plus fréquent,
    un salon qu'on vient de traiter et qui retombe donc en queue de liste.

    `read_state_confirmed` dit si cette relecture a abouti. Quand elle échoue,
    `unread_after` vaut null ET `unread_after_note` explique pourquoi : un null
    ne se confond plus avec un salon à zéro non-lu. `success` porte sur
    l'écriture du repère, qui a bien eu lieu dans ce cas.
    """
    try:
        if not room_id:
            return "Veuillez fournir un room_id."

        target = event_id
        if not target:
            recent = await _get(
                f"rooms/{quote(room_id)}/messages", params={"dir": "b", "limit": "5"}
            )
            for event in (recent or {}).get("chunk", []):
                if event.get("type") == "m.room.message" and (event.get("content") or {}):
                    target = event.get("event_id")
                    break
            if not target:
                return _compact({
                    "success": False,
                    "room_id": room_id,
                    "message": "Aucun message récent à retenir comme repère de lecture.",
                })

        await _post(f"rooms/{quote(room_id)}/read_markers", {
            "m.fully_read": target,
            "m.read": target,
        })

        # Relecture ciblée : un marquage sans effet doit se voir.
        counts = None
        echec = ""
        try:
            counts = await _room_unread_counts(room_id)
            if counts is None:
                echec = "le salon n'apparaît pas dans la synchronisation ciblée"
        except Exception as exc:
            # Sans le formateur, une exception httpx vide laissait « cause
            # inconnue » là où le type et la famille du défaut sont connus.
            echec = _format_error_body(exc)

        payload = {
            # L'écriture du repère, elle, a bien eu lieu : la relecture est un
            # contrôle, pas une condition de succès.
            "success": True,
            "room_id": room_id,
            "marked_up_to": target,
            "unread_after": counts["unread"] if counts else None,
            "highlights_after": counts["highlights"] if counts else None,
            "read_state_confirmed": counts is not None,
            "permalink": _permalink(room_id, target),
        }
        if counts is None:
            payload["unread_after_note"] = (
                f"Compteur non relu ({echec or 'cause inconnue'}) : "
                "unread_after à null signifie « inconnu », pas « zéro »."
            )
        return _compact(payload)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_get_reactions(room_id: str, event_id: str) -> str:
    """Lit les réactions posées sur un message, et par qui.

    Args:
        room_id  : Salon concerné.
        event_id : Message annoté.

    On savait poser une réaction, pas la relire — alors que c'est précisément
    ce qui sert à savoir si une alerte a été acquittée, et par quel membre.

    Retourne les réactions groupées par emoji, avec la liste des expéditeurs,
    et le détail événement par événement (`reaction_event_id` est celui qu'il
    faut pour retirer une réaction).
    """
    try:
        if not room_id or not event_id:
            return "Veuillez fournir room_id et event_id."

        data = await _get(
            f"_matrix/client/v1/rooms/{quote(room_id)}/relations/"
            f"{quote(event_id)}/m.annotation",
            {"limit": 100},
        )
        grouped: dict[str, dict] = {}
        details = []
        for e in (data or {}).get("chunk", []):
            if not isinstance(e, dict):
                continue
            key = ((e.get("content") or {}).get("m.relates_to") or {}).get("key") or "?"
            sender = e.get("sender")
            entry = grouped.setdefault(key, {"emoji": key, "count": 0, "senders": []})
            entry["count"] += 1
            if sender and sender not in entry["senders"]:
                entry["senders"].append(sender)
            details.append({
                "reaction_event_id": e.get("event_id"),
                "emoji": key,
                "sender": sender,
                "timestamp": _ts_to_iso(e.get("origin_server_ts")),
            })

        return _compact({
            "room_id": room_id,
            "event_id": event_id,
            "count": len(details),
            "by_emoji": sorted(grouped.values(), key=lambda g: -g["count"]),
            "reactions": details,
            "next_batch": (data or {}).get("next_batch"),
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def matrix_get_permalink(room_id: str, event_id: str = "") -> str:
    """Construit un lien matrix.to vers un salon ou un message.

    Args:
        room_id  : Salon concerné.
        event_id : Message précis (optionnel).

    Sans paramètre supplémentaire ni appel réseau. Sert à citer un échange
    depuis un compte rendu ou une carte Kanboard : un event_id nu n'est
    cliquable nulle part.
    """
    if not room_id:
        return "Veuillez fournir un room_id."
    return _compact({
        "room_id": room_id,
        "event_id": event_id or None,
        "permalink": _permalink(room_id, event_id),
    })


def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
