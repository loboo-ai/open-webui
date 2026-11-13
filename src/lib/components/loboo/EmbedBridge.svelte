<script lang="ts">
	// loboo fork: inside Loboo's 我的电脑, where this site is its 云端电脑 (the rules are in src/lib/loboo.ts), keep that
	// page told which page we are on and what the page is called. Opened on its own the site is not in a frame and
	// nothing here runs.
	import { onMount } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import { embedHost, inFrame, startEmbed, tellHost } from '$lib/loboo';

	// What that page was told last, so the same thing is not said twice.
	let told = '';
	const tellPage = () => {
		const path = location.pathname + location.search;
		const title = document.title;
		const now = `${path}\n${title}`;
		if (now !== told && tellHost({ type: 'loboo:page', path, title })) told = now;
	};

	if (inFrame()) {
		afterNavigate(tellPage);
		onMount(() => {
			const stop = startEmbed();
			// The first telling is when that page answers the hello.
			const answered = embedHost.subscribe(tellPage);
			// A chat's name arrives after its page does.
			const titles = new MutationObserver(tellPage);
			titles.observe(document.head, { subtree: true, childList: true, characterData: true });
			return () => {
				stop();
				answered();
				titles.disconnect();
			};
		});
	}
</script>
