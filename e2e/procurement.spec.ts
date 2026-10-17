import { test, expect } from "@playwright/test";

test("real API preserves generic procurement import, order and export", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Как пополняем товары").selectOption("procurement");
  await page.getByRole("button", { name: "Демо закупки", exact: true }).click();
  await page
    .getByLabel("Происхождение данных закупки")
    .fill("Synthetic procurement integration fixture");
  await page
    .getByRole("button", { name: "Проверить и импортировать закупку" })
    .click();
  await expect(page.getByTestId("dataset-summary")).toBeVisible();
  await page
    .getByRole("button", { name: "Перейти к плану", exact: true })
    .click();
  await expect(page.getByLabel("Срок поставки, дней")).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "Рассчитать заказ", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Дата продаж", { exact: true }).fill("2026-09-01");
  await page
    .getByLabel("Данные доступны до", { exact: true })
    .fill("2026-08-23");
  await page.getByLabel("Срок поставки, дней").fill("2");
  await page
    .getByLabel("Доступный остаток: Чай в бутылке", { exact: true })
    .fill("0");
  await page
    .getByRole("button", { name: "Рассчитать заказ", exact: true })
    .click();
  await expect(
    page.getByLabel("Заказ: Чай в бутылке", { exact: true }),
  ).toHaveValue("24");
  await expect(page.getByLabel("Заказ: Сок", { exact: true })).toHaveValue("0");
  await page
    .getByRole("button", { name: "Подтвердить заказ", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Заказ подтверждён и сохранён",
  );
  const href = await page
    .getByRole("link", { name: "Экспорт заказа CSV" })
    .getAttribute("href");
  const exported = await page.request.get(href!);
  expect(exported.ok()).toBeTruthy();
  expect(await exported.text()).toContain("forecast_sales_units");
  expect(await exported.text()).toContain(
    "Synthetic procurement integration fixture",
  );
});

test("saved procurement order can be confirmed while another dataset is selected", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#planning-kind").selectOption("procurement");
  await page.getByRole("button", { name: "Демо закупки", exact: true }).click();
  await page.locator("#proc-shop").fill("synthetic-shop-a");
  await page.locator("#proc-provenance").fill("Synthetic source A");
  await page.locator("#panel-import > section > button:last-child").click();
  await expect(page.getByTestId("dataset-summary")).toContainText(
    "Synthetic source A",
  );
  await page.locator("button[aria-controls='panel-planning']").click();
  await page.locator("#proc-date").fill("2026-09-01");
  await page.locator("#proc-cutoff").fill("2026-08-23");
  await page.locator("#proc-lead").fill("2");
  await page.locator("#stock-tea").fill("0");
  const planResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/plans") &&
      response.request().method() === "POST",
  );
  await page.locator("#planning > button").click();
  const savedPlan = await (await planResponse).json();
  await expect(page.getByTestId("procurement-table")).toBeVisible();

  await page.locator("button[aria-controls='panel-import']").click();
  await page.locator("#proc-shop").fill("synthetic-shop-b");
  await page.locator("#proc-provenance").fill("Synthetic source B");
  await page.locator("#panel-import > section > button:last-child").click();
  await expect(page.getByTestId("dataset-summary")).toContainText(
    "Synthetic source B",
  );
  await page.locator("button[aria-controls='panel-history']").click();
  await page.locator(`[data-plan-id='${savedPlan.id}'] button`).first().click();

  await expect(
    page.getByTestId("saved-procurement-dataset-notice"),
  ).toBeVisible();
  await expect(page.getByTestId("stale-plan")).toHaveCount(0);
  await expect(page.locator("#stock-tea")).toHaveValue("");
  await expect(page.locator("#order-tea")).toBeEnabled();
  await expect(page.locator("#planning .actions button")).toBeEnabled();
  await page.locator("#planning .actions button").click();
  await expect(page.getByRole("status")).toBeVisible();
  await page.locator("button[aria-controls='panel-import']").click();
  await expect(page.getByTestId("dataset-summary")).toContainText(
    "Synthetic source B",
  );
});

test("procurement import error focuses alert and preserves editable draft", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("#planning-kind")).toBeVisible();
  await page.route("**/api/datasets", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 422,
      json: { detail: "Invalid inventory balance" },
    });
  });
  await page.getByLabel("Как пополняем товары").selectOption("procurement");
  await page.getByRole("button", { name: "Демо закупки", exact: true }).click();
  await page
    .getByRole("button", { name: "Проверить и импортировать закупку" })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Invalid inventory balance",
  );
  await expect(page.getByRole("alert")).toBeFocused();
  await expect(page.getByLabel("Содержимое CSV закупки")).toContainText(
    "received",
  );
  await expect(
    page.getByRole("button", { name: "Проверить и импортировать закупку" }),
  ).toBeEnabled();
});

for (const width of [360, 768, 1440]) {
  test(`generic procurement workflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let dataset: any = null;
    let plan: any = null;
    let lastRequest: any;
    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      let body: any = [];
      if (req.method() === "POST" && url.pathname === "/api/datasets") {
        const input = req.postDataJSON();
        expect(input.schema_version).toBe("procurement-1");
        expect(input.csv_text).toContain("received");
        dataset = {
          ...input,
          id: "synthetic-procurement",
          planning_kind: "procurement",
          row_count: 168,
        };
        body = dataset;
      } else if (req.method() === "POST" && url.pathname === "/api/plans") {
        lastRequest = req.postDataJSON();
        plan = {
          ...lastRequest,
          id: "procurement-plan",
          planning_kind: "procurement",
          order_date: "2026-08-30",
          source: "synthetic",
          requested_method: lastRequest.method,
          status: "draft",
          revision: 0,
          items: dataset.catalog.map((i: any) => ({
            ...i,
            forecast_mean: 20,
            recommended_qty:
              lastRequest.usable_stock_assumption[i.product_id] === undefined
                ? 0
                : 24,
            confirmed_qty: null,
            method: "B1",
            warnings:
              lastRequest.usable_stock_assumption[i.product_id] === undefined
                ? ["Остаток неизвестен: ручное решение"]
                : [],
          })),
        };
        body = plan;
      } else if (req.method() === "POST" && url.pathname.endsWith("/confirm")) {
        const input = req.postDataJSON();
        plan = {
          ...plan,
          status: "confirmed",
          revision: 1,
          items: plan.items.map((i: any) => ({
            ...i,
            confirmed_qty: input.quantities[i.product_id],
          })),
        };
        body = plan;
      } else if (url.pathname === "/api/plans") body = plan ? [plan] : [];
      else if (url.pathname.endsWith("/procurement-plan")) body = plan;
      else if (url.pathname.endsWith("/synthetic-procurement")) body = dataset;
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await page.getByLabel("Как пополняем товары").selectOption("procurement");
    await page
      .getByRole("button", { name: "Демо закупки", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Проверить и импортировать закупку" })
      .click();
    await expect(page.getByTestId("dataset-summary")).toContainText(
      "Синтетический пример",
    );
    await page
      .getByRole("button", { name: "Перейти к плану", exact: true })
      .click();
    await page.getByLabel("Дата продаж", { exact: true }).fill("2026-09-01");
    await page
      .getByLabel("Данные доступны до", { exact: true })
      .fill("2026-08-23");
    await page.getByLabel("Срок поставки, дней").fill("2");
    await page
      .getByRole("button", { name: "Рассчитать заказ", exact: true })
      .click();
    await expect(page.getByTestId("procurement-table")).toContainText(
      "Для автоматического расчёта укажите доступный остаток",
    );
    expect(lastRequest.usable_stock_assumption).toEqual({});
    await page
      .getByLabel("Доступный остаток: Чай в бутылке", { exact: true })
      .fill("0");
    await expect(
      page.getByRole("button", { name: "Подтвердить заказ", exact: true }),
    ).toBeDisabled();
    await page
      .getByRole("button", { name: "Рассчитать заказ", exact: true })
      .click();
    expect(lastRequest.usable_stock_assumption).toEqual({ tea: 0 });
    await page.getByLabel("Заказ: Чай в бутылке", { exact: true }).fill("7");
    await expect(
      page.getByRole("button", { name: "Подтвердить заказ", exact: true }),
    ).toBeDisabled();
    await page.getByLabel("Заказ: Чай в бутылке", { exact: true }).fill("30");
    await page.getByLabel("Как пополняем товары").selectOption("production");
    await expect(page.getByTestId("mode-selection-notice")).toContainText(
      "несохранённые изменения",
    );
    await expect(
      page.getByRole("button", {
        name: "Проверить и импортировать",
        exact: true,
      }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "План", exact: true }).click();
    await expect(page.getByTestId("plan-table")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Рассчитать план", exact: true }),
    ).toHaveCount(0);
    await page.getByText("Дополнительно", { exact: true }).click();
    await page.getByRole("button", { name: "Качество прогноза", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Для проверки прогноза нужны данные" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Вернуться к несохранённому плану" })
      .click();
    await expect(
      page.getByLabel("Заказ: Чай в бутылке", { exact: true }),
    ).toHaveValue("30");
    await expect(
      page.getByText("Есть несохранённые изменения заказа.", { exact: false }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Подтвердить заказ", exact: true })
      .click();
    await expect(page.getByRole("status")).toContainText(
      "Заказ подтверждён и сохранён",
    );
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "План закупки", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel("Заказ: Чай в бутылке", { exact: true }),
    ).toHaveValue("30");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await expect(page.locator("#panel-planning")).not.toContainText(
      /выпеч|выпека|выпуск/i,
    );
  });
}
