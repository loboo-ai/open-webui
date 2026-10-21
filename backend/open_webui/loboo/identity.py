"""Open WebUI 账号与萝卜账号的对应：先按记下的萝卜账号 id（user.oauth.loboo.sub），没有时按登录名（email）。

登录名只由萝卜分配，这里不建号；第一次按登录名对上时记下萝卜账号 id，之后按它找。
"""

import logging

from open_webui.models.users import UserModel, Users

log = logging.getLogger(__name__)

PROVIDER = 'loboo'


class IdentityMismatch(Exception):
    """找不到能免密登录的账号；message 直接给用户看。"""


def loboo_id(user: UserModel) -> str | None:
    """这个账号记下的萝卜账号 id；没记时为 None。"""
    entry = user.oauth.get(PROVIDER) if isinstance(user.oauth, dict) else None
    sub = entry.get('sub') if isinstance(entry, dict) else None
    return sub if isinstance(sub, str) and sub else None


async def link(user: UserModel, loboo_user_id: str, db=None) -> bool:
    """
    按登录名对上之后，确认这个账号属于这位萝卜成员，必要时记下萝卜账号 id。
    账号已记着别人的 id、或这个 id 已记在别的账号上时不算对上，返回 False 并记日志。
    只给普通用户记 id：管理员账号不能免密登录，也就不需要记。
    """
    current = loboo_id(user)
    if current:
        if current != loboo_user_id:
            log.warning('loboo: user %s is linked to %s, not %s', user.id, current, loboo_user_id)
        return current == loboo_user_id
    other = await Users.get_user_by_oauth_sub(PROVIDER, loboo_user_id, db=db)
    if other and other.id != user.id:
        log.warning('loboo: %s is already linked to user %s, refusing user %s', loboo_user_id, other.id, user.id)
        return False
    if user.role == 'user':
        await Users.update_user_oauth_by_id(user.id, PROVIDER, loboo_user_id, db=db)
    return True


async def sso_user(loboo_user_id: str, browser_login: str | None, db=None) -> UserModel:
    """票据兑换出的萝卜成员对应的 Open WebUI 账号；只有普通用户能免密登录。"""
    user = await Users.get_user_by_oauth_sub(PROVIDER, loboo_user_id, db=db)
    if user is None and browser_login:
        user = await Users.get_user_by_email(browser_login.lower(), db=db)
        if user and not await link(user, loboo_user_id, db=db):
            raise IdentityMismatch('这个账号与萝卜账号对不上，请联系管理员处理。')
    if user is None:
        raise IdentityMismatch('账号还没开通好，请稍后重新进入。')
    if user.role != 'user':
        log.warning('loboo: refusing passwordless sign-in to %s user %s', user.role, user.id)
        raise IdentityMismatch('这个账号不能免密登录，请在浏览器里用密码登录。')
    return user
