import { useState } from "react";
import { api } from "./api";
import { businessDate } from "./dates";
import type { Dataset, DatasetTarget } from "./types";
import type { SectionProps } from "./sectionProps";

function syntheticSalesCsv() {
  const end = new Date(`${businessDate(-1)}T00:00:00Z`);
  const rows = ["date,product_id,item_line_count"];
  for (let day = 83; day >= 0; day--) {
    const date = new Date(end.getTime() - day * 86_400_000);
    for (const [index, product] of ["p1", "p2"].entries()) {
      rows.push(
        `${date.toISOString().slice(0, 10)},${product},${20 + date.getUTCDay() * 3 + index * 7}`,
      );
    }
  }
  return rows.join("\n");
}

export function ImportSection({
  csv,
  setCsv,
  source,
  setSource,
  bakeryId,
  setBakeryId,
  dataTarget,
  setDataTarget,
  provenance,
  setProvenance,
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
  | "bakeryId"
  | "setBakeryId"
  | "dataTarget"
  | "setDataTarget"
  | "provenance"
  | "setProvenance"
  | "dataset"
  | "setDataset"
  | "setPlan"
  | "busy"
  | "setError"
  | "setNotice"
  | "run"
>) {
  const [saved, setSaved] = useState<Pick<Dataset, "id" | "source">[]>([]);
  const [savedId, setSavedId] = useState("");
  const [savedLoaded, setSavedLoaded] = useState(false);
  const salesOnly = dataTarget === "item_line_count";
  return (
    <section>
      <h2>1. Импорт истории</h2>
      <p>
        CSV проверяется целиком: целые неотрицательные количества и уникальность
        даты/товара. Для истории в штуках проверяется баланс. Пропуски остаются
        неизвестными и не становятся нулями.
      </p>
      <details>
        <summary>Продолжить работу с сохранённым набором</summary>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const datasets = await api<Pick<Dataset, "id" | "source">[]>(
                "/datasets?limit=100",
              );
              setSaved(datasets);
              setSavedId(datasets[0]?.id ?? "");
              setSavedLoaded(true);
            })
          }
        >
          Обновить наборы
        </button>
        {!!saved.length && (
          <div className="controls">
            <label>
              Сохранённый набор
              <select
                disabled={busy}
                value={savedId}
                onChange={(e) => setSavedId(e.target.value)}
              >
                {saved.map((entry) => (
                  <option value={entry.id} key={entry.id}>
                    {entry.id} · {entry.source}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="secondary"
              disabled={busy || !savedId}
              onClick={() =>
                void run(async () => {
                  const result = await api<Dataset>(`/datasets/${savedId}`);
                  const stored: Dataset = {
                    ...result,
                    bakery_id: result.bakery_id ?? "legacy",
                    target: result.target ?? "sold_units",
                    unit: result.unit ?? "physical_unit",
                    provenance:
                      result.provenance ??
                      "Старый импорт: происхождение не указано",
                    metadata_declared: result.metadata_declared ?? false,
                  };
                  setDataset(stored);
                  setPlan(null);
                  setCsv("");
                  setSource(stored.source);
                  setBakeryId(stored.bakery_id);
                  setDataTarget(stored.target);
                  setProvenance(stored.provenance);
                  setNotice(
                    "Сохранённый набор открыт. Обновите модели, чтобы увидеть их статусы.",
                  );
                })
              }
            >
              Открыть набор
            </button>
          </div>
        )}
        {savedLoaded && !saved.length && (
          <p className="muted">Сохранённых наборов нет.</p>
        )}
        {saved.length === 100 && (
          <p className="muted">Показаны последние 100 наборов.</p>
        )}
      </details>
      <div className="actions">
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const r = await fetch("/demo.csv");
              if (!r.ok) throw new Error("Демо-файл недоступен");
              setCsv(await r.text());
              setSource("synthetic");
              setBakeryId("demo");
              setDataTarget("sold_units");
              setProvenance(
                "Синтетическая демонстрация BakePlan: физические штуки",
              );
              setNotice(
                "Загружены синтетические данные в штуках. Выберите дату плана и импортируйте CSV.",
              );
            })
          }
        >
          Загрузить демо CSV
        </button>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => {
            setCsv(syntheticSalesCsv());
            setSource("synthetic");
            setBakeryId("demo");
            setDataTarget("item_line_count");
            setProvenance(
              "Синтетические строки продаж для тестирования BakePlan",
            );
            setNotice(
              "Загружено синтетическое sales-only демо. Импортируйте CSV для диагностики; выпуск из этих данных не рассчитывается.",
            );
          }}
        >
          Демо строк продаж
        </button>
      </div>
      <div className="controls">
        <label>
          Источник
          <select
            disabled={busy}
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setProvenance(
                e.target.value === "synthetic"
                  ? "Синтетическая демонстрация BakePlan"
                  : "",
              );
            }}
          >
            <option value="synthetic">Синтетические / демонстрационные</option>
            <option value="local">Локальные данные пекарни</option>
            <option value="external">Внешние данные</option>
          </select>
        </label>
        <label>
          ID пекарни
          <input
            disabled={busy}
            value={bakeryId}
            onChange={(e) => setBakeryId(e.target.value)}
            placeholder="demo"
          />
        </label>
        <label>
          Измеряемая величина
          <select
            disabled={busy}
            value={dataTarget}
            onChange={(e) => setDataTarget(e.target.value as DatasetTarget)}
          >
            <option value="sold_units">
              Проданные физические штуки · sold_units
            </option>
            <option value="item_line_count">
              Строки продаж · item_line_count
            </option>
          </select>
        </label>
        <label>
          Происхождение данных
          <input
            disabled={busy}
            value={provenance}
            onChange={(e) => setProvenance(e.target.value)}
            placeholder="Название источника и описание набора"
          />
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
                void run(async () => setCsv(await f.text()));
              }
            }}
          />
        </label>
      </div>
      <p className="muted">
        {salesOnly
          ? "Схема sales-1 · единица item_line. Строка продажи не равна физической штуке. Агрегированные открытые данные, включая BreadBasket, подходят только для sales-only диагностики."
          : "Схема history-1 · единица physical_unit. Только подтверждённые физические количества и баланс допускаются к планированию."}
      </p>
      <label>
        Содержимое CSV
        <textarea
          aria-label="Содержимое CSV"
          rows={5}
          value={csv}
          disabled={busy}
          onChange={(e) => setCsv(e.target.value)}
          placeholder={
            salesOnly
              ? "date,product_id,item_line_count"
              : "date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status"
          }
        />
      </label>
      <button
        disabled={busy || !csv.trim() || !bakeryId.trim() || !provenance.trim()}
        onClick={() =>
          void run(async () => {
            const imported = await api<Dataset>("/datasets", {
              csv_text: csv,
              source,
              bakery_id: bakeryId.trim(),
              target: dataTarget,
              unit: salesOnly ? "item_line" : "physical_unit",
              provenance: provenance.trim(),
              schema_version: salesOnly ? "sales-1" : "history-1",
            });
            setDataset(imported);
            setPlan(null);
            setNotice(
              imported.target === "item_line_count"
                ? "Данные строк продаж сохранены. Доступны обучение и диагностический прогноз, без производственного плана."
                : "Данные проверены и сохранены. Можно рассчитать план или обучить модель.",
            );
          })
        }
      >
        Проверить и импортировать
      </button>
      {dataset && (
        <div className="success" data-testid="dataset-summary">
          <p>
            Набор {dataset.id} · источник: {dataset.source}{" "}
            {dataset.row_count !== undefined && `· строк: ${dataset.row_count}`}
          </p>
          <p>
            Пекарня: {dataset.bakery_id} · цель: {dataset.target} · единица:{" "}
            {dataset.unit} · схема: {dataset.schema_version}
          </p>
          <p>Происхождение: {dataset.provenance}</p>
        </div>
      )}
      {dataset?.diagnostics?.map((d, n) => (
        <p className="muted" key={n}>
          {d}
        </p>
      ))}
    </section>
  );
}
