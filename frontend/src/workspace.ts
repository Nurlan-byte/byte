import type { Dataset, Item, Plan } from "./types";

export function forPlanningKind<
  T extends { planning_kind?: string; forecast_only?: boolean },
>(value: T | null, kind = "production"): T | null {
  return value &&
    (value.forecast_only || (value.planning_kind ?? "production") === kind)
    ? value
    : null;
}

export function productActiveOnTarget(item: Item, targetDate: string) {
  return (
    (!item.active_from || targetDate >= item.active_from) &&
    (!item.active_to || targetDate <= item.active_to)
  );
}

export function savedDataset(result: Dataset): Dataset {
  return {
    ...result,
    bakery_id: result.bakery_id ?? "legacy",
    target: result.target ?? "sold_units",
    unit: result.unit ?? "physical_unit",
    provenance: result.provenance ?? "Старый импорт: происхождение не указано",
    metadata_declared: result.metadata_declared ?? false,
  };
}

export type WorkspaceSession =
  | { bakery_id: string; operator_id: string }
  | { bakery_id: null; operator_id: null };
export type WorkspaceKey = "dataset" | "plan" | "pos-mode" | "pos-connection";

// Store references only. Quantities and CSV drafts are never restored as saved data.
export function workspaceReference(
  key: WorkspaceKey,
  value?: string | null,
  session?: WorkspaceSession,
) {
  try {
    const scope = session?.bakery_id == null
      ? "owner"
      : `account.${JSON.stringify([session.operator_id, session.bakery_id])}`;
    const name = `bakeplan.${scope}.${key}`;
    if (value === undefined) return localStorage.getItem(name);
    if (value) localStorage.setItem(name, value);
    else localStorage.removeItem(name);
  } catch {
    /* Storage may be disabled; server history remains available. */
  }
  return null;
}

export function needsManualOrder(plan: Plan, item: Item) {
  return (
    (plan.dataset_schema_version === "pos-procurement-1" ||
      (plan.planning_kind === "procurement" && plan.requested_method === "AUTO")) &&
    item.target_is_open !== false &&
    item.automatic_eligible === false
  );
}

export function initialQuantity(plan: Plan, item: Item) {
  if (item.confirmed_qty != null) return String(item.confirmed_qty);
  return needsManualOrder(plan, item) ? "" : String(item.recommended_qty);
}

export function hasQuantityChanges(
  plan: Plan | null,
  quantities: Record<string, string>,
) {
  return !!plan?.items.some(
    (item) =>
      (quantities[item.product_id] ?? "") !== initialQuantity(plan, item),
  );
}
