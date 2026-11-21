import { expect, type Locator, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export function readOrderCsv(file: string, python = process.env.BAKEPLAN_TEST_PYTHON ||
  path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')): Record<string, string>[] {
  const parse = [
    'import csv, json, sys',
    'with open(sys.argv[1], encoding="utf-8-sig", newline="") as stream:',
    '    print(json.dumps(list(csv.DictReader(stream))))',
  ].join('\n');
  return JSON.parse(execFileSync(python, ['-c', parse, file], { encoding: 'utf8' }));
}

export type Section = 'План' | 'Данные' | 'Модель' | 'История';

export async function calculateAutomatically(page: Page, path: '/plans' | '/forecasts', button: Locator) {
  const [response] = await Promise.all([
    page.waitForResponse(r => new URL(r.url()).pathname === '/api' + path && r.request().method() === 'POST', { timeout: 90_000 }),
    button.click(),
  ]);
  expect(response.request().postDataJSON().method).toBe('AUTO');
  const result = await response.json();
  expect(response.ok(), JSON.stringify(result.error)).toBeTruthy();
  return result;
}

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
