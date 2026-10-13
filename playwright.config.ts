import { defineConfig } from '@playwright/test';
import path from 'node:path';
import os from 'node:os';

export default defineConfig({
  testDir: './e2e', workers: 1, timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:15173', trace: 'retain-on-failure',
    launchOptions: process.env.BAKEPLAN_CHROMIUM_PATH ? { executablePath: process.env.BAKEPLAN_CHROMIUM_PATH } : {},
  },
  webServer: [
    { command: `"${path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')}" -m uvicorn backend.app:app --host 127.0.0.1 --port 18000`, url: 'http://127.0.0.1:18000/docs', env: { BAKEPLAN_DB: path.join(os.tmpdir(), `bakeplan-e2e-${Date.now()}.sqlite3`) }, reuseExistingServer: false },
    { command: 'npm --prefix frontend run dev -- --port 15173 --strictPort', url: 'http://127.0.0.1:15173', env: { BAKEPLAN_API_URL: 'http://127.0.0.1:18000' }, reuseExistingServer: false },
  ],
});
