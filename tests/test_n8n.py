"""Tests unitaires offline pour le connecteur n8n.

Tous les appels HTTP sont mockés : la suite tourne sans réseau ni token réel.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("N8N_BASE_URL", "https://n8n.example.test")
os.environ.setdefault("N8N_API_KEY", "n8n_api_xxxx")

from n8n_mcp.server import (  # noqa: E402
    _dumps,
    _filter_workflow_for_put,
    _nodes_summary,
    n8n_get_workflow,
    n8n_list_workflows,
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
