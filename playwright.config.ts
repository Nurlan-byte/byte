import { defineConfig } from '@playwright/test';
import path from 'node:path';
import os from 'node:os';

export default defineConfig({
  testDir: './e2e', workers: 1, timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    { command: `"${path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')}" -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`, url: 'http://127.0.0.1:8000/docs', env: { BAKEPLAN_DB: path.join(os.tmpdir(), `bakeplan-e2e-${Date.now()}.sqlite3`) }, reuseExistingServer: false },
    { command: 'npm --prefix frontend run dev -- --port 5173', url: 'http://127.0.0.1:5173', reuseExistingServer: false },
  ],
});
