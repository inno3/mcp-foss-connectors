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
_RETRY_DELAY = 1.0


def _matrix_url(path: str) -> str:
    """Construit l'URL complète d'un endpoint Matrix Client-Server v3."""
    return f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/{path.lstrip('/')}"


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
async def matrix_get_room_messages(room_id: str, limit: int = 20) -> str:
    """Lit les derniers messages d'un salon Matrix.

    Args:
        room_id : Identifiant du salon (ex: !abc123:matrix.example.org).
        limit   : Nombre de messages à récupérer (défaut 20, max 100).

    Retourne : liste de {sender, body, timestamp, event_id, type}.
    """
    limit = min(max(1, limit), 100)
    data = await _get(
        f"rooms/{room_id}/messages",
        params={"dir": "b", "limit": str(limit)},
    )

    messages = []
    for event in data.get("chunk", []):
        if event.get("type") != "m.room.message":
            continue
        content = event.get("content", {})
        messages.append({
            "event_id": event.get("event_id"),
            "sender": event.get("sender"),
            "body": content.get("body"),
            "msgtype": content.get("msgtype"),
            "timestamp": _ts_to_iso(event.get("origin_server_ts")),
        })

    return _compact({"messages": messages, "count": len(messages)})


@mcp.tool()
async def matrix_send_message(room_id: str, message: str) -> str:
    """Envoie un message texte brut dans un salon Matrix.

    Args:
        room_id : Identifiant du salon (ex: !abc123:matrix.example.org).
        message : Contenu du message à envoyer.

    Retourne : event_id du message envoyé.
    """
    txn_id = str(uuid.uuid4()).replace("-", "")
    data = await _put(
        f"rooms/{room_id}/send/m.room.message/{txn_id}",
        {"msgtype": "m.text", "body": message},
    )
    return _compact({"event_id": data.get("event_id"), "room_id": room_id})


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
async def matrix_search_messages(query: str, room_id: str = "") -> str:
    """Recherche des messages dans les salons Matrix.

    Args:
        query   : Terme de recherche.
        room_id : Restreindre la recherche à un salon spécifique (optionnel).

    Retourne : liste de {event_id, sender, body, room_id, timestamp}.
    """
    search_body: dict = {
        "search_categories": {
            "room_events": {
                "search_term": query,
                "order_by": "recent",
            }
        }
    }
    if room_id:
        search_body["search_categories"]["room_events"]["filter"] = {
            "rooms": [room_id]
        }

    data = await _post("search", search_body)

    results_raw = (
        data.get("search_categories", {})
            .get("room_events", {})
            .get("results", [])
    )

    results = []
    for item in results_raw:
        event = item.get("result", {})
        content = event.get("content", {})
        results.append({
            "event_id": event.get("event_id"),
            "sender": event.get("sender"),
            "body": content.get("body"),
            "room_id": event.get("room_id"),
            "timestamp": _ts_to_iso(event.get("origin_server_ts")),
        })

    return _compact({"results": results, "count": len(results)})


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

def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
