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
  procurement = false,
  snapshot,
  stale,
  unitLabel,
}: {
  procurement?: boolean;
  snapshot: ForecastSnapshot;
  stale: boolean;
  unitLabel: string;
}) {
  const items = snapshot.result.items;
  return (
    <div
      data-testid="forecast-result"
      className={`forecast-result${stale ? " previous-result" : ""}`}
    >
      <div className="section-heading">
        <h3>Прогноз по истории продаж на {snapshot.targetDate}</h3>
        <span className="badge">
          {procurement ? "Для проверки заказа" : "Для проверки плана"}
        </span>
      </div>
      {stale && (
        <aside data-testid="stale-forecast" className="stale-result">
          Параметры изменились. Пересчитайте прогноз перед использованием.
        </aside>
      )}
      <p>Продажи учтены по {snapshot.cutoff}. Проверьте результат по каждому товару.</p>
      <EvaluationNotice value={snapshot.result.evaluation_applicability} />
      {procurement && (
        <p className="status-note">
          Прогноз продаж не является заказом. Проверьте остаток, упаковку и срок поставки.
        </p>
      )}
      {!items.length && (
        <p className="status-note">
          Для этих товаров нет пригодного прогноза. Укажите количество вручную.
        </p>
      )}
      <div className="table">
        <table data-testid="forecast-table">
          <thead>
            <tr>
              <th scope="col">Товар</th>
              <th scope="col">Прогноз продаж, {unitLabel}</th>
              <th scope="col">Что учесть</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.product_id}>
                <td>{item.product_id}</td>
                <td>
                  {item.forecast_mean == null
                    ? "Нет оценки"
                    : item.forecast_mean.toFixed(1)}
                </td>
                <td>
                  <strong>{methodText(item.method)}</strong>
                  {item.fallback_reason && (
                    <small className="unsaved">
                      Использован упрощённый прогноз. {reasonText(item.fallback_reason)}
                    </small>
                  )}
                  {item.evaluation_applicability &&
                    !item.evaluation_applicability.applicable && (
                      <EvaluationNotice value={item.evaluation_applicability} />
                    )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
