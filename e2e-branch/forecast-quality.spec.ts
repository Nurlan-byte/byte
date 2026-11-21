import { expect, test } from "@playwright/test";
import { navigate } from "../e2e/helpers";
import { businessDate } from "../frontend/src/dates";

test.use({ httpCredentials: { username: "admin-north", password: "fixture-only" } });

test("history includes tomorrow's issued forecast before an order is created", async ({ page }) => {
  const rows = Array.from({ length: 28 }, (_, i) => `${businessDate(i - 28)},water,2`);
  const history = await page.request.post("/api/datasets", { data: {
    bakery_id: "north", source: "synthetic", provenance: "Browser test of issued forecasts",
    target: "sold_units", unit: "physical_unit", schema_version: "pos-units-1",
    csv_text: ["date,product_id,sold", ...rows].join("\n"),
  } });
  expect(history.ok(), await history.text()).toBeTruthy();
  const { id } = await history.json();
  const input = { dataset_id: id, cutoff: businessDate(-1), target_date: businessDate(1), method: "AUTO" };
  const first = await page.request.post("/api/forecasts", { data: input });
  expect(first.ok(), await first.text()).toBeTruthy();
  const retry = await page.request.post("/api/forecasts", { data: input });
  expect(retry.ok()).toBeTruthy();
  await page.goto("/");
  await navigate(page, "История");
  const report = page.locator("details").filter({ has: page.getByText("Прогнозы и фактические продажи", { exact: true }) });
  await report.locator("summary").click();
  await report.getByRole("combobox", { name: /Источник/ }).selectOption("synthetic");
  await report.getByLabel("Дата продаж с", { exact: true }).fill(businessDate(1));
  await report.getByLabel("Дата продаж по", { exact: true }).fill(businessDate(1));
  await report.getByRole("button", { name: "Сверить прогнозы" }).click();
  await expect(report).toContainText("Сверено с продажами: 0 из 1");
  await expect(report).toContainText("День ещё не завершён: 1");
  await expect(report.getByText("water", { exact: true })).toBeVisible();
});
