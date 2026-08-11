"""Tests unitaires pour le serveur MCP Dolibarr."""

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Définir les variables d'environnement avant l'import
os.environ.setdefault("DOLIBARR_API_KEY", "test-key-123")
os.environ.setdefault("DOLIBARR_URL", "https://dolibarr.test.local")

from dolibarr_mcp.server import (
    TIMESPENT_ENDPOINT,
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
    dolibarr_close_task,
    dolibarr_create_project,
    dolibarr_dashboard,
    dolibarr_get_invoice,
    dolibarr_get_proposal,
    dolibarr_get_supplier_invoice,
    dolibarr_list_invoices,
    dolibarr_list_projects,
    dolibarr_list_proposals,
    dolibarr_list_supplier_invoices,
    dolibarr_list_tasks,
    dolibarr_list_thirdparties,
    dolibarr_log_time,
    dolibarr_update_invoice,
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
        assert set(payload) == {"date", "duration", "note", "user_id"}
        # dol_stringtotime attend une date lisible, pas un timestamp Unix.
        assert payload["date"] == "2026-07-31 00:00:00"
        assert payload["user_id"] == 7

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


@pytest.mark.asyncio
class TestLogTimeErrorDiagnosis:
    """Un 404 nu est indébogable : chaque cause doit être nommée."""

    async def _fail_with(self, exc: Exception, task_reply: object = None,
                         task_raises: Exception | None = None) -> dict:
        async def _get(endpoint: str, *a: object, **k: object) -> object:
            if task_raises is not None:
                raise task_raises
            return task_reply

        with patch("dolibarr_mcp.server.api_post", new_callable=AsyncMock) as post, \
             patch("dolibarr_mcp.server.api_get", new_callable=AsyncMock,
                   side_effect=_get):
            post.side_effect = exc
            return json.loads(await dolibarr_log_time(task_id=42, duration=1))

    async def test_missing_task_is_named_as_such(self) -> None:
        data = await self._fail_with(
            _http_error(404, {"error": {"code": 404, "message": "Task not found"}}),
            task_raises=_http_error(404),
        )
        assert data["cause"] == "tache_inexistante"
        assert "dolibarr_list_tasks" in data["hint"]

    async def test_existing_task_points_at_the_route(self) -> None:
        data = await self._fail_with(_http_error(404), task_reply=_task())
        assert data["cause"] == "route_ou_methode_invalide"
        assert "addtimespent" in data["hint"]

    async def test_permission_refusal_is_not_confused_with_a_missing_task(self) -> None:
        data = await self._fail_with(
            _http_error(403, {"error": {"code": 403, "message": "Access not allowed"}})
        )
        assert data["cause"] == "droits_insuffisants"
        assert data["dolibarr_error"] == "Access not allowed"

    async def test_unassigned_user_is_the_named_suspect_on_a_5xx(self) -> None:
        data = await self._fail_with(_http_error(500), task_reply=_task())
        assert data["cause"] == "utilisateur_non_affecte_ou_erreur_interne"
        assert "affect" in data["hint"]

    async def test_the_four_causes_produce_four_distinct_messages(self) -> None:
        """Le point du correctif : chaque échec doit se distinguer des autres."""
        cases = [
            await self._fail_with(_http_error(404), task_raises=_http_error(404)),
            await self._fail_with(_http_error(404), task_reply=_task()),
            await self._fail_with(_http_error(403)),
            await self._fail_with(_http_error(500), task_reply=_task()),
        ]
        assert all(c["success"] is False for c in cases)
        assert len({c["cause"] for c in cases}) == 4
        assert len({c["hint"] for c in cases}) == 4
        # La route en cause est toujours remontée, pour rendre l'échec debuggable.
        assert all(c["endpoint"] == "tasks/42/addtimespent" for c in cases)

    async def test_dolibarr_error_body_is_surfaced(self) -> None:
        data = await self._fail_with(
            _http_error(500, {"error": {"code": 500, "message": "Error when adding time"}}),
            task_reply=_task(),
        )
        assert data["dolibarr_error"] == "Error when adding time"

    async def test_empty_error_body_does_not_invent_a_message(self) -> None:
        data = await self._fail_with(_http_error(500), task_reply=_task())
        assert data["dolibarr_error"] is None


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
