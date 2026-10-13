import { defineConfig } from '@playwright/test';
import path from 'node:path';
import os from 'node:os';
import { randomUUID } from 'node:crypto';

const python = path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');

// A separate invocation, ports and new SQLite isolate the recording from both
// the 23 regression scenarios and the before/after PNG capture harness.
export default defineConfig({
  testDir: './video',
  outputDir: './test-results/video',
  workers: 1,
  retries: 0,
  timeout: 180_000,
  use: {
    baseURL: 'http://127.0.0.1:15175',
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Asia/Qyzylorda',
    deviceScaleFactor: 1,
    // The spec creates and closes its own recorded context so it can verify the
    // finalized WebM before writing a successful capture manifest.
    video: 'off',
    launchOptions: process.env.BAKEPLAN_CHROMIUM_PATH
      ? { executablePath: process.env.BAKEPLAN_CHROMIUM_PATH }
      : {},
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn backend.app:app --host 127.0.0.1 --port 18003`,
      url: 'http://127.0.0.1:18003/docs',
      env: { BAKEPLAN_DB: path.join(os.tmpdir(), `bakeplan-video-${randomUUID()}.sqlite3`) },
      reuseExistingServer: false,
    },
    {
      command: 'npm --prefix frontend run dev -- --port 15175 --strictPort',
      url: 'http://127.0.0.1:15175',
      env: { BAKEPLAN_API_URL: 'http://127.0.0.1:18003' },
      reuseExistingServer: false,
    },
  ],
});
