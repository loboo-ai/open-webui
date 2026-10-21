// loboo fork: inside the Loboo mini program web-view there is no password form to fall back to.
// When the session is lost the page goes back to the mini program to re-enter with a fresh ticket
// through Loboo's ChatGPT entry (the same page the ChatGPT card on the mini program home opens);
// signing out returns to the mini program home. REENTRY must match backend/open_webui/loboo/pages.py.
(function () {
	if (!/miniprogram/i.test(navigator.userAgent)) return;

	var REENTRY = '/pages/web/index?to=page&path=%2Faccount%2Fapi%2Fopenwebui%2Fenter&title=ChatGPT';
	var HOME = '/pages/home/index';
	var JWEIXIN = 'https://res.wx.qq.com/open/js/jweixin-1.6.0.js';
	// Re-entries in the last 30 seconds; after two the page stays put instead of bouncing.
	var HISTORY_KEY = 'loboo.miniapp.reentries';
	var WINDOW_MS = 30000;

	document.documentElement.classList.add('loboo-miniapp');

	var miniProgram = null;
	function wechat() {
		if (!miniProgram) {
			miniProgram = new Promise(function (resolve, reject) {
				if (window.wx && window.wx.miniProgram) return resolve(window.wx.miniProgram);
				var script = document.createElement('script');
				script.src = JWEIXIN;
				script.onload = function () {
					window.wx && window.wx.miniProgram ? resolve(window.wx.miniProgram) : reject();
				};
				script.onerror = reject;
				document.head.appendChild(script);
			});
			miniProgram.catch(function () {
				miniProgram = null;
			});
		}
		return miniProgram;
	}

	var leaving = false;
	function leave(home) {
		if (leaving) return;
		leaving = true;
		wechat().then(
			function (mp) {
				home ? mp.reLaunch({ url: HOME }) : mp.redirectTo({ url: REENTRY });
			},
			function () {
				leaving = false;
			}
		);
	}

	function recentReentries() {
		var now = Date.now();
		try {
			return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]').filter(function (at) {
				return now - at < WINDOW_MS;
			});
		} catch (e) {
			return [];
		}
	}

	function check(href) {
		var url;
		try {
			url = new URL(href, location.href);
		} catch (e) {
			return;
		}
		if (url.origin !== location.origin || url.pathname !== '/auth') return;
		if (url.searchParams.get('state') === 'logout') return leave(true);
		var reentries = recentReentries();
		if (reentries.length >= 2) return;
		reentries.push(Date.now());
		try {
			localStorage.setItem(HISTORY_KEY, JSON.stringify(reentries));
		} catch (e) {}
		leave(false);
	}

	['pushState', 'replaceState'].forEach(function (name) {
		var original = history[name];
		history[name] = function (state, title, url) {
			var result = original.apply(this, arguments);
			if (url !== undefined && url !== null) check(String(url));
			return result;
		};
	});
	window.addEventListener('popstate', function () {
		check(location.href);
	});
	check(location.href);
})();
