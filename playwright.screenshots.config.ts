import { defineConfig } from '@playwright/test';
import path from 'node:path';
import os from 'node:os';

// A separate invocation and empty database keep screenshot state independent
// from the 18 regression scenarios in playwright.config.ts.
export default defineConfig({
  testDir: './screenshots',
  outputDir: './test-results/screenshots',
  workers: 1,
  retries: 0,
  timeout: 180_000,
  use: {
    baseURL: 'http://127.0.0.1:15174',
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Asia/Qyzylorda',
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    launchOptions: process.env.BAKEPLAN_CHROMIUM_PATH
      ? { executablePath: process.env.BAKEPLAN_CHROMIUM_PATH }
      : {},
  },
  webServer: [
    {
      command: `"${path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')}" -m uvicorn backend.app:app --host 127.0.0.1 --port 18002`,
      url: 'http://127.0.0.1:18002/docs',
      env: { BAKEPLAN_DB: path.join(os.tmpdir(), `bakeplan-screenshots-${Date.now()}-${process.pid}.sqlite3`) },
      reuseExistingServer: false,
    },
    {
      command: 'npm --prefix frontend run dev -- --port 15174 --strictPort',
      url: 'http://127.0.0.1:15174',
      env: { BAKEPLAN_API_URL: 'http://127.0.0.1:18002' },
      reuseExistingServer: false,
    },
  ],
});
