"""
上游代码调用的入口：密码登录、整页打开、给模型连接和图片生成取 Key、对话带上终端。
没配置萝卜服务时只剩萝卜云的按需拉起和上下文缓存（见 cloud.py），其余都不做事。
"""

import logging

from fastapi import HTTPException, status

from open_webui.loboo import client, cloud, config, credentials
from open_webui.models.users import UserModel

log = logging.getLogger(__name__)


async def on_password_signin(user: UserModel) -> None:
    """
    密码校验通过、签发会话之前：同步取一次配置。萝卜账号已停用的普通用户不让登录；
    萝卜服务不可达时照常登录，对话时再取。管理员不受萝卜账号状态影响，留作应急入口。
    """
    if not config.ENABLED:
        return
    try:
        entry = await credentials.refresh(user)
    except client.LobooUnavailable as e:
        log.warning('loboo: config for user %s not refreshed at sign-in: %s', user.id, e)
        return
    if entry.state == 'disabled' and user.role == 'user':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=credentials.MESSAGES['disabled'])


async def on_session(user: UserModel) -> None:
    """整页打开（GET /api/v1/auths/）时：已知萝卜账号停用的普通用户退回登录页，其余在后台刷新配置。"""
    if not config.ENABLED:
        return
    if user.role == 'user' and credentials.known_disabled(user.id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=credentials.MESSAGES['disabled'])
    credentials.refresh_in_background(user)


async def connection_key(url: str, user: UserModel | None) -> str | None:
    """
    auth_type='loboo' 的连接发请求时用的 Key：只发往 LOBOO_CPA_BASE_URL 及其下的路径，地址对不上时拒绝而不是带错 Key。

    拉模型列表（GET <base>/models）时不抛错：本人没有可用的 Key（没开通、启动时预取、萝卜服务不可达）就借用
    最近取到的别人的 Key。CPA 不按 Key 区分模型，列表对谁都一样；模型注册表是全局的，某次列表拉空了，
    别人对话也会报 Model not found。拉列表不计额度。
    """
    target = config.normalize_url(url)
    if not _within_cpa(target):
        log.error('loboo: refusing to send a member key to %s', url)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='模型连接配置有误，请联系管理员。'
        )
    if target == config.LOBOO_CPA_BASE_URL + '/models':
        if user is not None:
            try:
                return await credentials.key_for(user)
            except HTTPException:
                pass
        return credentials.listing_key()
    if user is None:
        return None
    return await credentials.key_for(user)


async def image_key(base_url: str, user: UserModel | None) -> str | None:
    """
    图片生成、编辑用的 Key。接入地址是 LOBOO_CPA_BASE_URL 或其下的路径时用本人的 Key（和对话同一把），
    取不到就报原因，不退回后台填的 Key；其他图片后台返回 None，照旧用后台填的 Key。
    """
    if not _within_cpa(config.normalize_url(base_url)):
        return None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='请重新登录后再试。')
    return await credentials.key_for(user)


async def on_terminal_tools(
    request, user: UserModel, terminal_id: str, metadata: dict, tools: dict, event_emitter
) -> dict:
    """
    对话带上终端工具时：萝卜云的工具在这一轮第一次调用前才给 loboo-cli 写本人的 Key、拉起容器（见 cloud.py）。
    不会让对话失败；其他终端的工具原样返回。
    """
    try:
        return await cloud.wrap_terminal_tools(request, user, terminal_id, metadata, tools, event_emitter)
    except Exception:
        log.exception('loboo: cloud terminal tools for user %s left as they are', user.id)
        return tools


async def terminal_skills(
    request, user: UserModel, metadata: dict, extra_params: dict | None, wanted=()
) -> list | None:
    """这条消息的终端技能列表：萝卜云用按人缓存的，不为一条普通消息拉起容器；wanted 是点名或选用的技能 id。"""
    return await cloud.terminal_skills(request, user, metadata, extra_params, wanted)


async def terminal_skill(
    request, user: UserModel, metadata: dict, skill_name: str, extra_params: dict | None, **options
):
    """对话里选用或点名（$）的终端技能的正文：萝卜云用按人缓存的；options 是 get_terminal_skill 的关键字参数。"""
    return await cloud.terminal_skill(request, user, metadata, skill_name, extra_params, **options)


async def terminal_agents_md(request, user: UserModel, metadata: dict, extra_params: dict | None) -> str | None:
    """这条消息的终端 AGENTS.md：萝卜云用按人缓存的。"""
    return await cloud.terminal_agents_md(request, user, metadata, extra_params)


def _within_cpa(target: str) -> bool:
    """target 是规范化后的地址：CPA 接入地址本身或其下的路径。"""
    base = config.LOBOO_CPA_BASE_URL
    return bool(config.ENABLED and base and (target == base or target.startswith(base + '/')))
