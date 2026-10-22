// loboo fork: whichever model answers, the chat presents it as 萝卜哥 with the Loboo logo. The model
// selector keeps the real model names, and hovering 萝卜哥 shows which model it is.
import { WEBUI_BASE_URL } from '$lib/constants';

export const ASSISTANT_NAME = '萝卜哥';
// The site logo, drawn to sit inside a circle: avatars are cropped round.
export const ASSISTANT_AVATAR = `${WEBUI_BASE_URL}/static/favicon.png`;

// The image switch sits in the chat input in place of the integrations menu, which only admins keep.
export const IMAGE_TOGGLE_LABEL = '启用绘图';
