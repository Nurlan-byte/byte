import { MAX_QUANTITY, validQuantity } from "./quantities";
import type { Item, OrderConstraint } from "./types";

type OrderCoverage = {
  packs: number | null;
  shortage_units: number | null;
  constraint_reason: OrderConstraint | null;
};

/** Read saved decisions verbatim. Only edits and older snapshots need a local preview. */
function orderCoverage(
  item: Item,
  quantity: number,
  stock: number | null | undefined,
): OrderCoverage {
  if (
    quantity === item.confirmed_qty &&
    item.confirmed_packs != null &&
    item.confirmed_shortage_units !== undefined &&
    item.confirmed_constraint_reason !== undefined
  )
    return {
      packs: item.confirmed_packs,
      shortage_units: item.confirmed_shortage_units,
      constraint_reason: item.confirmed_constraint_reason,
    };
  if (
    quantity === item.recommended_qty &&
    item.recommended_packs !== undefined &&
    item.shortage_units !== undefined &&
    item.constraint_reason !== undefined
  )
    return {
      packs: item.recommended_packs,
      shortage_units: item.shortage_units,
      constraint_reason: item.constraint_reason,
    };

  const packs = item.pack_size == null ? null : quantity / item.pack_size;
  if (item.target_is_open === false)
    return { packs, shortage_units: 0, constraint_reason: null };
  if (item.forecast_mean == null || stock == null)
    return { packs, shortage_units: null, constraint_reason: null };
  const available = stock + (item.incoming_units ?? 0);
  const shortage = Math.max(
    0,
    Math.ceil(item.forecast_mean - available - quantity),
  );
  const supplierSetsLimit =
    item.max_order_qty != null && item.max_order_qty <= MAX_QUANTITY;
  const maximum = Math.min(item.max_order_qty ?? MAX_QUANTITY, MAX_QUANTITY);
  const capped = shortage > 0 && quantity + (item.pack_size ?? 1) > maximum;
  return {
    packs,
    shortage_units: shortage,
    constraint_reason: capped
      ? supplierSetsLimit
        ? "supplier_limit"
        : "system_limit"
      : null,
  };
}

/** Check the entered order against the saved forecast and stock for its sale date. */
export function reviewOrderItem(
  item: Item,
  value: string,
  stock = item.usable_stock_assumption,
) {
  const pack = item.pack_size ?? 1;
  const maximum = item.max_order_qty;
  const quantity = Number(value);
  const valid =
    validQuantity(value, pack, maximum ?? MAX_QUANTITY) &&
    (item.target_is_open !== false || quantity === 0);
  const coverage = valid ? orderCoverage(item, quantity, stock) : null;
  if (item.target_is_open === false) {
    return {
      valid,
      coverage,
      reasons: valid ? [] : ["В закрытый день укажите 0 шт."],
    };
  }

  const reasons: string[] = [];
  if (!valid) {
    const limit =
      maximum == null || maximum > MAX_QUANTITY
        ? " Предел системы — 1 000 000 000 шт."
        : ` Максимум поставщика — ${maximum} шт.`;
    reasons.push(`Введите 0 или целое количество, кратное ${pack}.${limit}`);
  }
  if (stock == null)
    reasons.push("Укажите остаток для автоматического расчёта или выберите количество заказа вручную.");
  if (item.forecast_mean == null) {
    reasons.push("Прогноз отсутствует. Проверьте количество вручную.");
  } else if (
    stock != null &&
    (item.method === "manual" || item.automatic_eligible === false)
  ) {
    reasons.push(
      "Автоматический расчёт недоступен. Проверьте количество вручную.",
    );
  }
  if (coverage?.shortage_units != null && coverage.shortage_units > 0) {
    const nextStep =
      coverage.constraint_reason === "supplier_limit"
        ? "Лимит поставщика не позволяет добавить упаковку."
        : coverage.constraint_reason === "system_limit"
          ? "Достигнут предел количества в одном заказе."
          : "Проверьте количество заказа.";
    reasons.push(
      `До прогноза не хватает ${coverage.shortage_units} шт. ${nextStep}`,
    );
  }
  return { valid, reasons, coverage };
}
