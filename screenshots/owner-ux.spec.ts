import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { navigate, openDetails } from '../e2e/helpers';

test('owner journey real browser evidence', async ({ page, browser }, testInfo) => {
  const variant = process.env.BAKEPLAN_CAPTURE_BASELINE === '1' ? 'before' : 'after';
  const sourceRoot = path.resolve(process.env.BAKEPLAN_CAPTURE_SOURCE_DIR ?? '.');
  const directory = testInfo.outputPath('evidence');
  await fs.mkdir(directory, { recursive: true });
  const files: string[] = [];
  async function capture(stage: string) {
    for (const width of [360, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(() => window.scrollTo(0, 0));
      const name = `${variant}-${stage}-${width}.png`;
      await page.screenshot({ path: path.join(directory, name), fullPage: true });
      files.push(name);
    }
  }
  await page.goto('/');
  await capture('start');
  await navigate(page, 'Данные');
  await page.getByLabel('CSV-файл', { exact: true }).setInputFiles('fixtures/synthetic_demo.csv');
  await expect(page.getByTestId('import-stage')).toContainText('Файл подготовлен');
  await page.getByLabel('Источник', { exact: true }).selectOption('synthetic');
  await openDetails(page, 'Параметры набора');
  await page.getByLabel('Код точки', { exact: true }).fill('synthetic-demo');
  await expect(page.getByRole('button', { name: 'Проверить и импортировать', exact: true })).toBeEnabled();
  await capture('file-selected');
  await page.getByRole('button', { name: 'Проверить и импортировать', exact: true }).click();
  await expect(page.getByTestId('dataset-summary')).toBeVisible();
  await navigate(page, 'План');
  await page.getByLabel('Дата плана', { exact: true }).fill('2026-10-13');
  await page.getByLabel('Данные доступны до', { exact: true }).fill('2026-10-11');
  await page.getByRole('button', { name: 'Рассчитать план', exact: true }).click();
  await expect(page.getByTestId('plan-table')).toBeVisible();
  await page.getByTestId('plan-table').locator('tbody tr').first().getByRole('spinbutton').fill('84');
  await capture('manual-plan');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await fs.writeFile(path.join(directory, 'manifest.json'), JSON.stringify({
    variant, source_root: sourceRoot, source_sha: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8', cwd: sourceRoot }).trim(),
    source_dirty: !!execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8', cwd: sourceRoot }).trim(),
    browser: browser.version(), generated_at: new Date().toISOString(), files,
    provenance: 'Real Chromium/Vite/FastAPI; synthetic fixture, isolated temporary SQLite; no mocked responses or visual edits.',
  }, null, 2));
});
