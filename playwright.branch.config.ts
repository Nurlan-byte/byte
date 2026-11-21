import { defineConfig } from "@playwright/test";
import path from "node:path";

const python = path.resolve(
  process.platform === "win32"
    ? ".venv/Scripts/python.exe"
    : ".venv/bin/python",
);
const api = "http://127.0.0.1:18007";
const frontend = "http://127.0.0.1:15179";

export default defineConfig({
  testDir: "./e2e-branch",
  outputDir: "./test-results/branch-access",
  workers: 1,
  timeout: 180_000,
  use: {
    baseURL: frontend,
    trace: "retain-on-failure",
    launchOptions: process.env.BAKEPLAN_CHROMIUM_PATH
      ? { executablePath: process.env.BAKEPLAN_CHROMIUM_PATH }
      : {},
  },
  webServer: [
    {
      command: `"${python}" -m e2e.branch_fixture`,
      url: api + "/api/health",
      reuseExistingServer: false,
    },
    {
      command: "npm --prefix frontend run dev -- --port 15179 --strictPort",
      url: frontend,
      env: { BAKEPLAN_API_URL: api },
      reuseExistingServer: false,
    },
  ],
});
