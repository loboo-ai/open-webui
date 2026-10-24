// loboo fork: whichever model answers, the chat presents it as 萝卜哥 with the Loboo logo. The model
// selector keeps the real model names, and hovering 萝卜哥 shows which model it is.
import { WEBUI_BASE_URL } from '$lib/constants';
import type { Model } from '$lib/stores';

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
