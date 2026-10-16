import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import { navigate, openHistoryPlan, prepareImport } from './helpers';

test('synthetic CSV → B1 → valid manual confirmation → reload → history → export', async ({ page, request }) => {
  await page.goto('/');
  await prepareImport(page);
  await page.getByLabel('Содержимое CSV').fill(await fs.readFile('fixtures/synthetic_demo.csv', 'utf8'));
  await page.getByRole('button', { name: 'Проверить и импортировать' }).click();
  await navigate(page, 'План');
  await page.getByLabel('Дата плана').fill('2026-10-12');
  await page.getByLabel('Данные доступны до').fill('2026-10-11');
  await page.getByRole('button', { name: 'Рассчитать план' }).click();
  const first = page.getByTestId('plan-table').locator('tbody tr').first();
  await expect(first).toContainText('65.0');
  const quantity = first.getByRole('spinbutton');
  await quantity.fill('90');
  await expect(page.getByRole('button', { name: 'Подтвердить и сохранить' })).toBeDisabled();
  await quantity.fill('84');
  await page.getByRole('button', { name: 'Подтвердить и сохранить' }).click();
  await expect(page.getByRole('status')).toContainText('сохран');
  const plans = await (await request.get('http://127.0.0.1:18000/api/plans')).json();
  const plan = await (await request.get(`http://127.0.0.1:18000/api/plans/${plans[0].id}`)).json();
  expect(plan.items[0].confirmed_qty).toBe(84);
  expect(plan.items[0].forecast_mean).toBe(65);
  await page.reload();
  await navigate(page, 'История');
  await page.getByRole('button', { name: 'Обновить историю' }).click();
  await openHistoryPlan(page, '2026-10-12', plan.id);
  await expect(page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton')).toHaveValue('84');
  const csv = await (await request.get(`http://127.0.0.1:18000/api/plans/${plan.id}/export`)).text();
  expect(csv).toContain('synthetic');
  expect(csv).toContain(',65.0,60,84,');
});

test('quantity edits are locked while confirmation response is delayed', async ({ page }) => {
  await page.goto('/');
  await prepareImport(page);
  await page.getByLabel('Содержимое CSV').fill(await fs.readFile('fixtures/synthetic_demo.csv', 'utf8'));
  await page.getByRole('button', { name: 'Проверить и импортировать' }).click();
  await navigate(page, 'План');
  await page.getByLabel('Дата плана').fill('2026-10-12');
  await page.getByLabel('Данные доступны до').fill('2026-10-11');
  await page.getByRole('button', { name: 'Рассчитать план' }).click();
  const quantity = page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton');
  await quantity.fill('84');
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/plans/*/confirm', async route => {
    const response = await route.fetch();
    await pending;
    await route.fulfill({ response });
  });
  await page.getByRole('button', { name: 'Подтвердить и сохранить' }).click();
  await expect(quantity).toBeDisabled();
  await expect(quantity).toHaveValue('84');
  release();
  await expect(page.getByRole('status')).toContainText('сохран');
  await expect(quantity).toBeEnabled();
  await expect(quantity).toHaveValue('84');
});

test('invalid balance is rejected visibly and creates no plan', async ({ page, request }) => {
  const before = await (await request.get('http://127.0.0.1:18000/api/plans')).json();
  await page.goto('/');
  await prepareImport(page);
  await page.getByLabel('Содержимое CSV').fill(await fs.readFile('fixtures/invalid_balance.csv', 'utf8'));
  await page.getByRole('button', { name: 'Проверить и импортировать' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await navigate(page, 'План');
  await expect(page.getByRole('button', { name: 'Рассчитать план' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Перейти к данным', exact: true })).toBeEnabled();
  await expect(page.getByTestId('plan-table')).toHaveCount(0);
  expect(await (await request.get('http://127.0.0.1:18000/api/plans')).json()).toEqual(before);
});

test('confirmation retry is idempotent, altered retry and stale revision conflict', async ({ request }) => {
  const dataset = await (await request.post('http://127.0.0.1:18000/api/datasets', { data: { csv_text: await fs.readFile('fixtures/synthetic_demo.csv','utf8'), source:'synthetic' } })).json();
  const plan = await (await request.post('http://127.0.0.1:18000/api/plans', { data: {dataset_id:dataset.id,target_date:'2026-10-12',cutoff:'2026-10-11',method:'B1',mode:'replay'} })).json();
  const data = { revision:0,idempotency_key:'browser-retry',quantities:Object.fromEntries(plan.items.map((i:{product_id:string;recommended_qty:number})=>[i.product_id,i.recommended_qty])) };
  const url=`http://127.0.0.1:18000/api/plans/${plan.id}/confirm`;
  const first=await request.post(url,{data}); expect(first.status()).toBe(200);
  expect(await (await request.post(url,{data})).json()).toEqual(await first.json());
  expect((await request.post(url,{data:{...data,quantities:{...data.quantities,p1:84}}})).status()).toBe(409);
  expect((await request.post(url,{data:{...data,idempotency_key:'stale-key'}})).status()).toBe(409);
});
