"""Tests unitaires pour le serveur MCP Dolibarr."""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

# Définir les variables d'environnement avant l'import
os.environ.setdefault("DOLIBARR_API_KEY", "test-key-123")
os.environ.setdefault("DOLIBARR_URL", "https://dolibarr.test.local")

from dolibarr_mcp.server import (
    DolibarrAPIError,
    _api_get_list,
    _clamp_limit,
    _dumps,
    _format_doc_line,
    _format_error,
    _line_type_label,
    _num,
    _parse_json_or_raise,
    _strip_html,
    dolibarr_create_project,
    dolibarr_dashboard,
    dolibarr_get_invoice,
    dolibarr_get_proposal,
    dolibarr_get_supplier_invoice,
    dolibarr_list_invoices,
    dolibarr_list_projects,
    dolibarr_list_proposals,
    dolibarr_list_supplier_invoices,
    dolibarr_list_thirdparties,
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
