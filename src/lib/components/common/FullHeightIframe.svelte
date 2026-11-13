<script context="module" lang="ts">
	// contentWindows of embeds rendered here; Chat.svelte trusts prompt messages from these
	const embedWindows = new Set<Window>();

	export const isEmbedWindow = (source: unknown): boolean => embedWindows.has(source as Window);
</script>

<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { config } from '$lib/stores';
	import { injectCsp } from '$lib/utils/csp';
	import { clampFrameHeight, withHeightReporter } from '$lib/utils/frameHeight';
	import FullScreenModal from '$lib/components/common/FullScreenModal.svelte';
	import ArrowsPointingOut from '$lib/components/icons/ArrowsPointingOut.svelte';

	const i18n: any = getContext('i18n');

	// Props
	export let src: string | null = null; // URL or raw HTML (auto-detected)
	export let title = 'Embedded Content';
	export let initialHeight: number | null = null; // initial height in px, null = auto

	export let iframeClassName = 'w-full rounded-2xl';

	export let args = null;

	export let allowScripts = true;
	export let allowForms = false;

	export let allowSameOrigin = false; // set to true only when you trust the content
	export let allowPopups = false;
	export let allowDownloads = true;

	export let referrerPolicy: HTMLIFrameElement['referrerPolicy'] =
		'strict-origin-when-cross-origin';
	export let allowFullscreen = true;

	export let payload = null; // payload to send into the iframe on request

	// loboo fork: in a message the frame is as tall as its content (utils/frameHeight.ts) and has a button that opens
	// the same content over the whole screen. `fill` is for where the container sets the height instead (the side
	// panel, the full-screen copy below): nothing sizes the frame from its content and there is no button.
	export let fill = false;
	let fullScreen = false;

	let iframe: HTMLIFrameElement | null = null;
	let iframeSrc: string | null = null;
	let iframeDoc: string | null = null;
	let registeredWindow: Window | null = null;

	// Derived: build sandbox attribute from flags
	$: sandbox =
		[
			allowScripts && 'allow-scripts',
			allowForms && 'allow-forms',
			allowSameOrigin && 'allow-same-origin',
			allowPopups && 'allow-popups',
			allowDownloads && 'allow-downloads'
		]
			.filter(Boolean)
			.join(' ') || undefined;

	// Detect URL vs raw HTML and prep src/srcdoc
	$: isUrl = typeof src === 'string' && /^(https?:)?\/\//i.test(src);
	$: if (src) {
		setIframeSrc();
	}

	const setIframeSrc = async () => {
		await tick();
		if (isUrl) {
			iframeSrc = src as string;
			iframeDoc = null;
		} else {
			const doc = await processHtmlForDeps(src as string);
			iframeDoc = fill ? doc : withHeightReporter(doc);
			iframeSrc = null;
		}
	};

	// Alpine directives detection
	const alpineDirectives = [
		'x-data',
		'x-init',
		'x-show',
		'x-bind',
		'x-on',
		'x-text',
		'x-html',
		'x-model',
		'x-modelable',
		'x-ref',
		'x-for',
		'x-if',
		'x-effect',
		'x-transition',
		'x-cloak',
		'x-ignore',
		'x-teleport',
		'x-id'
	];

	async function processHtmlForDeps(html: string): Promise<string> {
		if (!allowSameOrigin) return html;

		const scriptTags: string[] = [];

		// Alpine.js detection & injection
		const hasAlpineDirectives = alpineDirectives.some((dir) => html.includes(dir));
		if (hasAlpineDirectives) {
			try {
				const { default: alpineCode } = await import('alpinejs/dist/cdn.min.js?raw');
				const alpineBlob = new Blob([alpineCode], { type: 'text/javascript' });
				const alpineUrl = URL.createObjectURL(alpineBlob);
				const alpineTag = `<script src="${alpineUrl}" defer><\/script>`;
				scriptTags.push(alpineTag);
			} catch (error) {
				console.error('Error processing Alpine for iframe:', error);
			}
		}

		// Chart.js detection & injection
		const chartJsDirectives = ['new Chart(', 'Chart.'];
		const hasChartJsDirectives = chartJsDirectives.some((dir) => html.includes(dir));
		if (hasChartJsDirectives) {
			try {
				// import chartUrl from 'chart.js/auto?url';
				const { default: Chart } = await import('chart.js/auto');
				(window as any).Chart = Chart;

				const chartTag = `<script>
window.Chart = parent.Chart; // Chart previously assigned on parent
<\/script>`;
				scriptTags.push(chartTag);
			} catch (error) {
				console.error('Error processing Chart.js for iframe:', error);
			}
		}

		// If nothing to inject, return original HTML
		if (scriptTags.length === 0) return html;

		const tags = scriptTags.join('\n');

		// Prefer injecting into <head>, then before </body>, otherwise prepend
		if (html.includes('</head>')) {
			return html.replace('</head>', `${tags}\n</head>`);
		}
		if (html.includes('</body>')) {
			return html.replace('</body>', `${tags}\n</body>`);
		}
		return `${tags}\n${html}`;
	}

	// Try to measure same-origin content safely
	function resizeSameOrigin() {
		if (!iframe) return;
		try {
			const doc = iframe.contentDocument || iframe.contentWindow?.document;
			console.log('iframe doc:', doc);
			if (!doc) return;
			const h = Math.max(doc.documentElement?.scrollHeight ?? 0, doc.body?.scrollHeight ?? 0);
			if (h > 0) iframe.style.height = h + 20 + 'px';
		} catch {
			// Cross-origin → rely on postMessage from inside the iframe
		}
	}

	function onMessage(e: MessageEvent) {
		if (!iframe || e.source !== iframe.contentWindow) return;

		const data = e.data || {};
		if (!fill && data?.type === 'iframe:height' && typeof data.height === 'number') {
			iframe.style.height = clampFrameHeight(data.height) + 'px';
		}

		// Pong message for testing connectivity
		if (data?.type === 'pong') {
			console.log('Received pong from iframe:', data);

			// Optional: reply back
			iframe.contentWindow?.postMessage({ type: 'pong:ack' }, '*');
		}

		// Send payload data if requested
		if (data?.type === 'payload') {
			iframe.contentWindow?.postMessage(
				{ type: 'payload', requestId: data?.requestId ?? null, payload: payload },
				'*'
			);
		}
	}

	// When the iframe loads, try same-origin resize (cross-origin will noop)
	const onLoad = async () => {
		if (!fill) requestAnimationFrame(resizeSameOrigin);

		if (iframe?.contentWindow && iframe.contentWindow !== registeredWindow) {
			if (registeredWindow) {
				embedWindows.delete(registeredWindow);
			}
			registeredWindow = iframe.contentWindow;
			embedWindows.add(registeredWindow);
		}

		// if arguments are provided, inject them into the iframe window
		if (args && iframe?.contentWindow) {
			try {
				(iframe.contentWindow as any).args = args;
			} catch {
				// Cross-origin → args cannot be injected
			}
		}
	};

	// Ensure event listener bound only while component lives
	onMount(() => {
		window.addEventListener('message', onMessage);
	});

	onDestroy(() => {
		window.removeEventListener('message', onMessage);
		if (registeredWindow) {
			embedWindows.delete(registeredWindow);
		}
	});
</script>

<!-- loboo fork: the box is what the full-screen button sits in; with `fill` it makes no box of its own, so the frame
	still takes its height from the container (see `fill`) -->
<div class={fill ? 'contents' : 'group/frame relative'}>
	{#if iframeDoc}
		<iframe
			bind:this={iframe}
			srcdoc={injectCsp(iframeDoc, $config?.ui?.iframe_csp ?? '')}
			{title}
			class={iframeClassName}
			style={`${initialHeight ? `height:${initialHeight}px;` : ''}`}
			width="100%"
			frameborder="0"
			{sandbox}
			{allowFullscreen}
			on:load={onLoad}
		/>
	{:else if iframeSrc}
		<iframe
			bind:this={iframe}
			src={iframeSrc}
			{title}
			class={iframeClassName}
			style={`${initialHeight ? `height:${initialHeight}px;` : ''}`}
			width="100%"
			frameborder="0"
			{sandbox}
			referrerpolicy={referrerPolicy}
			{allowFullscreen}
			on:load={onLoad}
		/>
	{/if}
	{#if !fill && (iframeDoc || iframeSrc)}
		<button
			type="button"
			class="absolute top-2 right-2 flex size-7 items-center justify-center rounded-lg bg-white/90 text-gray-500 opacity-0 shadow-sm ring-1 ring-black/5 transition hover:text-gray-900 group-hover/frame:opacity-100 group-focus-within/frame:opacity-100 pointer-coarse:opacity-100 dark:bg-gray-850/90 dark:text-gray-400 dark:ring-white/10 dark:hover:text-gray-100"
			aria-label={$i18n.t('Open in full screen')}
			title={$i18n.t('Open in full screen')}
			on:click={() => (fullScreen = true)}
		>
			<ArrowsPointingOut className="size-4" />
		</button>
	{/if}
</div>

{#if !fill}
	<!-- loboo fork: a second frame with the same content, so the page starts over in it -->
	<FullScreenModal bind:show={fullScreen}>
		<svelte:self
			{src}
			{title}
			{args}
			{allowScripts}
			{allowForms}
			{allowSameOrigin}
			{allowPopups}
			{allowDownloads}
			{referrerPolicy}
			{allowFullscreen}
			{payload}
			fill
			iframeClassName="w-full h-full"
		/>
	</FullScreenModal>
{/if}
