import type { Forecast } from "./types";
import { methodText, reasonText } from "./explanations";
import { EvaluationNotice } from "./EvaluationNotice";

export type ForecastSnapshot = {
  result: Forecast;
  cutoff: string;
  targetDate: string;
  method: string;
};

export function ForecastResult({
  snapshot,
  stale,
  unitLabel,
}: {
  snapshot: ForecastSnapshot;
  stale: boolean;
  unitLabel: string;
}) {
  return (
    <div
      data-testid="forecast-result"
      className={stale ? "previous-result" : undefined}
    >
      <h3>Диагностический прогноз на {snapshot.targetDate}</h3>
      {stale && (
        <aside data-testid="stale-forecast" className="stale-result">
          Параметры изменены. Это прежний прогноз на {snapshot.targetDate}, срез{" "}
          {snapshot.cutoff}, выбранный метод {snapshot.method}. Нажмите
          «Получить прогноз» для новых параметров.
        </aside>
      )}
      <p>
        Срез: {snapshot.cutoff} · выбранный метод: {snapshot.method} · единица:{" "}
        {unitLabel}.
      </p>
      <EvaluationNotice value={snapshot.result.evaluation_applicability} />
      <details>
        <summary>Технические данные прогноза</summary>
        <p>
          Набор: {snapshot.result.dataset_id} · пекарня:{" "}
          {snapshot.result.bakery_id}
        </p>
        <p>
          Цель: {snapshot.result.target} · единица: {snapshot.result.unit} ·
          снимок модели: {snapshot.result.model_id ?? "не использован"}
        </p>
        {snapshot.result.evaluation_applicability?.reason && (
          <p>
            Причина неприменимости оценки:{" "}
            {snapshot.result.evaluation_applicability.reason}
          </p>
        )}
      </details>
      <div className="table">
        <table data-testid="forecast-table">
          <thead>
            <tr>
              <th>Товар</th>
              <th>Прогноз, {unitLabel}</th>
              <th>B1, {unitLabel}</th>
              <th>Фактический метод и ограничения</th>
            </tr>
          </thead>
          <tbody>
            {snapshot.result.items.map((item) => (
              <tr key={item.product_id}>
                <td>{item.product_id}</td>
                <td>
                  {item.forecast_mean == null
                    ? "Нет оценки"
                    : item.forecast_mean.toFixed(1)}
                </td>
                <td>
                  {item.b1_mean == null
                    ? "Нет оценки"
                    : item.b1_mean.toFixed(1)}
                </td>
                <td>
                  <strong>{methodText(item.method)}</strong>
                  {item.fallback_reason && (
                    <small>
                      Причина перехода: {reasonText(item.fallback_reason)}
                    </small>
                  )}
                  <details className="item-details">
                    <summary>Источники прогноза</summary>
                    <small>
                      Подходящих наблюдений:{" "}
                      {item.n ?? item.used_dates?.length ?? 0}
                    </small>
                    {!!item.used_dates?.length && (
                      <small>Даты: {item.used_dates.join(", ")}</small>
                    )}
                    {item.fallback_reason && (
                      <small>
                        Код или исходная причина: {item.fallback_reason}
                      </small>
                    )}
                    <EvaluationNotice value={item.evaluation_applicability} />
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
