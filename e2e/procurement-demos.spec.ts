import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { navigate, openDetails } from "./helpers";

for (const example of [
  {
    label: "Демо Cloud Game Zone — 6 товаров",
    other: "Простой пример — 2 товара",
    products: 6,
    rows: 546,
    id: "cgz-synthetic-demo",
  },
  {
    label: "Простой пример — 2 товара",
    other: "Демо Cloud Game Zone — 6 товаров",
    products: 2,
    rows: 168,
    id: "procurement-demo",
  },
]) {
  test(`${example.label}: replaces the draft and saves only on explicit import`, async ({
    page,
  }) => {
    await page.goto("/");
    await page.locator("#planning-kind").selectOption("procurement");
    let imports = 0;
    page.on("request", (r) => {
      if (r.url().endsWith("/api/datasets") && r.method() === "POST") imports++;
    });
    const importDraft = async () => {
      const response = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/datasets") && r.request().method() === "POST",
      );
      await page
        .getByRole("button", {
          name: "Проверить и импортировать закупку",
          exact: true,
        })
        .click();
      const saved = await response;
      expect(saved.ok()).toBeTruthy();
      await expect(page.getByTestId("dataset-summary")).toBeVisible();
      return saved.json();
    };
    await page
      .getByRole("button", { name: example.other, exact: true })
      .click();
    const original = await importDraft();
    await page.locator("#proc-file").setInputFiles({
      name: "draft.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "date,product_id,opening,received,sold,waste,closing\n2026-08-01,tea,0,12,10,0,2\n",
      ),
    });
    await expect(page.locator('#import [role="status"]')).toContainText(
      "draft.csv",
    );
    await page
      .getByRole("button", { name: example.label, exact: true })
      .click();
    await expect(page.locator("#proc-demo-help")).toContainText(
      "Выбор заменит CSV, каталог и источник",
    );
    await expect(page.locator("#proc-file")).toHaveValue("");
    await expect(page.locator('#import [role="status"]')).toHaveCount(0);
    await expect(page.locator('[id^="proc-product-"]')).toHaveCount(
      example.products,
    );
    await expect(page.locator("#proc-shop")).toHaveValue(example.id);
    await expect(page.locator("#proc-source")).toHaveValue("synthetic");
    await expect(page.locator("#proc-provenance")).toHaveValue(
      /синтетический/i,
    );
    await expect(page.getByTestId("dataset-summary")).toContainText(
      String(original.row_count),
    );
    await expect(page.getByTestId("dataset-summary")).toContainText(
      "План использует сохранённый набор",
    );
    expect(imports).toBe(1);
    const saved = await importDraft();
    expect(saved).toMatchObject({
      row_count: example.rows,
      source: "synthetic",
      planning_kind: "procurement",
    });
    expect(saved.catalog).toHaveLength(example.products);
    expect(imports).toBe(2);
    // Uploading an actual file after either demo clears the demo attribution.
    await page.locator("#proc-file").setInputFiles({
      name: "new-upload.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "date,product_id,opening,received,sold,waste,closing\n",
      ),
    });
    await expect(page.locator("#proc-shop")).toHaveValue("");
    await expect(page.locator("#proc-source")).toHaveValue("local");
    await expect(page.locator("#proc-provenance")).toHaveValue(
      "new-upload.csv",
    );
  });
}

test("CGZ demo button imports six products and completes train, plan, confirm, export and reload", async ({
  page,
}, testInfo) => {
  const evidenceDir = testInfo.outputPath("evidence");
  await fs.mkdir(evidenceDir, { recursive: true });
  const catalog = JSON.parse(
    await fs.readFile(
      "fixtures/cloud_game_zone_synthetic_catalog.json",
      "utf8",
    ),
  );
  let planId = "";
  await page.goto("/");
  await page.locator("#planning-kind").selectOption("procurement");
  await navigate(page, "Данные");
  await page
    .getByRole("button", {
      name: "Демо Cloud Game Zone — 6 товаров",
      exact: true,
    })
    .click();
  await expect(page.locator('[id^="proc-product-"]')).toHaveCount(6);
  await expect(page.locator("#proc-source")).toHaveValue("synthetic");
  const importing = page.waitForResponse(
    (r) => r.url().endsWith("/api/datasets") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Проверить и импортировать закупку" })
    .click();
  const imported = await importing;
  expect([200, 201]).toContain(imported.status());
  const dataset = await imported.json();
  expect(dataset.catalog).toEqual(catalog);
  expect(dataset).toMatchObject({
    row_count: 546,
    planning_kind: "procurement",
    source: "synthetic",
  });
  await expect(page.getByTestId("dataset-summary")).toContainText("546");

  await navigate(page, "Модель");
  await page.getByLabel("Срез обучения и прогноза").fill("2026-10-14");
  await page.getByLabel("Дата прогноза", { exact: true }).fill("2026-10-16");
  const training = page.waitForResponse(
    (r) => r.url().includes("/train") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Подготовить экспериментальный расчёт" })
    .click();
  expect((await training).ok()).toBe(true);
  await expect(page.getByTestId("model-summary")).toContainText("Расчёт готов");
  await page.getByRole("button", { name: "Проверить прогноз" }).click();
  await expect(page.getByTestId("forecast-table")).toContainText(
    "По похожим дням недели",
  );
  await expect(page.getByTestId("forecast-table")).toContainText(
    "Экспериментальный расчёт",
  );

  await navigate(page, "План");
  await page.getByLabel("Дата продаж", { exact: true }).fill("2026-10-16");
  await page
    .getByLabel("Данные доступны до", { exact: true })
    .fill("2026-10-14");
  await page.getByLabel("Срок поставки, дней").fill("1");
  await openDetails(page, "Настройки прогноза");
  await page.getByLabel("Способ расчёта закупки").selectOption("ML");
  for (const item of catalog)
    await page.getByLabel(`Доступный остаток: ${item.name}`).fill("0");
  const planning = page.waitForResponse(
    (r) => r.url().endsWith("/api/plans") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Рассчитать заказ", exact: true })
    .click();
  const plan = await (await planning).json();
  planId = plan.id;
  expect(plan.items).toHaveLength(6);
  expect(
    plan.items.filter((item: { method: string }) => item.method === "ML"),
  ).toHaveLength(3);
  expect(
    plan.items.filter((item: { method: string }) => item.method === "B1"),
  ).toHaveLength(3);
  const water = page
    .getByTestId("procurement-table")
    .locator("tbody tr")
    .filter({ hasText: "Вода в бутылке" });
  await expect(water).toContainText("24");
  await water.getByRole("spinbutton").fill("36");
  await expect(water.getByRole("spinbutton")).toHaveValue("36");
  await page.getByRole("button", { name: "Подтвердить заказ" }).click();
  await expect(page.getByRole("status")).toContainText("сохран");
  await navigate(page, "История");
  await expect(
    page.locator(`.history-card[data-plan-id="${planId}"]`),
  ).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await page
    .locator(`.history-card[data-plan-id="${planId}"]`)
    .getByRole("link", { name: "Экспорт CSV" })
    .click();
  const download = await downloadEvent;
  const exported = await fs.readFile((await download.path())!);
  expect(exported.toString("utf8")).toContain(",36,");
  await fs.writeFile(path.join(evidenceDir, "confirmed-order.csv"), exported);

  await page.reload();
  await navigate(page, "История");
  await page
    .locator(`.history-card[data-plan-id="${planId}"]`)
    .getByRole("button", { name: "Открыть план", exact: true })
    .click();
  await expect(page.getByTestId("procurement-table")).toBeVisible();
  const savedWater = page
    .getByTestId("procurement-table")
    .locator("tbody tr")
    .filter({ hasText: "Вода в бутылке" });
  await expect(savedWater.getByRole("spinbutton")).toHaveValue("36");
  const saved = await page.request.get(`/api/plans/${planId}`);
  expect(saved.ok()).toBeTruthy();
  await fs.writeFile(
    path.join(evidenceDir, "confirmed-plan.json"),
    JSON.stringify(await saved.json(), null, 2),
  );
  await page.screenshot({
    path: path.join(evidenceDir, "jury-cgz.png"),
    fullPage: true,
  });
});
