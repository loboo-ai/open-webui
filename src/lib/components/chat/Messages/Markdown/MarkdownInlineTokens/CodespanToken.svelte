<script lang="ts">
	import { copyToClipboard, unescapeHtml } from '$lib/utils';
	import { toast } from 'svelte-sonner';

	import { getContext } from 'svelte';

	import { settings } from '$lib/stores';

	const i18n = getContext('i18n');

	export let token;
	export let done = true;
</script>

<!-- svelte-ignore a11y-click-events-have-key-events -->
<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
<code
	class="codespan cursor-pointer {!done && ($settings?.chatFadeStreamingText ?? true)
		? 'fade-in-token'
		: ''}"
	on:click={async () => {
		// loboo fork: only claim the copy when it happened (Android WebViews can refuse it).
		if (await copyToClipboard(unescapeHtml(token.text))) {
			toast.success($i18n.t('Copied to clipboard'));
		}
	}}>{unescapeHtml(token.text)}</code
>
