import { afterEach, expect, test, vi } from "vitest";
import {
  hasQuantityChanges,
  initialQuantity,
  productActiveOnTarget,
  savedDataset,
  workspaceReference,
} from "./workspace";
import type { Dataset, Plan } from "./types";

afterEach(() => vi.unstubAllGlobals());

test("last order and POS references belong to the signed-in account and branch", () => {
  const entries = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => entries.set(key, value),
    removeItem: (key: string) => entries.delete(key),
  });
  const first = { bakery_id: "north", operator_id: "admin" };
  const colleague = { bakery_id: "north", operator_id: "colleague" };
  const transferred = { bakery_id: "south", operator_id: "admin" };
  for (const key of ["plan", "dataset", "pos-mode", "pos-connection"] as const) {
    workspaceReference(key, "older-demo");
    expect(workspaceReference(key, undefined, first)).toBeNull();
    workspaceReference(key, "first-branch", first);
    expect(workspaceReference(key, undefined, first)).toBe("first-branch");
    expect(workspaceReference(key, undefined, colleague)).toBeNull();
    expect(workspaceReference(key, undefined, transferred)).toBeNull();
    expect(workspaceReference(key)).toBe("older-demo");
  }
});

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

test("an open POS order without an automatic quantity starts blank until the operator explicitly enters one", () => {
  const item = {
    product_id: "p",
    recommended_qty: 0,
    confirmed_qty: null,
    target_is_open: true,
    automatic_eligible: false,
  } as Plan["items"][number];
  const plan = {
    dataset_schema_version: "pos-procurement-1",
    items: [item],
  } as Plan;
  expect(initialQuantity(plan, item)).toBe("");
  expect(hasQuantityChanges(plan, {})).toBe(false);
  expect(hasQuantityChanges(plan, { p: "" })).toBe(false);
  expect(hasQuantityChanges(plan, { p: "0" })).toBe(true);
  expect(hasQuantityChanges(plan, { p: "6" })).toBe(true);

  for (const quantity of [0, 6]) {
    const savedItem = { ...item, confirmed_qty: quantity };
    const saved = { ...plan, items: [savedItem] };
    expect(initialQuantity(saved, savedItem)).toBe(String(quantity));
    expect(hasQuantityChanges(saved, { p: String(quantity) })).toBe(false);
    expect(hasQuantityChanges(saved, { p: "" })).toBe(true);
  }
  expect(initialQuantity(plan, { ...item, automatic_eligible: true })).toBe(
    "0",
  );
  expect(initialQuantity(plan, { ...item, target_is_open: false })).toBe("0");
  expect(
    initialQuantity({ ...plan, dataset_schema_version: undefined }, item),
  ).toBe("0");
  const auto: Plan = { ...plan, planning_kind: "procurement", dataset_schema_version: "procurement-1", requested_method: "AUTO" };
  expect(initialQuantity(auto, item)).toBe("");
  expect(initialQuantity(auto, { ...item, confirmed_qty: 0 })).toBe("0");
  expect(initialQuantity(auto, { ...item, target_is_open: false })).toBe("0");
});
