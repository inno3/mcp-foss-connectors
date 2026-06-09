"""Tests unitaires offline pour le connecteur HedgeDoc.

Tous les appels HTTP sont mockés : la suite tourne sans réseau ni credentials réels.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("HEDGEDOC_URL", "https://pad.example.test")
os.environ.setdefault("HEDGEDOC_API_TOKEN", "hd-token-xxxx")

from hedgedoc_mcp.server import (  # noqa: E402
    _dumps,
    _extract_note_id_from_url,
    hedgedoc_get_note,
    hedgedoc_get_note_info,
)


class _FakeResponse:
    def __init__(self, status_code=200, text="", payload=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class TestHelpers:
    def test_extract_note_id_strips_base_and_anchor(self) -> None:
        assert _extract_note_id_from_url("/aBC123#") == "aBC123"
        assert _extract_note_id_from_url("https://pad.example.test/xYz789") == "xYz789"
        assert _extract_note_id_from_url("/s/mon-alias") == "s/mon-alias"

    def test_dumps_compact(self) -> None:
        assert _dumps({"a": 1}) == '{"a":1}'


@pytest.mark.asyncio
class TestGetNote:
    async def test_returns_markdown(self) -> None:
        with patch(
            "hedgedoc_mcp.server._hd_request",
            new_callable=AsyncMock,
            return_value=_FakeResponse(status_code=200, text="# Titre\ncorps"),
        ) as mock_req:
            raw = await hedgedoc_get_note("aBC123")
        assert mock_req.call_args[0][1] == "/aBC123/download"
        data = json.loads(raw)
        assert data["content"].startswith("# Titre")
        assert data["truncated"] is False

    async def test_redirect_is_reported(self) -> None:
        with patch(
            "hedgedoc_mcp.server._hd_request",
            new_callable=AsyncMock,
            return_value=_FakeResponse(
                status_code=302, headers={"location": "/login"}
            ),
        ):
            raw = await hedgedoc_get_note("priv")
        assert "error" in json.loads(raw)


@pytest.mark.asyncio
class TestGetNoteInfo:
    async def test_extracts_metadata(self) -> None:
        payload = {
            "title": "Note",
            "tags": ["a"],
            "createTime": "2026-01-01",
            "viewcount": 7,
        }
        with patch(
            "hedgedoc_mcp.server._hd_request",
            new_callable=AsyncMock,
            return_value=_FakeResponse(status_code=200, payload=payload),
        ) as mock_req:
            raw = await hedgedoc_get_note_info("aBC123")
        assert mock_req.call_args[0][1] == "/aBC123/info"
        data = json.loads(raw)
        assert data["title"] == "Note"
        assert data["viewcount"] == 7
