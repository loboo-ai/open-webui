"""对话里的联网搜索：所有模型用同一个 search_internet 工具，由 CPA 上的 GPT 原生搜索执行，按本人的 Key 计费。

对话模型只决定搜什么。搜索是对 LOBOO_SEARCH_MODEL 的一次 Responses 请求：带原生 web_search 工具、强制搜索、低推理强度，
把搜到的事实整理成带来源链接的要点，作为工具结果回给对话模型。CPA 会在不支持原生搜索的渠道上去掉 web_search 工具，
这时模型只能凭记忆作答，所以没有真正搜索过的答复一律不用：换一次渠道重试，仍然没有就如实报不可用。
进度、搜过的网站和引用来源用上游现成的 status、source 事件显示在消息上方和下方，不依赖 /v1/alpha/search。
"""

import asyncio
import contextlib
import datetime as dt
import logging
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import HTTPException, Request

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.loboo import config, credentials
from open_webui.models.users import UserModel
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import get_last_user_message
from open_webui.utils.session_pool import get_session

log = logging.getLogger(__name__)

# 成员都在中国：按北京时间理解"今天"，按中国给本地结果。
TIMEZONE = ZoneInfo('Asia/Shanghai')
USER_LOCATION = {'type': 'approximate', 'country': 'CN', 'timezone': 'Asia/Shanghai'}
WEEKDAYS = '一二三四五六日'
# 一次搜索通常 6～26 秒（宽泛的问题会多搜几次、打开网页）。超时、连接断开、上游出错或渠道没有真正搜索时
# 换一次渠道（超时多是某个渠道一时卡住，同一个问题再问通常 20 秒内就有结果），所以最多两次。
ATTEMPTS = 2
ATTEMPT_TIMEOUT_SECONDS = 45
UNAVAILABLE = '联网搜索暂时不可用，请稍后再试。'
THROTTLED = '联网搜索请求太频繁或额度已用完，请稍后再试。'
# 用户的原话一并交给搜索：对话模型不知道今天的日期，转述时会把"明天"写成别的日期。贴了长文的只带开头。
USER_WORDS_LIMIT = 2000

INSTRUCTIONS = (
    '现在是{now}，用户在中国。你在替另一个 AI 助手联网查资料：'
    '先用 web_search 搜索（通常一两次就够），需要核对时再打开网页；'
    '再把与问题直接相关的事实整理成不超过 6 条要点，每条一两句，写明信息的日期，'
    '后面用 Markdown 链接标注来源网页，链接文字写网页标题。'
    '来源说法不一时，以发布时间最新的官方来源为准，并点明分歧。'
    '要查的内容是那个助手转述的，可能写错日期，时间和地点以用户的原话为准。'
    '只依据这次搜到的内容，查不到就直说，不要编造；用提问的语言回答，不寒暄，不给建议。'
)
# 链接地址里可以有一层成对的括号，如维基百科的 /wiki/Foo_(bar)。
_MARKDOWN_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)')


class SearchFailed(Exception):
    """这次没有可用的搜索结果；消息是给对话模型和成员看的原因。"""


@dataclass(frozen=True)
class Findings:
    """一次真正执行过的搜索：整理好的要点，引用的网页（地址、标题、引用它的那一行），以及搜过的其余网页。"""

    text: str
    cited: list[tuple[str, str, str]]
    consulted: list[str]


def tools() -> list:
    """接入了 CPA 时对话里的联网搜索工具；没接入时为空，由上游的 search_web、fetch_url 照旧负责。"""
    return [search_internet] if config.ENABLED and config.LOBOO_CPA_BASE_URL else []


# 函数名就是对话模型看到的工具名：不能叫 web_search，经 CPA 转给 Claude 时它从不调用这个名字的函数（实测 0/12）；
# 也不与上游的 search_web 同名，前端按名字只把这个工具的调用收进状态条。
# docstring 是工具说明，和上游内置工具一样用英文写。
async def search_internet(
    query: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __metadata__: dict = None,
    __messages__: list = None,
) -> str:
    """
    Search the web with ChatGPT search and get checked findings with their sources. Use it when the answer
    depends on information that may be recent or may have changed (news, prices, weather, schedules, releases,
    who holds a role), when you are unsure of a fact, or when the user asks you to look something up, give links
    or read a specific page. Skip it for tasks that need no outside information, such as translation, rewriting
    or math.

    One call is usually enough: it searches several sources and opens pages by itself, and it knows the current
    date and time in China. Put the whole question in one call instead of splitting it into several searches,
    and don't search again to double-check the findings; call again only for something they did not cover.

    :param query: What to find out, as a self-contained question in the user's language, with the place or URL
        it depends on. Keep time words such as "today" or "tomorrow" as the user wrote them; don't add dates.
    :return: Dated findings with Markdown links to their sources; cite the sources you use as Markdown links
    """
    if not query.strip():
        return JSONCodec.dumps({'error': 'query is empty'})
    metadata = __metadata__ or {}
    # 用户这一轮的原话。带了附件或知识库时，上游会把检索到的内容拼进最后一条用户消息，
    # 拼之前的原话存在 base_user_prompt 里。
    user_words = metadata.get('base_user_prompt') or get_last_user_message(__messages__ or [])

    # 状态条一旦显示「搜索中」，每条出路都要收尾：消息里存的最后一个状态没结束，这条消息就一直转圈。
    await _status(__event_emitter__, {'action': 'web_search_queries_generated', 'queries': [query], 'done': False})
    try:
        findings = await search(UserModel(**__user__), query, user_words)
    except asyncio.CancelledError:
        # 成员停止了生成，上游会取消这个任务。收尾只是尽力而为：发状态失败也不能把取消换成别的异常。
        with contextlib.suppress(Exception):
            await _status(__event_emitter__, {'action': 'web_search', 'description': '已停止搜索', 'done': True})
        raise
    except SearchFailed as e:
        failure = str(e)
    except Exception:
        log.exception('loboo: web search failed')
        failure = UNAVAILABLE
    else:
        await _report(__event_emitter__, findings, cite=_citations_enabled(metadata))
        # 搜索时间对对话模型是有用的事实；它对"今天"的认知来自默认系统提示里的日期（deploy/loboo-webui/README.md）。
        return f'联网搜索结果（搜索时间：{_now()}）\n\n{findings.text}'
    await _status(__event_emitter__, {'action': 'web_search', 'description': failure, 'done': True, 'error': True})
    return JSONCodec.dumps({'error': failure}, ensure_ascii=False)


async def search(user: UserModel, query: str, user_words: str | None = None) -> Findings:
    """
    用本人的 Key 让 CPA 上的 GPT 搜索 query；user_words 是用户这一轮的原话，用来纠正转述里的时间和地点。
    拿不到真正搜索过的结果时抛 SearchFailed。
    """
    try:
        key = await credentials.key_for(user)
    except HTTPException as e:
        raise SearchFailed(e.detail) from e

    words = (user_words or '').strip()[:USER_WORDS_LIMIT]
    question = f'用户的原话：{words}\n\n要查的：{query}' if words and words != query.strip() else query
    body = {
        'model': config.LOBOO_SEARCH_MODEL,
        'instructions': INSTRUCTIONS.format(now=_now()),
        'input': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': question}]}],
        'tools': [{'type': 'web_search', 'user_location': USER_LOCATION}],
        'tool_choice': 'required',
        'reasoning': {'effort': 'low'},
        'include': ['web_search_call.action.sources'],
        'store': False,
        'stream': False,
    }
    for attempt in range(1, ATTEMPTS + 1):
        try:
            status, data = await _post(key, body)
        except TimeoutError:
            outcome = f'timed out after {ATTEMPT_TIMEOUT_SECONDS}s'
        except (aiohttp.ClientError, ValueError) as e:
            outcome = type(e).__name__
        else:
            if status in (401, 403, 429):
                log.warning(
                    'loboo: web search for user %s refused by the CPA: HTTP %d %s', user.id, status, _error(data)
                )
                raise SearchFailed(THROTTLED if status == 429 else UNAVAILABLE)
            findings = findings_from(data) if status == 200 else None
            if findings:
                return findings
            outcome = 'no search performed' if status == 200 else f'HTTP {status} {_error(data)}'
        log.warning('loboo: web search attempt %d/%d for user %s failed: %s', attempt, ATTEMPTS, user.id, outcome)
    raise SearchFailed(UNAVAILABLE)


def _error(data) -> str:
    """CPA 错误答复里的原因（OpenAI 格式的 error.message，或 CPA 自己的 error 字符串），记日志用。"""
    error = data.get('error') if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get('message') or error.get('code')
    return str(error)[:200] if error else ''


def findings_from(data) -> Findings | None:
    """
    CPA 答复（Responses 格式）里的搜索结果。有没失败的 web_search_call 且有正文才算真正搜索过，否则为 None；
    答复不合格式也是 None。引用的网页取 url_citation，再加正文里的 Markdown 链接（有的渠道只给后者）。
    """
    try:
        items = data['output']
        calls = [item for item in items if item['type'] == 'web_search_call' and item.get('status') != 'failed']
        parts = [
            part
            for item in items
            if item['type'] == 'message'
            for part in item['content']
            if part['type'] == 'output_text'
        ]
        text = '\n'.join(part['text'] for part in parts).strip()
        if not calls or not text:
            return None

        cited: dict[str, tuple[str, str]] = {}
        for part in parts:
            for annotation in part.get('annotations') or []:
                url = annotation.get('url') if annotation.get('type') == 'url_citation' else None
                if isinstance(url, str) and url.startswith(('http://', 'https://')):
                    url, line = _untracked(url), _line(part['text'], annotation.get('start_index') or 0)
                    cited.setdefault(url, (annotation.get('title') or url, line))
            for match in _MARKDOWN_LINK.finditer(part['text']):
                cited.setdefault(_untracked(match.group(2)), (match.group(1), _line(part['text'], match.start())))
        consulted = []
        for call in calls:
            for source in (call.get('action') or {}).get('sources') or []:
                url = source.get('url')
                if isinstance(url, str) and url.startswith(('http://', 'https://')):
                    consulted.append(_untracked(url))
    except (KeyError, TypeError, AttributeError):
        return None
    return Findings(
        text=text,
        cited=[(url, title, line) for url, (title, line) in cited.items()],
        consulted=[url for url in dict.fromkeys(consulted) if url not in cited],
    )


def _untracked(url: str) -> str:
    """
    去掉链接的 utm_source（OpenAI 搜索给引用加了 utm_source=openai），同一网页在引用和正文里才对得上。
    只按原文删掉这一项，不解码再编码查询串：GBK 编码的中文查询、没有值的参数都要原样保留。
    """
    base, question, rest = url.partition('?')
    query, hash_mark, fragment = rest.partition('#')
    if not question or 'utm_source=' not in query:
        return url
    kept = '&'.join(pair for pair in query.split('&') if not pair.startswith('utm_source='))
    return f'{base}?{kept}{hash_mark}{fragment}' if kept else f'{base}{hash_mark}{fragment}'


async def _post(key: str, body: dict) -> tuple[int, object]:
    session = await get_session()
    async with session.post(
        f'{config.LOBOO_CPA_BASE_URL}/responses',
        json=body,
        headers={'Authorization': f'Bearer {key}'},
        timeout=aiohttp.ClientTimeout(total=ATTEMPT_TIMEOUT_SECONDS),
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    ) as response:
        return response.status, await response.json(content_type=None)


def _now() -> str:
    now = dt.datetime.now(TIMEZONE)
    return f'北京时间 {now:%Y-%m-%d %H:%M}（星期{WEEKDAYS[now.weekday()]}）'


def _line(text: str, index: int) -> str:
    """text 里 index 所在的那一行：引用来源的摘录，在来源详情里显示。"""
    start = text.rfind('\n', 0, index) + 1
    end = text.find('\n', index)
    return text[start : end if end != -1 else len(text)].strip()


def _citations_enabled(metadata: dict) -> bool:
    """模型关掉了引用能力（citations）时不发引用来源，和上游给工具结果发来源的规则一样，默认开。"""
    model = metadata.get('model') or {}
    return (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('citations', True)


async def _report(emitter, findings: Findings, *, cite: bool) -> None:
    """把状态条收尾为搜过的网站（引用的在前）；cite 时引用的网页另作引用来源。"""
    sites = [{'title': title, 'link': url} for url, title, _ in findings.cited] + [
        {'link': url} for url in findings.consulted
    ]
    if sites:
        # 上游的状态条把这句翻成「已搜索 N 个网站」，并列出这些网站。
        searched = {'description': 'Searched {{count}} sites', 'urls': [site['link'] for site in sites], 'items': sites}
    else:
        # 天气、行情这类搜索的数据不来自网页，没有可列的网站。
        searched = {'description': '已联网搜索'}
    await _status(emitter, {'action': 'web_search', 'done': True, **searched})
    if not (emitter and cite):
        return
    for url, title, line in findings.cited:
        await emitter(
            {
                'type': 'source',
                'data': {
                    'source': {'name': title, 'url': url},
                    'document': [line or title],
                    'metadata': [{'source': url, 'name': title}],
                },
            }
        )


async def _status(emitter, data: dict) -> None:
    # 没有对话会话（直接调 API）时没有 emitter，状态无处显示。
    if emitter:
        await emitter({'type': 'status', 'data': data})
