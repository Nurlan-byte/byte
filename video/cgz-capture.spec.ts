import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { navigate, openDetails } from '../e2e/helpers';

const sha256 = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');

function webmDurationSeconds(bytes: Buffer): number {
  function vint(offset: number, keepMarker: boolean) {
    const first = bytes[offset];
    if (!first) throw new Error(`Invalid EBML integer at ${offset}`);
    let width = 1;
    while (!(first & (0x80 >> (width - 1)))) width++;
    if (width > 8 || offset + width > bytes.length) throw new Error('Invalid EBML width');
    let value = keepMarker ? first : first & ((0x80 >> (width - 1)) - 1);
    let unknown = !keepMarker && value === ((0x80 >> (width - 1)) - 1);
    for (let index = 1; index < width; index++) {
      value = value * 256 + bytes[offset + index];
      unknown = unknown && bytes[offset + index] === 255;
    }
    return { width, value, unknown };
  }
  function elements(start: number, end: number) {
    const result: { id: number; start: number; end: number }[] = [];
    for (let offset = start; offset < end;) {
      const id = vint(offset, true);
      const size = vint(offset + id.width, false);
      const payload = offset + id.width + size.width;
      const stop = size.unknown ? end : payload + size.value;
      if (stop > end || stop <= offset) throw new Error('Invalid EBML element boundary');
      result.push({ id: id.value, start: payload, end: stop });
      offset = stop;
    }
    return result;
  }
  const segment = elements(0, bytes.length).find(element => element.id === 0x18538067);
  if (!segment) throw new Error('WebM Segment is missing');
  const info = elements(segment.start, segment.end).find(element => element.id === 0x1549a966);
  if (!info) throw new Error('WebM Info is missing');
  let scale = 1_000_000;
  let duration = Number.NaN;
  for (const element of elements(info.start, info.end)) {
    const size = element.end - element.start;
    if (element.id === 0x2ad7b1) scale = bytes.readUIntBE(element.start, size);
    if (element.id === 0x4489) {
      if (size !== 4 && size !== 8) throw new Error('Invalid WebM Duration float');
      duration = size === 4 ? bytes.readFloatBE(element.start) : bytes.readDoubleBE(element.start);
    }
  }
  if (!Number.isFinite(duration)) throw new Error('Finalized WebM duration is missing');
  return duration * scale / 1_000_000_000;
}

test('record a real synthetic Cloud Game Zone procurement demonstration', async ({ browser, request }, testInfo) => {
  test.setTimeout(180_000);
  const evidenceDir = testInfo.outputPath('evidence');
  await fs.mkdir(evidenceDir, { recursive: true });
  const csv = await fs.readFile('fixtures/cloud_game_zone_synthetic.csv');
  const catalog = JSON.parse(await fs.readFile('fixtures/cloud_game_zone_synthetic_catalog.json', 'utf8')) as {
    product_id: string; name: string; pack_size: number; max_order_qty: number;
  }[];
  expect(sha256(csv)).toBe('a0ecee1cf940d16372d7e1f2a0fde07daf4d88792a14db95f714c8cb967f2944');
  const sourceSha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const sourceDirty = !!execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim();
  if (process.env.GITHUB_SHA) expect(sourceSha).toBe(process.env.GITHUB_SHA);
  const initial = await request.get('http://127.0.0.1:18003/api/datasets');
  expect(initial.ok()).toBe(true);
  expect(await initial.json()).toEqual([]);

  const context = await browser.newContext({
    baseURL: 'http://127.0.0.1:15175', viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU', timezoneId: 'Asia/Qyzylorda', deviceScaleFactor: 1,
    acceptDownloads: true, recordVideo: { dir: testInfo.outputPath('raw'), size: { width: 1440, height: 1000 } },
  });
  const page = await context.newPage();
  const video = page.video();
  expect(video).not.toBeNull();
  const started = performance.now();
  const scenes: { name: string; elapsed_seconds: number }[] = [];
  const scene = (name: string) => scenes.push({ name, elapsed_seconds: (performance.now() - started) / 1000 });
  const holdUntil = async (seconds: number) => {
    const remaining = seconds * 1000 - (performance.now() - started);
    if (remaining > 0) await page.waitForTimeout(remaining);
  };
  let complete = false;
  let planId = '';
  let exportHash = '';
  try {
    await page.goto('/');
    await page.locator('#planning-kind').selectOption('procurement');
    scene('procurement workspace');
    await holdUntil(5);
    await navigate(page, 'Данные');
    await page.locator('#proc-file').setInputFiles('fixtures/cloud_game_zone_synthetic.csv');
    await expect(page.locator('#import [role="status"]')).toContainText('Файл:');
    for (const [index, item] of catalog.entries()) {
      if (index) await page.getByRole('button', { name: 'Добавить товар' }).click();
      await page.locator(`#proc-product-${index}`).fill(item.product_id);
      await page.locator(`#proc-name-${index}`).fill(item.name);
      await page.locator(`#proc-pack-${index}`).fill(String(item.pack_size));
      await page.locator(`#proc-max-${index}`).fill(String(item.max_order_qty));
    }
    await page.locator('#proc-source').selectOption('synthetic');
    await page.locator('#proc-shop').fill('cgz-synthetic-demo');
    await page.locator('#proc-provenance').fill('Полностью синтетический сценарий Cloud Game Zone; предварительные допущения');
    scene('synthetic CSV and provisional catalog');
    await holdUntil(18);
    const importing = page.waitForResponse(r => r.url().endsWith('/api/datasets') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Проверить и импортировать закупку' }).click();
    const imported = await importing;
    expect(imported.status()).toBe(201);
    const dataset = await imported.json();
    expect(dataset).toMatchObject({ row_count: 546, planning_kind: 'procurement', source: 'synthetic' });
    await expect(page.getByTestId('dataset-summary')).toContainText('546');
    scene('validated import');
    await holdUntil(25);

    await navigate(page, 'Модель');
    await page.getByLabel('Срез обучения и прогноза').fill('2026-10-14');
    await page.getByLabel('Дата прогноза', { exact: true }).fill('2026-10-16');
    const training = page.waitForResponse(r => r.url().includes('/train') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Подготовить экспериментальный расчёт' }).click();
    expect((await training).ok()).toBe(true);
    await expect(page.getByTestId('model-summary')).toContainText('Расчёт готов');
    scene('trained model with evaluation');
    await holdUntil(37);
    await page.getByRole('button', { name: 'Проверить прогноз' }).click();
    await expect(page.getByTestId('forecast-table')).toContainText('По похожим дням недели');
    await expect(page.getByTestId('forecast-table')).toContainText('Экспериментальный расчёт');
    scene('ML and B1 forecast');
    await holdUntil(46);

    await navigate(page, 'План');
    await page.getByLabel('Дата продаж', { exact: true }).fill('2026-10-16');
    await page.getByLabel('Данные доступны до', { exact: true }).fill('2026-10-14');
    await page.getByLabel('Срок поставки, дней').fill('1');
    await openDetails(page, 'Настройки прогноза');
    await page.getByLabel('Способ расчёта закупки').selectOption('ML');
    for (const item of catalog) await page.getByLabel(`Доступный остаток: ${item.name}`).fill('0');
    const planning = page.waitForResponse(r => r.url().endsWith('/api/plans') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Рассчитать заказ', exact: true }).click();
    const plan = await (await planning).json();
    planId = plan.id;
    expect(plan.items).toHaveLength(6);
    expect(plan.items.filter((item: { method: string }) => item.method === 'ML')).toHaveLength(3);
    expect(plan.items.filter((item: { method: string }) => item.method === 'B1')).toHaveLength(3);
    const water = page.getByTestId('procurement-table').locator('tbody tr').filter({ hasText: 'Вода в бутылке' });
    await expect(water).toContainText('24');
    scene('six proposed orders');
    await holdUntil(61);
    await water.getByRole('spinbutton').fill('36');
    await expect(water.getByRole('spinbutton')).toHaveValue('36');
    scene('owner changes water to 36');
    await holdUntil(71);
    await page.getByRole('button', { name: 'Подтвердить заказ' }).click();
    await expect(page.getByRole('status')).toContainText('сохран');
    scene('confirmed order');
    await holdUntil(79);
    await navigate(page, 'История');
    await expect(page.locator(`.history-card[data-plan-id="${planId}"]`)).toBeVisible();
    const downloadEvent = page.waitForEvent('download');
    await page.locator(`.history-card[data-plan-id="${planId}"]`).getByRole('link', { name: 'Экспорт CSV' }).click();
    const download = await downloadEvent;
    const exported = await fs.readFile((await download.path())!);
    exportHash = sha256(exported);
    expect(exported.toString('utf8')).toContain(',36,');
    await fs.writeFile(path.join(evidenceDir, 'confirmed-order.csv'), exported);
    scene('history and CSV export');
    await holdUntil(96);
    complete = true;
  } finally {
    await context.close();
    if (complete) {
      const target = path.join(evidenceDir, 'cgz-synthetic-demo.webm');
      await video!.saveAs(target);
      const bytes = await fs.readFile(target);
      const duration = webmDurationSeconds(bytes);
      expect(duration).toBeGreaterThanOrEqual(90);
      expect(duration).toBeLessThanOrEqual(120);
      await fs.writeFile(path.join(evidenceDir, 'manifest.json'), JSON.stringify({
        capture_status: 'complete', source_sha: sourceSha, source_dirty: sourceDirty,
        provenance: 'Uncut silent Chromium recording of real Vite/FastAPI UI using only fully synthetic, provisional Cloud Game Zone fixtures; separate temporary SQLite; no mocked responses or private source data.',
        csv_sha256: sha256(csv), plan_id: planId, export_sha256: exportHash,
        video_sha256: sha256(bytes), video_duration_seconds: duration,
        viewport: { width: 1440, height: 1000 }, scenes,
      }, null, 2) + '\n');
    }
  }
});
