import { expect, type Locator, type Page } from '@playwright/test';

export type Section = 'План' | 'Данные' | 'Модель' | 'История';

export async function acceptPlanReplacement(page: Page, action: Locator) {
  const [prompt] = await Promise.all([
    page.waitForEvent('dialog').then(async dialog => {
      const observed = { type: dialog.type(), message: dialog.message() };
      await dialog.accept();
      return observed;
    }),
    action.click(),
  ]);
  expect(prompt.type).toBe('confirm');
  expect(prompt.message).toContain('Есть несохранённые количества');
}

export async function navigate(page: Page, section: Section) {
  if (section === 'Модель') {
    await openDetails(page, 'Дополнительно');
    await page.getByRole('button', { name: 'Качество прогноза', exact: true }).click();
    return;
  }
  await page.getByRole('navigation', { name: 'Разделы BakePlan', exact: true })
    .getByRole('button', { name: section, exact: true }).click();
}

export async function openDetails(page: Page, summary: string) {
  const details = page.getByText(summary, { exact: true }).locator('..');
  if (!(await details.evaluate(element => element.hasAttribute('open')))) {
    await details.locator('summary').click();
  }
}

export async function prepareImport(page: Page, metadata = false) {
  await navigate(page, 'Данные');
  await openDetails(page, 'Вставить или проверить CSV');
  if (metadata) await openDetails(page, 'Параметры набора');
}

export async function openHistoryPlan(page: Page, date: string, planId?: string) {
  await navigate(page, 'История');
  const card = planId
    ? page.locator(`.history-card[data-plan-id="${planId}"]`)
    : page.locator(`.history-card[data-target-date="${date}"]`).first();
  await card.getByRole('button', { name: 'Открыть план', exact: true }).click();
  await expect(page.getByTestId('plan-table')).toBeVisible();
}
