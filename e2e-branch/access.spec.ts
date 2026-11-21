import { expect, test, type Page } from "@playwright/test";
import { navigate, openDetails } from "../e2e/helpers";

test("an incomplete account response keeps saved orders closed until the administrator retries", async ({
  page,
  context,
}) => {
  await context.setExtraHTTPHeaders({
    Authorization:
      "Basic " + Buffer.from("admin-north:fixture-only").toString("base64"),
  });
  const workspaceRequests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/") && path !== "/api/session")
      workspaceRequests.push(path);
  });
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: { bakery_id: null } }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Не удалось проверить доступ к филиалу",
  );
  await expect(
    page.getByRole("navigation", { name: "Разделы BakePlan" }),
  ).toHaveCount(0);
  expect(workspaceRequests).toEqual([]);
  await page.unroute("**/api/session");
  await page.getByRole("button", { name: "Повторить" }).click();
  await expect(page.getByText("Филиал: north")).toBeVisible();
  await navigate(page, "Данные");
  await openDetails(page, "Параметры набора");
  const shop = page.getByLabel("Код точки", { exact: true });
  await expect(shop).toHaveValue("north");
  await expect(shop).not.toBeEditable();
  await page
    .getByLabel("Происхождение данных", { exact: true })
    .fill("Unsubmitted north draft");
  async function restoreTab() {
    await Promise.all([
      page.waitForResponse(
        (response) => response.url().endsWith("/api/session") && response.ok(),
      ),
      page.evaluate(() =>
        window.dispatchEvent(
          new PageTransitionEvent("pageshow", { persisted: true }),
        ),
      ),
    ]);
  }
  await restoreTab();
  await expect(
    page.getByLabel("Происхождение данных", { exact: true }),
  ).toHaveValue("Unsubmitted north draft");
  await context.setExtraHTTPHeaders({
    Authorization:
      "Basic " + Buffer.from("admin-south:fixture-only").toString("base64"),
  });
  await restoreTab();
  await expect(page.getByText("Филиал: south")).toBeVisible();
  await expect(page.getByText("Филиал: north")).toHaveCount(0);
  await navigate(page, "Данные");
  await openDetails(page, "Параметры набора");
  await expect(shop).toHaveValue("south");
  await expect(
    page.getByLabel("Происхождение данных", { exact: true }),
  ).not.toHaveValue("Unsubmitted north draft");
});

async function createOrder(page: Page, branch: string, teaUnits: number) {
  await page.getByLabel("Как пополняем товары").selectOption("procurement");
  await page
    .getByRole("button", { name: "Простой пример — 2 товара", exact: true })
    .click();
  const shop = page.getByLabel("Код точки", { exact: true });
  await expect(shop).toHaveValue(branch);
  await expect(shop).not.toBeEditable();
  await page
    .getByLabel("Происхождение данных закупки")
    .fill("Fixture " + branch);
  await page
    .getByRole("button", { name: "Проверить и импортировать закупку" })
    .click();
  await expect(page.getByTestId("dataset-summary")).toContainText(
    "Fixture " + branch,
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
    .getByLabel("Доступный остаток: Чай в бутылке", { exact: true })
    .fill("0");
  await page
    .getByRole("button", { name: "Рассчитать заказ", exact: true })
    .click();
  await page
    .getByLabel("Заказ: Чай в бутылке", { exact: true })
    .fill(String(teaUnits));
  await page.getByLabel("Заказ: Сок", { exact: true }).fill("0");
  await page
    .getByRole("button", { name: "Подтвердить заказ", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Заказ подтверждён и сохранён",
  );
  await navigate(page, "История");
  const card = page.locator(".history-card");
  await expect(card).toHaveCount(1);
  await expect(card).toHaveAttribute("data-status", "confirmed");
  const id = await card.getAttribute("data-plan-id");
  expect(id).toBeTruthy();
  const link = card.getByRole("link", { name: "Экспорт CSV" });
  const response = await page.request.get((await link.getAttribute("href"))!);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("Fixture " + branch);
  return id!;
}

test("each administrator imports, confirms, exports and restores only their branch on a shared browser", async ({
  browser,
  baseURL,
}) => {
  const credentials = (branch: string) => ({
    username: "admin-" + branch,
    password: "fixture-only",
  });
  const north = await browser.newContext({
    baseURL,
    httpCredentials: credentials("north"),
  });
  const northPage = await north.newPage();
  try {
    await northPage.goto("/");
    await expect(northPage.getByText("Филиал: north")).toBeVisible();
    const northOrder = await createOrder(northPage, "north", 30);

    // A switch of HTTP credentials on a shared machine preserves the previous browser storage.
    const south = await browser.newContext({
      baseURL,
      httpCredentials: credentials("south"),
      storageState: await north.storageState(),
    });
    try {
      const southPage = await south.newPage();
      await southPage.goto("/");
      await expect(southPage.getByText("Филиал: south")).toBeVisible();
      await expect(southPage.getByTestId("procurement-table")).toHaveCount(0);
      await navigate(southPage, "История");
      await expect(
        southPage.getByText("Сохранённых планов пока нет"),
      ).toBeVisible();
      const southOrder = await createOrder(southPage, "south", 36);
      expect(southOrder).not.toBe(northOrder);

      await southPage.reload();
      await expect(southPage.getByLabel("Заказ: Чай в бутылке")).toHaveValue(
        "36",
      );
      await navigate(southPage, "История");
      await expect(southPage.locator(".history-card")).toHaveCount(1);
      await expect(southPage.locator(".history-card")).toHaveAttribute(
        "data-plan-id",
        southOrder,
      );

      // A stale link under this account must be discarded, even when its order exists at another branch.
      await southPage.evaluate(
        ({ own, previous }) => {
          const key = Object.keys(localStorage).find(
            (key) => key.endsWith(".plan") && localStorage.getItem(key) === own,
          );
          if (!key) throw new Error("Missing current order reference");
          localStorage.setItem(key, previous);
        },
        { own: southOrder, previous: northOrder },
      );
      await southPage.reload();
      await expect(southPage.getByRole("alert")).toContainText(
        "Не удалось открыть прошлый набор или план",
      );
      await expect(southPage.getByTestId("procurement-table")).toHaveCount(0);
      await southPage.reload();
      await navigate(southPage, "История");
      await expect(southPage.locator(".history-card")).toHaveAttribute(
        "data-plan-id",
        southOrder,
      );
      await expect(southPage.getByRole("alert")).toHaveCount(0);
    } finally {
      await south.close();
    }

    await northPage.reload();
    await expect(northPage.getByLabel("Заказ: Чай в бутылке")).toHaveValue(
      "30",
    );
    await navigate(northPage, "История");
    await expect(northPage.locator(".history-card")).toHaveCount(1);
    await expect(northPage.locator(".history-card")).toHaveAttribute(
      "data-plan-id",
      northOrder,
    );
  } finally {
    await north.close();
  }
});
