"""Tests unitaires offline pour le connecteur GitLab.

Tous les appels HTTP sont mockés : la suite tourne sans réseau ni token réel.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("GITLAB_URL", "https://gitlab.example.test")
os.environ.setdefault("GITLAB_TOKEN", "glpat-xxxxxxxxxxxx")

from gitlab_mcp.server import (  # noqa: E402
    _dumps,
    _encode_project_id,
    _user_summary,
    gitlab_get_project,
    gitlab_list_projects,
)


class TestHelpers:
    def test_encode_project_id_numeric_passthrough(self) -> None:
        assert _encode_project_id(42) == "42"
        assert _encode_project_id("42") == "42"

    def test_encode_project_id_path_is_url_encoded(self) -> None:
        assert _encode_project_id("mygroup/myproject") == "mygroup%2Fmyproject"
        assert _encode_project_id("a/b/c") == "a%2Fb%2Fc"

    def test_user_summary_none(self) -> None:
        assert _user_summary(None) is None
        assert _user_summary({}) is None

    def test_user_summary_filters_fields(self) -> None:
        u = {"id": 5, "username": "alice", "name": "Alice", "secret": "x"}
        assert _user_summary(u) == {"id": 5, "username": "alice", "name": "Alice"}

    def test_dumps_is_compact_utf8(self) -> None:
        assert _dumps({"a": 1, "é": "ç"}) == '{"a":1,"é":"ç"}'


@pytest.mark.asyncio
class TestListProjects:
    async def test_filters_and_paginates(self) -> None:
        payload = [
            {
                "id": 1,
                "name": "Proj",
                "path_with_namespace": "grp/proj",
                "description": None,
                "web_url": "https://gitlab.example.test/grp/proj",
                "last_activity_at": "2026-01-01T00:00:00Z",
                "extra": "dropped",
            }
        ]
        with patch(
            "gitlab_mcp.server.api_get", new_callable=AsyncMock, return_value=payload
        ) as mock_get:
            raw = await gitlab_list_projects(search="proj", page=2)
        endpoint, kwargs = mock_get.call_args[0][0], mock_get.call_args[1]
        assert endpoint == "projects"
        assert kwargs["params"]["membership"] == "true"
        assert kwargs["params"]["page"] == 2
        assert kwargs["params"]["search"] == "proj"
        data = json.loads(raw)
        assert data["count"] == 1
        assert data["page"] == 2
        assert data["projects"][0]["description"] == ""
        assert "extra" not in data["projects"][0]

    async def test_error_is_formatted(self) -> None:
        with patch(
            "gitlab_mcp.server.api_get",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            raw = await gitlab_list_projects()
        assert "error" in json.loads(raw)


@pytest.mark.asyncio
class TestGetProject:
    async def test_encodes_path_and_extracts_license(self) -> None:
        payload = {
            "id": 7,
            "name": "P",
            "path_with_namespace": "grp/p",
            "namespace": {"id": 2, "name": "grp", "kind": "group"},
            "license": {"key": "mit"},
        }
        with patch(
            "gitlab_mcp.server.api_get", new_callable=AsyncMock, return_value=payload
        ) as mock_get:
            raw = await gitlab_get_project("grp/p")
        assert mock_get.call_args[0][0] == "projects/grp%2Fp"
        data = json.loads(raw)
        assert data["license"] == "mit"
        assert data["namespace"] == {"id": 2, "name": "grp", "kind": "group"}
