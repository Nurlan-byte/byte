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
  await expect(page.getByRole('button',{name:'Рассчитать план'})).toBeDisabled();
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
  expect(offsets).toEqual([0,20,0]);
});
