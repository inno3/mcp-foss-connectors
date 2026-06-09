"""Tests unitaires offline pour le connecteur Matrix.

Tous les appels HTTP sont mockés : la suite tourne sans réseau ni token réel.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("MATRIX_HOMESERVER_URL", "https://matrix.example.test")
os.environ.setdefault("MATRIX_ACCESS_TOKEN", "syt_xxxx")

import matrix_mcp.server as mx  # noqa: E402
from matrix_mcp.server import (  # noqa: E402
    _matrix_url,
    _ts_to_iso,
    matrix_get_room_messages,
    matrix_send_message,
    matrix_whoami,
)


class TestHelpers:
    def test_matrix_url_builds_csv3_path(self) -> None:
        base = mx.MATRIX_HOMESERVER_URL
        assert _matrix_url("account/whoami") == (
            f"{base}/_matrix/client/v3/account/whoami"
        )
        assert _matrix_url("/joined_rooms").endswith("/v3/joined_rooms")

    def test_ts_to_iso_none(self) -> None:
        assert _ts_to_iso(None) is None

    def test_ts_to_iso_converts_ms(self) -> None:
        # 0 ms = epoch
        assert _ts_to_iso(0) == "1970-01-01T00:00:00Z"


@pytest.mark.asyncio
class TestWhoami:
    async def test_returns_compact_identity(self) -> None:
        with patch(
            "matrix_mcp.server._get",
            new_callable=AsyncMock,
            return_value={"user_id": "@bot:example.test", "device_id": "DEV"},
        ) as mock_get:
            raw = await matrix_whoami()
        assert mock_get.call_args[0][0] == "account/whoami"
        data = json.loads(raw)
        assert data["user_id"] == "@bot:example.test"
        assert data["is_guest"] is False


@pytest.mark.asyncio
class TestGetRoomMessages:
    async def test_filters_message_events_only(self) -> None:
        chunk = {
            "chunk": [
                {
                    "type": "m.room.message",
                    "event_id": "$1",
                    "sender": "@a:x",
                    "content": {"body": "hi", "msgtype": "m.text"},
                    "origin_server_ts": 0,
                },
                {"type": "m.room.member", "event_id": "$2"},
            ]
        }
        with patch(
            "matrix_mcp.server._get", new_callable=AsyncMock, return_value=chunk
        ) as mock_get:
            raw = await matrix_get_room_messages("!room:x", limit=5)
        path = mock_get.call_args[0][0]
        assert path == "rooms/!room:x/messages"
        assert mock_get.call_args[1]["params"]["dir"] == "b"
        data = json.loads(raw)
        assert data["count"] == 1
        assert data["messages"][0]["body"] == "hi"

    async def test_limit_clamped(self) -> None:
        with patch(
            "matrix_mcp.server._get",
            new_callable=AsyncMock,
            return_value={"chunk": []},
        ) as mock_get:
            await matrix_get_room_messages("!r:x", limit=999)
        assert mock_get.call_args[1]["params"]["limit"] == "100"


@pytest.mark.asyncio
class TestSendMessage:
    async def test_puts_text_event(self) -> None:
        with patch(
            "matrix_mcp.server._put",
            new_callable=AsyncMock,
            return_value={"event_id": "$evt"},
        ) as mock_put:
            raw = await matrix_send_message("!r:x", "hello")
        path, body = mock_put.call_args[0][0], mock_put.call_args[0][1]
        assert path.startswith("rooms/!r:x/send/m.room.message/")
        assert body == {"msgtype": "m.text", "body": "hello"}
        assert json.loads(raw)["event_id"] == "$evt"
