<script lang="ts">
	// loboo fork: what a message shows in a small box, over the whole screen: the web page a tool returned
	// (FullHeightIframe) or a file's preview (ToolOutputFile). The content gets a box of fixed height to fill. The
	// close button is always there: Esc doesn't reach this page while the focus is inside a frame.
	import { getContext } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	export let show = false;
	export let title = '';

	const i18n: any = getContext('i18n');
</script>

<Modal
	bind:show
	size="full"
	ariaLabel={title || $i18n.t('Preview')}
	containerClassName="p-2 sm:p-6"
	className="bg-white dark:bg-gray-900 rounded-2xl overflow-hidden"
>
	<div class="flex h-[calc(100dvh-1rem-2px)] sm:h-[calc(100dvh-3rem-2px)] flex-col">
		<div class="flex shrink-0 items-center gap-2 px-4 py-2">
			{#if title}
				<h2 class="min-w-0 truncate text-sm font-medium text-gray-700 dark:text-gray-200">
					{title}
				</h2>
			{/if}
			<button
				type="button"
				class="ml-auto rounded-lg p-1 text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
				aria-label={$i18n.t('Close')}
				on:click={() => (show = false)}
			>
				<XMark className="size-5" />
			</button>
		</div>
		<div class="min-h-0 flex-1">
			<slot />
		</div>
	</div>
</Modal>
