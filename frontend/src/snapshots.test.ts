import { expect, test } from "vitest";
import { forecastInputsChanged, planInputsChanged } from "./snapshots";
import type { Plan } from "./types";

const current = {
  target: "2026-10-12",
  cutoff: "2026-10-10",
  method: "ML",
  targetOpen: true,
};
const plan: Plan = {
  id: "p",
  dataset_id: "d",
  target_date: current.target,
  cutoff: current.cutoff,
  status: "draft",
  source: "synthetic",
  revision: 0,
  requested_method: "ML",
  items: [
    {
      product_id: "p1",
      name: "Bread",
      forecast_mean: 12,
      recommended_qty: 12,
      confirmed_qty: null,
      batch_size: 12,
      max_daily_qty: 96,
      method: "B1",
      target_is_open: true,
      warnings: [],
    },
  ],
};

test("forecast identity includes requested method as well as dates", () => {
  const snapshot = {
    targetDate: current.target,
    cutoff: current.cutoff,
    method: current.method,
  };
  expect(forecastInputsChanged(snapshot, current)).toBe(false);
  expect(forecastInputsChanged(snapshot, { ...current, method: "B1" })).toBe(
    true,
  );
  expect(
    forecastInputsChanged(snapshot, { ...current, target: "2026-10-13" }),
  ).toBe(true);
  expect(
    forecastInputsChanged(snapshot, { ...current, cutoff: "2026-10-09" }),
  ).toBe(true);
});

test("plan compares requested method rather than per-product fallback", () => {
  expect(planInputsChanged(plan, current)).toBe(false);
  expect(planInputsChanged(plan, { ...current, method: "B1" })).toBe(true);
  expect(planInputsChanged(plan, { ...current, targetOpen: false })).toBe(true);
});

test("legacy snapshots do not invent missing requested method/calendar provenance", () => {
  const legacy = {
    ...plan,
    requested_method: undefined,
    items: plan.items.map((item) => ({ ...item, target_is_open: undefined })),
  };
  expect(planInputsChanged(legacy, current)).toBe(false);
  expect(planInputsChanged(legacy, { ...current, target: "2026-10-13" })).toBe(
    true,
  );
});
