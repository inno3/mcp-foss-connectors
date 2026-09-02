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
        # L'historique mocké s'arrête là (aucun `nextCursor`) : les cinq échecs
        # SONT toute l'histoire, le compte est donc exact et non un minorant.
        # `consecutive_failures_at_least` ne sert plus qu'au dépassement de
        # HEALTH_MAX_DEPTH — cf. TestConsecutiveFailuresReachTheirRealValue.
        assert "consecutive_failures_at_least" not in wf
        assert wf["never_succeeded"] is True
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


# ---------------------------------------------------------------------------
# Défauts relevés en recette du bundle 1.2.0, les 01 et 02/09/2026, contre
# l'instance de production (19 workflows actifs).
# ---------------------------------------------------------------------------


def _hours_ago(hours: float) -> str:
    """Horodatage ISO relatif à maintenant : les fenêtres sont glissantes."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _failures(count, workflow_id, *, start_id=0, first_hours_ago=1.0, step=0.5,
              status="error") -> list:
    """Une série d'échecs d'un même workflow, du plus récent au plus ancien."""
    return [
        _execution(id=start_id + i, workflowId=workflow_id, status=status,
                   startedAt=_hours_ago(first_hours_ago + i * step))
        for i in range(count)
    ]


def _fake_instance(rows, *, accepts_date_bounds=False, workflows=None, names=None):
    """Instance n8n mockée : /executions pagine par curseur, comme la vraie.

    `accepts_date_bounds=False` reproduit l'instance inno³, qui répond 400 dès
    qu'on lui passe `startedAfter` — d'où le filtrage côté client.
    """
    names = names or {}
    calls: list = []

    async def fake_get(path, params=None):
        params = dict(params or {})
        calls.append((path, params))
        if path == "executions":
            if not accepts_date_bounds and (
                "startedAfter" in params or "startedBefore" in params
            ):
                resp = httpx.Response(400, request=httpx.Request("GET", "http://t"))
                raise httpx.HTTPStatusError("", request=resp.request, response=resp)
            sel = list(rows)
            if params.get("status"):
                sel = [r for r in sel if r.get("status") == params["status"]]
            if params.get("workflowId"):
                sel = [r for r in sel if r.get("workflowId") == params["workflowId"]]
            if params.get("startedAfter"):
                sel = [r for r in sel
                       if str(r.get("startedAt") or "") >= params["startedAfter"]]
            sel.sort(key=lambda r: str(r.get("startedAt") or ""), reverse=True)
            start = int(params.get("cursor") or 0)
            size = int(params.get("limit") or 10)
            page = {"data": sel[start:start + size]}
            if start + size < len(sel):
                page["nextCursor"] = str(start + size)
            return page
        if path == "workflows":
            return {"data": workflows or []}
        if path.startswith("workflows/"):
            wid = path.split("/", 1)[1]
            return {"name": names.get(wid, wid)}
        return _execution_with_error()

    fake_get.calls = calls
    return fake_get


_NAMES = {
    "wf-bot": "Bot Matrix inno3pilot",
    "wf-cal": "Cal.com miroir",
    "wf-brief": "Brief quotidien",
}


def _sixty_failures() -> list:
    """La semaine relevée le 01/09 : 45 + 10 + 5 échecs sur trois workflows."""
    return (
        _failures(45, "wf-bot", start_id=100)
        + _failures(10, "wf-cal", start_id=200, first_hours_ago=2)
        + _failures(5, "wf-brief", start_id=300, first_hours_ago=3, step=24)
    )


class TestFailureCountsAreTotalsNotPageSizes:
    """Défaut 1 : troncature silencieuse au plafond `limit`.

    Sur la même fenêtre de 168 h, limit=50 rendait « 36 / 9 / 5 » et limit=60
    « 45 / 10 / 5 ». Un compte qui varie avec le plafond n'est pas un total, et
    rien dans la réponse ne signalait que la fenêtre n'était pas couverte.
    """

    async def test_by_workflow_carries_window_totals_not_the_page_count(self) -> None:
        api = _fake_instance(_sixty_failures(), names=_NAMES)
        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=api)):
            data = json.loads(await n8n_list_failures(hours=168, limit=50))

        totals = {g["workflow_name"]: g["failures"] for g in data["by_workflow"]}
        assert totals == {"Bot Matrix inno3pilot": 45, "Cal.com miroir": 10,
                          "Brief quotidien": 5}
        assert data["total_failures"] == 60
        assert data["window_complete"] is True
        assert data["count"] == 50            # `executions` reste borné par limit
        assert "truncation_note" in data      # et la coupe est dite

    async def test_the_totals_do_not_move_with_the_limit(self) -> None:
        def totals(payload):
            return {g["workflow_name"]: g["failures"] for g in payload["by_workflow"]}

        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=_fake_instance(_sixty_failures(),
                                                            names=_NAMES))):
            fifty = json.loads(await n8n_list_failures(hours=168, limit=50))
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=_fake_instance(_sixty_failures(),
                                                            names=_NAMES))):
            sixty = json.loads(await n8n_list_failures(hours=168, limit=60))

        assert totals(fifty) == totals(sixty)
        assert fifty["total_failures"] == sixty["total_failures"] == 60

    async def test_a_window_the_page_cap_cannot_cover_is_announced(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(server, "EXEC_MAX_PAGES", 1)
        api = _fake_instance(_failures(600, "wf-bot", step=0.2), names=_NAMES)
        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=api)):
            data = json.loads(await n8n_list_failures(hours=168, limit=50))

        assert data["window_complete"] is False
        assert data["oldest_seen_at"]
        assert any("NON couverte" in n for n in data["notes"])


class TestTheWindowIsActuallyAWindow:
    """Défaut 2 : `hours` filtrait la page demandée, pas la fenêtre.

    L'instance refuse les bornes de date sur /executions ; le filtre côté
    client ne portait que sur une page, si bien que « les 168 dernières
    heures » valait en réalité « les N derniers échecs, puis filtrés ».
    """

    async def test_the_window_is_walked_until_its_bound_is_crossed(self) -> None:
        rows = (_failures(30, "wf-bot", start_id=100)
                + _failures(30, "wf-old", start_id=400, first_hours_ago=300, step=1))
        api = _fake_instance(rows, names={"wf-bot": "Bot Matrix inno3pilot",
                                          "wf-old": "Vieux workflow"})
        with patch("n8n_mcp.server.api_get", new=AsyncMock(side_effect=api)):
            data = json.loads(await n8n_list_failures(hours=168, limit=10))

        assert data["total_failures"] == 30
        assert data["workflows_affected"] == 1
        assert data["window_complete"] is True
        assert any("côté client" in n for n in data["notes"])

    def test_the_docstring_states_the_instance_limitation(self) -> None:
        doc = n8n_list_failures.__doc__ or ""
        assert "400" in doc and "page" in doc


class TestConsecutiveFailuresReachTheirRealValue:
    """Défaut 3 : neuf échecs consécutifs se lisaient « 5 ».

    `per_workflow` plafonnait la profondeur inspectée sans que la valeur réelle
    soit atteignable, et `last_success_at = null` ne distinguait pas « n'a
    jamais réussi » de « dernier succès hors de la profondeur inspectée ».
    """

    @staticmethod
    def _instance(statuses, wid="esFmnG1NIMOQD2C0"):
        rows = [
            _execution(id=i, workflowId=wid, status=s, startedAt=_hours_ago(i * 24))
            for i, s in enumerate(statuses)
        ]
        return _fake_instance(
            rows,
            workflows=[{"id": wid, "name": "Brief quotidien direction", "active": True}],
        )

    async def test_the_streak_is_followed_beyond_the_first_page(self) -> None:
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._instance(["error"] * 9))):
            data = json.loads(await n8n_workflows_health())

        wf = data["workflows"][0]
        assert wf["consecutive_failures"] == 9          # et non 5
        assert "consecutive_failures_at_least" not in wf
        assert wf["last_success_at"] is None
        assert wf["never_succeeded"] is True            # levée de l'ambiguïté
        assert wf["oldest_failure_at"]

    async def test_a_success_beyond_the_first_page_closes_the_streak(self) -> None:
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._instance(["error"] * 6
                                                            + ["success"] * 3))):
            data = json.loads(await n8n_workflows_health())

        wf = data["workflows"][0]
        assert wf["consecutive_failures"] == 6
        assert wf["last_success_at"] is not None
        assert "never_succeeded" not in wf
        assert "last_success_unknown" not in wf

    async def test_the_depth_actually_inspected_is_reported(self, monkeypatch) -> None:
        monkeypatch.setattr(server, "HEALTH_MAX_DEPTH", 10)
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._instance(["error"] * 40))):
            data = json.loads(await n8n_workflows_health())

        wf = data["workflows"][0]
        assert wf["inspected"] == 10
        assert wf["consecutive_failures"] == 10
        assert wf["consecutive_failures_at_least"] is True
        assert wf["last_success_unknown"] is True       # ≠ « n'a jamais réussi »
        assert "never_succeeded" not in wf

    def test_the_docstring_states_what_per_workflow_now_means(self) -> None:
        doc = (n8n_workflows_health.__doc__ or "").lower()
        assert "première passe" in doc and "health_max_depth" in doc


class TestSweepingTheInstance:
    """Défaut 4 : un workflow déclenché toutes les 2 min sature l'historique.

    Sans filtre, 13 des 15 exécutions rendues appartenaient au même workflow :
    l'appel ne balayait pas l'instance, il rendait un seul workflow.
    """

    @staticmethod
    def _rows():
        noisy = [_execution(id=i, workflowId="igqOctuwbRaabeUS", status="success",
                            startedAt=_hours_ago(i / 30)) for i in range(200)]
        others = [_execution(id=900 + i, workflowId=f"wf-{i}", status="success",
                             startedAt=_hours_ago(5 + i)) for i in range(4)]
        return noisy + others

    async def test_a_noisy_workflow_can_be_excluded(self) -> None:
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=_fake_instance(self._rows()))):
            data = json.loads(await n8n_get_executions(
                limit=15, exclude_workflow_ids="igqOctuwbRaabeUS"))

        assert data["count"] == 4
        assert "igqOctuwbRaabeUS" not in {
            e["workflow_id"] for e in data["executions"]
        }
        assert data["excluded_workflow_ids"] == ["igqOctuwbRaabeUS"]

    async def test_one_per_workflow_gives_a_view_of_the_instance(self) -> None:
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=_fake_instance(self._rows()))):
            data = json.loads(await n8n_get_executions(limit=15, one_per_workflow=True))

        ids = [e["workflow_id"] for e in data["executions"]]
        assert len(ids) == len(set(ids)) == 5
        assert data["one_per_workflow"] is True

    async def test_without_a_filter_the_call_still_costs_one_page(self) -> None:
        with patch("n8n_mcp.server.api_get", new_callable=AsyncMock) as get:
            get.return_value = {"data": [_execution(status="success")]}
            await n8n_get_executions(limit=15)

        assert get.await_count == 1


class TestPaginationDegradesInsteadOfBreaking:
    """Paginer, c'est parier sur `cursor`. Une instance peut le refuser.

    Le pari ne doit jamais coûter la première page, qui, elle, a répondu.
    """

    @staticmethod
    def _refuses_cursor(rows):
        refused = httpx.HTTPStatusError(
            "", request=httpx.Request("GET", "http://t"),
            response=httpx.Response(400, request=httpx.Request("GET", "http://t")),
        )

        async def fake_get(path, params=None):
            params = dict(params or {})
            if path == "executions":
                if "startedAfter" in params or "cursor" in params:
                    raise refused
                sel = [r for r in rows if r.get("status") == params.get("status", r.get("status"))]
                size = int(params.get("limit") or 10)
                return {"data": sel[:size], "nextCursor": "next"}
            if path == "workflows":
                return {"data": [{"id": "W1", "name": "Brief", "active": True}]}
            if path.startswith("workflows/"):
                return {"name": "Brief"}
            return _execution_with_error()

        return fake_get

    async def test_a_refused_next_page_keeps_the_first_one(self) -> None:
        rows = _failures(3, "W1")
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._refuses_cursor(rows))):
            data = json.loads(await n8n_get_executions(
                limit=15, exclude_workflow_ids="autre"))

        assert data["count"] == 3
        assert data["pages_scanned"] == 1
        assert "sweep_note" in data          # le balayage s'est arrêté, il le dit

    async def test_a_refused_next_page_leaves_the_streak_a_minorant(self) -> None:
        rows = [_execution(id=i, workflowId="W1", status="error",
                           startedAt=_hours_ago(i * 24)) for i in range(5)]
        with patch("n8n_mcp.server.api_get",
                   new=AsyncMock(side_effect=self._refuses_cursor(rows))):
            data = json.loads(await n8n_workflows_health())

        wf = data["workflows"][0]
        assert wf["consecutive_failures"] == 5
        assert wf["consecutive_failures_at_least"] is True
        assert wf["last_success_unknown"] is True      # et non « n'a jamais réussi »
        assert "never_succeeded" not in wf
