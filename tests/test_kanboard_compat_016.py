"""Neuf contrats 0.16 conserves par la 0.17.1, sans donnees ni HTTP reels.

Repris de mcp-inno3/main (fa18893), auparavant sous docs/validation.
Ils ciblent maintenant le source canonique et sa collecte pytest ordinaire.
"""

import asyncio
import base64
import json

import httpx
import pytest

from kanboard_mcp import server as kb


class Rpc:
    def __init__(self):
        self.identities = {
            'human': {'id': '4', 'username': 'human', 'name': 'Synthetic Primary'},
            'robot': {'id': '84', 'username': 'robot', 'name': 'Synthetic Agent'},
        }
        self.author = 84
        self.read_error = False
        self.calls = []

    async def post(self, url, *, json, headers):
        user = base64.b64decode(headers['Authorization'].split()[1]).decode().split(':')[0]
        method = json['method']
        self.calls.append((user, method))
        if method == 'getMe':
            result = self.identities[user]
        elif method in {'getComment', 'getTask'}:
            if self.read_error:
                raise RuntimeError('Synthetic author read failure')
            result = {
                'id': '902', 'user_id': str(self.author), 'creator_id': str(self.author),
                'title': 'Synthetic', 'project_id': '42', 'column_id': '1',
            }
        else:
            result = 902
        return httpx.Response(200, request=httpx.Request('POST', url), json={'result': result})


@pytest.fixture
def rpc(monkeypatch):
    fake = Rpc()
    for key, value in {
        'KANBOARD_URL': 'https://kanboard.example.invalid/jsonrpc.php',
        'KANBOARD_USER': 'human', 'KANBOARD_TOKEN': 'synthetic-primary',
        'KANBOARD_USER_ALT': 'robot', 'KANBOARD_TOKEN_ALT': 'synthetic-agent',
        'KANBOARD_DEFAULT_AS_USER': 'primary', '_RETRY_DELAY': 0,
    }.items():
        monkeypatch.setattr(kb, key, value)
    for name in ('_identity_cache', '_my_id_cache', '_my_name_cache'):
        monkeypatch.setattr(kb, name, {})
    if hasattr(kb, '_identity_lock'):
        monkeypatch.setattr(kb, '_identity_lock', asyncio.Lock())

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        post = fake.post

    monkeypatch.setattr(kb.httpx, 'AsyncClient', Client)
    return fake


async def create(kind):
    if kind == 'task':
        return json.loads(await kb.kanboard_create_task(project_id=42, title='Synthetic', as_user='agent'))
    return json.loads(await kb.kanboard_add_comment(task_id=902, comment='Synthetic', as_user='agent'))


@pytest.mark.asyncio
async def test_task_preserves_creator_id(rpc):
    assert (await create('task'))['creator_id'] == 84


@pytest.mark.asyncio
async def test_comment_preserves_recorded_author_alias(rpc):
    assert (await create('comment'))['author_user_id_recorded'] == 84


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['task', 'comment'])
async def test_author_mismatch_preserves_legacy_field(rpc, kind):
    rpc.author = 4
    assert (await create(kind))['author_mismatch']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['task', 'comment'])
async def test_author_read_failure_preserves_cause(rpc, kind):
    rpc.read_error = True
    assert 'Synthetic author read failure' in (await create(kind))['author_read_error']


@pytest.mark.asyncio
async def test_display_name_survives_first_write(rpc):
    await kb.kb_call('closeTask', {'task_id': 902}, as_user='agent')
    data = json.loads(await kb.kanboard_list_accounts())
    assert data['accounts']['agent']['resolved_name'] == 'Synthetic Agent'


@pytest.mark.asyncio
async def test_primary_without_getme_keeps_016_behavior(rpc):
    rpc.identities['human'] = None
    assert await kb.kb_call('closeTask', {'task_id': 902}, as_user='primary') == 902


@pytest.mark.asyncio
async def test_missing_agent_keeps_typed_identity_error(rpc, monkeypatch):
    monkeypatch.setattr(kb, 'KANBOARD_TOKEN_ALT', '')
    with pytest.raises(kb.KanboardIdentityError):
        await kb.kb_call('closeTask', {'task_id': 902}, as_user='agent')
    assert rpc.calls == []
