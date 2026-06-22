// Documentation screenshots for docs/guides/web-ui.md.
//
// Captures the admin UI (zh locale) into docs/images/. Every shot is scoped to
// the component it illustrates (a card / panel / dialog) — never the whole
// browser window with the sidebar — so the docs stay focused and legible.
//
// Requires a local stack with DEMO DATA already seeded (the production lifespan
// no longer auto-seeds, and routing/trends/DNS pages are empty without agent
// reports):
//   1. control-server on :8001  (admin token "dev-admin-token", CORS *)
//   2. python apps/web/scripts/seed_docshots.py   (provisions edge1 + feeds data)
//   3. vite dev on :5174
// Then, from apps/web:  node scripts/doc-shots.mjs
//
// See docs/guides/web-ui.md (## 截图怎么再生成) for the full one-liners.
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
	const ctx = await browser.newContext({ viewport: { width: 1320, height: 1000 }, colorScheme: 'light', deviceScaleFactor: 2 });
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

// Screenshot a single element (focused) rather than the full page.
async function shotEl(page, selector, name, { nth = 0 } = {}) {
	const el = page.locator(selector).nth(nth);
	await el.waitFor({ state: 'visible', timeout: 8000 });
	await el.scrollIntoViewIfNeeded();
	await page.waitForTimeout(500); // let charts/sparklines settle
	await el.screenshot({ path: `${OUT}/${name}.png` });
	console.log('shot', name);
}

async function open(ctx, path) {
	const page = await ctx.newPage();
	await page.goto(BASE + path, { waitUntil: 'networkidle' });
	await page.waitForTimeout(1200);
	return page;
}

// --- focused, single-element shots on standalone pages ---
// [name, path, selector] — selector is the component the doc section describes.
const shots = [
	['wui-login', '/login', '.login', false],
	['wui-dashboard', '/', '.health-card', true],
	['wui-fleet-routing', '/', '.routing-card', true],
	['wui-nodes', '/nodes', 'main .card', true],
	['wui-registrations', '/registrations', 'main .card', true],
	['wui-enrollment', '/enrollment-tokens', 'main .card', true],
	['wui-provision', '/provision', 'main .card', true],
	['wui-audit', '/audit', 'main .card', true]
];

for (const [name, path, selector] of shots) {
	try {
		const ctx = await newCtx();
		const page = await open(ctx, path);
		await shotEl(page, selector, name);
		await ctx.close();
	} catch (e) {
		console.error('FAIL', name, e.message);
	}
}

// --- DNS groups: expand a group's zones + a zone's records, then capture the
//     whole content column (groups → zones → records) sans sidebar. ---
try {
	const ctx = await newCtx();
	const page = await open(ctx, '/dns-groups');
	// row action #0 = "区域" (select group) → reveals the zones card
	await page.locator('table tbody tr').first().locator('.actions button').first().click();
	await page.waitForTimeout(700);
	// open the records of the populated forward zone (reverse zone sorts first but
	// has no records) → reveals the records panel below the zones table
	await page.locator('.card').last().locator('tr', { hasText: 'example.dn42' })
		.locator('.actions button').first().click();
	await page.waitForTimeout(700);
	await shotEl(page, 'main', 'wui-dns-groups');
	await ctx.close();
} catch (e) {
	console.error('FAIL wui-dns-groups', e.message);
}

// --- node detail tabs — capture the tab's content card (or its headline
//     component), which excludes the page chrome and sidebar. ---
// TABS index: 0 overview · 1 peerings · 2 interfaces · 3 bgp · 4 internal ·
//             5 routing · 6 dns · 7 generations · 8 status · 9 desired · 10 tokens
const tabShots = [
	{ name: 'wui-node-overview', tab: null, sel: 'main .card' },
	{ name: 'wui-node-interfaces', tab: 2, sel: 'main .card' },
	{ name: 'wui-node-internal', tab: 4, sel: 'main .card' },
	{ name: 'wui-node-routing', tab: 5, sel: '.donuts' },
	{ name: 'wui-node-dns', tab: 6, sel: 'main .card' }
];
for (const s of tabShots) {
	try {
		const ctx = await newCtx();
		const page = await open(ctx, `/nodes/${NODE}`);
		if (s.tab !== null) {
			await page.locator('.tabs .tab').nth(s.tab).click();
			await page.waitForTimeout(1200);
		}
		await shotEl(page, s.sel, s.name);
		await ctx.close();
	} catch (e) {
		console.error('FAIL', s.name, e.message);
	}
}

// --- peer interconnect wizard, step by step (the .dialog is already focused) ---
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
