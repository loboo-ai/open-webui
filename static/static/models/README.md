# Model avatars

loboo fork: a model without its own profile image shows its vendor's mark, chosen by the `owned_by` its
connection reports (`backend/open_webui/loboo/model_logos.py`); `unknown.svg` covers any other vendor.

The marks come from [`@lobehub/icons-static-svg`](https://github.com/lobehub/lobe-icons) 1.95.1 (MIT),
redrawn on a white disc with fixed colours so they read the same in light and dark themes. The logos
themselves are trademarks of Anthropic, OpenAI, Google and xAI. `unknown.svg` uses the Heroicons
sparkles outline already bundled in `src/lib/components/icons/Sparkles.svelte`.
