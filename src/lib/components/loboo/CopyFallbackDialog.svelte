<script lang="ts">
	// loboo fork: when the WeChat mini program refuses the clipboard, the link, address or number is shown here to be
	// copied by hand (opened and closed by loboo.ts).
	import Modal from '$lib/components/common/Modal.svelte';

	export let title: string;
	export let hint: string;
	export let text: string;
	export let onClose: () => void;

	let show = true;
	$: if (!show) onClose();
</script>

<Modal bind:show size="sm">
	<div class="flex flex-col px-5 py-5">
		<div class="mb-2.5 text-sm font-medium dark:text-gray-200">{title}</div>
		<div class="text-sm text-gray-500">{hint}</div>
		<div
			class="mt-3 select-all break-all rounded-lg bg-gray-50 px-3 py-2 font-mono text-sm text-gray-800 dark:bg-gray-850 dark:text-gray-200"
		>
			{text}
		</div>
		<div class="mt-5 flex justify-end">
			<button
				class="flex h-7 items-center justify-center rounded-lg bg-gray-900 px-3 text-xs font-normal text-white transition hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
				on:click={() => (show = false)}
			>
				关闭
			</button>
		</div>
	</div>
</Modal>
