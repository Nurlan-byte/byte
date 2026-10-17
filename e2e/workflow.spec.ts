import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs/promises';
import { navigate, openDetails, openHistoryPlan, prepareImport } from './helpers';

const base = 'http://127.0.0.1:18000/api';

async function prepare(page: Page) {
  await page.goto('/');
  await prepareImport(page);
  await page.getByLabel('Содержимое CSV').fill(await fs.readFile('fixtures/synthetic_demo.csv', 'utf8'));
  await page.getByRole('button', { name: 'Проверить и импортировать' }).click();
  await expect(page.getByTestId('dataset-summary')).toBeVisible();
  await navigate(page, 'План');
  await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-12');
  await page.getByLabel('Данные доступны до', { exact: true }).fill('2026-10-11');
}

test('diagnostic result retains original parameters and marks each changed input as stale', async ({ page }) => {
  await prepare(page);
  await navigate(page, 'Модель');
  await page.getByRole('button', { name: 'Проверить прогноз', exact: true }).click();
  await expect(page.getByTestId('forecast-result')).toBeVisible();
  await expect(page.getByTestId('stale-forecast')).toHaveCount(0);
  await page.getByLabel('Дата прогноза', { exact: true }).fill('2026-10-13');
  await expect(page.getByTestId('stale-forecast')).toContainText('Параметры изменились');
  await expect(page.getByTestId('forecast-result')).toContainText('Прогноз по истории продаж на 2026-10-12');
  await page.getByLabel('Дата прогноза', { exact: true }).fill('2026-10-12');
  await expect(page.getByTestId('stale-forecast')).toHaveCount(0);
  await page.getByLabel('Срез обучения и прогноза', { exact: true }).fill('2026-10-10');
  await expect(page.getByTestId('stale-forecast')).toContainText('Параметры изменились');
  await expect(page.getByTestId('forecast-result')).toContainText('Продажи учтены по 2026-10-11');
  await page.getByLabel('Срез обучения и прогноза', { exact: true }).fill('2026-10-11');
  await page.getByLabel('Способ прогноза', { exact: true }).selectOption('B1');
  await expect(page.getByTestId('stale-forecast')).toContainText('Параметры изменились');
  await page.getByRole('button', { name: 'Проверить прогноз', exact: true }).click();
  await expect(page.getByTestId('stale-forecast')).toHaveCount(0);
  await expect(page.getByTestId('forecast-table')).toContainText('По похожим дням недели');
});

test('old plan cannot be confirmed under changed date, cutoff, method or calendar', async ({ page }) => {
  await prepare(page);
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  const confirm = page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true });
  const quantity = page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton');
  await quantity.fill('84');
  for (const [label, changed, original] of [
    ['Дата плана', '2026-10-13', '2026-10-12'],
    ['Данные доступны до', '2026-10-10', '2026-10-11'],
  ]) {
    await page.getByLabel(label, { exact: true }).fill(changed);
    await expect(page.getByTestId('stale-plan')).toBeVisible();
    await expect(confirm).toBeDisabled();
    await expect(quantity).toBeDisabled();
    await expect(quantity).toHaveValue('84');
    await page.getByLabel(label, { exact: true }).fill(original);
    await expect(confirm).toBeEnabled();
  }
  await page.getByText('Настройки прогноза', { exact: true }).click();
  await page.getByLabel('Способ расчёта', { exact: true }).selectOption('B0');
  await expect(confirm).toBeDisabled();
  await page.getByLabel('Способ расчёта', { exact: true }).selectOption('B1');
  await expect(confirm).toBeEnabled();
  await page.getByLabel('Целевой день открыт', { exact: true }).uncheck();
  await expect(confirm).toBeDisabled();
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  await expect(page.getByTestId('stale-plan')).toHaveCount(0);
  await expect(quantity).toHaveValue('0');
  await expect(quantity).toBeDisabled();
  await expect(confirm).toBeEnabled();
});

test('successful confirmation remains successful when history refresh fails', async ({ page, request }) => {
  await prepare(page);
  const created = page.waitForResponse(response => response.url().endsWith('/api/plans') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  const plan = await (await created).json();
  const row = page.getByTestId('plan-table').locator('tbody tr').first();
  await row.getByRole('spinbutton').fill('84');
  await page.route('**/api/plans?*', route => route.fulfill({ status: 503, json: { error: { message: 'История временно недоступна' } } }));
  await page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Подтверждённый план сохранён');
  await expect(page.getByRole('status')).toContainText('Не удалось обновить список истории');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(row).toContainText('Сохранено: 84 шт.');
  const stored = await (await request.get(`${base}/plans/${plan.id}`)).json();
  expect(stored.revision).toBe(1);
  expect(stored.items[0].confirmed_qty).toBe(84);
  await row.getByRole('spinbutton').fill('96');
  await expect(row).toContainText('Есть несохранённое изменение');
  await expect(row).toContainText('Сохранено: 84 шт.');
  const exported = await (await request.get(`${base}/plans/${plan.id}/export`)).text();
  expect(exported).toContain(',65.0,60,84,');
});

test('uncertain confirmation retries with the same snapshot and key', async ({ page }) => {
  await prepare(page);
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  const quantity = page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton');
  await quantity.fill('84');
  const bodies: unknown[] = [];
  await page.route('**/api/plans/*/confirm', async route => {
    bodies.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (bodies.length === 1) {
      // The server has committed but the UI did not receive its response.
      await route.fulfill({ status: 503, json: { error: { message: 'Ответ подтверждения не получен' } } });
    } else {
      await route.fulfill({ response });
    }
  });
  await page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Ответ подтверждения не получен');
  await expect(quantity).toHaveValue('84');
  await page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('сохранён');
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
  await expect(page.getByTestId('plan-table').locator('tbody tr').first()).toContainText('Сохранено: 84 шт.');
});

test('mobile page keeps all quantity fields visible in cards without horizontal scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await prepare(page);
  await navigate(page, 'Данные');
  await expect(page.getByTestId('dataset-summary')).not.toContainText('Технические данные набора');
  await navigate(page, 'План');
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  await expect(page.getByTestId('plan-table')).toBeVisible();
  await expect(page.getByTestId('plan-table')).not.toContainText('Технические данные плана');
  const widths = await page.evaluate(() => ({ body: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(widths.body).toBeLessThanOrEqual(widths.viewport);
  const table = page.getByTestId('plan-table').locator('..');
  expect(await table.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  for (const input of await page.getByTestId('plan-table').getByRole('spinbutton').all()) {
    await input.scrollIntoViewIfNeeded();
    await expect(input).toBeInViewport({ ratio: 1 });
  }
  await page.getByLabel('Дата плана', { exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByLabel('Дата плана', { exact: true })).toBeInViewport();
});

test('history restores its own planning parameters while the current imported dataset stays separate', async ({ page, request }) => {
  await prepare(page);
  const created = page.waitForResponse(response => response.url().endsWith('/api/plans') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  const original = await (await created).json();
  await prepareImport(page, true);
  await page.getByLabel('Код точки', { exact: true }).fill('e2e-history-current');
  const imported = page.waitForResponse(response => response.url().endsWith('/api/datasets') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Проверить и импортировать', exact: true }).click();
  const current = await (await imported).json();
  expect(current.id).not.toBe(original.dataset_id);
  await expect(page.getByTestId('plan-table')).toHaveCount(0);
  await navigate(page, 'План');
  await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-13');
  await page.getByLabel('Данные доступны до', { exact: true }).fill('2026-10-10');
  await page.getByText('Настройки прогноза', { exact: true }).click();
  await page.getByLabel('Способ расчёта', { exact: true }).selectOption('B0');
  await page.getByLabel('Целевой день открыт', { exact: true }).uncheck();
  await page.route('**/api/plans?*', route => route.fulfill({ json: [{
    id: original.id, dataset_id: original.dataset_id, target_date: original.target_date,
    source: original.source, status: original.status, revision: original.revision,
  }] }), { times: 1 });
  await navigate(page, 'История');
  await page.getByRole('button', { name: 'Обновить историю', exact: true }).click();
  await openHistoryPlan(page, '2026-10-12', original.id);
  await expect(page.getByLabel('Дата плана', { exact: true })).toHaveValue('2026-10-12');
  await expect(page.getByLabel('Данные доступны до', { exact: true })).toHaveValue('2026-10-11');
  await expect(page.getByLabel('Способ расчёта', { exact: true })).toHaveValue('B1');
  await expect(page.getByLabel('Целевой день открыт', { exact: true })).toBeChecked();
  await expect(page.getByTestId('stale-plan')).toHaveCount(0);
  await expect(page.getByText('Открыт план другого сохранённого набора.', { exact: false })).toBeVisible();
  await navigate(page, 'Данные');
  await openDetails(page, 'Продолжить работу с сохранённым набором');
  await expect(page.getByLabel('Сохранённый набор', { exact: true })).toHaveValue(current.id);
  await navigate(page, 'План');
  const quantity = page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton');
  await quantity.fill('84');
  await page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('сохранён');
  const stored = await (await request.get(`${base}/plans/${original.id}`)).json();
  expect(stored.dataset_id).toBe(original.dataset_id);
  expect(stored.items[0].confirmed_qty).toBe(84);
  await navigate(page, 'Данные');
  await expect(page.getByLabel('Сохранённый набор', { exact: true })).toHaveValue(current.id);
  await navigate(page, 'План');
});
