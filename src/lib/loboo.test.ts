import { describe, expect, it } from 'vitest';
import { classifyLink } from './loboo';

// The mini program's link rules (docs/loboo-cloud-plan-2026-10-25.md 3.6), as the click listener and the
// window.open wrapper see them.
const site = { origin: 'https://m.haina-api.net', base: 'https://m.haina-api.net/c/abc' };

describe('classifyLink', () => {
	it('sorts each kind of link into its rule', () => {
		expect(classifyLink('#section', site)).toEqual({ rule: 'R0' });
		expect(classifyLink('sandbox:/mnt/data/a.txt', site)).toEqual({ rule: 'R0' });
		expect(classifyLink('/api/v1/files/1/content', { ...site, download: true })).toEqual({
			rule: 'R1'
		});
		expect(classifyLink('blob:https://m.haina-api.net/1f2e', site)).toEqual({ rule: 'R1' });
		expect(classifyLink('mailto:a@b.com', site)).toEqual({ rule: 'R2', text: 'a@b.com' });
		expect(classifyLink('tel:+8613800000000', site)).toEqual({
			rule: 'R3',
			text: '+8613800000000'
		});
		expect(classifyLink('https://m.loboo-ai.com/c/1', site)).toEqual({
			rule: 'R4',
			text: 'https://m.loboo-ai.com/c/1'
		});
		expect(classifyLink('/cache/image/a.png', site)).toEqual({ rule: 'R5' });
		expect(classifyLink('/notes/1?x=1#h', { ...site, target: '_blank' })).toEqual({
			rule: 'R6',
			path: '/notes/1?x=1#h'
		});
		expect(classifyLink('/c/2', site)).toEqual({ rule: 'R7' });
	});

	it('copies the address or number alone, and the full address of another site', () => {
		expect(classifyLink('mailto:A%20B%3Ca@b.com%3E?subject=hi&body=x', site)).toEqual({
			rule: 'R2',
			text: 'A B<a@b.com>'
		});
		expect(classifyLink('tel:+86%20138-0000-0000?ext=1', site)).toEqual({
			rule: 'R3',
			text: '+86 138-0000-0000'
		});
		expect(classifyLink('//example.com/a b?q=1#x', site)).toEqual({
			rule: 'R4',
			text: 'https://example.com/a%20b?q=1#x'
		});
	});

	it('lets the first matching rule win', () => {
		// A download attribute beats every later rule, even on another site.
		expect(classifyLink('https://example.com/a.pdf', { ...site, download: true })).toEqual({
			rule: 'R1'
		});
		// This site's files are blocked even when asked for in a new window.
		expect(classifyLink('/static/a.pdf', { ...site, target: '_blank' })).toEqual({ rule: 'R5' });
		// Same-window targets keep a page of this site a plain navigation; relative links resolve against the page.
		expect(classifyLink('/c/2', { ...site, target: '_self' })).toEqual({ rule: 'R7' });
		expect(classifyLink('other', { ...site, target: '_blank' })).toEqual({
			rule: 'R6',
			path: '/c/other'
		});
		// http and https are different sites.
		expect(classifyLink('http://m.haina-api.net/c/2', site).rule).toBe('R4');
	});
});
