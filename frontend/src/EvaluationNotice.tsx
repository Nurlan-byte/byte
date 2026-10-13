import type { EvaluationApplicability } from "./types";

export function EvaluationNotice({
  value,
}: {
  value?: EvaluationApplicability;
}) {
  if (!value)
    return (
      <p className="muted">
        Применимость оценки к этому прогнозу не указана. Не переносите метрики
        модели на него автоматически.
      </p>
    );
  return (
    <p
      className={value.applicable ? "muted" : "evaluation-warning"}
      data-testid="evaluation-applicability"
    >
      {value.applicable
        ? `Горизонт совпадает с оценкой: ${value.requested_horizon_days} дн. между срезом и прогнозом. Это совместимость протокола, а не гарантия точности.`
        : value.reason === "forecast_horizon_mismatch"
          ? `Метрики модели не относятся к выбранному горизонту: оценка ${value.evaluated_horizon_days} дн., прогноз ${value.requested_horizon_days} дн. ML может использоваться, но его качество на этом горизонте здесь не измерено.`
          : "Метрики модели неприменимы к этому прогнозу. Проверьте фактический метод и причину перехода для каждого товара."}
    </p>
  );
}
