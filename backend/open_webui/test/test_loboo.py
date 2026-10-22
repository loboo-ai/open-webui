import base64
import io
import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.loboo import config, credentials, hooks, pages
from open_webui.loboo.router import safe_next
from open_webui.routers import images
from open_webui.utils.headers import get_headers_and_cookies
from PIL import Image


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
