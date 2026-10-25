// loboo fork: whichever model answers, the chat presents it as 萝卜哥 with the Loboo logo. The model
// selector keeps the real model names, and hovering 萝卜哥 shows which model it is.
import { get } from 'svelte/store';
import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
import { terminalServers, type Model } from '$lib/stores';
import type { TerminalConnection, TerminalSkill } from '$lib/apis/terminal';

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
