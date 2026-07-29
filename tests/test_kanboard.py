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
    _fold,
    _resolve_account,
    _ts_to_date,
    kanboard_get_board,
    kanboard_list_projects,
    kanboard_my_dashboard,
)


def _projects(count: int) -> list[dict]:
    """Fabrique `count` projets bruts tels que les renvoie getAllProjects."""
    return [
        {
            "id": str(i),
            "name": f"Projet {i}",
            "identifier": f"P{i}",
            "description": "",
            "is_active": "1",
            "nb_open_tasks": 0,
        }
        for i in range(1, count + 1)
    ]


def _board(task_count: int) -> list[dict]:
    """Fabrique un board getBoard a une swimlane et une colonne."""
    return [
        {
            "name": "Default",
            "columns": [
                {
                    "id": 5,
                    "title": "En cours",
                    "tasks": [
                        {
                            "id": i,
                            "title": f"Carte {i}",
                            "assignee_name": "Alice",
                            "date_due": "1609459200",
                            "priority": 2,
                            "category_name": "Support",
                        }
                        for i in range(1, task_count + 1)
                    ],
                }
            ],
        }
    ]


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

    async def test_bad_as_user_surfaces_as_tool_error(self) -> None:
        """La ValueError doit ressortir en message d'outil, pas en crash MCP."""
        result = await kanboard_my_dashboard(as_user="agnet")
        assert "as_user invalide" in result
        assert "agnet" in result

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

    def test_fold_strips_case_and_accents(self) -> None:
        assert _fold("Référentiel Qualité") == "referentiel qualite"
        assert _fold(None) == ""


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
        assert data["projects"][0]["id"] == "3"
        assert len(data["projects"][0]["description"]) == 200
        assert "secret" not in data["projects"][0]

    async def test_empty_returns_empty_page(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=None,
        ):
            raw = await kanboard_list_projects()
        data = json.loads(raw)
        assert data["projects"] == []
        assert data["total_available"] == 0
        assert data["has_more"] is False

    async def test_reports_total_before_pagination(self) -> None:
        """Le decoupage doit rester visible : total_available compte tout."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_projects(150),
        ):
            data = json.loads(await kanboard_list_projects(limit=20))
        assert data["total_available"] == 150
        assert data["count"] == 20
        assert data["has_more"] is True

    async def test_offset_reaches_projects_past_the_first_page(self) -> None:
        """Sans offset, les projets au-dela du 100e (MAX_LIMIT) etaient inatteignables."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_projects(150),
        ):
            data = json.loads(await kanboard_list_projects(limit=20, offset=100))
        assert data["offset"] == 100
        assert [p["id"] for p in data["projects"]] == [str(i) for i in range(101, 121)]

    async def test_last_page_has_no_more(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_projects(150),
        ):
            data = json.loads(await kanboard_list_projects(limit=20, offset=140))
        assert data["count"] == 10
        assert data["has_more"] is False

    async def test_name_filter_ignores_case_and_accents(self) -> None:
        payload = [
            {"id": "1", "name": "Référentiel Qualité", "identifier": "REF"},
            {"id": "2", "name": "Pilotage", "identifier": "PIL"},
        ]
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=payload,
        ):
            data = json.loads(await kanboard_list_projects(name_filter="REFERENTIEL"))
        assert data["total_available"] == 1
        assert data["projects"][0]["id"] == "1"

    async def test_name_filter_matches_identifier(self) -> None:
        payload = [
            {"id": "1", "name": "Référentiel Qualité", "identifier": "REF"},
            {"id": "2", "name": "Pilotage", "identifier": "PIL"},
        ]
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=payload,
        ):
            data = json.loads(await kanboard_list_projects(name_filter="pil"))
        assert [p["id"] for p in data["projects"]] == ["2"]

    async def test_name_filter_without_match_is_empty(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_projects(5),
        ):
            data = json.loads(await kanboard_list_projects(name_filter="introuvable"))
        assert data["total_available"] == 0
        assert data["projects"] == []


@pytest.mark.asyncio
class TestGetBoard:
    async def test_exposes_due_date_priority_and_category(self) -> None:
        """L'echeance etait jetee alors que getBoard la renvoie (date_due)."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_board(1),
        ):
            data = json.loads(await kanboard_get_board(project_id=262))
        task = data[0]["columns"][0]["tasks"][0]
        assert task["due_date"] == _ts_to_date("1609459200")
        assert task["due_date"] != ""
        assert task["priority"] == 2
        assert task["category_name"] == "Support"

    async def test_truncation_is_signalled(self) -> None:
        """Une colonne tronquee doit le dire, sinon elle se lit comme complete."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_board(12),
        ):
            data = json.loads(await kanboard_get_board(project_id=262))
        col = data[0]["columns"][0]
        assert col["truncated"] is True
        assert col["shown"] == 10
        assert col["total"] == 12
        assert len(col["tasks"]) == 10
        assert col["task_count"] == 12

    async def test_no_truncation_marker_when_column_is_complete(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_board(3),
        ):
            data = json.loads(await kanboard_get_board(project_id=262))
        col = data[0]["columns"][0]
        assert "truncated" not in col
        assert len(col["tasks"]) == 3

    async def test_tasks_per_column_raises_the_ceiling(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_board(12),
        ):
            data = json.loads(
                await kanboard_get_board(project_id=262, tasks_per_column=50)
            )
        col = data[0]["columns"][0]
        assert len(col["tasks"]) == 12
        assert "truncated" not in col

    async def test_tasks_per_column_is_clamped(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_board(12),
        ):
            data = json.loads(
                await kanboard_get_board(project_id=262, tasks_per_column=0)
            )
        col = data[0]["columns"][0]
        assert len(col["tasks"]) == 1
        assert col["truncated"] is True
