"""萝卜里的昵称和头像同步到 LoboOS：萝卜那边的值变了才覆盖，成员在 LoboOS 里自己改的保留到萝卜下次变化。

"变没变"按记号判断：上次同步过的萝卜值和 sub 一起记在 user.oauth.loboo（identity.py），成员没有接口能写它。
萝卜没设头像时生成 gravatar 的 retro 图案；萝卜以后设了头像，记号从 'gravatar' 变成头像路径，照样覆盖。
头像下载后存成 data URL，浏览器不直连萝卜或 gravatar。只同步普通用户。
"""

import asyncio
import base64
import hashlib
import io
import logging
import time

import aiohttp
from PIL import Image, ImageOps

from open_webui.loboo import client, config, identity
from open_webui.models.users import UserModel, Users
from open_webui.utils.validate import validate_background_image, validate_image_url

log = logging.getLogger(__name__)

GRAVATAR = 'gravatar'
# 与设置页上传头像的规格一致（UserProfileImage.svelte）：长边超过就居中裁成正方形，存 webp。
_SIZE = 250
# 萝卜头像不超过 100 KB（loboo-mono image-limits.ts），gravatar 250px 只有几 KB。
_MAX_BYTES = 256 * 1024
_TIMEOUT_SECONDS = 5
# 头像拿不到（萝卜源站或 gravatar 不可达）时，同一个头像过这么久再试，免得每次打开页面都白等一次下载。
_RETRY_AFTER_SECONDS = 3600
# 同一个人同时只同步一次：免密进入和整页打开可能同时触发（单 worker，进程内的锁够用）。
_locks: dict[str, asyncio.Lock] = {}
# (用户, 要同步成的头像) → 上次下载失败的时刻
_failed_at: dict[tuple[str, str], float] = {}


def _name(profile: client.Profile) -> str | None:
    """萝卜昵称：1–80 个字符的单行（萝卜自己也这么校验），不合规时这次不同步昵称。"""
    name = profile.display_name.strip()
    return name if 0 < len(name) <= 80 and '\n' not in name and '\r' not in name else None


def _avatar(user_id: str, profile: client.Profile) -> str | None:
    """
    要同步成的头像：萝卜头像路径或 'gravatar'。萝卜还没给头像字段、没配公网源站，或这个头像刚下载失败过
    （_RETRY_AFTER_SECONDS 内）时为 None，这次不同步头像。
    """
    if not (config.LOBOO_MEDIA_ORIGIN and profile.avatar_known):
        return None
    avatar = profile.avatar_path or GRAVATAR
    if time.monotonic() - _failed_at.get((user_id, avatar), -_RETRY_AFTER_SECONDS) < _RETRY_AFTER_SECONDS:
        return None
    return avatar


def pending(user: UserModel, profile: client.Profile) -> bool:
    """萝卜的昵称或头像和上次同步过的不一样：要同步。"""
    if user.role != 'user':
        return False
    marks = identity.synced_marks(user)
    name, avatar = _name(profile), _avatar(user.id, profile)
    return (name is not None and marks.get('name') != name) or (avatar is not None and marks.get('avatar') != avatar)


async def sync(user_id: str, profile: client.Profile) -> None:
    """后台任务：拿锁后按库里最新的记号再判断一次。头像拿不到时不写它的记号，下次再试；失败只记日志。"""
    try:
        async with _locks.setdefault(user_id, asyncio.Lock()):
            user = await Users.get_user_by_id(user_id)
            if user is None or not pending(user, profile):
                return
            marks = identity.synced_marks(user)
            fields, synced = {}, {}
            name = _name(profile)
            if name is not None and marks.get('name') != name:
                synced['name'] = name
                if user.name != name:
                    fields['name'] = name
            avatar = _avatar(user_id, profile)
            if avatar is not None and marks.get('avatar') != avatar:
                image = await _image(avatar, user_id)
                if image is None:
                    _failed_at[(user_id, avatar)] = time.monotonic()
                else:
                    _failed_at.pop((user_id, avatar), None)
                    fields['profile_image_url'] = image
                    synced['avatar'] = avatar
            if synced:
                await identity.save_synced(user_id, fields, synced)
    except Exception:
        log.exception('loboo: profile sync for user %s failed', user_id)


async def _image(avatar: str, user_id: str) -> str | None:
    if avatar == GRAVATAR:
        # 哈希用 LoboOS 用户 id 而不用邮箱：萝卜分配的登录名能被猜出来；f=y 时哈希只决定图案。
        digest = hashlib.sha256(user_id.encode()).hexdigest()
        url = f'https://www.gravatar.com/avatar/{digest}?s={_SIZE}&d=retro&r=g&f=y'
    else:
        url = config.LOBOO_MEDIA_ORIGIN + avatar
    try:
        # 解码、缩放在线程里做：单 worker，别让一张大图卡住所有请求。
        return await asyncio.to_thread(_data_url, await _download(url))
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError) as e:
        log.warning('loboo: avatar for user %s not synced from %s: %s', user_id, url.split('?')[0], e)
        return None


async def _download(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f'HTTP {response.status}')
            if (response.content_length or 0) > _MAX_BYTES:
                raise ValueError('image too large')
            data = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > _MAX_BYTES:
                    raise ValueError('image too large')
            return bytes(data)


def _data_url(data: bytes) -> str:
    """确认是图片（类型以内容为准），长边超过 _SIZE 的居中裁成正方形 webp，再转成 data URL。"""
    content_type = validate_background_image(data)
    with Image.open(io.BytesIO(data)) as image:
        if max(image.size) > _SIZE:
            out = io.BytesIO()
            ImageOps.fit(image.convert('RGBA'), (_SIZE, _SIZE)).save(out, 'WEBP', quality=80)
            data, content_type = out.getvalue(), 'image/webp'
    return validate_image_url(f'data:{content_type};base64,{base64.b64encode(data).decode()}')
