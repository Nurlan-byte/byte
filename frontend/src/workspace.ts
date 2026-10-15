import type { Dataset, Plan } from "./types";

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
