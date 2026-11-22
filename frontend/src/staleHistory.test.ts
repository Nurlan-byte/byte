import { expect, test } from "vitest";
import { procurementNotes, reasonText } from "./explanations";
import { reviewOrderItem } from "./orderReview";
import { initialQuantity } from "./workspace";
import type { Item, Plan } from "./types";

test("stale history leaves the order blank and explains why manual entries need review", () => {
  const item: Item = {
    name: "Вода", product_id: "old", method: "manual", forecast_mean: null,
    reason: "stale_sales_history", automatic_eligible: false, usable_stock_assumption: 0,
    pack_size: 24, recommended_qty: 0, confirmed_qty: null, batch_size: 1,
    max_daily_qty: 0, warnings: [],
  };
  const plan = { planning_kind: "procurement", requested_method: "AUTO" } as Plan;
  const reason = reasonText("stale_sales_history");
  expect(reason).toContain("56 дней");
  expect(initialQuantity(plan, item)).toBe("");
  expect(reviewOrderItem(item, "").valid).toBe(false);
  expect(procurementNotes(item)).toContain(reason);
  expect(reviewOrderItem(item, "24")).toMatchObject({ valid: true, reasons: [reason] });
  expect(initialQuantity(plan, { ...item, confirmed_qty: 0 })).toBe("0");
});
