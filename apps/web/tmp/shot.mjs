import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';

const BASE = 'http://127.0.0.1:5173';
const TOKEN = 'dev-admin-token';
const API = 'http://127.0.0.1:8000';
const OUT = 'tmp/shots';
mkdirSync(OUT, { recursive: true });

const shots = [
	{ name: 'login', path: '/login', auth: false },
	{ name: 'dashboard', path: '/', auth: true },
	{ name: 'dashboard-dark', path: '/', auth: true, theme: 'dark' },
	{ name: 'nodes', path: '/nodes', auth: true },
	{ name: 'node-detail', path: '/nodes/edge1', auth: true },
	{ name: 'node-detail-dark', path: '/nodes/edge1', auth: true, theme: 'dark' },
	{ name: 'node-status', path: '/nodes/edge1', auth: true, clickTab: 1, viewportOnly: true },
	{ name: 'registrations', path: '/registrations', auth: true },
	{ name: 'enrollment', path: '/enrollment-tokens', auth: true },
	{ name: 'provision', path: '/provision', auth: true },
	{ name: 'audit', path: '/audit', auth: true }
];

const browser = await chromium.launch({ channel: 'msedge', headless: true });
for (const s of shots) {
	const ctx = await browser.newContext({
		viewport: { width: 1366, height: 900 },
		colorScheme: 'dark'
	});
	await ctx.addInitScript(
		(a) => {
			if (a.auth) {
				localStorage.setItem('dn42.admin.token', a.t);
				localStorage.setItem('dn42.admin.apiBase', a.api);
			}
			if (a.lang) localStorage.setItem('dn42.locale', a.lang);
			localStorage.setItem('dn42.theme', a.theme || 'light');
		},
		{ t: TOKEN, api: API, lang: s.lang || '', theme: s.theme || 'light', auth: !!s.auth }
	);
	const page = await ctx.newPage();
	await page.goto(BASE + s.path, { waitUntil: 'load' });
	await page.waitForTimeout(1800);
	if (s.clickTab !== undefined) {
		// click the "Status events" tab (7th tab → index by querying .tab buttons)
		const tabs = page.locator('.tabs .tab');
		await tabs.nth(6).click();
		await page.waitForTimeout(1500);
	}
	await page.waitForTimeout(800);
	await page.screenshot({ path: `${OUT}/${s.name}.png`, fullPage: !s.viewportOnly });
	await ctx.close();
	console.log('shot', s.name);
}
await browser.close();
console.log('done');
