// Light/dark theme, persisted, applied via `data-theme` on <html>. Defaults to
// light (Radar-style). The CSS in app.css keys off [data-theme="dark"].

import { browser } from '$app/environment';

export type ThemeMode = 'light' | 'dark';

const KEY = 'dn42.theme';

function detect(): ThemeMode {
	if (!browser) return 'light';
	const saved = localStorage.getItem(KEY);
	return saved === 'dark' ? 'dark' : 'light';
}

class Theme {
	mode = $state<ThemeMode>(detect());

	constructor() {
		if (browser) this.#apply();
	}

	#apply() {
		document.documentElement.dataset.theme = this.mode;
	}

	set(mode: ThemeMode) {
		this.mode = mode;
		if (browser) {
			localStorage.setItem(KEY, mode);
			this.#apply();
		}
	}

	toggle() {
		this.set(this.mode === 'dark' ? 'light' : 'dark');
	}
}

export const theme = new Theme();
