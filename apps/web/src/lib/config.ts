// Runtime configuration for the admin UI.
//
// The control server base URL can come from a build-time env var
// (VITE_CONTROL_API), but operators can also override it at runtime from the
// login screen — handy when the same static build points at different fleets.

const DEFAULT_API_BASE = 'http://127.0.0.1:8000';

export const STORAGE_KEYS = {
	token: 'dn42.admin.token',
	apiBase: 'dn42.admin.apiBase'
} as const;

export function defaultApiBase(): string {
	const fromEnv = import.meta.env.VITE_CONTROL_API as string | undefined;
	return (fromEnv && fromEnv.trim()) || DEFAULT_API_BASE;
}
