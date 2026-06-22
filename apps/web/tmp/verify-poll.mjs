import { chromium } from 'playwright-core';

const BASE = 'http://127.0.0.1:5173';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
await ctx.addInitScript(() => {
	localStorage.setItem('dn42.admin.token', 'dev-admin-token');
	localStorage.setItem('dn42.admin.apiBase', 'http://127.0.0.1:8000');
	localStorage.setItem('dn42.refreshMs', '2000'); // aggressive 2s polling
	localStorage.setItem('dn42.locale', 'zh');
});
const page = await ctx.newPage();
await page.goto(BASE + '/nodes/edge1', { waitUntil: 'load' });
await page.waitForSelector('.tabs', { timeout: 15000 });
await page.waitForTimeout(1000);

// --- Test A: typing in the Edit modal survives multiple poll cycles ---
await page.getByRole('button', { name: '编辑' }).first().click();
await page.waitForTimeout(400);
const asn = page.locator('.dialog input').first();
await asn.click();
await asn.fill('');
await asn.type('99887766', { delay: 25 });
await page.waitForTimeout(5000); // > 2 poll intervals while modal open
const modalOpen = await page.locator('.dialog').isVisible();
const asnVal = await asn.inputValue();
const focused = await asn.evaluate((el) => el === document.activeElement);
console.log(`A: modalOpen=${modalOpen} value="${asnVal}" focusKept=${focused}`);

// --- Test B: with modal closed, content stays mounted across polls (no unmount) ---
await page.getByRole('button', { name: '取消' }).click();
await page.waitForTimeout(400);
let everUnmounted = false;
for (let i = 0; i < 8; i++) {
	const tabs = await page.locator('.tabs').count();
	const loadingShown = await page.locator('.empty', { hasText: '加载中' }).count();
	if (tabs === 0 || loadingShown > 0) everUnmounted = true;
	await page.waitForTimeout(400);
}
console.log(`B: contentStayedMounted=${!everUnmounted}`);

await browser.close();
console.log('done');
