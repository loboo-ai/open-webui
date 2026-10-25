// loboo fork: whichever model answers, the chat presents it as 萝卜哥 with the Loboo logo. The model
// selector keeps the real model names, and hovering 萝卜哥 shows which model it is.
import { mount, unmount } from 'svelte';
import { get } from 'svelte/store';
import { toast } from 'svelte-sonner';
import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
import { terminalServers, type Model } from '$lib/stores';
import type { TerminalConnection, TerminalSkill } from '$lib/apis/terminal';
import { copyToClipboard } from '$lib/utils';

export const ASSISTANT_NAME = '萝卜哥';
// The site logo, drawn to sit inside a circle: avatars are cropped round.
export const ASSISTANT_AVATAR = `${WEBUI_BASE_URL}/static/favicon.png`;

// The image switch sits in the chat input in place of the integrations menu, which only admins keep.
export const IMAGE_TOGGLE_LABEL = '启用绘图';

// 萝卜云 is the terminal the selected models share as their default (Model Defaults give every model the same one): a
// cloud computer per person. The cloud switch beside 临时对话 turns it on and off, and each new chat starts with it on
// (Chat.svelte setDefaults). Who may use it is decided by the connection's access grants; conditions on offering the
// switch belong here.
type TerminalServer = { id?: string; name?: string };

export const chatCloudTerminal = (
	modelIds: string[],
	models: Model[],
	terminalServers: TerminalServer[] | null
) => {
	const ids = new Set(
		modelIds.map((id) => models.find((model) => model.id === id)?.info?.meta?.terminalId)
	);
	if (ids.size !== 1) return null;
	return (
		(terminalServers ?? []).find(
			(t): t is TerminalServer & { id: string } => !!t.id && ids.has(t.id)
		) ?? null
	);
};

// Web search reports its progress and the sites it read in the status line above the reply (status events from
// backend loboo/web_search.py), so its tool calls are not shown again as blocks inside the reply.
export const STATUS_ONLY_TOOLS = new Set(['search_internet']);

// Site icons come straight from each site: the google.com favicon service upstream uses is unreachable from
// mainland China.
export const faviconUrl = (link: string): string => {
	try {
		return `${new URL(link).origin}/favicon.ico`;
	} catch {
		return '';
	}
};

// ── 萝卜云 asleep or awake ────────────────────────────────────────────────────────────────────────────────────────
// Each person's cloud computer sleeps when nobody uses it, and anything that reaches it wakes it. So the file panel
// (components/loboo/CloudGate.svelte), the file cards in replies and the / and $ menus first ask whether it is asleep.
// Only a system terminal can be 萝卜云, and only the backend knows which one is (an orchestrator connection): it
// answers unknown for every other terminal and whenever it cannot tell, and unknown leaves everything as it was.
export type CloudState = 'running' | 'sleeping' | 'unknown';

export const mayBeCloudTerminal = (
	terminalId: string | null,
	terminalServers: TerminalServer[] | null
): terminalId is string => !!terminalId && (terminalServers ?? []).some((t) => t.id === terminalId);

const parseCloudState = (body: unknown): CloudState => {
	const state = typeof body === 'object' && body !== null && 'state' in body ? body.state : null;
	return state === 'running' || state === 'sleeping' ? state : 'unknown';
};

// As long as the backend keeps the orchestrator's answer; a reply with several file cards asks once.
const CLOUD_STATE_TTL_MS = 10_000;
const cloudStates = new Map<string, { at: number; state: Promise<CloudState> }>();

export const getCloudState = (
	token: string,
	terminalId: string | null,
	{ fresh = false } = {}
): Promise<CloudState> => {
	if (!mayBeCloudTerminal(terminalId, get(terminalServers))) return Promise.resolve('unknown');
	const cached = cloudStates.get(terminalId);
	if (!fresh && cached && Date.now() - cached.at < CLOUD_STATE_TTL_MS) return cached.state;

	const state = fetch(
		`${WEBUI_API_BASE_URL}/loboo/cloud/state?terminal_id=${encodeURIComponent(terminalId)}`,
		{ headers: { Accept: 'application/json', authorization: `Bearer ${token}` } }
	)
		.then(
			async (res): Promise<CloudState> => (res.ok ? parseCloudState(await res.json()) : 'unknown')
		)
		.catch((): CloudState => 'unknown');
	cloudStates.set(terminalId, { at: Date.now(), state });
	return state;
};

// Writes the member's loboo-cli key into it, which starts it.
export const wakeCloud = async (token: string, terminalId: string): Promise<boolean> => {
	const woken = await fetch(`${WEBUI_API_BASE_URL}/loboo/cloud/wake`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ terminal_id: terminalId })
	})
		.then(async (res) => {
			const body: unknown = res.ok ? await res.json() : null;
			return typeof body === 'object' && body !== null && 'ok' in body && body.ok === true;
		})
		.catch(() => false);
	if (woken) cloudStates.set(terminalId, { at: Date.now(), state: Promise.resolve('running') });
	return woken;
};

// File cards in replies (Messages/TerminalOutputFile.svelte) show their preview only after a click while it sleeps.
export const PREVIEW_ASLEEP_TEXT = '预览需要唤醒萝卜云';
export const LOAD_PREVIEW_LABEL = '加载预览';

// The / and $ menus list the terminal's skills (apis/terminal listTerminalSkills). While 萝卜云 sleeps they show the
// list it gave last instead of waking it; until it has given one since the page loaded, that list is empty.
const lastTerminalSkills = new Map<string, TerminalSkill[]>();

export const terminalSkillsWhileAsleep = async (
	connection: TerminalConnection
): Promise<TerminalSkill[] | null> =>
	connection.system && (await getCloudState(connection.key, connection.selector)) === 'sleeping'
		? (lastTerminalSkills.get(connection.selector) ?? [])
		: null;

export const rememberTerminalSkills = (selector: string, skills: TerminalSkill[]) => {
	lastTerminalSkills.set(selector, skills);
	return skills;
};

// ── Links inside the WeChat mini program ──────────────────────────────────────────────────────────────────────────
// Its web-view opens no other site, no new window and no download, and a link that tries just does nothing. So there
// a link to another site, a mailto: or a tel: is copied for the member to paste elsewhere, and downloads and this
// site's files (they need this site's cookie, so a copied address would not open either) say they cannot be had here.
// src/app.html marks the mini program with the mini-program class on <html> before anything runs.
export const isMiniProgram = () => document.documentElement.classList.contains('mini-program');

// The rules in order, the first match wins (docs/loboo-cloud-plan-2026-10-25.md 3.6). Clicks with another button or
// a modifier key, and clicks inside editable text, are left alone before a link gets here.
export type LinkRule =
	| { rule: 'R0' } // left alone: a #fragment, a scheme the web-view handles itself (or not at all)
	| { rule: 'R1' } // a download
	| { rule: 'R2'; text: string } // mailto:, text is the address
	| { rule: 'R3'; text: string } // tel:, text is the number
	| { rule: 'R4'; text: string } // another site, text is the full address
	| { rule: 'R5' } // a file of this site
	| { rule: 'R6'; path: string } // a page of this site, asked for in a new window
	| { rule: 'R7' }; // a page of this site

const SITE_FILE_PREFIXES = ['/api/', '/cache/', '/static/', '/assets/'];
const SAME_WINDOW_TARGETS = new Set(['_self', '_top', '_parent']);

const decodeLinkPart = (part: string) => {
	try {
		return decodeURIComponent(part);
	} catch {
		return part;
	}
};

export const classifyLink = (
	href: string,
	{
		origin,
		base = origin,
		target = '',
		download = false
	}: { origin: string; base?: string; target?: string; download?: boolean }
): LinkRule => {
	const raw = href.trim();
	if (!raw || raw.startsWith('#')) return { rule: 'R0' };
	let url: URL;
	try {
		url = new URL(raw, base);
	} catch {
		return { rule: 'R0' };
	}
	if (download || url.protocol === 'blob:' || url.protocol === 'data:') return { rule: 'R1' };
	// The pathname leaves out ?subject= and the like.
	if (url.protocol === 'mailto:') return { rule: 'R2', text: decodeLinkPart(url.pathname) };
	if (url.protocol === 'tel:') return { rule: 'R3', text: decodeLinkPart(url.pathname) };
	if (url.protocol !== 'http:' && url.protocol !== 'https:') return { rule: 'R0' };
	if (url.origin !== origin) return { rule: 'R4', text: url.href };
	if (SITE_FILE_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) return { rule: 'R5' };
	if (target && !SAME_WINDOW_TARGETS.has(target)) {
		return { rule: 'R6', path: url.pathname + url.search + url.hash };
	}
	return { rule: 'R7' };
};

type CopiedKind = 'link' | 'email' | 'phone';
const COPIED: Record<CopiedKind, { toast: string; title: string; noun: string }> = {
	link: { toast: '已复制链接，请粘贴到浏览器打开', title: '复制链接', noun: '链接' },
	email: { toast: '已复制邮箱地址', title: '复制邮箱地址', noun: '邮箱地址' },
	phone: { toast: '已复制电话号码', title: '复制电话号码', noun: '电话号码' }
};

const NOT_HERE = {
	download: ['小程序里不能下载文件', '请在电脑上打开萝卜，在首页点「ChatGPT」进入后下载'],
	file: ['小程序里打不开文件', '请在电脑上打开萝卜，在首页点「ChatGPT」进入后查看']
} as const;

const shortLink = (link: string) => {
	const chars = Array.from(link.replace(/^https?:\/\//, ''));
	return chars.length > 40 ? `${chars.slice(0, 39).join('')}…` : chars.join('');
};

// One toast per kind: a second click replaces it.
const showNotHere = (kind: keyof typeof NOT_HERE) => {
	const [title, description] = NOT_HERE[kind];
	toast.info(title, { id: `loboo-mp-${kind}`, description, duration: 6000 });
};

let copyFallback: ReturnType<typeof mount> | null = null;

const closeCopyFallback = () => {
	if (copyFallback) void unmount(copyFallback);
	copyFallback = null;
};

// The web-view may refuse the clipboard; then the member copies by hand from a dialog.
const openCopyFallback = async (kind: CopiedKind, text: string) => {
	const { default: CopyFallbackDialog } =
		await import('$lib/components/loboo/CopyFallbackDialog.svelte');
	closeCopyFallback();
	const { title, noun } = COPIED[kind];
	const dialog = mount(CopyFallbackDialog, {
		target: document.body,
		props: {
			title,
			hint: `没能自动复制。请长按下面的${noun}选中后复制${kind === 'link' ? '，再粘贴到浏览器打开' : ''}。`,
			text,
			// Called from the dialog's own update, so it is taken down after that update finishes.
			onClose: () =>
				setTimeout(() => {
					if (copyFallback === dialog) closeCopyFallback();
				})
		}
	});
	copyFallback = dialog;
};

// Must be called while the click is being handled: the clipboard takes writes only during the member's gesture, so
// nothing is awaited before copyToClipboard starts.
const copyOut = async (kind: CopiedKind, text: string) => {
	if (!(await copyToClipboard(text))) return openCopyFallback(kind, text);
	toast.success(COPIED[kind].toast, {
		id: `loboo-mp-${kind}`,
		description: kind === 'link' ? shortLink(text) : text,
		duration: 4000
	});
};

const blockLink = (link: LinkRule) => {
	if (link.rule === 'R1') showNotHere('download');
	else if (link.rule === 'R2') void copyOut('email', link.text);
	else if (link.rule === 'R3') void copyOut('phone', link.text);
	else if (link.rule === 'R4') void copyOut('link', link.text);
	else if (link.rule === 'R5') showNotHere('file');
};

// Runs before every other click handler and never stops the click: the chat's own link handling
// (MarkdownInlineTokens handleLinkClick) and SvelteKit's router still see it.
const onLinkClick = (event: MouseEvent) => {
	if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
		return;
	const anchor = event.target instanceof Element ? event.target.closest('a[href]') : null;
	if (!(anchor instanceof HTMLAnchorElement) || anchor.isContentEditable) return;
	const link = classifyLink(anchor.getAttribute('href') ?? '', {
		origin: location.origin,
		base: document.baseURI,
		target: anchor.target,
		download: anchor.hasAttribute('download')
	});
	if (link.rule === 'R6') {
		anchor.removeAttribute('target');
	} else if (link.rule !== 'R0' && link.rule !== 'R7') {
		event.preventDefault();
		blockLink(link);
	}
};

// What a blocked window.open returns. Callers only touch these members (common/FileItem.svelte calls .focus() on the
// result, so null would throw); this is the one place the stand-in is passed off as a Window.
const CLOSED_WINDOW = {
	closed: true,
	focus() {},
	blur() {},
	close() {},
	postMessage() {}
} as unknown as Window;

// file-saver (saveAs), jsPDF and the hand-written downloads all end by clicking an <a> that is not in the page, which
// the click listener never sees. One in the page goes through the listener (R1).
const isDetachedDownload = (anchor: HTMLAnchorElement) =>
	!anchor.isConnected &&
	(anchor.hasAttribute('download') || anchor.protocol === 'blob:' || anchor.protocol === 'data:');

let linksInstalled = false;

export const installMiniProgramLinks = () => {
	if (linksInstalled || !isMiniProgram()) return;
	linksInstalled = true;

	window.addEventListener('click', onLinkClick, true);

	const nativeOpen = window.open.bind(window);
	window.open = (url?: string | URL, target?: string, features?: string) => {
		const href = url === undefined ? '' : String(url);
		if (!href || (target && SAME_WINDOW_TARGETS.has(target))) {
			return nativeOpen(url, target, features);
		}
		const link = classifyLink(href, {
			origin: location.origin,
			base: document.baseURI,
			target: target || '_blank'
		});
		if (link.rule === 'R0' || link.rule === 'R7') return nativeOpen(url, target, features);
		if (link.rule === 'R6') void import('$app/navigation').then(({ goto }) => goto(link.path));
		else blockLink(link);
		return CLOSED_WINDOW;
	};

	const nativeClick = HTMLAnchorElement.prototype.click;
	const nativeDispatchEvent = HTMLAnchorElement.prototype.dispatchEvent;
	HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
		if (isDetachedDownload(this)) showNotHere('download');
		else nativeClick.call(this);
	};
	HTMLAnchorElement.prototype.dispatchEvent = function (this: HTMLAnchorElement, event: Event) {
		if (event.type !== 'click' || !isDetachedDownload(this)) {
			return nativeDispatchEvent.call(this, event);
		}
		showNotHere('download');
		return false;
	};
};

// For downloads that fetch the file first: say so before fetching it.
export const blockDownloadInMiniProgram = (): boolean => {
	if (!isMiniProgram()) return false;
	showNotHere('download');
	return true;
};
