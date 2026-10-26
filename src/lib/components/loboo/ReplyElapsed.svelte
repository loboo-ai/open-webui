<script lang="ts">
	// loboo fork: while 萝卜哥 is answering, the time since the reply started counts up beside the cursor, so a long
	// wait (a web search, a big file) visibly keeps going. ResponseMessage mounts it only while the reply is pending.
	// Continuing a finished reply counts from when it was first created.
	import { onDestroy } from 'svelte';
	import { formatElapsed } from '$lib/loboo';

	// Unix seconds the reply was created at; one without it counts from when this appears.
	export let since: number | undefined = undefined;

	const start = since ? since * 1000 : Date.now();
	let now = Date.now();
	const timer = setInterval(() => (now = Date.now()), 1000);
	onDestroy(() => clearInterval(timer));
</script>

<!-- Under a status line the cursor is hidden and this starts the row, flush with the status text. -->
<span
	class="not-first:ms-1.5 text-xs text-gray-400 dark:text-gray-500 tabular-nums align-text-bottom"
>
	{formatElapsed((now - start) / 1000)}
</span>
