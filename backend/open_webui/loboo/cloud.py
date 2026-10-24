"""萝卜云：把成员本人的 Codex Key 放进他自己的云终端，loboo-cli 没有萝卜会话时用它去换。

只写进按用户隔离的终端（Terminals 编排服务，每人一个容器）：一般的 Open Terminal 连接可能多人共用，写进去
就泄露给了别人。同一个终端每 KEY_CACHE_TTL_SECONDS 最多写一次，Key 换了或 home 被重置，最多这么久后补上。
"""

import json
import logging
import time

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
