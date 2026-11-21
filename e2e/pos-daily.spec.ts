import { test, expect, type APIRequestContext } from "@playwright/test";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { navigate, openDetails } from "./helpers";

test.use({
  viewport: { width: 360, height: 800 },
  isMobile: true,
  hasTouch: true,
});

async function startApi(
  folder: string,
  day: string,
  provision: boolean,
  request: APIRequestContext,
  outage = false,
) {
  const venv = path.resolve(
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python",
  );
  const portFile = path.join(folder, day + ".port");
  const stopFile = path.join(folder, day + ".stop");
  const worker = spawn(
    process.env.BAKEPLAN_TEST_PYTHON || (existsSync(venv) ? venv : "python"),
    ["-m", "e2e.pos_daily_fixture"],
    {
      windowsHide: true,
      stdio: ["ignore", "ignore", "pipe"],
      env: {
        ...process.env,
        BAKEPLAN_DB: path.join(folder, "state.sqlite3"),
        BAKEPLAN_POS_ENABLED: "1",
        BAKEPLAN_POS_MASTER_KEY_FILE: path.join(folder, "master.key"),
        POS_TEST_PROVISION: provision ? "1" : "0",
        POS_TEST_OUTAGE: outage ? "1" : "0",
        POS_TEST_NOW: day + "T01:00:00+00:00",
        POS_TEST_PORT_FILE: portFile,
        POS_TEST_STOP_FILE: stopFile,
        BAKEPLAN_WEATHER_MODE: "",
        BAKEPLAN_WEATHER_API_KEY: "",
      },
    },
  );
  let diagnostics = "";
  worker.stderr.on("data", (data) => {
    diagnostics = (diagnostics + data.toString()).slice(-3000);
  });
  worker.on("error", (error) => {
    diagnostics = error.message;
  });
  const exited = new Promise<void>((resolve) =>
    worker.once("close", () => resolve()),
  );
  async function stop() {
    if (worker.exitCode === null && !worker.killed)
      await fs.writeFile(stopFile, "");
    await exited;
  }
  try {
    const address = async () =>
      "http://127.0.0.1:" + (await fs.readFile(portFile, "utf8"));
    await expect
      .poll(
        async () => {
          if (worker.exitCode !== null) return diagnostics;
          try {
            return (
              await request.get((await address()) + "/api/health", {
                timeout: 1000,
              })
            ).status();
          } catch {
            return 0;
          }
        },
        { timeout: 25_000 },
      )
      .toBe(200);
    return { base: await address(), stop };
  } catch (error) {
    await stop();
    throw error;
  }
}

test("one POS key updates tomorrow’s forecast after a server restart while the browser is closed", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(12_000);
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "bakeplan-daily-"));
  let server: Awaited<ReturnType<typeof startApi>> | undefined;
  const token = "fixture-pieces";
  const browserKeyPosts: string[] = [];
  const forecastPosts: string[] = [];
  try {
    server = await startApi(folder, "2026-10-19", true, request);
    // Keep the real API, worker, storage and UI. Route only to the isolated API port.
    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (req.postData()?.includes(token)) browserKeyPosts.push(url.pathname);
      if (req.method() === "POST" && url.pathname.endsWith("/first-forecast"))
        forecastPosts.push(url.pathname);
      const response = await route.fetch({
        url: server!.base + url.pathname + url.search,
        timeout: 65_000,
      });
      await route.fulfill({ response });
    });
    await page.goto("/");
    await navigate(page, "Данные");
    await openDetails(page, "Подключить кассу по ключу API");
    await expect(
      page.getByRole("checkbox", {
        name: "Обновлять продажи и прогноз каждый день",
      }),
    ).toBeChecked();
    await openDetails(page, "Файл ключа, город и период продаж");
    await page.getByLabel("Текстовый файл с ключом").setInputFiles({
      name: "key.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(token),
    });
    const connect = page.getByRole("button", {
      name: "Подключить ежедневное обновление",
    });
    await connect.click();
    await page
      .getByRole("combobox", { name: "Торговая точка", exact: true })
      .selectOption("1");
    expect(
      await (await request.get(server.base + "/api/pos/connections")).json(),
    ).toEqual([]);
    const connected = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/pos/connections") &&
        r.request().method() === "POST",
    );
    await connect.click();
    const first = await (await connected).json();
    expect(first.status).toBe("connected");
    expect(first.connection.last_success_day).toBe("2026-10-19");
    expect(first.forecast.targetDate).toBe("2026-10-20");
    await expect(page.getByTestId("forecast-result")).toContainText(
      "2026-10-20",
    );
    expect(
      await page.evaluate(() => JSON.stringify([localStorage, sessionStorage])),
    ).not.toContain(token);
    await page.goto("about:blank");
    await server.stop();
    for (const file of await fs.readdir(folder)) {
      expect(
        (await fs.readFile(path.join(folder, file))).includes(
          Buffer.from(token),
        ),
      ).toBe(false);
    }
    server = await startApi(folder, "2026-10-20", false, request);
    const connection = first.connection.id;
    let list = server.base + "/api/pos/connections";
    await expect
      .poll(
        async () =>
          (await (await request.get(list)).json())[0]?.last_success_day,
        { timeout: 65_000 },
      )
      .toBe("2026-10-20");
    const latest = await (
      await request.get(list + "/" + connection + "/latest")
    ).json();
    expect(latest.dataset.id).not.toBe(first.dataset.id);
    expect(latest.dataset.rows.at(-1).date).toBe("2026-10-19");
    expect(latest.forecast.targetDate).toBe("2026-10-21");
    expect(latest.forecast.result.dataset_id).toBe(latest.dataset.id);
    expect(JSON.stringify(latest)).not.toContain(token);
    await page.goto("/");
    await expect(page.getByTestId("forecast-result")).toContainText(
      "2026-10-21",
    );
    expect(browserKeyPosts).toEqual([
      "/api/pos/connections",
      "/api/pos/connections",
    ]);
    expect(forecastPosts).toEqual([]);
    await navigate(page, "Данные");
    await openDetails(page, "Подключить кассу по ключу API");
    const daily = page.getByRole("region", { name: "Ежедневные подключения" });
    await expect(daily).toContainText("Демо-клуб А");
    await expect(daily).toContainText("2026-10-20");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth === innerWidth,
      ),
    ).toBe(true);
    await daily.scrollIntoViewIfNeeded();
    const screenshot = testInfo.outputPath("daily-pos-connection.png");
    await page.screenshot({ path: screenshot });
    await testInfo.attach("Daily POS connection", {
      path: screenshot,
      contentType: "image/png",
    });
    await daily
      .getByRole("button", { name: "Открыть последний прогноз" })
      .click();
    await expect(page.getByTestId("forecast-result")).toContainText(
      "2026-10-21",
    );
    await page.goto("about:blank");
    await server.stop();
    server = await startApi(folder, "2026-10-21", false, request, true);
    list = server.base + "/api/pos/connections";
    await expect
      .poll(async () => (await (await request.get(list)).json())[0]?.status)
      .toBe("error");
    await page.goto("/");
    await expect(page.getByTestId("forecast-result")).toContainText(
      "2026-10-21",
    );
    await expect(page.locator("#panel-model")).toContainText(
      "Касса не обновилась.",
    );
    await expect(page.locator("#panel-model")).toContainText(
      "последний успешный прогноз от 2026-10-20",
    );
    await navigate(page, "Данные");
    await openDetails(page, "Подключить кассу по ключу API");
    await daily.getByRole("button", { name: "Отключить кассу" }).click();
    await expect(daily).toContainText(
      "Загруженные продажи и созданные планы сохранятся",
    );
    await daily.getByRole("button", { name: "Отмена", exact: true }).click();
    await expect(
      daily.getByRole("button", { name: "Открыть последний прогноз" }),
    ).toBeEnabled();
    await daily.getByRole("button", { name: "Отключить кассу" }).click();
    await daily
      .getByRole("button", { name: "Удалить ключ и отключить" })
      .click();
    await expect(daily).not.toBeAttached();
    expect(await (await request.get(list)).json()).toEqual([]);
    expect(
      (
        await request.get(server.base + "/api/datasets/" + latest.dataset.id)
      ).status(),
    ).toBe(200);
    expect(
      await page.evaluate(() => JSON.stringify([localStorage, sessionStorage])),
    ).not.toContain(token);
    await server.stop();
    for (const file of await fs.readdir(folder)) {
      expect(
        (await fs.readFile(path.join(folder, file))).includes(
          Buffer.from(token),
        ),
      ).toBe(false);
    }
  } finally {
    if (server) await server.stop();
    await fs.rm(folder, { recursive: true, force: true });
  }
});
