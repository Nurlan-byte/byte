import { useState } from "react";
import { api } from "./api";
import { businessDate } from "./dates";
import type { paths } from "./generated/order-api";
import type { Dataset } from "./types";

type Report = paths["/api/reports/issued-forecasts"]["get"]["responses"][200]["content"]["application/json"];

const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const format = (value: number | null) => value === null ? "—" : number.format(value);
const exclusions = {
  pending: "День ещё не завершён",
  missing_actual: "Нет факта продаж",
  closed: "Филиал закрыт",
  stockout: "Товар закончился",
  missing_pair: "Нет пары прогнозов",
} as const;

export function IssuedForecastReport({ branch, initialSource, dataset }: {
  branch: string;
  initialSource: string;
  dataset: Dataset | null;
}) {
  const [start, setStart] = useState(() => businessDate(-27));
  const [end, setEnd] = useState(() => businessDate());
  const [horizon, setHorizon] = useState("2");
  const [source, setSource] = useState(initialSource);
  const [unit, setUnit] = useState(dataset?.unit ?? "physical_unit");
  const [useSelected, setUseSelected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState<{query: string; data?: Report; error?: string} | null>(null);
  const selected = dataset?.bakery_id === branch && dataset.source === source && dataset.unit === unit;
  const params = new URLSearchParams({ bakery_id: branch, source, unit, start, end, horizon_days: horizon });
  if (selected && useSelected) params.set("actual_dataset_id", dataset.id);
  const query = params.toString();
  const result = loaded?.query === query ? loaded : null;
  const report = result?.data;
  const measure = unit === "physical_unit" ? "шт." : "строк чека";
  const names = selected ? {
    ...dataset.product_names,
    ...Object.fromEntries((dataset.catalog ?? []).map(p => [p.product_id, p.name])),
  } : {};
  return (
    <details className="quantity-help issued-forecast-report">
      <summary>Прогнозы и фактические продажи</summary>
      <p>Филиал {branch}. Учитываются выданные прогнозы, в том числе по неподтверждённым заказам.</p>
      <form onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setLoaded(null);
        try {
          const data = await api<Report>(`/reports/issued-forecasts?${query}`);
          setLoaded({ query, data });
        } catch (error) {
          setLoaded({ query, error: error instanceof Error ? error.message : "Не удалось получить отчёт." });
        } finally {
          setBusy(false);
        }
      }}>
        <fieldset disabled={busy} className="controls">
          <legend>Период и условия сверки</legend>
          <label>Дата продаж с <input type="date" required value={start} max={end} onChange={e => setStart(e.target.value)} /></label>
          <label>Дата продаж по <input type="date" required value={end} min={start} onChange={e => setEnd(e.target.value)} /></label>
          <label>Дней от среза данных до даты продаж <input type="number" min="2" max="1024" required value={horizon} onChange={e => setHorizon(e.target.value)} /></label>
          <label>Источник <select value={source} onChange={e => setSource(e.target.value)}>
            <option value="local">Продажи филиала</option><option value="external">Внешние данные</option><option value="synthetic">Демо-данные</option>
          </select></label>
          <label>Единица учёта <select value={unit} onChange={e => setUnit(e.target.value === "item_line" ? "item_line" : "physical_unit")}>
            <option value="physical_unit">Штуки</option><option value="item_line">Строки чеков</option>
          </select></label>
          {selected && <label><input type="checkbox" checked={useSelected} onChange={e => setUseSelected(e.target.checked)} /> Только продажи из открытого набора</label>}
        </fieldset>
        <button className="secondary" disabled={busy}>{busy ? "Сверяем…" : "Сверить прогнозы"}</button>
      </form>
      {result?.error && <p role="alert">{result.error}</p>}
      {report && <div>
        <p role="status"><strong>Сверено с продажами: {report.matched_product_days} из {report.issued_product_days}</strong> товаро-дней
          {report.coverage !== null && ` (${format(100 * report.coverage)}%)`}.</p>
        {report.issued_product_days === 0 && <p>За выбранные даты в журнале нет выданных прогнозов с таким сроком.</p>}
        {report.issued_product_days > 0 && <p className="muted">Для каждого товара и даты берём первый прогноз с указанным сроком.
          {report.actual_selection === "latest_observation" && " Факт берём из последних доступных данных по этому товару и дню."}</p>}
        {report.matched_product_days > 0 && <>
          <p>Средняя абсолютная ошибка BakePlan — <strong>{format(report.model_mae)} {measure}</strong>, по похожим дням недели (B1) — {format(report.b1_mae)} {measure}</p>
          <p>Сумма превышений над фактом — {format(report.overforecast_total)} {measure}, сумма недооценок — {format(report.underforecast_total)} {measure}</p>
          <ul>{Object.entries({ sparse: "Редкие продажи", regular: "Регулярные продажи", unknown: "Частота неизвестна" }).map(([key, label]) => {
            const row = report.by_frequency[key];
            if (!row?.matched_product_days) return null;
            return <li key={key}>{label}: {row.matched_product_days} товаро-дней, средняя ошибка BakePlan {format(row.model_mae)} {measure}, B1 {format(row.b1_mae)} {measure}</li>;
          })}</ul>
          {report.availability_unknown_days > 0 && <p>Наличие на полке неизвестно для {report.availability_unknown_days} сверенных товаро-дней.</p>}
        </>}
        {Object.entries(exclusions).map(([key, label]) => {
          const count = report.excluded[key as keyof typeof exclusions];
          return count > 0 && <p key={key}>{label}: {count}</p>;
        })}
        {report.issued_product_days > 0 && <div className="issued-score-scroll">
          <table><caption>Ошибка по товарам, {measure}</caption>
            <thead><tr><th scope="col">Товар</th><th scope="col">Сверено / выдано</th><th scope="col">Ошибка BakePlan</th><th scope="col">Ошибка B1</th><th scope="col">Выше факта, всего</th><th scope="col">Ниже факта, всего</th></tr></thead>
            <tbody>{Object.entries(report.by_product).sort(([a], [b]) => a.localeCompare(b)).map(([sku, row]) =>
              <tr key={sku}><th scope="row">{Object.hasOwn(names, sku) ? names[sku] : sku}</th><td>{row.matched_product_days} / {row.issued_product_days}</td>
                <td>{format(row.model_mae)}</td><td>{format(row.b1_mae)}</td>
                <td>{row.matched_product_days ? format(row.overforecast_total) : "—"}</td>
                <td>{row.matched_product_days ? format(row.underforecast_total) : "—"}</td></tr>)}</tbody>
          </table>
        </div>}
      </div>}
    </details>
  );
}
