"""Tests unitaires pour le serveur MCP Dolibarr."""

import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Définir les variables d'environnement avant l'import
os.environ.setdefault("DOLIBARR_API_KEY", "test-key-123")
os.environ.setdefault("DOLIBARR_URL", "https://dolibarr.test.local")

from dolibarr_mcp import server
from dolibarr_mcp.server import (
    TASK_CONTACTS_ENDPOINT,
    TIMESPENT_ENDPOINT,
    TIMESPENT_ITEM_ENDPOINT,
    TIMESPENT_LIST_ENDPOINT,
    DolibarrAPIError,
    _api_get_list,
    _clamp_limit,
    _dumps,
    _format_doc_line,
    _format_error,
    _line_type_label,
    _num,
    _parse_json_or_raise,
    _seconds_int,
    _seconds_to_hours,
    _strip_html,
    _ts_to_datetime,
    dolibarr_add_invoice_lines,
    dolibarr_add_invoice_payment,
    dolibarr_add_supplier_invoice_payment,
    dolibarr_add_ticket_message,
    dolibarr_assign_task_user,
    dolibarr_build_document,
    dolibarr_cash_forecast,
    dolibarr_close_task,
    dolibarr_close_ticket,
    dolibarr_create_bank_entry,
    dolibarr_create_invoice,
    dolibarr_create_invoice_from_proposal,
    dolibarr_create_project,
    dolibarr_create_supplier_invoice,
    dolibarr_create_task,
    dolibarr_create_ticket,
    dolibarr_dashboard,
    dolibarr_delete_bank_entry,
    dolibarr_delete_invoice,
    dolibarr_delete_invoice_line,
    dolibarr_delete_ticket,
    dolibarr_delete_time_entry,
    dolibarr_get_bank_balance,
    dolibarr_get_invoice,
    dolibarr_get_project,
    dolibarr_get_proposal,
    dolibarr_get_revenue_summary,
    dolibarr_get_supplier_invoice,
    dolibarr_get_thirdparty,
    dolibarr_get_ticket,
    dolibarr_list_bank_accounts,
    dolibarr_list_bank_entries,
    dolibarr_list_bank_statements,
    dolibarr_list_documents,
    dolibarr_list_invoice_payments,
    dolibarr_list_invoices,
    dolibarr_list_payment_types,
    dolibarr_list_products,
    dolibarr_list_projects,
    dolibarr_list_proposals,
    dolibarr_list_supplier_invoices,
    dolibarr_list_task_contacts,
    dolibarr_list_tasks,
    dolibarr_list_thirdparties,
    dolibarr_list_tickets,
    dolibarr_list_time_entries,
    dolibarr_list_users,
    dolibarr_log_time,
    dolibarr_resolve_email,
    dolibarr_set_bank_statement,
    dolibarr_toggle_bank_reconciliation,
    dolibarr_transfer_between_accounts,
    dolibarr_unassign_task_user,
    dolibarr_update_invoice,
    dolibarr_update_ticket,
    dolibarr_update_time_entry,
    dolibarr_upload_document,
    dolibarr_validate_supplier_invoice,
)


class TestHelpers:
    """Tests des fonctions utilitaires."""

    def test_clamp_limit_normal(self) -> None:
        assert _clamp_limit(50) == 50

    def test_clamp_limit_too_high(self) -> None:
        assert _clamp_limit(999) == 100

    def test_clamp_limit_too_low(self) -> None:
        assert _clamp_limit(0) == 1

    def test_clamp_limit_negative(self) -> None:
        assert _clamp_limit(-5) == 1

    def test_dumps_utf8(self) -> None:
        result = _dumps({"nom": "Réunion café"})
        assert "Réunion café" in result
        parsed = json.loads(result)
        assert parsed["nom"] == "Réunion café"

    def test_format_error_401(self) -> None:
        import httpx
        response = httpx.Response(401, request=httpx.Request("GET", "http://test"))
        exc = httpx.HTTPStatusError("", request=response.request, response=response)
        msg = _format_error(exc)
        assert "401" in msg
        assert "clé API" in msg

    def test_format_error_404(self) -> None:
        import httpx
        response = httpx.Response(404, request=httpx.Request("GET", "http://test"))
        exc = httpx.HTTPStatusError("", request=response.request, response=response)
        msg = _format_error(exc)
        assert "404" in msg


@pytest.mark.asyncio
class TestDolibarrProjects:
    """Tests des tools projets."""

    @patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock)
    async def test_list_projects_ok(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {
                "id": "1",
                "ref": "PRJ001",
                "title": "Projet Test",
                "fk_statut": "1",
                "date_start": "2026-01-01",
                "date_end": "2026-12-31",
                "budget_amount": "10000",
                "description": "Un projet de test",
            }
        ]
        result = await dolibarr_list_projects(status="1")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["projects"][0]["ref"] == "PRJ001"

    @patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock)
    async def test_list_projects_with_search(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"id": "1", "ref": "PRJ001", "title": "Alpha", "description": ""},
            {"id": "2", "ref": "PRJ002", "title": "Beta", "description": ""},
        ]
        result = await dolibarr_list_projects(search="alpha")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["projects"][0]["ref"] == "PRJ001"


@pytest.mark.asyncio
class TestDolibarrCreateProject:
    """Tests de dolibarr_create_project — contournement bug PROV-1.

    Le bug PROV-1 : un POST /projects avec payload complet et status=1
    déclenche le renommage immédiat de ref PROV → PJ-YYMM-XXXX, mais la
    transaction casse quand le payload est lourd (opp_status + montants +
    notes), laissant la ref à PROV → la création suivante viole la
    contrainte unique sur llx_projet.ref.

    Le contournement implémenté est en 3 étapes : POST minimal + PUT
    enrichissement + POST /validate. Ces tests valident chaque chemin.
    """

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_full_payload_3_steps(
        self,
        mock_put: AsyncMock,
        mock_post: AsyncMock,
        mock_get: AsyncMock,
    ) -> None:
        """Payload complet → 3 appels : POST minimal, PUT enrich, POST validate."""
        # POST projects (step 1) renvoie l'id, POST validate (step 3) renvoie ok
        mock_post.side_effect = [249, {"success": "validated"}]
        mock_put.return_value = {"success": "updated"}
        mock_get.return_value = {"id": "249", "ref": "PJ2605-0192", "title": "Test"}

        result = await dolibarr_create_project(
            title="GovTech Connect — DPG Sustainability",
            thirdparty_id=42,
            description="Acccompagnement sustainability",
            date_start="2026-05-12",
            date_end="2026-12-31",
            status=1,
            opp_status="PROSPEC",
            opp_amount=10000,
            budget_amount=10000,
            note_public="public",
            note_private="private",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["project_id"] == 249
        assert data["status"] == 1
        assert data["ref"] == "PJ2605-0192"
        assert "enrich_warning" not in data
        assert "validation_warning" not in data

        # Vérifier l'ordre exact des appels
        assert mock_post.call_count == 2
        # Step 1 : POST minimal
        step1_args = mock_post.call_args_list[0]
        assert step1_args.args[0] == "projects"
        step1_payload = step1_args.args[1]
        assert step1_payload["ref"] == "PROV"
        assert step1_payload["title"] == "GovTech Connect — DPG Sustainability"
        assert step1_payload["fk_statut"] == "0"  # toujours 0 à la création
        assert step1_payload["socid"] == "42"
        # Pas de champs lourds en step 1
        assert "opp_status" not in step1_payload
        assert "opp_amount" not in step1_payload
        assert "note_private" not in step1_payload

        # Step 2 : PUT enrich
        assert mock_put.call_count == 1
        step2_args = mock_put.call_args_list[0]
        assert step2_args.args[0] == "projects/249"
        step2_payload = step2_args.args[1]
        assert step2_payload["description"] == "Acccompagnement sustainability"
        assert step2_payload["opp_status"] == "PROSPEC"
        assert step2_payload["opp_amount"] == "10000"
        assert step2_payload["budget_amount"] == "10000"
        assert step2_payload["note_public"] == "public"
        assert step2_payload["note_private"] == "private"
        # PUT ne doit PAS contenir ref ni fk_statut
        assert "ref" not in step2_payload
        assert "fk_statut" not in step2_payload

        # Step 3 : POST validate
        step3_args = mock_post.call_args_list[1]
        assert step3_args.args[0] == "projects/249/validate"

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_minimal_payload_no_status(
        self,
        mock_post: AsyncMock,
    ) -> None:
        """Title seul, status=0 → 1 seul appel (pas de PUT, pas de validate)."""
        mock_post.return_value = 100

        result = await dolibarr_create_project(title="Brouillon", status=0)
        data = json.loads(result)
        assert data["success"] is True
        assert data["project_id"] == 100
        assert data["status"] == 0
        assert data["ref"] == "PROV"
        assert mock_post.call_count == 1  # un seul POST, pas de validate

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_step1_failure_stops(
        self,
        mock_post: AsyncMock,
    ) -> None:
        """Échec étape 1 → ne tente pas les suivantes, remonte l'erreur."""
        import httpx
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(500, request=request, text="Duplicate entry 'PROV-1'")
        mock_post.side_effect = httpx.HTTPStatusError("", request=request, response=response)

        result = await dolibarr_create_project(title="Test", description="x", status=1)
        # Ne lève pas, mais retourne l'erreur formatée
        assert "500" in result or "PROV-1" in result
        # Un seul appel : pas de fallback vers PUT/validate
        assert mock_post.call_count == 1

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_validation_failure_returns_warning(
        self,
        mock_put: AsyncMock,
        mock_post: AsyncMock,
        mock_get: AsyncMock,
    ) -> None:
        """Validation échoue → projet créé + validation_warning, pas d'erreur."""
        import httpx
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(404, request=request, text="Not found")
        mock_post.side_effect = [
            249,
            httpx.HTTPStatusError("", request=request, response=response),
        ]
        mock_put.return_value = {"success": "updated"}

        result = await dolibarr_create_project(
            title="Test", description="x", status=1,
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["project_id"] == 249
        assert data["status"] == 0  # validation pas passée
        assert data["ref"] == "PROV"
        assert "validation_warning" in data
        assert "brouillon" in data["validation_warning"]

    async def test_empty_title_rejected(self) -> None:
        """Title vide → message d'erreur explicite, pas d'appel API."""
        result = await dolibarr_create_project(title="")
        assert "obligatoire" in result


@pytest.mark.asyncio
class TestDolibarrInvoices:
    """Tests des tools facturation."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_list_invoices_unpaid(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {
                "id": "10",
                "ref": "FA2603-0001",
                "total_ttc": "1200.00",
                "total_ht": "1000.00",
                "date": "2026-03-01",
                "date_lim_reglement": "2026-04-01",
                "fk_statut": "1",
                "remaintopay": "1200.00",
            }
        ]
        result = await dolibarr_list_invoices(status="unpaid")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["invoices"][0]["total_ttc"] == 1200.0

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_list_invoices_min_amount(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"id": "1", "ref": "FA01", "total_ttc": "100.00", "fk_statut": "1"},
            {"id": "2", "ref": "FA02", "total_ttc": "5000.00", "fk_statut": "1"},
        ]
        result = await dolibarr_list_invoices(min_amount=1000)
        data = json.loads(result)
        assert data["count"] == 1
        assert data["invoices"][0]["ref"] == "FA02"


@pytest.mark.asyncio
class TestDolibarrDashboard:
    """Tests du dashboard."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_dashboard_aggregation(self, mock_api: AsyncMock) -> None:
        async def side_effect(endpoint: str, params: dict = None) -> list:
            if "projects" in endpoint:
                return [{"id": "1"}, {"id": "2"}]
            if "invoices" in endpoint:
                return [{"id": "10", "total_ttc": "1000", "remaintopay": "1000", "date_lim_reglement": "2099-01-01"}]
            if "proposals" in endpoint:
                return [{"id": "20", "total_ht": "5000"}]
            if "tickets" in endpoint:
                return [{"id": "30"}, {"id": "31"}]
            return []

        mock_api.side_effect = side_effect
        result = await dolibarr_dashboard()
        data = json.loads(result)
        assert data["projects_open"] == 2
        assert data["invoices_unpaid_count"] == 1
        assert data["proposals_pending_count"] == 1
        assert data["tickets_open_count"] == 2


# ---------------------------------------------------------------------------
# Régression : Réponses non-JSON et 404 "no results" — bug 14/05/2026
# (cf. messages "Erreur : Expecting value: line 1 column 1 (char 0)" remontés
# par list_thirdparties / list_projects pendant une tâche Cowork)
# ---------------------------------------------------------------------------


class TestNonJSONResponseHandling:
    """_parse_json_or_raise doit transformer un corps non-JSON en erreur lisible."""

    def _resp(self, status: int, body: str, content_type: str = "application/json"):
        import httpx
        return httpx.Response(
            status,
            content=body.encode("utf-8"),
            headers={"content-type": content_type},
            request=httpx.Request("GET", "http://test"),
        )

    def test_empty_body_raises_dolibarr_api_error(self) -> None:
        resp = self._resp(200, "")
        with pytest.raises(DolibarrAPIError) as exc_info:
            _parse_json_or_raise(resp, "GET", "/thirdparties")
        msg = str(exc_info.value)
        assert "HTTP 200" in msg
        assert "corps vide" in msg

    def test_html_body_raises_dolibarr_api_error(self) -> None:
        resp = self._resp(200, "<html>502 Bad Gateway</html>", "text/html")
        with pytest.raises(DolibarrAPIError) as exc_info:
            _parse_json_or_raise(resp, "GET", "/projects")
        msg = str(exc_info.value)
        assert "text/html" in msg
        assert "<html>" in msg

    def test_valid_json_returns_payload(self) -> None:
        resp = self._resp(200, '[{"id":1}]')
        out = _parse_json_or_raise(resp, "GET", "/x")
        assert out == [{"id": 1}]

    def test_format_error_for_dolibarr_api_error(self) -> None:
        exc = DolibarrAPIError(200, "Réponse non-JSON (corps vide)", "")
        msg = _format_error(exc)
        assert msg.startswith("Erreur Dolibarr :")
        assert "non-JSON" in msg


@pytest.mark.asyncio
class TestListEndpoint404AsEmpty:
    """_api_get_list traite HTTP 404 'No X found' comme une liste vide."""

    @patch("dolibarr_mcp.server._api_request", new_callable=AsyncMock)
    async def test_thirdparties_no_match_returns_empty_list(self, mock_req: AsyncMock) -> None:
        import httpx
        body = b'{"error":{"code":404,"message":"Not Found: No third parties found"}}'
        resp = httpx.Response(
            404, content=body,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "http://test"),
        )
        mock_req.side_effect = httpx.HTTPStatusError("404", request=resp.request, response=resp)
        out = await _api_get_list("thirdparties", {"limit": 20})
        assert out == []

    @patch("dolibarr_mcp.server._api_request", new_callable=AsyncMock)
    async def test_real_404_propagates(self, mock_req: AsyncMock) -> None:
        import httpx
        body = b'{"error":{"code":404,"message":"API endpoint not found"}}'
        resp = httpx.Response(
            404, content=body,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "http://test"),
        )
        mock_req.side_effect = httpx.HTTPStatusError("404", request=resp.request, response=resp)
        with pytest.raises(httpx.HTTPStatusError):
            await _api_get_list("thirdparties", {"limit": 20})

    @patch("dolibarr_mcp.server._api_request", new_callable=AsyncMock)
    async def test_list_thirdparties_no_match_returns_count_zero(self, mock_req: AsyncMock) -> None:
        import httpx
        body = b'{"error":{"code":404,"message":"Not Found: No third parties found"}}'
        resp = httpx.Response(
            404, content=body,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "http://test"),
        )
        mock_req.side_effect = httpx.HTTPStatusError("404", request=resp.request, response=resp)
        result = await dolibarr_list_thirdparties(search="XYZNOMATCH")
        data = json.loads(result)
        assert data["count"] == 0
        assert data["thirdparties"] == []


# =============================================================================
# Tests des bugs corrigés 2026-05-21
# =============================================================================

@pytest.mark.asyncio
class TestBug20260521AddProposalLines:
    """Bug #1 — dolibarr_add_proposal_lines envoyait un dict nu au lieu d'un tableau.

    Cause racine (api_proposals.class.php::postLines, Dolibarr v23) :

        foreach ($request_data as $TData) {          # itère sur les CLÉS du dict
            if (empty($TData[0])) {
                $TData = array($TData);              # wrap si scalaire sans clé [0]
            }
            foreach ($TData as $lineData) {          # itère sur la valeur wrappée
                $line = (object) $lineData;
                $propal->addline($line->desc, ...);  # stdClass vide → ligne vide
            }
        }

    Pattern empirique : N clés → N-1 lignes vides
      - iter 1 : valeur de "desc" (string) → foreach() PHP 8 warning, 0 ligne créée
      - iter 2..N : scalaire → empty($scalar[0]) = true → wrappé → (object)$scalar
                   → stdClass sans propriétés → addline(null, ...) → ligne vide

    Avec [line_data] : outer foreach obtient 1 élément (le dict entier) → correct.

    Champs REST attendus : desc, subprice, qty, tva_tx, product_type.
    Les champs HTML "prod_entry_mode" et "type" sont spécifiques au formulaire
    Dolibarr et ne sont PAS des paramètres de l'API REST.
    """

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_add_lines_sends_list_not_dict(
        self, mock_get: AsyncMock, mock_post: AsyncMock
    ) -> None:
        """api_post doit recevoir une LISTE [line_data], pas un dict nu."""
        from dolibarr_mcp.server import dolibarr_add_proposal_lines

        mock_post.return_value = 42  # ID de ligne retourné par Dolibarr
        mock_get.return_value = {
            "id": "10", "ref": "(PROV10)", "fk_statut": "0", "statut": "0",
            "lines": [], "total_ht": "0", "total_ttc": "0",
        }

        result = await dolibarr_add_proposal_lines(
            proposal_id=10,
            lines=[{"description": "Test forfait", "qty": 1, "subprice": 750, "tva_tx": 20}],
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["lines_added"] == 1
        assert data["total_ht"] == 750.0

        # VÉRIFICATION CRITIQUE : api_post doit avoir été appelé avec une liste
        assert mock_post.call_count == 1
        _, call_kwargs = mock_post.call_args
        positional_args = mock_post.call_args[0]
        # api_post(endpoint, data) — le 2e arg doit être une liste
        payload = positional_args[1] if len(positional_args) > 1 else call_kwargs.get("data")
        assert isinstance(payload, list), (
            f"Bug #1 régression : api_post doit recevoir une liste [line_data], "
            f"reçu {type(payload).__name__}"
        )
        assert len(payload) == 1
        assert payload[0]["desc"] == "Test forfait"
        assert payload[0]["qty"] == 1.0
        assert payload[0]["subprice"] == 750.0

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_add_lines_empty_description_rejected(
        self, mock_post: AsyncMock
    ) -> None:
        """Une ligne sans description doit être rejetée avant appel API."""
        from dolibarr_mcp.server import dolibarr_add_proposal_lines

        result = await dolibarr_add_proposal_lines(
            proposal_id=10,
            lines=[{"qty": 1, "subprice": 750}],  # pas de description
        )
        assert "description manquante" in result.lower() or "description" in result
        mock_post.assert_not_called()

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_add_lines_multiple_lines_sends_one_post_per_line(
        self, mock_post: AsyncMock
    ) -> None:
        """Chaque ligne doit déclencher un POST séparé (appel séquentiel)."""
        from dolibarr_mcp.server import dolibarr_add_proposal_lines

        mock_post.side_effect = [41, 42]  # IDs retournés pour chaque ligne

        result = await dolibarr_add_proposal_lines(
            proposal_id=10,
            lines=[
                {"description": "Ligne A", "qty": 1, "subprice": 500, "tva_tx": 20},
                {"description": "Ligne B", "qty": 2, "subprice": 100, "tva_tx": 20},
            ],
        )
        data = json.loads(result)
        assert data["lines_added"] == 2
        assert data["total_ht"] == 700.0
        assert mock_post.call_count == 2
        # Vérifier que chaque appel envoie bien une liste
        for call in mock_post.call_args_list:
            payload = call[0][1]
            assert isinstance(payload, list), "Chaque POST doit envoyer une liste"


@pytest.mark.asyncio
class TestBug20260521ValidateProposal:
    """Bug #2 — dolibarr_validate_proposal envoyait {} sans notrigger.
    Dolibarr v23 exige notrigger comme entier (0 ou 1), HTTP 400 sinon.
    """

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_validate_proposal_sends_notrigger_int(
        self, mock_get: AsyncMock, mock_post: AsyncMock
    ) -> None:
        """api_post doit recevoir {"notrigger": 0} (entier, pas booléen, pas absent)."""
        from dolibarr_mcp.server import dolibarr_validate_proposal

        mock_post.return_value = {"statut": 1, "ref": "PR2605-0001"}
        mock_get.return_value = {
            "id": "10", "ref": "PR2605-0001", "fk_statut": "1", "status": "1",
            "total_ttc": "900",
        }

        result = await dolibarr_validate_proposal(proposal_id=10)
        data = json.loads(result)
        assert data["success"] is True

        # VÉRIFICATION CRITIQUE : payload doit contenir notrigger=0 (entier)
        assert mock_post.call_count == 1
        payload = mock_post.call_args[0][1]
        assert isinstance(payload, dict), "Payload doit être un dict"
        assert "notrigger" in payload, "Bug #2 régression : 'notrigger' absent du payload"
        assert payload["notrigger"] == 0, (
            f"Bug #2 régression : notrigger doit être l'entier 0, reçu {payload['notrigger']!r}"
        )
        assert isinstance(payload["notrigger"], int), (
            f"notrigger doit être un int, pas {type(payload['notrigger']).__name__}"
        )

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_validate_proposal_returns_new_ref(
        self, mock_get: AsyncMock, mock_post: AsyncMock
    ) -> None:
        """Après validation, la nouvelle ref PJ-AAMM-XXXX doit être retournée."""
        from dolibarr_mcp.server import dolibarr_validate_proposal

        mock_post.return_value = 1
        mock_get.return_value = {
            "id": "10", "ref": "PR2605-0042", "fk_statut": "1", "status": "1",
            "total_ttc": "1800",
        }

        result = await dolibarr_validate_proposal(proposal_id=10)
        data = json.loads(result)
        assert data["ref"] == "PR2605-0042"
        assert data["status_label"] == "validée"


@pytest.mark.asyncio
class TestBug20260521ValidateProject:
    """Bug #3 — dolibarr_create_project : étape validate envoyait {} sans notrigger."""

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_create_project_validate_sends_notrigger_int(
        self,
        mock_get: AsyncMock,
        mock_put: AsyncMock,
        mock_post: AsyncMock,
    ) -> None:
        """L'étape validate de create_project doit envoyer {"notrigger": 0}."""
        mock_post.side_effect = [55, {"statut": 1}]  # step1=id, step3=validate
        mock_put.return_value = 55
        mock_get.return_value = {
            "id": "55", "ref": "PJ2605-0007", "fk_statut": "1",
            "title": "Projet test",
        }

        result = await dolibarr_create_project(
            title="Projet test", thirdparty_id=1, status=1
        )
        data = json.loads(result)
        assert data["success"] is True

        # Le 2e appel POST (étape 3 = validate) doit avoir notrigger=0
        assert mock_post.call_count >= 2, "Doit appeler POST au moins 2 fois (create + validate)"
        validate_call = mock_post.call_args_list[1]  # étape 3
        validate_payload = validate_call[0][1]
        assert isinstance(validate_payload, dict)
        assert "notrigger" in validate_payload, (
            "Bug #3 régression : 'notrigger' absent du payload validate projet"
        )
        assert validate_payload["notrigger"] == 0
        assert isinstance(validate_payload["notrigger"], int)


@pytest.mark.asyncio
class TestBug5CreateProposalDateValidity:
    """Bug #5 — dolibarr_create_proposal n'exposait pas date_validity.

    Sans ce paramètre, Dolibarr peut afficher 15/02/1970 :
    quand date_creation est absente ou nulle, certaines versions calculent
    fin_validite = duree_validite * 86400 (ex. 45 j × 86400 = 3 888 000 s
    depuis epoch = 1970-02-15) au lieu de date_creation + N jours.

    Fix : accepter date_validity (YYYY-MM-DD) et envoyer fin_validite comme
    timestamp Unix absolu via _date_str_to_ts().
    """

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_date_validity_sent_as_unix_timestamp(
        self, mock_post: AsyncMock
    ) -> None:
        """fin_validite doit être un timestamp Unix entier (pas une string ISO)."""
        from dolibarr_mcp.server import dolibarr_create_proposal

        mock_post.return_value = 99  # proposal_id retourné

        result = await dolibarr_create_proposal(
            thirdparty_id=1,
            date_validity="2026-07-05",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["proposal_id"] == 99
        assert data["date_validity"] == "2026-07-05"

        # Vérifier que fin_validite est bien un timestamp entier
        call_args = mock_post.call_args[0]
        payload = call_args[1]  # data dict
        assert "fin_validite" in payload, "fin_validite doit être dans le payload"
        fin_validite = payload["fin_validite"]
        assert isinstance(fin_validite, int), (
            f"fin_validite doit être un int (timestamp Unix), reçu {type(fin_validite).__name__}"
        )
        # 2026-07-05 00:00:00 UTC = 1783209600
        assert fin_validite == 1783209600, (
            f"fin_validite attendu 1783209600 (2026-07-05 UTC), reçu {fin_validite}"
        )

    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_no_date_validity_omits_fin_validite(
        self, mock_post: AsyncMock
    ) -> None:
        """Sans date_validity, fin_validite ne doit PAS être dans le payload."""
        from dolibarr_mcp.server import dolibarr_create_proposal

        mock_post.return_value = 100

        result = await dolibarr_create_proposal(thirdparty_id=1)
        data = json.loads(result)
        assert data["success"] is True
        assert data["date_validity"] == "(défaut Dolibarr)"

        call_args = mock_post.call_args[0]
        payload = call_args[1]
        assert "fin_validite" not in payload, (
            "fin_validite ne doit PAS être dans le payload si date_validity est absent"
        )

    async def test_invalid_date_format_rejected(self) -> None:
        """Un format de date invalide doit retourner une erreur sans appel API."""
        from dolibarr_mcp.server import dolibarr_create_proposal

        result = await dolibarr_create_proposal(
            thirdparty_id=1,
            date_validity="05/07/2026",  # format DD/MM/YYYY — invalide
        )
        assert "invalide" in result.lower() or "Format" in result
        assert "05/07/2026" in result

@pytest.mark.asyncio
class TestApiPostAcceptsList:
    """api_post doit accepter une liste en plus d'un dict."""

    @patch("dolibarr_mcp.server._api_request", new_callable=AsyncMock)
    async def test_api_post_with_list_passes_list_to_request(
        self, mock_req: AsyncMock
    ) -> None:
        from dolibarr_mcp.server import api_post

        mock_req.return_value = 99
        await api_post("proposals/10/lines", [{"desc": "Test", "qty": 1}])

        mock_req.assert_called_once()
        _, call_kwargs = mock_req.call_args
        json_payload = call_kwargs.get("json")
        assert isinstance(json_payload, list), (
            f"_api_request doit recevoir json=list, reçu {type(json_payload).__name__}"
        )

    @patch("dolibarr_mcp.server._api_request", new_callable=AsyncMock)
    async def test_api_post_with_dict_passes_dict(self, mock_req: AsyncMock) -> None:
        from dolibarr_mcp.server import api_post

        mock_req.return_value = {"ok": True}
        await api_post("proposals/10/validate", {"notrigger": 0})

        json_payload = mock_req.call_args[1].get("json")
        assert isinstance(json_payload, dict)
        assert json_payload["notrigger"] == 0

    @patch("dolibarr_mcp.server._api_request", new_callable=AsyncMock)
    async def test_api_post_with_none_sends_empty_dict(self, mock_req: AsyncMock) -> None:
        from dolibarr_mcp.server import api_post

        mock_req.return_value = {}
        await api_post("some/endpoint", None)

        json_payload = mock_req.call_args[1].get("json")
        assert json_payload == {}


class TestDateStrToTs:
    """Tests unitaires pour le helper _date_str_to_ts (synchrone)."""

    def test_utc_midnight_2026_07_05(self) -> None:
        """2026-07-05 00:00 UTC = 1783209600."""
        from dolibarr_mcp.server import _date_str_to_ts
        assert _date_str_to_ts("2026-07-05") == 1783209600

    def test_utc_midnight_2026_01_01(self) -> None:
        """2026-01-01 00:00 UTC = 1767225600."""
        from dolibarr_mcp.server import _date_str_to_ts
        assert _date_str_to_ts("2026-01-01") == 1767225600

    def test_raises_on_word(self) -> None:
        from dolibarr_mcp.server import _date_str_to_ts
        with pytest.raises(ValueError):
            _date_str_to_ts("not-a-date")

    def test_raises_on_french_format(self) -> None:
        from dolibarr_mcp.server import _date_str_to_ts
        with pytest.raises(ValueError):
            _date_str_to_ts("05/07/2026")

    def test_strips_whitespace(self) -> None:
        """_date_str_to_ts doit ignorer les espaces autour de la date."""
        from dolibarr_mcp.server import _date_str_to_ts
        assert _date_str_to_ts("  2026-07-05  ") == 1783209600


@pytest.mark.asyncio
class TestListStatusLabels:
    """Régression : les tools *list* doivent calculer status_label / days_late
    à partir du code de statut, sans dépendre d'un champ status_label que
    l'API liste ne renvoie pas (bug : statut vide + 0 jour de retard sur des
    factures pourtant validées et en retard de plusieurs années)."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_list_invoices_computes_label_and_late(self, mock_api: AsyncMock) -> None:
        # L'API liste expose le statut sous "status" (et non "fk_statut"),
        # et ne renvoie PAS de "status_label".
        mock_api.return_value = [
            {
                "id": "83",
                "ref": "FA2306-0681",
                "socid": "87",
                "status": "1",
                "paye": "0",
                "total_ttc": "1800",
                "total_ht": "1500",
                "date": "1686096000",  # 2023-06-07
                "date_lim_reglement": "1686182400",  # 2023-06-08
            }
        ]
        data = json.loads(await dolibarr_list_invoices(status="unpaid"))
        inv = data["invoices"][0]
        assert inv["status"] == "1"
        assert inv["status_label"] == "validée non payée"
        assert inv["days_late"] > 365  # facture de 2023, fortement en retard

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_list_invoices_paid_label(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"id": "1", "ref": "FA1", "socid": "1", "status": "1", "paye": "1",
             "total_ttc": "100", "total_ht": "100", "date": "1686096000"}
        ]
        inv = json.loads(await dolibarr_list_invoices(status="all"))["invoices"][0]
        assert inv["status_label"] == "payée"

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_list_supplier_invoices_computes_label(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"id": "5", "ref": "SI1", "ref_supplier": "X", "socid": "19",
             "status": "1", "paye": "0", "total_ttc": "6115.8", "date": "1740528000"}
        ]
        inv = json.loads(await dolibarr_list_supplier_invoices())["invoices"][0]
        assert inv["status_label"] == "validée non payée"

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_list_proposals_computes_label(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"id": "124", "ref": "PR2602-0111", "socid": "120", "status": "1",
             "total_ht": "39775", "total_ttc": "47730", "datep": "1738800000",
             "fin_validite": "1742601600"}
        ]
        p = json.loads(await dolibarr_list_proposals(status="1"))["proposals"][0]
        assert p["status"] == "1"
        assert p["status_label"] == "validée"


class TestDocLineFormatting:
    """Tests des helpers d'enrichissement des lignes de documents."""

    def test_strip_html_entities_and_nbsp(self) -> None:
        raw = "Audit&nbsp;c&oelig;ur&nbsp; &nbsp;20&nbsp;d&eacute;p&ocirc;ts"
        assert _strip_html(raw) == "Audit cœur 20 dépôts"

    def test_strip_html_br_to_newline(self) -> None:
        assert _strip_html("Ligne 1<br>Ligne 2<br/>Ligne 3") == "Ligne 1\nLigne 2\nLigne 3"

    def test_strip_html_tags_removed(self) -> None:
        assert _strip_html("<p>Texte <b>gras</b></p>") == "Texte gras"

    def test_strip_html_empty(self) -> None:
        assert _strip_html(None) == ""
        assert _strip_html("") == ""

    def test_num_string_to_int(self) -> None:
        assert _num("1500.00000000") == 1500
        assert isinstance(_num("1500.00000000"), int)

    def test_num_string_to_float(self) -> None:
        assert _num("20.000") == 20
        assert _num("19.6") == 19.6

    def test_num_empty_is_none(self) -> None:
        assert _num("") is None
        assert _num(None) is None

    def test_num_non_numeric_passthrough(self) -> None:
        assert _num("PROV") == "PROV"

    def test_line_type_label(self) -> None:
        assert _line_type_label("0") == "produit"
        assert _line_type_label("1") == "service"
        assert _line_type_label(1) == "service"

    def test_format_doc_line_service_full(self) -> None:
        line = {
            "id": "349", "rang": "1", "product_type": "1",
            "desc": "Audit&nbsp;Lot&nbsp;1", "qty": "7",
            "subprice": "1500.00000000", "remise_percent": "0",
            "tva_tx": "20.000", "total_ht": "10500.00000000",
            "total_tva": "2100.00000000", "total_ttc": "12600.00000000",
            "fk_product": "0",
        }
        out = _format_doc_line(line)
        assert out["line_id"] == 349
        assert out["type"] == "service"
        assert out["label"] == "Audit Lot 1"
        assert out["qty"] == 7
        assert out["subprice"] == 1500
        assert out["total_ttc"] == 12600
        # fk_product "0" → produit non lié, champ omis
        assert "product_id" not in out

    def test_format_doc_line_product_linked(self) -> None:
        line = {
            "id": "672", "rang": "1", "product_type": "1",
            "fk_product": "8", "product_ref": "SUPPORT-20",
            "product_label": "Carnet de 20 tickets", "desc": "Support",
            "qty": "1", "subprice": "1000", "tva_tx": "20", "total_ht": "1000",
        }
        out = _format_doc_line(line)
        assert out["product_id"] == 8
        assert out["product_ref"] == "SUPPORT-20"
        assert out["product_label"] == "Carnet de 20 tickets"

    def test_format_doc_line_drops_empty_fields(self) -> None:
        out = _format_doc_line({"id": "1", "desc": "X", "qty": "2", "total_ht": "0"})
        # total_ht=0 conservé (valeur numérique), product_ref absent
        assert out["total_ht"] == 0
        assert "product_ref" not in out
        assert "date_start" not in out


@pytest.mark.asyncio
class TestGetDocumentLines:
    """Tests des tools de détail renvoyant des lignes enrichies (toutes les lignes)."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_invoice_returns_all_lines(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "id": "430", "ref": "FA2605-0930", "socid": "10", "fk_statut": "1",
            "paye": "0", "total_ht": "1000", "total_ttc": "1200",
            "lines": [{"id": str(i), "desc": f"L{i}", "qty": "1", "total_ht": "100"}
                      for i in range(25)],
        }
        data = json.loads(await dolibarr_get_invoice(430))
        # plus de plafond à 20 : les 25 lignes sont présentes
        assert data["lines_count"] == 25
        assert len(data["lines"]) == 25
        assert data["lines"][0]["line_id"] == 0

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_proposal_cleans_html_in_lines(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "id": "150", "ref": "PR2605-0131", "socid": "120", "fk_statut": "1",
            "total_ht": "10500", "total_ttc": "12600",
            "lines": [{"id": "349", "desc": "Audit&nbsp;c&oelig;ur", "qty": "7",
                       "subprice": "1500", "total_ht": "10500"}],
        }
        data = json.loads(await dolibarr_get_proposal(150))
        assert data["lines"][0]["label"] == "Audit cœur"
        assert data["lines"][0]["subprice"] == 1500

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_supplier_invoice_by_id(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "id": "88", "ref": "SI2602-0084", "ref_supplier": "2026-89",
            "socid": "194", "libelle": "Bilan", "fk_statut": "1", "paye": "0",
            "total_ht": "2100", "total_tva": "0", "total_ttc": "2100",
            "date": "1772064000", "date_echeance": "1772064000", "fk_project": "89",
            "lines": [{"id": "117", "desc": "Bilan 24h", "qty": "1",
                       "subprice": "2100", "total_ht": "2100", "product_type": "1"}],
        }
        data = json.loads(await dolibarr_get_supplier_invoice(88))
        assert data["ref"] == "SI2602-0084"
        assert data["ref_supplier"] == "2026-89"
        assert data["label"] == "Bilan"
        assert data["status_label"] == "validée non payée"
        assert data["lines_count"] == 1
        assert data["lines"][0]["total_ht"] == 2100

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_supplier_invoice_by_ref(self, mock_api: AsyncMock) -> None:
        async def side_effect(endpoint, params=None):
            if endpoint == "supplierinvoices":
                return [{"id": "87"}]
            return {"id": "87", "ref": "SI2603-0085", "socid": "1",
                    "fk_statut": "1", "paye": "0", "lines": []}
        mock_api.side_effect = side_effect
        data = json.loads(await dolibarr_get_supplier_invoice(0, "SI2603-0085"))
        assert data["id"] == "87"
        assert data["lines_count"] == 0

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_supplier_invoice_not_found(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = []
        result = await dolibarr_get_supplier_invoice(0, "SI-INEXISTANT")
        assert "Aucune facture fournisseur" in result

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_invoice_note_public_html_cleaned(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "id": "430", "ref": "FA1", "socid": "1", "fk_statut": "1", "paye": "0",
            "note_public": "Commande n&deg;655969&nbsp;<br>\nLimoges&nbsp;", "lines": [],
        }
        data = json.loads(await dolibarr_get_invoice(430))
        assert "<br>" not in data["note_public"]
        assert "&nbsp;" not in data["note_public"]
        assert "Commande n°655969" in data["note_public"]


@pytest.mark.asyncio
class TestGetProposalNotePublic:
    """dolibarr_get_proposal : la note publique est rendue entière, ou dit qu'elle est coupée.

    Constat du 14/09/2026 sur la propale 172 : `note_public` revenait coupée à
    exactement 300 caractères, sans marqueur de coupe ni longueur totale, avant
    comme après un `dolibarr_update_proposal` réussi. Une note écrite pour être
    relue était donc invérifiable, et la coupe se lisait comme une perte en base.
    """

    @staticmethod
    def _proposal(note: object) -> dict:
        return {
            "id": "172", "ref": "PR2609-0172", "socid": "10", "fk_statut": "1",
            "total_ht": "1000", "total_tva": "200", "total_ttc": "1200",
            "note_public": note, "lines": [],
        }

    @staticmethod
    def _long_note(lines: int) -> str:
        # Une phrase par ligne, sans HTML ni espaces doubles : le texte nettoyé
        # par _strip_html est identique au texte fourni, d'où une égalité exacte.
        return "\n".join(
            f"Clause {i + 1} : acompte de trente pour cent à la commande, solde à réception."
            for i in range(lines)
        )

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_a_note_longer_than_300_chars_comes_back_whole(
        self, mock_api: AsyncMock
    ) -> None:
        note = self._long_note(12)
        assert len(note) > 300
        mock_api.return_value = self._proposal(note)

        data = json.loads(await dolibarr_get_proposal(172))

        assert len(data["note_public"]) == len(note), (
            f"note coupée à {len(data['note_public'])} caractères sur {len(note)}"
        )
        assert data["note_public"] == note
        assert data["note_public_length"] == len(note)
        assert data["note_public_truncated"] is False

    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_a_note_written_by_update_proposal_reads_back_identical(
        self, mock_get: AsyncMock, mock_put: AsyncMock
    ) -> None:
        # Le scénario du constat : écrire la note, puis la relire pour vérifier.
        from dolibarr_mcp.server import dolibarr_update_proposal

        note = self._long_note(15)
        stored = self._proposal("")

        async def put(endpoint, data=None):
            stored.update(data or {})
            return 172

        mock_put.side_effect = put
        mock_get.return_value = stored

        await dolibarr_update_proposal(172, note_public=note)
        data = json.loads(await dolibarr_get_proposal(172))

        assert mock_put.call_args.args == ("proposals/172", {"note_public": note})
        assert data["note_public"] == note
        assert data["note_public_length"] == len(note)
        assert data["note_public_truncated"] is False

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_length_is_that_of_the_cleaned_text_not_of_the_html(
        self, mock_api: AsyncMock
    ) -> None:
        # Dolibarr stocke la note en HTML ; la longueur annoncée est celle du
        # texte rendu, pas celle du HTML brut.
        html = "<p>Conditions&nbsp;:</p>" + "".join(
            f"<p>Lot {i + 1}&nbsp;: livraison sous <b>quinze</b> jours ouvr&eacute;s.</p>"
            for i in range(10)
        )
        expected = _strip_html(html)
        assert 300 < len(expected) < len(html)
        mock_api.return_value = self._proposal(html)

        data = json.loads(await dolibarr_get_proposal(172))

        assert data["note_public"] == expected
        assert "<p>" not in data["note_public"]
        assert data["note_public_length"] == len(expected)
        assert data["note_public_truncated"] is False

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_beyond_the_ceiling_the_cut_is_announced(
        self, mock_api: AsyncMock
    ) -> None:
        ceiling = server.DETAIL_NOTE_MAX_CHARS
        note = self._long_note(ceiling // 40)
        assert len(note) > ceiling
        mock_api.return_value = self._proposal(note)

        data = json.loads(await dolibarr_get_proposal(172))

        assert data["note_public_truncated"] is True
        assert data["note_public_length"] == len(note)
        assert len(data["note_public"]) == ceiling
        assert data["note_public"] == note[:ceiling]

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_an_empty_note_is_empty_and_not_truncated(
        self, mock_api: AsyncMock
    ) -> None:
        for raw in ("", None):
            mock_api.return_value = self._proposal(raw)

            data = json.loads(await dolibarr_get_proposal(172))

            assert data["note_public"] == ""
            assert data["note_public_length"] == 0
            assert data["note_public_truncated"] is False


@pytest.mark.asyncio
class TestDetailNotesRenderedWhole:
    """Les autres lectures de détail rendent leurs notes entières, ou disent qu'elles coupent.

    Même défaut que sur dolibarr_get_proposal : `[:300]` muet sur get_invoice,
    get_supplier_invoice et get_thirdparty, `[:500]` muet sur la description et
    la note publique de get_project.
    """

    @staticmethod
    def _note(lines: int) -> str:
        return "\n".join(
            f"Clause {i + 1} : acompte de trente pour cent à la commande, solde à réception."
            for i in range(lines)
        )

    @staticmethod
    def _assert_whole(data: dict, field: str, expected: str) -> None:
        assert len(data[field]) == len(expected), (
            f"{field} coupé à {len(data[field])} caractères sur {len(expected)}"
        )
        assert data[field] == expected
        assert data[f"{field}_length"] == len(expected)
        assert data[f"{field}_truncated"] is False

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_invoice_note_public_comes_back_whole(self, mock_api: AsyncMock) -> None:
        note = self._note(12)
        mock_api.return_value = {
            "id": "430", "ref": "FA2609-0430", "socid": "1", "fk_statut": "1", "paye": "0",
            "note_public": note, "lines": [],
        }
        data = json.loads(await dolibarr_get_invoice(430))
        self._assert_whole(data, "note_public", note)

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_supplier_invoice_note_public_comes_back_whole(
        self, mock_api: AsyncMock
    ) -> None:
        note = self._note(12)
        mock_api.return_value = {
            "id": "88", "ref": "SI2609-0088", "ref_supplier": "2026-89", "socid": "194",
            "fk_statut": "1", "paye": "0", "note_public": note, "lines": [],
        }
        data = json.loads(await dolibarr_get_supplier_invoice(88))
        self._assert_whole(data, "note_public", note)

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_thirdparty_note_public_comes_back_whole(self, mock_api: AsyncMock) -> None:
        note = self._note(12)
        mock_api.return_value = {"id": "120", "name": "Tiers", "note_public": note}
        data = json.loads(await dolibarr_get_thirdparty(120))
        self._assert_whole(data, "note_public", note)

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_project_description_and_note_come_back_whole(
        self, mock_api: AsyncMock
    ) -> None:
        description = self._note(9)
        note = self._note(9)
        assert len(description) > 500 and len(note) > 500
        mock_api.return_value = {
            "id": "258", "ref": "PJ2609-0258", "title": "Projet", "fk_statut": "1",
            "description": description, "note_public": note,
        }
        data = json.loads(await dolibarr_get_project(258))
        self._assert_whole(data, "description", description)
        self._assert_whole(data, "note_public", note)

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_thirdparty_html_note_length_is_the_cleaned_one(
        self, mock_api: AsyncMock
    ) -> None:
        html = "<p>Conditions&nbsp;:</p>" + "".join(
            f"<p>Lot {i + 1}&nbsp;: livraison sous <b>quinze</b> jours ouvr&eacute;s.</p>"
            for i in range(10)
        )
        expected = _strip_html(html)
        assert 300 < len(expected) < len(html)
        mock_api.return_value = {"id": "120", "name": "Tiers", "note_public": html}
        data = json.loads(await dolibarr_get_thirdparty(120))
        self._assert_whole(data, "note_public", expected)

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_get_project_beyond_the_ceiling_the_cut_is_announced(
        self, mock_api: AsyncMock
    ) -> None:
        ceiling = server.DETAIL_NOTE_MAX_CHARS
        long_text = self._note(ceiling // 40)
        assert len(long_text) > ceiling
        mock_api.return_value = {
            "id": "258", "ref": "PJ2609-0258", "title": "Projet",
            "description": long_text, "note_public": long_text,
        }
        data = json.loads(await dolibarr_get_project(258))
        for field in ("description", "note_public"):
            assert data[f"{field}_truncated"] is True
            assert data[f"{field}_length"] == len(long_text)
            assert len(data[field]) == ceiling
            assert data[field] == long_text[:ceiling]

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_empty_notes_are_empty_and_not_truncated(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "id": "430", "ref": "FA1", "socid": "1", "fk_statut": "1", "paye": "0",
            "note_public": None, "lines": [],
        }
        data = json.loads(await dolibarr_get_invoice(430))
        assert data["note_public"] == ""
        assert data["note_public_length"] == 0
        assert data["note_public_truncated"] is False

        mock_api.return_value = {"id": "258", "ref": "PJ1", "title": "Projet"}
        data = json.loads(await dolibarr_get_project(258))
        for field in ("description", "note_public"):
            assert data[field] == ""
            assert data[f"{field}_length"] == 0
            assert data[f"{field}_truncated"] is False


def _http_error(status: int, body: object | None = None) -> httpx.HTTPStatusError:
    """Fabrique l'erreur HTTP que httpx lève sur une reponse 4xx/5xx."""
    request = httpx.Request("POST", "https://dolibarr.test.local/api/index.php/x")
    response = (
        httpx.Response(status, request=request)
        if body is None
        else httpx.Response(status, request=request, json=body)
    )
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def _task(**over: object) -> dict:
    """Tâche telle que Dolibarr la renvoie : durées en SECONDES, et en chaîne."""
    raw = {
        "id": "42",
        "ref": "TK2607-0042",
        "label": "Audit",
        "progress": "50",
        "duration_effective": "117000",   # 32,5 h
        "planned_workload": "144000",     # 40 h
    }
    raw.update(over)  # type: ignore[arg-type]
    return raw


class TestDurationHelpers:
    """Dolibarr renvoie les durées en secondes, souvent sous forme de chaîne."""

    def test_string_seconds_become_hours(self) -> None:
        assert _seconds_to_hours("117000") == 32.5

    def test_rounds_to_two_decimals(self) -> None:
        assert _seconds_to_hours(5000) == 1.39

    def test_missing_stays_none_never_zero(self) -> None:
        # 0 se lirait « aucun temps passé » alors que l'information manque.
        assert _seconds_to_hours(None) is None
        assert _seconds_to_hours("") is None
        assert _seconds_to_hours("n/a") is None

    def test_raw_seconds_are_preserved_as_int(self) -> None:
        assert _seconds_int("117000") == 117000
        assert _seconds_int(None) is None


@pytest.mark.asyncio
class TestLogTimeRouteAndConversion:
    """Le 404 systématique venait de la route ; l'unité, elle, est un piège muet."""

    async def test_posts_on_the_addtimespent_route(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=1.5)

        endpoint = post.call_args[0][0]
        assert endpoint == TIMESPENT_ENDPOINT.format(task_id=42)
        assert endpoint == "tasks/42/addtimespent"
        # La classe Tasks est montée à la racine : un préfixe /projects rend un
        # 404 sec. Ce test épingle la route vérifiée contre le swagger de l'API.
        assert "projects/" not in endpoint
        # tasks/{id}/timespent existe mais est en lecture seule (GET).
        assert endpoint.endswith("/addtimespent")

    async def test_hours_are_converted_to_seconds(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=1.5)

        payload = post.call_args[0][1]
        assert payload["duration"] == 5400  # 1,5 h — l'API n'accepte que des secondes
        assert isinstance(payload["duration"], int)

    async def test_sends_the_field_names_dolibarr_expects(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=2, date="2026-07-31",
                                    note="Audit", user_id=7)

        payload = post.call_args[0][1]
        assert set(payload) == {"date", "duration", "note", "user_id", "progress"}
        # dol_stringtotime attend une date lisible, pas un timestamp Unix.
        assert payload["date"] == "2026-07-31 09:00:00"
        assert payload["user_id"] == 7

    async def test_a_full_timestamp_is_always_sent_never_a_bare_date(self) -> None:
        """element_datehour stocke une heure : une date nue la perdrait."""
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=1, date="2026-07-31")

        sent = post.call_args[0][1]["date"]
        assert sent == "2026-07-31 09:00:00"
        assert len(sent) == 19  # et non "2026-07-31" tout court

    async def test_the_hour_of_day_can_be_chosen(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=1, date="2026-07-31",
                                    time="14:30")

        assert post.call_args[0][1]["date"] == "2026-07-31 14:30:00"

    async def test_progress_is_always_transmitted_like_the_web_form(self) -> None:
        """Le formulaire web transmet progress à chaque saisie ; l'API aussi."""
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=1)

        # -1 est la valeur que l'API amont lit comme « ne pas toucher à
        # l'avancement » : elle n'applique que 0 <= progress <= 100.
        assert post.call_args[0][1]["progress"] == -1

    async def test_progress_can_be_set_explicitly(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=1, progress=60)

        assert post.call_args[0][1]["progress"] == 60

    async def test_an_out_of_range_progress_is_refused_before_the_call(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            out = await dolibarr_log_time(task_id=42, duration=1, progress=140)
        assert "progress" in out
        post.assert_not_called()

    async def test_user_id_is_omitted_rather_than_sent_null(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            await dolibarr_log_time(task_id=42, duration=1)

        assert "user_id" not in post.call_args[0][1]

    async def test_success_reports_hours_and_raw_seconds(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = {}
            get.return_value = _task()
            data = json.loads(await dolibarr_log_time(task_id=42, duration=1.5))

        assert data["success"] is True
        assert data["logged_hours"] == 1.5
        assert data["logged_seconds"] == 5400
        # Le total revient en chaîne de secondes : diviser sans convertir levait
        # un TypeError sur le chemin nominal.
        assert data["total_spent_hours"] == 32.5
        assert data["total_spent_seconds"] == 117000
        assert data["planned_hours"] == 40.0

    async def test_rejects_a_non_positive_duration(self) -> None:
        assert "supérieure à 0" in await dolibarr_log_time(task_id=42, duration=0)

    async def test_requires_a_task_id(self) -> None:
        assert "task_id" in await dolibarr_log_time(duration=1)


def _contact(user_id: int, code: str = "TASKCONTRIBUTOR", **over: object) -> dict:
    """Intervenant tel que tasks/{id}/contacts le renvoie (tout en chaînes)."""
    raw = {
        "parentId": "42",
        "source": "internal",
        "socid": "-1",
        "id": str(user_id),
        "nom": "Martin",
        "lastname": "Martin",
        "firstname": "Camille",
        "email": "camille.martin@example.org",
        "login": "cmartin",
        "statuscontact": "1",
        "rowid": "900",
        "code": code,
        "libelle": "Intervenant" if code == "TASKCONTRIBUTOR" else "Responsable",
        "status": 4,
        # Le rowid du type de contact varie d'une instance à l'autre : il est
        # exposé mais ne sert jamais de référence.
        "fk_c_type_contact": "181",
    }
    raw.update(over)  # type: ignore[arg-type]
    return raw


def _entry(entry_id: int, **over: object) -> dict:
    """Écriture de temps telle que tasks/{id}/timespent la renvoie."""
    raw = {
        "fk_project": "9",
        "project_ref": "PJ2602-0009",
        "project_label": "Refonte",
        "fk_task": "42",
        "task_ref": "TK2607-0042",
        "task_label": "Audit",
        "timespent_line_id": str(entry_id),
        "timespent_line_date": 1770854400,
        "timespent_line_datehour": 1770854400,   # 2026-02-12 00:00:00 GMT
        "timespent_line_duration": "5400",       # 1,5 h
        "timespent_line_fk_user": "7",
        "timespent_line_fk_product": None,
        "timespent_line_note": "Revue de code",
    }
    raw.update(over)  # type: ignore[arg-type]
    return raw


def _route_get(**by_suffix: object):
    """Mock d'api_get qui répond selon la route, et non une valeur unique.

    Le diagnostic interroge maintenant plusieurs routes (tâche, utilisateur,
    intervenants) : un mock à réponse unique les confondrait toutes.
    """
    async def _get(endpoint: str, *a: object, **k: object) -> object:
        for suffix, reply in by_suffix.items():
            if endpoint.endswith(suffix.replace("__", "/")):
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise _http_error(404)
    return _get


@pytest.mark.asyncio
class TestLogTimeErrorDiagnosis:
    """Un 404 nu est indébogable, et un diagnostic inventé est pire."""

    async def _fail_with(self, exc: Exception, user_id: int = 0,
                         **routes: object) -> dict:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_route_get(**routes)):
            post.side_effect = exc
            return json.loads(
                await dolibarr_log_time(task_id=42, duration=1, user_id=user_id)
            )

    async def test_missing_task_is_named_as_such(self) -> None:
        data = await self._fail_with(
            _http_error(404, {"error": {"code": 404, "message": "Task not found"}}),
            tasks__42=_http_error(404),
        )
        assert data["cause"] == "tache_inexistante"
        assert "dolibarr_list_tasks" in data["hint"]

    async def test_existing_task_points_at_the_route(self) -> None:
        data = await self._fail_with(
            _http_error(404, {"error": {"code": 404, "message": "no route"}}),
            tasks__42=_task(),
        )
        assert data["cause"] == "route_ou_methode_invalide"
        assert "addtimespent" in data["hint"]

    async def test_permission_refusal_is_not_confused_with_a_missing_task(self) -> None:
        data = await self._fail_with(
            _http_error(403, {"error": {"code": 403, "message": "Access not allowed"}})
        )
        assert data["cause"] == "droits_insuffisants"
        assert data["dolibarr_error"] == "Access not allowed"
        # Le refus porte sur la clé API, pas sur une affectation.
        assert "clé API" in data["hint"] or "CLÉ API" in data["hint"]

    async def test_an_empty_bodied_500_names_the_upstream_dolibarr_bug(self) -> None:
        """Le vrai symptôme : HTTP 500, zéro octet de corps = fatale PHP amont."""
        data = await self._fail_with(_http_error(500), tasks__42=_task())
        assert data["cause"] == "bug_dolibarr_addtimespent"
        assert data["dolibarr_error"] is None
        assert data["dolibarr_error_body_empty"] is True
        assert "Validator.php" in data["hint"]
        assert "interface web" in data["hint"]

    async def test_it_no_longer_blames_an_unassigned_user(self) -> None:
        """La route n'exige aucune affectation : l'accuser était un faux
        diagnostic, et il envoyait corriger une configuration déjà correcte."""
        data = await self._fail_with(_http_error(500), tasks__42=_task())
        assert "non_affecte" not in data["cause"]
        assert "pas affecté" not in data["hint"]
        assert "non affecté" not in data["hint"]

    async def test_assignment_is_only_reported_when_actually_checked(self) -> None:
        """`checks` ne dit que ce qui a été vérifié, jamais une supposition."""
        data = await self._fail_with(
            _http_error(500, {"error": {"code": 500, "message": "boom"}}),
            user_id=7,
            tasks__42=_task(),
            users__7={"id": "7"},
            tasks__42__contacts=[_contact(9), _contact(7, "TASKEXECUTIVE")],
        )
        assert data["checks"]["tache_existe"] is True
        assert data["checks"]["utilisateur_existe"] is True
        assert data["checks"]["utilisateur_affecte"] is True
        assert data["checks"]["roles_utilisateur"] == ["TASKEXECUTIVE"]

    async def test_an_unchecked_assignment_stays_null_not_false(self) -> None:
        data = await self._fail_with(
            _http_error(500, {"error": {"code": 500, "message": "boom"}}),
            tasks__42=_task(),
        )
        # Sans user_id, rien n'a pu être vérifié : ne rien affirmer.
        assert data["checks"]["utilisateur_affecte"] is None
        assert data["checks"]["utilisateur_existe"] is None

    async def test_a_contact_id_passed_as_a_user_id_is_named(self) -> None:
        data = await self._fail_with(
            _http_error(500, {"error": {"code": 500, "message": "boom"}}),
            user_id=7,
            tasks__42=_task(),
            users__7=_http_error(404),
        )
        assert data["cause"] == "utilisateur_inconnu"
        assert "llx_user" in data["hint"]

    async def test_each_cause_produces_a_distinct_message(self) -> None:
        """Le point du correctif : chaque échec doit se distinguer des autres."""
        cases = [
            await self._fail_with(_http_error(404), tasks__42=_http_error(404)),
            await self._fail_with(
                _http_error(404, {"error": {"message": "x"}}), tasks__42=_task()),
            await self._fail_with(_http_error(403)),
            await self._fail_with(_http_error(500), tasks__42=_task()),
            await self._fail_with(
                _http_error(500, {"error": {"message": "SQL error"}}),
                tasks__42=_task()),
            await self._fail_with(
                _http_error(400, {"error": {"message": "date is required"}}),
                tasks__42=_task()),
        ]
        assert all(c["success"] is False for c in cases)
        assert len({c["cause"] for c in cases}) == len(cases)
        assert len({c["hint"] for c in cases}) == len(cases)
        # La route en cause est toujours remontée, pour rendre l'échec debuggable.
        assert all(c["endpoint"] == "tasks/42/addtimespent" for c in cases)

    async def test_dolibarr_error_body_is_surfaced(self) -> None:
        data = await self._fail_with(
            _http_error(500, {"error": {"code": 500, "message": "Error when adding time"}}),
            tasks__42=_task(),
        )
        assert data["dolibarr_error"] == "Error when adding time"
        assert data["dolibarr_error_body_empty"] is False
        assert data["cause"] == "erreur_interne_dolibarr"

    async def test_a_non_addtimespent_route_keeps_a_neutral_5xx_message(self) -> None:
        """Le bug amont est nommé pour addtimespent, pas plaqué sur tout 500."""
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_route_get(tasks__42=_task())):
            from dolibarr_mcp.server import _log_time_error
            data = json.loads(await _log_time_error(
                _http_error(500), 42, "tasks/42/timespent/9"))
        assert data["cause"] == "erreur_interne_sans_corps"
        assert "log PHP" in data["hint"]


@pytest.mark.asyncio
class TestWriteThatChangedNothingIsNotASuccess:
    """Restler rend un 304 « nothing done » : le lire comme un succès ferait
    rapporter une imputation qui n'a rien écrit."""

    async def test_a_304_is_raised_instead_of_being_parsed_as_a_payload(self) -> None:
        import httpx

        from dolibarr_mcp.server import _api_request

        resp = httpx.Response(
            304, json={"error": {"code": 304, "message": "Error nothing done"}},
            request=httpx.Request("POST", "http://test"),
        )

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, *a: object, **k: object): return resp

        with patch("httpx.AsyncClient", lambda **k: _Client()):
            with pytest.raises(httpx.HTTPStatusError):
                await _api_request("POST", "tasks/42/addtimespent")

    async def test_log_time_reports_the_304_as_a_failure(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.side_effect = _http_error(
                304, {"error": {"code": 304, "message": "Error nothing done"}})
            data = json.loads(await dolibarr_log_time(task_id=42, duration=1))
        assert data["success"] is False
        assert data["cause"] == "aucun_changement"


@pytest.mark.asyncio
@pytest.mark.asyncio
class TestTaskContacts:
    """Lire et modifier les intervenants d'une tâche, sans id de rôle en dur."""

    async def test_both_forms_of_the_role_are_returned(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_contact(7), _contact(5, "TASKEXECUTIVE")]
            data = json.loads(await dolibarr_list_task_contacts(task_id=42))

        assert data["count"] == 2
        first = data["contacts"][0]
        assert first["user_id"] == 7
        assert first["role_code"] == "TASKCONTRIBUTOR"   # stable entre instances
        assert first["role_label"] == "Intervenant"      # traduit, personnalisable
        assert first["name"] == "Camille Martin"
        assert get.call_args[0][0] == TASK_CONTACTS_ENDPOINT.format(task_id=42)

    async def test_an_external_contact_is_not_exposed_as_a_user(self) -> None:
        """`id` est un user pour internal, un contact tiers pour external."""
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_contact(31, source="external")]
            data = json.loads(await dolibarr_list_task_contacts(task_id=42))

        contact = data["contacts"][0]
        assert contact["user_id"] is None
        assert contact["contact_id"] == 31
        assert contact["source"] == "external"

    async def test_assign_sends_the_role_code_never_a_numeric_id(self) -> None:
        async def _get(endpoint: str, *a: object, **k: object) -> object:
            if endpoint.startswith("setup/dictionary/contact_types"):
                return [
                    {"rowid": "181", "code": "TASKCONTRIBUTOR",
                     "type": "project_task", "source": "internal"},
                    {"rowid": "180", "code": "TASKEXECUTIVE",
                     "type": "project_task", "source": "internal"},
                ]
            # Tâche sans intervenant : le cas qui déclenche vraiment le POST.
            return [_contact(7)] if post.await_count else []

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_get):
            post.return_value = {}
            await dolibarr_assign_task_user(task_id=42, user_id=7,
                                            role="TASKCONTRIBUTOR")

        payload = post.call_args[0][1]
        assert payload["type_contact"] == "TASKCONTRIBUTOR"
        assert payload["source"] == "internal"
        assert payload["fk_socpeople"] == 7
        # Le rowid local (181 ici) ne doit apparaître nulle part dans l'envoi.
        assert "181" not in json.dumps(payload)
        assert 181 not in payload.values()

    async def test_the_role_is_validated_against_the_instance_dictionary(self) -> None:
        async def _get(endpoint: str, *a: object, **k: object) -> object:
            if endpoint.startswith("setup/dictionary/contact_types"):
                return [{"rowid": "181", "code": "TASKCONTRIBUTOR",
                         "type": "project_task", "source": "internal"}]
            return []

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_get):
            data = json.loads(await dolibarr_assign_task_user(
                task_id=42, user_id=7, role="INTERVENANT"))

        # Un code inconnu fait répondre Dolibarr par un 500 au message vide :
        # on l'arrête avant l'appel.
        assert data["cause"] == "role_inconnu"
        assert data["roles_valides"] == ["TASKCONTRIBUTOR"]
        post.assert_not_called()

    async def test_assigning_twice_is_reported_not_duplicated(self) -> None:
        async def _get(endpoint: str, *a: object, **k: object) -> object:
            if endpoint.startswith("setup/dictionary/contact_types"):
                return [{"rowid": "181", "code": "TASKCONTRIBUTOR",
                         "type": "project_task", "source": "internal"}]
            return [_contact(7)]

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_get):
            data = json.loads(await dolibarr_assign_task_user(task_id=42, user_id=7))

        assert data["already_assigned"] is True
        post.assert_not_called()

    async def test_assign_returns_the_state_after_the_operation(self) -> None:
        async def _get(endpoint: str, *a: object, **k: object) -> object:
            if endpoint.startswith("setup/dictionary/contact_types"):
                return [{"rowid": "181", "code": "TASKCONTRIBUTOR",
                         "type": "project_task", "source": "internal"}]
            return [_contact(7)] if post.await_count else []

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_get):
            post.return_value = {}
            data = json.loads(await dolibarr_assign_task_user(task_id=42, user_id=7))

        # Pas un simple booléen : l'état constaté après coup.
        assert data["success"] is True
        assert [c["user_id"] for c in data["contacts"]] == [7]

    async def test_unassign_resolves_a_single_role_on_its_own(self) -> None:
        with patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as dele, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_contact(7, "TASKEXECUTIVE")]
            data = json.loads(await dolibarr_unassign_task_user(task_id=42, user_id=7))

        assert dele.call_args[0][0] == "tasks/42/contacts/7/TASKEXECUTIVE"
        assert data["role"] == "TASKEXECUTIVE"

    async def test_unassign_refuses_to_guess_between_two_roles(self) -> None:
        with patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as dele, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_contact(7), _contact(7, "TASKEXECUTIVE")]
            data = json.loads(await dolibarr_unassign_task_user(task_id=42, user_id=7))

        assert data["cause"] == "role_ambigu"
        dele.assert_not_called()

    async def test_unassign_does_not_delete_when_the_user_is_absent(self) -> None:
        with patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as dele, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_contact(5)]
            data = json.loads(await dolibarr_unassign_task_user(task_id=42, user_id=7))

        assert data["cause"] == "utilisateur_non_affecte"
        dele.assert_not_called()


@pytest.mark.asyncio
class TestCreateTaskMandatoryFields:
    """POST tasks valide ref/label/fk_project avant tout : en omettre un rend
    un 400 sec. Le tool n'envoyait ni `ref`, ni le bon nom de champ projet."""

    async def _create(self, **kw: object) -> dict:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = 949
            get.return_value = _task(id="949", ref="TK2608-0258")
            raw = await dolibarr_create_task(project_id=300, label="Audit", **kw)
        return {"payload": post.call_args[0][1], "out": json.loads(raw)}

    async def test_the_three_mandatory_fields_are_always_sent(self) -> None:
        payload = (await self._create())["payload"]
        # Trio exigé par _validate(), inchangé de Dolibarr 16 à 23.
        assert {"ref", "label", "fk_project"} <= set(payload)
        assert payload["label"] == "Audit"
        assert payload["fk_project"] == 300

    async def test_the_project_field_is_not_the_sql_column_name(self) -> None:
        """`fk_projet` est le nom de la COLONNE ; l'API attend `fk_project`."""
        payload = (await self._create())["payload"]
        assert "fk_projet" not in payload

    async def test_ref_defaults_to_the_auto_sentinel(self) -> None:
        """L'API sait numéroter, mais seulement si on le lui demande : le bloc
        d'auto-génération ne part que sur la valeur "auto"."""
        assert (await self._create())["payload"]["ref"] == "auto"

    async def test_an_explicit_ref_is_honoured(self) -> None:
        payload = (await self._create(ref="TK-MIGRATION-001"))["payload"]
        assert payload["ref"] == "TK-MIGRATION-001"

    async def test_a_blank_ref_falls_back_to_auto_rather_than_a_400(self) -> None:
        assert (await self._create(ref="   "))["payload"]["ref"] == "auto"

    async def test_the_generated_ref_is_read_back_and_reported(self) -> None:
        """Le numéroteur choisit la référence : l'appelant ne peut la connaître
        qu'en relisant la tâche."""
        out = (await self._create())["out"]
        assert out["success"] is True
        assert out["task_id"] == 949
        assert out["ref"] == "TK2608-0258"

    async def test_creation_still_succeeds_if_the_read_back_fails(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            post.return_value = 949
            get.side_effect = _http_error(404)
            out = json.loads(await dolibarr_create_task(project_id=300, label="Audit"))

        # La tâche existe : ne pas la rapporter en échec pour une relecture ratée.
        assert out["success"] is True
        assert out["task_id"] == 949
        assert out["ref"] is None

    async def test_the_optional_fields_keep_their_shape(self) -> None:
        payload = (await self._create(planned_hours=4.5, progress=140,
                                      description="x"))["payload"]
        assert payload["planned_workload"] == 16200   # heures converties en secondes
        assert payload["progress"] == 100             # borné, pas transmis tel quel
        assert payload["description"] == "x"


@pytest.mark.asyncio
class TestListTimeEntries:
    """Reconstituer une ventilation sans rouvrir l'interface, écriture par écriture."""

    async def test_durations_come_back_in_both_units(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(2264), _entry(2265, timespent_line_duration="3600")]
            data = json.loads(await dolibarr_list_time_entries(task_id=42))

        assert get.call_args[0][0] == TIMESPENT_LIST_ENDPOINT.format(task_id=42)
        first = data["entries"][0]
        assert first["hours"] == 1.5
        assert first["seconds"] == 5400
        assert data["total_hours"] == 2.5
        assert data["total_seconds"] == 9000

    async def test_the_entry_id_needed_to_correct_a_line_is_exposed(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(2264)]
            data = json.loads(await dolibarr_list_time_entries(task_id=42))

        entry = data["entries"][0]
        assert entry["entry_id"] == 2264
        assert entry["user_id"] == 7
        assert entry["note"] == "Revue de code"
        assert entry["date"] == "2026-02-12"
        assert entry["datehour"] == "2026-02-12 00:00:00"

    async def test_a_breakdown_by_user_and_by_task_is_computed(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [
                _entry(1), _entry(2, timespent_line_fk_user="9",
                                  timespent_line_duration="3600"),
            ]
            data = json.loads(await dolibarr_list_time_entries(task_id=42))

        assert data["by_user"]["7"]["hours"] == 1.5
        assert data["by_user"]["9"]["hours"] == 1.0
        assert data["by_task"]["TK2607-0042"]["seconds"] == 9000

    async def test_filters_narrow_the_set(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [
                _entry(1),
                _entry(2, timespent_line_fk_user="9"),
                _entry(3, timespent_line_datehour=1767225600),  # 2026-01-01
            ]
            data = json.loads(await dolibarr_list_time_entries(task_id=42, user_id=7))

        assert data["matched"] == 2
        assert {e["entry_id"] for e in data["entries"]} == {1, 3}

        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(1), _entry(3, timespent_line_datehour=1767225600)]
            data = json.loads(await dolibarr_list_time_entries(
                task_id=42, date_start="2026-02-01"))

        assert [e["entry_id"] for e in data["entries"]] == [1]

    async def test_a_project_aggregates_every_task(self) -> None:
        async def _get(endpoint: str, *a: object, **k: object) -> object:
            if endpoint.endswith("/tasks"):
                return [{"id": "42"}, {"id": "43"}]
            return [_entry(1)] if endpoint.startswith("tasks/42") else [_entry(2)]

        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_get):
            data = json.loads(await dolibarr_list_time_entries(project_id=9))

        assert data["tasks_scanned"] == 2
        assert data["matched"] == 2
        assert data["total_seconds"] == 10800

    async def test_truncation_is_declared_and_totals_stay_complete(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(i) for i in range(1, 6)]
            data = json.loads(await dolibarr_list_time_entries(task_id=42, limit=2))

        assert data["returned"] == 2
        assert data["matched"] == 5
        assert data["truncated"] is True
        assert data["total_seconds"] == 27000   # les 5, pas les 2

    async def test_a_scope_is_required(self) -> None:
        assert "project_id" in await dolibarr_list_time_entries()


@pytest.mark.asyncio
class TestUpdateAndDeleteTimeEntry:
    """L'API amont réécrit la ligne entière : ses défauts sont destructeurs."""

    async def test_omitted_fields_are_carried_over_not_wiped(self) -> None:
        """note='' viderait le commentaire et user_id=0 réaffecterait la ligne
        à l'utilisateur 0 : le connecteur relit et repasse les valeurs."""
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(2264)]
            await dolibarr_update_time_entry(task_id=42, entry_id=2264, duration=2)

        payload = put.call_args[0][1]
        assert put.call_args[0][0] == TIMESPENT_ITEM_ENDPOINT.format(
            task_id=42, entry_id=2264)
        assert payload["duration"] == 7200
        assert payload["note"] == "Revue de code"   # conservé
        assert payload["user_id"] == 7              # conservé, pas remis à 0
        assert payload["date"] == "2026-02-12 00:00:00"  # heure conservée

    async def test_correcting_only_the_note_does_not_move_the_entry(self) -> None:
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(2264, timespent_line_datehour=1770886800)]
            await dolibarr_update_time_entry(task_id=42, entry_id=2264,
                                             note="Reventilation")

        payload = put.call_args[0][1]
        assert payload["note"] == "Reventilation"
        assert payload["duration"] == 5400                 # durée inchangée
        assert payload["date"] == "2026-02-12 09:00:00"    # horodatage inchangé

    async def test_an_unknown_entry_is_refused_before_any_write(self) -> None:
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(2264)]
            data = json.loads(await dolibarr_update_time_entry(
                task_id=42, entry_id=999, duration=1))

        assert data["cause"] == "ecriture_introuvable"
        put.assert_not_called()

    async def test_delete_says_what_disappeared(self) -> None:
        async def _get(endpoint: str, *a: object, **k: object) -> object:
            if endpoint.endswith("/timespent"):
                return [_entry(2264)]
            return _task(duration_effective="111600")

        with patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as dele, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_get):
            data = json.loads(await dolibarr_delete_time_entry(task_id=42,
                                                               entry_id=2264))

        assert dele.call_args[0][0] == "tasks/42/timespent/2264"
        assert data["deleted"]["hours"] == 1.5
        assert data["deleted"]["note"] == "Revue de code"
        assert data["total_spent_hours"] == 31.0

    async def test_delete_refuses_an_unknown_entry(self) -> None:
        with patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as dele, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_entry(2264)]
            data = json.loads(await dolibarr_delete_time_entry(task_id=42,
                                                               entry_id=999))

        assert data["cause"] == "ecriture_introuvable"
        dele.assert_not_called()


@pytest.mark.skip(
    reason="test d'intégration : exige une instance Dolibarr réelle et écrit "
           "dedans. Procédure et script complets dans dolibarr_mcp/README.md, "
           "section « Manual integration test ». Attendu : affecter un "
           "utilisateur, imputer 1,5 h, relire l'écriture, vérifier 5400 "
           "secondes, puis supprimer l'écriture."
)
def test_integration_assign_log_reread_5400_seconds() -> None:
    """Aller-retour complet contre une instance, à lancer à la main.

    Sur Dolibarr 23.0.x l'étape d'imputation échoue en amont
    (cause bug_dolibarr_addtimespent) : affectation, lecture et suppression
    passent quand même, ce qui délimite précisément le bug.
    """


class TestGmtTimestampFormatting:
    """L'API rend et relit les horodatages en GMT : un aller-retour ne doit
    pas décaler l'écriture."""

    def test_a_unix_timestamp_becomes_a_gmt_datetime(self) -> None:
        assert _ts_to_datetime(1770854400) == "2026-02-12 00:00:00"
        assert _ts_to_datetime("1770886800") == "2026-02-12 09:00:00"

    def test_a_missing_timestamp_yields_an_empty_string(self) -> None:
        assert _ts_to_datetime(None) == ""
        assert _ts_to_datetime("") == ""
        assert _ts_to_datetime("n/a") == ""


@pytest.mark.asyncio
class TestTaskDurationsExposedInHours:
    """`spent_hours` portait des secondes : un temps 3600 fois trop élevé."""

    async def test_seconds_are_converted_and_the_raw_value_kept(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_task()]
            data = json.loads(await dolibarr_list_tasks(project_id=9))

        task = data["tasks"][0]
        assert task["spent_hours"] == 32.5      # et non 117000
        assert task["spent_seconds"] == 117000  # rien n'est perdu

    async def test_planned_workload_gets_the_same_treatment(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_task()]
            data = json.loads(await dolibarr_list_tasks(project_id=9))

        task = data["tasks"][0]
        assert task["planned_hours"] == 40.0
        assert task["planned_seconds"] == 144000

    async def test_a_task_without_durations_reports_none_not_zero(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_task(duration_effective="", planned_workload=None)]
            data = json.loads(await dolibarr_list_tasks(project_id=9))

        task = data["tasks"][0]
        assert task["spent_hours"] is None
        assert task["planned_hours"] is None

    async def test_close_task_survives_string_seconds(self) -> None:
        """Diviser la chaîne '117000' levait un TypeError sur le chemin nominal."""
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock), \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = _task()
            data = json.loads(await dolibarr_close_task(task_id=42))

        assert data["success"] is True
        assert data["total_spent_hours"] == 32.5
        assert data["total_spent_seconds"] == 117000


@pytest.mark.asyncio
class TestUpdateInvoice:
    """dolibarr_update_invoice — PUT /invoices/{id}.

    Le rattachement d'une facture à un projet a longtemps été fait à la main
    dans `card.php` faute d'outil. Vérifié en production le 11/08/2026 sur
    FA2608-0939 : l'API accepte `fk_project` sur une facture **validée non
    payée** et le persiste (`Facture::update()` écrit `fk_projet` sans garde de
    statut). Ces tests figent le contrat côté client.
    """

    @staticmethod
    def _invoice(**over) -> dict:
        base = {
            "id": "445", "ref": "FA2608-0939", "socid": "12",
            "fk_project": "180", "fk_statut": "1", "status": "1", "paye": "0",
            "total_ht": "6000.00000000", "total_ttc": "7200.00000000",
        }
        base.update(over)
        return base

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_project_link_uses_fk_project(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """`fk_project` et non `fk_projet` : l'API expose la propriété, pas la colonne."""
        mock_get.return_value = self._invoice()

        data = json.loads(await dolibarr_update_invoice(invoice_id=445, project_id=180))

        endpoint, payload = mock_put.call_args[0]
        assert endpoint == "invoices/445"
        assert payload == {"fk_project": "180"}
        assert data["success"] is True
        assert data["fk_project"] == "180"
        assert "project_link_warning" not in data

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_validated_unpaid_invoice_is_updatable(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """Statut 1 / paye 0 : cas réel de FA2608-0939, aucun blocage attendu."""
        mock_get.return_value = self._invoice(fk_statut="1", paye="0")

        data = json.loads(await dolibarr_update_invoice(invoice_id=445, project_id=180))

        assert data["success"] is True
        assert data["status_label"]

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_project_zero_leaves_link_untouched(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """project_id=0 = « ne pas toucher », pas « détacher »."""
        mock_get.return_value = self._invoice()

        await dolibarr_update_invoice(invoice_id=445, note_public="Relance")

        _, payload = mock_put.call_args[0]
        assert "fk_project" not in payload
        assert payload == {"note_public": "Relance"}

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_unpersisted_project_link_is_reported(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """Une facture sur un projet inexistant : le PUT rend 200, le lien ne prend pas."""
        mock_get.return_value = self._invoice(fk_project=None)

        data = json.loads(await dolibarr_update_invoice(invoice_id=445, project_id=999))

        assert "project_link_warning" in data
        assert "999" in data["project_link_warning"]

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_invoice_on_closed_project_reads_back_its_id(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """L'UI masque les projets fermés ; l'API, elle, rend bien fk_project."""
        mock_get.return_value = self._invoice(fk_project="42")

        data = json.loads(await dolibarr_update_invoice(invoice_id=445, project_id=42))

        assert data["fk_project"] == "42"
        assert "project_link_warning" not in data

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_due_date_is_sent_as_timestamp(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """date_lim_reglement est un timestamp Unix : une chaîne s'afficherait en 1970."""
        mock_get.return_value = self._invoice()

        await dolibarr_update_invoice(invoice_id=445, date_due="2026-08-31")

        _, payload = mock_put.call_args[0]
        assert payload["date_lim_reglement"] == 1788134400

    async def test_bad_due_date_is_rejected_before_the_put(self) -> None:
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put:
            out = await dolibarr_update_invoice(invoice_id=445, date_due="31/08/2026")
        assert "YYYY-MM-DD" in out
        put.assert_not_called()

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_ref_customer_maps_to_ref_client(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        mock_get.return_value = self._invoice()

        await dolibarr_update_invoice(invoice_id=445, ref_customer="BC-2026-77")

        _, payload = mock_put.call_args[0]
        assert payload == {"ref_client": "BC-2026-77"}

    async def test_no_field_means_no_call(self) -> None:
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put:
            out = await dolibarr_update_invoice(invoice_id=445)
        assert "Aucun champ" in out
        put.assert_not_called()

    async def test_missing_id_is_rejected(self) -> None:
        assert "invoice_id" in await dolibarr_update_invoice(invoice_id=0, project_id=1)

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_write_is_reported_even_if_refetch_fails(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """Le PUT a abouti : le taire ferait rejouer une écriture déjà passée."""
        mock_get.side_effect = RuntimeError("boom")

        data = json.loads(await dolibarr_update_invoice(invoice_id=445, project_id=180))

        assert data["success"] is True
        assert "refetch_warning" in data

    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_http_error_is_formatted(self, mock_put: AsyncMock) -> None:
        request = httpx.Request("PUT", "https://dolibarr.test.local/x")
        mock_put.side_effect = httpx.HTTPStatusError(
            "denied", request=request,
            response=httpx.Response(403, request=request),
        )

        out = await dolibarr_update_invoice(invoice_id=445, project_id=180)

        assert "403" in out
@pytest.mark.asyncio
class TestUpdateProposal:
    """dolibarr_update_proposal — rattachement projet (fk_project), conversions
    de dates, read-after-write et warning de lien non persisté."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_maps_project_id_to_fk_project_and_confirms(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """project_id=258 → PUT {fk_project:"258"} ; read-after-write confirme."""
        from dolibarr_mcp.server import dolibarr_update_proposal

        mock_put.return_value = {"success": "updated"}
        mock_get.return_value = {"ref": "PR2606-0134", "status": 1, "fk_project": "258"}

        data = json.loads(await dolibarr_update_proposal(157, project_id=258))
        assert data["success"] is True
        assert data["fk_project"] == "258"
        assert data["updated_fields"] == ["fk_project"]
        assert "project_link_warning" not in data
        # Le champ envoyé est bien fk_project (PAS fk_projet)
        payload = mock_put.call_args_list[0].args[1]
        assert payload == {"fk_project": "258"}
        assert mock_put.call_args_list[0].args[0] == "proposals/157"

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_project_link_warning_when_not_persisted(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """Si le relire ne montre pas le projet demandé → project_link_warning."""
        from dolibarr_mcp.server import dolibarr_update_proposal

        mock_put.return_value = {"success": "updated"}
        mock_get.return_value = {"ref": "PR2606-0134", "status": 4, "fk_project": None}

        data = json.loads(await dolibarr_update_proposal(157, project_id=258))
        assert data["success"] is True
        assert "project_link_warning" in data
        assert "258" in data["project_link_warning"]

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_dates_converted_to_timestamp_and_ref_client(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """date/date_validity → timestamps entiers ; ref_customer → ref_client."""
        from dolibarr_mcp.server import _date_str_to_ts, dolibarr_update_proposal

        mock_put.return_value = {"success": "updated"}
        mock_get.return_value = {"ref": "PR2606-0134", "status": 0, "fk_project": None}

        await dolibarr_update_proposal(
            157, date="2026-06-24", date_validity="2026-08-08", ref_customer="BC-42")
        payload = mock_put.call_args_list[0].args[1]
        assert payload["date"] == _date_str_to_ts("2026-06-24")
        assert payload["fin_validite"] == _date_str_to_ts("2026-08-08")
        assert payload["ref_client"] == "BC-42"
        assert isinstance(payload["date"], int)

    async def test_no_fields_rejected(self) -> None:
        from dolibarr_mcp.server import dolibarr_update_proposal
        result = await dolibarr_update_proposal(157)
        assert "Aucun champ" in result

    async def test_missing_id_rejected(self) -> None:
        from dolibarr_mcp.server import dolibarr_update_proposal
        result = await dolibarr_update_proposal(0, project_id=258)
        assert "proposal_id" in result


@pytest.mark.asyncio
class TestDeleteProposal:
    """dolibarr_delete_proposal — succès + diagnostic 500 (calqué delete_project)."""

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_success(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        from dolibarr_mcp.server import dolibarr_delete_proposal

        mock_get.return_value = {"ref": "(PROV159)", "status": 0, "socid": "213", "lines": []}
        mock_delete.return_value = {"success": {"code": 200, "message": "Commercial Proposal deleted"}}

        data = json.loads(await dolibarr_delete_proposal(159))
        assert data["success"] is True
        assert data["proposal_id"] == 159
        assert data["ref"] == "(PROV159)"
        assert mock_delete.call_args_list[0].args[0] == "proposals/159"

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_500_returns_diagnostic(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        import httpx

        from dolibarr_mcp.server import dolibarr_delete_proposal

        mock_get.return_value = {"ref": "PR2606-0134", "status": 1, "socid": "213",
                                 "lines": [{"id": 1}, {"id": 2}]}
        request = httpx.Request("DELETE", "http://test")
        response = httpx.Response(500, request=request, text="Cannot delete, child exists")
        mock_delete.side_effect = httpx.HTTPStatusError("", request=request, response=response)

        # Propale validée (status 1) → force=True requis pour atteindre le DELETE.
        data = json.loads(await dolibarr_delete_proposal(157, force=True))
        assert data["success"] is False
        assert data["http_status"] == 500
        assert data["lines_count"] == 2
        assert "diagnostic" in data
        assert "llx_propaldet" in data["diagnostic"]

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_refused_on_validated_without_force(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        """Propale validée sans force → refus explicite, aucun DELETE émis."""
        from dolibarr_mcp.server import dolibarr_delete_proposal

        mock_get.return_value = {"ref": "PR2606-0134", "fk_statut": "1", "status": 1,
                                 "socid": "213", "lines": [{"id": 1}]}
        data = json.loads(await dolibarr_delete_proposal(157))
        assert data["success"] is False
        assert data["error"] == "proposal_not_draft"
        assert "validée" in data["status_label"]
        mock_delete.assert_not_called()

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_forced_on_validated(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        """force=True → DELETE effectué malgré le statut, réponse marquée forced."""
        from dolibarr_mcp.server import dolibarr_delete_proposal

        mock_get.return_value = {"ref": "PR2606-0134", "fk_statut": "1", "status": 1,
                                 "socid": "213", "lines": []}
        mock_delete.return_value = {"success": {"code": 200,
                                                "message": "Commercial Proposal deleted"}}
        data = json.loads(await dolibarr_delete_proposal(157, force=True))
        assert data["success"] is True
        assert data["forced"] is True
        assert mock_delete.call_args_list[0].args[0] == "proposals/157"

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_refused_when_status_unverifiable(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        """Lecture du statut en échec (hors 404) sans force → refus prudent."""
        from dolibarr_mcp.server import dolibarr_delete_proposal

        mock_get.side_effect = RuntimeError("réseau indisponible")
        data = json.loads(await dolibarr_delete_proposal(159))
        assert data["success"] is False
        assert data["error"] == "status_unverifiable"
        mock_delete.assert_not_called()

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_not_found_no_delete_attempted(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        """Propale inexistante (GET 404) → not_found propre, aucun DELETE."""
        import httpx

        from dolibarr_mcp.server import dolibarr_delete_proposal

        request = httpx.Request("GET", "http://test")
        response = httpx.Response(404, request=request)
        mock_get.side_effect = httpx.HTTPStatusError("", request=request, response=response)
        data = json.loads(await dolibarr_delete_proposal(9999))
        assert data["success"] is False
        assert data["error"] == "not_found"
        mock_delete.assert_not_called()


@pytest.mark.asyncio
class TestProposalLineOps:
    """Garde-fou statut sur update/delete de ligne + set_proposal_draft."""

    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_update_line_refused_on_validated(
        self, mock_get: AsyncMock, mock_put: AsyncMock
    ) -> None:
        """Propale validée → refus explicite, aucun PUT (évite le faux succès)."""
        from dolibarr_mcp.server import dolibarr_update_proposal_line

        mock_get.return_value = {"status": 1, "fk_statut": "1"}
        data = json.loads(await dolibarr_update_proposal_line(157, 356, {"remise_percent": 15}))
        assert data["success"] is False
        assert data["error"] == "proposal_not_draft"
        assert "validée" in data["status_label"]
        mock_put.assert_not_called()

    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_update_line_ok_on_draft_maps_description(
        self, mock_get: AsyncMock, mock_put: AsyncMock
    ) -> None:
        """Propale brouillon → PUT effectué, description normalisée en desc."""
        from dolibarr_mcp.server import dolibarr_update_proposal_line

        mock_get.return_value = {"status": 0, "fk_statut": "0",
                                 "total_ht": "6290", "total_ttc": "7548"}
        mock_put.return_value = {"success": "updated"}

        data = json.loads(await dolibarr_update_proposal_line(
            157, 356, {"remise_percent": 15, "description": "Mission"}))
        assert data["success"] is True
        assert mock_put.call_args_list[0].args[0] == "proposals/157/lines/356"
        payload = mock_put.call_args_list[0].args[1]
        assert payload["remise_percent"] == 15
        assert payload["desc"] == "Mission"  # description → desc
        assert "description" not in payload

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_line_refused_on_validated(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        from dolibarr_mcp.server import dolibarr_delete_proposal_line

        mock_get.return_value = {"status": 2, "fk_statut": "2"}
        data = json.loads(await dolibarr_delete_proposal_line(157, 357))
        assert data["success"] is False
        assert data["error"] == "proposal_not_draft"
        mock_delete.assert_not_called()

    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_delete_line_ok_on_draft(
        self, mock_get: AsyncMock, mock_delete: AsyncMock
    ) -> None:
        from dolibarr_mcp.server import dolibarr_delete_proposal_line

        mock_get.return_value = {"status": 0, "fk_statut": "0",
                                 "total_ht": "6290", "total_ttc": "7548", "lines": [{"id": 1}]}
        mock_delete.return_value = {"success": {"code": 200}}

        data = json.loads(await dolibarr_delete_proposal_line(157, 357))
        assert data["success"] is True
        assert mock_delete.call_args_list[0].args[0] == "proposals/157/lines/357"
        assert data["lines_remaining"] == 1

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_set_proposal_draft_posts_settodraft(
        self, mock_post: AsyncMock, mock_get: AsyncMock
    ) -> None:
        from dolibarr_mcp.server import dolibarr_set_proposal_draft

        mock_post.return_value = {"id": "157"}
        mock_get.return_value = {"ref": "PR2606-0134", "status": 0, "fk_statut": "0"}

        data = json.loads(await dolibarr_set_proposal_draft(157))
        assert data["success"] is True
        assert data["status_label"] == "brouillon"
        assert data["ref"] == "PR2606-0134"
        assert mock_post.call_args_list[0].args[0] == "proposals/157/settodraft"


# ---------------------------------------------------------------------------
# Banque et règlements
# ---------------------------------------------------------------------------


def _bank_account(**over) -> dict:
    """Compte courant ouvert et rapprochable, tel que le rend l'API."""
    base = {
        "id": "3", "ref": "BQ-PRO", "label": "Compte pro", "bank": "Banque X",
        "number": "00012345678", "iban": "FR7612345678901234567890123",
        "bic": "ABCDFRPP", "currency_code": "EUR",
        "type": "1", "status": "0", "rappro": "1", "balance": "12500.00",
    }
    base.update(over)
    return base


def _bank_line(**over) -> dict:
    """Écriture bancaire brute (AccountLine nettoyée par l'API)."""
    base = {
        "id": "901", "ref": "901", "fk_account": "3",
        "dateo": 1787011200, "datev": 1787011200,   # 2026-08-18
        "amount": "1200.00", "label": "VIR CLIENT", "fk_type": "VIR",
        "num_chq": "", "num_releve": "", "rappro": "0", "note": "",
    }
    base.update(over)
    return base


def _payment_types_rows() -> list:
    return [
        {"id": "2", "code": "LIQ", "label": "Espèces"},
        {"id": "4", "code": "VIR", "label": "Virement"},
        {"id": "7", "code": "CHQ", "label": "Chèque"},
    ]


@pytest.fixture(autouse=True)
def _clear_payment_types_cache():
    """Le dictionnaire des modes de règlement est mémorisé : repartir à vide."""
    server._PAYMENT_TYPES_CACHE = None
    yield
    server._PAYMENT_TYPES_CACHE = None


class TestBankEntrySignConvention:
    """dolibarr_create_bank_entry — le sens du montant.

    Point couvert en priorité : dans llx_bank le montant est SIGNÉ, et le
    formulaire `various_payment/card.php` porte ce signe dans un champ `sens`
    dont 1 vaut Crédit et 0 Débit (vérifié en production le 31/08/2026,
    confirmé par PaymentVarious::create qui applique
    `sign = ($sens == '0' ? -1 : 1)`). Une inversion ici ne lève aucune erreur :
    elle produit un solde faux.
    """

    @staticmethod
    def _api(created_amount: str, balance_before: str = "10000", balance_after: str = "11200"):
        """Aiguille api_get selon la route, api_post rend l'id de ligne créé."""
        balances = iter([balance_before, balance_after])

        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return next(balances)
            if endpoint.startswith("setup/dictionary/payment_types"):
                return _payment_types_rows()
            if endpoint.startswith("bankaccounts/lines/"):
                return _bank_line(amount=created_amount)
            if endpoint.endswith("/links"):
                return []
            return {}

        return fake_get

    async def test_credit_is_posted_positive(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._api("1200.00"))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 901
            data = json.loads(await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="Subvention",
                amount=1200, direction="credit", payment_type="VIR",
            ))

        endpoint, payload = post.call_args[0]
        assert endpoint == "bankaccounts/3/lines"
        assert payload["amount"] == 1200.0        # entrée d'argent → montant positif
        assert data["amount_signed"] == 1200.0
        assert data["direction"] == "credit"

    async def test_debit_is_posted_negative(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api("-1200.00", "10000", "8800"))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 901
            data = json.loads(await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="Frais bancaires",
                amount=1200, direction="debit", payment_type="VIR",
            ))

        assert post.call_args[0][1]["amount"] == -1200.0   # sortie → montant négatif
        assert data["amount_signed"] == -1200.0
        assert data["balance_delta"] == -1200.0

    async def test_negative_amount_is_refused_never_double_negated(self) -> None:
        """amount=-1200 + direction='debit' donnerait +1200 après abs() naïf."""
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="X",
                amount=-1200, direction="debit",
            )
        assert "strictement positif" in msg
        post.assert_not_called()

    async def test_unknown_direction_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="X", amount=10, direction="sortie",
            )
        assert "'credit'" in msg and "'debit'" in msg
        post.assert_not_called()

    async def test_balance_is_compared_before_and_after(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api("1200.00", "10000", "11200"))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 901
            data = json.loads(await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="Encaissement",
                amount=1200, direction="credit",
            ))

        assert data["balance_before"] == 10000
        assert data["balance_after"] == 11200
        assert data["balance_delta"] == 1200.0

    async def test_dates_are_sent_as_unix_timestamps(self) -> None:
        """`date` et `datev` sont castés en (int) : une date ISO y vaudrait 1970."""
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._api("10.00"))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 901
            await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="X", amount=10, direction="credit",
            )

        payload = post.call_args[0][1]
        assert isinstance(payload["date"], int)
        assert payload["date"] == payload["datev"] == 1787011200

    async def test_payment_type_is_sent_as_a_code_not_an_id(self) -> None:
        """addline() attend un code ; un id numérique y est déprécié."""
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._api("10.00"))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 901
            await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="X", amount=10,
                direction="credit", payment_type="4",   # id du virement
            )

        assert post.call_args[0][1]["type"] == "VIR"

    async def test_unknown_payment_type_lists_the_valid_codes(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.startswith("setup/dictionary/payment_types"):
                return _payment_types_rows()
            return "10000"

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="X", amount=10,
                direction="credit", payment_type="SEPA",
            )
        assert "SEPA" in msg and "VIR" in msg and "CHQ" in msg
        post.assert_not_called()

    async def test_project_is_attached_as_a_bank_url_link(self) -> None:
        """Une ligne de journal n'a pas de champ projet : le lien est le seul support."""
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._api("10.00"))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 901
            await dolibarr_create_bank_entry(
                account_id=3, date="2026-08-18", label="X", amount=10,
                direction="credit", project_id=42,
            )

        endpoint, payload = post.call_args_list[-1][0]
        assert endpoint == "bankaccounts/3/lines/901/links"
        assert payload["type"] == "project"
        assert payload["url_id"] == 42


class TestListBankAccounts:
    """dolibarr_list_bank_accounts — solde et rapprochabilité."""

    async def test_reconcilable_matches_dolibarr_own_rule(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_bank_account()]
            data = json.loads(await dolibarr_list_bank_accounts())

        account = data["accounts"][0]
        assert account["reconcilable"] is True
        assert account["rappro"] == 1
        assert account["type_label"] == "compte courant"
        assert data["total_balance"] == 12500.0

    async def test_a_cash_account_is_never_reconcilable(self) -> None:
        """Account::load_board exclut TYPE_CASH même quand rappro vaut 1."""
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_bank_account(type="2", label="Caisse")]
            data = json.loads(await dolibarr_list_bank_accounts())

        assert data["accounts"][0]["reconcilable"] is False
        assert data["accounts"][0]["type_label"] == "caisse"

    async def test_rappro_zero_is_not_reconcilable(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_bank_account(rappro="0")]
            data = json.loads(await dolibarr_list_bank_accounts())

        assert data["accounts"][0]["reconcilable"] is False
        assert data["accounts"][0]["rappro"] == 0

    async def test_closed_accounts_are_hidden_by_default(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_bank_account(), _bank_account(id="9", status="1")]

            default = json.loads(await dolibarr_list_bank_accounts())
            everything = json.loads(await dolibarr_list_bank_accounts(include_closed=True))

        assert default["count"] == 1
        assert everything["count"] == 2
        assert everything["accounts"][1]["status_label"] == "clos"


class TestListBankEntries:
    """dolibarr_list_bank_entries — filtres, tri, solde cumulé."""

    @staticmethod
    def _api(window: list, after: list | None = None, balance: str = "12500"):
        calls: list = []

        async def fake_get(endpoint, params=None):
            calls.append((endpoint, params))
            if endpoint.endswith("/balance"):
                return balance
            if endpoint.endswith("/links"):
                return []
            if endpoint.endswith("/lines"):
                criteria = (params or {}).get("sqlfilters", "")
                return after or [] if ":>:" in criteria else window
            return {}

        return fake_get, calls

    async def test_date_criteria_carry_no_table_alias(self) -> None:
        """`GET /bankaccounts/{id}/lines` bâtit son SQL sans alias : `t.` casse tout."""
        fake_get, calls = self._api([_bank_line()])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            await dolibarr_list_bank_entries(
                account_id=3, date_start="2026-08-01", date_end="2026-08-31", with_links=False,
            )

        criteria = [p.get("sqlfilters", "") for e, p in calls if e.endswith("/lines") and p]
        assert criteria, "aucun filtre transmis"
        assert all("t." not in c for c in criteria)
        assert "(dateo:>=:'2026-08-01')" in criteria[0]
        assert "(dateo:<=:'2026-08-31')" in criteria[0]

    async def test_a_default_window_is_applied_and_announced(self) -> None:
        fake_get, calls = self._api([_bank_line()])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_bank_entries(account_id=3, with_links=False))

        assert data["date_start"]           # la fenêtre retenue est rendue…
        assert "date_start_note" in data    # …et signalée, jamais posée en silence
        assert "90 jours" in data["date_start_note"]

    async def test_sort_is_descending_by_default(self) -> None:
        lines = [
            _bank_line(id="1", ref="1", dateo=1785456000),   # 2026-07-31
            _bank_line(id="2", ref="2", dateo=1787011200),   # 2026-08-18
        ]
        fake_get, _ = self._api(lines)
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            desc = json.loads(await dolibarr_list_bank_entries(account_id=3, with_links=False))
            asc = json.loads(
                await dolibarr_list_bank_entries(account_id=3, sort="asc", with_links=False)
            )

        assert [e["id"] for e in desc["entries"]] == [2, 1]
        assert [e["id"] for e in asc["entries"]] == [1, 2]

    async def test_running_balance_walks_back_from_the_account_balance(self) -> None:
        """Solde cumulé = solde Dolibarr, moins les écritures postérieures."""
        lines = [
            _bank_line(id="1", ref="1", dateo=1785456000, amount="500.00"),
            _bank_line(id="2", ref="2", dateo=1787011200, amount="-200.00"),
        ]
        fake_get, _ = self._api(lines, after=[], balance="1000")
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_bank_entries(
                account_id=3, date_start="2026-07-01", date_end="2026-08-31", with_links=False,
            ))

        by_id = {e["id"]: e for e in data["entries"]}
        assert by_id[2]["balance_after"] == 1000.0        # dernière écriture = solde du compte
        assert by_id[1]["balance_after"] == 1200.0        # avant le débit de 200
        assert by_id[2]["debit"] == 200.0 and by_id[2]["credit"] == 0
        assert by_id[1]["credit"] == 500.0 and by_id[1]["debit"] == 0

    async def test_entries_after_the_window_are_deducted_from_the_anchor(self) -> None:
        window = [_bank_line(id="1", ref="1", dateo=1785456000, amount="500.00")]
        after = [_bank_line(id="2", ref="2", dateo=1787011200, amount="300.00")]
        fake_get, _ = self._api(window, after=after, balance="1000")
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_bank_entries(
                account_id=3, date_start="2026-07-01", date_end="2026-07-31", with_links=False,
            ))

        # 1000 (solde) − 300 (écriture postérieure) = 700 après la dernière du lot
        assert data["entries"][0]["balance_after"] == 700.0

    async def test_future_dated_window_omits_the_running_balance(self) -> None:
        """Le solde de référence exclut les écritures postdatées : pas de cumul faux."""
        far = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp())
        fake_get, _ = self._api([_bank_line(dateo=far)])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_bank_entries(
                account_id=3, date_start="2098-01-01", date_end="2099-12-31", with_links=False,
            ))

        assert "balance_after" not in data["entries"][0]
        assert "postdatées" in data["running_balance_note"]

    async def test_offset_and_limit_paginate_the_sorted_window(self) -> None:
        lines = [
            _bank_line(id=str(i), ref=str(i), dateo=1785456000 + i * 86400) for i in range(1, 6)
        ]
        fake_get, _ = self._api(lines)
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_bank_entries(
                account_id=3, limit=2, offset=1, with_links=False,
            ))

        assert data["matched"] == 5
        assert [e["id"] for e in data["entries"]] == [4, 3]
        assert data["offset"] == 1

    async def test_links_resolve_thirdparty_and_source(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return "1000"
            if endpoint.endswith("/links"):
                return [
                    {"type": "company", "url_id": "12", "label": "ACME", "url": "/x"},
                    {"type": "payment", "url_id": "88", "label": "(CustomerInvoicePayment)"},
                ]
            if endpoint.endswith("/lines"):
                return [_bank_line()]
            return {}

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_bank_entries(account_id=3))

        entry = data["entries"][0]
        assert entry["thirdparty"] == {"id": 12, "name": "ACME"}
        assert entry["sources"][0]["type"] == "payment"
        assert entry["sources"][0]["type_label"] == "règlement facture client"

    async def test_with_links_false_makes_no_link_call(self) -> None:
        fake_get, calls = self._api([_bank_line()])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            await dolibarr_list_bank_entries(account_id=3, with_links=False)

        assert not [e for e, _ in calls if e.endswith("/links")]

    async def test_malformed_date_is_refused_before_any_call(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            msg = await dolibarr_list_bank_entries(account_id=3, date_start="31/08/2026")
        assert "YYYY-MM-DD" in msg
        get.assert_not_called()


class TestBankBalance:
    """dolibarr_get_bank_balance — report à une date donnée."""

    async def test_current_balance_uses_the_dedicated_route(self) -> None:
        async def fake_get(endpoint, params=None):
            return "12500" if endpoint.endswith("/balance") else _bank_account()

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)) as get:
            data = json.loads(await dolibarr_get_bank_balance(account_id=3))

        assert data["balance"] == 12500.0
        assert data["entries_applied"] == 0
        assert any(c[0][0].endswith("bankaccounts/3/balance") for c in get.call_args_list)

    async def test_past_date_removes_the_later_entries(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return "12500"
            if endpoint.endswith("/lines"):
                return [_bank_line(amount="1500.00"), _bank_line(id="902", amount="-300.00")]
            return _bank_account()

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_get_bank_balance(account_id=3, date="2026-01-31"))

        assert data["balance"] == 11300.0          # 12500 − (1500 − 300)
        assert data["entries_applied"] == 2

    async def test_future_date_adds_the_postdated_entries(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return "12500"
            if endpoint.endswith("/lines"):
                return [_bank_line(amount="1000.00")]
            return _bank_account()

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_get_bank_balance(account_id=3, date="2099-12-31"))

        assert data["balance"] == 13500.0


class TestSetBankStatement:
    """dolibarr_set_bank_statement — jamais de succès silencieux."""

    async def test_a_non_reconcilable_account_fails_without_writing(self) -> None:
        async def fake_get(endpoint, params=None):
            return "0" if endpoint.endswith("/balance") else _bank_account(rappro="0")

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server._web_post", new_callable=AsyncMock) as web:
            data = json.loads(await dolibarr_set_bank_statement(3, [901], "2026-08"))

        assert data["success"] is False
        assert "n'est pas rapprochable" in data["message"]
        assert "Aucune écriture n'a été touchée" in data["message"]
        web.assert_not_called()

    async def test_an_entry_from_another_account_fails_without_writing(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.startswith("bankaccounts/lines/"):
                return _bank_line(fk_account="7")
            return _bank_account()

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server._web_post", new_callable=AsyncMock) as web:
            data = json.loads(await dolibarr_set_bank_statement(3, [901], "2026-08"))

        assert data["success"] is False
        assert "appartient au compte 7" in data["message"]
        web.assert_not_called()

    async def test_missing_web_credentials_refuse_rather_than_pretend(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.startswith("bankaccounts/lines/"):
                return _bank_line()
            return _bank_account()

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.WEB_LOGIN", ""), \
             patch("dolibarr_mcp.server.WEB_PASSWORD", ""), \
             patch("dolibarr_mcp.server._web_post", new_callable=AsyncMock) as web:
            data = json.loads(await dolibarr_set_bank_statement(3, [901], "2026-08"))

        assert data["success"] is False
        assert "DOLIBARR_WEB_LOGIN" in data["message"]
        web.assert_not_called()

    async def test_reconciled_entries_are_verified_by_rereading_them(self) -> None:
        reads = iter([
            _bank_line(),                                       # avant
            _bank_line(num_releve="2026-08", rappro="1"),       # après
        ])

        async def fake_get(endpoint, params=None):
            if endpoint.startswith("bankaccounts/lines/"):
                return next(reads)
            return _bank_account()

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.WEB_LOGIN", "bot"), \
             patch("dolibarr_mcp.server.WEB_PASSWORD", "s3cr3t"), \
             patch("dolibarr_mcp.server._web_post", new_callable=AsyncMock) as web:
            web.return_value = "<html>ok</html>"
            data = json.loads(await dolibarr_set_bank_statement(3, [901], "2026-08"))

        assert data["success"] is True
        assert data["applied_count"] == 1
        assert data["applied"][0]["num_releve"] == "2026-08"

        path = web.call_args[0][0]
        fields = dict(web.call_args.kwargs["data"])
        assert path == "compta/bank/bankentries_list.php"
        assert fields["confirm_reconcile"] == "1"
        assert fields["num_releve"] == "2026-08"
        assert ("rowid[]", "901") in web.call_args.kwargs["data"]

    async def test_a_form_that_wrote_nothing_is_reported_as_failure(self) -> None:
        """Le formulaire peut répondre 200 sans rien écrire : la relecture tranche."""
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.WEB_LOGIN", "bot"), \
             patch("dolibarr_mcp.server.WEB_PASSWORD", "s3cr3t"), \
             patch("dolibarr_mcp.server._web_post", new_callable=AsyncMock) as web:
            web.return_value = "<html>ok</html>"

            async def fake_get(endpoint, params=None):
                if endpoint.startswith("bankaccounts/lines/"):
                    return _bank_line()          # ni num_releve ni rappro : rien n'a bougé
                return _bank_account()

            get.side_effect = fake_get
            data = json.loads(await dolibarr_set_bank_statement(3, [901], "2026-08"))

        assert data["success"] is False
        assert data["applied_count"] == 0
        assert data["not_applied"][0]["entry_id"] == 901

    async def test_an_empty_statement_number_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            msg = await dolibarr_set_bank_statement(3, [901], "  ")
        assert "num_releve" in msg
        get.assert_not_called()


class TestToggleBankReconciliation:
    """dolibarr_toggle_bank_reconciliation — champ `rappro`, pas `norappro`."""

    @staticmethod
    def _reads(before: str, after: str):
        """Chaque lecture de compte est suivie d'une lecture de solde."""
        accounts = iter([_bank_account(rappro=before), _bank_account(rappro=after)])

        async def fake_get(endpoint, params=None):
            return "3.00" if endpoint.endswith("/balance") else next(accounts)

        return fake_get

    async def test_enabling_writes_rappro_one(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._reads("0", "1"))), \
             patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put:
            data = json.loads(await dolibarr_toggle_bank_reconciliation(3, True))

        endpoint, payload = put.call_args[0]
        assert endpoint == "bankaccounts/3"
        assert payload == {"rappro": 1}          # et non norappro, inversé dans le formulaire
        assert data["success"] is True
        assert data["rappro_before"] == 0 and data["rappro_after"] == 1

    async def test_disabling_writes_rappro_zero(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._reads("1", "0"))), \
             patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put:
            data = json.loads(await dolibarr_toggle_bank_reconciliation(3, False))

        assert put.call_args[0][1] == {"rappro": 0}
        assert data["success"] is True

    async def test_a_put_without_effect_is_reported_as_failure(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._reads("0", "0"))), \
             patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock):
            data = json.loads(await dolibarr_toggle_bank_reconciliation(3, True))

        assert data["success"] is False
        assert "après relecture" in data["message"]


class TestInvoicePayments:
    """dolibarr_list_invoice_payments — le lien facture ↔ écriture bancaire."""

    async def test_bank_line_and_account_are_exposed(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/payments"):
                return [{
                    "ref": "PAY2608-0007", "amount": "7200.00", "type": "VIR",
                    "typeline": "payment", "date": 1787011200, "num": "VIR-77",
                    "fk_bank_line": "901",
                }]
            return _bank_line(num_releve="2026-08", rappro="1")

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_invoice_payments(445))

        payment = data["payments"][0]
        assert payment["bank_line_id"] == 901
        assert payment["bank_account_id"] == 3
        assert payment["bank_num_releve"] == "2026-08"
        assert payment["has_bank_line"] is True
        assert data["total_paid"] == 7200.0

    async def test_credit_notes_are_flagged_as_having_no_bank_line(self) -> None:
        async def fake_get(endpoint, params=None):
            return [{
                "ref": "FA2601-0001", "amount": "150.00", "type": "Avoir",
                "date": 1787011200, "num": "", "fk_bank_line": "0",
            }]

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_list_invoice_payments(445))

        assert data["payments"][0]["has_bank_line"] is False
        assert "bank_line_id" not in data["payments"][0]

    async def test_supplier_flag_switches_the_route(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_invoice_payments(88, supplier=True)

        assert get.call_args_list[0][0][0] == "supplierinvoices/88/payments"


class TestAddInvoicePayment:
    """dolibarr_add_invoice_payment — encaissement client et écriture bancaire."""

    @staticmethod
    def _api(before: list, after: list, balances: list, statut: str = "1"):
        it = iter(balances)

        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return next(it)
            if endpoint.startswith("setup/dictionary/payment_types"):
                return _payment_types_rows()
            if endpoint.endswith("/payments"):
                return after if fake_get.posted else before
            if endpoint.startswith("bankaccounts/lines/"):
                return _bank_line()
            if endpoint.startswith("invoices/"):
                return {"id": "445", "ref": "FA2608-0939", "fk_statut": statut,
                        "paye": "0", "total_ttc": "3000.00", "remaintopay": "3000.00"}
            return {}

        fake_get.posted = False
        return fake_get

    async def test_a_draft_invoice_is_refused_before_any_write(self) -> None:
        """L'API l'accepterait ; l'interface web ne le propose jamais."""
        fake_get = self._api([], [], ["0", "0"], statut="0")
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            data = json.loads(await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=3000,
                payment_type="VIR", account_id=3,
            ))

        assert data["success"] is False
        assert "brouillon" in data["message"]
        post.assert_not_called()

    async def test_the_invoice_reference_is_echoed_back(self) -> None:
        fake_get = self._api([], [], ["0", "3000"])

        async def fake_post(endpoint, data=None):
            fake_get.posted = True
            return 55

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)):
            data = json.loads(await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=3000,
                payment_type="VIR", account_id=3,
            ))

        assert data["invoice_ref"] == "FA2608-0939"

    async def test_the_amount_is_honoured_via_the_distributed_route(self) -> None:
        """`POST /invoices/{id}/payments` ignore tout montant et solde la facture."""
        fake_get = self._api([], [], ["0", "3000"])

        async def fake_post(endpoint, data=None):
            fake_get.posted = True
            return 55

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)) as post:
            await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=3000,
                payment_type="VIR", account_id=3,
            )

        endpoint, payload = post.call_args[0]
        assert endpoint == "invoices/paymentsdistributed"
        assert payload["arrayofamounts"] == {"445": {"amount": "3000.00", "multicurrency_amount": ""}}
        assert payload["paymentid"] == 4          # code VIR résolu en identifiant
        assert payload["accountid"] == 3
        assert payload["closepaidinvoices"] == "yes"

    async def test_the_created_payment_is_reread_from_the_invoice(self) -> None:
        created = {
            "ref": "PAY2608-0009", "amount": "3000.00", "type": "VIR",
            "typeline": "payment", "date": 1787011200, "num": "", "fk_bank_line": "901",
        }
        fake_get = self._api([], [created], ["0", "3000"])

        async def fake_post(endpoint, data=None):
            fake_get.posted = True
            return 55

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)):
            data = json.loads(await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=3000,
                payment_type="VIR", account_id=3,
            ))

        assert data["success"] is True
        assert data["payment_id"] == 55
        assert data["created_payments"][0]["bank_line_id"] == 901
        assert data["bank_entry"]["id"] == 901
        assert data["balance_before"] == 0
        assert data["balance_after"] == 3000
        assert data["balance_delta"] == 3000.0
        assert data["expected_delta"] == 3000.0
        assert "balance_warning" not in data

    async def test_a_balance_that_moved_the_wrong_way_raises_a_warning(self) -> None:
        fake_get = self._api([], [], ["1000", "-2000"])

        async def fake_post(endpoint, data=None):
            fake_get.posted = True
            return 55

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)):
            data = json.loads(await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=3000,
                payment_type="VIR", account_id=3,
            ))

        assert data["balance_delta"] == -3000.0
        assert data["expected_delta"] == 3000.0
        assert "balance_warning" in data

    async def test_a_pre_existing_payment_is_not_reported_as_created(self) -> None:
        existing = {
            "ref": "PAY2607-0001", "amount": "500.00", "type": "VIR",
            "typeline": "payment", "date": 1785456000, "num": "", "fk_bank_line": "800",
        }
        created = {
            "ref": "PAY2608-0009", "amount": "3000.00", "type": "VIR",
            "typeline": "payment", "date": 1787011200, "num": "", "fk_bank_line": "901",
        }
        fake_get = self._api([existing], [existing, created], ["500", "3500"])

        async def fake_post(endpoint, data=None):
            fake_get.posted = True
            return 55

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)):
            data = json.loads(await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=3000,
                payment_type="VIR", account_id=3,
            ))

        assert [p["ref"] for p in data["created_payments"]] == ["PAY2608-0009"]
        assert data["total_paid"] == 3500.0

    async def test_a_zero_amount_is_refused_before_any_write(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=0,
                payment_type="VIR", account_id=3,
            )
        assert "strictement positif" in msg
        post.assert_not_called()

    async def test_a_missing_bank_account_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_add_invoice_payment(
                invoice_id=445, date="2026-08-18", amount=100,
                payment_type="VIR", account_id=0,
            )
        assert "account_id" in msg
        post.assert_not_called()


class TestAddSupplierInvoicePayment:
    """dolibarr_add_supplier_invoice_payment — le compte est DÉBITÉ."""

    @staticmethod
    def _api(after: list, balances: list, statut: str = "1"):
        it = iter(balances)

        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return next(it)
            if endpoint.startswith("setup/dictionary/payment_types"):
                return _payment_types_rows()
            if endpoint.endswith("/payments"):
                return after if fake_get.posted else []
            if endpoint.startswith("bankaccounts/lines/"):
                return _bank_line(amount="-1200.00")
            if endpoint.startswith("supplierinvoices/"):
                return {"id": "88", "ref": "SI2608-0012", "fk_statut": statut,
                        "paye": "0", "total_ttc": "1200.00", "remaintopay": "1200.00"}
            return {}

        fake_get.posted = False
        return fake_get

    async def test_a_draft_supplier_invoice_is_refused(self) -> None:
        fake_get = self._api([], ["5000", "5000"], statut="0")
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            data = json.loads(await dolibarr_add_supplier_invoice_payment(
                supplier_invoice_id=88, date="2026-08-18", amount=1200,
                payment_type="VIR", account_id=3,
            ))

        assert data["success"] is False
        assert "brouillon" in data["message"]
        post.assert_not_called()

    async def test_the_native_route_is_used_with_an_explicit_amount(self) -> None:
        """Côté fournisseur, `{id}/payments` accepte bien un `amount`."""
        fake_get = self._api([], ["5000", "3800"])

        async def fake_post(endpoint, data=None):
            fake_get.posted = True
            return 66

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)) as post:
            data = json.loads(await dolibarr_add_supplier_invoice_payment(
                supplier_invoice_id=88, date="2026-08-18", amount=1200,
                payment_type="VIR", account_id=3,
            ))

        endpoint, payload = post.call_args[0]
        assert endpoint == "supplierinvoices/88/payments"
        assert payload["amount"] == 1200.0
        assert payload["payment_mode_id"] == 4
        assert data["expected_delta"] == -1200.0     # sortie de trésorerie
        assert data["balance_delta"] == -1200.0
        assert "balance_warning" not in data

    async def test_datepaye_is_a_unix_timestamp(self) -> None:
        """PaiementFourn::datepaye reçoit la valeur telle quelle : ISO → 1970."""
        fake_get = self._api([], ["5000", "3800"])

        async def fake_post(endpoint, data=None):
            fake_get.posted = True
            return 66

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)) as post:
            await dolibarr_add_supplier_invoice_payment(
                supplier_invoice_id=88, date="2026-08-18", amount=1200,
                payment_type="VIR", account_id=3,
            )

        assert post.call_args[0][1]["datepaye"] == 1787011200


class TestCreateSupplierInvoice:
    """dolibarr_create_supplier_invoice — le registre fournisseur redevient écrivable."""

    @staticmethod
    async def _reread(endpoint, params=None):
        return {
            "id": "310", "ref": "(PROV310)", "ref_supplier": "F-2026-118", "socid": "42",
            "fk_statut": "0", "paye": "0", "total_ht": "1000.00", "total_ttc": "1200.00",
            "date": 1787011200, "lines": [],
        }

    async def test_the_invoice_is_created_then_reread(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._reread)), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 310
            data = json.loads(await dolibarr_create_supplier_invoice(
                thirdparty_id=42, ref_supplier="F-2026-118",
                date="2026-08-18", due_date="2026-09-18", project_id=7,
            ))

        endpoint, payload = post.call_args_list[0][0]
        assert endpoint == "supplierinvoices"
        assert payload["socid"] == 42
        assert payload["ref_supplier"] == "F-2026-118"
        assert payload["date"] == "2026-08-18"
        assert payload["date_echeance"] == "2026-09-18"   # et non date_lim_reglement
        assert payload["fk_project"] == 7
        assert data["success"] is True
        assert data["ref"] == "(PROV310)"
        assert data["status_label"] == "brouillon"

    async def test_lines_carry_every_field_postline_dereferences(self) -> None:
        """postLine() lit ses champs sans isset() : une clé absente ampute la ligne."""
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._reread)), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 310
            data = json.loads(await dolibarr_create_supplier_invoice(
                thirdparty_id=42, ref_supplier="F-2026-118",
                lines=[{"description": "Hébergement", "qty": 12, "pu_ht": 80, "tva_tx": 20}],
            ))

        endpoint, body = post.call_args_list[1][0]
        assert endpoint == "supplierinvoices/310/lines"
        assert set(server.SUPPLIER_LINE_DEFAULTS) <= set(body)
        assert body["description"] == "Hébergement"
        assert body["qty"] == 12.0
        assert body["pu_ht"] == 80.0
        assert body["tva_tx"] == 20.0
        assert body["price_base_type"] == "HT"
        assert data["lines_added"] == 1

    async def test_subprice_is_accepted_as_an_alias_of_pu_ht(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._reread)), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 310
            await dolibarr_create_supplier_invoice(
                thirdparty_id=42, ref_supplier="F-2026-118",
                lines=[{"description": "Conseil", "subprice": 950}],
            )

        assert post.call_args_list[1][0][1]["pu_ht"] == 950.0

    async def test_a_refused_line_leaves_the_invoice_and_is_reported(self) -> None:
        async def fake_post(endpoint, data=None):
            if endpoint.endswith("/lines"):
                raise RuntimeError("Unable to insert the new line")
            return 310

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._reread)), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)):
            data = json.loads(await dolibarr_create_supplier_invoice(
                thirdparty_id=42, ref_supplier="F-2026-118",
                lines=[{"description": "Conseil", "pu_ht": 100}],
            ))

        assert data["success"] is True
        assert data["lines_added"] == 0
        assert data["lines_requested"] == 1
        assert "line_warnings" in data

    async def test_ref_supplier_is_mandatory(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_create_supplier_invoice(thirdparty_id=42, ref_supplier="")
        assert "ref_supplier" in msg
        post.assert_not_called()


class TestInvoiceListFilters:
    """dolibarr_list_invoices / _supplier_invoices — période, tri, recherche."""

    async def test_sorting_is_descending_on_the_invoice_date(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_invoices(status="all")

        params = get.call_args[0][1]
        assert params["sortfield"] == "t.datef,t.rowid"
        assert params["sortorder"] == "DESC"

    async def test_sort_asc_is_still_reachable(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_invoices(status="all", sort="asc")

        assert get.call_args[0][1]["sortorder"] == "ASC"

    async def test_a_period_narrows_the_query_server_side(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_invoices(
                status="all", date_start="2026-08-01", date_end="2026-08-31",
            )

        criteria = get.call_args[0][1]["sqlfilters"]
        assert "(t.datef:>=:'2026-08-01')" in criteria
        assert "(t.datef:<=:'2026-08-31')" in criteria

    async def test_search_ref_is_a_partial_match(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_invoices(status="all", search_ref="2608-0939")

        assert "(t.ref:like:'%2608-0939%')" in get.call_args[0][1]["sqlfilters"]

    async def test_status_period_and_ref_combine(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_invoices(
                status="unpaid", date_start="2026-01-01", search_ref="FA26",
            )

        criteria = get.call_args[0][1]["sqlfilters"]
        assert criteria.count(" and ") == 2
        assert "(t.fk_statut:=:1)" in criteria

    async def test_quotes_are_stripped_from_a_like_never_doubled(self) -> None:
        """Le callback LIKE de Dolibarr rejette toute apostrophe dans la valeur."""
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_invoices(status="all", search_ref="O'Brien (2026)")

        criteria = get.call_args[0][1]["sqlfilters"]
        assert criteria == "(t.ref:like:'%OBrien 2026%')"
        assert "''" not in criteria

    async def test_supplier_search_covers_the_supplier_own_reference(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_supplier_invoices(status="all", search_ref="F-2026")

        criteria = get.call_args[0][1]["sqlfilters"]
        assert "(t.ref:like:'%F-2026%')" in criteria
        assert "(t.ref_supplier:like:'%F-2026%')" in criteria
        assert criteria.startswith("(") and " or " in criteria

    async def test_supplier_list_sorts_descending_by_default(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_supplier_invoices(status="all")

        params = get.call_args[0][1]
        assert params["sortfield"] == "t.datef,t.rowid"
        assert params["sortorder"] == "DESC"

    async def test_a_malformed_period_is_refused(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            msg = await dolibarr_list_invoices(date_start="août 2026")
        assert "YYYY-MM-DD" in msg
        get.assert_not_called()


class TestRevenueSummaryBasis:
    """dolibarr_get_revenue_summary — assiette explicite et balayage complet."""

    @staticmethod
    def _invoice(date_ts: int, ht: str, ttc: str, statut: str = "1") -> dict:
        return {"id": "1", "total_ht": ht, "total_ttc": ttc, "date": date_ts,
                "fk_statut": statut, "paye": "0"}

    async def test_an_emitted_unpaid_invoice_counts_in_the_month(self) -> None:
        """Le CA du mois doit inclure une facture validée non encore encaissée."""
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = [self._invoice(1787011200, "46050.00", "55260.00", "1")]
            data = json.loads(await dolibarr_get_revenue_summary(year=2026, month=8))

        assert data["basis"] == "emises"
        assert data["month_ca_ht"] == 46050.0
        assert data["month_nb_invoices"] == 1

    async def test_the_status_filter_keeps_validated_and_paid(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_get_revenue_summary(year=2026, month=8)

        criteria = get.call_args_list[0][0][1]["sqlfilters"]
        assert "(t.fk_statut:>=:1)" in criteria
        assert "(t.fk_statut:<=:2)" in criteria
        # La grammaire des sqlfilters interdit les parenthèses dans une valeur :
        # (t.fk_statut:in:(1,2)) serait rejeté à l'analyse.
        assert ":in:" not in criteria

    async def test_the_scan_is_bounded_by_the_year_not_by_a_row_count(self) -> None:
        """La troncature à 500 lignes triées par rowid masquait les factures récentes."""
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_get_revenue_summary(year=2026, month=8)

        params = get.call_args_list[0][0][1]
        assert "(t.datef:>=:'2026-01-01')" in params["sqlfilters"]
        assert "(t.datef:<=:'2026-12-31')" in params["sqlfilters"]
        assert params["sortorder"] == "DESC"
        assert params["limit"] == server.REVENUE_SCAN_PAGE

    async def test_every_page_is_read(self) -> None:
        page = [self._invoice(1787011200, "100.00", "120.00")] * server.REVENUE_SCAN_PAGE
        pages = iter([page, page[:3], [], []])

        with patch("dolibarr_mcp.server._api_get_list",
                   new=AsyncMock(side_effect=lambda *a, **k: next(pages))):
            data = json.loads(await dolibarr_get_revenue_summary(year=2026, month=8))

        assert data["ytd_nb_invoices"] == server.REVENUE_SCAN_PAGE + 3

    async def test_the_cash_basis_reads_the_bank_journal(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint == "bankaccounts":
                return [_bank_account()]
            if endpoint.endswith("/links"):
                return [
                    {"type": "company", "url_id": "12", "label": "ACME"},
                    {"type": "payment", "url_id": "88", "label": "(CustomerInvoicePayment)"},
                ]
            if endpoint.endswith("/lines"):
                return [
                    _bank_line(id="901", ref="901", amount="7200.00"),
                    _bank_line(id="902", ref="902", amount="-300.00"),   # débit : ignoré
                ]
            return {}

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(
                await dolibarr_get_revenue_summary(year=2026, month=8, basis="encaissees")
            )

        assert data["basis"] == "encaissees"
        assert "TTC" in data["basis_label"]
        assert data["month_ca_ttc"] == 7200.0
        assert data["month_nb_payments"] == 1
        assert "month_ca_ht" not in data          # un encaissement n'a pas de base HT

    async def test_a_credit_without_a_payment_link_is_not_revenue(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint == "bankaccounts":
                return [_bank_account()]
            if endpoint.endswith("/links"):
                return [{"type": "banktransfert", "url_id": "5", "label": "Virement interne"}]
            if endpoint.endswith("/lines"):
                return [_bank_line(amount="5000.00")]
            return {}

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(
                await dolibarr_get_revenue_summary(year=2026, month=8, basis="encaissees")
            )

        assert data["ytd_ca_ttc"] == 0

    async def test_cash_accounts_are_left_out_of_the_bank_scan(self) -> None:
        seen = []

        async def fake_get(endpoint, params=None):
            if endpoint == "bankaccounts":
                return [_bank_account(id="3"), _bank_account(id="4", type="2")]
            if endpoint.endswith("/lines"):
                seen.append(endpoint)
            return []

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            await dolibarr_get_revenue_summary(year=2026, month=8, basis="encaissees")

        assert seen == ["bankaccounts/3/lines"]

    async def test_an_unknown_basis_is_refused(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            msg = await dolibarr_get_revenue_summary(basis="tresorerie")
        assert "emises" in msg and "encaissees" in msg
        get.assert_not_called()


class TestWebFormFallback:
    """Repli formulaire : jeton CSRF et session, isolés du reste du connecteur."""

    def test_the_csrf_token_is_read_whatever_the_attribute_order(self) -> None:
        assert server._extract_csrf_token(
            '<input type="hidden" name="token" value="abc123" />'
        ) == "abc123"
        assert server._extract_csrf_token(
            '<input value="xyz789" name="token" type="hidden">'
        ) == "xyz789"

    def test_a_page_without_token_yields_an_empty_string(self) -> None:
        assert server._extract_csrf_token("<html><body>rien</body></html>") == ""
        assert server._extract_csrf_token("") == ""

    async def test_the_fallback_refuses_without_configured_credentials(self) -> None:
        with patch("dolibarr_mcp.server.WEB_LOGIN", ""), \
             patch("dolibarr_mcp.server.WEB_PASSWORD", ""):
            with pytest.raises(DolibarrAPIError) as exc:
                await server._web_post("compta/bank/bankentries_list.php", [("a", "b")])
        assert "DOLIBARR_WEB_LOGIN" in str(exc.value)


class TestSqlFilterHelpers:
    """Assemblage des critères sqlfilters."""

    def test_like_values_lose_quotes_and_parentheses(self) -> None:
        assert server._sf_like("O'Brien (SA)") == "OBrien SA"

    def test_escape_still_doubles_quotes_for_equality(self) -> None:
        assert server._sf_escape("O'Brien") == "O''Brien"

    def test_empty_clauses_are_dropped(self) -> None:
        assert server._sf_and("(a:=:1)", "", "(b:=:2)") == "(a:=:1) and (b:=:2)"
        assert server._sf_and("", "") == ""

    def test_a_single_ref_field_needs_no_extra_parentheses(self) -> None:
        assert server._sf_ref_search("FA26", ("t.ref",)) == "(t.ref:like:'%FA26%')"

    def test_several_ref_fields_are_grouped_before_being_anded(self) -> None:
        criteria = server._sf_ref_search("F26", ("t.ref", "t.ref_supplier"))
        assert criteria.startswith("((") and criteria.endswith("))")
        assert " or " in criteria

    def test_bank_criteria_never_carry_an_alias(self) -> None:
        criteria = server._bank_date_filter(after="2026-01-01", before="2026-01-31")
        assert "t." not in criteria
        assert criteria == "(dateo:>=:'2026-01-01') and (dateo:<=:'2026-01-31')"

    def test_strict_after_excludes_the_boundary_day(self) -> None:
        assert server._bank_date_filter(after="2026-01-01", strict_after=True) == \
            "(dateo:>:'2026-01-01')"


# ---------------------------------------------------------------------------
# Facturation client, pièces jointes, référentiels, trésorerie
# ---------------------------------------------------------------------------


def _customer_invoice(**over) -> dict:
    """Facture client brouillon telle que la rend l'API."""
    base = {
        "id": "512", "ref": "(PROV512)", "socid": "42", "fk_statut": "0", "paye": "0",
        "total_ht": "2400.00", "total_tva": "480.00", "total_ttc": "2880.00",
        "date": 1787011200, "date_lim_reglement": 1789603200, "lines": [],
    }
    base.update(over)
    return base


class TestCreateInvoice:
    """dolibarr_create_invoice — le connecteur savait devis et pas facture."""

    async def test_the_invoice_is_created_then_reread(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = _customer_invoice()
            post.return_value = 512
            data = json.loads(await dolibarr_create_invoice(
                thirdparty_id=42, date="2026-08-18", due_date="2026-09-17",
                project_id=7, ref_customer="BC-2026-88",
            ))

        endpoint, payload = post.call_args_list[0][0]
        assert endpoint == "invoices"
        assert payload["socid"] == 42
        assert payload["date"] == "2026-08-18"
        assert payload["fk_project"] == 7
        assert payload["ref_client"] == "BC-2026-88"
        assert data["success"] is True
        assert data["ref"] == "(PROV512)"
        assert data["status_label"] == "brouillon"

    async def test_the_due_date_travels_as_date_lim_reglement(self) -> None:
        """C'est ce champ qu'Invoices::post() passe en $forceduedate ; sans lui
        Dolibarr recalcule l'échéance depuis les conditions du tiers."""
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = _customer_invoice()
            post.return_value = 512
            await dolibarr_create_invoice(thirdparty_id=42, due_date="2026-09-17")

        assert post.call_args_list[0][0][1]["date_lim_reglement"] == "2026-09-17"

    async def test_lines_carry_every_field_postline_dereferences(self) -> None:
        """Invoices::postLine() lit ses champs sans isset() — même piège qu'en fournisseur."""
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = _customer_invoice()
            post.return_value = 512
            data = json.loads(await dolibarr_create_invoice(
                thirdparty_id=42,
                lines=[{"description": "Audit FOSS", "qty": 3, "subprice": 800, "tva_tx": 20}],
            ))

        endpoint, body = post.call_args_list[1][0]
        assert endpoint == "invoices/512/lines"
        assert set(server.INVOICE_LINE_DEFAULTS) <= set(body)
        assert body["desc"] == "Audit FOSS"
        assert body["qty"] == 3.0
        assert body["subprice"] == 800.0
        assert body["tva_tx"] == 20.0
        assert body["situation_percent"] == 100
        assert data["lines_added"] == 1

    async def test_pu_ht_is_accepted_as_an_alias_of_subprice(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = _customer_invoice()
            post.return_value = 512
            await dolibarr_create_invoice(
                thirdparty_id=42, lines=[{"description": "Conseil", "pu_ht": 950}],
            )

        assert post.call_args_list[1][0][1]["subprice"] == 950.0

    async def test_a_line_without_description_is_skipped_and_reported(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = _customer_invoice()
            post.return_value = 512
            data = json.loads(await dolibarr_create_invoice(
                thirdparty_id=42, lines=[{"qty": 1, "subprice": 100}],
            ))

        assert data["lines_added"] == 0
        assert data["lines_requested"] == 1
        assert "description manquante" in data["line_warnings"][0]

    async def test_thirdparty_is_mandatory(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_create_invoice(thirdparty_id=0)
        assert "thirdparty_id" in msg
        post.assert_not_called()


class TestAddInvoiceLines:
    """dolibarr_add_invoice_lines — Facture::addline() refuse hors brouillon."""

    async def test_a_validated_invoice_is_refused_without_writing(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = _customer_invoice(fk_statut="1")
            data = json.loads(await dolibarr_add_invoice_lines(
                512, [{"description": "Extra", "subprice": 100}]
            ))

        assert data["success"] is False
        assert "verrouille" in data["message"]
        post.assert_not_called()

    async def test_a_draft_invoice_accepts_lines(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = _customer_invoice()
            data = json.loads(await dolibarr_add_invoice_lines(
                512, [{"description": "Extra", "subprice": 100}]
            ))

        assert data["success"] is True
        assert data["lines_added"] == 1
        assert post.call_args[0][0] == "invoices/512/lines"

    async def test_an_empty_list_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            msg = await dolibarr_add_invoice_lines(512, [])
        assert "vide" in msg
        get.assert_not_called()


class TestDeleteInvoiceLine:
    """dolibarr_delete_invoice_line — hors brouillon, l'appel n'a aucun effet."""

    async def test_a_validated_invoice_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            get.return_value = _customer_invoice(fk_statut="1")
            data = json.loads(await dolibarr_delete_invoice_line(512, 900))

        assert data["success"] is False
        delete.assert_not_called()

    async def test_the_line_count_is_compared_before_and_after(self) -> None:
        reads = iter([
            _customer_invoice(),                                   # garde brouillon
            _customer_invoice(lines=[{"id": "900"}, {"id": "901"}]),  # avant
            _customer_invoice(lines=[{"id": "901"}]),                 # après
        ])
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=lambda *a, **k: next(reads))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            data = json.loads(await dolibarr_delete_invoice_line(512, 900))

        assert delete.call_args[0][0] == "invoices/512/lines/900"
        assert data["success"] is True
        assert (data["lines_before"], data["lines_after"]) == (2, 1)

    async def test_a_delete_without_effect_is_reported_as_failure(self) -> None:
        lines = [{"id": "900"}, {"id": "901"}]
        reads = iter([
            _customer_invoice(),
            _customer_invoice(lines=lines),
            _customer_invoice(lines=lines),      # rien n'a bougé
        ])
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=lambda *a, **k: next(reads))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock):
            data = json.loads(await dolibarr_delete_invoice_line(512, 900))

        assert data["success"] is False
        assert "n'a pas pris" in data["message"]


class TestCreateInvoiceFromProposal:
    """dolibarr_create_invoice_from_proposal — Dolibarr n'a pas de route propale → facture."""

    @staticmethod
    def _proposal(**over) -> dict:
        base = {
            "id": "137", "ref": "PR2608-0021", "socid": "42", "fk_project": "7",
            "ref_client": "BC-88", "total_ht": "2400.00", "fk_statut": "2",
            "lines": [
                {"desc": "Audit FOSS", "qty": "3", "subprice": "800.00",
                 "tva_tx": "20.000", "remise_percent": "0", "product_type": "1"},
            ],
        }
        base.update(over)
        return base

    @staticmethod
    def _api(proposal: dict, invoice: dict):
        async def fake_get(endpoint, params=None):
            return proposal if endpoint.startswith("proposals/") else invoice

        return fake_get

    async def test_the_proposal_context_is_carried_over(self) -> None:
        invoice = _customer_invoice(total_ht="2400.00", lines=[{"id": "1"}])
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(self._proposal(), invoice))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            data = json.loads(await dolibarr_create_invoice_from_proposal(137))

        endpoint, payload = post.call_args_list[0][0]
        assert endpoint == "invoices"
        assert payload["socid"] == 42
        assert payload["fk_project"] == 7
        assert payload["ref_client"] == "BC-88"
        assert payload["linked_objects"] == {"propal": 137}   # onglet « éléments liés »
        assert data["proposal_ref"] == "PR2608-0021"

    async def test_the_lines_are_recreated_one_by_one(self) -> None:
        invoice = _customer_invoice(total_ht="2400.00", lines=[{"id": "1"}])
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(self._proposal(), invoice))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            data = json.loads(await dolibarr_create_invoice_from_proposal(137))

        endpoint, body = post.call_args_list[1][0]
        assert endpoint == "invoices/512/lines"
        assert body["desc"] == "Audit FOSS"
        assert body["qty"] == 3.0
        assert body["subprice"] == 800.0
        assert data["lines_added"] == 1
        assert data["success"] is True

    async def test_a_partially_copied_invoice_is_not_reported_as_success(self) -> None:
        proposal = self._proposal(lines=[
            {"desc": "A", "qty": "1", "subprice": "100"},
            {"desc": "B", "qty": "1", "subprice": "200"},
        ])
        invoice = _customer_invoice()

        async def fake_post(endpoint, data=None):
            if endpoint.endswith("/lines") and data.get("desc") == "B":
                raise RuntimeError("ErrorBadStatus")
            return 512

        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(proposal, invoice))), \
             patch("dolibarr_mcp.server.api_post", new=AsyncMock(side_effect=fake_post)):
            data = json.loads(await dolibarr_create_invoice_from_proposal(137))

        assert data["success"] is False
        assert "INCOMPLÈTE" in data["message"]
        assert data["lines_added"] == 1 and data["lines_requested"] == 2

    async def test_a_total_mismatch_raises_a_warning(self) -> None:
        invoice = _customer_invoice(total_ht="2000.00", lines=[{"id": "1"}])
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(self._proposal(), invoice))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            data = json.loads(await dolibarr_create_invoice_from_proposal(137))

        assert "total_warning" in data

    async def test_an_empty_proposal_produces_nothing(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(self._proposal(lines=[]), {}))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            data = json.loads(await dolibarr_create_invoice_from_proposal(137))

        assert data["success"] is False
        assert "aucune ligne" in data["message"]
        post.assert_not_called()

    async def test_the_proposal_status_is_left_alone_by_default(self) -> None:
        invoice = _customer_invoice(total_ht="2400.00")
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(self._proposal(), invoice))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            await dolibarr_create_invoice_from_proposal(137)

        assert not [c for c in post.call_args_list if "setinvoiced" in c[0][0]]

    async def test_the_proposal_can_be_marked_invoiced_on_demand(self) -> None:
        invoice = _customer_invoice(total_ht="2400.00")
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(self._proposal(), invoice))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            data = json.loads(await dolibarr_create_invoice_from_proposal(
                137, mark_proposal_invoiced=True
            ))

        assert [c for c in post.call_args_list if "setinvoiced" in c[0][0]]
        assert data["proposal_status"] == "facturée"


class TestDocuments:
    """Pièces jointes et régénération de PDF."""

    async def test_documents_are_listed_by_reference(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [{
                "name": "SI2608-0012.pdf", "relativename": "SI2608-0012/SI2608-0012.pdf",
                "size": "48211", "type": "file", "date": 1787011200,
            }]
            data = json.loads(await dolibarr_list_documents(
                module="supplier_invoice", ref="SI2608-0012"
            ))

        params = get.call_args[0][1]
        assert params["modulepart"] == "facture_fournisseur"
        assert params["ref"] == "SI2608-0012"
        assert data["documents"][0]["relative_path"] == "SI2608-0012/SI2608-0012.pdf"

    async def test_listing_needs_an_id_or_a_ref(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            msg = await dolibarr_list_documents(module="invoice")
        assert "object_id" in msg
        get.assert_not_called()

    async def test_a_local_file_is_sent_base64_encoded(self, tmp_path) -> None:
        target = tmp_path / "facture.pdf"
        target.write_bytes(b"%PDF-1.4 fake")

        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = []
            data = json.loads(await dolibarr_upload_document(
                file_path=str(target), module="supplier_invoice", ref="SI2608-0012",
            ))

        import base64
        endpoint, body = post.call_args[0]
        assert endpoint == "documents/upload"
        assert body["filename"] == "facture.pdf"
        assert body["fileencoding"] == "base64"
        assert base64.b64decode(body["filecontent"]) == b"%PDF-1.4 fake"
        assert body["overwriteifexists"] == 0
        assert data["success"] is True

    async def test_a_missing_file_is_refused(self, tmp_path) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_upload_document(
                file_path=str(tmp_path / "absent.pdf"), module="invoice", ref="FA1",
            )
        assert "introuvable" in msg
        post.assert_not_called()

    async def test_an_oversized_file_is_refused_before_reading_it(self, tmp_path) -> None:
        target = tmp_path / "gros.pdf"
        target.write_bytes(b"x")
        with patch("dolibarr_mcp.server.DOCUMENT_UPLOAD_MAX_BYTES", 0), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_upload_document(
                file_path=str(target), module="invoice", ref="FA1",
            )
        assert "trop volumineux" in msg
        post.assert_not_called()

    async def test_builddoc_never_returns_the_pdf_bytes(self) -> None:
        """Un PDF en base64 dans la réponse saturerait la fenêtre de contexte."""
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put:
            put.return_value = {
                "filename": "FA2608-0939.pdf", "content-type": "application/pdf",
                "filesize": 52341, "content": "JVBERi0xLjQK" * 5000, "encoding": "base64",
            }
            raw = await dolibarr_build_document(module="invoice", ref="FA2608-0939")

        data = json.loads(raw)
        assert "content" not in data
        assert "JVBERi" not in raw
        assert data["filesize"] == 52341
        assert put.call_args[0][1]["original_file"] == "FA2608-0939/FA2608-0939.pdf"

    async def test_an_explicit_path_overrides_the_default_convention(self) -> None:
        with patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock) as put:
            put.return_value = {}
            await dolibarr_build_document(
                module="invoice", ref="FA1", original_file="autre/chemin.pdf"
            )
        assert put.call_args[0][1]["original_file"] == "autre/chemin.pdf"


class TestReferenceLists:
    """Utilisateurs, produits, modes de règlement."""

    async def test_disabled_users_are_filtered_server_side(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_users()
        assert get.call_args[0][1]["sqlfilters"] == "(t.statut:=:1)"

    async def test_disabled_users_can_be_included(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_users(include_disabled=True)
        assert "sqlfilters" not in get.call_args[0][1]

    async def test_users_are_searchable_by_login_or_name(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [
                {"id": "4", "login": "bjean", "lastname": "Jean", "firstname": "Benjamin",
                 "email": "b@example.org", "statut": "1", "employee": "1"},
                {"id": "9", "login": "autre", "lastname": "Martin", "firstname": "Léa",
                 "statut": "1", "employee": "0"},
            ]
            data = json.loads(await dolibarr_list_users(search="jean"))

        assert data["count"] == 1
        assert data["users"][0]["id"] == 4
        assert data["users"][0]["employee"] is True

    async def test_products_and_services_are_selectable(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_products(kind="service")
        assert get.call_args[0][1]["mode"] == 2

        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_products()
        assert get.call_args[0][1]["mode"] == 0

    async def test_product_search_covers_ref_and_label(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_list_products(search="audit")

        criteria = get.call_args[0][1]["sqlfilters"]
        assert "(t.ref:like:'%audit%')" in criteria
        assert "(t.label:like:'%audit%')" in criteria

    async def test_an_unknown_kind_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            msg = await dolibarr_list_products(kind="abonnement")
        assert "'service'" in msg
        get.assert_not_called()

    async def test_payment_types_expose_code_and_id(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = _payment_types_rows()
            data = json.loads(await dolibarr_list_payment_types())

        by_code = {t["code"]: t for t in data["payment_types"]}
        assert by_code["VIR"]["id"] == 4
        assert data["count"] == 3


class TestCashForecast:
    """dolibarr_cash_forecast — la synthèse que le pilotage recoupait à la main."""

    @staticmethod
    def _api(customer: list, supplier: list, accounts: list | None = None):
        async def fake_get(endpoint, params=None):
            if endpoint == "bankaccounts":
                return accounts if accounts is not None else [_bank_account()]
            return {}

        async def fake_list(endpoint, params=None):
            return supplier if endpoint == "supplierinvoices" else customer

        return fake_get, fake_list

    @staticmethod
    def _inv(ref: str, ttc: str, due_offset: int, remaining: str | None = None) -> dict:
        from datetime import datetime, timedelta, timezone
        due = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=due_offset)
        row = {"id": "1", "ref": ref, "total_ttc": ttc, "thirdparty_name": "ACME",
               "date_lim_reglement": int(due.timestamp())}
        if remaining is not None:
            row["remaintopay"] = remaining
        return row

    async def test_the_projection_combines_balance_receivable_and_payable(self) -> None:
        get, lst = self._api(
            customer=[self._inv("FA1", "5000.00", 10)],
            supplier=[self._inv("SI1", "2000.00", 20)],
        )
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)):
            data = json.loads(await dolibarr_cash_forecast(horizon_days=90))

        assert data["opening_balance"] == 12500.0
        assert data["expected_in"] == 5000.0
        assert data["expected_out"] == 2000.0
        assert data["projected_balance"] == 15500.0

    async def test_overdue_invoices_land_in_their_own_bucket(self) -> None:
        get, lst = self._api(customer=[self._inv("FA1", "5000.00", -15)], supplier=[])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)):
            data = json.loads(await dolibarr_cash_forecast())

        assert data["overdue_in"] == 5000.0
        assert data["receivable"][0]["bucket"] == "en retard"

    async def test_remaintopay_wins_over_the_full_amount(self) -> None:
        """Un acompte déjà encaissé ne doit pas être reprévu."""
        get, lst = self._api(
            customer=[self._inv("FA1", "5000.00", 10, remaining="1500.00")], supplier=[],
        )
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)):
            data = json.loads(await dolibarr_cash_forecast())

        assert data["expected_in"] == 1500.0

    async def test_an_invoice_beyond_the_horizon_is_counted_apart(self) -> None:
        get, lst = self._api(customer=[self._inv("FA1", "5000.00", 200)], supplier=[])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)):
            data = json.loads(await dolibarr_cash_forecast(horizon_days=90))

        assert data["expected_in"] == 0                 # hors horizon
        assert data["receivable_total"] == 5000.0       # mais bien dû
        assert data["projected_balance"] == 12500.0

    async def test_a_fully_paid_invoice_is_dropped(self) -> None:
        get, lst = self._api(
            customer=[self._inv("FA1", "5000.00", 10, remaining="0")], supplier=[],
        )
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)):
            data = json.loads(await dolibarr_cash_forecast())

        assert data["receivable"] == []
        assert data["expected_in"] == 0

    async def test_only_validated_unpaid_invoices_are_scanned(self) -> None:
        get, lst = self._api(customer=[], supplier=[])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)) as m:
            await dolibarr_cash_forecast()

        assert all(c[0][1]["sqlfilters"] == "(t.fk_statut:=:1)" for c in m.call_args_list)

    async def test_cash_accounts_and_closed_accounts_stay_out(self) -> None:
        get, lst = self._api(
            customer=[], supplier=[],
            accounts=[_bank_account(), _bank_account(id="4", type="2"),
                      _bank_account(id="5", status="1")],
        )
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)):
            data = json.loads(await dolibarr_cash_forecast())

        assert [a["account_id"] for a in data["accounts"]] == [3]

    async def test_supplier_invoices_can_be_left_out(self) -> None:
        get, lst = self._api(customer=[], supplier=[self._inv("SI1", "2000.00", 20)])
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=get)), \
             patch("dolibarr_mcp.server._api_get_list", new=AsyncMock(side_effect=lst)):
            data = json.loads(await dolibarr_cash_forecast(include_supplier=False))

        assert data["payable"] == []
        assert data["expected_out"] == 0


class TestTransferBetweenAccounts:
    """dolibarr_transfer_between_accounts — la trésorerie totale ne bouge pas."""

    @staticmethod
    def _balances(seq: list):
        it = iter(seq)

        async def fake_get(endpoint, params=None):
            return next(it)

        return fake_get

    async def test_the_dedicated_route_creates_both_entries(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._balances(["5000", "1000", "3000", "3000"]))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            data = json.loads(await dolibarr_transfer_between_accounts(
                from_account_id=3, to_account_id=4, date="2026-08-18",
                amount=2000, description="Dotation compte courant",
            ))

        endpoint, payload = post.call_args[0]
        assert endpoint == "bankaccounts/transfer"
        assert payload["bankaccount_from_id"] == 3
        assert payload["bankaccount_to_id"] == 4
        assert payload["amount"] == 2000.0
        assert isinstance(payload["date"], int)      # castée en (int) côté Dolibarr
        assert data["success"] is True
        assert data["total_delta"] == 0.0            # un virement ne crée pas d'argent

    async def test_balances_that_did_not_move_as_expected_are_flagged(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._balances(["5000", "1000", "5000", "1000"]))), \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock):
            data = json.loads(await dolibarr_transfer_between_accounts(
                from_account_id=3, to_account_id=4, date="2026-08-18",
                amount=2000, description="Test",
            ))

        assert data["success"] is False
        assert "balance_warning" in data

    async def test_the_same_account_twice_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_transfer_between_accounts(
                from_account_id=3, to_account_id=3, date="2026-08-18",
                amount=100, description="Test",
            )
        assert "identiques" in msg
        post.assert_not_called()

    async def test_a_negative_amount_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            msg = await dolibarr_transfer_between_accounts(
                from_account_id=3, to_account_id=4, date="2026-08-18",
                amount=-100, description="Test",
            )
        assert "strictement positif" in msg
        post.assert_not_called()


class TestBankStatements:
    """dolibarr_list_bank_statements — état d'avancement du rapprochement."""

    async def test_entries_are_grouped_by_statement_number(self) -> None:
        lines = [
            _bank_line(id="1", ref="1", num_releve="2026-07", rappro="1", amount="100.00",
                       dateo=1785456000),
            _bank_line(id="2", ref="2", num_releve="2026-08", rappro="1", amount="200.00",
                       dateo=1787011200),
            _bank_line(id="3", ref="3", num_releve="2026-08", rappro="1", amount="-50.00",
                       dateo=1787011200),
            _bank_line(id="4", ref="4", num_releve="", rappro="0", amount="900.00",
                       dateo=1787011200),
        ]
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = lines
            data = json.loads(await dolibarr_list_bank_statements(account_id=3))

        by_num = {s["num_releve"]: s for s in data["statements"]}
        assert by_num["2026-08"]["entries"] == 2
        assert by_num["2026-08"]["total"] == 150.0
        assert data["unassigned_entries"] == 1
        assert data["unreconciled_entries"] == 1
        assert data["last_statement"] == "2026-08"

    async def test_the_default_window_is_a_year_and_is_announced(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            data = json.loads(await dolibarr_list_bank_statements(account_id=3))

        assert data["date_start"]
        criteria = get.call_args[0][1]["sqlfilters"]
        assert "t." not in criteria and "(dateo:>=:" in criteria


class TestBankEntriesReconciledFilter:
    """Le filtre `reconciled` sert le rapprochement, pas le solde cumulé."""

    @staticmethod
    def _api(lines: list):
        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return "1000"
            if endpoint.endswith("/links"):
                return []
            return lines

        return fake_get

    async def test_unreconciled_entries_are_filtered_server_side(self) -> None:
        calls = []

        async def fake_get(endpoint, params=None):
            calls.append((endpoint, params))
            return "1000" if endpoint.endswith("/balance") else [_bank_line()]

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            await dolibarr_list_bank_entries(account_id=3, reconciled="no", with_links=False)

        criteria = [p.get("sqlfilters", "") for e, p in calls if e.endswith("/lines") and p]
        assert "(rappro:=:0)" in criteria[0]
        assert "t." not in criteria[0]

    async def test_a_filtered_window_omits_the_running_balance(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._api([_bank_line()]))):
            data = json.loads(await dolibarr_list_bank_entries(
                account_id=3, reconciled="yes", with_links=False,
            ))

        assert "balance_after" not in data["entries"][0]
        assert "sous-ensemble" in data["running_balance_note"]

    async def test_an_unknown_value_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            msg = await dolibarr_list_bank_entries(account_id=3, reconciled="peut-être")
        assert "'yes'" in msg and "'no'" in msg
        get.assert_not_called()


class TestDeleteBankEntry:
    """dolibarr_delete_bank_entry — refuse tout ce qui n'est pas une saisie isolée."""

    @staticmethod
    def _api(line: dict, links: list, balances: list | None = None):
        it = iter(balances or ["1000", "800"])

        async def fake_get(endpoint, params=None):
            if endpoint.endswith("/balance"):
                return next(it)
            if endpoint.endswith("/links"):
                return links
            return line

        return fake_get

    async def test_a_reconciled_entry_is_refused(self) -> None:
        line = _bank_line(num_releve="2026-08", rappro="1")
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=self._api(line, []))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            data = json.loads(await dolibarr_delete_bank_entry(3, 901))

        assert data["success"] is False
        assert "rapprochée" in data["message"]
        delete.assert_not_called()

    async def test_an_entry_carrying_a_payment_is_refused(self) -> None:
        """Supprimer la ligne laisserait le règlement orphelin."""
        links = [{"type": "payment", "url_id": "88", "label": "(CustomerInvoicePayment)"}]
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(_bank_line(), links))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            data = json.loads(await dolibarr_delete_bank_entry(3, 901))

        assert data["success"] is False
        assert "orphelin" in data["message"]
        delete.assert_not_called()

    async def test_a_thirdparty_link_alone_does_not_block(self) -> None:
        """'company' désigne le tiers, pas un objet de règlement."""
        links = [{"type": "company", "url_id": "12", "label": "ACME"}]
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(_bank_line(amount="200.00"), links,
                                                       ["1000", "800"]))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            data = json.loads(await dolibarr_delete_bank_entry(3, 901))

        assert data["success"] is True
        assert delete.call_args[0][0] == "bankaccounts/3/lines/901"

    async def test_an_entry_from_another_account_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(_bank_line(fk_account="7"), []))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            data = json.loads(await dolibarr_delete_bank_entry(3, 901))

        assert data["success"] is False
        delete.assert_not_called()

    async def test_the_balance_must_move_by_the_opposite_amount(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(_bank_line(amount="200.00"), [],
                                                       ["1000", "800"]))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock):
            data = json.loads(await dolibarr_delete_bank_entry(3, 901))

        assert data["expected_delta"] == -200.0
        assert data["balance_delta"] == -200.0
        assert "balance_warning" not in data

    async def test_a_balance_that_did_not_move_is_flagged(self) -> None:
        with patch("dolibarr_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(_bank_line(amount="200.00"), [],
                                                       ["1000", "1000"]))), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock):
            data = json.loads(await dolibarr_delete_bank_entry(3, 901))

        assert data["success"] is False
        assert "balance_warning" in data


# ---------------------------------------------------------------------------
# Non-régression : défauts constatés à la recette du 31/08/2026
# ---------------------------------------------------------------------------


class TestCreditNotesAreNeverHidden:
    """Les avoirs ont un total NÉGATIF. Les filtrer par défaut fausse le CA.

    Constaté en production le 31/08/2026 : `min_amount` valant 0 par défaut, la
    comparaison `total_ttc < min_amount` écartait tout avoir. 13 des 100
    dernières factures de l'instance disparaissaient sans le moindre signal, et
    le CA d'août reconstitué depuis cette liste passait de 6 350 € — le chiffre
    juste, net des avoirs AV2608-0651 et AV2608-0652 — à 52 400 €.
    """

    @staticmethod
    def _rows() -> list:
        return [
            {"id": "447", "ref": "FA2608-0940", "total_ttc": "55260.00",
             "total_ht": "46050.00", "fk_statut": "1", "date": 1787011200},
            {"id": "448", "ref": "AV2608-0651", "total_ttc": "-33300.00",
             "total_ht": "-27750.00", "fk_statut": "2", "date": 1787011200},
            {"id": "449", "ref": "AV2608-0652", "total_ttc": "-21960.00",
             "total_ht": "-18300.00", "fk_statut": "2", "date": 1787011200},
        ]

    async def test_the_default_keeps_every_credit_note(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = self._rows()
            data = json.loads(await dolibarr_list_invoices(status="all"))

        assert data["count"] == 3
        assert {i["ref"] for i in data["invoices"]} == {
            "FA2608-0940", "AV2608-0651", "AV2608-0652"
        }
        assert data["credit_notes_count"] == 2

    async def test_an_explicit_threshold_still_filters(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = self._rows()
            data = json.loads(await dolibarr_list_invoices(status="all", min_amount=1000))

        assert [i["ref"] for i in data["invoices"]] == ["FA2608-0940"]

    async def test_supplier_credit_notes_are_kept_too(self) -> None:
        with patch("dolibarr_mcp.server._api_get_list", new_callable=AsyncMock) as get:
            get.return_value = [{"id": "1", "ref": "SI1", "total_ttc": "-500.00",
                                 "fk_statut": "2"}]
            data = json.loads(await dolibarr_list_supplier_invoices(status="all"))

        assert data["count"] == 1


class TestRevenueMakesCreditNotesVisible:
    """Le CA net est juste ; c'est son opacité qui le faisait passer pour faux."""

    @staticmethod
    def _august() -> list:
        return [
            {"id": "450", "total_ht": "350.00", "total_ttc": "420.00", "date": 1787011200},
            {"id": "447", "total_ht": "46050.00", "total_ttc": "55260.00", "date": 1787011200},
            {"id": "448", "total_ht": "-27750.00", "total_ttc": "-33300.00", "date": 1787011200},
            {"id": "449", "total_ht": "-18300.00", "total_ttc": "-21960.00", "date": 1787011200},
            {"id": "445", "total_ht": "6000.00", "total_ttc": "7200.00", "date": 1787011200},
        ]

    async def test_the_net_figure_matches_production(self) -> None:
        pages = iter([self._august(), [], []])
        with patch("dolibarr_mcp.server._api_get_list",
                   new=AsyncMock(side_effect=lambda *a, **k: next(pages))):
            data = json.loads(await dolibarr_get_revenue_summary(year=2026, month=8))

        # 350 + 46050 + 6000 − 27750 − 18300 = 6350, chiffre relevé en production.
        assert data["month_ca_ht"] == 6350.0
        assert data["month_nb_invoices"] == 5

    async def test_the_gross_and_the_credit_notes_are_broken_out(self) -> None:
        pages = iter([self._august(), [], []])
        with patch("dolibarr_mcp.server._api_get_list",
                   new=AsyncMock(side_effect=lambda *a, **k: next(pages))):
            data = json.loads(await dolibarr_get_revenue_summary(year=2026, month=8))

        assert data["month_gross_ht"] == 52400.0          # ce que montre list_invoices
        assert data["month_credit_notes_ht"] == -46050.0
        assert data["month_nb_credit_notes"] == 2
        # brut + avoirs = net : la réconciliation doit tomber juste
        assert data["month_gross_ht"] + data["month_credit_notes_ht"] == data["month_ca_ht"]
        assert "NET des avoirs" in data["basis_label"]


class TestDocumentModuleNames:
    """`fetchObjectByElement` parle en noms d'élément, pas en noms de module REST."""

    def test_supplier_invoice_is_translated(self) -> None:
        assert server._document_module("supplier_invoice") == "facture_fournisseur"
        assert server._document_module("invoice") == "facture"
        assert server._document_module("proposal") == "propal"

    def test_an_unknown_module_passes_through(self) -> None:
        assert server._document_module("ticket") == "ticket"

    def test_a_provisional_ref_loses_its_parentheses(self) -> None:
        """Le dossier réel est PROV92 ; '(PROV92)' fait répondre du non-JSON."""
        assert server._document_ref("(PROV92)") == "PROV92"
        assert server._document_ref("SI2603-0085") == "SI2603-0085"

    async def test_a_missing_object_is_not_reported_as_an_empty_folder(self) -> None:
        response = httpx.Response(
            404, json={"error": {"code": 404, "message": "not found for modulepart = x"}},
            request=httpx.Request("GET", "http://test"),
        )
        exc = httpx.HTTPStatusError("", request=response.request, response=response)
        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=exc)):
            data = json.loads(await dolibarr_list_documents(module="supplier_invoice", ref="X"))

        assert data["success"] is False
        assert "not found for modulepart" in data["message"]   # message Dolibarr conservé
        assert "count" not in data

    async def test_upload_normalises_the_ref_before_sending(self, tmp_path) -> None:
        target = tmp_path / "zz.pdf"
        target.write_bytes(b"%PDF")
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = [{"name": "zz.pdf", "size": "4"}]
            data = json.loads(await dolibarr_upload_document(
                file_path=str(target), module="supplier_invoice", ref="(PROV92)",
            ))

        body = post.call_args[0][1]
        assert body["ref"] == "PROV92"
        assert body["modulepart"] == "facture_fournisseur"
        assert data["ref"] == "PROV92"
        assert data["ref_requested"] == "(PROV92)"
        assert "verification_warning" not in data

    async def test_a_file_absent_from_the_reread_is_flagged(self, tmp_path) -> None:
        target = tmp_path / "zz.pdf"
        target.write_bytes(b"%PDF")
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock):
            get.return_value = [{"name": "autre.pdf", "size": "10"}]
            data = json.loads(await dolibarr_upload_document(
                file_path=str(target), module="invoice", ref="FA1",
            ))

        assert "verification_warning" in data


class TestAccountBalanceIsTheRealOne:
    """`GET /bankaccounts/{id}` ne renseigne pas `balance` : il vaut 0."""

    async def test_toggle_reports_the_computed_balance(self) -> None:
        async def fake_get(endpoint, params=None):
            return "3.00" if endpoint.endswith("/balance") else _bank_account(balance="0")

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock):
            data = json.loads(await dolibarr_toggle_bank_reconciliation(3, True))

        assert data["account"]["balance"] == 3     # et non 0, défaut de l'objet API


class TestDeleteInvoice:
    """dolibarr_delete_invoice — seuls les brouillons."""

    async def test_a_validated_invoice_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            get.return_value = _customer_invoice(id="450", ref="FA2608-0941", fk_statut="1")
            data = json.loads(await dolibarr_delete_invoice(450))

        assert data["success"] is False
        assert "séquence" in data["message"]
        delete.assert_not_called()

    async def test_a_draft_is_deleted_and_the_removal_verified(self) -> None:
        response = httpx.Response(404, request=httpx.Request("GET", "http://test"))
        gone = httpx.HTTPStatusError("", request=response.request, response=response)
        reads = iter([_customer_invoice(), gone])

        async def fake_get(endpoint, params=None):
            value = next(reads)
            if isinstance(value, Exception):
                raise value
            return value

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            data = json.loads(await dolibarr_delete_invoice(512))

        assert data["success"] is True
        assert delete.call_args[0][0] == "invoices/512"

    async def test_a_delete_without_effect_is_reported(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock):
            get.return_value = _customer_invoice()
            data = json.loads(await dolibarr_delete_invoice(512))

        assert data["success"] is False
        assert "toujours lisible" in data["message"]

    async def test_the_supplier_flag_switches_the_route(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            get.return_value = {"id": "92", "ref": "(PROV92)", "fk_statut": "0"}
            await dolibarr_delete_invoice(92, supplier=True)

        assert delete.call_args[0][0] == "supplierinvoices/92"


class TestValidateSupplierInvoice:
    """Le cycle fournisseur ne s'arrête plus au brouillon."""

    async def test_validation_returns_the_definitive_ref(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            get.return_value = {"id": "92", "ref": "SI2608-0090", "ref_supplier": "ZZ-01",
                                "fk_statut": "1", "paye": "0", "total_ht": "3.00"}
            data = json.loads(await dolibarr_validate_supplier_invoice(92))

        assert post.call_args[0][0] == "supplierinvoices/92/validate"
        assert data["success"] is True
        assert data["ref"] == "SI2608-0090"

    async def test_a_still_draft_invoice_is_not_a_success(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock):
            get.return_value = {"id": "92", "ref": "(PROV92)", "fk_statut": "0"}
            data = json.loads(await dolibarr_validate_supplier_invoice(92))

        assert data["success"] is False


class TestResolveEmailCoversInternalUsers:
    """Une adresse interne n'est ni un tiers ni un contact.

    Constaté le 31/08/2026 : `resolve_email("jbranson@inno3.fr")` rendait
    found=false, ce qui interdisait de désigner un responsable d'action interne
    autrement qu'en nom libre. La table des utilisateurs n'était pas consultée.
    """

    @staticmethod
    def _user() -> dict:
        return {"id": "5", "login": "jbranson", "firstname": "J", "lastname": "Branson",
                "email": "jbranson@inno3.fr", "statut": "1"}

    async def test_an_internal_address_is_resolved(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.startswith("users/email/"):
                return self._user()
            return []

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_resolve_email("jbranson@inno3.fr"))

        assert data["found"] is True
        assert data["user"]["id"] == 5
        assert data["user"]["login"] == "jbranson"
        assert data["matched_as"] == ["user"]

    async def test_the_dedicated_user_route_is_used(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_resolve_email("Jbranson@INNO3.fr")

        assert get.call_args_list[0][0][0] == "users/email/jbranson@inno3.fr"

    async def test_a_plus_addressed_email_is_url_encoded(self) -> None:
        """Dans un chemin, un « + » non encodé se lit comme une espace."""
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            await dolibarr_resolve_email("compta+dolibarr@inno3.fr")

        assert get.call_args_list[0][0][0] == "users/email/compta%2Bdolibarr@inno3.fr"

    async def test_a_contact_still_wins_its_own_block(self) -> None:
        async def fake_get(endpoint, params=None):
            if endpoint.startswith("users/email/"):
                raise RuntimeError("404")          # adresse non interne
            if endpoint == "contacts":
                return [{"id": "75", "firstname": "V", "lastname": "C",
                         "email": "v@client.fr", "socid": "186"}]
            return {"id": "186", "name": "Client"}

        with patch("dolibarr_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await dolibarr_resolve_email("v@client.fr"))

        assert data["found"] is True
        assert data["contact"]["id"] == "75"
        assert data["matched_as"] == ["contact"]

    async def test_an_unknown_address_says_so_including_users(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = []
            data = json.loads(await dolibarr_resolve_email("inconnu@nulle-part.test"))

        assert data["found"] is False
        assert "utilisateur" in data["suggestion"]
        assert data["matched_as"] == []


# ---------------------------------------------------------------------------
# Tickets — clôture, messages, mise à jour, suppression
# ---------------------------------------------------------------------------


def _ticket(**over) -> dict:
    """Ticket tel que rendu par l'API REST : statuts en chaînes, status_label
    toujours null (le libellé est calculé par l'UI, jamais sérialisé)."""
    base = {
        "id": "321", "ref": "TS2608-0042", "track_id": "abc123def456",
        "subject": "Imputation 1h", "message": "Une heure de support.",
        "fk_statut": "1", "status": "1", "status_label": None,
        "severity_code": "NORMAL", "type_code": "SUPPORT",
        "fk_soc": "12", "fk_project": "180", "fk_user_assign": "0",
        "datec": 1774000000, "date_close": "",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
class TestCloseTicket:
    """dolibarr_close_ticket — PUT /tickets/{id}.

    L'API REST n'a pas d'endpoint de clôture, et Ticket::update() écrit la
    colonne fk_statut depuis $this->status (Dolibarr récents) : un PUT ne
    portant que fk_statut répond 200 sans rien écrire. L'outil envoie les
    deux clés puis relit le ticket pour vérifier le statut appliqué.
    """

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_the_put_carries_both_status_keys_and_close_metadata(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        mock_get.side_effect = [
            _ticket(),
            _ticket(fk_statut="8", status="8", date_close=1774100000),
        ]

        data = json.loads(await dolibarr_close_ticket(ticket_id=321))

        endpoint, payload = mock_put.call_args[0]
        assert endpoint == "tickets/321"
        assert payload["status"] == 8
        assert payload["fk_statut"] == 8
        assert payload["progress"] == 100          # Ticket::close() fait pareil
        assert payload["date_close"] > 946684800   # timestamp Unix courant
        assert data["success"] is True
        assert data["previous_status"] == 1
        assert data["status"] == 8
        assert data["status_label"] == "Fermé"

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_a_ref_resolves_through_the_filtered_list(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        mock_get.side_effect = [
            [_ticket()],
            _ticket(fk_statut="8", status="8"),
        ]

        data = json.loads(await dolibarr_close_ticket(ref="TS2608-0042"))

        list_endpoint, list_params = mock_get.call_args_list[0][0]
        assert list_endpoint == "tickets"
        assert "TS2608-0042" in list_params["sqlfilters"]
        assert mock_put.call_args[0][0] == "tickets/321"
        assert data["success"] is True

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_the_closing_message_goes_out_before_the_status_change(
        self, mock_put: AsyncMock, mock_post: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """Message d'abord, statut ensuite : si le POST échoue, le ticket reste
        ouvert et l'appel peut être rejoué sans perdre le message."""
        order: list[str] = []
        mock_post.side_effect = lambda *a, **k: order.append("post") or 456
        mock_put.side_effect = lambda *a, **k: order.append("put") or {}
        mock_get.side_effect = [_ticket(), _ticket(fk_statut="8", status="8")]

        data = json.loads(await dolibarr_close_ticket(ticket_id=321, message="Temps imputé, je clos."))

        assert order == ["post", "put"]
        post_endpoint, post_payload = mock_post.call_args[0]
        assert post_endpoint == "tickets/newmessage"
        assert post_payload == {"track_id": "abc123def456", "message": "Temps imputé, je clos."}
        assert data["message_posted"] is True

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_an_already_closed_ticket_is_a_noop_not_a_304_crash(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """Rejouer le PUT sur un ticket déjà fermé ferait un 304 Restler ;
        l'outil court-circuite avant."""
        mock_get.return_value = _ticket(fk_statut="8", status="8")

        data = json.loads(await dolibarr_close_ticket(ticket_id=321))

        mock_put.assert_not_called()
        assert data["success"] is True
        assert "inchangé" in data["message"]

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_a_silently_ignored_put_is_reported_as_a_failure(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        """Le piège d'origine : HTTP 200 mais fk_statut inchangé en base."""
        mock_get.side_effect = [_ticket(), _ticket()]  # relecture : toujours statut 1

        out = await dolibarr_close_ticket(ticket_id=321)

        assert "Échec silencieux" in out
        assert "resté au statut 1" in out

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_an_unknown_status_is_rejected_before_any_call(
        self, mock_get: AsyncMock
    ) -> None:
        out = await dolibarr_close_ticket(ticket_id=321, status=4)

        mock_get.assert_not_called()
        assert "Statut 4 invalide" in out

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_reopening_does_not_stamp_a_close_date(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        mock_get.side_effect = [
            _ticket(fk_statut="8", status="8"),
            _ticket(fk_statut="3", status="3"),
        ]

        data = json.loads(await dolibarr_close_ticket(ticket_id=321, status=3))

        _, payload = mock_put.call_args[0]
        assert "date_close" not in payload
        assert "progress" not in payload
        assert data["status_label"] == "En cours"


@pytest.mark.asyncio
class TestTicketStatusLabels:
    """status_label remontait null : l'API ne sérialise jamais le libellé
    (calculé par l'UI via getLibStatut). Il est reconstitué côté connecteur."""

    async def test_list_tickets_rebuilds_the_label_from_fk_statut(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_ticket(fk_statut="3"), _ticket(id="322", fk_statut="0")]
            data = json.loads(await dolibarr_list_tickets())

        assert data["tickets"][0]["status_label"] == "En cours"
        assert data["tickets"][1]["status_label"] == "Non lu"

    async def test_get_ticket_rebuilds_the_label_too(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = _ticket(fk_statut="8")
            data = json.loads(await dolibarr_get_ticket(ticket_id=321))

        assert data["status_label"] == "Fermé"

    async def test_a_label_provided_by_the_api_wins_over_the_map(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = [_ticket(status_label="Résolu")]
            data = json.loads(await dolibarr_list_tickets())

        assert data["tickets"][0]["status_label"] == "Résolu"


@pytest.mark.asyncio
class TestGetTicketMessages:
    """GET /tickets/{id} sérialise le fil sous `messages` : l'exposer évite
    un aller-retour navigateur pour lire l'historique."""

    async def test_messages_are_stripped_and_truncated(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = _ticket(messages=[
                {"id": "9", "fk_user_author": "4", "datec": 1774000000,
                 "message": "<p>Bonjour,<br>c'est fait.</p>", "private": "0"},
            ])
            data = json.loads(await dolibarr_get_ticket(ticket_id=321))

        msg = data["messages"][0]
        assert msg["author_user_id"] == "4"
        assert msg["message"] == "Bonjour,\nc'est fait."
        assert msg["date"].startswith("2026-")

    async def test_an_old_dolibarr_without_messages_yields_an_empty_list(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = _ticket()
            data = json.loads(await dolibarr_get_ticket(ticket_id=321))

        assert data["messages"] == []


@pytest.mark.asyncio
class TestAddTicketMessage:
    """dolibarr_add_ticket_message — POST /tickets/newmessage (track_id)."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock)
    async def test_the_message_is_posted_with_the_track_id(
        self, mock_post: AsyncMock, mock_get: AsyncMock
    ) -> None:
        mock_get.return_value = _ticket()
        mock_post.return_value = 456

        data = json.loads(await dolibarr_add_ticket_message(ticket_id=321, message="Point d'étape."))

        endpoint, payload = mock_post.call_args[0]
        assert endpoint == "tickets/newmessage"
        assert payload == {"track_id": "abc123def456", "message": "Point d'étape."}
        assert data["success"] is True

    async def test_an_empty_message_is_rejected(self) -> None:
        out = await dolibarr_add_ticket_message(ticket_id=321)
        assert "obligatoire" in out


@pytest.mark.asyncio
class TestUpdateTicket:
    """dolibarr_update_ticket — PUT /tickets/{id}, champs fournis seulement."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_put", new_callable=AsyncMock)
    async def test_only_the_provided_fields_reach_the_put(
        self, mock_put: AsyncMock, mock_get: AsyncMock
    ) -> None:
        mock_get.side_effect = [_ticket(), _ticket(severity_code="HIGH", fk_project="200")]

        data = json.loads(await dolibarr_update_ticket(
            ticket_id=321, severity="high", project_id=200,
        ))

        endpoint, payload = mock_put.call_args[0]
        assert endpoint == "tickets/321"
        assert payload == {"severity_code": "HIGH", "fk_project": 200}
        assert data["updated_fields"] == ["fk_project", "severity_code"]

    async def test_no_field_at_all_is_refused(self) -> None:
        with patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = _ticket()
            out = await dolibarr_update_ticket(ticket_id=321)

        assert "Aucun champ" in out

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    async def test_a_bad_severity_is_rejected(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _ticket()
        out = await dolibarr_update_ticket(ticket_id=321, severity="urgent")
        assert "invalide" in out


@pytest.mark.asyncio
class TestDeleteTicket:
    """dolibarr_delete_ticket — DELETE /tickets/{id}, irréversible."""

    @patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock)
    @patch("dolibarr_mcp.server.api_delete", new_callable=AsyncMock)
    async def test_delete_targets_the_resolved_id(
        self, mock_delete: AsyncMock, mock_get: AsyncMock
    ) -> None:
        mock_get.return_value = [_ticket()]

        data = json.loads(await dolibarr_delete_ticket(ref="TS2608-0042"))

        mock_delete.assert_called_once_with("tickets/321")
        assert data["success"] is True
        assert data["ref"] == "TS2608-0042"

    async def test_no_identifier_is_refused(self) -> None:
        out = await dolibarr_delete_ticket()
        assert "ticket_id ou ref" in out


from contextlib import contextmanager


@contextmanager
def _observer(fn):
    """Installe un observateur de création de ticket, et le retire après.

    Les observateurs sont un état de module : sans retrait, un test en
    contaminerait un autre.
    """
    server.TICKET_CREATE_OBSERVERS.append(fn)
    try:
        yield fn
    finally:
        server.TICKET_CREATE_OBSERVERS.remove(fn)


@pytest.mark.asyncio
class TestCreateTicketReportsSideEffects:
    """dolibarr_create_ticket — ce qu'un module tiers écrit au passage est dit.

    Reproduction du défaut du 18/09/2026 : sur l'instance inno³, le module
    maison `supportcredits` débite un carnet client sur TICKET_CREATE. Onze
    tickets créés par cet outil ont débité onze unités sur deux carnets, et le
    retour de l'outil n'en disait rien : l'écriture ne s'est vue qu'au refus du
    `consume` suivant (« statut=2, restant=0 »). Le connecteur ne peut pas
    empêcher ce débit — il est gouverné côté serveur — mais il doit le NOMMER.
    """

    async def test_the_answer_says_whether_side_effects_were_looked_for(self) -> None:
        # Sans observateur installé, le connecteur n'a rien regardé. Le dire est
        # autre chose que d'affirmer qu'il ne s'est rien passé.
        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            data = json.loads(
                await dolibarr_create_ticket(
                    subject="s", message="m", thirdparty_id=37, project_id=73,
                    type_code="SUPPORT",
                )
            )

        assert data["side_effects_checked"] is False
        assert data["side_effects"] == []

    async def test_an_observed_debit_is_named_in_the_answer(self) -> None:
        async def observer(ticket_id: int, thirdparty_id: int) -> dict:
            return {
                "module": "supportcredits",
                "debited": True,
                "carnet_ref": "CARNET-2026-00045",
                "units": 1,
                "message": "1 unité débitée sur le carnet CARNET-2026-00045.",
            }

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            with _observer(observer):
                data = json.loads(
                    await dolibarr_create_ticket(
                        subject="s", message="m", thirdparty_id=37,
                    )
                )

        assert data["side_effects_checked"] is True
        assert data["side_effects"][0]["carnet_ref"] == "CARNET-2026-00045"
        assert "CARNET-2026-00045" in data["message"]

    async def test_the_docstring_documents_the_supportcredits_debit(self) -> None:
        doc = dolibarr_create_ticket.__doc__ or ""
        assert "supportcredits" in doc
        assert "side_effects" in doc

    async def test_the_docstring_prices_the_severity_parameter(self) -> None:
        # `severity` ne choisit pas qu'un libellé : sur une instance en débit
        # automatique il fixe le nombre d'unités prélevées au client, et rien
        # dans son nom ne le laisse deviner.
        doc = dolibarr_create_ticket.__doc__ or ""
        assert "5" in doc and "unités" in doc
        assert "SUPPORTCREDITS_CONSUME_MODE" in doc

    async def test_a_broken_observer_never_fails_the_creation(self) -> None:
        # Le ticket est créé : une panne d'observateur ne doit pas se lire
        # comme un échec d'écriture. Mais elle doit se voir.
        async def observer(ticket_id: int, thirdparty_id: int) -> dict:
            raise RuntimeError("boom")

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            with _observer(observer):
                data = json.loads(
                    await dolibarr_create_ticket(subject="s", message="m", thirdparty_id=37)
                )

        assert data["success"] is True
        assert data["ticket_id"] == 512
        assert data["side_effects"][0]["error"].startswith("RuntimeError")
        assert "n'a PAS été vérifié" in data["side_effects"][0]["message"]

    async def test_an_observer_with_nothing_to_say_adds_no_line(self) -> None:
        async def observer(ticket_id: int, thirdparty_id: int) -> None:
            return None

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            with _observer(observer):
                data = json.loads(
                    await dolibarr_create_ticket(subject="s", message="m", thirdparty_id=37)
                )

        # Regardé, et rien trouvé : ce n'est pas la même chose que pas regardé.
        assert data["side_effects_checked"] is True
        assert data["side_effects"] == []
        assert data["message"] == "Ticket créé avec succès (ID 512)."

    async def test_the_observer_receives_the_new_ticket_id_and_the_thirdparty(self) -> None:
        seen: list = []

        async def observer(ticket_id: int, thirdparty_id: int) -> None:
            seen.append((ticket_id, thirdparty_id))
            return None

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = "512"
            with _observer(observer):
                await dolibarr_create_ticket(subject="s", message="m", thirdparty_id=37)

        # L'API rend l'id comme une chaîne sur certaines instances : un
        # observateur doit recevoir un entier, pas à le deviner.
        assert seen == [(512, 37)]

    async def test_a_ticket_without_thirdparty_still_calls_the_observer(self) -> None:
        # thirdparty_id = 0 : c'est à l'observateur de décider qu'il n'a rien à
        # chercher, pas au coeur de le supposer.
        seen: list = []

        async def observer(ticket_id: int, thirdparty_id: int) -> None:
            seen.append((ticket_id, thirdparty_id))
            return None

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = 512
            with _observer(observer):
                await dolibarr_create_ticket(subject="s", message="m")

        assert seen == [(512, 0)]

    async def test_registering_twice_reports_the_debit_once(self) -> None:
        async def observer(ticket_id: int, thirdparty_id: int) -> dict:
            return {"module": "supportcredits", "units": 1, "message": "1 unité débitée."}

        server.register_ticket_create_observer(observer)
        server.register_ticket_create_observer(observer)
        try:
            with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
                post.return_value = 512
                data = json.loads(
                    await dolibarr_create_ticket(subject="s", message="m", thirdparty_id=37)
                )
        finally:
            server.TICKET_CREATE_OBSERVERS.remove(observer)

        assert len(data["side_effects"]) == 1
        assert data["message"].count("1 unité débitée.") == 1

    async def test_an_unparsable_id_does_not_break_the_creation(self) -> None:
        seen: list = []

        async def observer(ticket_id: int, thirdparty_id: int) -> None:
            seen.append(ticket_id)
            return None

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = {"unexpected": "shape"}
            with _observer(observer):
                data = json.loads(
                    await dolibarr_create_ticket(subject="s", message="m", thirdparty_id=37)
                )

        assert data["success"] is True
        assert seen == [0]


@pytest.mark.asyncio
class TestLogTimeDocstringMatchesReality:
    """dolibarr_log_time — la docstring ne doit pas annoncer une route morte.

    Reproduction du défaut du 18/09/2026 : la docstring affirmait « sur
    Dolibarr 23.0.x cette route est CASSÉE EN AMONT et tout appel échoue ».
    Dix-sept imputations ont pourtant été écrites et relues ce jour-là. Une
    limite périmée coûte autant qu'une limite tue : elle a fait envisager un
    contournement navigateur pour rien.
    """

    async def test_the_docstring_does_not_claim_every_call_fails(self) -> None:
        doc = dolibarr_log_time.__doc__ or ""
        assert "tout appel échoue" not in doc
        assert "CASSÉE EN AMONT" not in doc
        # Et elle dit l'état réel, pas seulement l'absence de l'ancien
        # verdict : une docstring qui se tait laisse le doute intact.
        assert "fonctionne" in doc

    async def test_the_docstring_still_names_the_known_upstream_bug(self) -> None:
        # La fatale existe sur certaines 23.0.x : la supprimer serait retomber
        # dans l'erreur inverse.
        doc = dolibarr_log_time.__doc__ or ""
        assert "addtimespent" in doc
        assert "23" in doc
