import { useEffect, useRef, useState } from "react";
import type { ImportProps } from "./ImportSection";
import type { Dataset } from "./types";
import { api } from "./api";
import {
  demoCatalog,
  parseCatalog,
  syntheticProcurementCsv,
} from "./procurement";

export function ProcurementImport(p: ImportProps) {
  const [catalog, setCatalog] = useState(demoCatalog);
  const [signature, setSignature] = useState("");
  const [saved, setSaved] = useState<Dataset[]>([]);
  const [savedId, setSavedId] = useState("");
  const summary = useRef<HTMLDivElement>(null);
  const dataset = p.dataset?.planning_kind === "procurement" ? p.dataset : null;
  const draft = JSON.stringify([
    p.csv,
    catalog,
    p.source,
    p.bakeryId,
    p.provenance,
  ]);
  useEffect(() => {
    if (dataset && p.active !== false) summary.current?.focus();
  }, [dataset, p.active]);
  return (
    <section aria-labelledby="import-heading" id="import">
      <h2 id="import-heading">Данные закупаемых товаров</h2>
      <p>
        Продажи и движение готового товара в физических штуках. Пропуски
        означают неизвестные данные.
      </p>
      {dataset && (
        <div
          className="success"
          data-testid="dataset-summary"
          tabIndex={-1}
          ref={summary}
        >
          <h3>Набор сохранён</h3>
          <p>Происхождение: {dataset.provenance}</p>
          <p>
            Источник: {dataset.source} · строк: {dataset.row_count ?? "—"} ·
            схема procurement-1
          </p>
          {signature !== draft && (
            <p className="status-note">
              Есть несохранённые поля импорта. План использует сохранённый
              набор.
            </p>
          )}
          <button disabled={p.busy} onClick={p.onContinue}>
            Перейти к плану
          </button>
        </div>
      )}
      <details>
        <summary>Открыть сохранённые данные закупки</summary>
        <button
          disabled={p.busy}
          onClick={() =>
            void p.run(async () =>
              setSaved(
                (await api<Dataset[]>("/datasets?limit=100")).filter(
                  (d) => d.planning_kind === "procurement",
                ),
              ),
            )
          }
        >
          Обновить наборы закупки
        </button>
        <label htmlFor="proc-saved">Сохранённый набор закупки</label>
        <select
          id="proc-saved"
          value={savedId}
          onChange={(e) => setSavedId(e.target.value)}
        >
          <option value="">Выберите набор</option>
          {saved.map((d) => (
            <option key={d.id} value={d.id}>
              {d.provenance || d.id}
            </option>
          ))}
        </select>
        <button
          disabled={p.busy || !savedId}
          onClick={() =>
            void p.run(async () => {
              const d = await api<Dataset>(
                `/datasets/${encodeURIComponent(savedId)}`,
              );
              p.setDataset(d);
              p.setPlan(null);
              p.setNotice("Набор закупки открыт.");
            })
          }
        >
          Открыть набор закупки
        </button>
        {!saved.length && (
          <p>Список пуст. Обновите наборы или импортируйте CSV.</p>
        )}
      </details>
      <div className="data-guide">
        <p>CSV: date,product_id,opening,received,sold,waste,closing</p>
        <p>
          Баланс: остаток утром + поступило = продано + списано + остаток
          вечером. Каталог должен содержать ровно товары CSV.
        </p>
      </div>
      <button
        className="secondary"
        disabled={p.busy}
        onClick={() => {
          p.setCsv(syntheticProcurementCsv());
          setCatalog(demoCatalog);
          p.setSource("synthetic");
          p.setBakeryId("procurement-demo");
          p.setProvenance("Синтетический пример закупки чая и сока");
        }}
      >
        Демо закупки
      </button>
      <div className="control-field">
        <label htmlFor="proc-file">CSV-файл закупки</label>
        <input
          id="proc-file"
          type="file"
          accept=".csv,text/csv"
          disabled={p.busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file)
              void p.run(async () => {
                if (file.size > 2_000_000)
                  throw new Error("Файл превышает 2 МБ.");
                p.setCsv(await file.text());
              });
          }}
        />
      </div>
      <div className="control-field">
        <label htmlFor="proc-csv">Содержимое CSV закупки</label>
        <textarea
          id="proc-csv"
          rows={6}
          disabled={p.busy}
          value={p.csv}
          onChange={(e) => p.setCsv(e.target.value)}
        />
      </div>
      <div className="control-field">
        <label htmlFor="proc-catalog">Каталог товаров (JSON)</label>
        <p id="catalog-help">
          Упаковку и лимит задаёт поставщик. Значения ниже — синтетический
          пример; замените их для своего каталога. product_id — код; name —
          название; pack_size — штук в упаковке; max_order_qty — лимит штук или
          null без лимита.
        </p>
        <textarea
          id="proc-catalog"
          aria-describedby="catalog-help"
          rows={8}
          disabled={p.busy}
          value={catalog}
          onChange={(e) => setCatalog(e.target.value)}
        />
      </div>
      <div className="controls">
        <div className="control-field">
          <label htmlFor="proc-source">Источник закупки</label>
          <select
            id="proc-source"
            value={p.source}
            disabled={p.busy}
            onChange={(e) => p.setSource(e.target.value)}
          >
            <option value="synthetic">Синтетические данные</option>
            <option value="local">Локальные данные</option>
            <option value="external">Внешние данные</option>
          </select>
        </div>
        <div className="control-field">
          <label htmlFor="proc-shop">ID торговой точки</label>
          <input
            id="proc-shop"
            value={p.bakeryId}
            disabled={p.busy}
            onChange={(e) => p.setBakeryId(e.target.value)}
          />
        </div>
      </div>
      <div className="control-field">
        <label htmlFor="proc-provenance">Происхождение данных закупки</label>
        <input
          id="proc-provenance"
          value={p.provenance}
          disabled={p.busy}
          onChange={(e) => p.setProvenance(e.target.value)}
        />
      </div>
      <button
        disabled={
          p.busy || !p.csv.trim() || !p.provenance.trim() || !p.bakeryId.trim()
        }
        onClick={() =>
          void p.run(async () => {
            const products = parseCatalog(catalog);
            const result = await api<Dataset>("/datasets", {
              schema_version: "procurement-1",
              source: p.source,
              bakery_id: p.bakeryId.trim(),
              target: "sold_units",
              unit: "physical_unit",
              provenance: p.provenance.trim(),
              csv_text: p.csv,
              catalog: products,
            });
            p.setDataset(result);
            p.setPlan(null);
            p.setDataTarget("sold_units");
            setSignature(draft);
            p.setNotice("Данные закупки проверены и сохранены.");
          })
        }
      >
        Проверить и импортировать закупку
      </button>
    </section>
  );
}
