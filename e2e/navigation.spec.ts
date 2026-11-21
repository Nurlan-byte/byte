import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import { navigate, prepareImport } from './helpers';

async function preparePlan(page: import('@playwright/test').Page) {
  await page.goto('/');
  await prepareImport(page);
  await page.getByLabel('Содержимое CSV', { exact: true }).fill(await fs.readFile('fixtures/synthetic_demo.csv', 'utf8'));
  await page.getByRole('button', { name: 'Проверить и импортировать', exact: true }).click();
  await expect(page.getByTestId('dataset-summary')).toBeVisible();
  await navigate(page, 'План');
  await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-12');
  await page.getByLabel('Данные доступны до', { exact: true }).fill('2026-10-11');
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  await expect(page.getByTestId('plan-table')).toBeVisible();
}

test('plan is the default workspace and all sections are keyboard accessible', async ({ page }) => {
  await page.goto('/');
  const navigation = page.getByRole('navigation', { name: 'Разделы BakePlan', exact: true });
  const planButton = navigation.getByRole('button', { name: 'План', exact: true });
  await expect(planButton).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#panel-planning')).toBeVisible();
  await expect(page.locator('#panel-import')).toBeHidden();
  await expect(page.getByLabel('Содержимое CSV', { exact: true })).toBeHidden();
  await planButton.focus();
  for (const [name, panel] of [['Данные', 'import'], ['История', 'history']] as const) {
    await page.keyboard.press('Tab');
    const button = navigation.getByRole('button', { name, exact: true });
    await expect(button).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(button).toHaveAttribute('aria-current', 'page');
    await expect(page.locator(`#panel-${panel}`)).toBeVisible();
    await expect(page.locator('#panel-planning')).toBeHidden();
  }
  const advanced = page.getByText('Дополнительно', { exact: true });
  await advanced.focus();
  await page.keyboard.press('Enter');
  const quality = page.getByRole('button', { name: 'Качество прогноза', exact: true });
  await quality.focus();
  await page.keyboard.press('Enter');
  await expect(quality).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#panel-model')).toBeVisible();
  await planButton.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#panel-planning')).toBeVisible();
});

test('section navigation preserves the dataset, diagnostic forecast and unsaved manual plan', async ({ page }) => {
  await preparePlan(page);
  const quantity = page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton');
  await quantity.fill('84');
  await navigate(page, 'Модель');
  await page.getByLabel('Способ прогноза', { exact: true }).selectOption('B1');
  await page.getByRole('button', { name: 'Проверить прогноз', exact: true }).click();
  await expect(page.getByTestId('forecast-result')).toBeVisible();
  await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('65.0');
  await navigate(page, 'Данные');
  await expect(page.getByTestId('dataset-summary')).toBeVisible();
  await expect(page.getByLabel('Содержимое CSV', { exact: true })).not.toHaveValue('');
  const savedPeriod = await page.getByTestId('dataset-period').innerText();
  const provenance = page.getByTestId('dataset-summary').getByText(/^Происхождение:/);
  const savedProvenance = await provenance.innerText();
  await page.getByLabel('Источник', { exact: true }).selectOption('external');
  await page.getByLabel('Происхождение данных', { exact: true }).fill('Unsaved external draft, not imported');
  await page.getByLabel('Содержимое CSV', { exact: true }).fill('date,product_id,item_line_count\n2020-01-01,p1,1');
  await expect(page.getByTestId('unsaved-import')).toBeVisible();
  await expect(page.getByTestId('dataset-period')).toHaveText(savedPeriod);
  await expect(provenance).toHaveText(savedProvenance);
  await expect(page.getByTestId('workspace-source')).toContainText('Синтетические данные');
  await navigate(page, 'История');
  await expect(page.locator('#history')).toBeVisible();
  await navigate(page, 'Модель');
  await expect(page.getByLabel('Способ прогноза', { exact: true })).toHaveValue('B1');
  await expect(page.getByTestId('forecast-result')).toBeVisible();
  await expect(page.getByTestId('stale-forecast')).toHaveCount(0);
  await navigate(page, 'План');
  await expect(quantity).toHaveValue('84');
  await expect(page.getByTestId('plan-table').locator('tbody tr').first()).toContainText('Ещё не сохранено');
  await expect(page.getByLabel('Дата плана', { exact: true })).toHaveValue('2026-10-12');
  await expect(page.getByLabel('Данные доступны до', { exact: true })).toHaveValue('2026-10-11');
  await expect(page.getByTestId('stale-plan')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Подтвердить и сохранить', exact: true })).toBeEnabled();
  await expect(page.locator('.workspace-header time')).toHaveAttribute('datetime', '2026-10-12');
  await expect(page.locator('.workspace-header time')).toContainText('12 октября 2026');
  await expect(page.locator('.workspace-header')).toContainText('Историческое воспроизведение');
  await expect(page.locator('.workspace-header')).not.toContainText('на завтра');
});

for (const width of [360, 768, 1440]) {
  test(`all sections contain their page width at ${width}px`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 1000 });
    await preparePlan(page);
    await navigate(page, 'Модель');
    await page.getByRole('button', { name: 'Подготовить экспериментальный расчёт', exact: true }).click();
    await expect(page.getByTestId('model-summary')).toContainText('Расчёт готов', { timeout: 65_000 });
    await expect(page.getByTestId('model-summary')).not.toContainText('MAE');
    await page.getByRole('button', { name: 'Проверить прогноз', exact: true }).click();
    await expect(page.getByTestId('forecast-table').locator('tbody tr').first()).toContainText('Экспериментальный расчёт');
    if (width === 360) {
      const forecast = page.getByRole('region', { name: 'Прогноз по товарам', exact: true });
      await forecast.focus();
      await page.keyboard.press('Shift+Tab');
      await page.keyboard.press('Tab');
      await expect(forecast).toBeFocused();
      if (await forecast.evaluate(el => el.scrollWidth > el.clientWidth)) {
        const start = await forecast.evaluate(el => el.scrollLeft);
        await page.keyboard.press('ArrowRight');
        await expect.poll(() => forecast.evaluate(el => el.scrollLeft)).toBeGreaterThan(start);
      }
    }
    await navigate(page, 'История');
    await page.getByRole('button', { name: 'Обновить историю', exact: true }).click();
    await expect(page.locator('.history-card').first()).toBeVisible();
    await expect(page.locator('.history-card').first()).toContainText('Синтетические данные');
    for (const section of ['План', 'Данные', 'Модель', 'История'] as const) {
      await navigate(page, section);
      const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
      expect(dimensions.width, `${section} must not overflow the ${width}px page`).toBeLessThanOrEqual(dimensions.viewport);
    }
    await navigate(page, 'План');
    await page.getByLabel('Дата плана', { exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByLabel('Дата плана', { exact: true })).toBeInViewport({ ratio: 1 });
    const quantity = page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton');
    await quantity.scrollIntoViewIfNeeded();
    await expect(quantity).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test('mobile navigation fits at 320px and import actions are on the first screen at 360px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('/');
  const navigation = page.getByRole('navigation', { name: 'Разделы BakePlan', exact: true });
  await expect(navigation.getByRole('button', { name: 'План', exact: true })).toBeEnabled();
  await page.evaluate(() => document.fonts.ready.then(() => true));
  for (const name of ['План', 'Данные', 'История']) {
    await expect(navigation.getByRole('button', { name, exact: true })).toBeInViewport({ ratio: 1 });
  }
  await expect(navigation.getByText('Дополнительно', { exact: true })).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);

  await page.setViewportSize({ width: 360, height: 800 });
  await navigate(page, 'Данные');
  const file = page.getByLabel('CSV-файл', { exact: true });
  const demo = page.getByRole('button', { name: 'Загрузить демо CSV', exact: true });
  await expect(file).toBeEnabled();
  await expect(demo).toBeEnabled();
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await expect(file).toBeInViewport({ ratio: 1 });
  await expect(demo).toBeInViewport({ ratio: 1 });
});
