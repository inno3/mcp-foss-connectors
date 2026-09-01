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
from typing import Any
from urllib.parse import quote

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
_RETRY_DELAY = 1.0


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
    """Appel HTTP Matrix avec retry et backoff exponentiel."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=True, timeout=30) as client:
                logger.info("%s %s (attempt %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=_headers(), **kwargs)
                resp.raise_for_status()
                if resp.status_code == 204 or not resp.content:
                    return {}
                return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s in %.1fs: %s", method, url, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s (HTTP %d) in %.1fs", url, exc.response.status_code, delay)
                await asyncio.sleep(delay)
            else:
                body = exc.response.text[:500] if exc.response.content else ""
                raise RuntimeError(
                    f"Erreur Matrix {exc.response.status_code} — {method} {url} : {body}"
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

    Retourne les messages, `next_token`, `pages_fetched` et `has_more`.
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
            "(room_id, exclude_rooms), appliqués côté serveur — mesuré le "
            "01/09/2026 : 2613 sans exclusion, 2050 avec. Il IGNORE en revanche "
            "le filtre d'expéditeur et la fenêtre de dates, et compte les "
            "messages supprimés. Voir index_matches_reliable."
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
        return f"Erreur : {exc}"


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
        return f"Erreur : {exc}"


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
        return f"Erreur : {exc}"


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
        return f"Erreur : {exc}"


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
        return f"Erreur : {exc}"


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
        return f"Erreur : {exc}"


def _context_side(events: Any, want: int, room_name: str, include_bodyless: bool) -> list:
    """Un côté du contexte, filtré puis tranché au nombre demandé.

    Le filtrage précède la coupe : trancher d'abord rendrait « 3 messages »
    dont deux réactions.
    """
    kept = []
    for e in (events or []):
        entry = _format_event(e, room_name, body_chars=BODY_PREVIEW_CHARS)
        if not include_bodyless and not entry.get("body"):
            continue
        kept.append(entry)
        if len(kept) >= max(0, int(want)):
            break
    return kept


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
    date et permalien.
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

        return _compact({
            "room_id": room_id,
            "count": len(files),
            "files": files,
            "next_token": (data or {}).get("end"),
        })
    except Exception as exc:
        return f"Erreur : {exc}"


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

    Retourne les messages avant, le message lui-même, et ceux d'après — chaque
    côté tranché au nombre demandé, y compris les événements sans corps
    (réactions, éditions) que le serveur inclut dans son décompte.
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
        # L'API ne prend qu'UNE limite, qu'elle répartit à sa guise : demander
        # 3 avant et 3 après rendait 3 et 4. On tranche côté client, sans quoi
        # les deux côtés ne sont pas comparables.
        return _compact({
            "room_id": room_id,
            "room_name": name or None,
            "before": _context_side(
                (data or {}).get("events_before"), before, name, include_bodyless
            ),
            "event": _format_event((data or {}).get("event") or {}, name,
                                   body_chars=BODY_PREVIEW_CHARS),
            "after": _context_side(
                (data or {}).get("events_after"), after, name, include_bodyless
            ),
            "bodyless_included": include_bodyless,
        })
    except Exception as exc:
        return f"Erreur : {exc}"


#: Filtre de synchronisation réduit au strict nécessaire pour un état de
#: lecture : /sync rend sinon tout l'historique récent de tous les salons.
_UNREAD_SYNC_FILTER = {
    "presence": {"types": []},
    "account_data": {"types": []},
    "room": {
        "timeline": {"limit": 1},
        "state": {"types": ["m.room.name"], "lazy_load_members": True},
        "ephemeral": {"types": []},
        "account_data": {"types": []},
    },
}


@mcp.tool()
async def matrix_list_unread(only_unread: bool = True, limit: int = 50) -> str:
    """Salons ayant des messages non lus, avec leur compteur.

    Args:
        only_unread : n'afficher que les salons ayant du non-lu (défaut : oui).
        limit       : nombre max de salons rendus (défaut 50).

    Répond à « qu'est-ce qui est arrivé depuis mon dernier passage ». Rien ne
    le permettait : la recherche et la lecture d'un salon supposent de savoir
    OÙ regarder, ce qui est précisément l'information qui manquait.

    `unread` compte les notifications, `highlights` les mentions directes —
    c'est ce second chiffre qui distingue une alerte automatique d'un message
    qui vous vise. Les salons sont rendus les plus sollicitants en tête.

    L'état de lecture n'existe que côté serveur : il vient de la même
    synchronisation qu'utilise un client Matrix, restreinte ici au dernier
    message par salon pour ne pas rapatrier l'historique.
    """
    try:
        data = await _get("sync", {
            "timeout": 0,
            "filter": json.dumps(_UNREAD_SYNC_FILTER, separators=(",", ":")),
        })
        joined = ((data or {}).get("rooms") or {}).get("join") or {}

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
            if not name:
                name = await _room_name(room_id)

            last = None
            timeline = ((room.get("timeline") or {}).get("events") or [])
            for event in reversed(timeline):
                if event.get("type") == "m.room.message":
                    last = _format_event(event, name, body_chars=200)
                    break

            rooms.append({k: v for k, v in {
                "room_id": room_id,
                "room_name": name or None,
                "unread": unread,
                "highlights": highlights,
                "last_message": last,
                "permalink": _permalink(room_id),
            }.items() if v not in (None, "")})

        rooms.sort(key=lambda r: (-r.get("highlights", 0), -r.get("unread", 0)))
        capped = rooms[:max(1, min(int(limit), 200))]
        return _compact({
            "count": len(capped),
            "rooms_with_unread": sum(1 for r in rooms if r.get("unread")),
            "total_unread": sum(r.get("unread", 0) for r in rooms),
            "total_highlights": sum(r.get("highlights", 0) for r in rooms),
            "only_unread": only_unread,
            "truncated": len(rooms) > len(capped),
            "rooms": capped,
        })
    except Exception as exc:
        return f"Erreur : {exc}"


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

    Retourne le message retenu comme repère et le compteur relu après coup.
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

        # Relecture : un marquage sans effet doit se voir.
        remaining = None
        try:
            state = json.loads(await matrix_list_unread(only_unread=False, limit=200))
            for room in state.get("rooms", []):
                if room.get("room_id") == room_id:
                    remaining = room.get("unread", 0)
                    break
        except Exception:
            pass

        return _compact({
            "success": remaining in (0, None),
            "room_id": room_id,
            "marked_up_to": target,
            "unread_after": remaining,
            "permalink": _permalink(room_id, target),
        })
    except Exception as exc:
        return f"Erreur : {exc}"


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
        return f"Erreur : {exc}"


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
