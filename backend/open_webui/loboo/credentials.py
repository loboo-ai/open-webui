"""每个人的 Codex Key：从萝卜服务取，只放在进程内（单 worker）。登录时同步取，整页打开时后台刷新，
对话时过期就重取；萝卜服务不可达时，旧配置最多再用 STALE_MAX_SECONDS，Key 到期后不再用。
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException

from open_webui.loboo import client, config, identity
from open_webui.models.users import UserModel

log = logging.getLogger(__name__)

# 对话里显示的提示，按配置的状态区分。
MESSAGES = {
    'unlinked': '你的账号还没有对应的萝卜账号，暂时不能对话，请联系管理员。',
    'pending': 'Codex 正在开通，开通后就能对话。',
    'inactive': 'Codex 未开通或已到期，续费后就能继续对话。',
    'disabled': '萝卜账号已停用，请联系管理员。',
}
UNAVAILABLE = '暂时取不到模型配置，请稍后重试。'


@dataclass(frozen=True)
class Entry:
    """state：active（有 Key）、pending、inactive、unlinked（萝卜里没有这个人）、disabled（萝卜账号已停用）。"""

    state: str
    key: str | None
    ends_at: float | None
    fetched_at: float


_entries: dict[str, Entry] = {}
_locks: dict[str, asyncio.Lock] = {}
_attempted_at: dict[str, float] = {}
_background: set[asyncio.Task] = set()


def _fresh(entry: Entry, now: float) -> bool:
    return now - entry.fetched_at < config.KEY_CACHE_TTL_SECONDS and not (entry.ends_at and entry.ends_at <= now)


def _usable_when_unavailable(entry: Entry, now: float) -> bool:
    return now - entry.fetched_at < config.STALE_MAX_SECONDS and not (entry.ends_at and entry.ends_at <= now)


def remember(user_id: str, profile: client.Profile) -> Entry:
    """记下刚从萝卜服务取到的配置（免密登录兑换票据时）。"""
    entry = Entry(profile.state, profile.key, profile.ends_at, time.time())
    _entries[user_id] = entry
    return entry


async def _fetch(user: UserModel) -> Entry:
    linked = identity.loboo_id(user)
    try:
        profile = await (client.profile_by_id(linked) if linked else client.profile_by_login(user.email))
    except client.LobooError as e:
        if e.code == client.ACCOUNT_NOT_FOUND:
            return Entry('unlinked', None, None, time.time())
        if e.code == client.ACCOUNT_NOT_ACTIVATED:
            return Entry('disabled', None, None, time.time())
        raise client.LobooUnavailable(str(e)) from e
    # 按登录名找到的要先确认是同一个人，对不上时当作没有对应的萝卜账号，不用别人的 Key。
    if not linked and not await identity.link(user, profile.user_id):
        return Entry('unlinked', None, None, time.time())
    return Entry(profile.state, profile.key, profile.ends_at, time.time())


async def refresh(user: UserModel) -> Entry:
    """
    向萝卜服务取这个人的配置；萝卜服务不可达时抛 LobooUnavailable，旧配置不动。
    同一个人同时只取一次：等锁期间别人刚取到的，直接用。
    """
    started = time.time()
    lock = _locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        previous = _entries.get(user.id)
        if previous and previous.fetched_at >= started:
            return previous
        _attempted_at[user.id] = time.time()
        entry = await _fetch(user)
        _entries[user.id] = entry
    if entry.state == 'disabled' and (previous is None or previous.state != 'disabled'):
        # 萝卜里停用了：吊销这个人在 Open WebUI 的全部会话。
        from open_webui.utils.auth import revoke_user_tokens

        log.info('loboo: account of user %s is disabled, revoking sessions', user.id)
        await revoke_user_tokens(None, user.id)
    return entry


async def key_for(user: UserModel) -> str:
    """对话时这个人的 Key。取不到时抛 HTTPException，detail 会显示在对话里。"""
    now = time.time()
    entry = _entries.get(user.id)
    if entry is None or not _fresh(entry, now):
        try:
            entry = await refresh(user)
        except client.LobooUnavailable as e:
            if entry is None or not _usable_when_unavailable(entry, now):
                log.warning('loboo: no usable config for user %s: %s', user.id, e)
                raise HTTPException(status_code=503, detail=UNAVAILABLE)
            log.warning('loboo: service unavailable, using config fetched at %d for user %s', entry.fetched_at, user.id)
    if entry.state == 'active' and entry.key:
        return entry.key
    raise HTTPException(status_code=403, detail=MESSAGES.get(entry.state, UNAVAILABLE))


def listing_key() -> str | None:
    """拉模型列表时借用的 Key：最近取到、还在有效期内的一把；一把都没有时为 None。"""
    now = time.time()
    usable = [
        entry
        for entry in _entries.values()
        if entry.state == 'active' and entry.key and _usable_when_unavailable(entry, now)
    ]
    return max(usable, key=lambda entry: entry.fetched_at).key if usable else None


def known_disabled(user_id: str) -> bool:
    """最近一次取配置时萝卜账号已停用，且还没过期。"""
    entry = _entries.get(user_id)
    return bool(entry and entry.state == 'disabled' and _fresh(entry, time.time()))


def refresh_in_background(user: UserModel) -> None:
    """整页打开时刷新配置，不等结果；同一个人 REFRESH_INTERVAL_SECONDS 内只刷新一次。"""
    now = time.time()
    if now - _attempted_at.get(user.id, 0) < config.REFRESH_INTERVAL_SECONDS:
        return
    _attempted_at[user.id] = now
    task = asyncio.create_task(_refresh_quietly(user))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _refresh_quietly(user: UserModel) -> None:
    try:
        await refresh(user)
    except client.LobooUnavailable as e:
        log.warning('loboo: background refresh for user %s failed: %s', user.id, e)
    except Exception:
        log.exception('loboo: background refresh for user %s failed', user.id)
