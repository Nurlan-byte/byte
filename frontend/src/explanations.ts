import type { Item } from "./types";

const reasons: Record<string, string> = {
  not_better_than_b1_validation: "Проверка на прошлых данных не показала улучшения, поэтому используется расчёт по похожим дням.",
  forecast_horizon_mismatch: "Для этого срока экспериментальный прогноз не подходит; используется расчёт по похожим дням.",
  insufficient_history_dates: "Недостаточно истории продаж. Проверьте количество вручную.",
  insufficient_training_feature_rows: "Недостаточно подходящих дней для расчёта. Проверьте количество вручную.",
  no_holdout_pairs: "Недостаточно данных для проверки качества прогноза.",
  no_ready_products: "Для этих товаров пока нельзя подготовить экспериментальный расчёт.",
  estimator_failure: "Экспериментальный расчёт не удался. Проверьте предложенное количество.",
  no_b1_history: "Недостаточно истории продаж. Укажите количество вручную.",
  unsupported_protocol: "Сохранённый расчёт устарел. Создайте новый прогноз.",
  invalid_training_cutoff: "Выбранная дата истории недоступна. Проверьте дату.",
  model_not_ready: "Экспериментальный расчёт пока недоступен. Использован расчёт по похожим дням.",
  product_not_trained: "Для этого товара экспериментальный расчёт не подготовлен.",
  product_not_ready: "Для этого товара экспериментальный расчёт пока недоступен.",
  snapshot_mismatch: "Данные изменились. Создайте новый прогноз.",
  feature_out_of_bounds: "Для этих данных экспериментальный расчёт ненадёжен.",
  prediction_out_of_bounds: "Экспериментальный результат выходит за ожидаемые пределы.",
  nonfinite_prediction: "Экспериментальный расчёт не дал пригодного результата.",
  invalid_artifact: "Сохранённый экспериментальный расчёт недоступен.",
  model_too_old: "Экспериментальный расчёт устарел. Обновите его перед использованием.",
};

export function reasonText(reason: string) {
  return reasons[reason] ?? "Причину не удалось уточнить. Проверьте количество вручную.";
}

export function methodText(method: string) {
  const descriptions: Record<string, string> = {
    B0: "По прошлой неделе",
    B1: "По похожим дням недели",
    ML: "Экспериментальный расчёт",
    manual: "Нужно указать количество вручную",
  };
  return descriptions[method] ?? "Способ расчёта не указан";
}

export function procurementNotes(item: Item): string[] {
  const notes = [
    "Прогноз основан на наблюдаемых продажах; неудовлетворённый спрос неизвестен.",
    "Доступный остаток на день продажи задаётся вами и не выводится из прошлого вечернего остатка.",
  ];
  const unknownOpen = item.unknown_open_n ?? 0;
  const unknownStockout = item.unknown_stockout_n ?? 0;
  if (unknownOpen || unknownStockout)
    notes.push(
      `Доступность товара в прошлые дни не подтверждена: режим работы неизвестен в ${unknownOpen} строках, дефицит — в ${unknownStockout}.`,
    );
  if (item.target_is_open === false)
    notes.push("День отмечен как закрытый; заказать можно только 0.");
  if (item.usable_stock_assumption == null)
    notes.push("Для автоматического расчёта укажите доступный остаток на начало целевого дня.");
  if (item.n === 0)
    notes.push("Нет подходящей истории продаж: количество нужно указать вручную; 0 не является прогнозом.");
  if (item.max_constraint_active)
    notes.push("Максимум поставщика ниже расчётной потребности; часть спроса может остаться непокрытой.");
  if (item.fallback_reason)
    notes.push(`Экспериментальный прогноз не применён. ${reasonText(item.fallback_reason)}`);
  return notes;
}
