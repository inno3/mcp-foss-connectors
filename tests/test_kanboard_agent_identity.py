"""Contrat d'identite, exerce au niveau HTTP avec des fixtures synthetiques."""

import asyncio
import base64
import json

import httpx
import pytest

from kanboard_mcp import server as kb


class Rpc:
    def __init__(self):
        self.calls = []
        self.identities = {
            "human": {"id": "4", "username": "human"},
            "robot": {"id": "84", "username": "robot"},
        }
        self.author = 84
        self.read_error = False

    async def post(self, url, *, json, headers):
        user = base64.b64decode(headers["Authorization"].split()[1]).decode().split(":")[0]
        method = json["method"]
        params = json.get("params", {})
        self.calls.append((user, method, dict(params)))
        await asyncio.sleep(0)
        if method == "getMe":
            result = self.identities[user]
            if isinstance(result, Exception):
                raise result
        elif method == "getComment":
            result = None if self.read_error else {"id": 901, "user_id": str(self.author)}
        elif method == "getTask":
            result = None if self.read_error else {
                "id": "902", "title": "Temoin", "project_id": "262",
                "creator_id": str(self.author), "column_id": "1", "is_active": "1",
            }
        elif method in {"getAllTasks", "getAllProjects", "getMyOverdueTasks", "getOverdueTasks"}:
            result = []
        else:
            result = 902
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"result": result})

    @property
    def writes(self):
        return [c for c in self.calls if not c[1].startswith(("get", "search"))]


@pytest.fixture
def rpc(monkeypatch):
    server = Rpc()
    for key, value in {
        "KANBOARD_URL": "https://kanboard.invalid/jsonrpc.php",
        "KANBOARD_USER": "human", "KANBOARD_TOKEN": "synthetic-primary",
        "KANBOARD_USER_ALT": "robot", "KANBOARD_TOKEN_ALT": "synthetic-agent",
        "KANBOARD_DEFAULT_AS_USER": "primary", "_RETRY_DELAY": 0,
    }.items():
        monkeypatch.setattr(kb, key, value)
    for name in ("_identity_cache", "_my_id_cache", "_my_name_cache"):
        if hasattr(kb, name):
            monkeypatch.setattr(kb, name, {})
    if hasattr(kb, "_identity_lock"):
        monkeypatch.setattr(kb, "_identity_lock", asyncio.Lock())

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        post = server.post

    monkeypatch.setattr(kb.httpx, "AsyncClient", Client)
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["KANBOARD_USER_ALT", "KANBOARD_TOKEN_ALT", "both"])
@pytest.mark.parametrize("account", ["agent", "bot", "default"])
async def test_missing_agent_never_writes(rpc, monkeypatch, missing, account):
    monkeypatch.setattr(kb, "KANBOARD_DEFAULT_AS_USER", "agent")
    for field in ("KANBOARD_USER_ALT", "KANBOARD_TOKEN_ALT"):
        if missing in (field, "both"):
            monkeypatch.setattr(kb, field, "")
    with pytest.raises(kb.KanboardIdentityError, match="(?i)(agent|identit)"):
        await kb.kb_call("createTask", {"title": "Temoin"}, as_user=account)
    assert rpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [
    {"id": "4", "username": "human"},
    {"id": "4", "username": "robot"},
    {"id": "85", "username": "stranger"},
    None, False, {}, {"id": "bad", "username": "robot"},
    {"id": 0, "username": "robot"}, {"id": True, "username": "robot"},
    RuntimeError("getMe unavailable"),
])
async def test_unestablished_identity_never_writes(rpc, identity):
    rpc.identities["robot"] = identity
    with pytest.raises(kb.KanboardIdentityError, match="(?i)(agent|identit)"):
        await kb.kb_call("removeTask", {"task_id": 902}, as_user="agent")
    assert rpc.writes == []


@pytest.mark.asyncio
async def test_same_configured_login_never_writes(rpc, monkeypatch):
    monkeypatch.setattr(kb, "KANBOARD_USER_ALT", "human")
    with pytest.raises(kb.KanboardIdentityError, match="(?i)(agent|identit)"):
        await kb.kb_call("closeTask", {"task_id": 902}, as_user="agent")
    assert rpc.writes == []


@pytest.mark.asyncio
async def test_first_concurrent_writes_share_verified_identity(rpc):
    await asyncio.gather(*[
        kb.kb_call("updateTask", {"id": i}, as_user="agent") for i in (901, 902)
    ])
    assert len(rpc.writes) == 2
    assert all(c[0] == "robot" for c in rpc.writes)
    assert [(u, m) for u, m, _ in rpc.calls if m == "getMe"] == [
        ("robot", "getMe"), ("human", "getMe"),
    ]
    assert all(m == "getMe" for _, m, _ in rpc.calls[:2])


@pytest.mark.asyncio
async def test_primary_is_explicitly_allowed_and_verified(rpc):
    await kb.kb_call("closeTask", {"task_id": 902}, as_user="primary")
    assert [(u, m) for u, m, _ in rpc.calls] == [("human", "getMe"), ("human", "closeTask")]


@pytest.mark.asyncio
@pytest.mark.parametrize("account,user", [("primary", "human"), ("agent", "robot")])
async def test_display_name_decodes_once_after_write_identity_probe(rpc, account, user):
    """La garde 0.17.1 alimente aussi le cache de noms decode en 0.17.2."""
    rpc.identities[user]["name"] = "Synthetic &amp;amp; Name"
    await kb.kb_call("closeTask", {"task_id": 902}, as_user=account)
    for _ in range(2):
        accounts = json.loads(await kb.kanboard_list_accounts())["accounts"]
        assert accounts[account]["resolved_name"] == "Synthetic &amp; Name"
    assert [(u, m) for u, m, _ in rpc.calls if u == user and m == "getMe"] == [(user, "getMe")]


@pytest.mark.asyncio
async def test_read_fallback_is_available_and_reports_primary(rpc, monkeypatch):
    monkeypatch.setattr(kb, "KANBOARD_TOKEN_ALT", "")
    response = json.loads(await kb.kanboard_my_overdue(as_user="agent"))
    assert response["as_user"] == "primary"
    assert rpc.writes == []
    assert all(user == "human" for user, _, _ in rpc.calls)


@pytest.mark.asyncio
async def test_plain_read_does_not_require_getme(rpc):
    rpc.identities["robot"] = RuntimeError("not available")
    assert await kb.kb_call("getAllTasks", as_user="agent") == []
    assert [m for _, m, _ in rpc.calls] == ["getAllTasks"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["task", "comment"])
@pytest.mark.parametrize("recorded", [84, 4, None])
async def test_author_is_read_back_not_inferred(rpc, kind, recorded):
    rpc.author = recorded
    rpc.read_error = recorded is None
    if kind == "task":
        data = json.loads(await kb.kanboard_create_task(project_id=262, title="Temoin", as_user="agent"))
        method, field = "createTask", "creator_id"
    else:
        # Un ancien cache de resolution ne constitue pas une preuve d'identite.
        kb._my_id_cache["agent"] = 4
        data = json.loads(await kb.kanboard_add_comment(task_id=902, comment="Temoin", as_user="agent"))
        method, field = "createComment", "user_id"
    assert data["success"] is True
    assert data["as_user"] == "agent"
    assert data["author_user_id"] == recorded
    assert data["author_verified"] is (recorded == 84)
    if recorded != 84:
        assert data["author_warning"]
    assert next(p for _, m, p in rpc.writes if m == method)[field] == 84


@pytest.mark.asyncio
async def test_no_successful_comment_claim_when_agent_missing(rpc, monkeypatch):
    monkeypatch.setattr(kb, "KANBOARD_TOKEN_ALT", "")
    out = await kb.kanboard_add_comment(task_id=902, comment="Temoin", as_user="agent")
    assert out.startswith("Refus"), out
    assert '"success": true' not in out.lower()
    assert rpc.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("kanboard_create_project", {"name": "Temoin"}),
    ("kanboard_create_column", {"project_id": 262, "title": "Temoin"}),
    ("kanboard_create_swimlane", {"project_id": 262, "name": "Temoin"}),
    ("kanboard_create_category", {"project_id": 262, "name": "Temoin"}),
    ("kanboard_create_subtask", {"task_id": 902, "title": "Temoin", "user_id": 4}),
    ("kanboard_create_task_link", {"task_id": 902, "opposite_task_id": 903, "link_id": 1}),
])
async def test_objects_without_author_do_not_invent_one(rpc, tool, args):
    data = json.loads(await getattr(kb, tool)(**args, as_user="agent"))
    assert data["success"] is True
    assert data["as_user"] == "agent"
    assert data["author_user_id"] is None
    assert data["author_verification_method"] == "not_exposed"
