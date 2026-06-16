// SPA mode: no server-side rendering, no prerendering. The admin token only
// exists in the browser, so every route renders client-side and is served
// through the adapter-static fallback page.
export const ssr = false;
export const prerender = false;
export const trailingSlash = 'never';
