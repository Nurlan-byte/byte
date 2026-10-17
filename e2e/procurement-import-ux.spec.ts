import { test, expect } from "@playwright/test";

test("file import uses explicit catalog fields and preserves an unspecified maximum", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await page.locator("#planning-kind").selectOption("procurement");
  await expect(page.locator("#proc-csv")).toBeHidden();
  await expect(page.locator("#proc-catalog")).toHaveCount(0);
  await expect(page.locator("#proc-pack-0")).toHaveValue("");
  await page.locator("#proc-file").setInputFiles({
    name: "synthetic-shop.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "date,product_id,opening,received,sold,waste,closing\n2026-08-01,tea,0,12,10,0,2\n",
    ),
  });
  await expect(page.locator("#proc-source")).toHaveValue("local");
  await expect(page.locator("#proc-shop")).toHaveValue("");
  await expect(page.locator("#proc-provenance")).toHaveValue("synthetic-shop.csv");
  await page.locator("#proc-product-0").fill("tea");
  await page.locator("#proc-name-0").fill("Synthetic tea");
  await page.locator("#proc-shop").fill("form-test");
  await page.locator("#proc-provenance").fill("Synthetic catalog form test");
  let requests = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/datasets") && request.method() === "POST")
      requests++;
  });
  await page.locator("#import > button:last-child").click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(requests).toBe(0);
  await expect(page.locator("#proc-name-0")).toHaveValue("Synthetic tea");
  await page.locator("#proc-pack-0").fill("6");
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    for (const id of [
      "proc-product-0",
      "proc-name-0",
      "proc-pack-0",
      "proc-max-0",
    ]) {
      const box = await page.locator(`#${id}`).boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    }
    await page.screenshot({
      path: testInfo.outputPath(`procurement-import-${width}.png`),
      fullPage: true,
    });
  }
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/api/datasets") && r.request().method() === "POST",
  );
  await page.locator("#import > button:last-child").click();
  const saved = await response;
  expect(saved.status()).toBe(201);
  expect(saved.request().postDataJSON().catalog).toEqual([
    {
      product_id: "tea",
      name: "Synthetic tea",
      pack_size: 6,
      max_order_qty: null,
    },
  ]);
  await expect(page.getByTestId("dataset-summary")).toBeVisible();
  await page.locator("#proc-manual-csv > summary").click();
  await expect(page.locator("#proc-csv")).toHaveValue(/2026-08-01,tea/);
});
