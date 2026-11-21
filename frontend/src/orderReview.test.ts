import { expect, test } from "vitest";
import { reviewOrderItem } from "./orderReview";
import type { Item } from "./types";
import { stockAssumptions } from "./procurement";
import { MAX_QUANTITY } from "./quantities";
import orderCases from "../../fixtures/procurement_order_cases.json";

test("stock and uncapped order quantities enforce the system limit without inventing a supplier cap", () => {
  const large = {
    ...item,
    forecast_mean: MAX_QUANTITY + 1,
    usable_stock_assumption: 0,
    pack_size: 1,
    max_order_qty: null,
  };
  expect(stockAssumptions({ sku: String(MAX_QUANTITY) })).toEqual({
    sku: MAX_QUANTITY,
  });
  expect(() => stockAssumptions({ sku: String(MAX_QUANTITY + 1) })).toThrow(
    /1 000 000 000/,
  );
  expect(reviewOrderItem(large, String(MAX_QUANTITY + 1))).toMatchObject({
    valid: false,
  });
  expect(
    reviewOrderItem(large, String(MAX_QUANTITY)).reasons.join(" "),
  ).toContain("предел количества");
  expect(
    reviewOrderItem(large, String(MAX_QUANTITY)).reasons.join(" "),
  ).not.toContain("поставщика");
});

const item: Item = {
  name: "Вода",
  product_id: "water",
  method: "B1",
  forecast_mean: 14.2,
  usable_stock_assumption: 3,
  pack_size: 6,
  max_order_qty: 20,
  recommended_qty: 12,
  confirmed_qty: null,
  batch_size: 1,
  max_daily_qty: 0,
  warnings: [],
};

test("covered orders remain clear despite a model fallback or unknown historical stockouts", () => {
  expect(
    reviewOrderItem(
      { ...item, fallback_reason: "product_not_ready", unknown_stockout_n: 26 },
      "12",
    ),
  ).toEqual({
    valid: true,
    reasons: [],
    coverage: { packs: 2, shortage_units: 0, constraint_reason: null },
  });
});

test("an unknown stock estimate differs from explicit zero or historical plan-level stock", () => {
  const unknown = { ...item, usable_stock_assumption: null };
  expect(reviewOrderItem(unknown, "18").reasons.join(" ")).toContain(
    "Укажите остаток",
  );
  expect(reviewOrderItem(unknown, "18", 0).reasons).toEqual([]);
  expect(reviewOrderItem(unknown, "12", 0).reasons.join(" ")).toContain(
    "3 шт.",
  );
});

test("a manual forecast needs review while closed dates only permit a zero order", () => {
  expect(
    reviewOrderItem({ ...item, forecast_mean: null }, "0").reasons.join(" "),
  ).toContain("Прогноз отсутствует");
  expect(
    reviewOrderItem({ ...item, method: "manual" }, "12").reasons.join(" "),
  ).toContain("вручную");
  const closed = {
    ...item,
    target_is_open: false,
    usable_stock_assumption: null,
    forecast_mean: null,
  };
  expect(reviewOrderItem(closed, "0")).toEqual({
    valid: true,
    reasons: [],
    coverage: { packs: 0, shortage_units: 0, constraint_reason: null },
  });
  expect(reviewOrderItem(closed, "6")).toEqual({
    valid: false,
    reasons: ["В закрытый день укажите 0 шт."],
    coverage: null,
  });
});

test.each(orderCases)(
  "$name matches the API for an unsaved quantity or a legacy plan",
  ({ input, expected, edit }) => {
    const line = {
      ...item,
      ...input,
      recommended_qty: expected.recommended_qty,
      automatic_eligible: expected.automatic_eligible,
    };
    expect(
      reviewOrderItem(line, String(expected.recommended_qty)),
    ).toMatchObject({
      valid: true,
      coverage: {
        packs: expected.recommended_packs,
        shortage_units: expected.shortage_units,
        constraint_reason: expected.constraint_reason,
      },
    });
    if (edit) {
      expect(reviewOrderItem(line, String(edit.quantity))).toMatchObject({
        valid: true,
        coverage: {
          packs: edit.quantity / input.pack_size,
          shortage_units: edit.shortage_units,
          constraint_reason: edit.constraint_reason,
        },
      });
    }
  },
);

test("saved explanations stay tied to their quantities; manual edits get a fresh preview", () => {
  const saved: Item = {
    ...item,
    recommended_qty: 18,
    recommended_packs: 3,
    shortage_units: 7,
    constraint_reason: "supplier_limit",
    confirmed_qty: 6,
    confirmed_packs: 1,
    confirmed_shortage_units: 19,
    confirmed_constraint_reason: null,
  };
  expect(reviewOrderItem(saved, "18").reasons).toEqual([
    "До прогноза не хватает 7 шт. Лимит поставщика не позволяет добавить упаковку.",
  ]);
  expect(reviewOrderItem(saved, "6").reasons).toEqual([
    "До прогноза не хватает 19 шт. Проверьте количество заказа.",
  ]);
  expect(reviewOrderItem(saved, "12").coverage).toEqual({
    packs: 2,
    shortage_units: 0,
    constraint_reason: null,
  });
});

test("a historical supplier limit above the system ceiling leaves the system as the constraint", () => {
  const older = {
    ...item,
    forecast_mean: MAX_QUANTITY,
    pack_size: 600_000_000,
    usable_stock_assumption: 0,
  };
  for (const [limit, reason] of [
    [MAX_QUANTITY + 100, "system_limit"],
    [MAX_QUANTITY, "supplier_limit"],
  ] as const) {
    const review = reviewOrderItem(
      { ...older, max_order_qty: limit },
      "600000000",
    );
    expect(review.coverage).toEqual({
      packs: 1,
      shortage_units: 400_000_000,
      constraint_reason: reason,
    });
  }
});

test("the gap follows the entered valid order; supplier limit applies at the largest allowed pack multiple", () => {
  const capped = { ...item, forecast_mean: 23.1, max_constraint_active: true };
  expect(reviewOrderItem(capped, "6").reasons).toEqual([
    "До прогноза не хватает 15 шт. Проверьте количество заказа.",
  ]);
  expect(reviewOrderItem(capped, "18").reasons).toEqual([
    "До прогноза не хватает 3 шт. Лимит поставщика не позволяет добавить упаковку.",
  ]);
  expect(
    reviewOrderItem({ ...capped, max_order_qty: null }, "24").reasons,
  ).toEqual([]);
  expect(
    reviewOrderItem({ ...capped, max_order_qty: 5 }, "0").reasons.join(" "),
  ).toContain("Лимит поставщика");
});

test("an empty, negative, fractional, non-pack or over-limit edit is invalid and gets no calculated shortfall", () => {
  for (const value of ["", "-1", "4.2", "4", "24"]) {
    const review = reviewOrderItem(item, value);
    expect(review.valid).toBe(false);
    expect(review.reasons).toHaveLength(1);
    expect(review.reasons[0]).toContain("кратное 6");
  }
});
