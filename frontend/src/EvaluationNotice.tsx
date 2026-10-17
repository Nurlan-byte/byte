import type { EvaluationApplicability } from "./types";

export function EvaluationNotice({ value }: { value?: EvaluationApplicability }) {
  if (!value)
    return (
      <p className="muted">
        Качество прогноза для этих данных не проверено. Сверьте предложенное количество с продажами и остатком.
      </p>
    );
  return (
    <p
      className={value.applicable ? "muted" : "evaluation-warning"}
      data-testid="evaluation-applicability"
    >
      {value.applicable
        ? "Проверка качества относится к такому же сроку прогноза. Она не гарантирует точность будущих продаж."
        : value.reason === "forecast_horizon_mismatch"
          ? "Проверка качества относится к другому сроку. Проверьте предложенное количество вручную."
          : "Проверка качества не подходит для этого прогноза. Проверьте предложенное количество вручную."}
    </p>
  );
}
