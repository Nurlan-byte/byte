import { api } from "./api";
import type { Dataset } from "./types";
import type { SectionProps } from "./sectionProps";
export function ImportSection({
  csv,
  setCsv,
  source,
  setSource,
  dataset,
  setDataset,
  setPlan,
  busy,
  setError,
  setNotice,
  run,
}: Pick<
  SectionProps,
  | "csv"
  | "setCsv"
  | "source"
  | "setSource"
  | "dataset"
  | "setDataset"
  | "setPlan"
  | "busy"
  | "setError"
  | "setNotice"
  | "run"
>) {
  return (
    <section>
      <h2>1. Импорт истории</h2>
      <p>
        CSV проверяется целиком: целые неотрицательные количества, баланс и
        уникальность даты/товара. Пропуски не становятся нулями.
      </p>
      <button
        className="secondary"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            const r = await fetch("/demo.csv");
            if (!r.ok) throw new Error("Демо-файл недоступен");
            setCsv(await r.text());
            setSource("synthetic");
            setNotice(
              "Загружены синтетические данные. Выберите дату плана и импортируйте CSV.",
            );
          })
        }
      >
        Загрузить демо CSV
      </button>
      <div className="controls">
        <label>
          Источник
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="synthetic">Синтетические / демонстрационные</option>
            <option value="local">Локальные данные пекарни</option>
            <option value="external">Внешние данные</option>
          </select>
        </label>
        <label>
          CSV-файл
          <input
            type="file"
            accept=".csv,text/csv"
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) {
                if (f.size > 2_000_000) {
                  setError("Файл превышает 2 МБ");
                  return;
                }
                void f.text().then(setCsv);
              }
            }}
          />
        </label>
      </div>
      <label>
        Содержимое CSV
        <textarea
          aria-label="Содержимое CSV"
          rows={5}
          value={csv}
          onChange={(e) => setCsv(e.target.value)}
          placeholder="date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status"
        />
      </label>
      <button
        disabled={busy || !csv.trim()}
        onClick={() =>
          void run(async () => {
            setDataset(
              await api<Dataset>("/datasets", { csv_text: csv, source }),
            );
            setPlan(null);
            setNotice("Данные проверены и сохранены. Можно рассчитать план.");
          })
        }
      >
        Проверить и импортировать
      </button>
      {dataset && (
        <p className="success">
          Набор {dataset.id} · источник: {dataset.source}{" "}
          {dataset.row_count !== undefined && `· строк: ${dataset.row_count}`}
        </p>
      )}
      {dataset?.diagnostics?.map((d, n) => (
        <p className="muted" key={n}>
          {d}
        </p>
      ))}
    </section>
  );
}
