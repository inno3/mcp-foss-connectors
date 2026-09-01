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

from kanboard_mcp import server  # noqa: E402
from kanboard_mcp.server import (  # noqa: E402
    ACTIVITY_API_CAP,
    COMMENT_PREVIEW_CHARS,
    _clamp_limit,
    _dumps,
    _fold,
    _resolve_account,
    _ts_to_date,
    kanboard_check_project_access,
    kanboard_create_task,
    kanboard_create_task_link,
    kanboard_get_board,
    kanboard_get_comment,
    kanboard_get_task,
    kanboard_list_comments,
    kanboard_list_link_types,
    kanboard_list_projects,
    kanboard_list_task_links,
    kanboard_my_dashboard,
    kanboard_recent_activity,
    kanboard_remove_comment,
    kanboard_remove_task_link,
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
            data = json.loads(await kanboard_get_comment(comment_id=7))
        assert data["error"] == "not_found_or_forbidden"
        assert data["comment_id"] == 7

    async def test_bad_id_403_names_both_causes(self) -> None:
        """Kanboard rend 403 sur un id inexistant, pas un resultat vide.

        Un « permissions insuffisantes » nu envoyait chercher un probleme de
        droits qui n'existe pas, et restait indiscernable d'un vrai refus.
        """
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_http_403(),
        ):
            data = json.loads(await kanboard_get_comment(comment_id=99999999))
        assert data["error"] == "not_found_or_forbidden"
        assert "n'existe pas" in data["hint"]
        assert "hors de portee" in data["hint"]
        assert "kanboard_check_project_access" in data["hint"]

    async def test_list_comments_bad_task_id_names_both_causes(self) -> None:
        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_http_403(),
        ):
            data = json.loads(await kanboard_list_comments(task_id=99999999))
        assert data["error"] == "not_found_or_forbidden"
        assert data["task_id"] == 99999999

    async def test_write_tools_stop_at_the_failed_preread(self) -> None:
        """Un 403 a la relecture ne doit surtout pas laisser passer l'ecriture."""
        calls: list[str] = []

        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            calls.append(method)
            if method == "getComment":
                raise _http_403()
            raise AssertionError(f"ecriture tentee malgre la relecture ratee : {method}")

        with patch(
            "kanboard_mcp.server.kb_call",
            new_callable=AsyncMock,
            side_effect=_dispatch,
        ):
            upd = json.loads(await kanboard_update_comment(comment_id=999, comment="x"))
            rm = json.loads(await kanboard_remove_comment(comment_id=999))
        assert upd["error"] == "not_found_or_forbidden"
        assert rm["error"] == "not_found_or_forbidden"
        assert calls == ["getComment", "getComment"]


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
            data = json.loads(await kanboard_update_comment(comment_id=7, comment="x"))
        assert data["error"] == "not_found_or_forbidden"
        # L'ecriture ne doit pas avoir eu lieu.
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


# Types de liens tels que les renvoie getAllLinks. Les libelles suivent la
# langue de l'instance et les ids ne sont pas garantis : c'est tout l'interet
# de kanboard_list_link_types.
_LINK_TYPES = [
    {"id": "1", "label": "relates to", "opposite_id": "0"},
    {"id": "2", "label": "blocks", "opposite_id": "3"},
    {"id": "3", "label": "is blocked by", "opposite_id": "2"},
    {"id": "8", "label": "is a milestone of", "opposite_id": "9"},
    {"id": "9", "label": "targets milestone", "opposite_id": "8"},
]


def _task_link(**over: object) -> dict:
    """Lien brut de getAllTaskLinks.

    Piege reproduit fidelement : Kanboard aliase `opposite_task_id AS task_id`,
    donc `task_id` designe ici la tache LIEE, pas celle qu'on a interrogee.
    """
    raw = {
        "id": "17",
        "task_id": "300",
        "link_id": "9",
        "label": "targets milestone",
        "title": "Livrer le rapport",
        "is_active": "1",
        "column_id": "4",
        "column_title": "En cours",
        "project_id": "7",
        "project_name": "Projet 7",
        "date_due": "0",
    }
    raw.update(over)  # type: ignore[arg-type]
    return raw


@pytest.mark.asyncio
class TestListLinkTypes:
    async def test_presupposes_no_hardcoded_identifier(self) -> None:
        """Les ids varient selon l'instance : ils doivent venir de l'API seule."""
        with patch(
            "kanboard_mcp.server.kb_call", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = [
                {"id": "42", "label": "jalon maison", "opposite_id": "43"},
                {"id": "43", "label": "vise le jalon maison", "opposite_id": "42"},
            ]
            data = json.loads(await kanboard_list_link_types())

        assert mock_call.call_args[0][0] == "getAllLinks"
        # Aucun parametre d'identifiant n'est envoye : rien n'est presuppose.
        assert mock_call.call_args[0][1:] in ((), (None,))
        assert {t["id"] for t in data["link_types"]} == {42, 43}
        assert data["count"] == 2

    async def test_resolves_the_opposite_label(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = _LINK_TYPES
            data = json.loads(await kanboard_list_link_types())

        milestone = [t for t in data["link_types"] if t["id"] == 8][0]
        assert milestone["opposite_id"] == 9
        assert milestone["opposite_label"] == "targets milestone"

    async def test_symmetric_type_has_no_opposite(self) -> None:
        """opposite_id=0 signifie « son propre oppose », pas « type 0 »."""
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = _LINK_TYPES
            data = json.loads(await kanboard_list_link_types())

        relates = [t for t in data["link_types"] if t["id"] == 1][0]
        assert relates["opposite_id"] is None
        assert relates["opposite_label"] is None


@pytest.mark.asyncio
class TestCreateTaskLink:
    async def test_transmits_the_three_expected_parameters(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = 17
            data = json.loads(await kanboard_create_task_link(
                task_id=100, opposite_task_id=300, link_id=9
            ))

        assert mock_call.call_args[0][0] == "createTaskLink"
        assert mock_call.call_args[0][1] == {
            "task_id": 100,
            "opposite_task_id": 300,
            "link_id": 9,
        }
        assert data["success"] is True
        assert data["task_link_id"] == 17

    async def test_says_the_reverse_link_is_automatic(self) -> None:
        """Sans cette mention, le lien inverse se lit comme un doublon."""
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = 17
            data = json.loads(await kanboard_create_task_link(
                task_id=100, opposite_task_id=300, link_id=9
            ))
        assert "automatiquement" in data["note"]
        assert mock_call.call_count == 1  # surtout pas un second appel inverse

    async def test_missing_link_id_points_at_the_lookup_tool(self) -> None:
        result = await kanboard_create_task_link(task_id=100, opposite_task_id=300)
        assert "kanboard_list_link_types" in result

    async def test_refuses_to_link_a_task_to_itself(self) -> None:
        result = await kanboard_create_task_link(
            task_id=100, opposite_task_id=100, link_id=9
        )
        assert "elle-meme" in result

    async def test_requires_both_tasks(self) -> None:
        assert "task_id" in await kanboard_create_task_link(opposite_task_id=300, link_id=9)
        assert "opposite_task_id" in await kanboard_create_task_link(task_id=100, link_id=9)

    async def test_silent_refusal_is_reported_as_failure(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = False
            data = json.loads(await kanboard_create_task_link(
                task_id=100, opposite_task_id=300, link_id=9
            ))
        assert data["success"] is False
        assert "kanboard_list_link_types" in data["hint"]


@pytest.mark.asyncio
class TestListTaskLinks:
    async def _links(self, rows: list[dict]) -> dict:
        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllTaskLinks":
                return rows
            if method == "getAllLinks":
                return _LINK_TYPES
            raise AssertionError(f"methode inattendue : {method}")

        with patch(
            "kanboard_mcp.server.kb_call", new_callable=AsyncMock, side_effect=_dispatch
        ):
            return json.loads(await kanboard_list_task_links(task_id=100))

    async def test_returns_title_and_status_not_just_an_id(self) -> None:
        """C'est ce qui rend la sortie exploitable sans second appel."""
        data = await self._links([_task_link()])
        link = data["links"][0]
        assert link["title"] == "Livrer le rapport"
        assert link["status"] == "open"
        assert link["column"] == "En cours"
        assert link["project_name"] == "Projet 7"

    async def test_closed_task_is_reported_as_closed(self) -> None:
        data = await self._links([_task_link(is_active="0")])
        assert data["links"][0]["status"] == "closed"

    async def test_the_aliased_task_id_is_exposed_as_the_linked_task(self) -> None:
        """Kanboard aliase opposite_task_id en task_id : ne pas le repropager."""
        data = await self._links([_task_link()])
        link = data["links"][0]
        assert link["linked_task_id"] == 300      # la tache liee
        assert data["task_id"] == 100             # celle qu'on a interrogee
        assert "task_id" not in link              # pas de champ ambigu dans la ligne

    async def test_removal_identifier_is_the_link_row_not_the_task(self) -> None:
        data = await self._links([_task_link()])
        link = data["links"][0]
        assert link["task_link_id"] == 17
        assert link["task_link_id"] != link["linked_task_id"]

    async def test_no_links_is_an_empty_list_not_an_error(self) -> None:
        data = await self._links([])
        assert data["count"] == 0
        assert data["links"] == []

    async def test_link_labels_survive_a_failing_type_lookup(self) -> None:
        """Un getAllLinks en echec ne doit pas faire perdre la liste des liens."""
        async def _dispatch(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllTaskLinks":
                return [_task_link()]
            raise Exception("getAllLinks indisponible")

        with patch(
            "kanboard_mcp.server.kb_call", new_callable=AsyncMock, side_effect=_dispatch
        ):
            data = json.loads(await kanboard_list_task_links(task_id=100))
        assert data["count"] == 1
        assert data["links"][0]["relation"] == "targets milestone"  # libelle brut

    async def test_requires_a_task_id(self) -> None:
        assert "task_id est obligatoire" in await kanboard_list_task_links()


@pytest.mark.asyncio
class TestRemoveTaskLink:
    async def test_sends_the_link_row_id(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = True
            data = json.loads(await kanboard_remove_task_link(task_link_id=17))

        assert mock_call.call_args[0][0] == "removeTaskLink"
        assert mock_call.call_args[0][1] == {"task_link_id": 17}
        assert data["success"] is True

    async def test_missing_id_warns_against_passing_a_task_id(self) -> None:
        result = await kanboard_remove_task_link()
        assert "task_link_id" in result
        assert "linked_task_id" in result

    async def test_refusal_is_not_reported_as_success(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = False
            data = json.loads(await kanboard_remove_task_link(task_link_id=17))
        assert data["success"] is False


# ---------------------------------------------------------------------------
# Balayage exhaustif et resolution par nom
# ---------------------------------------------------------------------------


class TestSearchSweepIsHonestAboutWhatItSaw:
    """Le balayage s'arretait a `limit`, biaisant vers les premiers projets.

    Constate le 31/08/2026 sur l'instance : une recherche a limit=20 s'arretait
    au 26e projet sur 65. Les 39 suivants n'etaient jamais regardes, et rien
    dans la reponse ne disait quoi faire de cette absence.
    """

    @staticmethod
    def _dispatch(projects: int, matches_per_project: int):
        async def _run(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                return [{"id": i, "name": f"P{i}", "is_active": "1"}
                        for i in range(1, projects + 1)]
            if method == "getAllTasks":
                pid = (params or {}).get("project_id", 1)
                return [{"id": pid * 100 + j, "title": "cible", "description": "",
                         "project_id": pid} for j in range(matches_per_project)]
            raise AssertionError(f"methode inattendue : {method}")

        return _run

    async def test_an_early_stop_now_says_what_to_do(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch(projects=10, matches_per_project=5)):
            data = json.loads(await kanboard_search_tasks(query="cible", limit=5))

        assert data["has_more"] is True
        assert data["projects_scanned"] < data["projects_total"]
        assert "scan_all=True" in data["scan_warning"]
        assert "n'ont PAS ete regardes" in data["scan_warning"]

    async def test_scan_all_visits_every_project(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch(projects=10, matches_per_project=5)):
            data = json.loads(await kanboard_search_tasks(
                query="cible", limit=5, scan_all=True))

        assert data["projects_scanned"] == data["projects_total"] == 10
        assert data["has_more"] is False
        assert "scan_warning" not in data
        # La liste reste plafonnee, mais le compte reel est rendu.
        assert data["count"] == 5
        assert data["total_matches"] == 50

    async def test_a_complete_sweep_without_scan_all_is_not_warned(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch(projects=2, matches_per_project=1)):
            data = json.loads(await kanboard_search_tasks(query="cible", limit=20))

        assert data["has_more"] is False
        assert "scan_warning" not in data
        assert data["total_matches"] == 2


class TestCreateTaskAcceptsNames:
    """Creer une tache demandait 3 recherches d'identifiants au prealable."""

    @staticmethod
    def _dispatch(created_id=42):
        calls: list = []

        async def _run(method: str, params: dict | None = None, as_user: str = ""):
            calls.append((method, params))
            if method == "getAllSwimlanes":
                return [{"id": 7, "name": "Direction inno³"},
                        {"id": 8, "name": "Missions & Hermine"}]
            if method == "getAllCategories":
                return [{"id": 3, "name": "Pilotage"}]
            if method == "getColumns":
                return [{"id": 11, "title": "A faire"}]
            if method == "createTask":
                return created_id
            raise AssertionError(f"methode inattendue : {method}")

        _run.calls = calls
        return _run

    async def test_a_swimlane_name_is_resolved(self) -> None:
        dispatch = self._dispatch()
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=dispatch):
            data = json.loads(await kanboard_create_task(
                project_id=262, title="T", swimlane_name="Missions & Hermine"))

        created = [p for m, p in dispatch.calls if m == "createTask"][0]
        assert created["swimlane_id"] == 8
        assert data["success"] is True

    async def test_a_category_name_is_resolved(self) -> None:
        dispatch = self._dispatch()
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=dispatch):
            await kanboard_create_task(project_id=262, title="T", category_name="Pilotage")

        created = [p for m, p in dispatch.calls if m == "createTask"][0]
        assert created["category_id"] == 3

    async def test_an_unknown_name_lists_what_exists_and_creates_nothing(self) -> None:
        dispatch = self._dispatch()
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=dispatch):
            msg = await kanboard_create_task(
                project_id=262, title="T", swimlane_name="Inexistant")

        assert "introuvable" in msg
        assert "Direction inno³" in msg
        assert not [m for m, _ in dispatch.calls if m == "createTask"]

    async def test_giving_both_id_and_name_is_refused(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock) as call:
            msg = await kanboard_create_task(
                project_id=262, title="T", swimlane_id=7, swimlane_name="Direction inno³")
        assert "pas les deux" in msg
        call.assert_not_called()

    async def test_ids_still_work_unchanged(self) -> None:
        dispatch = self._dispatch()
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=dispatch):
            await kanboard_create_task(project_id=262, title="T",
                                       swimlane_id=7, category_id=3)

        created = [p for m, p in dispatch.calls if m == "createTask"][0]
        assert created["swimlane_id"] == 7 and created["category_id"] == 3
        # Aucune resolution declenchee quand l'identifiant est donne.
        assert not [m for m, _ in dispatch.calls if m in ("getAllSwimlanes", "getAllCategories")]


# ---------------------------------------------------------------------------
# Non-regression : recette du 01/09/2026
# ---------------------------------------------------------------------------


class TestSearchResultsAreReachable:
    """scan_all corrigeait le COMPTE sans donner acces a la LISTE.

    Constate a la recette : 195 correspondances annoncees sur l'instance,
    toujours les 20 memes rendues, aucune des 175 autres nommable.
    """

    @staticmethod
    def _dispatch(projects=3, per_project=10):
        async def _run(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                return [{"id": i, "name": f"P{i}", "is_active": "1"}
                        for i in range(1, projects + 1)]
            if method == "getAllTasks":
                pid = (params or {}).get("project_id", 1)
                return [{"id": pid * 100 + j, "title": f"cible {pid}-{j}",
                         "description": "", "project_id": pid}
                        for j in range(per_project)]
            if method == "getProjectById":
                return {"name": f"Projet {(params or {}).get('project_id')}"}
            if method == "getAllCategories":
                return []
            return []

        return _run

    async def test_offset_reaches_beyond_the_first_page(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch()):
            first = json.loads(await kanboard_search_tasks(
                query="cible", limit=5, scan_all=True))
            second = json.loads(await kanboard_search_tasks(
                query="cible", limit=5, scan_all=True, offset=5))

        ids_first = {t["id"] for t in first["results"]}
        ids_second = {t["id"] for t in second["results"]}
        assert ids_first and ids_second
        assert not (ids_first & ids_second)      # aucun recoupement
        assert second["offset"] == 5

    async def test_the_next_offset_is_handed_back(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch()):
            data = json.loads(await kanboard_search_tasks(
                query="cible", limit=5, scan_all=True))

        assert data["total_matches"] == 30
        assert data["has_more_results"] is True
        assert data["next_offset"] == 5

    async def test_the_last_page_says_there_is_no_more(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch(projects=1, per_project=3)):
            data = json.loads(await kanboard_search_tasks(
                query="cible", limit=20, scan_all=True))

        assert data["has_more_results"] is False
        assert "next_offset" not in data

    async def test_results_carry_project_and_category_names(self) -> None:
        async def _run(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                return [{"id": 262, "name": "Pilotage", "is_active": "1"}]
            if method == "getAllTasks":
                return [{"id": 1, "title": "cible", "description": "",
                         "project_id": 262, "category_id": 3}]
            if method == "getProjectById":
                return {"name": "Coordination Claude x Benjamin"}
            if method == "getAllCategories":
                return [{"id": 3, "name": "Interne"}]
            return []

        server._PROJECT_NAMES.clear()
        server._CATEGORY_NAMES.clear()
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock, side_effect=_run):
            data = json.loads(await kanboard_search_tasks(query="cible", scan_all=True))

        task = data["results"][0]
        # Tous deux revenaient vides : le nom de projet n'etait jamais resolu,
        # et la categorie n'etait lisible que par get_board, trop volumineux.
        assert task["project_name"] == "Coordination Claude x Benjamin"
        assert task["category_name"] == "Interne"


class TestDeletedTaskIsNotAPermissionProblem:
    """Kanboard rend un refus d'acces pour une tache supprimee."""

    async def test_the_message_names_both_causes(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=RuntimeError("Access denied")):
            data = json.loads(await kanboard_get_task(task_id=7847))

        assert data["found"] is False
        assert "n'existe plus" in data["message"]
        assert "perimetre" in data["message"]

    async def test_a_null_task_is_plainly_absent(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   return_value=None):
            data = json.loads(await kanboard_get_task(task_id=999))

        assert data["found"] is False
        assert "introuvable" in data["message"]

    async def test_a_real_error_is_still_raised(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=RuntimeError("boom réseau")):
            msg = await kanboard_get_task(task_id=1)
        assert "boom" in msg


class TestHasMoreResultsNeverContradictsTheWarning:
    """Deux champs voisins se contredisaient dans la meme reponse.

    Constate a la seconde passe : count=20, total_matches=21, scan_warning
    present — et has_more_results=false. Un appelant qui se fie au booleen
    conclut qu'il a tout vu.
    """

    @staticmethod
    def _dispatch(projects=10, per_project=5):
        async def _run(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getAllProjects":
                return [{"id": i, "name": f"P{i}", "is_active": "1"}
                        for i in range(1, projects + 1)]
            if method == "getAllTasks":
                pid = (params or {}).get("project_id", 1)
                return [{"id": pid * 100 + j, "title": "cible", "description": "",
                         "project_id": pid} for j in range(per_project)]
            if method == "getProjectById":
                return {"name": "P"}
            return []

        return _run

    async def test_a_truncated_sweep_admits_there_is_more(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch()):
            data = json.loads(await kanboard_search_tasks(query="cible", limit=5))

        assert "scan_warning" in data
        # Le booleen doit dire la meme chose que l'avertissement.
        assert data["has_more_results"] is True

    async def test_unrendered_matches_count_even_without_scan_all(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch(projects=1, per_project=5)):
            data = json.loads(await kanboard_search_tasks(query="cible", limit=3))

        assert data["total_matches"] > data["count"]
        assert data["has_more_results"] is True
        assert data["next_offset"] == 3

    async def test_a_complete_exhaustive_sweep_says_no_more(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock,
                   side_effect=self._dispatch(projects=1, per_project=3)):
            data = json.loads(await kanboard_search_tasks(
                query="cible", limit=20, scan_all=True))

        assert data["has_more_results"] is False
        assert "next_offset" not in data
        assert "scan_warning" not in data


class TestGetTaskExposesProjectAndCategory:
    """La resolution n'avait ete faite que sur search_tasks."""

    async def test_both_names_are_resolved(self) -> None:
        async def _run(method: str, params: dict | None = None, as_user: str = ""):
            if method == "getTask":
                return {"id": 7848, "title": "ZZ", "project_id": 262,
                        "category_id": 3, "column_id": 1, "swimlane_id": 2}
            if method == "getProjectById":
                return {"name": "Coordination Claude x Benjamin"}
            if method == "getAllCategories":
                return [{"id": 3, "name": "Interne"}]
            return []

        server._PROJECT_NAMES.clear()
        server._CATEGORY_NAMES.clear()
        with patch("kanboard_mcp.server.kb_call", new_callable=AsyncMock, side_effect=_run):
            data = json.loads(await kanboard_get_task(task_id=7848))

        assert data["project_name"] == "Coordination Claude x Benjamin"
        assert data["category_name"] == "Interne"
        assert data["category_id"] == 3
