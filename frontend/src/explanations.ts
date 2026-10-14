const reasons: Record<string, string> = {
  insufficient_history_dates: "Недостаточно дат истории для временной проверки",
  insufficient_training_feature_rows:
    "Недостаточно пригодных строк с признаками",
  no_holdout_pairs: "Нет сопоставимых наблюдений на отложенном периоде",
  no_ready_products: "Нет товаров с готовой моделью",
  estimator_failure: "Не удалось обучить модель",
  no_b1_history: "Нет подходящей истории даже для B1; нужен ручной план",
  unsupported_protocol:
    "Версия протокола модели не поддерживается; обучите модель заново",
  invalid_training_cutoff: "Срез обучения модели не подтверждён",
  model_not_ready: "Модель не готова; используется доступная история B1",
  product_not_trained: "Для этого товара модель не обучена",
  product_not_ready: "Модель этого товара не готова",
  snapshot_mismatch: "Снимок данных модели не совпадает с историей",
  feature_out_of_bounds: "Признаки выходят за допустимые границы ML",
  prediction_out_of_bounds: "Прогноз ML выходит за допустимые границы",
  nonfinite_prediction: "ML не вернул допустимое число",
  invalid_artifact: "Сохранённая модель повреждена или неприменима",
  model_too_old:
    "Модель обучена более 14 дней назад относительно среза; её метрики к этому прогнозу не относятся. Обучите модель заново",
};

export function reasonText(reason: string) {
  return reasons[reason] ?? reason;
}

export function methodText(method: string) {
  const descriptions: Record<string, string> = {
    B0: "B0 · тот же день неделю назад",
    B1: "B1 · среднее до 4 таких дней недели",
    ML: "ML · B1 с поправкой локальной модели",
    manual: "Ручной план · автоматический выпуск отключён",
  };
  return descriptions[method] ?? method;
}
