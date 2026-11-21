import { PosOrderSetup } from "./PosOrderSetup";
import { useRef, useState, useEffect } from "react";
import { api, createForecast } from "./api";
import type { Dataset, ForecastMethod, ModelSummary } from "./types";
import type { SectionProps } from "./sectionProps";
import { ForecastResult, type ForecastSnapshot } from "./ForecastResult";
import { forecastInputsChanged } from "./snapshots";
import { reasonText } from "./explanations";

type Props = Pick<
  SectionProps,
  "busy" | "cutoff" | "setCutoff" | "target" | "setTarget" | "run" | "setNotice"
> & {
  dataset: Dataset;
  active?: boolean;
  initial?: ForecastSnapshot;
  onOrderDataset?: (dataset: Dataset) => Promise<void>;
  runReplacingPlan?: SectionProps["run"];
};
export function ModelSection({
  dataset,
  busy,
  cutoff,
  setCutoff,
  target,
  setTarget,
  run,
  setNotice,
  active = true,
  initial,
  onOrderDataset,
  runReplacingPlan = run,
}: Props) {
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [forecast, setForecast] = useState<ForecastSnapshot | null>(
    initial ?? null,
  );
  const automatic =
    dataset.planning_kind === "procurement" ||
    dataset.forecast_only ||
    dataset.schema_version === "sales-1";
  const [diagnosticMethod, setDiagnosticMethod] = useState<ForecastMethod>(
    initial?.method ?? "ML",
  );
  const forecastMethod = automatic ? "AUTO" : diagnosticMethod;
  const [loadError, setLoadError] = useState("");
  const modelRead = useRef(0);
  useEffect(() => {
    if (!active || automatic) return;
    const version = ++modelRead.current;
    void api<ModelSummary[]>(`/datasets/${dataset.id}/models`)
      .then((rows) => {
        if (modelRead.current !== version) return;
        setModels(rows);
        setLoaded(true);
        setLoadError("");
      })
      .catch(() => {
        if (modelRead.current === version)
          setLoadError(
            "Не удалось загрузить модели. Нажмите «Обновить расчёты» после восстановления соединения.",
          );
      });
    return () => {
      modelRead.current++;
    };
  }, [active, automatic, dataset.id]);
  const training = useRef<{ signature: string; key: string } | null>(null);
  const salesOnly =
    dataset.target === "item_line_count" || dataset.unit === "item_line";
  const unitLabel = salesOnly ? "строк продаж" : "шт.";

  return (
    <section
      className="quality-workspace"
      id="model"
      aria-labelledby="model-heading"
      data-testid="model-panel"
    >
      <div className="section-heading">
        <div>
          <h1 id="model-heading">
            {dataset.planning_kind === "procurement"
              ? "Качество прогноза продаж"
              : "Качество прогноза"}
          </h1>
        </div>
        <span className="badge">Необязательный шаг</span>
      </div>
      <p className="section-copy">
        {automatic
          ? "Проверьте ожидаемые продажи по товарам на выбранную дату."
          : "Посмотрите прогноз по товарам на выбранную дату и сравните методы на прошлых продажах."}
      </p>
      {dataset.demand_context && (
        <div className="status-note">
          <strong>Факторы, доступные модели</strong>
          <p>
            {dataset.demand_context.calendar_source ?? "Дни недели"}
            {dataset.demand_context.weather.length > 0
              ? ` · ${dataset.demand_context.city ?? "Погода"} · ${dataset.demand_context.weather.length} дневных погодных записей`
              : " · Погодные данные не подключены"}
            .
          </p>
          {dataset.demand_context.weather_source?.startsWith("Open-Meteo") && (
            <small>
              Погодные прогнозы NOAA GFS предоставлены{" "}
              <a
                href="https://open-meteo.com/"
                target="_blank"
                rel="noreferrer"
              >
                Open-Meteo
              </a>
              .
            </small>
          )}
        </div>
      )}
      <div className="quality-setup">
        <h2 className="subheading">Параметры прогноза</h2>
        <p className="muted">
          {salesOnly
            ? "Единица прогноза — строка продажи. По этому набору нельзя назначить выпуск."
            : dataset.forecast_only
              ? "Касса передала проданные штуки. Для решения о выпуске или заказе проверьте остатки."
              : dataset.planning_kind === "procurement"
                ? "Прогноз оценивает продажи в штуках. Заказ подтверждается отдельно после проверки остатка и упаковки."
                : "Прогноз оценивает продажи в штуках. Количество для выпечки вы подтвердите отдельно в плане."}
        </p>
        {!automatic && dataset.metadata_declared === false && (
          <p className="error">
            Этот старый набор не содержит явно объявленных метаданных. Для
            обучения повторно импортируйте исходный CSV, указав пекарню,
            величину, единицу и происхождение.
          </p>
        )}
        <div className="controls">
          <div className="control-field">
            <label htmlFor="model-cutoff">
              {automatic
                ? "Последний день истории продаж"
                : "Срез обучения и прогноза"}
            </label>
            <input
              id="model-cutoff"
              type="date"
              disabled={busy}
              value={cutoff}
              onChange={(e) => setCutoff(e.target.value)}
            />
          </div>
          <div className="control-field">
            <label htmlFor="forecast-date">Дата прогноза</label>
            <input
              id="forecast-date"
              type="date"
              disabled={busy}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
          </div>
          {!automatic && (
            <div className="control-field">
              <label htmlFor="diagnostic-method">Способ прогноза</label>
              <select
                id="diagnostic-method"
                disabled={busy}
                value={forecastMethod}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value === "ML" || value === "B1") setDiagnosticMethod(value);
                }}
              >
                <option value="ML">Экспериментальный расчёт</option>
                <option value="B1">По похожим дням недели</option>
              </select>
            </div>
          )}
        </div>
        <div className="actions quality-actions">
          <button
            className="cta"
            disabled={busy || !cutoff || !target}
            onClick={() =>
              void run(async () => {
                const result = await createForecast({
                  dataset_id: dataset.id,
                  target_date: target,
                  cutoff,
                  method: forecastMethod,
                });
                setForecast({
                  result,
                  cutoff,
                  targetDate: target,
                  method: forecastMethod,
                });
                setNotice(
                  "Прогноз готов. Проверьте результат и предупреждения по каждому товару.",
                );
              })
            }
          >
            Проверить прогноз
          </button>

          {!automatic && (
            <button
              disabled={busy || !cutoff || dataset.metadata_declared === false}
              onClick={() =>
                void run(async () => {
                  modelRead.current++;
                  const signature = JSON.stringify({
                    dataset: dataset.id,
                    cutoff,
                  });
                  if (training.current?.signature !== signature) {
                    training.current = { signature, key: crypto.randomUUID() };
                  }
                  const model = await api<ModelSummary>(
                    `/datasets/${dataset.id}/train`,
                    {
                      cutoff,
                      idempotency_key: training.current.key,
                    },
                  );
                  setForecast(null);
                  setModels((previous) => [
                    model,
                    ...previous.filter((entry) => entry.id !== model.id),
                  ]);
                  setLoaded(true);
                  setLoadError("");
                  setNotice(
                    model.status === "ready"
                      ? "Модель сохранена. Это не подтверждение качества; проверьте оценку и фактический метод прогноза."
                      : "Подготовка завершена, но экспериментальный расчёт пока недоступен. Причины показаны ниже; при подходящей истории можно использовать расчёт по похожим дням.",
                  );
                })
              }
            >
              Подготовить экспериментальный расчёт
            </button>
          )}
        </div>
        <details>
          <summary>Как работает прогноз</summary>
          {automatic ? (
            <p>
              BakePlan рассчитывает прогноз по каждому товару. Для редких продаж
              он использует похожие дни недели, если на прошлых датах они дали
              меньшую ошибку.
            </p>
          ) : (
            <p>
              По похожим дням недели можно рассчитать прогноз без подготовки.
              Для экспериментального способа сначала подготовьте модель на
              выбранном срезе. При нехватке данных BakePlan объяснит, какой
              способ удалось использовать.
            </p>
          )}
          <p>
            Модель использует только продажи до среза включительно. Пропущенная
            запись означает неизвестное значение. Синтетические наборы служат
            для демонстрации. Точность и экономию для конкретной точки нужно
            проверять на её продажах.
          </p>
        </details>
      </div>
      {forecast && (
        <ForecastResult
          procurement={dataset.planning_kind === "procurement"}
          productNames={dataset.product_names}
          hasDemandContext={!!dataset.demand_context}
          snapshot={forecast}
          stale={forecastInputsChanged(forecast, {
            target,
            cutoff,
            method: forecastMethod,
          })}
          unitLabel={unitLabel}
        />
      )}
      {onOrderDataset && (
        <PosOrderSetup
          dataset={dataset}
          busy={busy}
          run={runReplacingPlan}
          onReady={onOrderDataset}
        />
      )}
      {!automatic && (
        <>
          <div className="saved-models-heading">
            <h2 className="subheading">Сохранённые расчёты</h2>
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  modelRead.current++;
                  setModels(
                    await api<ModelSummary[]>(`/datasets/${dataset.id}/models`),
                  );
                  setLoaded(true);
                  setLoadError("");
                  setNotice("Статусы сохранённых моделей обновлены.");
                })
              }
            >
              Обновить расчёты
            </button>
          </div>
          {loadError && (
            <p role="alert" className="error">
              {loadError}
            </p>
          )}
          {!models.length && (
            <div className="empty-note">
              <strong>
                {loaded
                  ? "Сохранённых моделей пока нет"
                  : "Модели ещё не загружены"}
              </strong>
              <p>
                Обновите список или подготовьте экспериментальный расчёт.
                Прогноз по похожим дням можно получить без подготовки при
                достаточной истории.
              </p>
            </div>
          )}
          {models.map((model) => (
            <article
              key={model.id}
              className="model-card"
              data-testid="model-summary"
            >
              <div className="section-heading">
                <h3>
                  {model.status === "ready"
                    ? "Расчёт готов"
                    : "Расчёт недоступен"}
                </h3>
                <span
                  className={`badge ${model.status === "ready" ? "badge-success" : "badge-warning"}`}
                >
                  {model.status === "ready"
                    ? "Доступна для расчёта"
                    : "Нужна проверка"}
                </span>
              </div>
              {model.reason && <p>Причина: {reasonText(model.reason)}</p>}
              <p>
                Срез обучения: {model.training_cutoff} ·{" "}
                {model.unit === "item_line"
                  ? "строки продаж"
                  : "физические штуки"}
                .
              </p>
              <p className="muted">
                Готовность означает возможность расчёта, а не доказанную
                точность.
              </p>
              {model.training_cutoff !== cutoff && (
                <p className="muted">
                  Срез этой модели отличается от выбранного. Сервер проверит
                  совместимость перед использованием.
                </p>
              )}
              <ModelEvaluation
                procurement={dataset.planning_kind === "procurement"}
                evaluation={model.evaluation}
                unitLabel={unitLabel}
              />
              <details className="model-products">
                <summary>Готовность по товарам</summary>
                <ul>
                  {Object.entries(model.products ?? {}).map(
                    ([product, status]) => (
                      <li key={product}>
                        {product}:{" "}
                        {status.status === "ready" ? "готова" : "не готова"}
                        {status.reason ? ` · ${reasonText(status.reason)}` : ""}
                      </li>
                    ),
                  )}
                </ul>
              </details>
            </article>
          ))}
        </>
      )}
    </section>
  );
}

export function ModelEvaluation({
  evaluation,
  procurement = false,
  unitLabel,
}: {
  procurement?: boolean;
  evaluation: ModelSummary["evaluation"];
  unitLabel: string;
}) {
  if (evaluation == null)
    return (
      <p className="status-note" data-testid="model-evaluation">
        Качество прогноза пока не проверено. Проверьте предложенные количества
        вручную.
      </p>
    );

  const b1 = evaluation.b1_mae;
  const ml = evaluation.ml_mae;
  const comparable =
    (evaluation.paired_n ?? 0) > 0 &&
    b1 != null &&
    ml != null &&
    Number.isFinite(b1) &&
    Number.isFinite(ml);
  const mlWorse = comparable && ml > b1;
  return (
    <div
      data-testid="model-evaluation"
      className={mlWorse || !comparable ? "evaluation-warning" : "status-note"}
    >
      <h4>Качество прогноза</h4>
      <p>
        {!comparable
          ? "Недостаточно данных для сравнения способов расчёта. Проверьте количество вручную."
          : mlWorse
            ? "Экспериментальный расчёт на проверке оказался менее точным. Предпочтите расчёт по похожим дням и проверьте количество."
            : "На прошлых данных экспериментальный расчёт прошёл проверку. Это не гарантия точности будущих продаж."}
      </p>
      {comparable && (
        <details className="error-comparison">
          <summary>На сколько обычно ошибались методы</summary>
          <dl>
            <div>
              <dt>По похожим дням</dt>
              <dd>
                {b1.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}{" "}
                {unitLabel}
              </dd>
            </div>
            <div>
              <dt>Экспериментальный расчёт</dt>
              <dd>
                {ml.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}{" "}
                {unitLabel}
              </dd>
            </div>
          </dl>
          <p>
            Среднее отклонение от фактических продаж на одних и тех же
            наблюдениях. Чем меньше значение, тем точнее метод на этой проверке.
          </p>
        </details>
      )}
      {(procurement || evaluation.selection_performed) && (
        <p>
          Проверка использовалась для выбора способа расчёта. Она не является
          независимой оценкой точности.
        </p>
      )}
    </div>
  );
}
