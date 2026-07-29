"""Tests unitaires offline pour le connecteur Kanboard.

Tous les appels JSON-RPC sont mockés : la suite tourne sans réseau ni token réel.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("KANBOARD_URL", "https://kb.example.test/jsonrpc.php")
os.environ.setdefault("KANBOARD_USER", "alice")
os.environ.setdefault("KANBOARD_TOKEN", "tok-primary")

from kanboard_mcp.server import (  # noqa: E402
    _clamp_limit,
    _dumps,
    _resolve_account,
    _ts_to_date,
    kanboard_list_projects,
)


class TestHelpers:
    def test_resolve_account_aliases(self) -> None:
        assert _resolve_account("agent") == "agent"
        assert _resolve_account("ai") == "agent"
        assert _resolve_account("bot") == "agent"
        assert _resolve_account("alt") == "agent"
        assert _resolve_account("me") == "primary"
        assert _resolve_account("human") == "primary"
        assert _resolve_account("user") == "primary"
        assert _resolve_account("") == "primary"
        assert _resolve_account("claude") == "agent"

    def test_resolve_account_rejects_unknown(self) -> None:
        """Une valeur inconnue doit échouer, pas retomber sur 'primary'.

        Le repli silencieux signait les écritures avec le compte humain alors que
        l'appelant demandait le compte agent : la traçabilité Kanboard devenait
        fausse sans que rien ne le signale.
        """
        with pytest.raises(ValueError, match="as_user invalide"):
            _resolve_account("unknown")
        with pytest.raises(ValueError, match="as_user invalide"):
            _resolve_account("agnet")

    def test_clamp_limit(self) -> None:
        assert _clamp_limit(0) == 1
        assert _clamp_limit(99999) <= 100
        assert _clamp_limit(10) == 10

    def test_ts_to_date_empty(self) -> None:
        assert _ts_to_date("0") == ""
        assert _ts_to_date(None) == ""

    def test_ts_to_date_converts(self) -> None:
        # 2021-01-01 00:00:00 UTC-ish — assert it formats to a YYYY-MM-DD string
        out = _ts_to_date(1609459200)
        assert len(out) == 10 and out[4] == "-"

    def test_dumps_compact(self) -> None:
        assert _dumps({"a": 1}) == '{"a":1}'


@pytest.mark.asyncio
class TestListProjects:
    async def test_filters_fields_and_clamps(self) -> None:
        payload = [
            {
                "id": "3",
                "name": "Pilotage",
                "identifier": "PIL",
                "description": "x" * 500,
                "is_active": "1",
                "nb_open_tasks": 4,
                "secret": "drop",
            }
        ]
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=payload,
        ) as mock_call:
            raw = await kanboard_list_projects(limit=10)
        assert mock_call.call_args[0][0] == "getAllProjects"
        data = json.loads(raw)
        assert data[0]["id"] == "3"
        assert len(data[0]["description"]) == 200
        assert "secret" not in data[0]

    async def test_empty_returns_empty_list(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=None,
        ):
            raw = await kanboard_list_projects()
        assert json.loads(raw) == []
