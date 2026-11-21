import { expect, test } from "@playwright/test";
import { navigate } from "./helpers";

test.use({ viewport: { width: 360, height: 800 } });

test("both empty plans show a usable first action at phone size", async ({ page }) => {
  await page.goto("/");
  const start = page.getByRole("button", { name: "Перейти к данным", exact: true });
  await expect(start).toBeEnabled({ timeout: 15000 });
  await expect(start).toBeInViewport({ ratio: 1 });
  await page.getByLabel("Как пополняем товары").selectOption("procurement");
  await navigate(page, "План");
  await expect(page.getByLabel("Срок поставки, дней")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Рассчитать заказ" })).toHaveCount(0);
  await expect(start).toBeInViewport({ ratio: 1 });
  await start.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Простой пример — 2 товара" })).toBeVisible();
});

test("calculating an order moves keyboard focus to its result and preserves a manual edit", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Как пополняем товары").selectOption("procurement");
  await page.getByRole("button", { name: "Простой пример — 2 товара" }).click();
  await page.getByRole("button", { name: "Проверить и импортировать закупку" }).click();
  await page.getByRole("button", { name: "Перейти к плану", exact: true }).click();
  await page.getByLabel("Дата продаж", { exact: true }).fill("2026-09-01");
  await page.getByLabel("Данные доступны до", { exact: true }).fill("2026-08-23");
  await page.getByLabel("Срок поставки, дней").fill("2");
  await page.getByLabel("Доступный остаток: Чай в бутылке").fill("0");
  await page.getByRole("button", { name: "Рассчитать заказ", exact: true }).focus();
  const created = page.waitForResponse(r => r.url().endsWith("/api/plans") && r.request().method() === "POST");
  await page.keyboard.press("Enter");
  const draft = await (await created).json();
  const result = page.getByRole("heading", { name: /Заказ от .*2026-09-01/ });
  await expect(result).toBeFocused();
  await expect(result).toBeInViewport({ ratio: 1 });
  await page.getByRole("link", { name: "К подтверждению" }).click();
  await expect(page.getByRole("button", { name: "Подтвердить заказ", exact: true })).toBeInViewport({ ratio: 1 });
  await expect(page.getByText("Ожидает проверки", { exact: true })).toBeVisible();
  const saved = await page.request.get(`/api/plans/${draft.id}`);
  expect(await saved.json()).toMatchObject({ status: "draft", revision: draft.revision });
  await expect(page.locator("#plan-confirm")).toBeFocused();
  await page.getByRole("link", { name: "К товарам" }).click();
  await expect(page.getByRole("region", { name: "Прогноз продаж и заказ" })).toBeFocused();
  const quantity = page.getByLabel("Заказ: Чай в бутылке", { exact: true });
  await quantity.fill("30");
  await expect(quantity).toBeFocused();
  await expect(quantity).toHaveValue("30");
});
