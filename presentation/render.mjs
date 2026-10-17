import { chromium } from '@playwright/test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';

const dir = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  const failures = [];
  const pageUrl = pathToFileURL(path.join(dir, 'index.html')).href;
  page.on('request', request => { if (request.url() !== pageUrl) failures.push(`Extra request: ${request.url()}`); });
  page.on('requestfailed', request => failures.push(`Failed request: ${request.url()}`));
  await page.goto(pageUrl);
  if (!(await page.locator('img').evaluateAll(images => images.every(image => image.src.startsWith('data:'))))) throw new Error('Presentation images must be embedded');
  await page.locator('img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
  if (await page.locator('.slide').count() !== 8) throw new Error('Expected exactly 8 slides');
  await fs.mkdir(path.join(dir, 'qa'), { recursive: true });
  for (let i = 0; i < 8; i++) {
    const state = await page.evaluate(() => {
      const active = document.querySelector('.slide.active');
      const bounds = active.getBoundingClientRect();
      return { number: active.querySelector('.number').textContent, visible: [...document.querySelectorAll('.slide')].filter(el => getComputedStyle(el).display !== 'none').length, bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } };
    });
    if (state.visible !== 1 || state.number !== `${String(i + 1).padStart(2, '0')} / 08`) throw new Error(`Navigation mismatch on slide ${i+1}: ${JSON.stringify(state)}`);
    await page.screenshot({ path: path.join(dir, 'qa', `slide-${String(i+1).padStart(2,'0')}.png`) });
    if (i < 7) await page.keyboard.press('ArrowRight');
  }
  await page.keyboard.press('Space');
  if (!(await page.locator('.s8').evaluate(el => el.classList.contains('active')))) throw new Error('End navigation overflowed');
  await page.keyboard.press('ArrowLeft');
  if (!(await page.locator('.s7').evaluate(el => el.classList.contains('active')))) throw new Error('Back navigation failed');
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.reload();
  for (let i = 0; i < 8; i++) {
    await page.screenshot({ path: path.join(dir, 'qa', `slide-${String(i+1).padStart(2,'0')}-1366.png`) });
    if (i < 7) await page.keyboard.press('ArrowRight');
  }
  await page.emulateMedia({ media: 'print' });
  await page.pdf({ path: path.join(dir, 'BakePlan_pitch.pdf'), printBackground: true, preferCSSPageSize: true });
  if (failures.length) throw new Error(failures.join('\n'));
  console.log('Rendered 8 slide PNGs and offline PDF; keyboard navigation passed.');
} finally {
  await browser.close();
}
