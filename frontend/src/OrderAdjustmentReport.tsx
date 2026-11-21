import { useState } from "react";
import { api } from "./api";
import { businessDate } from "./dates";
import { overrideReasonLabels, type OverrideReason } from "./types";

type Report = {
  confirmed_product_days: number;
  comparable_product_days: number;
  adjusted_product_days: number;
  adjustment_rate: number | null;
  reason_counts: Partial<Record<OverrideReason | "unspecified", number>>;
  matched_sales_days: number;
  sales_mae_units: number | null;
  sales_bias_units: number | null;
};

export function OrderAdjustmentReport({
  branch,
  source,
  datasetId,
}: {
  branch: string;
  source: string;
  datasetId?: string;
}) {
  const [start, setStart] = useState(() => businessDate(-29));
  const [end, setEnd] = useState(() => businessDate());
  const [mode, setMode] = useState("operational");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState<{ query: string; data: Report } | null>(
    null,
  );
  const params = new URLSearchParams({
    bakery_id: branch,
    source,
    start,
    end,
    mode,
  });
  if (datasetId) params.set("actual_dataset_id", datasetId);
  const query = params.toString();
  const report = loaded?.query === query ? loaded.data : null;
  return (
    <details className="quantity-help">
      <summary>Правки заказов и точность прогноза</summary>
      <p>
        Филиал {branch}.{" "}
        {source === "synthetic"
          ? "Демо-данные."
          : source === "external"
            ? "Внешний источник."
            : "Данные филиала."}
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError("");
          setLoaded(null);
          try {
            const data = await api<Report>(
              `/reports/order-adjustments?${query}`,
            );
            setLoaded({ query, data });
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Продажи с{" "}
          <input
            aria-label="Начало отчёта"
            type="date"
            required
            value={start}
            max={end || undefined}
            disabled={busy}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          по{" "}
          <input
            aria-label="Конец отчёта"
            type="date"
            required
            value={end}
            min={start || undefined}
            disabled={busy}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <label>
          Заказы отчёта{" "}
          <select
            value={mode}
            disabled={busy}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="operational">Рабочие</option>
            <option value="replay">Историческое воспроизведение</option>
          </select>
        </label>
        <button className="secondary" disabled={busy}>
          {busy ? "Загрузка отчёта…" : "Показать отчёт"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {report && (
        <div role="status">
          {report.adjustment_rate === null ? (
            <p>
              В этом периоде нет подтверждённых позиций с предложенным
              количеством.
            </p>
          ) : (
            <p>
              <strong>
                Изменены на упаковку или больше: {report.adjusted_product_days}{" "}
                из {report.comparable_product_days} (
                {(100 * report.adjustment_rate).toLocaleString("ru-RU", {
                  maximumFractionDigits: 1,
                })}
                %).
              </strong>
            </p>
          )}
          <p>
            По каждому товару и дню учитывается последнее подтверждение. Всего
            позиций: {report.confirmed_product_days}. Дни закрытия и позиции без
            рекомендации исключены из доли правок.
          </p>
          {Object.entries(report.reason_counts).map(([reason, count]) => (
            <p key={reason}>
              {reason === "unspecified"
                ? "Причина не указана"
                : overrideReasonLabels[reason as OverrideReason]}{" "}
              — {count}
            </p>
          ))}
          {datasetId &&
            (report.sales_mae_units === null ? (
              <p>
                В выбранном наборе нет сопоставимых завершённых дней с
                продажами.
              </p>
            ) : (
              <p>
                Сверено с продажами из выбранного набора:{" "}
                {report.matched_sales_days}. Средняя абсолютная ошибка —{" "}
                {report.sales_mae_units.toLocaleString("ru-RU", {
                  maximumFractionDigits: 2,
                })}{" "}
                шт. Среднее отклонение прогноза от продаж —{" "}
                {report.sales_bias_units?.toLocaleString("ru-RU", {
                  maximumFractionDigits: 2,
                })}{" "}
                шт.
              </p>
            ))}
        </div>
      )}
    </details>
  );
}
