"""免密登录：萝卜的 ChatGPT 入口跳到 GET /api/v1/loboo/sso?ticket=…&next=…，用一次性票据登录。

在小程序里、或从萝卜的页面跳过来时直接进入；其他情况（别人发来的链接、从别的网站跳过来）先显示确认页，
用户点继续后同源 POST 再签发，防止别人用自己的票据做链接，让你悄悄登进他的账号。
"""

import hashlib
import hmac
import logging
import time
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import get_async_session
from open_webui.loboo import client, config, credentials, identity, pages
from open_webui.models.users import Users
from open_webui.utils.auth import create_signin_response

log = logging.getLogger(__name__)

router = APIRouter()

_MAX_NEXT = 2048
# 确认页上的凭据多久内有效；它只能在本站同源提交。
_GRANT_SECONDS = 120
_GRANT_KEY = hashlib.sha256(b'open-webui/loboo-sso-grant/v1:' + WEBUI_SECRET_KEY.encode()).digest()

_TICKET_MESSAGES = {
    client.TICKET_INVALID: '登录链接已失效，请重新进入。',
    client.ACCOUNT_NOT_ACTIVATED: credentials.MESSAGES['disabled'],
    client.ACCOUNT_NOT_FOUND: '萝卜里找不到这个账号，请联系管理员。',
}


def safe_next(value: str | None) -> str:
    """登录后进入的站内路径：一个 / 开头的 SPA 路径；外站、// 或反斜杠开头、含控制字符、指向接口的一律换成 /。"""
    if not value or len(value) > _MAX_NEXT:
        return '/'
    for candidate in (value, unquote(value)):
        if (
            not candidate.startswith('/')
            or candidate.startswith('//')
            or '\\' in candidate
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate)
            or candidate.startswith('/api/')
        ):
            return '/'
    parts = urlsplit(value)
    return '/' if parts.scheme or parts.netloc else value


def _in_miniapp(request: Request) -> bool:
    # UA 可以伪造，只用来决定要不要多一步确认和怎么提示，不用于鉴权。
    return 'miniprogram' in request.headers.get('user-agent', '').lower()


def _direct_basis(request: Request) -> str | None:
    """
    不用确认就能直接登录的来路：在小程序里（web-view 只能打开业务域名，别人的链接进不来），
    或从萝卜的页面跳过来（萝卜的跳转只带源站作 Referer，在 LOBOO_TRUSTED_ORIGINS 里）。其余为 None。
    """
    if _in_miniapp(request):
        return 'miniapp'
    referer = urlsplit(request.headers.get('referer', ''))
    if referer.scheme and f'{referer.scheme}://{referer.netloc}'.lower() in config.LOBOO_TRUSTED_ORIGINS:
        return 'loboo page'
    return None


def _grant(user_id: str) -> str:
    message = f'{user_id}.{int(time.time()) + _GRANT_SECONDS}'
    signature = hmac.new(_GRANT_KEY, message.encode(), hashlib.sha256).hexdigest()
    return f'{message}.{signature}'


def _granted_user_id(grant: str) -> str | None:
    try:
        user_id, expires, signature = grant.rsplit('.', 2)
        expected = hmac.new(_GRANT_KEY, f'{user_id}.{expires}'.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected) and int(expires) >= time.time():
            return user_id
    except ValueError:
        pass
    return None


def _same_origin(request: Request) -> bool:
    """确认是本站页面提交的：Origin 与 Host 一致；Origin 为 null 时以浏览器给的 Sec-Fetch-Site 为准。"""
    origin = request.headers.get('origin')
    if origin and origin != 'null':
        return urlsplit(origin).netloc == request.headers.get('host')
    return request.headers.get('sec-fetch-site') == 'same-origin'


async def _sign_in(request: Request, user, next_path: str, db: AsyncSession) -> Response:
    """签发会话：cookie 和 localStorage 写给同一个人，再整页进入，socket 用新 token 重连。"""
    miniapp = _in_miniapp(request)
    cookies = Response()
    try:
        result = await create_signin_response(request, user, db, cookies, set_cookie=True, source='loboo')
    except HTTPException as e:
        log.warning('loboo: sign-in for user %s failed: %s', user.id, e.detail)
        return pages.error('登录没有完成，请重新进入。', miniapp=miniapp, retry=True)
    token = result.get('token') if isinstance(result, dict) else None
    if not token:
        # 开了二次验证，或账号还在待审核：这条路径完成不了，让用户走密码登录。
        return pages.error('这个账号需要在浏览器里用密码登录。', miniapp=miniapp)
    page = pages.bridge(token, next_path)
    for value in cookies.headers.getlist('set-cookie'):
        page.headers.append('set-cookie', value)
    return page


@router.get('/sso')
async def sso(
    request: Request,
    ticket: str = '',
    next_path: str = Query('/', alias='next'),
    db: AsyncSession = Depends(get_async_session),
):
    miniapp = _in_miniapp(request)
    if not config.ENABLED:
        return pages.error('免密登录未开放，请用账号密码登录。', miniapp=miniapp, status_code=404)
    target = safe_next(next_path)
    # 等萝卜服务时还没查过库，不占数据库连接。
    try:
        profile = await client.redeem(ticket)
    except client.LobooError as e:
        return pages.error(_TICKET_MESSAGES[e.code], miniapp=miniapp, retry=e.code == client.TICKET_INVALID)
    except client.LobooUnavailable as e:
        log.warning('loboo: ticket redemption failed: %s', e)
        return pages.error('暂时连不上萝卜服务，请稍后重新进入。', miniapp=miniapp, status_code=503)
    try:
        user = await identity.sso_user(profile.user_id, profile.browser_login, db=db)
    except identity.IdentityMismatch as e:
        return pages.error(str(e), miniapp=miniapp, status_code=409)
    credentials.remember(user.id, profile)
    basis = _direct_basis(request)
    log.info('loboo: sign-in for user %s %s', user.id, f'directly from {basis}' if basis else 'awaits confirmation')
    if basis:
        return await _sign_in(request, user, target, db)
    return pages.confirm(profile.display_name, profile.browser_login, _grant(user.id), target)


@router.post('/sso/confirm')
async def confirm(
    request: Request,
    grant: str = Form(''),
    next_path: str = Form('/', alias='next'),
    db: AsyncSession = Depends(get_async_session),
):
    miniapp = _in_miniapp(request)
    user_id = _granted_user_id(grant) if config.ENABLED and _same_origin(request) else None
    user = await Users.get_user_by_id(user_id, db=db) if user_id else None
    if user is None or user.role != 'user':
        return pages.error('确认已过期，请重新进入。', miniapp=miniapp)
    return await _sign_in(request, user, safe_next(next_path), db)
