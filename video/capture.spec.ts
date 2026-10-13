import { test, expect, type Locator } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import type { Dataset, Forecast, ModelSummary, Plan } from '../frontend/src/types';
import { navigate } from '../e2e/helpers';

const apiBase = 'http://127.0.0.1:18003/api';
const targetDate = '2026-10-13';
const cutoff = '2026-10-11';
const fixturePath = 'fixtures/synthetic_demo.csv';
const viewport = { width: 1440, height: 1000 };
const targetSeconds = 105;
const sha256 = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');

// Read standard EBML Segment/Info metadata, not a guessed byte-pattern match.
// Playwright's bundled recorder writes Duration as a float in TimecodeScale
// units. This checks the actual finalized media duration without ffprobe/extra
// installations on the CI runner. The MP4 is independently ffprobed later.
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

test('record a real 105-second synthetic BakePlan demonstration', async ({ browser, browserName, request }, testInfo) => {
  const evidenceDir = testInfo.outputPath('evidence');
  await fs.mkdir(evidenceDir, { recursive: true });
  const csv = await fs.readFile(fixturePath, 'utf8');
  const fixtureHash = sha256(csv.replace(/\r\n/g, '\n'));
  expect(fixtureHash).toBe('3811e52ebd6b3bebebafc8b4c479446e8cf263cdd9d056303394778f00d985ce');
  const sourceSha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  expect(sourceSha).toMatch(/^[a-f0-9]{40}$/);
  if (process.env.GITHUB_SHA) expect(sourceSha).toBe(process.env.GITHUB_SHA);
  const initial = await request.get(`${apiBase}/datasets`);
  expect(initial.ok()).toBe(true);
  expect(await initial.json()).toEqual([]);
  const initialPlans = await request.get(`${apiBase}/plans`);
  expect(initialPlans.ok()).toBe(true);
  expect(await initialPlans.json()).toEqual([]);

  const states: Record<string, unknown> = {};
  const scenes: { name: string; start_seconds: number; end_seconds?: number }[] = [];
  const network: { method: string; path: string; status: number; elapsed_seconds: number }[] = [];
  const manifest: Record<string, unknown> = {
    schema_version: 1,
    capture_status: 'incomplete',
    source_sha: sourceSha,
    source_dirty: execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim().length > 0,
    generated_at_utc: new Date().toISOString(),
    provenance: 'Uncut, silent Playwright recordVideo capture of the actual Vite UI and FastAPI. All state-changing steps use real UI controls. No mocks, DOM/CSS edits, overlays, compositing, speed-up, or private data. Automated Chromium recording, not manual Windows or physical-device QA.',
    audio: 'none; no recorded narration',
    runner_os: process.env.RUNNER_OS ?? process.platform,
    os_release: os.release(),
    os_arch: os.arch(),
    browser_name: browserName,
    browser_version: browser.version(),
    node_version: process.version,
    viewport,
    locale: 'ru-RU',
    timezone: 'Asia/Qyzylorda',
    device_scale_factor: 1,
    target_duration_seconds: targetSeconds,
    allowed_duration_seconds: [90, 120],
    fixture: { path: fixturePath, raw_sha256: sha256(csv), canonical_lf_sha256: fixtureHash, source: 'synthetic', row_count: 280 },
    target_date: targetDate,
    cutoff,
    horizon_days: 2,
    mode: 'replay',
    database: 'Fresh dedicated temporary SQLite, empty datasets and plans asserted before capture. Never uploaded.',
    github_run_id: process.env.GITHUB_RUN_ID ?? null,
    github_run_attempt: process.env.GITHUB_RUN_ATTEMPT ?? null,
    states,
    scenes,
    network,
  };

  const context = await browser.newContext({
    baseURL: 'http://127.0.0.1:15175', viewport, locale: 'ru-RU',
    timezoneId: 'Asia/Qyzylorda', deviceScaleFactor: 1, acceptDownloads: true,
    recordVideo: { dir: testInfo.outputPath('raw'), size: viewport },
  });
  const started = performance.now();
  const page = await context.newPage();
  const video = page.video();
  expect(video).not.toBeNull();
  let successful = false;
  let closed = false;
  const elapsed = () => (performance.now() - started) / 1000;

  function scene(name: string) {
    const seconds = elapsed();
    if (scenes.length) scenes[scenes.length - 1].end_seconds = seconds;
    scenes.push({ name, start_seconds: seconds });
  }
  async function holdUntil(seconds: number) {
    // A monotonic timeline preserves real action/training time. Slow work is
    // never edited out or accelerated; a late recording fails the duration gate.
    while (elapsed() < seconds) {
      await new Promise(resolve => setTimeout(resolve, Math.ceil((seconds - elapsed()) * 1000)));
    }
    expect(elapsed(), `Recording overran at the ${seconds}s checkpoint`).toBeLessThan(115);
  }
  async function show(locator: Locator) {
    await locator.scrollIntoViewIfNeeded();
    await expect(locator).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
  }
  async function postFromUi<T>(button: string, endpoint: string): Promise<T> {
    const pending = page.waitForResponse(response =>
      new URL(response.url()).pathname === `/api${endpoint}` && response.request().method() === 'POST', { timeout: 65_000 });
    await page.getByRole('button', { name: button, exact: true }).click();
    const response = await pending;
    network.push({ method: 'POST', path: `/api${endpoint}`, status: response.status(), elapsed_seconds: elapsed() });
    expect(response.ok(), `${endpoint} must use the real successful API`).toBe(true);
    const result: T = await response.json();
    await expect(page.getByRole('button', { name: button, exact: true })).toBeEnabled();
    return result;
  }

  try {
    scene('Empty plan workspace');
    await page.goto('/');
    await expect(page.getByLabel('Базовый метод', { exact: true })).toHaveValue('B1');
    await holdUntil(4);

    scene('Import the committed synthetic CSV through the real file input');
    await navigate(page, 'Данные');
    await page.getByLabel('CSV-файл', { exact: true }).setInputFiles(path.resolve(fixturePath));
    await expect(page.getByRole('status')).toContainText('Файл прочитан');
    await expect(page.getByLabel('Источник', { exact: true })).toHaveValue('synthetic');
    await expect(page.getByLabel('Измеряемая величина', { exact: true })).toHaveValue('sold_units');
    await show(page.getByRole('button', { name: 'Проверить и импортировать', exact: true }));
    await holdUntil(10);
    const dataset = await postFromUi<Dataset>('Проверить и импортировать', '/datasets');
    expect(dataset).toMatchObject({ source: 'synthetic', target: 'sold_units', unit: 'physical_unit', row_count: 280 });
    states.dataset = dataset;
    await show(page.getByTestId('dataset-summary'));
    await expect(page.getByTestId('dataset-summary')).toContainText('Это синтетическая демонстрация');
    await holdUntil(20);

    scene('Select the historical target and cutoff; B1 remains the default');
    await navigate(page, 'План');
    await page.getByLabel('Дата плана', { exact: true }).fill(targetDate);
    await page.getByLabel('Данные доступны до', { exact: true }).fill(cutoff);
    await expect(page.getByLabel('Базовый метод', { exact: true })).toHaveValue('B1');
    await show(page.locator('.planning-controls'));
    await expect(page.locator('#planning')).toContainText('Историческое воспроизведение');
    states.default_plan_method = 'B1';
    await holdUntil(27);

    scene('Train the real model on the imported synthetic history');
    await navigate(page, 'Модель');
    await expect(page.getByLabel('Срез обучения и прогноза', { exact: true })).toHaveValue(cutoff);
    await expect(page.getByLabel('Дата прогноза', { exact: true })).toHaveValue(targetDate);
    const trainingStarted = elapsed();
    const model = await postFromUi<ModelSummary>('Обучить модель', `/datasets/${dataset.id}/train`);
    manifest.training_elapsed_seconds = elapsed() - trainingStarted;
    expect(model).toMatchObject({ status: 'ready', dataset_id: dataset.id, training_cutoff: cutoff });
    expect(Object.keys(model.products)).toEqual(['p1', 'p2', 'p3', 'p4', 'p5']);
    expect(Object.values(model.products).every(product => product.status === 'ready')).toBe(true);
    expect(model.evaluation).toMatchObject({ horizon_days: 2, paired_n: 60, coverage_denominator_n: 60, coverage: 1 });
    expect(model.evaluation!.b1_mae).toBeCloseTo(0.416667, 6);
    expect(model.evaluation!.ml_mae).toBeCloseTo(0.767225, 6);
    expect(model.evaluation!.ml_mae!).toBeGreaterThan(model.evaluation!.b1_mae!);
    states.model = model;
    await show(page.getByTestId('model-summary').getByRole('heading', { name: 'Модель готова', exact: true }));
    await holdUntil(33);

    scene('Read the honest paired holdout evaluation: ML is worse than B1');
    const evaluation = page.getByTestId('model-evaluation');
    await show(evaluation);
    await expect(evaluation).toContainText('0,416667');
    await expect(evaluation).toContainText('0,767225');
    await expect(evaluation).toContainText('60/60');
    await expect(evaluation).toContainText('На этой проверке ML хуже B1');
    await expect(evaluation.locator('.metric-grid')).toBeInViewport({ ratio: 1 });
    await holdUntil(47);

    scene('Calculate and inspect the real diagnostic ML forecast');
    const forecast = await postFromUi<Forecast>('Получить прогноз', '/forecasts');
    expect(forecast.model_id).toBe(model.id);
    expect(forecast.evaluation_applicability).toMatchObject({ applicable: true, requested_horizon_days: 2 });
    expect(forecast.items).toHaveLength(5);
    expect(forecast.items.every(item => item.method === 'ML' && item.fallback_reason == null)).toBe(true);
    expect(forecast.items[0]).toMatchObject({ product_id: 'p1', b1_mean: 26 });
    expect(forecast.items[0].forecast_mean).toBeCloseTo(29.7621242495623, 10);
    states.forecast = forecast;
    await show(page.getByTestId('forecast-result'));
    await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('29.8');
    await expect(page.getByTestId('stale-forecast')).toHaveCount(0);
    await holdUntil(58);

    scene('Choose ML explicitly for this demonstration and calculate a plan');
    await navigate(page, 'План');
    await expect(page.getByLabel('Базовый метод', { exact: true })).toHaveValue('B1');
    await page.getByLabel('Базовый метод', { exact: true }).selectOption('ML');
    await holdUntil(61);
    const plan = await postFromUi<Plan>('Рассчитать план', '/plans');
    expect(plan).toMatchObject({ dataset_id: dataset.id, model_id: model.id, target_date: targetDate, cutoff, mode: 'replay', requested_method: 'ML', status: 'draft', revision: 0 });
    expect(plan.items.map(item => item.recommended_qty)).toEqual([24, 30, 32, 36, 36]);
    expect(plan.items[0]).toMatchObject({ product_id: 'p1', forecast_mean: forecast.items[0].forecast_mean, recommended_qty: 24, confirmed_qty: null, method: 'ML', batch_size: 12, max_daily_qty: 100 });
    states.draft_plan = plan;
    const firstRow = page.getByTestId('plan-table').locator('tbody tr').first();
    const quantity = page.getByLabel('Количество Хлеб пшеничный', { exact: true });
    await show(page.getByTestId('plan-table'));
    await expect(quantity).toHaveValue('24');
    await expect(firstRow).toContainText('29.8');
    await holdUntil(67);

    scene('Manually change p1 to 84; forecast 29.8 and recommendation 24 stay separate');
    await quantity.fill('84');
    await expect(quantity).toHaveValue('84');
    await expect(firstRow).toContainText('Изменено вручную · не сохранено');
    await expect(firstRow.locator('.recommendation-cell')).toHaveText('24');
    await expect(firstRow.locator('.forecast-cell')).toHaveText('29.8');
    await expect(firstRow).toBeInViewport({ ratio: 1 });
    states.manual_adjustment = { product_id: 'p1', quantity: 84, reason: 'Demonstration of owner override only; not a forecast or production recommendation.' };
    await holdUntil(75);

    scene('Confirm and persist the owner-reviewed quantities');
    const confirmed = await postFromUi<Plan>('Подтвердить и сохранить', `/plans/${plan.id}/confirm`);
    expect(confirmed).toMatchObject({ id: plan.id, revision: 1, status: 'confirmed', mode: 'replay' });
    expect(confirmed.items.map(item => item.confirmed_qty)).toEqual([84, 30, 32, 36, 36]);
    expect(confirmed.items[0]).toMatchObject({ forecast_mean: forecast.items[0].forecast_mean, recommended_qty: 24, confirmed_qty: 84, method: 'ML' });
    states.confirmed_plan = confirmed;
    await show(firstRow);
    await expect(firstRow).toContainText('Сохранено: 84 шт.');
    await holdUntil(82);

    scene('Review the confirmed history entry and download its real CSV');
    await navigate(page, 'История');
    const card = page.locator(`.history-card[data-plan-id="${plan.id}"]`);
    await show(card);
    await expect(card).toContainText('Подтверждён');
    await expect(card).toContainText('редакция 1');
    await holdUntil(86);
    const downloadPending = page.waitForEvent('download');
    await card.getByRole('link', { name: 'Экспорт CSV', exact: true }).click();
    const download = await downloadPending;
    const exportedPath = path.join(evidenceDir, 'confirmed-plan.csv');
    await download.saveAs(exportedPath);
    expect(await download.failure()).toBeNull();
    const downloadedCsv = await fs.readFile(exportedPath, 'utf8');
    const exported = await request.get(`${apiBase}/plans/${plan.id}/export`);
    expect(exported.ok()).toBe(true);
    expect(downloadedCsv).toBe(await exported.text());
    expect(downloadedCsv).toContain(',24,84,ML,');
    expect(downloadedCsv.trim().split(/\r?\n/)).toHaveLength(6);
    manifest.csv = { file: 'confirmed-plan.csv', sha256: sha256(downloadedCsv), bytes: Buffer.byteLength(downloadedCsv), suggested_filename: download.suggestedFilename(), downloaded_via: 'Real history-card export link; bytes matched against the real export API.' };
    await holdUntil(91);

    scene('Reload and reopen the same saved revision from history');
    await page.reload();
    await navigate(page, 'История');
    await page.getByRole('button', { name: 'Обновить историю', exact: true }).click();
    await show(card);
    await holdUntil(95);
    await card.getByRole('button', { name: 'Открыть план', exact: true }).click();
    await show(page.getByTestId('plan-table'));
    await expect(quantity).toHaveValue('84');
    await expect(firstRow).toContainText('Сохранено: 84 шт.');
    await expect(firstRow.locator('.recommendation-cell')).toHaveText('24');
    await expect(firstRow.locator('.forecast-cell')).toHaveText('29.8');
    await expect(page.getByTestId('stale-plan')).toHaveCount(0);
    const stored = await request.get(`${apiBase}/plans/${plan.id}`);
    expect(stored.ok()).toBe(true);
    const reopened: Plan = await stored.json();
    expect(reopened).toEqual(confirmed);
    states.reopened_plan_after_page_reload = reopened;
    manifest.persistence_scope = 'Browser page reload and exact API equality for the saved plan. This recording does not claim an OS/process restart.';
    await holdUntil(targetSeconds);
    const elapsedBeforeClose = elapsed();
    expect(elapsedBeforeClose).toBeGreaterThanOrEqual(100);
    expect(elapsedBeforeClose).toBeLessThanOrEqual(110);
    manifest.monotonic_capture_elapsed_seconds = elapsedBeforeClose;
    scenes[scenes.length - 1].end_seconds = elapsedBeforeClose;
    successful = true;
  } finally {
    try {
      await context.close();
      closed = true;
      if (video) {
        const videoPath = path.join(evidenceDir, 'bakeplan-demo.webm');
        await video.saveAs(videoPath);
        const videoBytes = await fs.readFile(videoPath);
        const mediaDuration = webmDurationSeconds(videoBytes);
        manifest.video = { file: 'bakeplan-demo.webm', sha256: sha256(videoBytes), bytes: videoBytes.length, duration_seconds: mediaDuration, duration_source: 'Finalized WebM Segment/Info Duration × TimecodeScale', width: viewport.width, height: viewport.height };
        if (successful) {
          expect(mediaDuration).toBeGreaterThanOrEqual(90);
          expect(mediaDuration).toBeLessThanOrEqual(120);
          manifest.capture_status = 'complete';
        }
      }
    } finally {
      if (!closed) await context.close().catch(() => {});
      await fs.writeFile(path.join(evidenceDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
      await fs.copyFile('docs/DEMO.md', path.join(evidenceDir, 'DEMO.md'));
    }
  }
});
