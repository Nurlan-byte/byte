import { expect, test } from "@playwright/test";
import { navigate, openDetails } from "../e2e/helpers";

const day = "2025-08-16";
const render = (n: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(n);

test.use({ httpCredentials: { username: "admin-matched", password: "fixture-only" } });

test("two issued SKU forecasts match later sales without a confirmed order", async ({ page }) => {
  const api = page.request;
  const plans = async () => {
    const response = await api.get("/api/plans");
    expect(response.status()).toBe(200);
    return response.json();
  };
  expect(await plans()).toEqual([]);
  const history = await api.get("/api/datasets");
  expect(history.status()).toBe(200);
  const [dataset] = await history.json();
  // Read the model's actual predictions for the immutable issue inputs. This historical
  // replay cannot issue a second live forecast, and is independent of the reporting API.
  const replay = await api.post("/api/forecasts", {
    data: { dataset_id: dataset.id, target_date: day, cutoff: "2025-08-14", method: "AUTO" },
  });
  expect(replay.status()).toBe(200);
  const predictions: { product_id: string; forecast_mean: number; b1_mean: number }[] =
    (await replay.json()).items;
  const baseline = { tea: 4, chips: 10 };
  const sales = { tea: 2, chips: 12 };
  const expected = predictions.map((item) => {
    const sku = item.product_id as keyof typeof sales;
    expect(item.b1_mean).toBe(baseline[sku]);
    return { sku, error: item.forecast_mean - sales[sku], b1: baseline[sku] - sales[sku] };
  });
  expect(expected.map((p) => p.sku).sort()).toEqual(["chips", "tea"]);

  await page.goto("/");
  await navigate(page, "История");
  await openDetails(page, "Прогнозы и фактические продажи");
  const panel = page.locator("details").filter({ has: page.getByText("Прогнозы и фактические продажи", { exact: true }) });
  await panel.getByLabel("Дата продаж с", { exact: true }).fill(day);
  await panel.getByLabel("Дата продаж по", { exact: true }).fill(day);
  const source = panel.getByRole("combobox", { name: /^Источник/ });
  await expect(source).toBeVisible();
  await source.selectOption("synthetic");
  const refresh = panel.getByRole("button", { name: "Сверить прогнозы" });
  await refresh.click();
  await expect(panel.getByRole("status")).toContainText("Сверено с продажами: 0 из 2");
  await expect(panel.getByText("Нет факта продаж: 2")).toBeVisible();

  const receipt = await api.post("/api/datasets", {
    data: {
      bakery_id: "matched", source: "synthetic", schema_version: "procurement-1",
      unit: "physical_unit", target: "sold_units", provenance: "Synthetic completed day",
      catalog: Object.keys(sales).map((sku) => ({ product_id: sku, name: sku, pack_size: 1 })),
      csv_text: [
        "date,product_id,opening,received,sold,waste,closing,is_open,stockout_status",
        ...Object.entries(sales).map(([sku, qty]) => `${day},${sku},0,${qty},${qty},0,0,true,none`),
      ].join("\n"),
    },
  });
  expect(receipt.status()).toBe(201);
  await refresh.click();
  await expect(panel.getByRole("status")).toContainText("Сверено с продажами: 2 из 2");
  await expect(panel.getByRole("status")).toContainText("100%");
  const mae = expected.reduce((total, p) => total + Math.abs(p.error), 0) / expected.length;
  expect(render(mae)).not.toBe(render(2));
  await expect(panel.getByText(/Средняя абсолютная ошибка BakePlan/)).toContainText(
    `Средняя абсолютная ошибка BakePlan — ${render(mae)} шт., по похожим дням недели (B1) — 2 шт.`,
  );
  const table = panel.getByRole("table", { name: "Ошибка по товарам, шт." });
  for (const { sku, error, b1 } of expected) {
    const row = table.getByRole("row").filter({ has: page.getByRole("rowheader", { name: sku, exact: true }) });
    await expect(row.getByRole("cell")).toHaveText([
      "1 / 1", render(Math.abs(error)), render(Math.abs(b1)),
      render(Math.max(error, 0)), render(Math.max(-error, 0)),
    ]);
  }
  expect(await plans()).toEqual([]);
});
