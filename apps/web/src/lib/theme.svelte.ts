// Theme: light / dark / system. Persisted as a preference; `system` follows the
// OS via prefers-color-scheme and keeps tracking it live. The resolved value is
// applied via `data-theme` on <html>; CSS in app.css keys off [data-theme="dark"].

import { browser } from '$app/environment';

export type ThemePref = 'light' | 'dark' | 'system';
export type ThemeMode = 'light' | 'dark';

const KEY = 'dn42.theme';
const ORDER: ThemePref[] = ['system', 'light', 'dark'];

function readPref(): ThemePref {
	if (!browser) return 'system';
	const saved = localStorage.getItem(KEY);
	return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'system';
}

function systemMode(): ThemeMode {
	if (!browser) return 'light';
	return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

class Theme {
	/** User preference: may be `system`. */
	pref = $state<ThemePref>(readPref());
	/** Resolved light/dark actually applied to the document. */
	mode = $state<ThemeMode>('light');

	constructor() {
		if (!browser) return;
		this.mode = this.pref === 'system' ? systemMode() : this.pref;
		this.#apply();
		// Track OS changes so `system` stays live without a reload.
		window
			.matchMedia('(prefers-color-scheme: dark)')
			.addEventListener('change', () => {
				if (this.pref === 'system') {
					this.mode = systemMode();
					this.#apply();
				}
			});
	}

	#apply() {
		if (browser) document.documentElement.dataset.theme = this.mode;
	}

	set(pref: ThemePref) {
		this.pref = pref;
		this.mode = pref === 'system' ? systemMode() : pref;
		if (browser) {
			localStorage.setItem(KEY, pref);
			this.#apply();
		}
	}

	/** Cycle system → light → dark → system. */
	toggle() {
		const next = ORDER[(ORDER.indexOf(this.pref) + 1) % ORDER.length];
		this.set(next);
	}
}

export const theme = new Theme();
