import type { Dataset, Item, Plan } from "./types";

export function forPlanningKind<T extends { planning_kind?: string }>(
  value: T | null,
  kind = "production",
): T | null {
  return value && (value.planning_kind ?? "production") === kind ? value : null;
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

// Store references only. Quantities and CSV drafts are never restored as saved data.
export function workspaceReference(
  key: "dataset" | "plan",
  value?: string | null,
) {
  try {
    const name = `bakeplan.owner.${key}`;
    if (value === undefined) return localStorage.getItem(name);
    if (value) localStorage.setItem(name, value);
    else localStorage.removeItem(name);
  } catch {
    /* Storage may be disabled; server history remains available. */
  }
  return null;
}

export function hasQuantityChanges(
  plan: Plan | null,
  quantities: Record<string, string>,
) {
  return !!plan?.items.some(
    (item) =>
      quantities[item.product_id] !==
      String(item.confirmed_qty ?? item.recommended_qty),
  );
}
