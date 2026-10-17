import { useEffect, useRef, useState } from "react";
import type { ImportProps } from "./ImportSection";
import type { CatalogItem, Dataset } from "./types";
import { api } from "./api";
import { datasetSourceLabel } from "./datasetSummary";
import {
  demoCatalog,
  parseCatalog,
  syntheticProcurementCsv,
} from "./procurement";

type ProductDraft = {
  product_id: string;
  name: string;
  pack_size: string;
  max_order_qty: string;
};
const emptyProduct = (): ProductDraft => ({
  product_id: "",
  name: "",
  pack_size: "",
  max_order_qty: "",
});
const editableCatalog = (items: CatalogItem[]): ProductDraft[] =>
  items.map((item) => ({
    product_id: item.product_id,
    name: item.name,
    pack_size: String(item.pack_size),
    max_order_qty: item.max_order_qty == null ? "" : String(item.max_order_qty),
  }));

export function ProcurementImport(p: ImportProps) {
  const [catalog, setCatalog] = useState<ProductDraft[]>([emptyProduct()]);
  const [fileName, setFileName] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  function updateProduct(
    index: number,
    field: keyof ProductDraft,
    value: string,
  ) {
    setCatalog((previous) =>
      previous.map((item, n) =>
        n === index ? { ...item, [field]: value } : item,
      ),
    );
  }
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
            Источник: {datasetSourceLabel(dataset.source)} · строк:{" "}
            {dataset.row_count ?? "—"}
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
      <details className="data-guide">
        <summary>Формат файла и проверка остатков</summary>
        <p>CSV: date,product_id,opening,received,sold,waste,closing</p>
        <p>
          Баланс: остаток утром + поступило = продано + списано + остаток
          вечером. Каталог должен содержать ровно товары CSV.
        </p>
      </details>
      <button
        className="secondary"
        disabled={p.busy}
        onClick={() => {
          p.setCsv(syntheticProcurementCsv());
          setCatalog(editableCatalog(parseCatalog(demoCatalog)));
          setFileName("");
          if (fileInput.current) fileInput.current.value = "";
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
          ref={fileInput}
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
                setFileName(file.name);
                if (p.source === "synthetic") {
                  p.setSource("local");
                  p.setProvenance(file.name);
                }
                if (p.bakeryId === "demo" || p.bakeryId === "procurement-demo")
                  p.setBakeryId("");
                p.setNotice(
                  "Файл прочитан. Заполните товары и проверьте источник перед импортом.",
                );
              });
          }}
        />
      </div>
      {fileName && (
        <p role="status">Файл: {fileName}. Данные ещё не импортированы.</p>
      )}
      <details id="proc-manual-csv">
        <summary>Вставить или исправить текст файла</summary>
        <div className="control-field">
          <label htmlFor="proc-csv">Содержимое CSV закупки</label>
          <textarea
            id="proc-csv"
            rows={6}
            disabled={p.busy}
            value={p.csv}
            onChange={(e) => {
              p.setCsv(e.target.value);
              setFileName("");
            }}
          />
        </div>
      </details>
      <h3>Товары и условия поставщика</h3>
      <p id="catalog-help">
        Добавьте каждый товар из файла. Код должен совпадать с кодом в файле.
        Упаковку укажите по условиям поставщика. Пустой максимум означает, что
        ограничение не задано.
      </p>
      {catalog.map((item, index) => (
        <fieldset
          key={index}
          disabled={p.busy}
          aria-describedby="catalog-help"
          style={{ minInlineSize: 0 }}
        >
          <legend>Товар {index + 1}</legend>
          <div className="controls">
            <div className="control-field">
              <label htmlFor={`proc-product-${index}`}>
                Код товара {index + 1}
              </label>
              <input
                id={`proc-product-${index}`}
                value={item.product_id}
                maxLength={64}
                onChange={(e) =>
                  updateProduct(index, "product_id", e.target.value)
                }
              />
            </div>
            <div className="control-field">
              <label htmlFor={`proc-name-${index}`}>
                Название товара {index + 1}
              </label>
              <input
                id={`proc-name-${index}`}
                value={item.name}
                maxLength={100}
                onChange={(e) => updateProduct(index, "name", e.target.value)}
              />
            </div>
            <div className="control-field">
              <label htmlFor={`proc-pack-${index}`}>
                Штук в упаковке {index + 1}
              </label>
              <input
                id={`proc-pack-${index}`}
                type="number"
                min={1}
                max={1000000000}
                step={1}
                value={item.pack_size}
                onChange={(e) =>
                  updateProduct(index, "pack_size", e.target.value)
                }
              />
            </div>
            <div className="control-field">
              <label htmlFor={`proc-max-${index}`}>
                Максимум заказа {index + 1} (необязательно)
              </label>
              <input
                id={`proc-max-${index}`}
                type="number"
                min={0}
                max={1000000000}
                step={1}
                value={item.max_order_qty}
                placeholder="Не задан"
                onChange={(e) =>
                  updateProduct(index, "max_order_qty", e.target.value)
                }
              />
            </div>
          </div>
          <button
            type="button"
            disabled={p.busy || catalog.length === 1}
            onClick={() =>
              setCatalog((previous) => previous.filter((_, n) => n !== index))
            }
          >
            Удалить товар {index + 1}
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        className="secondary"
        disabled={p.busy || catalog.length >= 50}
        onClick={() => setCatalog((previous) => [...previous, emptyProduct()])}
      >
        Добавить товар
      </button>
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
          <label htmlFor="proc-shop">Код точки</label>
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
            const products = parseCatalog(
              JSON.stringify(
                catalog.map((item, index) => {
                  if (
                    !/^[a-zA-Z0-9_-]+$/.test(item.product_id) ||
                    !item.name.trim()
                  )
                    throw new Error(
                      `Товар ${index + 1}: укажите название и код латинскими буквами, цифрами, дефисом или подчёркиванием.`,
                    );
                  if (
                    !/^\d+$/.test(item.pack_size) ||
                    Number(item.pack_size) < 1 ||
                    Number(item.pack_size) > 1000000000
                  )
                    throw new Error(
                      `Товар ${index + 1}: укажите целое количество в упаковке от 1 до 1000000000.`,
                    );
                  if (
                    item.max_order_qty !== "" &&
                    (!/^\d+$/.test(item.max_order_qty) ||
                      Number(item.max_order_qty) > 1000000000)
                  )
                    throw new Error(
                      `Товар ${index + 1}: максимум должен быть целым неотрицательным числом или пустым.`,
                    );
                  return {
                    product_id: item.product_id,
                    name: item.name.trim(),
                    pack_size: Number(item.pack_size),
                    max_order_qty:
                      item.max_order_qty === ""
                        ? null
                        : Number(item.max_order_qty),
                  };
                }),
              ),
            );
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
