"""Actions ouvertes : filtre côté API et budget de temps, sur données fictives.

Constaté en réel le 06/11/2026 en 1.8.15 : sans filtre, l'appel dépassait le délai
du client. `percent<>100` gardait environ 8 700 événements à -1, relus un par un
par la route, et le balayage continuait après l'abandon du client.
"""

import asyncio
import json
import math
import os
import time

import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server

NOT_APPLICABLE = ("(t.percent:>=:0)", "(t.percent:<:100)")


def _action(identifier, **extra):
    row = {
        "id": str(identifier), "label": f"Action fictive {identifier}",
        "type": "user", "code": "AC_OTH", "percentage": "0",
        "datep": 1793500000, "userownerid": "5", "userassigned": {},
    }
    row.update(extra)
    return row


def _pages(rows, calls, delay=0.0):
    async def get(endpoint, params=None):
        params = params or {}
        calls.append(dict(params))
        if delay:
            await asyncio.sleep(delay)
        start = params.get("page", 0) * params.get("limit", 100)
        return rows[start:start + params.get("limit", 100)]

    return get


async def test_default_leaves_not_applicable_events_to_the_api(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "api_get", _pages([_action(1)], calls))

    result = json.loads(await server.dolibarr_list_actions_open(project_id=12))

    filters = calls[0]["sqlfilters"]
    assert all(clause in filters for clause in NOT_APPLICABLE)
    assert "(t.percent:<>:100)" not in filters
    assert "(t.fk_project:=:12)" in filters
    assert [row["id"] for row in result["actions"]] == ["1"]


async def test_default_counter_is_announced_as_partial(monkeypatch):
    rows = [_action(1, type="systemauto"), _action(2)]
    monkeypatch.setattr(server, "api_get", _pages(rows, []))

    result = json.loads(await server.dolibarr_list_actions_open())

    assert result["excluded_system"] == 1
    assert result["excluded_system_exact"] is False
    assert "-1" in result["excluded_system_note"]
    assert result["total"] == 1 and result["total_exact"] is True


async def test_include_system_keeps_the_full_scope_and_an_exact_counter(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "api_get", _pages([_action(1, percentage="-1")], calls))

    result = json.loads(await server.dolibarr_list_actions_open(include_system=True))

    assert calls[0]["sqlfilters"] == "(t.percent:<>:100)"
    assert result["excluded_system"] == 0
    assert result["excluded_system_exact"] is True
    assert "excluded_system_note" not in result


async def test_budget_stops_between_pages_without_a_later_request(monkeypatch):
    calls = []
    rows = [_action(i) for i in range(1, 2001)]
    monkeypatch.setattr(server, "api_get", _pages(rows, calls, delay=0.05))

    started = time.monotonic()
    result = json.loads(await server.dolibarr_list_actions_open(limit=5, time_budget=0.12))
    elapsed = time.monotonic() - started
    sent = len(calls)
    await asyncio.sleep(0.15)

    assert elapsed < 0.5
    assert 1 <= sent < 20
    assert len(calls) == sent
    assert result["scan_truncated"] is True
    assert result["total"] is None and result["total_exact"] is False
    assert result["total_lower_bound"] >= 100
    assert result["count"] == 5
    assert "budget" in result["scan_note"].lower()
    assert result["time_budget"] == 0.12
    assert result["elapsed_seconds"] < 0.5


async def test_budget_cancels_a_page_that_never_answers(monkeypatch):
    cancelled = []

    async def hung(endpoint, params=None):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(params.get("page"))
            raise
        return []

    monkeypatch.setattr(server, "api_get", hung)

    started = time.monotonic()
    result = json.loads(await server.dolibarr_list_actions_open(time_budget=0.1))

    assert time.monotonic() - started < 1
    assert cancelled == [0]
    assert result["actions"] == []
    assert result["total"] is None
    assert result["total_lower_bound"] == 0
    assert result["scan_truncated"] is True
    assert "budget" in result["scan_note"].lower()


async def test_budget_is_capped_and_reported(monkeypatch):
    monkeypatch.setattr(server, "api_get", _pages([], []))

    result = json.loads(await server.dolibarr_list_actions_open(time_budget=600))

    assert result["time_budget"] == 25.0
    assert result["scan_truncated"] is False
    assert result["elapsed_seconds"] >= 0


@pytest.mark.parametrize("budget", [0, -1, math.nan, math.inf])
async def test_invalid_budget_fails_before_any_request(monkeypatch, budget):
    calls = []
    monkeypatch.setattr(server, "api_get", _pages([], calls))

    result = await server.dolibarr_list_actions_open(time_budget=budget)

    assert result.startswith("Erreur"), result
    assert calls == []


async def test_other_scans_keep_no_budget(monkeypatch):
    calls = []
    rows = [{"id": i} for i in range(1, 251)]
    fetch = _pages(rows, calls, delay=0.01)

    scanned = await server._scan_list("projects", {}, fetch=lambda e, p: fetch(e, p))

    assert scanned.complete is True
    assert len(scanned) == 250
