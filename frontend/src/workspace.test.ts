import { afterEach, expect, test, vi } from "vitest";
import {
  hasQuantityChanges,
  productActiveOnTarget,
  savedDataset,
  workspaceReference,
} from "./workspace";
import type { Dataset, Plan } from "./types";

afterEach(() => vi.unstubAllGlobals());

test("blocked browser storage never prevents the server workflow", () => {
  vi.stubGlobal("localStorage", {
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("denied");
    },
    removeItem() {
      throw new Error("denied");
    },
  });
  expect(workspaceReference("dataset")).toBeNull();
  expect(() => workspaceReference("dataset", "saved-id")).not.toThrow();
  expect(() => workspaceReference("plan", null)).not.toThrow();
});

test("legacy metadata stays explicitly undeclared; no invented period or rows", () => {
  const legacy = savedDataset({ id: "old", source: "local" } as Dataset);
  expect(legacy.metadata_declared).toBe(false);
  expect(legacy.provenance).toContain("не указано");
  expect(legacy.rows).toBeUndefined();
  expect(legacy.row_count).toBeUndefined();
});

test("manual zero is a changed quantity when the saved recommendation is positive", () => {
  const plan = {
    items: [{ product_id: "p1", recommended_qty: 24, confirmed_qty: null }],
  } as Plan;
  expect(hasQuantityChanges(plan, { p1: "0" })).toBe(true);
  expect(hasQuantityChanges(plan, { p1: "24" })).toBe(false);
  const confirmed = {
    items: [{ ...plan.items[0], confirmed_qty: 84 }],
  } as Plan;
  expect(hasQuantityChanges(confirmed, { p1: "24" })).toBe(true);
  expect(hasQuantityChanges(confirmed, { p1: "84" })).toBe(false);
});

test("product activity dates include boundaries and exclude outside targets", () => {
  const item = {
    active_from: "2026-10-01",
    active_to: "2026-10-11",
  } as Plan["items"][number];
  expect(productActiveOnTarget(item, "2026-09-30")).toBe(false);
  expect(productActiveOnTarget(item, "2026-10-01")).toBe(true);
  expect(productActiveOnTarget(item, "2026-10-11")).toBe(true);
  expect(productActiveOnTarget(item, "2026-10-12")).toBe(false);
});
