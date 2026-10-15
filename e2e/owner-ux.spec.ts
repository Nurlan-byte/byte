import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawn, type ChildProcess } from 'node:child_process';
import { navigate, openDetails } from './helpers';

async function importFile(page: Page) {
  await navigate(page, 'Данные');
  await page.getByLabel('CSV-файл', { exact: true }).setInputFiles('fixtures/synthetic_demo.csv');
  await page.getByRole('button', { name: 'Проверить и импортировать', exact: true }).click();
  await expect(page.getByTestId('dataset-summary')).toBeVisible();
}

test('first visit, file selection and successful import are distinct and keyboard accessible', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Подготовим выпечку на день' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Рассчитать план' })).toHaveCount(0);
  const start = page.getByRole('button', { name: 'Перейти к данным', exact: true });
  await expect(start).toBeEnabled();
  await start.focus();
  expect(await start.evaluate(el => getComputedStyle(el).outlineStyle)).toBe('solid');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Какие данные нужны?' })).toBeVisible();
  const posts: string[] = [];
  page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
  await page.getByLabel('CSV-файл', { exact: true }).setInputFiles('fixtures/synthetic_demo.csv');
  await expect(page.getByTestId('import-stage')).toContainText('ещё не импортирован');
  await expect(page.getByTestId('dataset-summary')).toHaveCount(0);
  expect(posts).toEqual([]);
  await page.getByRole('button', { name: 'Проверить и импортировать', exact: true }).click();
  await expect(page.getByTestId('import-stage')).toContainText('проверены и сохранены');
  expect(posts.filter(url => url.endsWith('/api/datasets'))).toHaveLength(1);
  await expect(page.getByTestId('dataset-period')).toHaveText('17.08.2026 — 11.10.2026');
  await page.reload();
  await expect(page.getByTestId('workspace-source')).toContainText('17.08.2026 — 11.10.2026');
  await expect(page.getByLabel('Способ расчёта', { exact: true })).toHaveValue('B1');
  await navigate(page, 'Данные');
  await expect(page.getByTestId('unsaved-import')).toHaveCount(0);
  await openDetails(page, 'Продолжить работу с сохранённым набором');
  const selected = page.getByLabel('Сохранённый набор', { exact: true });
  await expect(selected).toBeEnabled();
  const id = await selected.inputValue();
  expect(id.length).toBeGreaterThan(0);
  await expect(selected.locator(`option[value="${id}"]`)).toContainText('Синтетические данные');
  await page.getByRole('button', { name: 'Открыть набор', exact: true }).click();
  await expect(page.getByTestId('dataset-period')).toHaveText('17.08.2026 — 11.10.2026');
});

test('missing weekday history stays unknown, manual fields explain batches and errors receive focus', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 1000 });
  await page.goto('/');
  await navigate(page, 'Данные');
  await openDetails(page, 'Вставить или проверить CSV');
  await page.getByLabel('Содержимое CSV').fill('date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status\n2026-10-05,p1,true,0,12,12,0,0,0,none');
  await page.getByRole('button', { name: 'Проверить и импортировать' }).click();
  await expect(page.getByTestId('dataset-summary')).toBeVisible();
  await navigate(page, 'План');
  await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-13');
  await page.getByLabel('Данные доступны до', { exact: true }).fill('2026-10-11');
  await page.getByRole('button', { name: 'Рассчитать план' }).click();
  const row = page.getByTestId('plan-table').locator('tbody tr').first();
  await expect(row.locator('.forecast-cell')).toHaveText('Нет оценки');
  await expect(row.locator('.recommendation-cell')).toContainText('Ручная заготовка');
  const input = row.getByRole('spinbutton');
  await input.fill('90');
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  await expect(row).toContainText('кратное 12, не больше 96');
  await expect(page.getByRole('button', { name: 'Подтвердить и сохранить' })).toBeDisabled();
  await input.fill('84');
  await expect(input).toHaveAttribute('aria-invalid', 'false');
  await input.evaluate(element => element.scrollIntoView({ block: 'center' }));
  await expect(input).toBeInViewport({ ratio: 1 });
  expect(await page.getByTestId('plan-table').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await navigate(page, 'Данные');
  await page.getByLabel('CSV-файл', { exact: true }).setInputFiles('fixtures/invalid_balance.csv');
  await page.getByRole('button', { name: 'Проверить и импортировать' }).click();
  await expect(page.getByRole('alert')).toBeFocused();
  await expect(page.getByRole('alert').locator('.error-message')).toContainText('Строка 2: Нарушен баланс');
  await navigate(page, 'План');
  await expect(input).toHaveValue('84');
});

test('unknown persisted references recover with guidance and working history', async ({ page }) => {
  await page.addInitScript(() => { localStorage.setItem('bakeplan.owner.dataset', 'missing-owner-dataset'); });
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Откройте сохранённый набор');
  await expect(page.getByRole('button', { name: 'Перейти к данным', exact: true })).toBeEnabled();
  await navigate(page, 'История');
  await expect(page.getByRole('button', { name: 'Обновить историю' })).toBeEnabled();
});

test('owner UI import → train → forecast → edit → confirm → export → real process restart', async ({ page, request }) => {
  test.setTimeout(120_000);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'bakeplan-owner-'));
  const db = path.join(directory, 'owner.sqlite3');
  const apiBase = 'http://127.0.0.1:18004';
  const uiBase = 'http://127.0.0.1:15176';
  let backend: ChildProcess | undefined;
  let frontend: ChildProcess | undefined;
  async function stop(child: ChildProcess | undefined) {
    if (!child || child.exitCode !== null) return;
    const exited = new Promise<void>(resolve => child.once('exit', () => resolve()));
    child.kill();
    await exited;
  }
  async function startApi() {
    backend = spawn(path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'), ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '18004'], {
      env: { ...process.env, BAKEPLAN_DB: db }, windowsHide: true, stdio: 'ignore',
    });
    await expect.poll(async () => {
      if (backend?.exitCode !== null) throw new Error('Isolated API failed to start');
      try { return (await request.get(`${apiBase}/api/health`)).status(); } catch { return 0; }
    }).toBe(200);
  }
  try {
    for (const url of [apiBase, uiBase]) {
      let inUse = false;
      try { await request.get(url, { timeout: 1000 }); inUse = true; } catch { /* free */ }
      expect(inUse, `Dedicated port must be free: ${url}`).toBe(false);
    }
    await startApi();
    frontend = spawn(process.execPath, [path.resolve('frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '15176', '--strictPort'], {
      cwd: path.resolve('frontend'), env: { ...process.env, BAKEPLAN_API_URL: apiBase }, windowsHide: true, stdio: 'ignore',
    });
    await expect.poll(async () => { try { return (await request.get(uiBase)).status(); } catch { return 0; } }).toBe(200);
    await page.goto(uiBase);
    await importFile(page);
    await navigate(page, 'План');
    await expect(page.getByLabel('Способ расчёта', { exact: true })).toHaveValue('B1');
    await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-13');
    await page.getByLabel('Данные доступны до', { exact: true }).fill('2026-10-11');
    await navigate(page, 'Модель');
    await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
    await expect(page.getByTestId('model-summary')).toContainText('Модель готова', { timeout: 65000 });
    const evaluation = page.getByTestId('model-evaluation');
    for (const value of ['0,416667', '1,058562', '60/60', 'ML хуже B1']) await expect(evaluation).toContainText(value);
    await page.getByRole('button', { name: 'Получить прогноз' }).click();
    await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('27.6');
    await navigate(page, 'План');
    await page.getByLabel('Способ расчёта', { exact: true }).selectOption('ML');
    const created = page.waitForResponse(r => r.url().endsWith('/api/plans') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Рассчитать план' }).click();
    const plan = await (await created).json();
    expect(plan.items[0]).toMatchObject({ forecast_mean: 27.644832659891915, recommended_qty: 24, confirmed_qty: null });
    const row = page.getByTestId('plan-table').locator('tbody tr').first();
    await row.getByRole('spinbutton').fill('84');
    await expect(row).toContainText('Ещё не сохранено');
    await page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true }).click();
    await expect(row).toContainText('Сохранено: 84 шт.');
    const saved = await (await request.get(`${apiBase}/api/plans/${plan.id}`)).json();
    expect(saved).toMatchObject({ status: 'confirmed', revision: 1 });
    await row.getByRole('spinbutton').fill('96');
    const dialog = page.waitForEvent('dialog');
    const reloading = page.reload();
    const warning = await dialog;
    expect(warning.type()).toBe('beforeunload');
    await warning.accept();
    await reloading;
    await expect(row.getByRole('spinbutton')).toHaveValue('84');
    await navigate(page, 'История');
    const card = page.locator(`.history-card[data-plan-id="${plan.id}"]`);
    await expect(card).toContainText('Подтверждён');
    const downloaded = page.waitForEvent('download');
    await card.getByRole('link', { name: 'Экспорт CSV' }).click();
    const download = await downloaded;
    const bytes = await fs.readFile((await download.path())!);
    expect(bytes.toString('utf8')).toContain(',27.644832659891915,24,84,');
    expect(bytes.toString('utf8')).toBe(await (await request.get(`${apiBase}/api/plans/${plan.id}/export`)).text());
    await stop(backend);
    await startApi();
    await page.reload();
    await expect(row.getByRole('spinbutton')).toHaveValue('84');
    await expect(page.getByTestId('workspace-source')).toContainText('17.08.2026 — 11.10.2026');
    await navigate(page, 'Модель');
    await expect(page.getByTestId('model-evaluation')).toContainText('1,058562');
    expect(await (await request.get(`${apiBase}/api/plans/${plan.id}`)).json()).toEqual(saved);
    expect(await (await request.get(`${apiBase}/api/plans/${plan.id}/export`)).text()).toBe(bytes.toString('utf8'));
  } finally {
    await stop(frontend);
    await stop(backend);
    if (!path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep)) throw new Error('Unexpected temporary path');
    await fs.rm(directory, { recursive: true, force: true });
  }
});
