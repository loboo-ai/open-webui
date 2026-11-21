// loboo fork: the one place pdf.js is loaded, for the viewer, the page thumbnails and text extraction alike.
//
// It is the legacy build: the default one calls newer built-ins directly (Map.prototype.getOrInsertComputed,
// Uint8Array.prototype.toHex, Promise.try), so in an older WebView, such as WeChat's on iOS below 26.2, every PDF
// failed to open; the legacy build polyfills them.
//
// Neither build polyfills async iteration of ReadableStream, which no released Safari has, and page.getTextContent()
// iterates one with `for await` (on the main thread; the worker's one falls back by itself), so that goes in first.

import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.mjs?url';

type Pdfjs = typeof import('pdfjs-dist/legacy/build/pdf.mjs');

type ReadableStreamWithAsyncIterator<T> = ReadableStream<T> & {
	[Symbol.asyncIterator]?: () => AsyncIterableIterator<T>;
};

function ensureReadableStreamAsyncIterator() {
	if (typeof ReadableStream === 'undefined') {
		return;
	}

	const prototype = ReadableStream.prototype as ReadableStreamWithAsyncIterator<unknown>;
	if (prototype[Symbol.asyncIterator]) {
		return;
	}

	Object.defineProperty(prototype, Symbol.asyncIterator, {
		value: async function* (this: ReadableStream<unknown>) {
			const reader = this.getReader();
			try {
				while (true) {
					const { done, value } = await reader.read();
					if (done) {
						return;
					}
					yield value;
				}
			} finally {
				reader.releaseLock();
			}
		},
		configurable: true,
		writable: true
	});
}

let loading: Promise<Pdfjs> | null = null;

export const loadPdfjs = (): Promise<Pdfjs> =>
	(loading ??= (async () => {
		ensureReadableStreamAsyncIterator();
		const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
		pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
		return pdfjs;
	})().catch((error) => {
		// A chunk that failed to load (a dropped connection) is tried again next time.
		loading = null;
		throw error;
	}));
