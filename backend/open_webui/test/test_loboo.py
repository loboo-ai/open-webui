import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.loboo import config, credentials, pages
from open_webui.loboo.router import safe_next
from open_webui.utils.headers import get_headers_and_cookies


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
