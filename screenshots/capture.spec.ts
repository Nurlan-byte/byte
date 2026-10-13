import { test, expect, type Locator, type Page } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';

const targetDate = '2026-10-13';
const cutoff = '2026-10-11';
const fixturePath = 'fixtures/synthetic_demo.csv';
const apiBase = 'http://127.0.0.1:18002/api';
const sha256 = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');

type EvidenceFile = {
  file: string;
  description: string;
  sha256: string;
  bytes: number;
  viewport?: { width: number; height: number } | null;
  capture_kind?: 'section' | 'viewport';
  png_size?: { width: number; height: number };
};

async function postFromUi(page: Page, buttonName: string, endpoint: string) {
  const pending = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api${endpoint}` && response.request().method() === 'POST', { timeout: 65_000 });
  await page.getByRole('button', { name: buttonName, exact: true }).click();
  const response = await pending;
  expect(response.ok(), `${endpoint} must return a real successful API response`).toBe(true);
  return response.json();
}

test('capture real synthetic desktop, laptop and mobile planning evidence', async ({ page, request, browser, browserName }, testInfo) => {
  const evidenceDir = testInfo.outputPath('evidence');
  await fs.mkdir(evidenceDir, { recursive: true });
  const csv = await fs.readFile(fixturePath, 'utf8');
  const sourceSha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  expect(sourceSha).toMatch(/^[a-f0-9]{40}$/);
  if (process.env.GITHUB_SHA) expect(sourceSha).toBe(process.env.GITHUB_SHA);
  const files: EvidenceFile[] = [];
  const manifest: Record<string, unknown> = {
    schema_version: 1,
    capture_status: 'incomplete',
    source_sha: sourceSha,
    source_dirty: execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim().length > 0,
    generated_at_utc: new Date().toISOString(),
    provenance: 'Automated Playwright CI Chromium captures of the running Vite UI and FastAPI application. No mocked responses, edited DOM, compositing, or private data. Not manual Windows or physical-device QA.',
    runner_os: process.env.RUNNER_OS ?? process.platform,
    browser_name: browserName,
    browser_version: browser.version(),
    node_version: process.version,
    locale: 'ru-RU',
    timezone: 'Asia/Qyzylorda',
    device_scale_factor: 1,
    fixture: { path: fixturePath, raw_sha256: sha256(csv), canonical_lf_sha256: sha256(csv.replace(/\r\n/g, '\n')), source: 'synthetic', row_count: csv.trim().split(/\r?\n/).length - 1 },
    target_date: targetDate,
    cutoff,
    horizon_days: 2,
    mode: 'replay',
    database: 'Dedicated empty temporary SQLite; distinct from regression tests and never uploaded.',
    github_run_id: process.env.GITHUB_RUN_ID ?? null,
    github_run_attempt: process.env.GITHUB_RUN_ATTEMPT ?? null,
    files,
  };

  async function capture(file: string, description: string, section?: Locator) {
    await page.evaluate(() => document.fonts.ready);
    await expect(page.getByRole('alert')).toHaveCount(0);
    const options = { path: path.join(evidenceDir, file), animations: 'disabled' as const, caret: 'hide' as const };
    const png = section ? await section.screenshot(options) : await page.screenshot({ ...options, fullPage: false });
    expect(png.subarray(1, 4).toString()).toBe('PNG');
    files.push({
      file, description, sha256: sha256(png), bytes: png.length,
      viewport: page.viewportSize(), capture_kind: section ? 'section' : 'viewport',
      png_size: { width: png.readUInt32BE(16), height: png.readUInt32BE(20) },
    });
  }

  try {
    // Assert an empty real backend rather than relying on test ordering.
    const initialDatasets = await request.get(`${apiBase}/datasets`);
    expect(initialDatasets.ok()).toBe(true);
    expect(await initialDatasets.json()).toEqual([]);
    await page.goto('/');
    await page.getByLabel('ID пекарни', { exact: true }).fill('synthetic-defense-capture');
    await page.getByLabel('Источник', { exact: true }).selectOption('synthetic');
    await page.getByLabel('Происхождение данных', { exact: true }).fill('Committed synthetic BakePlan fixture; automated CI screenshots, not real bakery results');
    await page.getByLabel('Содержимое CSV', { exact: true }).fill(csv);
    const dataset = await postFromUi(page, 'Проверить и импортировать', '/datasets');
    expect(dataset).toMatchObject({ source: 'synthetic', target: 'sold_units', unit: 'physical_unit' });
    await expect(page.getByTestId('dataset-summary')).toContainText('Текущий сохранённый набор');
    await page.getByLabel('Дата плана', { exact: true }).fill(targetDate);
    await page.getByLabel('Данные доступны до', { exact: true }).fill(cutoff);
    await page.evaluate(() => window.scrollTo(0, 0));
    await capture('01-desktop-import.png', 'Desktop viewport: real imported synthetic history and the available workflow.');

    const fallback = await postFromUi(page, 'Получить прогноз', '/forecasts');
    expect(fallback.model_id).toBeNull();
    expect(fallback.items.every((item: { method: string; fallback_reason: string | null }) => item.method === 'B1' && !!item.fallback_reason)).toBe(true);
    await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('Причина перехода');
    await capture('02-desktop-explicit-fallback.png', 'Requested ML before training: actual B1 and explicit missing-model fallback for each product.', page.getByTestId('forecast-result'));

    const model = await postFromUi(page, 'Обучить модель', `/datasets/${dataset.id}/train`);
    expect(model.status).toBe('ready');
    expect(model.evaluation).toMatchObject({ horizon_days: 2, paired_n: 60, coverage: 1 });
    await expect(page.getByTestId('model-summary')).toContainText('Модель готова');
    await page.getByTestId('model-summary').getByText('Диагностика оценки модели', { exact: true }).click();
    await expect(page.getByTestId('model-summary').getByText(/Горизонт оценки: 2 дн./)).toBeVisible();
    await capture('03-desktop-model-evaluation.png', 'Technical readiness and synthetic t−2 evaluation, including paired coverage. These metrics do not establish real accuracy.', page.getByTestId('model-summary'));

    const forecast = await postFromUi(page, 'Получить прогноз', '/forecasts');
    expect(forecast.model_id).toBe(model.id);
    expect(forecast.evaluation_applicability.applicable).toBe(true);
    expect(forecast.items.every((item: { method: string; forecast_mean: number }) => item.method === 'ML' && Number.isFinite(item.forecast_mean) && item.forecast_mean >= 0)).toBe(true);
    await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('ML');
    await expect(page.getByTestId('stale-forecast')).toHaveCount(0);
    await capture('04-desktop-ml-forecast.png', 'Actual ML forecast compared with B1 at the evaluated two-day horizon; diagnostic only.', page.getByTestId('forecast-result'));

    await page.setViewportSize({ width: 1366, height: 900 });
    await page.getByLabel('Базовый метод', { exact: true }).selectOption('ML');
    const plan = await postFromUi(page, 'Рассчитать план', '/plans');
    expect(plan.items[0]).toMatchObject({ product_id: 'p1', recommended_qty: 24, confirmed_qty: null, method: 'ML' });
    expect(plan.items[0].forecast_mean).toBe(forecast.items[0].forecast_mean);
    expect(plan.model_id).toBe(model.id);
    const firstRow = page.getByTestId('plan-table').locator('tbody tr').first();
    const quantity = firstRow.getByRole('spinbutton');
    await quantity.fill('90');
    await expect(page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true })).toBeDisabled();
    await quantity.fill('84');
    await expect(page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true })).toBeEnabled();
    await expect(quantity).toHaveValue('84');
    await expect(firstRow).toContainText('Ещё не сохранено');
    await capture('05-laptop-adjusted-plan.png', 'Laptop layout: actual ML forecast, recommended batch 24, manual draft quantity 84 before confirmation.', page.locator('#planning'));

    const confirmed = await postFromUi(page, 'Подтвердить и сохранить', `/plans/${plan.id}/confirm`);
    expect(confirmed).toMatchObject({ status: 'confirmed', revision: 1 });
    expect(confirmed.items[0].confirmed_qty).toBe(84);
    await expect(page.getByRole('status')).toContainText('Подтверждённый план сохранён');
    // Reload and reopen through real history to prove this is persisted state.
    await page.reload();
    await page.getByRole('button', { name: 'Обновить историю', exact: true }).click();
    await page.getByRole('button', { name: /2026-10-13.*synthetic.*confirmed/ }).click();
    await expect(quantity).toHaveValue('84');
    await expect(firstRow).toContainText('Сохранено: 84 шт.');
    await expect(page.getByTestId('stale-plan')).toHaveCount(0);
    await capture('06-laptop-saved-plan.png', 'Laptop layout: persisted confirmed revision reopened from history after page reload.', page.locator('#planning'));
    await capture('07-laptop-history-export.png', 'Real saved history; the reopened plan provides the CSV export link.', page.locator('#history'));

    const stored = await request.get(`${apiBase}/plans/${plan.id}`);
    expect(stored.ok()).toBe(true);
    expect(await stored.json()).toEqual(confirmed);
    const exported = await request.get(`${apiBase}/plans/${plan.id}/export`);
    expect(exported.ok()).toBe(true);
    const exportedCsv = await exported.text();
    expect(exportedCsv).toContain(',24,84,ML,');
    await fs.writeFile(path.join(evidenceDir, 'confirmed-plan.csv'), exportedCsv);
    files.push({ file: 'confirmed-plan.csv', description: 'Real API export of the confirmed synthetic plan shown in screenshots.', sha256: sha256(exportedCsv), bytes: Buffer.byteLength(exportedCsv) });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#planning').evaluate(element => element.scrollIntoView({ block: 'start' }));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await capture('08-mobile-plan-controls.png', 'Mobile-width Chromium viewport: real responsive plan controls. This is viewport emulation, not a physical phone test.');
    const tableContainer = page.getByTestId('plan-table').locator('..');
    await page.getByTestId('plan-table').evaluate(element => element.scrollIntoView({ block: 'start' }));
    await tableContainer.evaluate(element => {
      const input = element.querySelector('input')!;
      element.scrollLeft += input.getBoundingClientRect().left - element.getBoundingClientRect().left - 110;
    });
    await expect(quantity).toBeInViewport({ ratio: 1 });
    await capture('09-mobile-saved-quantity.png', 'Mobile viewport: internally scrolled table showing the saved 84-unit quantity, separate from forecast and recommendation.');
    await firstRow.getByText('Источники и модель', { exact: true }).click();
    await tableContainer.evaluate(element => {
      const methodCell = element.querySelector('tbody tr td:last-child')!;
      element.scrollLeft += methodCell.getBoundingClientRect().left - element.getBoundingClientRect().left;
    });
    await page.getByTestId('plan-table').evaluate(element => element.scrollIntoView({ block: 'start' }));
    await expect(firstRow.getByText('Подходящих наблюдений: 4', { exact: true })).toBeVisible();
    await expect(firstRow.locator('td').last().locator('strong')).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await capture('10-mobile-method-details.png', 'Mobile viewport: horizontally scrolled actual-method and source-date details, with page width still contained.');

    manifest.dataset_id = dataset.id;
    manifest.model_id = model.id;
    manifest.protocol_version = model.protocol_version;
    manifest.built_in_evaluation = { source: 'saved model built-in chronological holdout, not the separate forward CLI', ...model.evaluation };
    manifest.plan_id = plan.id;
    manifest.confirmed_revision = confirmed.revision;
    manifest.p1 = { forecast: plan.items[0].forecast_mean, recommended: 24, confirmed: 84, method: 'ML' };
    manifest.capture_status = 'complete';
  } finally {
    await fs.writeFile(path.join(evidenceDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
    await fs.copyFile('docs/SCREENSHOTS.md', path.join(evidenceDir, 'SCREENSHOTS.md'));
  }
});
