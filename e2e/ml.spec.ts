import { test, expect, type Page } from '@playwright/test';
import { navigate, openHistoryPlan, prepareImport } from './helpers';

const cutoff = '2026-10-11';
const target = '2026-10-12';
const base = 'http://127.0.0.1:18000/api';

function historyCsv(salesOnly = false, days = 84) {
  const header = salesOnly
    ? 'date,product_id,item_line_count'
    : 'date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status';
  const lines = [header];
  const end = Date.parse(`${cutoff}T00:00:00Z`);
  for (let index = days - 1; index >= 0; index--) {
    const day = new Date(end - index * 86400000);
    const date = day.toISOString().slice(0, 10);
    for (let product = 1; product <= 5; product++) {
      const value = 25 + day.getUTCDay() * 3 + product * 4;
      lines.push(salesOnly
        ? `${date},p${product},${value}`
        : `${date},p${product},true,0,${value + 5},${value},5,0,0,none`);
    }
  }
  return lines.join('\n');
}

async function importHistory(page: Page, options: { salesOnly?: boolean; days?: number; bakery?: string } = {}) {
  await prepareImport(page, true);
  await page.getByLabel('ID пекарни').fill(options.bakery ?? 'e2e-ml');
  await page.getByLabel('Измеряемая величина').selectOption(options.salesOnly ? 'item_line_count' : 'sold_units');
  await page.getByLabel('Происхождение данных').fill('Synthetic automated integration test, not real bakery data');
  await page.getByLabel('Содержимое CSV').fill(historyCsv(options.salesOnly, options.days));
  const saved = page.waitForResponse(response => response.url().endsWith('/api/datasets') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Проверить и импортировать' }).click();
  const response = await saved;
  expect(response.ok()).toBeTruthy();
  const dataset = await response.json();
  const datasetDetails = page.getByTestId('dataset-summary').locator('details');
  if (!(await datasetDetails.evaluate(element => element.hasAttribute('open')))) {
    await datasetDetails.getByText('Технические данные набора', { exact: true }).click();
  }
  await expect(page.getByTestId('dataset-summary').getByText(`Набор: ${dataset.id}`, { exact: true })).toBeVisible();
  await navigate(page, 'Модель');
  await page.getByLabel('Срез обучения и прогноза').fill(cutoff);
  await page.getByLabel('Дата прогноза', { exact: true }).fill(target);
  return dataset;
}

test('explicit units → train/status → ML forecast → safeguarded plan → confirmation → persisted reload', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.goto('/');
  const dataset = await importHistory(page);
  expect(dataset).toMatchObject({ bakery_id: 'e2e-ml', target: 'sold_units', unit: 'physical_unit', schema_version: 'history-1' });
  await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
  await expect(page.getByTestId('model-summary')).toContainText('Модель готова', { timeout: 65_000 });
  await page.getByRole('button', { name: 'Обновить модели' }).click();
  await expect(page.getByTestId('model-summary')).toContainText(cutoff);
  const models = await (await request.get(`${base}/datasets/${dataset.id}/models`)).json();
  expect(models[0].status).toBe('ready');
  expect(JSON.stringify(models)).not.toContain('coefficients');
  await page.getByTestId('model-summary').getByText('Диагностика оценки модели', { exact: true }).click();
  await expect(page.getByTestId('model-summary').getByText(/Горизонт оценки: 2 дн./)).toBeVisible();
  await expect(page.getByTestId('model-summary')).toContainText('пригодных наблюдаемых меток');

  await page.getByRole('button', { name: 'Получить прогноз' }).click();
  await page.getByTestId('forecast-result').getByText('Технические данные прогноза', { exact: true }).click();
  await expect(page.getByTestId('forecast-result').getByText(new RegExp(models[0].id))).toBeVisible();
  await expect(page.getByTestId('forecast-result').getByTestId('evaluation-applicability').first()).toContainText('оценка 2 дн., прогноз 1 дн.');
  await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('ML');
  await expect(page.getByTestId('forecast-result').getByRole('spinbutton')).toHaveCount(0);
  await page.getByLabel('Дата прогноза', { exact: true }).fill('2026-10-13');
  await page.getByRole('button', { name: 'Получить прогноз', exact: true }).click();
  await expect(page.getByTestId('forecast-result').getByTestId('evaluation-applicability').first()).toContainText('Горизонт совпадает с оценкой: 2 дн.');
  await page.getByLabel('Дата прогноза', { exact: true }).fill(target);
  await navigate(page, 'План');
  await page.getByLabel('Базовый метод').selectOption('ML');
  const planned = page.waitForResponse(response => response.url().endsWith('/api/plans') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  const plan = await (await planned).json();
  expect(plan.model_id).toBe(models[0].id);
  expect(plan.items[0].method).toBe('ML');
  const quantity = page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton');
  await quantity.fill('90');
  await expect(page.getByRole('button', { name: 'Подтвердить и сохранить' })).toBeDisabled();
  await quantity.fill('84');
  await page.getByRole('button', { name: 'Подтвердить и сохранить' }).click();
  await expect(page.getByRole('status')).toContainText('сохран');
  const confirmed = await (await request.get(`${base}/plans/${plan.id}`)).json();
  expect(confirmed.items[0].confirmed_qty).toBe(84);
  expect(confirmed.model_id).toBe(models[0].id);
  expect(confirmed.items[0].method).toBe('ML');

  await page.reload();
  await navigate(page, 'Данные');
  await page.getByText('Продолжить работу с сохранённым набором', { exact: true }).click();
  const datasetsLoaded = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/datasets' && response.request().method() === 'GET');
  await page.getByRole('button', { name: 'Обновить наборы' }).click();
  const datasetsResponse = await datasetsLoaded;
  expect(datasetsResponse.ok()).toBeTruthy();
  expect((await datasetsResponse.json()).map((entry: { id: string }) => entry.id)).toContain(dataset.id);
  const savedDataset = page.getByLabel('Сохранённый набор', { exact: true });
  await expect(savedDataset).toBeVisible();
  await expect(savedDataset).toBeEnabled();
  await expect(savedDataset.locator(`option[value="${dataset.id}"]`)).toHaveCount(1);
  await savedDataset.selectOption(dataset.id);
  await expect(savedDataset).toHaveValue(dataset.id);
  await page.getByRole('button', { name: 'Открыть набор', exact: true }).click();
  await navigate(page, 'Модель');
  await page.getByRole('button', { name: 'Обновить модели' }).click();
  await page.getByTestId('model-summary').getByText('Технические данные модели', { exact: true }).click();
  await expect(page.getByTestId('model-summary').getByText(new RegExp(models[0].id))).toBeVisible();
  await navigate(page, 'История');
  await page.getByRole('button', { name: 'Обновить историю' }).click();
  await openHistoryPlan(page, '2026-10-12', plan.id);
  await expect(page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton')).toHaveValue('84');
  const savedRow = page.getByTestId('plan-table').locator('tbody tr').first();
  await savedRow.getByText('Источники и модель', { exact: true }).click();
  await expect(savedRow.getByText(`Снимок модели: ${models[0].id}`, { exact: true })).toBeVisible();
});

test('sales-only forecast cannot expose production controls or inherit a units model', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await importHistory(page, { bakery: 'e2e-switch-units' });
  await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
  await expect(page.getByTestId('model-summary')).toContainText('Модель готова', { timeout: 65_000 });
  await page.getByRole('button', { name: 'Получить прогноз' }).click();
  await expect(page.getByTestId('forecast-result')).toBeVisible();
  const sales = await importHistory(page, { salesOnly: true, bakery: 'e2e-switch-sales' });
  expect(sales).toMatchObject({ target: 'item_line_count', unit: 'item_line', schema_version: 'sales-1' });
  await expect(page.getByTestId('model-summary')).toHaveCount(0);
  await expect(page.getByTestId('forecast-result')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Рассчитать план', exact: true, includeHidden: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Подтвердить и сохранить', includeHidden: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Обновить историю', includeHidden: true })).toHaveCount(0);
  await navigate(page, 'План');
  await expect(page.getByTestId('sales-only-notice')).toContainText('не преобразуются');
  await navigate(page, 'Модель');
  await page.getByRole('button', { name: 'Получить прогноз' }).click();
  await page.getByTestId('forecast-result').getByText('Технические данные прогноза', { exact: true }).click();
  await expect(page.getByTestId('forecast-result').getByText(/Цель: item_line_count · единица: item_line/)).toBeVisible();
  await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('B1');
  await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('Причина перехода');
  await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
  await expect(page.getByTestId('model-summary')).toContainText('Модель готова', { timeout: 65_000 });
  await page.getByRole('button', { name: 'Получить прогноз' }).click();
  await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('ML');
  await expect(page.getByRole('spinbutton')).toHaveCount(0);
  const blocked = await request.post(`${base}/plans`, { data: { dataset_id: sales.id, target_date: target, cutoff, method: 'ML', mode: 'replay' } });
  expect(blocked.status()).toBe(422);
});

test('short histories report not-ready and explicit B1 fallback', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await importHistory(page, { days: 14, bakery: 'e2e-short' });
  await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
  await expect(page.getByTestId('model-summary')).toContainText('Модель не готова', { timeout: 65_000 });
  await page.getByTestId('model-summary').getByText('Технические данные модели', { exact: true }).click();
  await expect(page.getByTestId('model-summary').getByText('Код причины: insufficient_history_dates', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Получить прогноз' }).click();
  const row = page.getByTestId('forecast-table').locator('tbody tr').first();
  await expect(row).toContainText('B1');
  await expect(row).toContainText('Причина перехода');
  await navigate(page, 'План');
  await page.getByLabel('Базовый метод').selectOption('ML');
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  await expect(page.getByTestId('plan-table').locator('tbody tr').first()).toContainText('B1');
  await expect(page.getByTestId('plan-table').locator('tbody tr').first()).toContainText('Причина перехода');
});

test('training locks edits, retries the same key, and changes key with the cutoff', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/');
  const dataset = await importHistory(page, { bakery: 'e2e-retry' });
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const bodies: { cutoff: string; idempotency_key: string }[] = [];
  await page.route(`**/api/datasets/${dataset.id}/train`, async route => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) {
      await pending;
      await route.fulfill({ status: 503, json: { error: { message: 'Обучение временно недоступно' } } });
    } else {
      await route.continue();
    }
  });
  await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Обучить модель', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Измеряемая величина')).toBeDisabled();
  await expect(page.getByLabel('Содержимое CSV')).toBeDisabled();
  await expect(page.getByLabel('Срез обучения и прогноза')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Демо строк продаж', includeHidden: true })).toBeDisabled();
  release();
  await expect(page.getByRole('alert')).toContainText('временно недоступно');
  await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
  await expect(page.getByTestId('model-summary')).toContainText('Модель готова', { timeout: 65_000 });
  expect(bodies[1].idempotency_key).toBe(bodies[0].idempotency_key);
  await page.getByLabel('Срез обучения и прогноза').fill('2026-10-10');
  await page.getByRole('button', { name: 'Обучить модель', exact: true }).click();
  await expect(page.getByTestId('model-summary')).toHaveCount(2, { timeout: 65_000 });
  expect(bodies[2].idempotency_key).not.toBe(bodies[1].idempotency_key);
});
