// loboo fork: a web page shown in a message (FullHeightIframe) is as tall as its content, so the frame doesn't scroll.
//
// The frame can't measure the page from outside (the sandbox puts the page on another origin), so this script goes
// in at the end of the page and reports the height in the `iframe:height` message FullHeightIframe already took from
// pages that sent it themselves. As part of the page it is under the admin's iframe CSP like any other inline
// script: where that forbids inline scripts it doesn't run, and the frame behaves as it did without it.
//
// What it measures:
// - While the page is taller than the frame: how far the page scrolls, which is exact.
// - Once the page fits: the root element's own box, which is what lets the frame get shorter again. Content that
//   sticks out below that box (absolutely positioned, or under a root of fixed height) would have the frame shrink
//   onto it and grow back for ever, so the height such a page last needed stays as a floor and it never shrinks.
// - Plus the horizontal scrollbar where scrollbars take up room; that alone would bring a vertical one.
// - Nothing while the frame has no height of its own (hidden, or not laid out yet): everything reads 0 then.
//
// A page sized from the viewport (100vh and then some: a margin, a second section) gets taller every time the frame
// does. It shows as the page outgrowing the frame by the same amount again, in the same round of rendering as a
// resize that changed only the height (the frame's own doing; a new width reflows any page). The second time in a
// row the frame stops following, and the page scrolls by that amount inside it.
const REPORTER = `<script>
(function () {
	var root = document.documentElement;
	var seen = 0, floor = 0, gap = 0, chased = 0, echo = false, width = window.innerWidth;
	function report() {
		var view = root.clientHeight;
		if (!view) return;
		var height = root.offsetHeight;
		if (root.scrollHeight > view) {
			floor = root.scrollHeight > height + 1 ? root.scrollHeight : 0;
			height = root.scrollHeight;
		} else if (floor > height) {
			height = floor;
		}
		height += Math.max(0, window.innerHeight - view);
		if (height === seen) return;
		seen = height;
		var over = height - window.innerHeight;
		chased = echo && over > 0 && Math.abs(over - gap) <= 1 ? chased + 1 : 0;
		gap = over;
		if (chased < 2) parent.postMessage({ type: 'iframe:height', height: height }, '*');
	}
	window.addEventListener('resize', function () {
		if (window.innerWidth === width) {
			echo = true;
			setTimeout(function () { echo = false; }, 0);
		}
		width = window.innerWidth;
		report();
	});
	window.addEventListener('load', report);
	var observer = new ResizeObserver(report);
	observer.observe(root);
	if (document.body) observer.observe(document.body);
	report();
})();
<\/script>`;

// At the end rather than in <head> or before </body>: the CSP <meta> (utils/csp.ts) is then always ahead of it, and
// those two strings can also turn up inside the page's own scripts.
export const withHeightReporter = (html: string): string => `${html}\n${REPORTER}`;

// What a page may ask for, whoever sent the message: tall enough to see, short enough to scroll past.
export const clampFrameHeight = (height: number): number => Math.min(20000, Math.max(80, height));
