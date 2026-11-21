import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { SupplierOrderText } from "./SupplierOrderText";
import type { Item, Plan } from "./types";

const water: Item = {
  product_id: "water",
  name: "Вода",
  batch_size: 1,
  max_daily_qty: 200,
  forecast_mean: 21.8,
  recommended_qty: 24,
  confirmed_qty: 36,
  pack_size: 12,
  method: "B1",
  warnings: [],
};

const saved: Plan = {
  id: "saved",
  revision: 2,
  status: "confirmed",
  planning_kind: "procurement",
  dataset_id: "internal-dataset",
  source: "private CSV",
  provenance: "internal owner notes",
  cutoff: "2026-10-14",
  order_date: "2026-10-15",
  target_date: "2026-10-16",
  items: [water],
};

function render(overrides: Partial<Plan> = {}) {
  return renderToStaticMarkup(
    <SupplierOrderText plan={{ ...saved, ...overrides }} />,
  );
}

test("shows saved quantities, pack sizes and dates, omitting zero lines and internal data", () => {
  const html = render({
    items: [
      water,
      { ...water, product_id: "chips", name: "Чипсы", confirmed_qty: 0 },
    ],
  });
  expect(html).toContain("Дата заказа: 2026-10-15");
  expect(html).toContain("Дата продаж: 2026-10-16");
  expect(html).toContain("Вода — 36 шт. (3 уп. по 12 шт.)");
  expect(html).toContain("Скопировать заказ");
  expect(html).not.toMatch(
    /Чипсы|24 шт\.|21\.8|2026-10-14|internal|private CSV/,
  );
});

test("legacy confirmed lines with no pack size use units, and a missing order date stays unset", () => {
  const html = render({
    items: [{ ...water, pack_size: undefined }],
    order_date: undefined,
  });
  expect(html).toContain("Вода — 36 шт.");
  expect(html).not.toContain("уп. по");
  expect(html).not.toContain("Дата заказа:");
});

test("draft, production and legacy production plans have no supplier message", () => {
  expect(render({ status: "draft" })).toBe("");
  expect(render({ planning_kind: "production" })).toBe("");
  expect(render({ planning_kind: undefined })).toBe("");
});

test("all-zero orders have nothing to copy", () => {
  const html = render({ items: [{ ...water, confirmed_qty: 0 }] });
  expect(html).toContain("нет товаров к поставке");
  expect(html).not.toContain("Скопировать заказ");
});

test("a missing or invalid quantity blocks copying a partial order", () => {
  for (const quantity of [null, -12, 1.5, 13, Infinity, 108]) {
    const html = render({
      items: [
        water,
        {
          ...water,
          product_id: "second",
          confirmed_qty: quantity,
          max_order_qty: 100,
        },
      ],
    });
    expect(html).toContain("Проверьте сохранённые позиции");
    expect(html).not.toContain("Скопировать заказ");
    expect(html).not.toContain("Вода — 36 шт.");
  }
});
