"""调萝卜账号服务的两个服务端接口：兑换票据、按账号查配置。凭服务令牌，走内网，不跟随重定向。"""

import datetime as dt
import logging
from dataclasses import dataclass

import aiohttp

from open_webui.loboo import config

log = logging.getLogger(__name__)

# 萝卜服务答复里会改变处理方式的业务错误；其余错误一律按暂时不可用处理。
TICKET_INVALID = 'TICKET_INVALID'
ACCOUNT_NOT_FOUND = 'ACCOUNT_NOT_FOUND'
ACCOUNT_NOT_ACTIVATED = 'ACCOUNT_NOT_ACTIVATED'
_BUSINESS_CODES = {TICKET_INVALID, ACCOUNT_NOT_FOUND, ACCOUNT_NOT_ACTIVATED}


class LobooError(Exception):
    """萝卜服务明确答复的业务错误，code 见上面几个常量。"""

    def __init__(self, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


class LobooUnavailable(Exception):
    """萝卜服务连不上、超时、出错或答复不合规，结果不明。"""


@dataclass(frozen=True)
class Profile:
    """萝卜里的一位成员与本人的 Codex Key；state 不是 active 时没有 Key。"""

    user_id: str
    display_name: str
    browser_login: str | None
    state: str
    key: str | None
    ends_at: float | None


def _text(value, *, optional=False) -> str | None:
    if value is None and optional:
        return None
    if isinstance(value, str) and value:
        return value
    raise ValueError('expected a non-empty string')


def _profile(data) -> Profile:
    user = data['user']
    credential = data['credential']
    state = credential['state']
    if state == 'active':
        key = _text(credential['key'])
        ends_at = _text(credential.get('endsAt'), optional=True)
        ends = dt.datetime.fromisoformat(ends_at).timestamp() if ends_at else None
    elif state in ('pending', 'inactive'):
        key, ends = None, None
    else:
        raise ValueError(f'unknown credential state {state!r}')
    return Profile(
        user_id=_text(user['id']),
        display_name=_text(user['displayName']),
        browser_login=_text(data.get('browserLogin'), optional=True),
        state=state,
        key=key,
        ends_at=ends,
    )


async def _post(path: str, body: dict) -> Profile:
    url = f'{config.LOBOO_SERVICE_URL}/account/api/openwebui/{path}'
    try:
        timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT_SECONDS)
        # 内网直连，不走环境里的代理。
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.post(
                url,
                json=body,
                headers={'Authorization': f'Bearer {config.LOBOO_SERVICE_TOKEN}'},
                allow_redirects=False,
            ) as response:
                status = response.status
                data = await response.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as e:
        raise LobooUnavailable(f'loboo {path}: {type(e).__name__}') from e

    if status == 200:
        try:
            return _profile(data)
        except (KeyError, TypeError, ValueError) as e:
            raise LobooUnavailable(f'loboo {path}: malformed profile') from e
    error = data.get('error') if isinstance(data, dict) else None
    code = error.get('code') if isinstance(error, dict) else None
    if code in _BUSINESS_CODES:
        raise LobooError(code, str(error.get('message') or code))
    # 令牌不对、未开放等配置问题也走这里：对登录放行，对 Key 按不可用拒绝。
    raise LobooUnavailable(f'loboo {path}: HTTP {status} {code or ""}'.strip())


async def redeem(ticket: str) -> Profile:
    return await _post('redeem', {'ticket': ticket})


async def profile_by_id(user_id: str) -> Profile:
    return await _post('profile', {'userId': user_id})


async def profile_by_login(browser_login: str) -> Profile:
    return await _post('profile', {'browserLogin': browser_login})
