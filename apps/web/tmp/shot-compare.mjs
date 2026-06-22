import { chromium } from 'playwright-core';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 700, height: 360 } });
const page = await ctx.newPage();
await page.goto('http://127.0.0.1:5173/logo-compare.html', { waitUntil: 'load' });
await page.waitForTimeout(800);
await page.screenshot({ path: 'tmp/shots/logo-compare.png' });
await browser.close();
console.log('done');
