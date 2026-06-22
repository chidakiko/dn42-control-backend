// Documentation screenshots for docs/web-ui.md.
//
// Captures the admin UI (zh locale) into docs/images/. Requires a local stack:
//   - control-server seeded on :8000 (admin token "dev-admin-token", seed node edge1)
//   - vite dev on :5173
// Run from apps/web:  node scripts/doc-shots.mjs
//
// The peer "一键互联" wizard is driven step-by-step (fill minimal fields, Next)
// and each step's modal is captured.
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';

const BASE = 'http://127.0.0.1:5174';
const API = 'http://127.0.0.1:8001';
const TOKEN = 'dev-admin-token';
const NODE = 'edge1';
const OUT = '../../docs/images';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: 'msedge', headless: true });

async function newCtx() {
	const ctx = await browser.newContext({ viewport: { width: 1366, height: 920 }, colorScheme: 'light' });
	await ctx.addInitScript(
		(a) => {
			localStorage.setItem('dn42.admin.token', a.t);
			localStorage.setItem('dn42.admin.apiBase', a.api);
			localStorage.setItem('dn42.locale', 'zh');
			localStorage.setItem('dn42.theme', 'light');
		},
		{ t: TOKEN, api: API }
	);
	return ctx;
}

// --- simple full-page / viewport shots ---
const pages = [
	{ name: 'wui-login', path: '/login', full: false, auth: false },
	{ name: 'wui-dashboard', path: '/', full: true },
	{ name: 'wui-nodes', path: '/nodes', full: true },
	{ name: 'wui-registrations', path: '/registrations', full: true },
	{ name: 'wui-enrollment', path: '/enrollment-tokens', full: true },
	{ name: 'wui-provision', path: '/provision', full: true },
	{ name: 'wui-audit', path: '/audit', full: true }
];

for (const s of pages) {
	try {
		const ctx = await newCtx();
		const page = await ctx.newPage();
		await page.goto(BASE + s.path, { waitUntil: 'load' });
		await page.waitForTimeout(1800);
		await page.screenshot({ path: `${OUT}/${s.name}.png`, fullPage: s.full });
		await ctx.close();
		console.log('shot', s.name);
	} catch (e) {
		console.error('FAIL', s.name, e.message);
	}
}

// --- node detail tabs (overview / peerings / interfaces / internal) ---
const tabShots = [
	{ name: 'wui-node-overview', tab: null, full: false },
	{ name: 'wui-node-peerings', tab: 1, full: true },
	{ name: 'wui-node-interfaces', tab: 2, full: true },
	{ name: 'wui-node-internal', tab: 4, full: true }
];
for (const s of tabShots) {
	try {
		const ctx = await newCtx();
		const page = await ctx.newPage();
		await page.goto(`${BASE}/nodes/${NODE}`, { waitUntil: 'load' });
		await page.waitForTimeout(1800);
		if (s.tab !== null) {
			await page.locator('.tabs .tab').nth(s.tab).click();
			await page.waitForTimeout(1200);
		}
		await page.screenshot({ path: `${OUT}/${s.name}.png`, fullPage: s.full });
		await ctx.close();
		console.log('shot', s.name);
	} catch (e) {
		console.error('FAIL', s.name, e.message);
	}
}

// --- peer interconnect wizard, step by step ---
try {
	const ctx = await newCtx();
	const page = await ctx.newPage();
	await page.goto(`${BASE}/nodes/${NODE}`, { waitUntil: 'load' });
	await page.waitForTimeout(1800);

	const fill = async (label, value) =>
		page.locator('.dialog label.field', { hasText: label }).first().locator('input,textarea').fill(value);
	const next = async () => {
		await page.locator('.dialog').getByRole('button', { name: '下一步' }).click();
		await page.waitForTimeout(500);
	};
	const shotModal = async (name) => {
		await page.waitForTimeout(400);
		await page.locator('.dialog').screenshot({ path: `${OUT}/${name}.png` });
		console.log('shot', name);
	};

	await page.getByRole('button', { name: '一键互联' }).click();
	await page.waitForTimeout(700);

	// step 1 — 基本
	await fill('名称', 'demo-peer');
	await fill('对端 ASN', '4242421111');
	await shotModal('wui-wizard-1');
	await next();

	// step 2 — WireGuard（接口名已按 peer 名自动带入）
	await fill('本端地址', 'fe80::1/64');
	await fill('对端公钥', '+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=');
	await shotModal('wui-wizard-2');
	await next();

	// step 3 — BGP（加一条 IPv6 链路本地会话，填源地址）
	await page.locator('.dialog').getByRole('button', { name: 'IPv6 链路本地' }).click();
	await page.waitForTimeout(400);
	await fill('源地址', 'fe80::1');
	await shotModal('wui-wizard-3');
	await next();

	// step 4 — 确认
	await shotModal('wui-wizard-4');

	await ctx.close();
} catch (e) {
	console.error('FAIL wizard', e.message);
}

await browser.close();
console.log('done');
