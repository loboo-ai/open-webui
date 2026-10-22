"""免密登录的页面：提交（页面运行后才兑换票据）、桥接（写入登录状态后进入）、确认（浏览器里打开时先确认身份）、出错。

页面不经前端构建，内联样式和脚本都带 nonce；动态文本一律转义，内联数据放进 JSON 脚本块再读取。
"""

import html
import json
import secrets

from fastapi.responses import HTMLResponse

from open_webui.loboo import config

# 会话失效或进入失败时回到小程序的网页页，经萝卜的入口重新进入（与小程序首页的「问一问」卡片打开的一样，
# 见萝卜 ops/host/miniapp/entries.json）；static/static/loader.js 里有同一个地址。
MINIAPP_REENTRY = '/pages/web/index?to=page&path=%2Faccount%2Fapi%2Fopenwebui%2Fenter&title=%E9%97%AE%E4%B8%80%E9%97%AE'
JWEIXIN = 'https://res.wx.qq.com/open/js/jweixin-1.6.0.js'

# 深色跟随系统；小程序里跟随小程序，小程序只有浅色（与萝卜网页一致：<html> 带 mini-program 时不用深色）。
_MINI_PROGRAM = "if(/miniProgram/i.test(navigator.userAgent))document.documentElement.classList.add('mini-program')"
_STYLE = """
:root{color-scheme:light dark;--fg:#171717;--muted:#6b7280;--bg:#fff;--card:#f9fafb;--line:#e5e7eb;
--accent:#171717;--on-accent:#fff}
:root.mini-program{color-scheme:light}
@media (prefers-color-scheme:dark){:root:not(.mini-program){--fg:#f3f4f6;--muted:#9ca3af;--bg:#171717;
--card:#262626;--line:#374151;--accent:#f3f4f6;--on-accent:#171717}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
font:16px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
color:var(--fg);background:var(--bg)}
main{width:100%;max-width:360px;text-align:center}
h1{font-size:20px;font-weight:600;margin:0 0 8px}
p{margin:0 0 24px;color:var(--muted);word-break:break-word}
strong{color:var(--fg);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin:0 0 24px}
.card strong{display:block;font-size:18px}
.card span{display:block;color:var(--muted);font-size:14px}
.actions{display:flex;flex-direction:column;gap:12px}
button,a.button{display:block;width:100%;padding:12px 16px;border-radius:12px;border:1px solid var(--line);
font:inherit;text-decoration:none;color:var(--fg);background:transparent;cursor:pointer}
button.primary,a.primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}
.spinner{width:28px;height:28px;margin:0 auto 16px;border-radius:50%;border:3px solid var(--line);
border-top-color:var(--fg);animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
"""


def _json_for_script(value) -> str:
    """放进 <script type="application/json"> 的数据：转掉能提前结束脚本块或打断 JS 的字符。"""
    text = json.dumps(value, ensure_ascii=False)
    for char, escaped in (('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), (' ', '\\u2028'), (' ', '\\u2029')):
        text = text.replace(char, escaped)
    return text


def _page(
    title: str,
    body: str,
    *,
    nonce: str,
    status_code: int = 200,
    wechat: bool = False,
    referrer: str = 'no-referrer',
) -> HTMLResponse:
    scripts = f"'nonce-{nonce}'" + (f' {JWEIXIN}' if wechat else '')
    csp = (
        f"default-src 'none'; script-src {scripts}; style-src 'nonce-{nonce}'; img-src 'self' data:; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    document = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="robots" content="noindex,nofollow">'
        f'<title>{html.escape(title)}</title><script nonce="{nonce}">{_MINI_PROGRAM}</script>'
        f'<style nonce="{nonce}">{_STYLE}</style></head>'
        f'<body><main>{body}</main></body></html>'
    )
    return HTMLResponse(
        document,
        status_code=status_code,
        headers={
            'Content-Security-Policy': csp,
            'Cache-Control': 'no-store',
            'Pragma': 'no-cache',
            'Referrer-Policy': referrer,
            'X-Content-Type-Options': 'nosniff',
        },
    )


def submit(ticket: str, next_path: str, basis: str) -> HTMLResponse:
    """
    打开免密登录地址后的第一页：页面运行起来才把票据同源提交去兑换（POST /api/v1/loboo/sso）。
    只请求、不运行页面的客户端用不掉票据：Windows 桌面端的 WebView2 会先请求一遍自己拦下的跳转，
    再把同一个地址交给系统浏览器。
    """
    nonce = secrets.token_urlsafe(16)
    body = (
        '<div class="spinner"></div><p>正在进入…</p>'
        '<form id="loboo-sso" method="post" action="/api/v1/loboo/sso">'
        f'<input type="hidden" name="ticket" value="{html.escape(ticket)}">'
        f'<input type="hidden" name="next" value="{html.escape(next_path)}">'
        f'<input type="hidden" name="basis" value="{html.escape(basis)}">'
        '<noscript><button class="primary" type="submit">继续</button></noscript></form>'
        f'<script nonce="{nonce}">document.getElementById(\'loboo-sso\').submit();</script>'
    )
    # 同确认页：提交时要带上真实的 Origin，兑换接口据此核对同源。
    return _page('正在进入…', body, nonce=nonce, referrer='same-origin')


def bridge(token: str, next_path: str) -> HTMLResponse:
    """写入登录状态后整页进入：先清掉可能残留的别人的 token，SPA 启动时只认 localStorage。"""
    nonce = secrets.token_urlsafe(16)
    data = _json_for_script({'token': token, 'next': next_path})
    body = (
        '<div class="spinner"></div><p>正在进入…</p>'
        f'<script type="application/json" id="loboo-sso">{data}</script>'
        f'<script nonce="{nonce}">(function(){{'
        "var d=JSON.parse(document.getElementById('loboo-sso').textContent);"
        "try{localStorage.removeItem('token');localStorage.setItem('token',d.token);}catch(e){}"
        'location.replace(d.next);'
        '})();</script>'
    )
    return _page('正在进入…', body, nonce=nonce)


def confirm(display_name: str, browser_login: str | None, grant: str, next_path: str) -> HTMLResponse:
    """不在小程序里打开免密登录地址时，先让用户确认以谁的身份登录，防止被别人的链接悄悄换了账号。"""
    nonce = secrets.token_urlsafe(16)
    login = f'<span>{html.escape(browser_login)}</span>' if browser_login else ''
    body = (
        '<h1>确认登录</h1><p>将以下面的萝卜账号登录，之后的对话都记在这个账号下。</p>'
        f'<div class="card"><strong>{html.escape(display_name)}</strong>{login}</div>'
        '<form class="actions" method="post" action="/api/v1/loboo/sso/confirm">'
        f'<input type="hidden" name="grant" value="{html.escape(grant)}">'
        f'<input type="hidden" name="next" value="{html.escape(next_path)}">'
        '<button class="primary" type="submit">继续</button>'
        '<a class="button" href="/">取消</a></form>'
    )
    # no-referrer 会让浏览器提交表单时把 Origin 发成 null，确认接口就核对不了同源；同源时才带来源。
    return _page('确认登录', body, nonce=nonce, referrer='same-origin')


def error(message: str, *, miniapp: bool, retry: bool = False, status_code: int = 400) -> HTMLResponse:
    """
    进入失败。在小程序里给“重新进入”，回小程序重新换票据；retry 为 True（票据失效）时自动回去一次，
    30 秒内不重复，免得来回跳。浏览器里给“回萝卜重新进入”，回萝卜服务首页重新点 ChatGPT；没配首页地址时给“前往登录”。
    """
    nonce = secrets.token_urlsafe(16)
    if not miniapp:
        action = (
            f'<a class="button primary" href="{html.escape(config.LOBOO_PORTAL_URL)}">回萝卜重新进入</a>'
            if config.LOBOO_PORTAL_URL
            else '<a class="button primary" href="/auth">前往登录</a>'
        )
        body = f'<h1>没能进入</h1><p>{html.escape(message)}</p><div class="actions">{action}</div>'
        return _page('没能进入', body, nonce=nonce, status_code=status_code)
    data = _json_for_script({'url': MINIAPP_REENTRY, 'auto': retry})
    body = (
        f'<h1>没能进入</h1><p>{html.escape(message)}</p>'
        '<div class="actions"><button class="primary" id="reenter" type="button">重新进入</button></div>'
        f'<script type="application/json" id="loboo-reentry">{data}</script>'
        f'<script nonce="{nonce}" src="{JWEIXIN}"></script>'
        f'<script nonce="{nonce}">(function(){{'
        "var d=JSON.parse(document.getElementById('loboo-reentry').textContent);"
        'function go(){if(window.wx&&wx.miniProgram){wx.miniProgram.redirectTo({url:d.url});}}'
        "document.getElementById('reenter').addEventListener('click',go);"
        "var key='loboo.sso.retry',now=Date.now(),last=0;"
        'try{last=Number(localStorage.getItem(key))||0;}catch(e){}'
        'if(d.auto&&now-last>30000){try{localStorage.setItem(key,String(now));}catch(e){}go();}'
        '})();</script>'
    )
    return _page('没能进入', body, nonce=nonce, status_code=status_code, wechat=True)
