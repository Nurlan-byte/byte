import type { CatalogItem } from "./types";

export function parseCatalog(text: string): CatalogItem[] {
  const value: unknown = JSON.parse(text);
  if (!Array.isArray(value) || !value.length)
    throw new Error("Добавьте хотя бы один товар в каталог.");
  const ids = new Set<string>();
  for (const item of value) {
    if (
      !item ||
      typeof item.product_id !== "string" ||
      !item.product_id.trim() ||
      typeof item.name !== "string" ||
      !item.name.trim() ||
      ids.has(item.product_id) ||
      !Number.isSafeInteger(item.pack_size) ||
      item.pack_size <= 0 ||
      (item.max_order_qty != null &&
        (!Number.isSafeInteger(item.max_order_qty) || item.max_order_qty < 0))
    ) {
      throw new Error(
        "Проверьте каталог: уникальный product_id, название, целая упаковка > 0, лимит — целое ≥ 0 или null.",
      );
    }
    ids.add(item.product_id);
    item.max_order_qty ??= null;
  }
  return value;
}

export function stockAssumptions(values: Record<string, string>) {
  const result: Record<string, number> = {};
  for (const [id, value] of Object.entries(values)) {
    if (!value.trim()) continue;
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))
      throw new Error(
        "Доступный остаток должен быть целым числом ≥ 0 или пустым.",
      );
    result[id] = Number(value);
  }
  return result;
}

export function syntheticProcurementCsv() {
  const rows = ["date,product_id,opening,received,sold,waste,closing"];
  for (let day = 1; day <= 84; day++) {
    const date = new Date(Date.UTC(2026, 5, day)).toISOString().slice(0, 10);
    rows.push(
      `${date},tea,${day === 1 ? "10,24" : "14,20"},20,0,14`,
      `${date},juice,8,12,12,0,8`,
    );
  }
  return rows.join("\n");
}
export const demoCatalog = JSON.stringify(
  [
    {
      product_id: "tea",
      name: "Чай в бутылке",
      pack_size: 6,
      max_order_qty: 120,
    },
    { product_id: "juice", name: "Сок", pack_size: 4, max_order_qty: null },
  ],
  null,
  2,
);
