import { MAX_QUANTITY, validQuantity } from "./quantities";
import type { Item } from "./types";

/** Check the entered order against the saved forecast and stock for its sale date. */
export function reviewOrderItem(
  item: Item,
  value: string,
  stock = item.usable_stock_assumption,
): { valid: boolean; reasons: string[] } {
  const pack = item.pack_size ?? 1;
  const maximum = item.max_order_qty;
  const quantity = Number(value);
  const valid = validQuantity(value, pack, maximum ?? MAX_QUANTITY);
  if (item.target_is_open === false) {
    return valid && quantity === 0
      ? { valid: true, reasons: [] }
      : { valid: false, reasons: ["В закрытый день укажите 0 шт."] };
  }

  const reasons: string[] = [];
  if (!valid) {
    const limit = maximum == null ? " Предел системы — 1 000 000 000 шт." : ` Максимум поставщика — ${maximum} шт.`;
    reasons.push(`Введите 0 или целое количество, кратное ${pack}.${limit}`);
  }
  if (stock == null)
    reasons.push("Укажите остаток на дату продаж и пересчитайте заказ.");
  if (item.forecast_mean == null) {
    reasons.push("Прогноз отсутствует. Проверьте количество вручную.");
  } else if (stock != null) {
    if (item.method === "manual" || item.automatic_eligible === false) {
      reasons.push(
        "Автоматический расчёт недоступен. Проверьте количество вручную.",
      );
    }
    if (valid) {
      const gap = Math.ceil(item.forecast_mean - stock - (item.incoming_units ?? 0) - quantity);
      if (gap > 0) {
        const atLimit = maximum != null && quantity + pack > maximum;
        const nextStep = atLimit
          ? "Лимит поставщика не позволяет добавить упаковку."
          : quantity + pack > MAX_QUANTITY
            ? "Достигнут предел количества в одном заказе."
            : "Проверьте количество заказа.";
        reasons.push(
          `До прогноза не хватает ${gap} шт. ${nextStep}`,
        );
      }
    }
  }
  return { valid, reasons };
}
