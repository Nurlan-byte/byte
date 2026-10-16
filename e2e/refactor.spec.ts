import { test, expect } from '@playwright/test';
import { navigate, openHistoryPlan, prepareImport } from './helpers';

test('structured 413 import error displays server message', async ({ page }) => {
  await page.route('**/api/datasets', route => route.fulfill({status:413,contentType:'application/json',body:JSON.stringify({error:{code:'upload_too_large',message:'CSV превышает допустимый размер',details:{limit_bytes:1000000}}})}));
  await page.goto('/');
  await prepareImport(page);
  await page.getByLabel('Содержимое CSV').fill('oversized fixture');
  await page.getByRole('button',{name:'Проверить и импортировать'}).click();
  await expect(page.getByRole('alert')).toContainText('CSV превышает допустимый размер');
  await navigate(page, 'План');
  await expect(page.getByRole('button',{name:'Рассчитать план'})).toHaveCount(0);
  await expect(page.getByTestId('plan-table')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Перейти к данным', exact: true })).toBeEnabled();
});

test('non-JSON upstream error displays HTTP fallback', async ({ page }) => {
  await page.route('**/api/datasets', route => route.fulfill({status:502,contentType:'text/html',body:'<html>upstream failed</html>'}));
  await page.goto('/');
  await prepareImport(page);
  await page.getByLabel('Содержимое CSV').fill('fixture');
  await page.getByRole('button',{name:'Проверить и импортировать'}).click();
  await expect(page.getByRole('alert')).toContainText('HTTP 502');
  await expect(page.getByRole('alert')).not.toContainText('SyntaxError');
});

test('history pages request summaries and load detail separately', async ({ page }) => {
  const summaries=Array.from({length:21},(_,i)=>({id:`summary-${i}`,target_date:`2026-10-${String(i+1).padStart(2,'0')}`,source:'synthetic',status:'draft',revision:0,dataset_id:'dataset',created_at:'2026-10-13'}));
  const offsets:number[]=[];
  await page.route('**/api/plans?*', route => {
    const url=new URL(route.request().url());
    expect(url.searchParams.get('limit')).toBe('20');
    const offset=Number(url.searchParams.get('offset'));offsets.push(offset);
    return route.fulfill({json:summaries.slice(offset,offset+20)});
  });
  await page.route('**/api/plans/summary-20', route => route.fulfill({json:{...summaries[20],cutoff:'2026-10-20',mode:'replay',items:[{product_id:'p1',name:'Тестовый хлеб',batch_size:12,max_daily_qty:100,forecast_mean:65,recommended_qty:60,confirmed_qty:null,method:'B1',warnings:[],used_dates:['2026-10-14'],n:1}]}}));
  await page.goto('/');
  await navigate(page, 'История');
  await expect(page.locator('.history-card')).toHaveCount(20);
  await expect(page.getByRole('button',{name:'Обновить историю'})).toBeEnabled();
  offsets.length = 0; // Startup may fetch twice under React StrictMode. Pagination below is exact.
  await page.getByRole('button',{name:'Обновить историю'}).click();
  await expect(page.getByRole('navigation',{name:'Страницы истории'})).toContainText('Страница 1');
  await expect(page.getByRole('button',{name:'Предыдущая страница'})).toBeDisabled();
  await page.getByRole('button',{name:'Следующая страница'}).click();
  await expect(page.getByRole('navigation', { name: 'Страницы истории' })).toContainText('Страница 2');
  await expect(page.getByRole('button',{name:'Следующая страница'})).toBeDisabled();
  await openHistoryPlan(page, '2026-10-21', 'summary-20');
  await expect(page.getByLabel('Количество Тестовый хлеб')).toHaveValue('60');
  await navigate(page, 'История');
  await page.getByRole('button',{name:'Предыдущая страница'}).click();
  await expect(page.getByRole('navigation', { name: 'Страницы истории' })).toContainText('Страница 1');
  expect(offsets).toEqual([0,20,20,0]);
});

test('legacy inactive plan can be corrected to zero without unlocking a stale snapshot', async ({ page }) => {
  const legacy = {
    id: 'legacy-inactive', dataset_id: 'dataset', target_date: '2026-10-12',
    cutoff: '2026-10-11', source: 'synthetic', requested_method: 'B1',
    status: 'draft', revision: 0,
    items: [{ product_id: 'p1', name: 'Legacy', batch_size: 12,
      max_daily_qty: 100, forecast_mean: 24, recommended_qty: 24,
      confirmed_qty: null, method: 'B1', warnings: [],
      target_is_open: true, active_to: '2026-10-11' }],
  };
  await page.addInitScript(() => localStorage.setItem('bakeplan.owner.plan', 'legacy-inactive'));
  await page.route('**/api/plans/legacy-inactive', route => route.fulfill({ json: legacy }));
  await page.route('**/api/plans?*', route => route.fulfill({ json: [] }));
  const submitted: unknown[] = [];
  await page.route('**/api/plans/legacy-inactive/confirm', route => {
    submitted.push(route.request().postDataJSON());
    return route.fulfill({ json: { ...legacy, status: 'confirmed', revision: 1,
      items: [{ ...legacy.items[0], confirmed_qty: 0 }] } });
  });
  await page.goto('/');
  const quantity = page.getByRole('spinbutton', { name: 'Количество Legacy' });
  const repair = page.getByRole('button', { name: 'Установить 0 для Legacy' });
  const confirm = page.getByRole('button', { name: 'Подтвердить и сохранить' });
  await expect(quantity).toHaveValue('24');
  await expect(quantity).toBeDisabled();
  await expect(confirm).toBeDisabled();
  await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-13');
  await expect(page.getByTestId('stale-plan')).toBeVisible();
  await expect(repair).toBeDisabled();
  await expect(confirm).toBeDisabled();
  await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-12');
  await repair.click();
  await expect(quantity).toHaveValue('0');
  await expect(confirm).toBeEnabled();
  await confirm.click();
  expect(submitted).toHaveLength(1);
  expect(submitted[0]).toMatchObject({ revision: 0, quantities: { p1: 0 } });
});
