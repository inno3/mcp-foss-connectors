"""Actions ouvertes : exclusion des événements système sur données fictives."""

import json
import os

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server


def _action(identifier, **extra):
    row = {
        "id": str(identifier), "label": f"Action fictive {identifier}",
        "type": "user", "code": "AC_OTH", "percentage": "0",
        "datep": 1793500000, "userownerid": "5", "userassigned": {},
        "authorid": "9",
    }
    row.update(extra)
    return row


def _pages(rows, calls=None):
    async def get(endpoint, params=None):
        assert endpoint == "agendaevents"
        params = params or {}
        if calls is not None:
            calls.append(dict(params))
        start = params.get("page", 0) * params.get("limit", 100)
        return rows[start:start + params.get("limit", 100)]

    return get


@pytest.mark.parametrize("fields", [
    {"type": "systemauto"},
    {"type": " SYSTEMAUTO "},
    {"type": "SystemAuto", "percentage": "50"},
    {"percent": -1},
    {"percent": "-1"},
    {"percent": "-1.0"},
    {"percentage": -1},
    {"percentage": "-1"},
    {"percentage": "-1.0"},
    {"percent": None, "percentage": "-1"},
    {"type": "systemauto", "percent": -1, "percentage": -1},
])
async def test_default_excludes_system_or_not_applicable_once(monkeypatch, fields):
    monkeypatch.setattr(server, "api_get", _pages([_action(1, **fields), _action(2)]))

    result = json.loads(await server.dolibarr_list_actions_open())

    assert [row["id"] for row in result["actions"]] == ["2"]
    assert result["total"] == result["count"] == result["returned"] == 1
    assert result["excluded_system"] == 1
    assert result["excluded_system_exact"] is True
    assert result["total_exact"] is True


@pytest.mark.parametrize("fields", [
    {"percent": 0, "percentage": -1},
    {"percent": "50", "percentage": -1},
    {"percent": "-1.5", "percentage": -1},
    {"percent": "", "percentage": -1},
    {"percent": "inconnu", "percentage": -1},
    {"type": None, "percentage": None},
    {"type": 4, "percentage": "0"},
])
async def test_existing_percent_precedence_and_unknown_progress_are_preserved(monkeypatch, fields):
    monkeypatch.setattr(server, "api_get", _pages([_action(1, **fields)]))

    result = json.loads(await server.dolibarr_list_actions_open())

    assert [row["id"] for row in result["actions"]] == ["1"]
    assert result["excluded_system"] == 0


async def test_include_system_restores_both_categories(monkeypatch):
    calls = []
    rows = [
        _action(1, type="systemauto", percentage="0"),
        _action(2, percentage="-1"),
        _action(3),
    ]
    monkeypatch.setattr(server, "api_get", _pages(rows, calls))

    result = json.loads(await server.dolibarr_list_actions_open(include_system=True))

    assert [row["id"] for row in result["actions"]] == ["1", "2", "3"]
    assert result["total"] == 3
    assert result["excluded_system"] == 0
    assert result["excluded_system_exact"] is True
    assert calls[0]["sqlfilters"] == "(t.percent:<>:100)"


async def test_exclusion_count_uses_assignment_scope_and_counts_each_action_once(monkeypatch):
    rows = [
        _action(1, type="systemauto"),
        _action(2, type="systemauto", userownerid="6"),
        _action(3, percentage="-1", userownerid="6", userassigned={"5": {"id": "5"}}),
        _action(4, percentage="-1", userownerid=None, authorid="5"),
        _action(5, percentage="-1", userownerid=None, authorid="6"),
        _action(6, userownerid="6", userassigned=[{"id": 5}]),
        _action(7, userownerid=None, authorid="5"),
        _action(8, type="systemauto", percentage="-1"),
    ]
    monkeypatch.setattr(server, "api_get", _pages(rows))

    result = json.loads(await server.dolibarr_list_actions_open(assigned_to=5))
    included = json.loads(await server.dolibarr_list_actions_open(assigned_to=5, include_system=True))

    assert [row["id"] for row in result["actions"]] == ["6", "7"]
    assert result["total"] == 2
    assert result["excluded_system"] == 4
    assert [row["id"] for row in included["actions"]] == ["1", "3", "4", "6", "7", "8"]
    assert included["total"] == result["total"] + result["excluded_system"]
    assert included["excluded_system"] == 0


async def test_filters_before_pagination_with_system_events_filling_the_first_api_page(monkeypatch):
    rows = [_action(i, type="systemauto") for i in range(1, 121)]
    rows += [_action(i) for i in range(121, 351)]
    monkeypatch.setattr(server, "api_get", _pages(rows))
    cursor = ""
    ids = []

    for expected in (100, 100, 30):
        result = json.loads(await server.dolibarr_list_actions_open(limit=100, cursor=cursor))
        assert result["total"] == 230
        assert result["count"] == result["returned"] == expected
        assert result["excluded_system"] == 120
        assert result["excluded_system_exact"] is True
        assert result["scan_truncated"] is False
        ids.extend(row["id"] for row in result["actions"])
        cursor = result["next_cursor"]

    assert cursor is None
    assert ids == [str(i) for i in range(121, 351)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("rows,excluded", [
    ([], 0),
    ([_action(1, type="systemauto"), _action(2, percentage="-1")], 2),
])
async def test_empty_result_has_exact_counters(monkeypatch, rows, excluded):
    monkeypatch.setattr(server, "api_get", _pages(rows))

    result = json.loads(await server.dolibarr_list_actions_open(limit=3))

    assert result["actions"] == []
    assert result["total"] == result["count"] == result["returned"] == 0
    assert result["excluded_system"] == excluded
    assert result["excluded_system_exact"] is True
    assert result["next_cursor"] is None
    assert result["truncated"] is False


async def test_scan_limit_exposes_partial_exclusion_count(monkeypatch):
    monkeypatch.setattr(server, "EXPORT_SCAN_MAX_PAGES", 1)
    rows = [_action(i, type="systemauto") for i in range(1, 91)]
    rows += [_action(i) for i in range(91, 111)]
    monkeypatch.setattr(server, "api_get", _pages(rows))

    result = json.loads(await server.dolibarr_list_actions_open(limit=100))

    assert result["total"] is None
    assert result["total_exact"] is False
    assert result["total_lower_bound"] == result["count"] == 10
    assert result["excluded_system"] == 90
    assert result["excluded_system_exact"] is False
    assert result["scan_truncated"] is True
    assert result["truncated"] is True


async def test_repeated_page_does_not_inflate_exclusions(monkeypatch):
    rows = [_action(i, type="systemauto") for i in range(1, 100)] + [_action(100)]

    async def repeat(endpoint, params=None):
        return rows

    monkeypatch.setattr(server, "api_get", repeat)

    result = json.loads(await server.dolibarr_list_actions_open(limit=3))

    assert result["total"] is None
    assert result["total_lower_bound"] == 1
    assert result["excluded_system"] == 99
    assert result["excluded_system_exact"] is False


@pytest.mark.parametrize("kwargs,expected", [
    ({"date_from": "2026-11-01"}, ["(t.datep:>=:'2026-11-01 00:00:00')"]),
    ({"date_to": "2026-11-02"}, ["(t.datep:<=:'2026-11-02 23:59:59')"]),
    ({"date_from": "2026-11-01", "date_to": "2026-11-02"}, [
        "(t.datep:>=:'2026-11-01 00:00:00')", "(t.datep:<=:'2026-11-02 23:59:59')",
    ]),
    ({"date_from": "2024-02-29", "date_to": "2024-02-29"}, [
        "(t.datep:>=:'2024-02-29 00:00:00')", "(t.datep:<=:'2024-02-29 23:59:59')",
    ]),
])
async def test_dates_filter_the_api_for_inclusive_whole_days(monkeypatch, kwargs, expected):
    calls = []
    monkeypatch.setattr(server, "api_get", _pages([], calls))

    result = json.loads(await server.dolibarr_list_actions_open(
        project_id=12, thirdparty_id=34, assigned_to=5, **kwargs,
    ))

    assert result["total"] == 0
    assert len(calls) == 1
    filters = calls[0]["sqlfilters"]
    assert "(t.percent:<>:100)" in filters
    assert "(t.fk_project:=:12)" in filters
    assert "(t.fk_soc:=:34)" in filters
    assert all(clause in filters for clause in expected)
    assert "user_ids" not in calls[0]
    assert calls[0]["sortfield"] == "t.datep,t.id"


@pytest.mark.parametrize("kwargs", [
    {"date_from": "2026-02-29"},
    {"date_to": "2026-11-31"},
    {"date_from": "2026-1-01"},
    {"date_to": "2026-11-2"},
    {"date_from": " 2026-11-01"},
    {"date_to": "2026-11-02 "},
    {"date_from": "2026-11-01T00:00:00"},
    {"date_to": "2026-11-02' OR 1=1"},
    {"date_from": "2026-11-03", "date_to": "2026-11-02"},
])
async def test_invalid_dates_fail_before_any_request(monkeypatch, kwargs):
    calls = []
    monkeypatch.setattr(server, "api_get", _pages([], calls))

    result = await server.dolibarr_list_actions_open(**kwargs)

    assert result.startswith("Erreur"), result
    assert calls == []


async def test_http_failure_is_never_recast_as_an_empty_success(monkeypatch):
    async def down(endpoint, params=None):
        request = httpx.Request("GET", "https://erp.example.test/api/index.php/agendaevents")
        response = httpx.Response(503, request=request, text="Service unavailable")
        response.raise_for_status()

    monkeypatch.setattr(server, "api_get", down)

    result = await server.dolibarr_list_actions_open(date_from="2026-11-01")

    assert result.startswith("Erreur HTTP 503")
    assert "excluded_system" not in result
