import {
  test,
  expect,
  type Page,
  type APIRequestContext,
  type TestInfo,
} from "@playwright/test";
import fs from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import os from "node:os";
import { navigate, openDetails, readOrderCsv } from "./helpers";

test.use({
  viewport: { width: 360, height: 800 },
  isMobile: true,
  hasTouch: true,
});

async function confirmAndExport(
  page: Page,
  request: APIRequestContext,
  testInfo: TestInfo,
  base: string,
  python: string,
  sourceId: string,
) {
  await openDetails(page, "Подготовить заказ поставщику");
  await expect(page.getByLabel("Способ прогноза", { exact: true })).toHaveCount(
    0,
  );
  await page.getByLabel("Штук в упаковке: Чай").fill("4");
  await page.getByLabel("Штук в упаковке: Вода").fill("2");
  const converted = page.waitForResponse((r) =>
    r.url().endsWith("/order-catalog"),
  );
  await page.getByRole("button", { name: "Перейти к расчёту заказа" }).click();
  const catalog = await (await converted).json();
  expect(catalog.source_dataset_id).toBe(sourceId);
  expect(catalog.forecast_only).toBe(false);
  await expect(page.getByLabel("Способ расчёта закупки")).toHaveCount(0);
  await page.getByLabel("Срок поставки, дней").fill("1");
  await page.getByLabel("Доступный остаток: Чай", { exact: true }).fill("2");
  await expect(
    page.getByLabel("Доступный остаток: Вода", { exact: true }),
  ).toHaveValue("");
  await page
    .getByRole("button", { name: "Рассчитать заказ", exact: true })
    .click();
  const tea = page.getByLabel("Заказ: Чай", { exact: true });
  const water = page.getByLabel("Заказ: Вода", { exact: true });
  await expect(tea).toHaveValue("4");
  await expect(water).toHaveValue("");
  const confirm = page.getByRole("button", {
    name: "Подтвердить заказ",
    exact: true,
  });
  await expect(confirm).toBeDisabled();
  await water.fill("0");
  await expect(confirm).toBeEnabled();
  await water.fill("2");
  await tea.fill("8");
  const confirmed = page.waitForResponse(
    (r) => r.url().endsWith("/confirm") && r.request().method() === "POST",
  );
  await confirm.click();
  const saved = await (await confirmed).json();
  expect(saved.status).toBe("confirmed");
  expect(saved.dataset_id).toBe(catalog.id);
  expect(saved.usable_stock_assumption).toEqual({ "17": 2 });
  expect(saved.lead_time_days).toBe(1);
  expect(
    saved.items.map((i: { confirmed_qty: number }) => i.confirmed_qty),
  ).toEqual([8, 2]);
  expect(
    await (await request.get(base + "/api/plans/" + saved.id)).json(),
  ).toEqual(saved);
  const link = page.getByRole("link", { name: "Экспорт заказа CSV" });
  await link.scrollIntoViewIfNeeded();
  const screenshot = testInfo.outputPath("mobile-confirmed-order.png");
  await page.screenshot({ path: screenshot });
  await testInfo.attach("mobile-confirmed-order", {
    path: screenshot,
    contentType: "image/png",
  });
  const downloaded = page.waitForEvent("download");
  await link.click();
  const output = testInfo.outputPath("confirmed-order.csv");
  await (await downloaded).saveAs(output);
  const rows = readOrderCsv(output, python);
  expect(rows).toMatchObject([
    {
      plan_id: saved.id,
      product_id: "17",
      name: "Чай",
      pack_size: "4",
      confirmed_order_units: "8",
      assumed_usable_stock_units: "2",
      suggested_order_units: "4",
      suggested_order_packs: "1",
    },
    {
      plan_id: saved.id,
      product_id: "18",
      name: "Вода",
      pack_size: "2",
      confirmed_order_units: "2",
      assumed_usable_stock_units: "",
      suggested_order_units: "",
      suggested_order_packs: "",
    },
  ]);
  await testInfo.attach("confirmed-order", {
    path: output,
    contentType: "text/csv",
  });
}

test("mobile POS key to forecast, packing, confirmation and CSV export", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(12_000);
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "bakeplan-pos-"));
  const db = path.join(folder, "store.sqlite3");
  const portFile = path.join(folder, "port");
  const venv = path.resolve(
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python",
  );
  const python =
    process.env.BAKEPLAN_TEST_PYTHON || (existsSync(venv) ? venv : "python");
  const worker = spawn(python, ["-m", "e2e.pos_fixture"], {
    windowsHide: true,
    stdio: "ignore",
    env: {
      ...process.env,
      BAKEPLAN_DB: db,
      BAKEPLAN_POS_ENABLED: "1",
      POS_TEST_PORT_FILE: portFile,
      BAKEPLAN_WEATHER_MODE: "",
      BAKEPLAN_WEATHER_API_KEY: "",
    },
  });
  let spawnError: Error | undefined;
  const exit = new Promise<void>((resolve) => {
    worker.once("close", () => resolve());
    worker.once("error", (error) => {
      spawnError = error;
    });
  });
  let frontend: ChildProcess | undefined;
  try {
    await expect
      .poll(
        async () => {
          if (spawnError || worker.exitCode !== null)
            throw spawnError || new Error("Isolated POS API exited");
          return fs.readFile(portFile, "utf8").catch(() => "");
        },
        { timeout: 20_000 },
      )
      .toMatch(/^\d+$/);
    const base = "http://127.0.0.1:" + (await fs.readFile(portFile, "utf8"));
    await expect
      .poll(
        async () => {
          try {
            return (await request.get(base + "/api/health")).status();
          } catch {
            return 0;
          }
        },
        { timeout: 20_000 },
      )
      .toBe(200);
    // Give downloads and ordinary requests the same real proxy as a normal Vite session.
    const launch = [
      'import { createServer } from "vite";',
      'const server = await createServer({ server: { host: "127.0.0.1", port: 0 } });',
      "await server.listen();",
      "process.send(server.httpServer.address().port);",
    ].join("\n");
    frontend = spawn(process.execPath, ["--input-type=module", "-e", launch], {
      cwd: path.resolve("frontend"),
      env: { ...process.env, BAKEPLAN_API_URL: base },
      windowsHide: true,
      stdio: ["ignore", "ignore", "ignore", "ipc"],
    });
    const uiPort = await new Promise<number>((resolve, reject) => {
      frontend!.once("message", (port) => resolve(Number(port)));
      frontend!.once("error", reject);
      frontend!.once("exit", () =>
        reject(new Error("Isolated frontend exited")),
      );
    });
    await page.goto("http://127.0.0.1:" + uiPort);
    for (const [token, unit, value] of [
      ["fixture-pieces", "physical_unit", "6.0"],
      ["fixture-weight", "item_line", "1.0"],
    ]) {
      await navigate(page, "Данные");
      const open = page
        .getByText("Подключить кассу по ключу API", { exact: true })
        .locator("..");
      if (!(await open.evaluate((el) => el.hasAttribute("open"))))
        await open.locator("summary").click();
      const box = page.getByLabel("Ключ API кассы", { exact: true });
      await expect(box).toBeVisible();
      if (token === "fixture-pieces") {
        const fileInput = page.getByLabel("Текстовый файл с ключом");
        await open.locator("form details > summary").click();
        await fileInput.setInputFiles({
          name: "cash-register.txt",
          mimeType: "text/plain",
          buffer: Buffer.from(token),
        });
      } else {
        await box.fill(token);
      }
      const submit = page.getByRole("button", {
        name: "Загрузить продажи и получить прогноз",
      });
      if (token === "fixture-pieces") {
        await submit.click();
        await page
          .getByRole("combobox", { name: "Торговая точка", exact: true })
          .selectOption("1");
      }
      const imported = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/pos/import") &&
          r.request().method() === "POST",
      );
      await submit.click();
      const detail = await (await imported).json();
      expect(detail.status).toBe("imported");
      expect(detail.dataset.unit).toBe(unit);
      expect(detail.dataset.rows).toHaveLength(
        unit === "physical_unit" ? 112 : 56,
      );
      const table = page.getByTestId("forecast-table");
      await expect(table).toBeVisible({ timeout: 65000 });
      await expect(
        table.getByRole("row").filter({ hasText: "Чай" }),
      ).toContainText(value);
      await expect(page.locator("#panel-model")).toBeVisible();
      const saved = await (
        await request.get(base + "/api/datasets/" + detail.dataset.id)
      ).json();
      expect(saved).toEqual(detail.dataset);
      expect(
        await page.evaluate(
          () => +document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      expect(
        await page.evaluate(() =>
          JSON.stringify([localStorage, sessionStorage]),
        ),
      ).not.toContain(token);
      expect(JSON.stringify(saved)).not.toContain(token);
      await table.scrollIntoViewIfNeeded();
      const screenshot = testInfo.outputPath(
        "mobile-forecast-" + unit + ".png",
      );
      await page.screenshot({ path: screenshot });
      await testInfo.attach("mobile-forecast-" + unit, {
        path: screenshot,
        contentType: "image/png",
      });
      if (unit === "physical_unit") {
        await confirmAndExport(
          page,
          request,
          testInfo,
          base,
          python,
          detail.dataset.id,
        );
      } else {
        await expect(
          page.getByText("Подготовить заказ поставщику"),
        ).toHaveCount(0);
      }
      await page.reload();
      await expect(page.getByTestId("workspace-source")).toContainText(
        "Poster POS",
      );
    }
    for (const file of await fs.readdir(folder)) {
      const bytes = await fs.readFile(path.join(folder, file));
      expect(
        bytes.includes(Buffer.from("fixture-pieces")) ||
          bytes.includes(Buffer.from("fixture-weight")),
      ).toBe(false);
    }
  } finally {
    if (frontend && frontend.exitCode === null) {
      const closed = new Promise<void>((resolve) =>
        frontend!.once("close", () => resolve()),
      );
      frontend.kill();
      await closed;
    }
    if (worker.exitCode === null) worker.kill();
    await exit;
    await fs.rm(folder, { recursive: true, force: true });
  }
});
