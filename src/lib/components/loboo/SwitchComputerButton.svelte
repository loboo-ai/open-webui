<script lang="ts">
	// loboo fork: only there inside Loboo's 我的电脑, where this site is its 云端电脑 (src/lib/loboo.ts). The list of
	// computers is that page's, so the button only asks it to open its menu here.
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Computer from '$lib/components/icons/Computer.svelte';
	import { embedHost, SWITCH_COMPUTER_LABEL, tellHost } from '$lib/loboo';

	// In the collapsed sidebar's column of icons; otherwise in the open sidebar's top row.
	export let rail = false;

	let button: HTMLButtonElement;
	const ask = (event: MouseEvent) => {
		// A click on the collapsed sidebar opens it; this one should not.
		event.stopImmediatePropagation();
		event.preventDefault();
		const { left, bottom } = button.getBoundingClientRect();
		tellHost({ type: 'loboo:switch', x: left, y: bottom });
	};
</script>

{#if $embedHost}
	{#if rail}
		<div>
			<Tooltip content={SWITCH_COMPUTER_LABEL} placement="right">
				<button
					bind:this={button}
					class=" cursor-pointer flex size-8 items-center justify-center transition group"
					on:click={ask}
					draggable="false"
					aria-label={SWITCH_COMPUTER_LABEL}
				>
					<div
						class="self-center flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg transition group-hover:bg-gray-100 dark:group-hover:bg-gray-900"
					>
						<Computer className="size-4" strokeWidth="1.5" />
					</div>
				</button>
			</Tooltip>
		</div>
	{:else}
		<Tooltip content={SWITCH_COMPUTER_LABEL} placement="bottom">
			<button
				bind:this={button}
				class="flex size-[1.875rem] justify-center items-center rounded-lg hover:bg-gray-100 dark:hover:bg-gray-900 transition"
				on:click={ask}
				aria-label={SWITCH_COMPUTER_LABEL}
			>
				<Computer className="size-4" strokeWidth="1.5" />
			</button>
		</Tooltip>
	{/if}
{/if}
