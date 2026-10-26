"""对话里的联网搜索：所有模型用同一个 search_internet 工具，由 CPA 上的 GPT 原生搜索执行，按本人的 Key 计费。

对话模型只决定搜什么。搜索是对 LOBOO_SEARCH_MODEL 的一次 Responses 请求：带原生 web_search 工具、强制搜索、低推理强度，
把搜到的事实整理成带来源链接的要点，作为工具结果回给对话模型。CPA 会在不支持原生搜索的渠道上去掉 web_search 工具，
这时模型只能凭记忆作答，所以没有真正搜索过的答复一律不用：换一次渠道重试，仍然没有就如实报不可用。
请求是流式的：搜索端每搜一次、打开一个网页都有事件，据此把进度显示在状态条上，也据此分辨渠道卡住和查得久。
一次调用可以带几个互不相关的问题（如每类新闻一个），同时搜，没查到的单独写明，对话模型只需重问那几个。
进度、搜过的网站和引用来源用上游现成的 status、source 事件显示在消息上方和下方，不依赖 /v1/alpha/search。
"""

import asyncio
import contextlib
import datetime as dt
import logging
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit
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
# 普通问题一次搜索 6～26 秒；一次查一批新闻的要搜四五次、打开几个网页，再花半分钟写要点，1～2 分钟很正常，
# 但事件之间最长也就几秒。所以按进度判断渠道卡住：开始有事件后 IDLE_TIMEOUT_SECONDS 内没有新事件就换一次渠道；
# 连接断开、上游出错或渠道没有真正搜索时也换，最多两次。第一个事件之前 CPA 可能还在自己换凭据（Zeu 的 Cloudflare 504
# 要等 60 秒才回），这段给 FIRST_EVENT_TIMEOUT_SECONDS，让它换完；提前断开在 CPA 看来是客户端取消，不会让坏渠道冷却。
# 整次搜索（含重试）最多等 SEARCH_DEADLINE_SECONDS，一直有进展却超过它的，重试也只会再花一样久，
# 直接请对话模型把问题拆小。
ATTEMPTS = 2
FIRST_EVENT_TIMEOUT_SECONDS = 90
IDLE_TIMEOUT_SECONDS = 30
SEARCH_DEADLINE_SECONDS = 180
UNAVAILABLE = '联网搜索暂时不可用，请稍后再试。'
THROTTLED = '联网搜索请求太频繁或额度已用完，请稍后再试。'
TOO_LONG = f'联网搜索 {SEARCH_DEADLINE_SECONDS // 60} 分钟内没有查完，请把问题拆成几个小问题分别搜索。'
# 一次调用里同时搜的问题最多这么多个，工具说明里的 "at most 5" 跟它一致。再多会顶到上游的并发限制
# （Zeu 晚高峰对排队太多的请求回 429）。
MAX_QUERIES = 5
# 用户的原话一并交给搜索：对话模型不知道今天的日期，转述时会把"明天"写成别的日期。贴了长文的只带开头。
USER_WORDS_LIMIT = 2000

INSTRUCTIONS = (
    '现在是{now}，用户在中国。你在替另一个 AI 助手联网查资料：'
    '先用 web_search 搜索（通常一两次就够），需要核对时再打开网页；'
    '再把与问题直接相关的事实整理成要点：要求了条数就照给（最多 15 条），没说条数时不超过 6 条；'
    '每条一两句，写明信息的日期，'
    '后面用 Markdown 链接标注来源网页，链接文字写网页标题。'
    '来源说法不一时，以发布时间最新的官方来源为准，并点明分歧。'
    '要查的内容是那个助手转述的，可能写错日期，时间和地点以用户的原话为准；'
    '用户的原话里可能还有别的问题，只查「要查的」这一项。'
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
    queries: list[str],
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

    Each question in `queries` is searched on its own and all of them run at the same time, so put one question
    per independent topic in a single call (for example one per news category, city or product), at most 5; a
    simple question needs just one. Each search covers several sources, opens pages by itself and knows the
    current date and time in China. Don't search again to double-check the findings; call again only for what
    they did not cover, such as a question reported as not found.

    :param queries: The questions to find out, each self-contained in the user's language, with the place, time
        window or URL it depends on. Keep time words such as "today" or "tomorrow" as the user wrote them; don't
        add dates.
    :return: Dated findings with Markdown links to their sources, one section per question when there are
        several; cite the sources you use as Markdown links
    """
    # 参数是对话模型给的，上游只按名字过滤、不校验类型，偶尔会把单个问题直接写成字符串。
    if isinstance(queries, str):
        queries = [queries]
    # 前端按问题给状态条上的每一项做 key，重复的要去掉。
    queries = list(dict.fromkeys(q.strip() for q in queries or [] if isinstance(q, str) and q.strip()))
    if not queries:
        return JSONCodec.dumps({'error': 'queries is empty'})
    queries, skipped = queries[:MAX_QUERIES], queries[MAX_QUERIES:]
    metadata = __metadata__ or {}
    # 用户这一轮的原话，取前端发来的那条用户消息：发给模型的最后一条用户消息里，上游会在前面加附件标签
    # （<attached_files>，带图片时它是第一段文字，base_user_prompt 取到的就只有它），带知识库时还拼了检索内容。
    # 只有直接调 API、没有这条消息时才退回最后一条用户消息。
    user_message = metadata.get('user_message')
    user_words = user_message.get('content') if user_message else get_last_user_message(__messages__ or [])
    user = UserModel(**__user__)
    # 只有一个问题时跟着搜索端一步步显示；几个问题同时搜时每步都显示太乱（每条状态还要写一次聊天记录），
    # 只报查完了几个。
    progress = (lambda data: _status(__event_emitter__, data)) if len(queries) == 1 else None
    finished = 0

    async def look_up(query: str) -> Findings | str:
        nonlocal finished
        outcome = await _look_up(user, query, user_words, progress)
        if len(queries) > 1:
            finished += 1
            description = f'已查完 {finished}/{len(queries)} 个问题'
            await _status(__event_emitter__, {'action': 'web_search', 'description': description, 'done': False})
        return outcome

    # 状态条一旦显示「搜索中」，每条出路都要收尾：消息里存的最后一个状态没结束，这条消息就一直转圈。
    await _status(__event_emitter__, {'action': 'web_search_queries_generated', 'queries': queries, 'done': False})
    try:
        outcomes = await asyncio.gather(*(look_up(query) for query in queries))
    except asyncio.CancelledError:
        # 成员停止了生成，上游会取消这个任务。收尾只是尽力而为：发状态失败也不能把取消换成别的异常。
        with contextlib.suppress(Exception):
            await _status(__event_emitter__, {'action': 'web_search', 'description': '已停止搜索', 'done': True})
        raise
    found = [outcome for outcome in outcomes if isinstance(outcome, Findings)]
    if not found:
        reasons = set(outcomes)
        failure = reasons.pop() if len(reasons) == 1 else UNAVAILABLE
        await _status(__event_emitter__, {'action': 'web_search', 'description': failure, 'done': True, 'error': True})
        return JSONCodec.dumps({'error': failure}, ensure_ascii=False)
    await _report(__event_emitter__, found, cite=_citations_enabled(metadata))
    return _result_text(queries, outcomes, skipped)


async def _look_up(user: UserModel, query: str, user_words: str | None, progress) -> Findings | str:
    """一个问题：搜到的结果，或给对话模型看的没查到的原因。"""
    try:
        return await search(user, query, user_words, progress)
    except SearchFailed as e:
        return str(e)
    except Exception:
        log.exception('loboo: web search failed')
        return UNAVAILABLE


def _result_text(queries: list[str], outcomes: list[Findings | str], skipped: list[str]) -> str:
    """给对话模型的结果：一个问题时就是要点；几个问题时每个问题一节，没查到的写明原因，它只需重问那几个。"""
    # 搜索时间对对话模型是有用的事实；它对"今天"的认知来自默认系统提示里的日期（deploy/loboo-webui/README.md）。
    head = f'联网搜索结果（搜索时间：{_now()}）'
    if len(queries) == 1:
        return f'{head}\n\n{outcomes[0].text}'
    sections = [
        f'### {query}\n{outcome.text if isinstance(outcome, Findings) else f"没有查到：{outcome}"}'
        for query, outcome in zip(queries, outcomes)
    ]
    if skipped:
        sections.append(f'下面的问题超过一次最多 {MAX_QUERIES} 个，没有查，需要时再调用一次：' + '；'.join(skipped))
    return '\n\n'.join([head, *sections])


async def search(user: UserModel, query: str, user_words: str | None = None, progress=None) -> Findings:
    """
    用本人的 Key 让 CPA 上的 GPT 搜索 query；user_words 是用户这一轮的原话，用来纠正转述里的时间和地点。
    progress 是异步回调，收到要显示在状态条上的进度（上游 status 事件的 data）。
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
        'stream': True,
    }
    deadline = asyncio.get_running_loop().time() + SEARCH_DEADLINE_SECONDS
    for attempt in range(1, ATTEMPTS + 1):
        if attempt > 1 and progress:
            await progress({'action': 'web_search', 'description': '这次没有搜成，换个渠道重新搜索', 'done': False})
        outcome = await _attempt(user, key, body, deadline, progress)
        if isinstance(outcome, Findings):
            return outcome
        log.warning('loboo: web search attempt %d/%d for user %s failed: %s', attempt, ATTEMPTS, user.id, outcome)
    raise SearchFailed(UNAVAILABLE)


async def _attempt(user: UserModel, key: str, body: dict, deadline: float, progress) -> Findings | str:
    """一次搜索请求：真正搜索过的结果，或值得换渠道再试的失败原因（记日志用）；不该再试时抛 SearchFailed。"""
    try:
        async with asyncio.timeout_at(deadline):
            # 每次尝试都是一个新会话：CPA 按请求开头把相同的请求粘在同一个渠道上（会话粘性），
            # 不带自己的 prompt_cache_key，重试就会回到刚失败的渠道。
            data = await _stream(key, {**body, 'prompt_cache_key': uuid.uuid4().hex}, progress)
    except _AttemptFailed as e:
        if e.status in (401, 403, 429):
            log.warning('loboo: web search for user %s refused by the CPA: %s', user.id, e)
            raise SearchFailed(THROTTLED if e.status == 429 else UNAVAILABLE) from e
        return str(e)
    except TimeoutError:
        # 没有进展的超时 _stream 已经换成了 _AttemptFailed，到这里的只有整次搜索的期限。
        log.warning('loboo: web search for user %s not done in %ds', user.id, SEARCH_DEADLINE_SECONDS)
        raise SearchFailed(TOO_LONG) from None
    except (aiohttp.ClientError, ValueError) as e:
        return type(e).__name__
    return findings_from(data) or 'no search performed'


class _AttemptFailed(Exception):
    """这一次请求没拿到完整的搜索结果；status 是 CPA 回的 HTTP 状态码，流里出的错为 None。"""

    def __init__(self, outcome: str, status: int | None = None):
        super().__init__(outcome)
        self.status = status


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


async def _stream(key: str, body: dict, progress) -> dict:
    """
    发一次流式搜索请求，返回最终的 Responses 对象；搜索端的每一步换成状态交给 progress。
    没有进展（第一个事件迟迟不来、两个事件之间隔太久）和其余失败都抛 _AttemptFailed。
    """
    session = await get_session()
    started = False
    try:
        async with asyncio.timeout(FIRST_EVENT_TIMEOUT_SECONDS) as idle:
            async with session.post(
                f'{config.LOBOO_CPA_BASE_URL}/responses',
                json=body,
                headers={'Authorization': f'Bearer {key}'},
                # 时限只由这里的空闲计时和 search 的整次期限管，不用共享会话默认的总超时。
                timeout=aiohttp.ClientTimeout(),
                # 最后的 response.completed 一行带着全部输出和来源，大的查询有几十 KB；
                # 按行读的上限是缓冲的两倍，默认只有 128 KB。
                read_bufsize=2**20,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                if response.status != 200:
                    data = await response.json(content_type=None)
                    raise _AttemptFailed(f'HTTP {response.status} {_error(data)}', response.status)
                async for event in _data_events(response):
                    started = True
                    idle.reschedule(asyncio.get_running_loop().time() + IDLE_TIMEOUT_SECONDS)
                    kind = event.get('type')
                    if kind in ('response.completed', 'response.incomplete'):
                        return event['response']
                    if kind in ('response.failed', 'error'):
                        raise _AttemptFailed(f'{kind} {_error(event.get("response") or event)}')
                    if progress and (status := _progress(event)):
                        await progress(status)
    except TimeoutError:
        if not idle.expired():
            raise
        waited = IDLE_TIMEOUT_SECONDS if started else FIRST_EVENT_TIMEOUT_SECONDS
        raise _AttemptFailed(f'no {"new" if started else "first"} event in {waited}s') from None
    raise _AttemptFailed('stream ended before completion')


async def _data_events(response):
    """流里的 data 事件。CPA 在没有输出时每 15 秒发一行 keep-alive 注释，那不是进度。"""
    async for line in response.content:
        if line.startswith(b'data:'):
            yield JSONCodec.loads(line[5:])


def _progress(event: dict) -> dict | None:
    """搜索端完成的一步换成状态条上的一条状态：搜了什么、在读哪个网站、开始整理结果；其余事件为 None。"""
    item = event.get('item') or {}
    if event.get('type') == 'response.output_item.done' and item.get('type') == 'web_search_call':
        action = item.get('action') or {}
        if action.get('type') == 'search':
            # 前端按搜索词给每一项做 key，重复的词要去掉。
            queries = list(dict.fromkeys(q for q in action.get('queries') or [action.get('query')] if q))
            return {'action': 'web_search_queries_generated', 'queries': queries, 'done': False} if queries else None
        host = urlsplit(action.get('url') or '').hostname
        return {'action': 'web_search', 'description': f'正在阅读 {host}', 'done': False} if host else None
    if event.get('type') == 'response.output_item.added' and item.get('type') == 'message':
        return {'action': 'web_search', 'description': '正在整理搜索结果', 'done': False}
    return None


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


async def _report(emitter, findings: list[Findings], *, cite: bool) -> None:
    """把状态条收尾为这次各个问题搜过的网站（引用的在前，同一网页只列一次）；cite 时引用的网页另作引用来源。"""
    cited: dict[str, tuple[str, str]] = {}
    for found in findings:
        for url, title, line in found.cited:
            cited.setdefault(url, (title, line))
    consulted = [url for url in dict.fromkeys(url for found in findings for url in found.consulted) if url not in cited]
    sites = [{'title': title, 'link': url} for url, (title, _) in cited.items()] + [{'link': url} for url in consulted]
    if sites:
        # 上游的状态条把这句翻成「已搜索 N 个网站」，并列出这些网站。
        searched = {'description': 'Searched {{count}} sites', 'urls': [site['link'] for site in sites], 'items': sites}
    else:
        # 天气、行情这类搜索的数据不来自网页，没有可列的网站。
        searched = {'description': '已联网搜索'}
    await _status(emitter, {'action': 'web_search', 'done': True, **searched})
    if not (emitter and cite):
        return
    for url, (title, line) in cited.items():
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
