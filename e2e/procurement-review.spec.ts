import { expect, test } from "@playwright/test";

test("reviewed orders preserve hidden lines and expose only the saved supplier message", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  });
  await page.goto("/");
  await page.getByLabel("Как пополняем товары").selectOption("procurement");
  await page.getByRole("button", { name: "Простой пример — 2 товара" }).click();
  await page.getByRole("button", { name: "Проверить и импортировать закупку" }).click();
  await expect(page.getByTestId("dataset-summary")).toBeVisible();
  await page.getByRole("button", { name: "Перейти к плану", exact: true }).click();

  const target = page.getByLabel("Дата продаж", { exact: true });
  await target.fill("2026-09-01");
  await page.getByLabel("Данные доступны до", { exact: true }).fill("2026-08-23");
  await page.getByLabel("Срок поставки, дней").fill("2");
  await page.getByLabel("Доступный остаток: Чай в бутылке", { exact: true }).fill("0");
  await expect(page.getByLabel("Доступный остаток: Сок", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "Рассчитать заказ", exact: true }).click();

  const tea = page.getByLabel("Заказ: Чай в бутылке", { exact: true });
  const juice = page.getByLabel("Заказ: Сок", { exact: true });
  const review = page.getByRole("button", { name: /Требуют проверки/ });
  const all = page.getByRole("button", { name: /Все позиции/ });
  const confirm = page.getByRole("button", { name: "Подтвердить заказ", exact: true });
  const supplier = page.getByRole("region", { name: "Сообщение поставщику" });
  const message = supplier.getByRole("textbox", { name: "Текст заказа для поставщика" });
  const copy = supplier.getByRole("button", { name: "Скопировать заказ" });

  await expect(tea).toHaveValue("24");
  await expect(juice).toHaveValue("0");
  await expect(supplier).toBeHidden();
  const teaRow = page.getByRole("row").filter({ has: tea });
  await expect(teaRow.locator(".recommendation-cell")).toContainText("4 уп.");
  await tea.fill("12");
  await expect(teaRow.locator(".confirmation-cell")).toContainText("2 уп.");
  await expect(teaRow).toContainText("До прогноза не хватает 8 шт.");
  await tea.fill("30");
  await expect(teaRow).not.toContainText("До прогноза не хватает");
  await review.click();
  await expect(review).toHaveAttribute("aria-pressed", "true");
  await expect(tea).toBeHidden();
  await expect(juice).toBeVisible();
  await expect(page.getByRole("row").filter({ has: juice })).toContainText("Укажите остаток");
  await juice.fill("8");
  await all.click();
  await expect(tea).toHaveValue("30");
  await expect(juice).toHaveValue("8");
  await review.click();
  await confirm.click();
  await expect(message).toHaveValue(/Чай в бутылке — 30 шт\./);
  await expect(message).toHaveValue(/Сок — 8 шт\./);

  await all.click();
  await juice.fill("0");
  await expect(copy).toBeHidden();
  await confirm.click();
  await expect(message).toHaveValue(/Чай в бутылке — 30 шт\. \(5 уп\. по 6 шт\.\)/);
  await expect(message).not.toHaveValue(/Сок/);
  await copy.click();
  await expect(supplier).toContainText("Скопируйте выделенный текст вручную");
  await expect(message).toBeFocused();
  expect(await message.evaluate((field: HTMLTextAreaElement) =>
    field.selectionStart === 0 && field.selectionEnd === field.value.length,
  )).toBe(true);

  await target.fill("2026-09-02");
  await expect(page.getByTestId("stale-plan")).toBeVisible();
  await expect(copy).toBeHidden();
});
