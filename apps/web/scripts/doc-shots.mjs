// Documentation screenshots for docs/guides/web-ui.md.
//
// Captures the admin UI (zh locale, light theme) into docs/images/. Every shot
// is scoped to the component it illustrates (a card / panel / dialog) — never
// the whole browser window with the sidebar — so the docs stay focused.
//
// Data source is configurable via env (no secrets committed):
//   DOCSHOTS_BASE   web origin to screenshot      (default http://localhost:5173)
//   DOCSHOTS_API    control-server the UI talks to (default http://127.0.0.1:8001)
//   DOCSHOTS_TOKEN  admin token                    (default dev-admin-token)
//   DOCSHOTS_NODE   node id for detail/wizard pages (default edge1)
//
// Two ways to run:
//   A) Local demo (leak-free, repo default):
//        1. control-server on :8001  (token "dev-admin-token", CORS *)
//        2. python apps/web/scripts/seed_docshots.py   (provisions edge1 + data)
//        3. vite dev on :5174 ; then from apps/web:  DOCSHOTS_BASE=http://127.0.0.1:5174 node scripts/doc-shots.mjs
//   B) Against a live fleet (real data): point env at it, e.g.
//        DOCSHOTS_API=https://control-server.<fleet> DOCSHOTS_TOKEN=<token> \
//        DOCSHOTS_NODE=<node> node scripts/doc-shots.mjs
//      (the running web origin's control-server CORS must allow DOCSHOTS_BASE)
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';

const BASE = process.env.DOCSHOTS_BASE || 'http://localhost:5173';
const API = process.env.DOCSHOTS_API || 'http://127.0.0.1:8001';
const TOKEN = process.env.DOCSHOTS_TOKEN || 'dev-admin-token';
const NODE = process.env.DOCSHOTS_NODE || 'edge1';
const OUT = '../../docs/images';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: 'msedge', headless: true });

// auth=false → no token in localStorage (so /login shows the form).
async function newCtx({ auth = true } = {}) {
	const ctx = await browser.newContext({
		viewport: { width: 1320, height: 1000 },
		colorScheme: 'light',
		deviceScaleFactor: 2
	});
	await ctx.addInitScript(
		(a) => {
			if (a.auth) localStorage.setItem('dn42.admin.token', a.t);
			localStorage.setItem('dn42.admin.apiBase', a.api);
			localStorage.setItem('dn42.locale', 'zh');
			localStorage.setItem('dn42.theme', 'light');
		},
		{ t: TOKEN, api: API, auth }
	);
	return ctx;
}

// Screenshot a single element (focused) rather than the full page.
async function shotEl(page, selector, name, { nth = 0 } = {}) {
	const el = page.locator(selector).nth(nth);
	await el.waitFor({ state: 'visible', timeout: 10000 });
	await el.scrollIntoViewIfNeeded();
	await page.waitForTimeout(700); // let charts / map / sparklines settle
	await el.screenshot({ path: `${OUT}/${name}.png` });
	console.log('shot', name);
}

async function open(ctx, path) {
	const page = await ctx.newPage();
	await page.goto(BASE + path, { waitUntil: 'networkidle' });
	await page.waitForTimeout(1400);
	return page;
}

// --- focused, single-element shots on standalone pages ---
// [name, path, selector, auth]
const shots = [
	['wui-login', '/login', '.login', false],
	['wui-dashboard', '/', '.doc-topology', true],
	['wui-fleet-routing', '/', '.doc-routing', true],
	['wui-traffic', '/', '.doc-traffic', true],
	['wui-nodes', '/nodes', 'main .card', true],
	['wui-registrations', '/registrations', 'main .card', true],
	['wui-enrollment', '/enrollment-tokens', 'main .card', true],
	['wui-provision', '/provision', 'main .card', true],
	['wui-audit', '/audit', 'main .card', true]
];

for (const [name, path, selector, auth] of shots) {
	try {
		const ctx = await newCtx({ auth });
		const page = await open(ctx, path);
		await shotEl(page, selector, name);
		await ctx.close();
	} catch (e) {
		console.error('FAIL', name, e.message);
	}
}

// --- DNS groups: expand the first group's zones + the first zone's records,
//     then capture the whole content column (groups → zones → records). ---
try {
	const ctx = await newCtx();
	const page = await open(ctx, '/dns-groups');
	// best-effort expansion: first group row → 区域, then first zone row → 记录.
	try {
		await page.locator('table tbody tr').first().locator('.actions button').first().click();
		await page.waitForTimeout(700);
		await page.locator('.card').last().locator('table tbody tr').first()
			.locator('.actions button').first().click();
		await page.waitForTimeout(700);
	} catch (e) {
		console.error('warn dns-groups expansion', e.message);
	}
	await shotEl(page, 'main', 'wui-dns-groups');
	await ctx.close();
} catch (e) {
	console.error('FAIL wui-dns-groups', e.message);
}

// --- node detail — two-level tabs. Top groups (.tabs .tab):
//       0 概览 · 1 互联 · 2 路由 · 3 DNS · 4 运维
//     互联 sub-tabs (.subtabs .subtab): 0 Peering · 1 接口 · 2 BGP · 3 内部互联
//     路由 sub-tabs: 0 路由表 · 1 路由调优
// Capture the tab's content card (excludes page chrome + sidebar). ---
async function openTab(page, group, sub) {
	if (group != null) {
		await page.locator('.tabs .tab').nth(group).click();
		await page.waitForTimeout(400);
	}
	if (sub != null) {
		await page.locator('.subtabs .subtab').nth(sub).click();
		await page.waitForTimeout(400);
	}
	await page.waitForTimeout(800);
}
const tabShots = [
	{ name: 'wui-node-overview', group: null, sub: null, sel: 'main .card' },
	{ name: 'wui-node-interfaces', group: 1, sub: 1, sel: 'main .card' },
	{ name: 'wui-node-internal', group: 1, sub: 3, sel: 'main .card' },
	{ name: 'wui-node-routing', group: 2, sub: 0, sel: '.donuts' },
	{ name: 'wui-node-dns', group: 3, sub: null, sel: 'main .card' }
];
for (const s of tabShots) {
	try {
		const ctx = await newCtx();
		const page = await open(ctx, `/nodes/${NODE}`);
		await openTab(page, s.group, s.sub);
		await shotEl(page, s.sel, s.name);
		await ctx.close();
	} catch (e) {
		console.error('FAIL', s.name, e.message);
	}
}

// --- one-click interconnect wizard, step by step (the .dialog is focused).
//     Fills demo values only — never clicks "创建对等连接", so nothing mutates. ---
try {
	const ctx = await newCtx();
	const page = await open(ctx, `/nodes/${NODE}`);

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

	// ① 基本
	await fill('名称', 'demo-peer');
	await fill('对端 ASN', '4242421111');
	await shotModal('wui-wizard-1');
	await next();

	// ② WireGuard（接口名按 peer 名自动带入；填公钥 + 链路本地对）
	await fill('对端公钥', '+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=');
	await fill('本端 link-local', 'fe80::1');
	await fill('对端 link-local', 'fe80::2');
	await shotModal('wui-wizard-2');
	await next();

	// ③ BGP（主会话由链路本地对派生，只读展示）
	await shotModal('wui-wizard-3');
	await next();

	// ④ 确认
	await shotModal('wui-wizard-4');

	await ctx.close();
} catch (e) {
	console.error('FAIL wizard', e.message);
}

await browser.close();
console.log('done');
