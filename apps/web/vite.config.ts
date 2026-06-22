import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// Pure client-side SPA: the admin token lives in the browser and talks
			// directly to the FastAPI control server. No SSR/server load. Every route
			// is served through the fallback page and rendered on the client.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	server: {
		port: 5173
	}
});
