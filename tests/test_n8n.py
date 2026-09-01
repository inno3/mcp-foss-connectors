"""Tests unitaires offline pour le connecteur n8n.

Tous les appels HTTP sont mockés : la suite tourne sans réseau ni token réel.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("N8N_BASE_URL", "https://n8n.example.test")
os.environ.setdefault("N8N_API_KEY", "n8n_api_xxxx")

from n8n_mcp import server  # noqa: E402
from n8n_mcp.server import (  # noqa: E402
    _dumps,
    _filter_workflow_for_put,
    _nodes_summary,
    n8n_delete_execution,
    n8n_get_execution_detail,
    n8n_get_executions,
    n8n_get_workflow,
    n8n_list_failures,
    n8n_list_tags,
    n8n_list_workflows,
    n8n_retry_execution,
    n8n_workflows_health,
)


class TestHelpers:
    def test_nodes_summary_keeps_short_type(self) -> None:
        nodes = [{"name": "Start", "type": "n8n-nodes-base.start"}]
        assert _nodes_summary(nodes) == [{"name": "Start", "type": "start"}]

    def test_filter_workflow_for_put_whitelists_keys(self) -> None:
        wf = {
            "name": "wf",
            "nodes": [],
            "connections": {},
            "id": "abc",  # not allowed
            "active": True,  # not allowed
            "meta": None,  # dropped when null
            "settings": {"executionOrder": "v1", "binaryMode": "x"},
        }
        out = _filter_workflow_for_put(wf)
        assert set(out) == {"name", "nodes", "connections", "settings"}
        assert out["settings"] == {"executionOrder": "v1"}
        assert "meta" not in out

    def test_dumps_compact(self) -> None:
        assert _dumps([1, 2]) == "[1,2]"


@pytest.mark.asyncio
class TestListWorkflows:
    async def test_unwraps_data_envelope(self) -> None:
        payload = {"data": [{"id": "1", "name": "W", "active": True, "updatedAt": "t"}]}
        with patch(
            "n8n_mcp.server.api_get", new_callable=AsyncMock, return_value=payload
        ) as mock_get:
            raw = await n8n_list_workflows(limit=10)
        assert mock_get.call_args[0][0] == "workflows"
        assert mock_get.call_args[1]["params"] == {"limit": 10}
        data = json.loads(raw)
        assert data[0]["id"] == "1"
        assert data[0]["active"] is True

    async def test_limit_capped_at_200(self) -> None:
        with patch(
            "n8n_mcp.server.api_get", new_callable=AsyncMock, return_value=[]
        ) as mock_get:
            await n8n_list_workflows(limit=9999)
        assert mock_get.call_args[1]["params"]["limit"] == 200


@pytest.mark.asyncio
class TestGetWorkflow:
    async def test_summarizes_nodes(self) -> None:
        payload = {
            "id": "9",
            "name": "Flow",
            "active": False,
            "tags": [{"name": "prod"}],
            "nodes": [{"name": "HTTP", "type": "n8n-nodes-base.httpRequest"}],
        }
        with patch(
            "n8n_mcp.server.api_get", new_callable=AsyncMock, return_value=payload
        ) as mock_get:
            raw = await n8n_get_workflow("9")
        assert mock_get.call_args[0][0] == "workflows/9"
        data = json.loads(raw)
        assert data["node_count"] == 1
        assert data["nodes"] == [{"name": "HTTP", "type": "httpRequest"}]
        assert data["tags"] == ["prod"]


# ---------------------------------------------------------------------------
# Diagnostic et surveillance des exécutions
# ---------------------------------------------------------------------------


def _execution(**over) -> dict:
    """Exécution telle que la rend GET /executions : SANS le champ `data`."""
    base = {
        "id": 31027, "workflowId": "esFmnG1NIMOQD2C0", "status": "error",
        "mode": "trigger", "finished": False,
        "startedAt": "2026-08-31T05:30:00.044Z", "stoppedAt": "2026-08-31T05:30:02.830Z",
    }
    base.update(over)
    return base


def _execution_with_error(node="API — Agenda aujourd'hui (CalDAV)",
                          message="Converting circular structure to JSON") -> dict:
    """Exécution relue avec includeData=true : c'est là que vit l'erreur."""
    return dict(_execution(), data={
        "resultData": {
            "lastNodeExecuted": node,
            "runData": {node: [{"error": {"message": message, "name": "NodeApiError"}}]},
        }
    })


class TestErrorsAreActuallyResolved:
    """`GET /executions` ne renvoie pas `data` : l'erreur ne peut pas y être.

    La version précédente cherchait pourtant l'erreur dans la réponse de liste,
    si bien que le champ `error` promis par la docstring restait toujours
    absent. Constaté le 31/08/2026 sur le workflow « Brief quotidien direction »,
    en échec chaque matin depuis le 25/08 sans qu'aucune cause ne remonte.
    """

    async def test_the_error_is_fetched_per_failed_execution(self) -> None:
        async def fake_get(path, params=None):
            if path == "executions":
                return {"data": [_execution()]}
            return _execution_with_error()

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await n8n_get_executions(workflow_id="esFmnG1NIMOQD2C0"))

        ex = data["executions"][0]
        assert ex["error"] == "Converting circular structure to JSON"
        assert ex["errors"][0]["node"] == "API — Agenda aujourd'hui (CalDAV)"
        assert data["errors_resolved"] == 1

    async def test_a_successful_execution_costs_no_extra_call(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = {"data": [_execution(status="success")]}
            data = json.loads(await n8n_get_executions())

        assert get.await_count == 1          # la liste, et rien de plus
        assert data["errors_resolved"] == 0

    async def test_with_errors_false_skips_the_lookup(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = {"data": [_execution()]}
            await n8n_get_executions(with_errors=False)

        assert get.await_count == 1

    async def test_every_node_error_is_collected_not_just_the_first(self) -> None:
        raw = {"data": {"resultData": {"runData": {
            "A": [{"error": {"message": "premier échec"}}],
            "B": [{"error": {"message": "second échec"}}],
        }}}}
        assert [e["message"] for e in server._exec_errors(raw)] == [
            "premier échec", "second échec"
        ]

    async def test_the_lookup_budget_is_announced_when_reached(self) -> None:
        rows = [_execution(id=i) for i in range(server.EXEC_ERROR_LOOKUP_MAX + 5)]

        async def fake_get(path, params=None):
            return {"data": rows} if path == "executions" else _execution_with_error()

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await n8n_get_executions(limit=250))

        skipped = [e for e in data["executions"] if e.get("error_lookup_skipped")]
        assert len(skipped) == 5
        assert data["errors_resolved"] == server.EXEC_ERROR_LOOKUP_MAX


class TestExecutionsAcrossAllWorkflows:
    """workflow_id est devenu optionnel : 17 appels remplacés par un seul."""

    async def test_no_workflow_id_means_no_filter(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = {"data": []}
            await n8n_get_executions()

        assert "workflowId" not in get.call_args.kwargs["params"]

    async def test_date_bounds_are_forwarded(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = {"data": []}
            await n8n_get_executions(started_after="2026-08-30T00:00:00Z")

        assert get.call_args.kwargs["params"]["startedAfter"] == "2026-08-30T00:00:00Z"

    async def test_an_instance_that_rejects_date_bounds_falls_back(self) -> None:
        """Les bornes sont récentes ; une instance ancienne répond 400."""
        response = httpx.Response(400, request=httpx.Request("GET", "http://t"))
        refused = httpx.HTTPStatusError("", request=response.request, response=response)
        calls = []

        async def fake_get(path, params=None):
            calls.append(params or {})
            if "startedAfter" in (params or {}):
                raise refused
            return {"data": [_execution(status="success", startedAt="2026-08-31T05:30:00Z"),
                             _execution(status="success", startedAt="2026-01-01T00:00:00Z")]}

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await n8n_get_executions(started_after="2026-08-30T00:00:00Z"))

        assert len(calls) == 2                       # tentative, puis repli
        assert data["count"] == 1                    # filtré côté client
        assert "côté client" in data["note"]

    async def test_the_limit_is_capped_at_the_api_maximum(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = {"data": []}
            await n8n_get_executions(limit=9999)

        assert get.call_args.kwargs["params"]["limit"] == server.EXEC_MAX_LIMIT


class TestListFailures:
    """« Qu'est-ce qui a échoué cette nuit ? » en un appel."""

    @staticmethod
    def _api(rows: list, names: dict):
        async def fake_get(path, params=None):
            if path == "executions":
                return {"data": rows if (params or {}).get("status") == "error" else []}
            if path.startswith("workflows/"):
                return {"name": names.get(path.split("/")[1], "?")}
            return _execution_with_error()

        return fake_get

    async def test_failures_are_grouped_by_workflow(self) -> None:
        rows = [_execution(id=1), _execution(id=2),
                _execution(id=3, workflowId="autre")]
        api = self._api(rows, {"esFmnG1NIMOQD2C0": "Brief quotidien direction",
                               "autre": "Autre workflow"})
        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=api)):
            data = json.loads(await n8n_list_failures(hours=24))

        assert data["count"] == 3
        assert data["workflows_affected"] == 2
        top = data["by_workflow"][0]
        assert top["workflow_name"] == "Brief quotidien direction"
        assert top["failures"] == 2
        assert top["last_error"] == "Converting circular structure to JSON"

    async def test_the_window_is_sent_as_a_date_bound(self) -> None:
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params or {}))
            return {"data": []}

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await n8n_list_failures(hours=48))

        assert data["window_hours"] == 48
        assert any("startedAfter" in p for _, p in calls)

    async def test_an_instance_without_crashed_status_still_reports_errors(self) -> None:
        response = httpx.Response(400, request=httpx.Request("GET", "http://t"))
        refused = httpx.HTTPStatusError("", request=response.request, response=response)

        async def fake_get(path, params=None):
            if path == "executions":
                if (params or {}).get("status") == "crashed":
                    raise refused
                return {"data": [_execution()]}
            if path.startswith("workflows/"):
                return {"name": "Brief"}
            return _execution_with_error()

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await n8n_list_failures())

        assert data["count"] == 1
        assert any("crashed" in n for n in data["notes"])

    async def test_the_workflow_name_is_resolved_once_per_workflow(self) -> None:
        rows = [_execution(id=i) for i in range(4)]
        calls = []

        async def fake_get(path, params=None):
            calls.append(path)
            if path == "executions":
                return {"data": rows if (params or {}).get("status") == "error" else []}
            if path.startswith("workflows/"):
                return {"name": "Brief"}
            return _execution_with_error()

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            await n8n_list_failures()

        assert sum(1 for c in calls if c.startswith("workflows/")) == 1


class TestWorkflowsHealth:
    """Le compteur d'échecs consécutifs distingue l'incident du workflow cassé."""

    @staticmethod
    def _api(statuses: list):
        rows = [
            _execution(id=i, status=s, startedAt=f"2026-08-{31 - i:02d}T05:30:00Z")
            for i, s in enumerate(statuses)
        ]

        async def fake_get(path, params=None):
            if path == "workflows":
                return {"data": [{"id": "W1", "name": "Brief quotidien", "active": True}]}
            if path == "executions":
                return {"data": rows}
            return _execution_with_error()

        return fake_get

    async def test_a_workflow_failing_every_night_is_ranked_first(self) -> None:
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(["error"] * 5))):
            data = json.loads(await n8n_workflows_health())

        wf = data["workflows"][0]
        assert wf["health"] == "en échec"
        assert wf["consecutive_failures"] == 5
        assert wf["consecutive_failures_at_least"] is True   # aucun succès dans la fenêtre
        assert wf["last_error"] == "Converting circular structure to JSON"
        assert data["failing"] == 1

    async def test_an_isolated_failure_is_not_a_broken_workflow(self) -> None:
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(["error", "success", "success"]))):
            data = json.loads(await n8n_workflows_health())

        wf = data["workflows"][0]
        assert wf["consecutive_failures"] == 1
        assert "consecutive_failures_at_least" not in wf
        assert wf["last_success_at"] is not None

    async def test_a_healthy_workflow_costs_no_error_lookup(self) -> None:
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._api(["success"]))) as get:
            data = json.loads(await n8n_workflows_health())

        assert data["workflows"][0]["health"] == "ok"
        assert data["failing"] == 0
        assert not any(c.args and str(c.args[0]).startswith("executions/")
                       for c in get.await_args_list)

    async def test_an_active_workflow_that_never_ran_is_flagged(self) -> None:
        async def fake_get(path, params=None):
            if path == "workflows":
                return {"data": [{"id": "W1", "name": "Jamais", "active": True}]}
            return {"data": []}

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await n8n_workflows_health())

        assert data["never_ran"] == 1
        assert data["workflows"][0]["health"] == "jamais exécuté"

    async def test_inactive_workflows_are_skipped_by_default(self) -> None:
        async def fake_get(path, params=None):
            if path == "workflows":
                return {"data": [{"id": "W1", "name": "Off", "active": False}]}
            return {"data": []}

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await n8n_workflows_health())

        assert data["checked"] == 0


class TestExecutionDetailStaysReadable:
    """195 000 caractères sur l'exécution 31027 : l'outil de diagnostic saturait."""

    @staticmethod
    def _fat_execution() -> dict:
        huge = {"body": "x" * 50000}
        return dict(_execution(), data={"resultData": {
            "lastNodeExecuted": "HTTP",
            "runData": {"HTTP": [{"data": {"main": [[{"json": huge}]]}}]},
        }})

    async def test_payloads_are_omitted_by_default(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = self._fat_execution()
            raw = await n8n_get_execution_detail("31027")

        assert len(raw) < 2000                      # contre ~195 000 auparavant
        assert "xxxxxxxxxx" not in raw
        data = json.loads(raw)
        assert data["nodes"]["HTTP"]["output_count"] == 1
        assert "first_item" not in data["nodes"]["HTTP"]
        assert "include_output=True" in data["output_note"]

    async def test_an_opted_in_preview_is_bounded_and_says_so(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = self._fat_execution()
            data = json.loads(await n8n_get_execution_detail(
                "31027", include_output=True, max_item_chars=200))

        node = data["nodes"]["HTTP"]
        assert len(node["first_item"]) == 200
        assert node["preview_truncated"] is True
        assert node["first_item_chars"] > 50000
        assert "preview_note" in data

    async def test_errors_are_listed_flat_for_direct_reading(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = _execution_with_error()
            data = json.loads(await n8n_get_execution_detail("31027"))

        assert data["errors"][0]["message"] == "Converting circular structure to JSON"
        assert data["errors"][0]["node"] == "API — Agenda aujourd'hui (CalDAV)"
        assert data["duration_s"] == 2.79


class TestRetryAndDelete:
    """Écritures sur l'historique d'exécution."""

    async def test_retry_defaults_to_the_version_that_failed(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("n8n_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = _execution(id=31099, status="success")
            get.return_value = _execution(id=31099, status="success")
            data = json.loads(await n8n_retry_execution("31027"))

        path, payload = post.call_args[0]
        assert path == "executions/31027/retry"
        assert payload == {"loadWorkflow": False}
        assert data["success"] is True

    async def test_retry_can_load_the_corrected_workflow(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock), \
             patch("n8n_mcp.server.api_post", new_callable=AsyncMock) as post:
            post.return_value = _execution(id=31099, status="success")
            await n8n_retry_execution("31027", load_workflow=True)

        assert post.call_args[0][1] == {"loadWorkflow": True}

    async def test_an_instance_without_retry_says_so(self) -> None:
        response = httpx.Response(404, request=httpx.Request("POST", "http://t"))
        exc = httpx.HTTPStatusError("", request=response.request, response=response)
        with patch("n8n_mcp.server.api_post", new=AsyncMock(side_effect=exc)):
            data = json.loads(await n8n_retry_execution("31027"))

        assert data["success"] is False
        assert "n'expose pas" in data["message"]

    async def test_deletion_is_verified_by_rereading(self) -> None:
        response = httpx.Response(404, request=httpx.Request("GET", "http://t"))
        gone = httpx.HTTPStatusError("", request=response.request, response=response)
        reads = iter([_execution(), gone])

        async def fake_get(path, params=None):
            value = next(reads)
            if isinstance(value, Exception):
                raise value
            return value

        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=fake_get)), \
             patch("n8n_mcp.server.api_delete", new_callable=AsyncMock) as delete:
            data = json.loads(await n8n_delete_execution("31027"))

        assert data["success"] is True
        assert delete.call_args[0][0] == "executions/31027"

    async def test_a_delete_without_effect_is_reported(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get, \
             patch("n8n_mcp.server.api_delete", new_callable=AsyncMock):
            get.return_value = _execution()
            data = json.loads(await n8n_delete_execution("31027"))

        assert data["success"] is False
        assert "toujours lisible" in data["message"]


class TestTags:
    async def test_tags_are_listed(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = {"data": [{"id": "t1", "name": "pilotage"}]}
            data = json.loads(await n8n_list_tags())

        assert data["success"] is True
        assert data["tags"][0]["name"] == "pilotage"

    async def test_an_instance_without_tags_does_not_pretend_it_has_none(self) -> None:
        response = httpx.Response(404, request=httpx.Request("GET", "http://t"))
        exc = httpx.HTTPStatusError("", request=response.request, response=response)
        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=exc)):
            data = json.loads(await n8n_list_tags())

        assert data["success"] is False
        assert "count" not in data
