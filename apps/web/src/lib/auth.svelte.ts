// Reactive auth/session state, persisted to localStorage.
//
// The admin Bearer token and the control-server base URL live here. Because
// this is a `.svelte.ts` module the `$state` is shared reactively across every
// component that imports `auth`.

import { browser } from '$app/environment';
import { STORAGE_KEYS, defaultApiBase } from './config';

function read(key: string, fallback = ''): string {
	if (!browser) return fallback;
	return localStorage.getItem(key) ?? fallback;
}

class AuthState {
	token = $state(read(STORAGE_KEYS.token));
	apiBase = $state(read(STORAGE_KEYS.apiBase, defaultApiBase()));

	get isAuthed(): boolean {
		return this.token.trim().length > 0;
	}

	/** Persist a freshly-entered token + API base and mark the session live. */
	login(token: string, apiBase: string) {
		this.token = token.trim();
		this.apiBase = (apiBase.trim() || defaultApiBase()).replace(/\/+$/, '');
		if (browser) {
			localStorage.setItem(STORAGE_KEYS.token, this.token);
			localStorage.setItem(STORAGE_KEYS.apiBase, this.apiBase);
		}
	}

	/** Drop the token (e.g. on logout or a 401). Keeps the API base around. */
	logout() {
		this.token = '';
		if (browser) localStorage.removeItem(STORAGE_KEYS.token);
	}
}

export const auth = new AuthState();
