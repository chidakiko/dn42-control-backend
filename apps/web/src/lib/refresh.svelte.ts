// Global auto-refresh ticker. A single interval bumps `tick`; live pages run
// their load function inside an `$effect` that reads `autoRefresh.tick`, so one
// timer drives every subscribed page. Interval is user-selectable and persisted.
// Polling pauses while the tab is hidden to avoid pointless background fetches.

import { browser } from '$app/environment';

const KEY = 'dn42.refreshMs';
const DEFAULT_MS = 10000;

function initialMs(): number {
	if (!browser) return DEFAULT_MS;
	const raw = localStorage.getItem(KEY);
	if (raw === null) return DEFAULT_MS;
	const n = Number(raw);
	return Number.isFinite(n) && n >= 0 ? n : DEFAULT_MS;
}

class RefreshState {
	/** Polling interval in ms; 0 means auto-refresh is off. */
	intervalMs = $state(initialMs());
	/** Monotonic counter bumped on every tick. Pages depend on this. */
	tick = $state(0);

	#timer: ReturnType<typeof setInterval> | null = null;

	start() {
		if (!browser) return;
		this.stop();
		if (this.intervalMs > 0) {
			this.#timer = setInterval(() => {
				// Don't poll a hidden tab. Background polls update data in place
				// (silently) without remounting the DOM, so editing isn't disrupted.
				if (document.visibilityState === 'visible') this.tick++;
			}, this.intervalMs);
		}
	}

	stop() {
		if (this.#timer) {
			clearInterval(this.#timer);
			this.#timer = null;
		}
	}

	set(ms: number) {
		this.intervalMs = ms;
		if (browser) localStorage.setItem(KEY, String(ms));
		this.start();
	}
}

export const autoRefresh = new RefreshState();

if (browser) autoRefresh.start();
