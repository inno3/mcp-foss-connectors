"""Contrat de l'opportunité et du statut d'un projet, sans service distant.

Constaté en réel le 03/11/2026 sur un projet de test, avec la version 1.8.12 :
- opp_status="prospect", valeur documentée, a effacé le statut en annonçant un succès :
  Project::update() met NULL, sans erreur, tout opp_status non numérique ;
- status=0 n'a rien changé, succès annoncé aussi : le connecteur envoyait fk_statut,
  alors que Project::update() écrit la propriété status ;
- status="0,1,2" sur la liste a rendu total=0 alors que le projet cherché existait.
Lu dans le cœur 24.0.1 : sans usage_opportunity, la carte du projet masque l'opportunité,
et les montants pondérés se calculent sur opp_percent, que l'API ne déduit pas du statut.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp.server import (
    dolibarr_create_project,
    dolibarr_get_project,
    dolibarr_list_projects,
    dolibarr_update_project,
)

PROJECT_ID = 317
REF = "PJ2611-0223"

KNOWN_STATUSES = [
    ("prospect", "1", "0"),
    ("qualification", "2", "20"),
    ("proposal", "3", "40"),
    ("negotiation", "4", "60"),
    ("won", "6", "100"),
    ("lost", "7", "0"),
    ("PROSP", "1", "0"),
    ("QUAL", "2", "20"),
    ("PROPO", "3", "40"),
    ("NEGO", "4", "60"),
    ("PENDING", "5", "50"),
    ("WON", "6", "100"),
    ("LOST", "7", "0"),
    ("Won", "6", "100"),
    (" won ", "6", "100"),
    ("1", "1", "0"),
    ("6", "6", "100"),
]
REFUSED_STATUSES = ["PROSPEC", "gagné", "0", "-1", "1.5", "1,2", "   "]


async def _update(put_result=None, **kwargs):
    """Met à jour un projet ; rend (réponse décodée, mock PUT)."""
    with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = put_result
        result = await dolibarr_update_project(PROJECT_ID, **kwargs)
    return json.loads(result), mock_put


async def _create(put_result, **kwargs):
    """Crée un projet ouvert ; rend (réponse décodée, mock POST, mock PUT)."""
    with (
        patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as mock_post,
        patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as mock_put,
        patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as mock_get,
    ):
        mock_post.side_effect = [PROJECT_ID, {"success": {"code": 200}}]
        mock_put.return_value = put_result
        mock_get.return_value = {"ref": REF, "status": "1"}
        result = await dolibarr_create_project(title="Projet test", **kwargs)
    return json.loads(result), mock_post, mock_put


@pytest.mark.asyncio
class TestOppStatusTranslation:
    @pytest.mark.parametrize(("given", "rowid", "percent"), KNOWN_STATUSES)
    async def test_update_sends_the_dictionary_id(self, given: str, rowid: str, percent: str) -> None:
        data, mock_put = await _update({"opp_status": rowid}, opp_status=given)

        payload = mock_put.call_args.args[1]
        assert payload["opp_status"] == rowid
        assert payload["opp_percent"] == percent
        assert payload["usage_opportunity"] == "1"
        assert "update_warning" not in data

    @pytest.mark.parametrize(("given", "rowid", "percent"), KNOWN_STATUSES)
    async def test_create_sends_the_dictionary_id(self, given: str, rowid: str, percent: str) -> None:
        data, mock_post, mock_put = await _create({"opp_status": rowid}, opp_status=given)

        payload = mock_put.call_args.args[1]
        assert payload["opp_status"] == rowid
        assert payload["opp_percent"] == percent
        assert payload["usage_opportunity"] == "1"
        assert "opp_status" not in mock_post.call_args_list[0].args[1]
        assert "enrich_warning" not in data

    async def test_id_of_a_custom_dictionary_passes_without_percent(self) -> None:
        """Le pourcentage d'une ligne ajoutée à l'instance n'est pas connu du connecteur."""
        data, mock_put = await _update({"opp_status": "12"}, opp_status="12")

        payload = mock_put.call_args.args[1]
        assert payload["opp_status"] == "12"
        assert "opp_percent" not in payload
        assert payload["usage_opportunity"] == "1"
        assert "update_warning" not in data

    @pytest.mark.parametrize("given", REFUSED_STATUSES)
    async def test_update_refuses_an_unknown_value_before_writing(self, given: str) -> None:
        """Le cas constaté : sans ce refus, Dolibarr efface le statut en place."""
        data, mock_put = await _update(opp_status=given)

        assert data["success"] is False
        assert "prospect" in data["error"]
        mock_put.assert_not_awaited()

    @pytest.mark.parametrize("given", REFUSED_STATUSES)
    async def test_create_refuses_an_unknown_value_before_creating(self, given: str) -> None:
        data, mock_post, mock_put = await _create(None, opp_status=given)

        assert data["success"] is False
        assert "prospect" in data["error"]
        mock_post.assert_not_awaited()
        mock_put.assert_not_awaited()

    async def test_amount_alone_does_not_turn_on_opportunity_tracking(self) -> None:
        """La carte refuse d'enregistrer un montant suivi sans statut (ErrorOppStatusRequiredIfAmount)."""
        _, mock_put = await _update({"opp_amount": "500"}, opp_amount=500)
        assert "usage_opportunity" not in mock_put.call_args.args[1]

        _, _, mock_put = await _create({"opp_amount": "500"}, opp_amount=500)
        assert "usage_opportunity" not in mock_put.call_args.args[1]

    async def test_no_opportunity_field_is_sent_by_default(self) -> None:
        _, mock_put = await _update(title="Nouveau titre")

        assert mock_put.call_args.args[1] == {"title": "Nouveau titre"}

    async def test_create_warns_when_the_status_did_not_stick(self) -> None:
        data, _, _ = await _create({"opp_status": None}, opp_status="won")

        assert data["success"] is True
        assert "opp_status" in data["enrich_warning"]

    async def test_refused_enrichment_gives_a_cause_even_without_message(self) -> None:
        """Un délai dépassé porte un message vide : l'avertissement n'en reste pas muet."""
        with (
            patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as mock_post,
            patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as mock_put,
            patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as mock_get,
        ):
            mock_post.side_effect = [PROJECT_ID, {"success": {"code": 200}}]
            mock_put.side_effect = httpx.ReadTimeout("")
            mock_get.return_value = {"ref": REF, "status": "1"}
            data = json.loads(await dolibarr_create_project(title="Projet test", opp_status="won"))

        assert data["success"] is True
        assert "ReadTimeout" in data["enrich_warning"] or "délai" in data["enrich_warning"].lower()


@pytest.mark.asyncio
class TestUpdateProjectStatus:
    @pytest.mark.parametrize("status", [0, 1, 2])
    async def test_status_is_the_field_dolibarr_writes(self, status: int) -> None:
        """status=0 compris : un zéro n'est pas une absence."""
        data, mock_put = await _update({"status": str(status)}, status=status)

        payload = mock_put.call_args.args[1]
        assert payload["status"] == str(status)
        assert "fk_statut" not in payload
        assert data["success"] is True
        assert data["updated_fields"] == ["status"]
        assert "update_warning" not in data

    @pytest.mark.parametrize("status", [3, 9])
    async def test_unknown_status_is_refused_before_writing(self, status: int) -> None:
        """Project::update() écrirait la valeur telle quelle."""
        data, mock_put = await _update(status=status)

        assert data["success"] is False
        mock_put.assert_not_awaited()

    async def test_status_is_not_sent_by_default(self) -> None:
        _, mock_put = await _update(title="Nouveau titre")

        assert "status" not in mock_put.call_args.args[1]

    async def test_warns_when_the_status_did_not_change(self) -> None:
        """Le cas constaté : succès annoncé, statut inchangé."""
        data, _ = await _update({"status": "1"}, status=0)

        assert data["success"] is True
        assert "status" in data["update_warning"]
        assert "1" in data["update_warning"]

    async def test_warns_when_the_opportunity_status_was_lost(self) -> None:
        data, _ = await _update({"status": "1", "opp_status": None}, opp_status="won")

        assert "opp_status" in data["update_warning"]

    @pytest.mark.parametrize("answer", [None, [], "ok"])
    async def test_warns_when_the_answer_does_not_confirm(self, answer) -> None:
        data, _ = await _update(answer, status=2)

        assert data["success"] is True
        assert "non confirmé" in data["update_warning"]

    @pytest.mark.parametrize("answer", [None, {"status": "1"}])
    async def test_other_fields_are_not_compared(self, answer) -> None:
        data, _ = await _update(answer, title="Nouveau titre")

        assert "update_warning" not in data

    async def test_numeric_answers_are_compared_by_value(self) -> None:
        data, _ = await _update({"status": 2, "opp_status": 6}, status=2, opp_status="won")

        assert "update_warning" not in data

    async def test_failed_write_keeps_the_dolibarr_message(self) -> None:
        request = httpx.Request("PUT", "http://test")
        response = httpx.Response(500, request=request, text="Duplicate entry")
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as mock_put:
            mock_put.side_effect = httpx.HTTPStatusError("", request=request, response=response)
            result = await dolibarr_update_project(PROJECT_ID, status=2)

        assert "Duplicate entry" in result
        assert '"success": true' not in result


@pytest.mark.asyncio
class TestGetProjectTracking:
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_percent_and_tracking_are_returned_raw(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = {"id": "317", "ref": REF, "opp_percent": "20.00", "usage_opportunity": 1}

        data = json.loads(await dolibarr_get_project(PROJECT_ID))

        assert data["opp_percent"] == "20.00"
        assert data["usage_opportunity"] == 1

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_tracking_off_is_not_mistaken_for_absent(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = {"id": "317", "opp_percent": 0, "usage_opportunity": 0}

        data = json.loads(await dolibarr_get_project(PROJECT_ID))

        assert data["opp_percent"] == 0
        assert data["usage_opportunity"] == 0

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_keys_stay_present(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = {"id": "317", "ref": REF}

        data = json.loads(await dolibarr_get_project(PROJECT_ID))

        assert data["opp_percent"] is None
        assert data["usage_opportunity"] is None


@pytest.mark.asyncio
class TestListProjectsStatusFilter:
    @pytest.mark.parametrize("status", ["0,1,2", "open", "3", "-1"])
    async def test_unknown_filter_is_refused(self, status: str) -> None:
        """Le cas constaté : total=0 annoncé comme exact, projet pourtant présent."""
        with patch("dolibarr_mcp.server._scan_list", new_callable=AsyncMock) as mock_scan:
            data = json.loads(await dolibarr_list_projects(status=status))

        assert data["success"] is False
        assert 'status=""' in data["error"]
        mock_scan.assert_not_awaited()

    @pytest.mark.parametrize("status", ["0", "1", "2"])
    async def test_known_filter_reaches_the_query(self, status: str) -> None:
        with patch("dolibarr_mcp.server._scan_list", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            await dolibarr_list_projects(status=status)

        assert mock_scan.call_args.args[1]["sqlfilters"] == f"(t.fk_statut:=:{status})"

    async def test_empty_filter_means_every_status(self) -> None:
        with patch("dolibarr_mcp.server._scan_list", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            await dolibarr_list_projects(status="")

        assert "sqlfilters" not in mock_scan.call_args.args[1]
