import { useMemo, useRef, useState, useEffect } from "react";
import { savedDataset } from "./workspace";
import { api } from "./api";
import { businessDate } from "./dates";
import { datasetSourceLabel, formatDatasetPeriod } from "./datasetSummary";
import type { Dataset, DatasetTarget } from "./types";
import type { SectionProps } from "./sectionProps";

type SavedDataset = Pick<Dataset, "id" | "source" | "created_at" | "row_count">;

export function SavedDatasetSelect({
  datasets,
  value,
  onChange,
  disabled,
}: {
  datasets: SavedDataset[];
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="control-field">
      <label htmlFor="saved-dataset">Сохранённый набор</label>
      <select
        id="saved-dataset"
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Выберите набор из списка</option>
        {datasets.map((entry, index) => (
          <option value={entry.id} key={entry.id}>
            {index + 1}. {datasetSourceLabel(entry.source)} ·{" "}
            {entry.row_count ?? "—"} строк ·{" "}
            {entry.created_at
              ? new Date(entry.created_at).toLocaleString("ru-RU")
              : "дата не указана"}
          </option>
        ))}
      </select>
    </div>
  );
}

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

type ImportProps = Pick<
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
> & { onContinue?: () => void; active?: boolean };

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
  onContinue = () => {},
  active = true,
}: ImportProps) {
  const [saved, setSaved] = useState<SavedDataset[]>([]);
  const [savedId, setSavedId] = useState("");
  const [savedLoaded, setSavedLoaded] = useState(false);
  const [fileName, setFileName] = useState("");
  const [savedError, setSavedError] = useState("");
  useEffect(() => {
    if (!active) return;
    let current = true;
    void api<SavedDataset[]>("/datasets?limit=100")
      .then((rows) => {
        if (!current) return;
        setSaved(rows);
        setSavedLoaded(true);
        setSavedError("");
        setSavedId(dataset?.id ?? "");
      })
      .catch(() => {
        if (current)
          setSavedError(
            "Не удалось получить сохранённые наборы. Нажмите «Обновить наборы», когда соединение восстановится.",
          );
      });
    return () => {
      current = false;
    };
  }, [active, dataset?.id]);
  const fileInput = useRef<HTMLInputElement>(null);
  const summary = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (active && dataset) summary.current?.focus();
  }, [active, dataset]);
  const [importedDraft, setImportedDraft] = useState<{
    datasetId: string;
    signature: string;
  } | null>(null);
  const salesOnly = dataTarget === "item_line_count";
  const savedSalesOnly =
    dataset?.target === "item_line_count" || dataset?.unit === "item_line";
  const period = useMemo(() => formatDatasetPeriod(dataset), [dataset]);
  const draftSignature = JSON.stringify([
    csv,
    source,
    bakeryId.trim(),
    dataTarget,
    provenance.trim(),
  ]);
  const draftMatchesSaved =
    dataset?.id === importedDraft?.datasetId &&
    draftSignature === importedDraft?.signature;
  const hasDraftChanges =
    !!dataset &&
    ((!!csv.trim() && !draftMatchesSaved) ||
      source !== dataset.source ||
      bakeryId.trim() !== dataset.bakery_id ||
      dataTarget !== dataset.target ||
      provenance.trim() !== dataset.provenance);
  const csvTooLong = [...csv].length > 1_000_000;
  const missingImportFields = csvTooLong
    ? "CSV превышает 1 000 000 символов. Сократите историю и повторите импорт."
    : !csv.trim()
      ? "Выберите CSV-файл, загрузите демо или вставьте CSV ниже."
      : !bakeryId.trim()
        ? "Укажите ID пекарни в разделе «Параметры набора»."
        : !provenance.trim()
          ? "Укажите происхождение данных: название источника и описание набора."
          : "";

  return (
    <section id="import" aria-labelledby="import-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">История продаж</p>
          <h2 id="import-heading">Данные пекарни</h2>
          <p className="section-copy">
            Загрузите историю из CSV или попробуйте демо. Сначала проверим
            данные, затем можно перейти к плану выпечки.
          </p>
        </div>
        <span className={`badge ${dataset ? "badge-success" : ""}`}>
          {dataset ? "Проверено и сохранено" : "Начните с импорта"}
        </span>
      </div>

      {dataset && (
        <div
          className="success"
          data-testid="dataset-summary"
          ref={summary}
          tabIndex={-1}
        >
          <div className="section-heading">
            <h3>Текущий сохранённый набор для расчёта</h3>
            <span
              className={`badge ${dataset.source === "synthetic" || savedSalesOnly ? "badge-warning" : "badge-success"}`}
            >
              {savedSalesOnly ? "Только диагностика" : "Физические штуки"}
            </span>
          </div>
          <div className="metric-grid">
            <div className="metric-card">
              <span>Строк в наборе</span>
              <strong>
                {dataset.row_count ?? dataset.rows?.length ?? "Не указано"}
              </strong>
            </div>
            <div className="metric-card">
              <span>Период истории</span>
              <strong data-testid="dataset-period">{period}</strong>
            </div>
            <div className="metric-card">
              <span>Источник</span>
              <strong>{datasetSourceLabel(dataset.source)}</strong>
            </div>
          </div>
          <p>Происхождение: {dataset.provenance}</p>
          {dataset.source === "synthetic" && (
            <p className="status-note">
              Это синтетическая демонстрация, а не реальные продажи пекарни.
            </p>
          )}
          {savedSalesOnly && (
            <p className="status-note">
              Строки продаж не равны физическим штукам. Для этого набора
              доступна диагностика; план выпуска не рассчитывается.
            </p>
          )}
          {dataset.metadata_declared === false && (
            <p className="status-note">
              Старый набор доступен для B0/B1. Для обучения модели повторно
              импортируйте исходный CSV с явным происхождением и единицей.
            </p>
          )}
          {!!dataset.missing_calendar_days && (
            <p className="status-note">
              Пропуски дат по товарам: {dataset.missing_calendar_days}. Это
              сумма отсутствующих дат внутри истории каждого товара; пропуски не
              считаются нулевыми продажами.
            </p>
          )}
          {!!dataset.stockout_count && (
            <p className="status-note">
              Строк с отмеченным дефицитом: {dataset.stockout_count}. Продажи в
              эти дни могут не отражать весь спрос.
            </p>
          )}
          {!!dataset.unknown_stockout_count && (
            <p className="status-note">
              Строк с неизвестным статусом дефицита:{" "}
              {dataset.unknown_stockout_count}. Эти наблюдения ограничивают
              автоматический расчёт.
            </p>
          )}
          {dataset.diagnostics?.map((diagnostic, index) => (
            <p className="muted" key={index}>
              {diagnostic}
            </p>
          ))}
          {hasDraftChanges && (
            <p className="status-note" data-testid="unsaved-import">
              Ниже есть изменения для нового импорта. Расчёт пока использует
              сохранённый набор с периодом {period}.
            </p>
          )}
          <div className="actions">
            <button disabled={busy} onClick={onContinue}>
              {savedSalesOnly ? "Перейти к диагностике" : "Перейти к плану"}
            </button>
            <span className="muted">
              {savedSalesOnly
                ? "Прогноз наблюдаемых строк продаж"
                : "Расчёт по дням недели доступен без обучения"}
            </span>
          </div>
          <details>
            <summary>Технические данные набора</summary>
            <p>Набор: {dataset.id}</p>
            <p>
              Пекарня: {dataset.bakery_id} · источник: {dataset.source}
            </p>
            <p>
              Цель: {dataset.target} · единица: {dataset.unit} · схема:{" "}
              {dataset.schema_version}
            </p>
            <p>
              Версия данных:{" "}
              {dataset.dataset_version ?? dataset.version ?? "не указана"}
            </p>
            {dataset.created_at && <p>Сохранён: {dataset.created_at}</p>}
          </details>
        </div>
      )}

      <details className="saved-datasets">
        <summary>Продолжить работу с сохранённым набором</summary>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const datasets = await api<SavedDataset[]>("/datasets?limit=100");
              setSaved(datasets);
              setSavedId(dataset?.id ?? "");
              setSavedLoaded(true);
              setSavedError("");
            })
          }
        >
          Обновить наборы
        </button>
        {!!saved.length && (
          <div className="controls">
            <SavedDatasetSelect
              datasets={saved}
              value={savedId}
              onChange={setSavedId}
              disabled={busy}
            />
            <button
              className="secondary"
              disabled={busy || !savedId}
              onClick={() =>
                void run(async () => {
                  const result = await api<Dataset>(`/datasets/${savedId}`);
                  const stored = savedDataset(result);
                  setDataset(stored);
                  setPlan(null);
                  setCsv("");
                  setFileName("");
                  if (fileInput.current) fileInput.current.value = "";
                  setImportedDraft(null);
                  setSource(stored.source);
                  setBakeryId(stored.bakery_id);
                  setDataTarget(stored.target);
                  setProvenance(stored.provenance);
                  setNotice(
                    "Сохранённый набор открыт. Проверьте его период и перейдите к плану.",
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
        {savedError && (
          <p role="alert" className="error">
            {savedError}
          </p>
        )}
        {saved.length === 100 && (
          <p className="muted">Показаны последние 100 наборов.</p>
        )}
      </details>

      <h3>{dataset ? "Загрузить другой набор" : "Загрузить историю"}</h3>
      {!dataset && (
        <div className="data-guide">
          <h3>Какие данные нужны?</h3>
          <p>
            По каждому изделию и дню: сколько осталось утром, испекли, продали,
            списали и осталось вечером. Отметьте закрытые дни и случаи, когда
            товар закончился.
          </p>
          <p>
            Для расчёта по дням недели полезна история за последние 8 недель.
            Короткую историю можно импортировать, но для части изделий оценки
            может не быть.
          </p>
          <details>
            <summary>Формат файла и пять изделий</summary>
            <p>
              Одна строка — один день и одно изделие. Количества — целые
              физические штуки. Пропуск даты не означает нулевые продажи.
            </p>
            <p>
              Каталог фиксирован: p1 — хлеб пшеничный, p2 — хлеб ржаной, p3 —
              багет, p4 — круассан, p5 — булочка. Другие изделия нельзя
              переименовывать в эти коды ради импорта.
            </p>
            <p>
              Столбцы CSV: date, product_id, is_open, opening, produced, sold,
              waste, closing, other_out, stockout_status.
            </p>
            <p>
              Баланс: остаток утром + выпуск = продажи + списания + остаток
              вечером + прочее выбытие. stockout_status: none — товар не
              заканчивался, occurred — заканчивался, unknown — неизвестно.
            </p>
            <a href="/demo.csv" download>
              Скачать синтетический пример CSV
            </a>
          </details>
        </div>
      )}
      <p className="import-stage" data-testid="import-stage">
        {draftMatchesSaved
          ? "3 из 3 · Данные проверены и сохранены. Можно рассчитывать план."
          : csv.trim()
            ? "2 из 3 · Файл подготовлен, но ещё не импортирован. Проверьте источник и нажмите «Проверить и импортировать»."
            : "1 из 3 · Выберите файл или попробуйте демо."}
      </p>
      <div className="file-drop">
        <label htmlFor="import-file">CSV-файл</label>
        <p className="muted" id="import-file-help">
          UTF-8, разделитель — запятая. До 1 000 000 символов CSV и 10 000
          строк; весь запрос — до 2 МБ. Для своего файла проверьте источник и
          происхождение данных ниже.
        </p>
        <input
          id="import-file"
          ref={fileInput}
          type="file"
          accept=".csv,text/csv"
          aria-describedby="import-file-help"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            if (file.size > 2_000_000) {
              setError(
                "Файл превышает 2 МБ. Уменьшите CSV и выберите файл снова. Текущее содержимое не изменено.",
              );
              event.target.value = "";
              return;
            }
            void run(async () => {
              let text: string;
              try {
                text = await file.text();
              } catch {
                if (fileInput.current) fileInput.current.value = "";
                throw new Error(
                  "Не удалось прочитать CSV-файл. Выберите его снова или вставьте содержимое в разделе «Вставить или проверить CSV». Текущее содержимое не изменено.",
                );
              }
              if ([...text].length > 1_000_000) {
                if (fileInput.current) fileInput.current.value = "";
                throw new Error(
                  "CSV превышает 1 000 000 символов. Сократите файл и выберите его снова. Текущее содержимое не изменено.",
                );
              }
              setCsv(text);
              setFileName(file.name);
              setNotice(
                "Файл прочитан, но ещё не импортирован. Проверьте источник и нажмите «Проверить и импортировать».",
              );
            });
          }}
        />
        {fileName && <p className="muted">Выбран файл: {fileName}</p>}
      </div>
      <div className="actions">
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const response = await fetch("/demo.csv");
              if (!response.ok)
                throw new Error(
                  "Демо-файл недоступен. Повторите загрузку или выберите свой CSV-файл.",
                );
              setCsv(await response.text());
              setFileName("");
              if (fileInput.current) fileInput.current.value = "";
              setSource("synthetic");
              setBakeryId("demo");
              setDataTarget("sold_units");
              setProvenance(
                "Синтетическая демонстрация BakePlan: физические штуки",
              );
              setNotice(
                "Подготовлены синтетические данные в штуках. Нажмите «Проверить и импортировать», затем перейдите к плану.",
              );
            })
          }
        >
          Загрузить демо CSV
        </button>
        <button
          className="button-link"
          disabled={busy}
          onClick={() => {
            setCsv(syntheticSalesCsv());
            setFileName("");
            if (fileInput.current) fileInput.current.value = "";
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
        <span className="muted">Оба демо содержат синтетические данные</span>
      </div>
      <div className="controls">
        <div className="control-field">
          <label htmlFor="import-source">Источник</label>
          <select
            id="import-source"
            disabled={busy}
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
              setProvenance(
                event.target.value === "synthetic"
                  ? "Синтетическая демонстрация BakePlan"
                  : "",
              );
            }}
          >
            <option value="synthetic">Синтетические данные</option>
            <option value="local">Локальные данные пекарни</option>
            <option value="external">Внешние данные</option>
          </select>
        </div>
        <div className="control-field">
          <label htmlFor="import-target">Измеряемая величина</label>
          <select
            id="import-target"
            disabled={busy}
            value={dataTarget}
            onChange={(event) =>
              setDataTarget(event.target.value as DatasetTarget)
            }
          >
            <option value="sold_units">Продажи в штуках</option>
            <option value="item_line_count">Строки чеков — диагностика</option>
          </select>
        </div>
      </div>
      <div className="control-field">
        <label htmlFor="import-provenance">Происхождение данных</label>
        <input
          id="import-provenance"
          disabled={busy}
          value={provenance}
          onChange={(event) => setProvenance(event.target.value)}
          placeholder="Название источника и описание набора"
        />
      </div>
      <p className="muted">
        {salesOnly
          ? "Строка продажи не равна физической штуке. Агрегированные открытые данные, включая BreadBasket, подходят только для sales-only диагностики."
          : "Для плана нужны физические количества и корректный баланс. Пропуски остаются неизвестными и не становятся нулевыми продажами."}
      </p>
      <details>
        <summary>Параметры набора</summary>
        <div className="controls">
          <div className="control-field">
            <label htmlFor="import-bakery">ID пекарни</label>
            <input
              id="import-bakery"
              disabled={busy}
              value={bakeryId}
              onChange={(event) => setBakeryId(event.target.value)}
              placeholder="demo"
            />
          </div>
        </div>
        <p className="muted">
          {salesOnly
            ? "Схема sales-1 · единица item_line."
            : "Схема history-1 · единица physical_unit."}{" "}
          Наборы и модели разделяются по ID пекарни и единице измерения.
        </p>
      </details>
      <details>
        <summary>Вставить или проверить CSV</summary>
        <p className="muted">
          CSV проверяется целиком: целые неотрицательные количества, уникальная
          пара даты и товара, для физических штук — баланс остатков.
        </p>
        <label htmlFor="import-csv">Содержимое CSV</label>
        <textarea
          id="import-csv"
          rows={6}
          value={csv}
          disabled={busy}
          onChange={(event) => {
            setCsv(event.target.value);
            setFileName("");
            if (fileInput.current) fileInput.current.value = "";
          }}
          placeholder={
            salesOnly
              ? "date,product_id,item_line_count"
              : "date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status"
          }
        />
      </details>
      <div className="actions">
        <button
          disabled={busy || !!missingImportFields}
          aria-describedby="import-readiness"
          onClick={() =>
            void run(async () => {
              let imported: Dataset;
              try {
                imported = await api<Dataset>("/datasets", {
                  csv_text: csv,
                  source,
                  bakery_id: bakeryId.trim(),
                  target: dataTarget,
                  unit: salesOnly ? "item_line" : "physical_unit",
                  provenance: provenance.trim(),
                  schema_version: salesOnly ? "sales-1" : "history-1",
                });
              } catch (error) {
                const message =
                  error instanceof Error ? error.message : String(error);
                throw new Error(
                  `${message}\nПроверьте строку и поле из сообщения, содержимое CSV и параметры источника. После исправления повторите импорт.`,
                );
              }
              setDataset(imported);
              setImportedDraft({
                datasetId: imported.id,
                signature: draftSignature,
              });
              setPlan(null);
              setNotice(
                imported.target === "item_line_count"
                  ? "Данные строк продаж сохранены. Доступны обучение и диагностический прогноз, без производственного плана."
                  : "Данные проверены и сохранены. Можно перейти к плану; обучение модели не обязательно.",
              );
            })
          }
        >
          Проверить и импортировать
        </button>
        <span id="import-readiness" className="muted">
          {missingImportFields ||
            (draftMatchesSaved
              ? "Этот CSV уже проверен и сохранён."
              : "CSV подготовлен. Проверка и сохранение — после нажатия кнопки.")}
        </span>
      </div>
      {dataset && (
        <p className="muted">
          Изменения CSV и полей выше применяются только после повторного
          импорта.
        </p>
      )}
    </section>
  );
}
