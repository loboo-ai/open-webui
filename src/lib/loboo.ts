// loboo fork: whichever model answers, the chat presents it as 萝卜哥 with the Loboo logo. The model
// selector keeps the real model names, and hovering 萝卜哥 shows which model it is.
import { WEBUI_BASE_URL } from '$lib/constants';

export const ASSISTANT_NAME = '萝卜哥';
// The site logo, drawn to sit inside a circle: avatars are cropped round.
export const ASSISTANT_AVATAR = `${WEBUI_BASE_URL}/static/favicon.png`;

// The image switch sits in the chat input in place of the integrations menu, which only admins keep.
export const IMAGE_TOGGLE_LABEL = '启用绘图';

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
