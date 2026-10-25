import asyncio
import base64
import html
import io
import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.loboo import client, cloud, config, credentials, hooks, identity, model_logos, pages, web_search
from open_webui.loboo import router as sso_router
from open_webui.loboo.router import safe_next
from open_webui.routers import images
from open_webui.utils.headers import get_headers_and_cookies
from PIL import Image
from starlette.requests import Request


@pytest.mark.parametrize(
    'value, expected',
    [
        ('/', '/'),
        ('/c/abc?model=gpt-5', '/c/abc?model=gpt-5'),
        ('', '/'),
        ('//evil.example', '/'),
        ('/\\evil.example', '/'),
        ('/\t/evil.example', '/'),
        ('/%2F%2Fevil.example', '/'),
        ('/%5Cevil.example', '/'),
        ('https://evil.example/', '/'),
        ('c/abc', '/'),
        ('/api/v1/loboo/sso?ticket=x', '/'),
        ('/' + 'a' * 2048, '/'),
    ],
)
def test_safe_next_only_allows_site_paths(value, expected):
    assert safe_next(value) == expected


def test_pages_escape_dynamic_text():
    hostile = '/</script><script>alert(1)</script> '
    page = pages.bridge('tok"en', hostile)
    body = page.body.decode()
    assert '<script>alert(1)' not in body
    data = re.search(r'<script type="application/json" id="loboo-sso">(.*?)</script>', body).group(1)
    assert json.loads(data) == {'token': 'tok"en', 'next': hostile}
    nonce = re.search(r"script-src 'nonce-([^']+)'", page.headers['content-security-policy']).group(1)
    assert f'<script nonce="{nonce}">' in body
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['referrer-policy'] == 'no-referrer'

    confirm = pages.confirm('<img src=x onerror=alert(1)>', 'a@loboo.ai', 'g"rant', '/"><b>').body.decode()
    assert '<img' not in confirm and '"><b>' not in confirm
    assert 'value="g&quot;rant"' in confirm


@pytest.mark.asyncio
async def test_loboo_cloud_gives_loboo_cli_the_member_key_only_in_per_user_terminals(monkeypatch):
    from open_webui.models.config import Config

    monkeypatch.setattr(config, 'ENABLED', True)
    connections = [
        {
            'id': 'cloud',
            'url': 'http://orchestrator',
            'key': 'k',
            'auth_type': 'bearer',
            'server_type': 'orchestrator',
            'policy_id': 'loboo',
        },
        {'id': 'shared', 'url': 'http://open-terminal', 'key': 'k', 'auth_type': 'bearer'},
    ]

    async def get(key, default=None):
        return connections if key == 'terminal_server.connections' else default

    monkeypatch.setattr(Config, 'get', staticmethod(get))

    async def key_for(user):
        if user.id == 'no-key':
            raise HTTPException(status_code=403, detail='Codex 未开通')
        return f'key-of-{user.id}'

    monkeypatch.setattr(credentials, 'key_for', key_for)
    writes = []

    async def write_file(url, headers, cookies, path, content, **kwargs):
        writes.append((url, headers['X-User-Id'], path, json.loads(content)))

    monkeypatch.setattr(cloud, 'write_file', write_file)
    monkeypatch.setattr(cloud, '_written', {})
    request = SimpleNamespace(cookies={}, state=SimpleNamespace())
    user = SimpleNamespace(id='u1', name='n', email='e', role='user')
    keyless = SimpleNamespace(id='no-key', name='n', email='e', role='user')

    # 每人一个容器的终端：Key 写进本人 home，loboo-cli 用它换会话；缓存期内同一个 Key 不重复写。
    await hooks.on_terminal_tools(request, user, 'cloud', {'chat_id': 'c1'})
    await hooks.on_terminal_tools(request, user, 'cloud', {'chat_id': 'c2'})
    assert writes == [
        (
            'http://orchestrator/p/loboo/files/write',
            'u1',
            '/home/user/.codex/auth.json',
            {'OPENAI_API_KEY': 'key-of-u1'},
        )
    ]
    # 可能多人共用的普通终端、没有 Key 的人：都不写。
    await hooks.on_terminal_tools(request, user, 'shared', {})
    await hooks.on_terminal_tools(request, keyless, 'cloud', {})
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_loboo_connection_sends_only_the_member_key(monkeypatch):
    monkeypatch.setattr(config, 'ENABLED', True)
    monkeypatch.setattr(config, 'LOBOO_CPA_BASE_URL', 'https://cpa.example/v1')

    async def key_for(user):
        if user.id == 'no-key':
            raise HTTPException(status_code=403, detail='Codex 未开通')
        return f'key-of-{user.id}'

    monkeypatch.setattr(credentials, 'key_for', key_for)
    monkeypatch.setattr(credentials, 'listing_key', lambda: 'borrowed')
    request = SimpleNamespace(cookies={'token': 'session-jwt'}, state=SimpleNamespace())
    user = SimpleNamespace(id='u1', name='n', email='e', role='user')
    keyless = SimpleNamespace(id='no-key', name='n', email='e', role='user')
    connection = {
        'auth_type': 'loboo',
        'forward_cookies': True,
        'headers': {'authorization': 'Bearer custom', 'X-Extra': 'kept'},
    }

    async def authorization(url, who):
        headers, cookies = await get_headers_and_cookies(request, url, 'shared', connection, user=who)
        assert cookies == {} and headers['X-Extra'] == 'kept'
        values = [v for k, v in headers.items() if k.lower() == 'authorization']
        assert len(values) <= 1
        return values[0] if values else None

    # 对话用本人的 Key，自定义头覆盖不了，cookie 不转发；地址大小写、末尾的 / 不影响。
    assert await authorization('https://CPA.example/v1/', user) == 'Bearer key-of-u1'
    # 拉模型列表：本人没有 Key、或没有用户（启动时预取）时借用别人的，不报错。
    assert await authorization('https://cpa.example/v1/models', user) == 'Bearer key-of-u1'
    assert await authorization('https://cpa.example/v1/models', keyless) == 'Bearer borrowed'
    assert await authorization('https://cpa.example/v1/models', None) == 'Bearer borrowed'
    # 其余请求没有用户时不带 Key；没有 Key 的人对话时报错，文案进对话气泡。
    assert await authorization('https://cpa.example/v1', None) is None
    with pytest.raises(HTTPException) as raised:
        await authorization('https://cpa.example/v1', keyless)
    assert raised.value.status_code == 403
    # 只发往配置的地址及其下的路径。
    for url in ('https://other.example/v1', 'https://cpa.example/v10/models', 'http://cpa.example/v1'):
        with pytest.raises(HTTPException) as raised:
            await authorization(url, user)
        assert raised.value.status_code == 500


@pytest.mark.asyncio
async def test_loboo_images_use_the_member_key(monkeypatch):
    monkeypatch.setattr(config, 'ENABLED', True)
    monkeypatch.setattr(config, 'LOBOO_CPA_BASE_URL', 'https://cpa.example/v1')

    async def key_for(user):
        if user.id == 'no-key':
            raise HTTPException(status_code=403, detail='Codex 未开通')
        return f'key-of-{user.id}'

    monkeypatch.setattr(credentials, 'key_for', key_for)
    user = SimpleNamespace(id='u1', name='n', email='e', role='user')
    keyless = SimpleNamespace(id='no-key', name='n', email='e', role='user')

    async def refusal(call, who):
        with pytest.raises(HTTPException) as raised:
            await call(who)
        return raised.value.status_code, raised.value.detail

    # CPA 地址用本人的 Key（大小写、末尾的 / 不影响）；别的图片后台返回 None，照旧用后台填的 Key。
    assert await hooks.image_key('https://CPA.example/v1/', user) == 'key-of-u1'
    assert await hooks.image_key('https://other.example/v1', user) is None
    assert await hooks.image_key('https://cpa.example/v10', user) is None
    assert (await refusal(lambda who: hooks.image_key('https://cpa.example/v1', who), None))[0] == 401

    image_config = SimpleNamespace(
        IMAGE_GENERATION_ENGINE='openai',
        IMAGE_GENERATION_MODEL='gpt-image-2',
        IMAGE_SIZE='',
        IMAGES_OPENAI_API_BASE_URL='https://cpa.example/v1',
        IMAGES_OPENAI_API_KEY='admin-key',
        IMAGES_OPENAI_API_VERSION='',
        IMAGES_OPENAI_API_PARAMS=None,
        IMAGE_EDIT_ENGINE='openai',
        IMAGE_EDIT_MODEL='gpt-image-2',
        IMAGE_EDIT_SIZE='',
        IMAGES_EDIT_OPENAI_API_BASE_URL='https://cpa.example/v1',
        IMAGES_EDIT_OPENAI_API_KEY='admin-key',
        IMAGES_EDIT_OPENAI_API_VERSION='',
    )
    sent = []

    class Session:
        def post(self, url, headers=None, **kwargs):
            sent.append((url, headers['Authorization']))
            raise RuntimeError('stop before the network')

    async def get_image_config():
        return image_config

    async def get_session():
        return Session()

    monkeypatch.setattr(images, 'get_image_config', get_image_config)
    monkeypatch.setattr(images, 'get_session', get_session)
    png = io.BytesIO()
    Image.new('RGB', (1, 1)).save(png, 'PNG')
    picture = 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode()

    async def generate(who):
        return await images.image_generations(None, images.CreateImageForm(prompt='a cat'), user=who)

    async def edit(who):
        return await images.image_edits(None, images.EditImageForm(prompt='a hat', image=picture), user=who)

    # 生成和编辑都带本人的 Key，后台填的那把不用（假会话在发出前停下，上游代码把它报成 400）。
    assert (await refusal(generate, user))[0] == 400
    assert (await refusal(edit, user))[0] == 400
    assert sent == [
        ('https://cpa.example/v1/images/generations', 'Bearer key-of-u1'),
        ('https://cpa.example/v1/images/edits', 'Bearer key-of-u1'),
    ]
    # 没有 Key 的人原样拿到原因（会显示在对话里），不被包成 400，也不发请求。
    assert await refusal(generate, keyless) == (403, 'Codex 未开通')
    assert await refusal(edit, keyless) == (403, 'Codex 未开通')
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_loboo_sso_redeems_only_once_the_page_runs(monkeypatch):
    monkeypatch.setattr(config, 'ENABLED', True)
    monkeypatch.setattr(config, 'LOBOO_TRUSTED_ORIGINS', frozenset({'https://service.loboo-ai.com'}))
    monkeypatch.setattr(config, 'LOBOO_PORTAL_URL', 'https://service.loboo-ai.com/account/')
    redeemed = []

    async def redeem(ticket):
        redeemed.append(ticket)
        return client.Profile('lb-1', '成员', None, 'inactive', None, None)

    async def sso_user(loboo_user_id, browser_login, db=None):
        return SimpleNamespace(id='u1', role='user')

    async def sign_in(request, user, next_path, db):
        return f'signed in, next {next_path}'

    monkeypatch.setattr(client, 'redeem', redeem)
    monkeypatch.setattr(identity, 'sso_user', sso_user)
    monkeypatch.setattr(credentials, 'remember', lambda user_id, profile: None)
    monkeypatch.setattr(sso_router, '_sign_in', sign_in)

    def request(method, **headers):
        raw = [(k.encode(), v.encode()) for k, v in {'host': 'm.loboo-ai.com', **headers}.items()]
        return Request({'type': 'http', 'method': method, 'headers': raw})

    def field(page, name):
        return html.unescape(re.search(rf'name="{name}" value="([^"]*)"', page.body.decode()).group(1))

    # 打开地址只回提交页、不兑换（WebView2 拦下跳转前的那次请求用不掉票据）；从萝卜页面来的带上签名的来路。
    from_loboo = await sso_router.sso(request('GET', referer='https://service.loboo-ai.com/'), 't1', '/c/1')
    from_elsewhere = await sso_router.sso(request('GET'), 't2', '/')
    basis = field(from_loboo, 'basis')
    assert redeemed == [] and (field(from_loboo, 'ticket'), field(from_loboo, 'next')) == ('t1', '/c/1')
    assert basis.startswith('loboo page|') and field(from_elsewhere, 'basis') == ''

    # 本站提交页发回来才兑换；签名的来路只对它绑定的票据有效，换一张票据就要确认。
    same_site = request('POST', origin='https://m.loboo-ai.com')
    assert await sso_router.sso_redeem(same_site, 't1', '/c/1', basis, None) == 'signed in, next /c/1'
    confirm = await sso_router.sso_redeem(same_site, 't3', '/', basis, None)
    assert '确认登录' in confirm.body.decode() and redeemed == ['t1', 't3']

    # 别的网站替你提交的不兑换；浏览器里的出错页带成员回萝卜重新进入。
    cross_site = await sso_router.sso_redeem(request('POST', origin='https://evil.example'), 't4', '/', basis, None)
    assert cross_site.status_code == 403 and redeemed == ['t1', 't3']
    assert 'href="https://service.loboo-ai.com/account/">回萝卜重新进入' in cross_site.body.decode()


def test_model_logo_follows_the_vendor_the_connection_reports():
    registry = {
        'claude-opus-5.5': {'owned_by': 'openai', 'openai': {'id': 'claude-opus-5.5', 'owned_by': 'anthropic'}},
        'gpt-6.1-sol': {'owned_by': 'openai', 'openai': {'id': 'gpt-6.1-sol', 'owned_by': 'openai'}},
        'my-helper': {'owned_by': 'openai', 'info': {'base_model_id': 'claude-opus-5.5'}},
        'mystery': {'owned_by': 'openai', 'openai': {'id': 'mystery', 'owned_by': 'acme'}},
    }
    assert model_logos.logo_url(registry, 'claude-opus-5.5') == '/static/models/anthropic.svg'
    assert model_logos.logo_url(registry, 'gpt-6.1-sol') == '/static/models/openai.svg'
    assert model_logos.logo_url(registry, 'my-helper') == '/static/models/anthropic.svg'
    assert model_logos.logo_url(registry, 'mystery') == model_logos.UNKNOWN
    assert model_logos.logo_url(registry, 'gone') == model_logos.UNKNOWN


class FakeCpa:
    """假的 CPA 会话：按顺序给出答复（状态码与 JSON、要抛出的异常，或一直不回的 HANG），记下发出的请求。"""

    HANG = object()

    def __init__(self):
        self.replies, self.sent = [], []

    def post(self, url, json=None, headers=None, **kwargs):
        self.sent.append((url, headers['Authorization'], json))
        reply = self.replies.pop(0)
        if reply is self.HANG:
            return HangingResponse()
        if isinstance(reply, BaseException):
            raise reply
        return FakeResponse(*reply)


class FakeResponse:
    def __init__(self, status, data):
        self.status, self.data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self.data


class HangingResponse:
    async def __aenter__(self):
        await asyncio.Event().wait()

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_loboo_web_search_answers_only_with_real_searches(monkeypatch):
    monkeypatch.setattr(config, 'ENABLED', True)
    monkeypatch.setattr(config, 'LOBOO_CPA_BASE_URL', 'https://cpa.example/v1')
    monkeypatch.setattr(config, 'LOBOO_SEARCH_MODEL', 'gpt-search')

    async def key_for(user):
        return f'key-of-{user.id}'

    monkeypatch.setattr(credentials, 'key_for', key_for)
    text = (
        '- 上海今天有雨（[A 站](https://a.example/1?utm_source=openai)）\n- 气温 20℃ [天气网](https://c.example/3)\n'
        '- 词条 [Foo](https://wiki.example/wiki/Foo_(bar))'
    )
    searched = {
        'output': [
            {
                'type': 'web_search_call',
                'status': 'completed',
                'action': {
                    'type': 'search',
                    'sources': [
                        {'type': 'url', 'url': 'https://b.example/2'},
                        {'type': 'url', 'url': 'https://c.example/3'},
                        {'type': 'url', 'url': 'https://old.example/s?q=%C9%CF%BA%A3&amp&utm_source=openai'},
                        {'type': 'api', 'name': 'oai-weather'},
                    ],
                },
            },
            {
                'type': 'message',
                'content': [
                    {
                        'type': 'output_text',
                        'text': text,
                        'annotations': [
                            {'type': 'url_citation', 'url': 'https://a.example/1?utm_source=openai', 'title': 'A 站'}
                        ],
                    }
                ],
            },
        ]
    }
    from_memory = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '凭记忆：可能有雨'}]}]}
    cpa, events = FakeCpa(), {}

    async def get_session():
        return cpa

    async def emit(event):
        events.setdefault(event['type'], []).append(event['data'])

    monkeypatch.setattr(web_search, 'get_session', get_session)
    member = {'id': 'u1', 'email': 'a@loboo.ai', 'name': 'n', 'role': 'user'} | dict.fromkeys(
        ('last_active_at', 'updated_at', 'created_at'), 0
    )

    async def ask(*answers, capabilities=None):
        cpa.replies, cpa.sent = list(answers), []
        events.clear()
        # 带了附件时上游把检索内容拼进最后一条用户消息，原话另存在 base_user_prompt。
        messages = [{'role': 'user', 'content': '<检索到的附件内容>\n上海明天要带伞吗'}]
        metadata = {'base_user_prompt': '上海明天要带伞吗', 'model': {'info': {'meta': {'capabilities': capabilities}}}}
        return await web_search.search_internet(
            '上海今天天气', __user__=member, __event_emitter__=emit, __metadata__=metadata, __messages__=messages
        )

    # 渠道没有真正搜索（工具被去掉、凭记忆作答）时不用它的话，换一次渠道；用本人的 Key 发给 CPA 上的 GPT 强制搜索。
    result = await ask((200, from_memory), (200, searched))
    assert result.startswith('联网搜索结果（搜索时间：北京时间 ') and result.endswith(f'\n\n{text}')
    assert [(url, key) for url, key, _ in cpa.sent] == [('https://cpa.example/v1/responses', 'Bearer key-of-u1')] * 2
    body = cpa.sent[0][2]
    assert (body['model'], body['tool_choice'], body['tools'][0]['type']) == ('gpt-search', 'required', 'web_search')
    # 用户的原话一并带上：对话模型转述时可能把"明天"写错。
    assert body['input'][0]['content'][0]['text'] == '用户的原话：上海明天要带伞吗\n\n要查的：上海今天天气'
    # 状态条先显示搜索词，再列出搜过的网站：引用的在前、带标题，去掉 utm_source 后同一网页只列一次，
    # 其余查询串原样保留（GBK 编码、没有值的参数）。引用的网页另作引用来源。
    assert events['status'][0] == {'action': 'web_search_queries_generated', 'queries': ['上海今天天气'], 'done': False}
    assert events['status'][1]['items'] == [
        {'title': 'A 站', 'link': 'https://a.example/1'},
        {'title': '天气网', 'link': 'https://c.example/3'},
        {'title': 'Foo', 'link': 'https://wiki.example/wiki/Foo_(bar)'},
        {'link': 'https://b.example/2'},
        {'link': 'https://old.example/s?q=%C9%CF%BA%A3&amp'},
    ]
    sources = events['source']
    assert [source['metadata'][0]['source'] for source in sources][:2] == ['https://a.example/1', 'https://c.example/3']
    assert sources[1]['document'] == ['- 气温 20℃ [天气网](https://c.example/3)']
    # 模型关掉了引用能力时只列网站，不发引用来源。
    await ask((200, searched), capabilities={'citations': False})
    assert events['status'][-1]['done'] is True and 'source' not in events

    # 某个渠道一时卡住超时：换一次渠道。
    assert (await ask(TimeoutError(), (200, searched))).endswith(f'\n\n{text}') and len(cpa.sent) == 2
    # 两次都没有真正搜索：如实报不可用，不把凭记忆写的内容当结果。
    assert json.loads(await ask((200, from_memory), (502, {}))) == {'error': web_search.UNAVAILABLE}
    assert len(cpa.sent) == 2 and events['status'][-1]['error'] is True
    # 额度用完、请求太快：不再换渠道重试。
    assert json.loads(await ask((429, {'error': 'rate limited'}))) == {'error': web_search.THROTTLED}
    assert len(cpa.sent) == 1
    # 意外的错误、成员中途停止生成（上游取消任务），也都要把「搜索中」收尾，否则消息里的状态条一直转圈。
    assert json.loads(await ask(RuntimeError('boom'))) == {'error': web_search.UNAVAILABLE}
    assert events['status'][-1]['done'] is True
    events.clear()
    searching = asyncio.create_task(ask(FakeCpa.HANG))
    for _ in range(100):
        if events.get('status'):
            break
        await asyncio.sleep(0)
    searching.cancel()
    with pytest.raises(asyncio.CancelledError):
        await searching
    assert events['status'][-1] == {'action': 'web_search', 'description': '已停止搜索', 'done': True}

def _terminal_skill_reader(monkeypatch):
    """read_skill_file against a fake terminal: one skill with a text and an image supporting file."""
    from open_webui.tools import builtin
    from open_webui.utils import terminals

    requests = []

    async def fake_json(request, user, metadata, path, extra_params=None):
        requests.append(path)
        if path.startswith('/skills/read'):
            return {
                'name': 'loboo-cli',
                'content': 'Check [INSTALL.md](INSTALL.md) first.',
                'location': '/home/user/.agents/skills/loboo-cli/SKILL.md',
                'resources': ['INSTALL.md', 'logo.png'],
            }
        if path.endswith('INSTALL.md'):
            return {'content': 'install steps ' * 10}
        return None  # images come back as raw bytes, which get_terminal_json reads as None

    monkeypatch.setattr(terminals, 'get_terminal_json', fake_json)

    async def read(path, **kwargs):
        return json.loads(
            await builtin.read_skill_file(
                'terminal:loboo-cli',
                path,
                __request__=object(),
                __user__={'id': 'u1'},
                __metadata__={'terminal_id': 'cloud'},
                **kwargs,
            )
        )

    return read, requests


@pytest.mark.asyncio
async def test_terminal_skill_reads_the_supporting_files_view_skill_lists(monkeypatch):
    read, requests = _terminal_skill_reader(monkeypatch)

    page = await read('INSTALL.md', max_chars=20)
    assert page == {'path': 'INSTALL.md', 'content': ('install steps ' * 10)[:20], 'next_offset': 20}
    assert '/files/read?path=%2Fhome%2Fuser%2F.agents%2Fskills%2Floboo-cli%2FINSTALL.md' in requests

    image = await read('logo.png')
    assert 'error' not in image
    assert image['location'] == '/home/user/.agents/skills/loboo-cli/logo.png' and image['notice']


@pytest.mark.asyncio
async def test_terminal_skill_refuses_files_view_skill_does_not_list(monkeypatch):
    read, requests = _terminal_skill_reader(monkeypatch)

    for path in ('missing.md', '../x', '/etc/passwd', 'references/../INSTALL.md'):
        result = await read(path)
        assert "'INSTALL.md'" in result['error'], path
    assert not any(request.startswith('/files/read') for request in requests)
