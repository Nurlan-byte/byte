import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { navigate } from "./helpers";

async function importShop(page: Page, name: string) {
  await navigate(page, "Данные");
  await page
    .getByRole("button", { name: "Простой пример — 2 товара", exact: true })
    .click();
  await page.locator("#proc-shop").fill(name);
  await page.locator("#proc-provenance").fill(name);
  await page
    .getByRole("button", { name: "Проверить и импортировать закупку" })
    .click();
  await expect(page.getByTestId("dataset-summary")).toContainText(name);
  await navigate(page, "План");
}

async function startOrder(page: Page) {
  await page.goto("/");
  await page.locator("#planning-kind").selectOption("procurement");
  await importShop(page, "workspace-a");
  await page.locator("#proc-date").fill("2026-09-01");
  await page.locator("#proc-cutoff").fill("2026-08-23");
  await page.locator("#proc-lead").fill("2");
  await page.locator("#stock-tea").fill("0");
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/api/plans") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Рассчитать заказ" }).click();
  return (await response).json();
}

test("switching shops clears stock and lead even before a first order; server bounds disable calculation", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#planning-kind").selectOption("procurement");
  await importShop(page, "first-without-plan");
  await page.locator("#stock-tea").fill("53");
  await page.locator("#proc-lead").fill("3");
  await importShop(page, "another-shop");
  await expect(page.locator("#stock-tea")).toHaveValue("");
  await expect(page.locator("#proc-lead")).toHaveValue("");
  await page.locator("#proc-lead").fill("366");
  const calculate = page.getByRole("button", { name: "Рассчитать заказ" });
  await expect(calculate).toBeDisabled();
  await page.locator("#proc-lead").fill("2");
  await page.locator("#stock-tea").fill("1000000001");
  await expect(calculate).toBeDisabled();
  await page.locator("#stock-tea").fill("1000000000");
  await expect(calculate).toBeEnabled();
});

test("an edited order survives cancelled history navigation and recalculation", async ({
  page,
}) => {
  const plan = await startOrder(page);
  await page.locator("#order-tea").fill("60");
  await navigate(page, "История");
  const open = page.locator(`[data-plan-id="${plan.id}"] button`).first();
  page.once("dialog", (dialog) => dialog.dismiss());
  await open.click();
  await expect(open).toBeVisible();
  await navigate(page, "План");
  await expect(page.locator("#order-tea")).toHaveValue("60");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Рассчитать заказ" }).click();
  await expect(page.locator("#order-tea")).toHaveValue("60");
  await navigate(page, "История");
  page.once("dialog", (dialog) => dialog.accept());
  await open.click();
  await expect(page.locator("#order-tea")).toHaveValue(
    String(
      plan.items.find((i: { product_id: string }) => i.product_id === "tea")
        .recommended_qty,
    ),
  );
});

test("download from an open order uses its visible revision after another operator confirms a newer one", async ({
  page,
}) => {
  const draft = await startOrder(page);
  await page
    .getByRole("button", { name: "Подтвердить заказ", exact: true })
    .click();
  const link = page.getByRole("link", {
    name: "Экспорт заказа CSV",
    exact: true,
  });
  const savedRevision = draft.revision + 1;
  const url = `/api/plans/${draft.id}/export?revision=${savedRevision}`;
  await expect(link).toHaveAttribute("href", url);
  const expected = await (await page.request.get(url)).body();
  const changed = await page.request.post(`/api/plans/${draft.id}/confirm`, {
    data: {
      revision: savedRevision,
      idempotency_key: crypto.randomUUID(),
      quantities: { tea: 60, juice: 0 },
    },
  });
  expect(changed.ok()).toBeTruthy();
  expect(
    await (await page.request.get(`/api/plans/${draft.id}/export`)).body(),
  ).not.toEqual(expected);
  const download = page.waitForEvent("download");
  await link.click();
  expect(await readFile((await (await download).path())!)).toEqual(expected);
});
