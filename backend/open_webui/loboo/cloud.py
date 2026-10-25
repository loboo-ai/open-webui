"""萝卜云：把成员本人的 Codex Key 放进他自己的云终端，loboo-cli 没有萝卜会话时用它去换。

只写进按用户隔离的终端（Terminals 编排服务，每人一个容器）：一般的 Open Terminal 连接可能多人共用，写进去
就泄露给了别人。同一个终端每 KEY_CACHE_TTL_SECONDS 最多写一次，Key 换了或 home 被重置，最多这么久后补上。

另外回答"这个人的容器醒着没有"（terminal_state）：文件面板据此决定是直接挂载，还是先显示休眠卡片，
免得一打开页面就把回收了的容器又拉起来。

普通消息不碰容器：每条消息要的技能列表和 AGENTS.md 用按人缓存的（terminal_skills、terminal_agents_md），
Key 改到这一轮第一次调用终端工具前再写（wrap_terminal_tools）。容器只在真用终端时醒着，空闲 15 分钟后回收。
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import aiohttp
from fastapi import HTTPException

from open_webui.loboo import config, credentials
from open_webui.models.users import UserModel

log = logging.getLogger(__name__)

# loboo-cli 没有会话时读这个文件里的 Key（loboo-mono/cli/auth.go 的 readCodexKey）。
KEY_FILE = '/home/user/.codex/auth.json'

# (用户, 终端, 终端上下文) → (写进去的 Key, 写入时间)
_written: dict[tuple[str, str, str], tuple[str, float]] = {}
_warned_at: dict[tuple[str, str, str], float] = {}


async def provide_cli_key(request, user: UserModel, terminal_id: str, metadata: dict) -> None:
    """对话带上终端时调用；取不到 Key 或写入失败都只记日志，不影响对话。"""
    from open_webui.env import AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL, AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA
    from open_webui.models.config import Config
    from open_webui.utils.terminals import (
        TERMINAL_CONTEXT_HEADER,
        get_terminal_server_url,
        is_terminal_orchestrator,
        terminal_context_id,
    )
    from open_webui.utils.tools import build_tool_server_headers

    connections = await Config.get('terminal_server.connections', []) or []
    connection = next((item for item in connections if item.get('id') == terminal_id), None)
    if connection is None or not is_terminal_orchestrator(connection):
        return
    try:
        key = await credentials.key_for(user)
    except HTTPException:
        return

    context = 'automation' if metadata.get('automation_id') else 'chat'
    context_id = terminal_context_id(connection, metadata, context)
    slot = (user.id, terminal_id, context_id or 'default')
    now = time.time()
    written = _written.get(slot)
    if written and written[0] == key and now - written[1] < config.KEY_CACHE_TTL_SECONDS:
        return

    # 与 get_terminal_json 相同的鉴权与路由头；不带 X-Session-Id，写入路径不随会话的当前目录变
    headers, cookies = await build_tool_server_headers(connection, request, user, metadata=metadata)
    headers['X-User-Id'] = user.id
    if context_id:
        headers[TERMINAL_CONTEXT_HEADER] = context_id
    try:
        await write_file(
            f'{get_terminal_server_url(connection)}/files/write',
            headers,
            cookies,
            KEY_FILE,
            json.dumps({'OPENAI_API_KEY': key}),
            timeout=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA,
            ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
        )
    except Exception as e:
        if now - _warned_at.get(slot, 0) >= config.KEY_CACHE_TTL_SECONDS:
            _warned_at[slot] = now
            log.warning(
                'loboo: could not give loboo-cli its key in terminal %s for user %s: %s', terminal_id, user.id, e
            )
        return
    _written[slot] = (key, now)


async def write_file(url: str, headers: dict, cookies: dict, path: str, content: str, *, timeout, ssl) -> None:
    """Open Terminal 的 POST /files/write；父目录不存在时由它创建。"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=True) as session:
        async with session.post(
            url, json={'path': path, 'content': content}, headers=headers, cookies=cookies, ssl=ssl
        ) as response:
            if response.status != 200:
                raise RuntimeError(f'HTTP {response.status}: {(await response.text())[:200]}')


# 编排服务的实例列表：地址 → (取到的时刻, 列表；取不到为 None)。列表里有所有人，只在服务端用。
_STATE_TTL_SECONDS = 10
_STATE_TIMEOUT_SECONDS = 3
_instances_cache: dict[str, tuple[float, list | None]] = {}
_instances_lock = asyncio.Lock()


async def terminal_state(user: UserModel, terminal_id: str) -> dict:
    """
    萝卜云里这个人的容器醒着没有：running、sleeping，或查不到时 unknown（调用方照常放行）。
    查的是编排服务的实例列表，它既不拉起容器也不续命。按默认上下文判断：现在的连接不按对话分容器。
    """
    state = {'state': 'unknown', 'idle_seconds': None, 'idle_timeout_minutes': None}
    connection = await _orchestrator(user, terminal_id)
    rows = await _instances(connection) if connection else None
    if rows is None:
        return state
    # 编排服务把没写策略的连接记在 default 策略下（Terminals backends/base.py）。
    policy_id = str(connection.get('policy_id') or '').strip() or 'default'
    row = next(
        (
            row
            for row in rows
            if row.get('user_id') == user.id
            and str(row.get('policy_id') or 'default') == policy_id
            and (row.get('context_id') or 'default') == 'default'
        ),
        None,
    )
    if row is None or row.get('status') != 'running':
        return {**state, 'state': 'sleeping'}
    return {
        'state': 'running',
        'idle_seconds': row.get('idle_seconds'),
        'idle_timeout_minutes': row.get('idle_timeout_minutes'),
    }


async def wake(request, user: UserModel, terminal_id: str) -> bool:
    """面板上点唤醒：给 loboo-cli 写好本人的 Key（写入顺带拉起容器）。不能用这个终端时返回 False。"""
    if await _orchestrator(user, terminal_id) is None:
        return False
    if config.ENABLED:
        await provide_cli_key(request, user, terminal_id, {})
    return True


async def _orchestrator(user: UserModel, terminal_id: str) -> dict | None:
    """这个人能用的、按人分容器的终端连接（编排服务）；不是或没有权限时为 None。"""
    from open_webui.models.config import Config
    from open_webui.models.groups import Groups
    from open_webui.utils.access_control import has_connection_access
    from open_webui.utils.terminals import is_terminal_orchestrator

    connections = await Config.get('terminal_server.connections', []) or []
    connection = next((item for item in connections if item.get('id') == terminal_id), None)
    if connection is None or not connection.get('enabled', True) or not is_terminal_orchestrator(connection):
        return None
    group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, include_inherited=True)}
    return connection if await has_connection_access(user, connection, group_ids) else None


async def _instances(connection: dict) -> list | None:
    """编排服务的实例列表，缓存 _STATE_TTL_SECONDS；并发的查询排队，后来的直接用刚取到的。"""
    url = str(connection.get('url') or '').rstrip('/')
    async with _instances_lock:
        cached = _instances_cache.get(url)
        if cached and time.monotonic() - cached[0] < _STATE_TTL_SECONDS:
            return cached[1]
        rows = await _fetch_instances(url, str(connection.get('key') or ''))
        _instances_cache[url] = (time.monotonic(), rows)
        return rows


async def _fetch_instances(url: str, key: str) -> list | None:
    """GET /api/v1/terminals 要管理 Key，而且不能带 X-User-Id（带了编排服务按代理请求处理，回 403）。"""
    from open_webui.env import AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL

    try:
        timeout = aiohttp.ClientTimeout(total=_STATE_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(
                f'{url}/api/v1/terminals',
                headers={'Authorization': f'Bearer {key}'},
                ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f'HTTP {response.status}')
                rows = await response.json()
        if not isinstance(rows, list):
            raise ValueError('not a list')
        return rows
    except Exception as e:
        log.warning('loboo: could not list cloud terminals at %s: %s', url, e)
        return None


@dataclass(frozen=True)
class _Context:
    """每条消息要的终端上下文：技能列表和 AGENTS.md（没有为 None）。"""

    skills: list
    agents_md: str | None
    loaded_at: float


# (用户, 终端, 上下文) → 最近一次从容器取到的上下文。只在容器真被用过之后刷新，所以是"它上次醒着时的样子"：
# 在面板或 Shell 里直接改了技能目录、AGENTS.md，要等下一次调用终端工具后才进系统提示。
_contexts: dict[tuple[str, str, str], _Context] = {}
# 对话里选用或用 $ 点名的技能正文：((用户, 终端, 上下文), 技能名, 取法) → get_terminal_skill 的结果。取法是传给
# get_terminal_skill 的关键字参数（overview、tools_enabled），结果随它变。选用和点名会一直留在对话里，
# 不缓存的话这个对话之后的每条消息都要去容器取一次。
_skill_bodies: dict[tuple[tuple[str, str, str], str, tuple], dict] = {}
_refreshing: dict[tuple[str, str, str], asyncio.Task] = {}
# 一轮里连着调用多次终端工具时，只在最后一次之后取一回。
_REFRESH_DELAY_SECONDS = 5
# 兜底：缓存超过这么久、而容器正醒着时，下一条消息顺手在后台刷新（容器醒着，问它不会多拉起一次）。
_CONTEXT_MAX_AGE_SECONDS = 6 * 3600
STARTING = '萝卜云启动中，第一次要几秒…'


async def terminal_skills(
    request, user: UserModel, metadata: dict, extra_params: dict | None, wanted=()
) -> list | None:
    """
    这条消息的终端技能列表；萝卜云用缓存，其他终端照旧现取。wanted 是这条消息点名或选用的终端技能 id：
    缓存里没有时（比如刚在面板里新建的技能）当场重取，成员点名了，拉起容器是应该的。
    """
    from open_webui.utils.terminals import get_terminal_json

    slot = await _cloud_slot(user.id, metadata)
    if slot is None:
        return await get_terminal_json(request, user, metadata, '/skills', extra_params)
    context = await _context(slot, request, user, metadata, extra_params)
    if context is not None and not set(wanted) <= {skill.get('id') for skill in context.skills}:
        context = await _load_context(request, user, metadata, extra_params) or context
        _contexts[slot] = context
    return context.skills if context else None


async def terminal_agents_md(request, user: UserModel, metadata: dict, extra_params: dict | None) -> str | None:
    """这条消息的终端 AGENTS.md；萝卜云用缓存，其他终端照旧现取。"""
    from open_webui.utils.terminals import get_terminal_agents_md

    slot = await _cloud_slot(user.id, metadata)
    if slot is None:
        return await get_terminal_agents_md(request, user, metadata, extra_params)
    context = await _context(slot, request, user, metadata, extra_params)
    return context.agents_md if context else None


async def terminal_skill(
    request, user: UserModel, metadata: dict, skill_name: str, extra_params: dict | None, **options
):
    """
    对话里选用或点名的终端技能的正文；options 原样交给 get_terminal_skill。
    萝卜云按同样的取法第一次取一次，之后用缓存，其他终端照旧现取。
    """
    from open_webui.utils.terminals import get_terminal_skill

    slot = await _cloud_slot(user.id, metadata)
    if slot is None:
        return await get_terminal_skill(request, user, metadata, skill_name, extra_params, **options)
    key = (slot, skill_name, tuple(sorted(options.items())))
    body = _skill_bodies.get(key)
    if body is None:
        body = await get_terminal_skill(request, user, metadata, skill_name, extra_params, **options)
        if body is not None:
            _skill_bodies[key] = body
    return body


async def wrap_terminal_tools(
    request, user: UserModel, terminal_id: str, metadata: dict, tools: dict, event_emitter
) -> dict:
    """
    萝卜云的终端工具：这一轮第一次调用前写好 loboo-cli 的 Key（容器休眠时这一步顺带拉起它，并提示"启动中"）；
    每次调用后在后台刷新技能列表和 AGENTS.md 的缓存（容器刚被用过，问它不会多拉起一次）。其他终端原样返回。
    """
    slot = await _cloud_slot(user.id, {**metadata, 'terminal_id': terminal_id})
    if slot is None:
        return tools
    turn: dict = {}

    async def prepare() -> bool:
        """返回容器是不是冷的；出错只记日志，工具照常调用。"""
        try:
            cold = (await terminal_state(user, terminal_id))['state'] == 'sleeping'
            if cold:
                await _status(event_emitter, {'action': 'loboo_cloud', 'description': STARTING, 'done': False})
            if config.ENABLED:
                await provide_cli_key(request, user, terminal_id, metadata)
            return cold
        except Exception:
            log.exception('loboo: preparing cloud terminal %s for user %s failed', terminal_id, user.id)
            return False

    def wrap(entry: dict) -> dict:
        original = entry['callable']

        # 不用 functools.wraps：get_updated_tool_function 见到 __function__ 会拿原函数重新包一层，把这里绕开。
        async def call(**kwargs):
            if 'ready' not in turn:
                turn['ready'] = asyncio.ensure_future(prepare())
            cold = await turn['ready']
            try:
                return await original(**kwargs)
            finally:
                _refresh_later(slot, request, user, metadata)
                if cold and not turn.get('started'):
                    turn['started'] = True
                    await _status(event_emitter, {'action': 'loboo_cloud', 'done': True, 'hidden': True})

        return {**entry, 'callable': call}

    return {name: wrap(entry) for name, entry in tools.items()}


async def _status(event_emitter, data: dict) -> None:
    """回复上方的状态行（没有对话会话时没有 emitter）。出错只记日志：它不能让工具调用失败。"""
    if not event_emitter:
        return
    try:
        await event_emitter({'type': 'status', 'data': data})
    except Exception:
        log.exception('loboo: cloud terminal status not shown')


async def _cloud_slot(user_id: str, metadata: dict) -> tuple[str, str, str] | None:
    """萝卜云（编排服务）连接时返回缓存键 (用户, 终端, 上下文)；其他终端为 None。"""
    from open_webui.models.config import Config
    from open_webui.utils.terminals import is_terminal_orchestrator, terminal_context_id

    terminal_id = metadata.get('terminal_id')
    connections = await Config.get('terminal_server.connections', []) or []
    connection = next((item for item in connections if item.get('id') == terminal_id), None)
    if connection is None or not is_terminal_orchestrator(connection):
        return None
    context = 'automation' if metadata.get('automation_id') else 'chat'
    return user_id, terminal_id, terminal_context_id(connection, metadata, context) or 'default'


async def _context(slot, request, user: UserModel, metadata: dict, extra_params: dict | None) -> _Context | None:
    context = _contexts.get(slot)
    if context is None:
        # 进程重启后这个人的第一条消息：还没有缓存，只好问一次容器（会拉起它）。
        context = await _load_context(request, user, metadata, extra_params)
        if context is not None:
            _contexts[slot] = context
    elif time.monotonic() - context.loaded_at > _CONTEXT_MAX_AGE_SECONDS and slot not in _refreshing:
        # 在面板或 Shell 里直接改了技能、AGENTS.md 又一直没调用终端工具的兜底；容器睡着时不叫醒它。
        if (await terminal_state(user, slot[1]))['state'] == 'running':
            _refresh_later(slot, request, user, metadata)
    return context


async def _load_context(request, user: UserModel, metadata: dict, extra_params: dict | None) -> _Context | None:
    """从容器取技能列表和 AGENTS.md；技能列表取不到时为 None（不缓存，下一条消息再取）。"""
    from open_webui.utils.terminals import get_terminal_agents_md, get_terminal_json

    # 先取技能列表：容器睡着时由它等冷启动，AGENTS.md 再读就不会因为冷启动超时被当成"没有"。
    listed = await get_terminal_json(request, user, metadata, '/skills', extra_params)
    if not isinstance(listed, list):
        return None
    return _Context(listed, await get_terminal_agents_md(request, user, metadata, extra_params), time.monotonic())


def _refresh_later(slot, request, user: UserModel, metadata: dict) -> None:
    """容器刚被用过：_REFRESH_DELAY_SECONDS 后在后台重取上下文和点名过的技能正文，这期间又被用到就往后推。"""
    previous = _refreshing.pop(slot, None)
    if previous is not None:
        previous.cancel()

    async def refresh():
        await asyncio.sleep(_REFRESH_DELAY_SECONDS)
        from open_webui.utils.terminals import get_terminal_skill

        try:
            context = await _load_context(request, user, metadata, {})
            if context is not None:
                _contexts[slot] = context
            for key in [key for key in _skill_bodies if key[0] == slot]:
                _, skill_name, options = key
                body = await get_terminal_skill(request, user, metadata, skill_name, {}, **dict(options))
                if body is None:
                    _skill_bodies.pop(key, None)
                else:
                    _skill_bodies[key] = body
        except Exception as e:
            log.debug('loboo: refreshing cloud terminal context for user %s failed: %s', user.id, e)

    task = asyncio.create_task(refresh())
    _refreshing[slot] = task
    task.add_done_callback(lambda done: _refreshing.pop(slot, None) if _refreshing.get(slot) is done else None)
