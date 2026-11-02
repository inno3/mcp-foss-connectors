"""Contrat de la création de projet et des champs d'opportunité, sans service distant.

Le 02/11/2026, dolibarr_create_project a rendu success, status=1 et ref="PROV" sans
aucun avertissement (projet 316). La route POST /projects/{id}/validate appelle
Project::setValid(), qui ne fait que passer fk_statut à 1 : elle ne renumérote pas.
Le connecteur demande donc la numérotation à la création avec ref="auto", et seule
la relecture dit quelle référence le projet porte vraiment.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp.server import dolibarr_create_project, dolibarr_get_project

PROJECT_ID = 316
REF = "PJ2611-0042"


def _http_error(status: int, text: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://test")
    response = httpx.Response(status, request=request, text=text)
    return httpx.HTTPStatusError("", request=request, response=response)


async def _create(read_back, *, post=None, **kwargs):
    """Crée un projet ; rend (réponse décodée, mock POST, mock GET)."""
    side_effect = post if post is not None else [PROJECT_ID, {"success": {"code": 200}}]
    with (
        patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as mock_post,
        patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock),
        patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as mock_get,
    ):
        mock_post.side_effect = side_effect
        if isinstance(read_back, Exception):
            mock_get.side_effect = read_back
        else:
            mock_get.return_value = read_back
        result = await dolibarr_create_project(title="Projet test", **kwargs)
    return json.loads(result), mock_post, mock_get


READ_FAILURES = [_http_error(500), httpx.ReadTimeout(""), httpx.ConnectError("refused")]
READ_FAILURE_IDS = ["http-500", "message-vide", "connexion"]
UNUSABLE_READS = [{"id": "316", "status": "1"}, {"id": "316", "ref": "", "status": "1"}, [], None]
UNUSABLE_READ_IDS = ["ref-absente", "ref-vide", "liste", "null"]


@pytest.mark.asyncio
class TestCreateProjectAutoRef:
    @pytest.mark.parametrize("status", [0, 1])
    async def test_numbering_is_requested_at_creation(self, status: int) -> None:
        """Le cas du projet 316 à sa source : plus aucun "PROV" envoyé, brouillon compris."""
        data, mock_post, _ = await _create(
            {"ref": REF, "status": str(status)},
            post=[PROJECT_ID] if status == 0 else None,
            status=status,
        )

        endpoint, payload = mock_post.call_args_list[0].args
        assert endpoint == "projects"
        assert payload["ref"] == "auto"
        assert payload["fk_statut"] == "0"
        assert data["ref"] == REF
        assert data["status"] == status
        assert "PROV" not in json.dumps(data)

    async def test_validate_still_runs_for_its_trigger(self) -> None:
        _, mock_post, _ = await _create({"ref": REF, "status": "1"})

        assert mock_post.call_args_list[1].args == (
            f"projects/{PROJECT_ID}/validate", {"notrigger": 0},
        )

    async def test_second_creation_is_not_blocked_by_a_project_stuck_on_prov(self) -> None:
        """Deux créations de suite : aucune ne repose la référence unique PROV."""
        posted = []
        for number in (1, 2):
            _, mock_post, _ = await _create({"ref": f"PJ2611-000{number}", "status": "1"})
            posted.append(mock_post.call_args_list[0].args[1]["ref"])

        assert posted == ["auto", "auto"]

    async def test_read_comes_after_every_write(self) -> None:
        with (
            patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as mock_post,
            patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as mock_put,
            patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as mock_get,
        ):
            calls: list[str] = []
            mock_post.side_effect = lambda path, _data: calls.append(path) or PROJECT_ID
            mock_put.side_effect = lambda path, _data: calls.append(f"PUT {path}") or {}

            async def _read(path):
                calls.append(f"GET {path}")
                return {"ref": REF, "status": "1"}

            mock_get.side_effect = _read
            await dolibarr_create_project(title="Projet test", description="x")

        assert calls == [
            "projects",
            f"PUT projects/{PROJECT_ID}",
            f"projects/{PROJECT_ID}/validate",
            f"GET projects/{PROJECT_ID}",
        ]


@pytest.mark.asyncio
class TestCreateProjectValidateReadback:
    async def test_ref_still_prov_after_validate_is_announced(self) -> None:
        """Filet de sécurité : validate accepté, la référence relue vaut PROV."""
        data, mock_post, mock_get = await _create({"id": "316", "ref": "PROV", "status": "1"})

        assert data["success"] is True
        assert data["project_id"] == PROJECT_ID
        assert data["status"] == 1
        assert data["ref"] == "PROV"
        warning = data["validate_warning"]
        assert "PROV" in warning
        assert f"id={PROJECT_ID}" in warning
        assert "validation_warning" not in data
        assert mock_post.await_count == 2
        mock_get.assert_awaited_once_with(f"projects/{PROJECT_ID}")

    async def test_literal_auto_is_announced(self) -> None:
        """Une instance qui ne numérote pas garderait la chaîne « auto » comme référence."""
        data, _, _ = await _create({"ref": "auto", "status": "1"})

        assert data["ref"] == "auto"
        assert "auto" in data["validate_warning"]
        assert "uk_projet_ref" in data["validate_warning"]

    async def test_final_ref_comes_from_the_read_back_without_warning(self) -> None:
        data, _, _ = await _create({"id": "316", "ref": REF, "status": "1"})

        assert data["ref"] == REF
        assert data["status"] == 1
        assert REF in data["message"]
        assert "validate_warning" not in data
        assert "validation_warning" not in data
        assert "refetch_warning" not in data

    @pytest.mark.parametrize(
        "project",
        [
            {"ref": REF, "status": 1},
            {"ref": REF, "fk_statut": "1"},
            {"ref": REF, "fk_statut": 1, "status": 1},
        ],
    )
    async def test_status_one_is_read_in_every_form(self, project: dict) -> None:
        data, _, _ = await _create(project)

        assert data["status"] == 1
        assert "validate_warning" not in data

    @pytest.mark.parametrize(
        "project",
        [{"ref": REF, "status": "0"}, {"ref": REF, "fk_statut": 0}],
    )
    async def test_status_read_back_wins_over_the_accepted_route(self, project: dict) -> None:
        """Route acceptée, projet relu en brouillon : la réponse dit ce qui est relu."""
        data, _, _ = await _create(project)

        assert data["status"] == 0
        assert data["ref"] == REF
        assert "statut" in data["validate_warning"]

    async def test_ref_and_status_problems_are_both_named(self) -> None:
        data, _, _ = await _create({"ref": "PROV", "status": 0})

        assert data["status"] == 0
        assert "PROV" in data["validate_warning"]
        assert "statut" in data["validate_warning"]

    @pytest.mark.parametrize("error", READ_FAILURES, ids=READ_FAILURE_IDS)
    async def test_read_back_failure_is_no_longer_silent(self, error: Exception) -> None:
        """Le `except: pass` de la relecture rendait le même PROV sans avertissement."""
        data, mock_post, mock_get = await _create(error)

        assert data["success"] is True
        assert data["project_id"] == PROJECT_ID
        assert data["status"] == 1
        assert data["ref"] is None
        assert "non confirmée" in data["message"]
        warning = data["validate_warning"]
        assert "relecture impossible" in warning
        assert "non confirm" in warning
        assert warning.rstrip(" .")[-1] != ":", "cause absente du message"
        if isinstance(error, httpx.ReadTimeout):
            assert "ReadTimeout" in warning
        # Écriture déjà acceptée : ni création ni validation rejouée.
        assert mock_post.await_count == 2
        assert mock_get.await_count == 1

    @pytest.mark.parametrize("project", UNUSABLE_READS, ids=UNUSABLE_READ_IDS)
    async def test_read_back_without_ref_does_not_invent_one(self, project) -> None:
        data, _, _ = await _create(project)

        assert data["status"] == 1
        assert data["ref"] is None
        assert "aucune référence" in data["validate_warning"]
        assert "vaut encore" not in data["validate_warning"]


@pytest.mark.asyncio
class TestCreateProjectRefusedValidate:
    async def test_refused_validate_keeps_its_warning_and_reports_the_ref(self) -> None:
        data, mock_post, mock_get = await _create(
            {"ref": REF, "status": "0"}, post=[PROJECT_ID, _http_error(500)],
        )

        assert data["success"] is True
        assert data["status"] == 0
        assert data["ref"] == REF
        assert "brouillon" in data["validation_warning"]
        assert "Erreur 500" in data["validation_warning"], "cause absente du message"
        assert "PROV" not in data["validation_warning"]
        assert "validate_warning" not in data
        assert mock_post.await_count == 2
        mock_get.assert_awaited_once_with(f"projects/{PROJECT_ID}")

    async def test_lost_response_with_applied_validate_shows_the_real_status(self) -> None:
        """Réponse perdue, validation appliquée : la relecture dit le vrai statut, sans rejeu."""
        data, mock_post, _ = await _create(
            {"ref": REF, "status": "1"}, post=[PROJECT_ID, httpx.ReadTimeout("")],
        )

        assert data["status"] == 1
        assert data["ref"] == REF
        assert "ReadTimeout" in data["validation_warning"]
        assert "statut" in data["validate_warning"]
        assert mock_post.await_count == 2

    async def test_refused_validate_and_failed_read_stay_explicit(self) -> None:
        data, _, _ = await _create(_http_error(503), post=[PROJECT_ID, _http_error(500)])

        assert data["status"] == 0
        assert data["ref"] is None
        assert "validation_warning" in data
        assert "relecture impossible" in data["validate_warning"]


@pytest.mark.asyncio
class TestCreateProjectDraft:
    async def test_draft_gets_its_number_without_validate(self) -> None:
        data, mock_post, mock_get = await _create(
            {"ref": REF, "status": "0"}, post=[PROJECT_ID], status=0,
        )

        assert data["status"] == 0
        assert data["ref"] == REF
        assert "validate_warning" not in data
        assert "refetch_warning" not in data
        assert mock_post.await_count == 1
        mock_get.assert_awaited_once_with(f"projects/{PROJECT_ID}")

    @pytest.mark.parametrize("error", READ_FAILURES, ids=READ_FAILURE_IDS)
    async def test_draft_read_failure_is_announced(self, error: Exception) -> None:
        data, mock_post, _ = await _create(error, post=[PROJECT_ID], status=0)

        assert data["success"] is True
        assert data["status"] == 0
        assert data["ref"] is None
        assert "relecture impossible" in data["refetch_warning"]
        assert "validate_warning" not in data
        assert mock_post.await_count == 1

    @pytest.mark.parametrize("ref", ["auto", "PROV"])
    async def test_draft_placeholder_ref_is_announced(self, ref: str) -> None:
        data, _, _ = await _create({"ref": ref, "status": "0"}, post=[PROJECT_ID], status=0)

        assert data["ref"] == ref
        assert ref in data["refetch_warning"]
        assert "validate_warning" not in data

    async def test_draft_read_back_as_open_is_announced(self) -> None:
        data, _, _ = await _create({"ref": REF, "status": "1"}, post=[PROJECT_ID], status=0)

        assert data["status"] == 1
        assert "statut" in data["refetch_warning"]


@pytest.mark.asyncio
class TestCreateProjectFailureBeforeNumbering:
    async def test_creation_failure_stops_before_validate(self) -> None:
        with (
            patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as mock_post,
            patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as mock_get,
        ):
            mock_post.side_effect = _http_error(500, "Duplicate entry 'PJ2611-0042-1'")
            result = await dolibarr_create_project(title="Projet test")

        assert "PJ2611-0042-1" in result
        assert mock_post.await_count == 1
        mock_get.assert_not_awaited()


@pytest.mark.asyncio
class TestGetProjectOpportunity:
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_opportunity_fields_are_returned_raw(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = {
            "id": "316", "ref": REF, "title": "Projet",
            "opp_status": "2", "opp_amount": "10000.00000000",
        }

        data = json.loads(await dolibarr_get_project(PROJECT_ID))

        assert data["opp_status"] == "2"
        assert data["opp_amount"] == "10000.00000000"

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_zero_amount_is_not_mistaken_for_absent(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = {"id": "316", "opp_status": 0, "opp_amount": 0}

        data = json.loads(await dolibarr_get_project(PROJECT_ID))

        assert data["opp_status"] == 0
        assert data["opp_amount"] == 0

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_keys_stay_present_without_opportunity(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = {"id": "316", "ref": REF, "title": "Projet"}

        data = json.loads(await dolibarr_get_project(PROJECT_ID))

        assert data["opp_status"] is None
        assert data["opp_amount"] is None

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_fields_also_come_back_when_reading_by_ref(self, mock_get: AsyncMock) -> None:
        mock_get.side_effect = [
            [{"id": "316"}],
            {"id": "316", "ref": REF, "opp_status": "3", "opp_amount": "500"},
        ]

        data = json.loads(await dolibarr_get_project(ref=REF))

        assert data["opp_status"] == "3"
        assert data["opp_amount"] == "500"

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_existing_keys_are_kept(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = {"id": "316", "ref": REF, "title": "Projet"}

        data = json.loads(await dolibarr_get_project(PROJECT_ID))

        assert {
            "id", "ref", "title", "status", "thirdparty", "date_start", "date_end", "budget",
            "description", "description_length", "description_truncated",
            "note_public", "note_public_length", "note_public_truncated", "usage_bill_time",
        } <= set(data)
