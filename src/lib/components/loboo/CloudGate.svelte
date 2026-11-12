<script lang="ts">
	// loboo fork: the gate for 萝卜云 in the tab panel (ChatTabs wraps every tab in it; tabs of no terminal pass
	// straight through). Everything such a tab does reaches the cloud computer and keeps it awake, and tabs come back
	// with a chat whether or not the panel shows, so a tab runs only while someone is there:
	// - asleep when the tab mounts: a card wakes it on request; asking for the tab (a file to show through
	//   display_file or 在文件中打开, a terminal to start) wakes it straight away (wakeRequest);
	// - nobody has touched the page for 10 minutes: the tab is taken down until 继续;
	// - a Shell tab was used in the last 5 minutes: a request every 4 minutes, because the orchestrator counts only
	//   the start of the Shell's WebSocket as use, not what goes over it.
	// Terminals that are not 萝卜云, and an unknown state, get the tab as it always was.
	import { onMount } from 'svelte';
	import { settings, terminalServers } from '$lib/stores';
	import { resolveTerminalConnection, terminalRequest } from '$lib/apis/terminal';
	import { getCloudState, mayBeCloudTerminal, wakeCloud, type CloudState } from '$lib/loboo';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Icon from '$lib/components/chat/FileNav/Icon.svelte';

	// The tab's terminal; null for tabs of none.
	export let terminalId: string | null = null;
	// ChatTabs bumps it when someone asks for this tab: wake straight away instead of showing the asleep card.
	export let wakeRequest = 0;
	// The tab is a Shell (its chat is the Shell's session): see keepAlive.
	export let shell = false;
	export let chatId: string | null = null;
	// Runs before an idle pause takes the tab down, so edits not saved yet can be kept as a draft.
	export let beforePause: () => void = () => {};

	const IDLE_AFTER_MS = 10 * 60_000;
	const KEEPALIVE_EVERY_MS = 4 * 60_000;
	const KEEPALIVE_WITHIN_MS = 5 * 60_000;

	type Phase = 'checking' | 'open' | 'sleeping' | 'waking' | 'paused';

	// Starts closed so FileNav cannot mount (and reach the terminal) before the first gate() call.
	let phase: Phase = 'checking';
	// The terminal being gated: the tab's when it may be 萝卜云, otherwise null and the tab just opens;
	// undefined until the first gate() call.
	let gatedId: string | null | undefined = undefined;
	// What the backend last said; pausing and keeping awake apply only once it has said running or sleeping.
	let known: CloudState = 'unknown';
	let checkSeq = 0;
	let lastActivityAt = Date.now();

	const token = () => localStorage.token;

	const openPanel = () => {
		lastActivityAt = Date.now();
		phase = 'open';
	};

	const check = async (fresh = false) => {
		const id = gatedId;
		if (!id) return;
		const seq = ++checkSeq;
		phase = 'checking';
		const state = await getCloudState(token(), id, { fresh });
		if (seq !== checkSeq) return;
		known = state;
		if (phase !== 'checking') return;
		if (state === 'sleeping') {
			phase = 'sleeping';
			return;
		}
		// Awake: write the loboo-cli key anyway (the backend writes it at most once per 10 minutes per member), for
		// members who only use the panel or the Shell and never let the model run a terminal tool.
		if (state === 'running') void wakeCloud(token(), id);
		openPanel();
	};

	const gate = (id: string | null) => {
		if (id === gatedId) return;
		gatedId = id;
		known = 'unknown';
		checkSeq++;
		if (id) void check();
		else phase = 'open';
	};

	// The terminal list arrives after the page (null until then): wait for it rather than open the tab early.
	$: if (!terminalId) {
		gate(null);
	} else if ($terminalServers !== null) {
		gate(mayBeCloudTerminal(terminalId, $terminalServers) ? terminalId : null);
	}

	let handledWakeRequest = 0;
	$: if (wakeRequest > handledWakeRequest && gatedId !== undefined) {
		handledWakeRequest = wakeRequest;
		if (gatedId && phase !== 'open') {
			if (phase !== 'waking') void wakeCloud(token(), gatedId);
			openPanel();
		}
	}

	const wake = async () => {
		const id = gatedId;
		if (!id) return;
		phase = 'waking';
		await wakeCloud(token(), id);
		if (id === gatedId && phase === 'waking') openPanel();
	};

	const keepAlive = () => {
		if (!shell || phase !== 'open' || known === 'unknown' || !gatedId) return;
		if (Date.now() - lastActivityAt >= KEEPALIVE_WITHIN_MS) return;
		const connection = resolveTerminalConnection(
			gatedId,
			$terminalServers ?? [],
			($settings as { terminalServers?: { url: string; key?: string }[] })?.terminalServers ?? [],
			token()
		);
		if (!connection) return;
		void terminalRequest(connection, chatId, '/api/config').catch(() => {});
	};

	const pauseWhenIdle = () => {
		if (phase === 'open' && known !== 'unknown' && Date.now() - lastActivityAt >= IDLE_AFTER_MS) {
			beforePause();
			phase = 'paused';
		}
	};

	// While the asleep card shows, the model may start the cloud computer by running a tool: open the panel then.
	const openIfWoken = async () => {
		const id = gatedId;
		if (phase !== 'sleeping' || !id) return;
		const state = await getCloudState(token(), id, { fresh: true });
		if (state === 'running' && id === gatedId && phase === 'sleeping') {
			known = state;
			openPanel();
		}
	};

	onMount(() => {
		const touched = () => {
			lastActivityAt = Date.now();
		};
		// scroll and focus do not bubble: listen while they go down.
		const events = [
			'pointerdown',
			'pointermove',
			'keydown',
			'wheel',
			'scroll',
			'touchstart',
			'focus'
		];
		for (const name of events) {
			window.addEventListener(name, touched, { capture: true, passive: true });
		}
		const idleTimer = setInterval(() => {
			pauseWhenIdle();
			void openIfWoken();
		}, 30_000);
		const keepAliveTimer = setInterval(keepAlive, KEEPALIVE_EVERY_MS);
		return () => {
			checkSeq++;
			for (const name of events) {
				window.removeEventListener(name, touched, { capture: true });
			}
			clearInterval(idleTimer);
			clearInterval(keepAliveTimer);
		};
	});
</script>

{#if phase === 'open'}
	<slot />
{:else}
	<div class="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
		{#if phase === 'checking'}
			<Spinner className="size-5 text-gray-400" />
		{:else}
			<Icon name="terminal" size={24} strokeWidth={1.4} class="text-gray-300 dark:text-gray-600" />
			<div class="text-xs text-gray-500 dark:text-gray-400">
				{phase === 'paused' ? '已暂停刷新。' : '萝卜云已休眠，文件都还在。'}
			</div>
			<button
				type="button"
				class="mt-1 flex h-7 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-3 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
				disabled={phase === 'waking'}
				on:click={() => (phase === 'paused' ? check(true) : wake())}
			>
				{#if phase === 'waking'}<Spinner className="size-3" />{/if}
				{phase === 'paused' ? '继续' : '唤醒'}
			</button>
			{#if phase !== 'paused'}
				<div class="text-[0.625rem] text-gray-400 dark:text-gray-500">唤醒约需 5 秒</div>
			{/if}
		{/if}
	</div>
{/if}
