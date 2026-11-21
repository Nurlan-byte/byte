import { expect, test, type Page } from "@playwright/test";
import { businessDate } from "../frontend/src/dates";
import { calculateAutomatically, navigate, openDetails } from "./helpers";

async function shop(page: Page, branch: string) {
  const dataset = await page.request.post("/api/datasets", {
    data: {
      schema_version: "procurement-1",
      source: "synthetic",
      bakery_id: branch,
      target: "sold_units",
      unit: "physical_unit",
      provenance: branch,
      catalog: [{ product_id: "water", name: "Вода", pack_size: 1 }],
      csv_text:
        "date,product_id,opening,received,sold,waste,closing\n" +
        [businessDate(-13), businessDate(-6)]
          .map((day) => `${day},water,0,20,20,0,0`)
          .join("\n"),
    },
  });
  expect(dataset.ok()).toBeTruthy();
  return (await dataset.json()).id as string;
}

async function openShop(page: Page, id: string) {
  await page.getByLabel("Как пополняем товары").selectOption("procurement");
  await navigate(page, "Данные");
  await openDetails(page, "Открыть сохранённые данные закупки");
  await page.getByRole("button", { name: "Обновить наборы закупки" }).click();
  await page.getByLabel("Сохранённый набор закупки").selectOption(id);
  await page.getByRole("button", { name: "Открыть набор закупки" }).click();
  await navigate(page, "План");
}

async function createDelivery(page: Page, number: string) {
  await page.getByLabel("Номер поставки").fill(number);
  await page
    .getByLabel("Ожидаемая дата", { exact: true })
    .fill(businessDate(1));
  await page.getByLabel("Заказано, шт.", { exact: true }).fill("12");
  await page.getByRole("button", { name: "Записать поставку" }).click();
  const row = page.getByRole("article", { name: `${number} · Вода` });
  await expect(row.getByText("12 шт.", { exact: true })).toHaveCount(2);
  return row;
}

test("a lost receipt response can be retried after reload; saved and current shipment amounts stay distinct", async ({
  page,
}) => {
  const branch = "registry-retry";
  const id = await shop(page, branch);
  await page.goto("/");
  await openShop(page, id);
  await openDetails(page, "Реестр поставок · " + branch);
  const row = await createDelivery(page, "В-12");
  const planButton = page.getByRole("button", { name: "Рассчитать заказ" });
  await page.getByLabel("Дата продаж", { exact: true }).fill(businessDate(1));
  await page
    .getByLabel("Данные доступны до", { exact: true })
    .fill(businessDate(-1));
  await page.getByLabel("Срок поставки, дней").fill("1");
  await page.getByLabel("Доступный остаток: Вода").fill("0");
  await expect(page.getByLabel("Источник поставок")).toHaveValue("registry");
  test.slow();
  const plan = await calculateAutomatically(page, "/plans", planButton);
  const demand = Math.ceil(plan.items[0].forecast_mean);
  expect(demand).toBeGreaterThan(12);
  const order = page.getByLabel("Заказ: Вода", { exact: true });
  const snapshot = page.locator("[data-delivery-id]");
  await expect(order).toHaveValue(String(demand - 12));
  await expect(snapshot).toContainText("12 шт.");
  let originalKey = "";
  await page.route(
    "**/deliveries/*/receipts",
    async (route) => {
      originalKey = route.request().postDataJSON().idempotency_key;
      const result = await route.fetch();
      expect(result.ok()).toBeTruthy();
      await route.abort("failed");
    },
    { times: 1 },
  );
  await row.getByLabel("Получено сейчас, шт.").fill("5");
  await row.getByRole("button", { name: "Принять", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.reload();
  await openDetails(page, "Реестр поставок · " + branch);
  const retry = row.getByRole("button", { name: "Повторить приёмку" });
  const request = page.waitForRequest((r) => r.url().endsWith("/receipts"));
  await retry.click();
  expect((await request).postDataJSON().idempotency_key).toBe(originalKey);
  await expect(retry).toBeHidden();
  await expect(row).toContainText("7 шт.");
  await expect(row).toContainText("5 шт.");
  await expect(snapshot).toContainText("12 шт.");
  await planButton.click();
  await expect(order).toHaveValue(String(demand - 7));
  await expect(snapshot).toContainText("7 шт.");
  await row.getByText("Изменить поставку").click();
  await row.getByLabel("Новая дата поступления").fill(businessDate(2));
  await row.getByRole("button", { name: "Перенести дату" }).click();
  await expect(row).toContainText(businessDate(2));
  await expect(snapshot).toContainText(businessDate(1));
  await planButton.click();
  await expect(order).toHaveValue(String(demand));
  await expect(snapshot).toHaveCount(0);
  page.once("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: "Отменить остаток" }).click();
  await expect(row).toContainText("Отменена");
  await expect(row).toContainText("0 шт.");
});

test("same-number deliveries are independent between branches and can be received in full", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 850 });
  const a = "registry-first",
    b = "registry-second";
  const first = await shop(page, a),
    second = await shop(page, b);
  await page.goto("/");
  await openShop(page, first);
  await openDetails(page, "Реестр поставок · " + a);
  await createDelivery(page, "R-1");
  await openShop(page, second);
  await openDetails(page, "Реестр поставок · " + b);
  await expect(page.getByText("Поставок пока нет.")).toBeVisible();
  const row = await createDelivery(page, "R-1");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(360);
  await row.getByLabel("Получено сейчас, шт.").fill("12");
  await row.getByRole("button", { name: "Принять", exact: true }).click();
  await expect(row).toContainText("Принята");
  await expect(row.getByLabel("Получено сейчас, шт.")).toBeHidden();
  await openShop(page, first);
  await openDetails(page, "Реестр поставок · " + a);
  await expect(row.getByLabel("Получено сейчас, шт.")).toHaveAttribute(
    "max",
    "12",
  );
});
