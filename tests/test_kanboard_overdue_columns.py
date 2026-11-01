"""kanboard_my_overdue(with_columns=True) : un getBoard par projet, budget annoncé."""

import asyncio
import json
import os
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ.setdefault("KANBOARD_URL", "https://kanboard.example.test/jsonrpc.php")
os.environ.setdefault("KANBOARD_USER", "tester")
os.environ.setdefault("KANBOARD_TOKEN", "test-token")

from kanboard_mcp import server  # noqa: E402
from kanboard_mcp.server import kanboard_my_overdue  # noqa: E402

PAST = str(int(datetime(2026, 1, 1).timestamp()))


def _rows() -> list[dict]:
    """Lignes telles que les rend getOverdueTasks : ni colonne ni swimlane."""
    return [
        {"id": "10", "title": "Carte A", "project_id": "262", "project_name": "Pilotage",
         "owner_id": "84", "assignee_name": "Agent", "date_due": PAST},
        {"id": "11", "title": "Carte B", "project_id": "262", "project_name": "Pilotage",
         "owner_id": "84", "assignee_name": "Agent", "date_due": PAST},
        {"id": "20", "title": "Carte C", "project_id": "263", "project_name": "Autre",
         "owner_id": "84", "assignee_name": "Agent", "date_due": PAST},
        {"id": "30", "title": "Carte d'un collegue", "project_id": "264", "project_name": "Tiers",
         "owner_id": "4", "assignee_name": "Alice", "date_due": PAST},
    ]


BOARDS = {
    262: [
        {"name": "Actions &amp; suivi", "columns": [
            {"title": "En cours", "tasks": [{"id": "10"}]},
            {"title": "Suspens", "tasks": [{"id": "99"}]},
        ]},
        {"name": "Backlog", "columns": [{"title": "A trier", "tasks": [{"id": 11}]}]},
    ],
    263: [{"name": "Default", "columns": [{"title": "Fait", "tasks": [{"id": "20"}]}]}],
}


def _dispatcher(calls: list, board=None):
    async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
        calls.append((method, (params or {}).get("project_id"), as_user))
        if method == "getMe":
            return {"id": 84, "username": "bot", "name": "Agent"}
        if method == "getOverdueTasks":
            return _rows()
        if method == "getBoard":
            return await board(params["project_id"]) if board else BOARDS[params["project_id"]]
        raise AssertionError(f"methode inattendue : {method}")

    return _dispatch


async def _overdue(calls: list, board=None, **kwargs) -> dict:
    with patch("kanboard_mcp.server._my_id_cache", {}), patch(
        "kanboard_mcp.server.kb_call", new_callable=AsyncMock, side_effect=_dispatcher(calls, board)
    ):
        return json.loads(await kanboard_my_overdue(as_user="agent", **kwargs))


async def test_default_stays_a_single_call_with_the_same_keys() -> None:
    calls: list = []
    data = await _overdue(calls)

    assert not [c for c in calls if c[0] == "getBoard"]
    assert set(data) == {"as_user", "source", "overdue_count", "tasks"}
    assert "column_resolved" not in data["tasks"][0]


async def test_one_board_per_concerned_project_resolves_column_and_swimlane() -> None:
    calls: list = []
    data = await _overdue(calls, with_columns=True)

    assert sorted(c[1] for c in calls if c[0] == "getBoard") == [262, 263]
    assert all(c[2] == data["as_user"] for c in calls if c[0] == "getBoard")
    tasks = {t["id"]: t for t in data["tasks"]}
    assert (tasks["10"]["column_name"], tasks["10"]["swimlane_name"]) == ("En cours", "Actions & suivi")
    assert (tasks["11"]["column_name"], tasks["11"]["swimlane_name"]) == ("A trier", "Backlog")
    assert (tasks["20"]["column_name"], tasks["20"]["swimlane_name"]) == ("Fait", "Default")
    assert all(t["column_resolved"] is True for t in data["tasks"])
    assert data["columns_complete"] is True
    assert data["columns_unresolved_tasks"] == 0
    assert data["columns_unresolved_projects"] == []
    assert data["columns_board_calls"] == 2
    assert data["columns_budget_seconds"] == server.OVERDUE_COLUMNS_BUDGET_SECONDS


async def test_an_unreadable_project_gives_a_partial_result_that_says_so() -> None:
    async def board(project_id):
        if project_id == 263:
            raise RuntimeError("refus du serveur")
        return BOARDS[project_id]

    data = await _overdue([], board, with_columns=True)

    tasks = {t["id"]: t for t in data["tasks"]}
    assert tasks["10"]["column_resolved"] is True
    assert tasks["20"]["column_resolved"] is False
    assert tasks["20"]["column_name"] == ""
    assert data["columns_complete"] is False
    assert data["columns_unresolved_tasks"] == 1
    assert data["columns_unresolved_projects"][0]["project_id"] == 263
    assert "refus du serveur" in data["columns_unresolved_projects"][0]["reason"]


async def test_the_time_budget_stops_the_wait_and_is_announced() -> None:
    async def board(project_id):
        if project_id == 263:
            await asyncio.sleep(30)
        return BOARDS[project_id]

    with patch.object(server, "OVERDUE_COLUMNS_BUDGET_SECONDS", 0.05):
        data = await _overdue([], board, with_columns=True)

    assert data["overdue_count"] == 3
    assert data["columns_complete"] is False
    assert data["columns_unresolved_projects"] == [{"project_id": 263, "reason": "budget"}]
    assert data["columns_elapsed_seconds"] < 5
    assert {t["id"]: t["column_resolved"] for t in data["tasks"]} == {"10": True, "11": True, "20": False}


async def test_a_card_missing_from_its_board_is_counted_as_unresolved() -> None:
    async def board(project_id):
        return [{"name": "Default", "columns": []}] if project_id == 263 else BOARDS[project_id]

    data = await _overdue([], board, with_columns=True)

    assert data["columns_complete"] is False
    assert data["columns_unresolved_tasks"] == 1
    assert data["columns_unresolved_projects"] == []


async def test_rows_that_already_carry_their_column_need_no_board() -> None:
    calls: list = []
    rows = [dict(_rows()[0], column_name="En cours", swimlane_name="Default")]

    async def _dispatch(method, params=None, as_user=""):
        calls.append(method)
        return {"id": 84, "username": "bot", "name": "Agent"} if method == "getMe" else rows

    with patch("kanboard_mcp.server._my_id_cache", {}), patch(
        "kanboard_mcp.server.kb_call", new_callable=AsyncMock, side_effect=_dispatch
    ):
        data = json.loads(await kanboard_my_overdue(as_user="agent", with_columns=True))

    assert "getBoard" not in calls
    assert data["columns_complete"] is True
    assert data["columns_board_calls"] == 0
