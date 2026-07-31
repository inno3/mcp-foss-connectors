"""Tests unitaires offline pour le connecteur Kanboard.

Tous les appels JSON-RPC sont mockés : la suite tourne sans réseau ni token réel.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("KANBOARD_URL", "https://kb.example.test/jsonrpc.php")
os.environ.setdefault("KANBOARD_USER", "alice")
os.environ.setdefault("KANBOARD_TOKEN", "tok-primary")
# Deux comptes configures : c'est le mode nominal du connecteur, et la seule
# configuration ou la contrainte d'auteur sur les commentaires est jouable.
os.environ.setdefault("KANBOARD_USER_ALT", "bot")
os.environ.setdefault("KANBOARD_TOKEN_ALT", "tok-agent")

from kanboard_mcp.server import (  # noqa: E402
    ACTIVITY_API_CAP,
    COMMENT_PREVIEW_CHARS,
    _clamp_limit,
    _dumps,
    _fold,
    _resolve_account,
    _ts_to_date,
    kanboard_check_project_access,
    kanboard_get_board,
    kanboard_get_comment,
    kanboard_get_task,
    kanboard_list_comments,
    kanboard_list_projects,
    kanboard_my_dashboard,
    kanboard_recent_activity,
    kanboard_remove_comment,
    kanboard_search_tasks,
    kanboard_update_comment,
)


def _http_403() -> httpx.HTTPStatusError:
    """Reproduit le 403 que Kanboard rend sur getAllProjects a un non-admin."""
    request = httpx.Request("POST", "https://kb.example.test/jsonrpc.php")
    return httpx.HTTPStatusError(
        "Forbidden", request=request, response=httpx.Response(403, request=request)
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


def _comment(comment_id: int = 7, text: str = "Texte initial", user_id: str = "84") -> dict:
    """Fabrique un commentaire brut tel que le renvoie getComment/getAllComments."""
    return {
        "id": str(comment_id),
        "task_id": "42",
        "user_id": user_id,
        "username": "bot",
        "name": "Agent IA",
        "date_creation": "1609459200",
        "date_modification": "1609545600",
        "comment": text,
        "secret": "drop",
    }


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


def _task_calls(comments: list[dict]):
    """Aiguille kb_call par methode pour les tests de kanboard_get_task."""
    task = {
        "id": 42,
        "title": "Carte de test",
        "description": "d",
        "project_id": 262,
        "project_name": "Pilotage",
        "column_name": "En cours",
        "swimlane_name": "Default",
        "assignee_name": "Alice",
        "creator_name": "Alice",
    }

    async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
        if method == "getTask":
            return task
        if method == "getAllComments":
            return comments
        if method == "getAllSubtasks":
            return []
        raise AssertionError(f"methode inattendue : {method}")

    return _dispatch


@pytest.mark.asyncio
class TestGetTaskComments:
    async def test_long_comment_is_flagged_as_truncated(self) -> None:
        """Le [:500] muet faisait relire un commentaire coupe comme complet."""
        long_text = "x" * (COMMENT_PREVIEW_CHARS + 140)
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_task_calls([_comment(text=long_text)]),
        ):
            data = json.loads(await kanboard_get_task(task_id=42))
        entry = data["comments"][0]
        assert entry["comment_truncated"] is True
        assert entry["comment_full_length"] == COMMENT_PREVIEW_CHARS + 140
        assert len(entry["content"]) == COMMENT_PREVIEW_CHARS

    async def test_short_comment_carries_no_marker(self) -> None:
        """L'absence de marqueur doit valoir « contenu complet »."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_task_calls([_comment(text="court")]),
        ):
            data = json.loads(await kanboard_get_task(task_id=42))
        entry = data["comments"][0]
        assert entry["content"] == "court"
        assert "comment_truncated" not in entry
        assert "comment_full_length" not in entry

    async def test_full_comments_returns_whole_text(self) -> None:
        long_text = "y" * (COMMENT_PREVIEW_CHARS + 300)
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_task_calls([_comment(text=long_text)]),
        ):
            data = json.loads(await kanboard_get_task(task_id=42, full_comments=True))
        entry = data["comments"][0]
        assert entry["content"] == long_text
        assert "comment_truncated" not in entry


@pytest.mark.asyncio
class TestListComments:
    async def test_empty_returns_empty_list(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_call:
            raw = await kanboard_list_comments(task_id=42)
        assert mock_call.call_args[0][0] == "getAllComments"
        assert json.loads(raw) == []

    async def test_exposes_documented_fields_without_truncation(self) -> None:
        """C'est le tool de reference avant reecriture : aucune coupure permise."""
        long_text = "z" * (COMMENT_PREVIEW_CHARS + 900)
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=[_comment(text=long_text)],
        ):
            rows = json.loads(await kanboard_list_comments(task_id=42))
        row = rows[0]
        assert row["comment"] == long_text
        assert set(row) == {
            "id", "task_id", "user_id", "username", "name",
            "date_creation", "date_modification", "comment",
        }
        assert row["date_creation"] != ""

    async def test_requires_task_id(self) -> None:
        assert "task_id est obligatoire" in await kanboard_list_comments()


@pytest.mark.asyncio
class TestGetComment:
    async def test_returns_full_content(self) -> None:
        long_text = "w" * (COMMENT_PREVIEW_CHARS + 50)
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_comment(text=long_text),
        ) as mock_call:
            row = json.loads(await kanboard_get_comment(comment_id=7))
        assert mock_call.call_args[0][0] == "getComment"
        assert mock_call.call_args[0][1] == {"comment_id": 7}
        assert row["comment"] == long_text
        assert "secret" not in row

    async def test_missing_comment_is_reported(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=None,
        ):
            assert "introuvable" in await kanboard_get_comment(comment_id=7)


@pytest.mark.asyncio
class TestUpdateComment:
    async def test_sends_id_and_content_and_returns_previous(self) -> None:
        """Kanboard ne versionne pas : sans previous_comment, l'ancien texte est perdu."""

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getComment":
                return _comment(text="Ancien texte")
            if method == "updateComment":
                return True
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ) as mock_call:
            data = json.loads(
                await kanboard_update_comment(comment_id=7, comment="Nouveau texte")
            )
        update_call = [c for c in mock_call.call_args_list if c[0][0] == "updateComment"][0]
        # L'API attend `content`, le parametre expose s'appelle `comment`.
        assert update_call[0][1] == {"id": 7, "content": "Nouveau texte"}
        assert data["success"] is True
        assert data["previous_comment"] == "Ancien texte"
        assert data["new_comment"] == "Nouveau texte"

    async def test_missing_comment_is_not_overwritten(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_call:
            out = await kanboard_update_comment(comment_id=7, comment="x")
        assert "introuvable" in out
        assert [c[0][0] for c in mock_call.call_args_list] == ["getComment"]

    async def test_requires_both_arguments(self) -> None:
        assert "comment_id est obligatoire" in await kanboard_update_comment(comment="x")
        assert "comment est obligatoire" in await kanboard_update_comment(comment_id=7)

    async def test_bare_false_becomes_an_author_message(self) -> None:
        """Un `false` nu est indebogable : il faut nommer l'auteur et le compte."""

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getComment":
                return _comment(text="Ancien texte")
            if method == "updateComment":
                return False
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            data = json.loads(
                await kanboard_update_comment(comment_id=7, comment="x", as_user="primary")
            )
        assert data["success"] is False
        assert data["error"] == "permission_denied"
        assert data["attempted_as_user"] == "primary"
        assert data["comment_author"] == "Agent IA"
        assert "as_user='agent'" in data["hint"]

    async def test_permission_exception_becomes_an_author_message(self) -> None:
        """Selon la version, le refus d'auteur ressort en erreur JSON-RPC."""

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getComment":
                return _comment(text="Ancien texte")
            if method == "updateComment":
                raise Exception("Kanboard API error: Access Forbidden")
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            data = json.loads(
                await kanboard_update_comment(comment_id=7, comment="x", as_user="agent")
            )
        assert data["error"] == "permission_denied"
        assert data["attempted_as_user"] == "agent"
        assert "as_user='primary'" in data["hint"]
        assert "Forbidden" in data["api_detail"]

    async def test_single_account_setup_does_not_suggest_a_phantom_account(self) -> None:
        """Sans compte agent, "reessayer avec l'autre" enverrait sur une fausse piste.

        Tout retombe sur primary : le refus signifie alors que le commentaire
        appartient a un utilisateur Kanboard tiers.
        """

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getComment":
                return _comment(text="Ancien texte")
            if method == "updateComment":
                return False
            raise AssertionError(f"methode inattendue : {method}")

        with (
            patch("kanboard_mcp.server.KANBOARD_USER_ALT", ""),
            patch("kanboard_mcp.server.KANBOARD_TOKEN_ALT", ""),
            patch(
                "kanboard_mcp.server.kb_call",
                new_callable=AsyncMock,
                side_effect=_dispatch,
            ),
        ):
            data = json.loads(
                await kanboard_update_comment(comment_id=7, comment="x", as_user="agent")
            )
        # "agent" demande mais non configure : c'est primary qui a signe.
        assert data["attempted_as_user"] == "primary"
        assert "as_user=" not in data["hint"]
        assert "Aucun second compte" in data["hint"]


@pytest.mark.asyncio
class TestRemoveComment:
    async def test_returns_deleted_content(self) -> None:
        """Kanboard ne garde aucune copie : le texte doit survivre dans la reponse."""

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getComment":
                return _comment(text="A supprimer")
            if method == "removeComment":
                return True
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ) as mock_call:
            data = json.loads(await kanboard_remove_comment(comment_id=7))
        remove_call = [c for c in mock_call.call_args_list if c[0][0] == "removeComment"][0]
        assert remove_call[0][1] == {"comment_id": 7}
        assert data["success"] is True
        assert data["deleted_comment"] == "A supprimer"
        assert data["deleted_comment_author"] == "Agent IA"
        assert data["task_id"] == "42"

    async def test_refusal_names_the_other_account(self) -> None:
        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getComment":
                return _comment(text="A supprimer")
            if method == "removeComment":
                return False
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            data = json.loads(
                await kanboard_remove_comment(comment_id=7, as_user="primary")
            )
        assert data["success"] is False
        assert data["action"] == "supprimer"
        assert "as_user='agent'" in data["hint"]

    async def test_requires_comment_id(self) -> None:
        assert "comment_id est obligatoire" in await kanboard_remove_comment()


def _events(count: int, start_ts: int = 1753900000) -> list[dict]:
    """Fabrique `count` evenements getProjectActivity, du plus recent au plus ancien."""
    return [
        {
            "event_name": "task.update",
            "date_creation": str(start_ts - i * 3600),
            "author_name": "Alice",
            "task": {"id": 1000 + i, "title": f"Carte {i}"},
            "event_title": "Alice a modifie la tache",
        }
        for i in range(count)
    ]


@pytest.mark.asyncio
class TestRecentActivity:
    async def test_full_window_is_flagged_as_capped(self) -> None:
        """L'API plafonne a 50 sans le dire : 50 evenements ne sont pas « tout »."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_events(ACTIVITY_API_CAP),
        ):
            data = json.loads(await kanboard_recent_activity(project_id=262, limit=100))
        assert data["api_cap_reached"] is True
        assert data["api_cap"] == ACTIVITY_API_CAP
        assert "plafonne" in data["note"]
        assert data["oldest_available"] != ""

    async def test_partial_window_is_not_flagged(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_events(12),
        ):
            data = json.loads(await kanboard_recent_activity(project_id=262, limit=100))
        assert data["api_cap_reached"] is False
        assert "note" not in data
        assert data["count"] == 12

    async def test_limit_can_only_narrow_never_widen(self) -> None:
        """`limit` cote connecteur ne peut pas depasser ce que l'API a rendu."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_events(ACTIVITY_API_CAP),
        ):
            data = json.loads(await kanboard_recent_activity(project_id=262, limit=100))
        assert data["count"] == ACTIVITY_API_CAP
        assert len(data["events"]) == ACTIVITY_API_CAP

    async def test_since_iso_older_than_window_is_flagged_incomplete(self) -> None:
        """Le filtre date s'applique APRES le plafond : il ne remonte rien de plus."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_events(ACTIVITY_API_CAP),
        ):
            data = json.loads(
                await kanboard_recent_activity(project_id=262, since_iso="2020-01-01")
            )
        assert data["window_incomplete"] is True

    async def test_since_iso_inside_window_is_not_flagged(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_events(ACTIVITY_API_CAP),
        ):
            data = json.loads(
                await kanboard_recent_activity(project_id=262, since_iso="2030-01-01")
            )
        assert "window_incomplete" not in data

    async def test_empty_project_returns_empty_payload(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=None,
        ):
            data = json.loads(await kanboard_recent_activity(project_id=262))
        assert data == {"count": 0, "api_cap_reached": False, "events": []}


@pytest.mark.asyncio
class TestNonAdminProjectFallback:
    async def test_my_dashboard_survives_a_403_on_get_all_projects(self) -> None:
        """getAllProjects est admin-only : as_user='agent' rendait un 403 sec."""
        calls: list[str] = []

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            calls.append(method)
            if method == "getMe":
                return {"id": 84, "username": "bot", "name": "Agent IA"}
            if method == "getAllProjects":
                raise _http_403()
            if method == "getMyProjects":
                return [{"id": 262, "name": "Pilotage", "is_active": "1"}]
            if method == "getAllTasks":
                return []
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server._my_id_cache", {}
        ), patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            out = await kanboard_my_dashboard(as_user="agent")
        assert "403" not in out
        assert "getMyProjects" in calls

    async def test_list_projects_reports_the_narrowed_scope(self) -> None:
        """Retomber sur les projets « membre » sans le dire ferait conclure a tort
        qu'un projet n'existe pas."""

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                raise _http_403()
            if method == "getMyProjects":
                return _projects(3)
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            data = json.loads(await kanboard_list_projects())
        assert data["scope"] == "member"
        assert data["total_available"] == 3

    async def test_admin_scope_is_reported_as_all(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            return_value=_projects(3),
        ):
            data = json.loads(await kanboard_list_projects())
        assert data["scope"] == "all"

    async def test_non_permission_errors_are_not_swallowed(self) -> None:
        """Seul un refus declenche le repli : une panne doit rester visible."""

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                raise Exception("Kanboard API error: database is gone")
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            out = await kanboard_list_projects()
        assert "database is gone" in out


@pytest.mark.asyncio
class TestCheckProjectAccess:
    @staticmethod
    def _dispatch(primary: dict, agent: dict, admin_agent: bool = False):
        async def _inner(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getMyProjectsList":
                return primary if as_user == "primary" else agent
            if method == "getAllProjects":
                if as_user == "primary" or admin_agent:
                    return []
                raise _http_403()
            raise AssertionError(f"methode inattendue : {method}")

        return _inner

    async def test_reports_the_gap_between_accounts(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=self._dispatch(
                primary={"262": "Pilotage", "31": "Site web"},
                agent={"262": "Pilotage"},
            ),
        ):
            data = json.loads(await kanboard_check_project_access())
        assert data["accounts_configured"] == ["primary", "agent"]
        assert data["primary"]["is_admin"] is True
        assert data["agent"]["is_admin"] is False
        assert data["only_primary_count"] == 1
        assert data["only_primary"][0]["id"] == "31"
        assert data["only_agent_count"] == 0

    async def test_named_project_says_which_account_can_write(self) -> None:
        """Le point du tool : savoir AVANT d'ecrire, pas apres un 403."""
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=self._dispatch(
                primary={"262": "Pilotage", "31": "Site web"},
                agent={"262": "Pilotage"},
            ),
        ):
            data = json.loads(await kanboard_check_project_access(project_id=31))
        assert data["writable_by"] == ["primary"]
        assert data["project_name"] == "Site web"
        assert "as_user='primary'" in data["hint"]
        assert "agent" in data["hint"]

    async def test_project_reachable_by_both_has_no_hint(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=self._dispatch(
                primary={"262": "Pilotage"},
                agent={"262": "Pilotage"},
            ),
        ):
            data = json.loads(await kanboard_check_project_access(project_id=262))
        assert data["writable_by"] == ["primary", "agent"]
        assert "hint" not in data

    async def test_unreachable_project_is_called_out(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=self._dispatch(primary={"262": "Pilotage"}, agent={}),
        ):
            data = json.loads(await kanboard_check_project_access(project_id=999))
        assert data["writable_by"] == []
        assert "hors de portee" in data["hint"]

    async def test_search_tasks_reports_scope_and_incomplete_sweep(self) -> None:
        """Le balayage s'arrete a `limit` : « rien trouve » ne vaut pas « rien »."""
        projects = [
            {"id": i, "name": f"Projet {i}", "is_active": "1"} for i in range(1, 6)
        ]

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                raise _http_403()
            if method == "getMyProjects":
                return projects
            if method == "getAllTasks":
                return [{"id": 1, "title": "cible", "description": "", "project_id": 1}]
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            data = json.loads(await kanboard_search_tasks(query="cible", limit=1))
        assert data["scope"] == "member"
        assert data["count"] == 1
        assert data["projects_scanned"] == 1
        assert data["projects_total"] == 5
        assert data["has_more"] is True

    async def test_search_tasks_full_sweep_has_no_more(self) -> None:
        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                return [{"id": 1, "name": "P1", "is_active": "1"}]
            if method == "getAllTasks":
                return []
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            data = json.loads(await kanboard_search_tasks(query="absent"))
        assert data["has_more"] is False
        assert data["projects_scanned"] == data["projects_total"] == 1
        assert data["results"] == []

    async def test_gap_list_is_clamped_and_says_so(self) -> None:
        primary = {str(i): f"Projet {i}" for i in range(1, 40)}
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=self._dispatch(primary=primary, agent={}),
        ):
            data = json.loads(await kanboard_check_project_access(limit=5))
        assert data["only_primary_count"] == 39
        assert len(data["only_primary"]) == 5
        assert data["truncated"] is True
