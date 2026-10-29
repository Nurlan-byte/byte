import { useRef, useState, useEffect } from "react";
import { api } from "./api";
import type { Dataset, Forecast, ModelSummary } from "./types";
import type { SectionProps } from "./sectionProps";
import { ForecastResult, type ForecastSnapshot } from "./ForecastResult";
import { forecastInputsChanged } from "./snapshots";
import { reasonText } from "./explanations";

type Props = Pick<
  SectionProps,
  "busy" | "cutoff" | "setCutoff" | "target" | "setTarget" | "run" | "setNotice"
> & { dataset: Dataset; active?: boolean };
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
}: Props) {
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [forecast, setForecast] = useState<ForecastSnapshot | null>(null);
  const [forecastMethod, setForecastMethod] = useState("ML");
  const [loadError, setLoadError] = useState("");
  const modelRead = useRef(0);
  useEffect(() => {
    if (!active) return;
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
            "Не удалось загрузить модели. Нажмите «Обновить модели» после восстановления соединения.",
          );
      });
    return () => {
      modelRead.current++;
    };
  }, [active, dataset.id]);
  const training = useRef<{ signature: string; key: string } | null>(null);
  const salesOnly =
    dataset.target === "item_line_count" || dataset.unit === "item_line";
  const unitLabel = salesOnly ? "строк продаж" : "шт.";

  return (
    <section
      id="model"
      aria-labelledby="model-heading"
      data-testid="model-panel"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">Дополнительная проверка</p>
          <h2 id="model-heading">
            {dataset.planning_kind === "procurement"
              ? "Качество прогноза продаж"
              : "Качество прогноза"}
          </h2>
        </div>
        <span className="badge">Необязательный шаг</span>
      </div>
      <p className="section-copy">
        Здесь можно проверить экспериментальный расчёт на прошлых продажах.
        Готовый расчёт сам не назначает план или заказ.
      </p>
      {!salesOnly && (
        <p className="status-note">
          Для первого плана подготовка не нужна. По умолчанию используется расчёт по похожим дням;
          после расчёта проверьте количества и подтвердите{" "}
          {dataset.planning_kind === "procurement" ? "заказ" : "выпуск"}{" "}
          вручную.
        </p>
      )}
      <p className="muted">
        {salesOnly
          ? "Только строки продаж: оцениваем их число, а не количество изделий. Производственные партии, рекомендации выпуска и подтверждение недоступны."
          : dataset.planning_kind === "procurement"
            ? "Прогноз продаж не является заказом. Проверьте доступный остаток, упаковку и лимит."
            : "Диагностический прогноз не назначает выпуск. Производственный план требует отдельной ручной проверки."}{" "}
        Синтетические примеры проверяют работу программы. Точность на будущих
        продажах и экономия не доказаны.
      </p>
      <details>
        <summary>Как работает прогноз</summary>
        <p>
          Экспериментальный расчёт использует только продажи, доступные к
          выбранной дате. Если данных недостаточно, используется расчёт по
          похожим дням с объяснением. Отсутствующее значение не заменяется нулём.
        </p>
      </details>
      {dataset.metadata_declared === false && (
        <p className="error">
          Этот старый набор не содержит явно объявленных метаданных. Для
          обучения повторно импортируйте исходный CSV, указав пекарню, величину,
          единицу и происхождение.
        </p>
      )}
      <div className="controls">
        <div className="control-field">
          <label htmlFor="model-cutoff">Срез обучения и прогноза</label>
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
        <div className="control-field">
          <label htmlFor="diagnostic-method">Способ прогноза</label>
          <select
            id="diagnostic-method"
            disabled={busy}
            value={forecastMethod}
            onChange={(e) => setForecastMethod(e.target.value)}
          >
            <option value="ML">Экспериментальный расчёт</option>
            <option value="B1">По похожим дням недели</option>
          </select>
        </div>
      </div>
      <div className="actions">
        <button
          disabled={busy || !cutoff || dataset.metadata_declared === false}
          onClick={() =>
            void run(async () => {
              modelRead.current++;
              const signature = JSON.stringify({ dataset: dataset.id, cutoff });
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
        <button
          disabled={busy || !cutoff || !target}
          onClick={() =>
            void run(async () => {
              const result = await api<Forecast>("/forecasts", {
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
            Обновите список или подготовьте экспериментальный расчёт. Прогноз
            по похожим дням можно получить без подготовки при достаточной истории.
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
              {model.status === "ready" ? "Расчёт готов" : "Расчёт недоступен"}
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
            {model.unit === "item_line" ? "строки продаж" : "физические штуки"}.
          </p>
          <p className="muted">
            Готовность означает возможность расчёта, а не доказанную точность.
          </p>
          {model.training_cutoff !== cutoff && (
            <p className="muted">
              Срез этой модели отличается от выбранного. Сервер проверит
              совместимость перед использованием.
            </p>
          )}
          <h4>Готовность по товарам</h4>
          <ul>
            {Object.entries(model.products ?? {}).map(([product, status]) => (
              <li key={product}>
                {product}: {status.status === "ready" ? "готова" : "не готова"}
                {status.reason ? ` · ${reasonText(status.reason)}` : ""}
              </li>
            ))}
          </ul>
          <ModelEvaluation
            procurement={dataset.planning_kind === "procurement"}
            evaluation={model.evaluation}
            unitLabel={unitLabel}
          />
        </article>
      ))}
      {forecast && (
        <ForecastResult
          procurement={dataset.planning_kind === "procurement"}
          snapshot={forecast}
          stale={forecastInputsChanged(forecast, {
            target,
            cutoff,
            method: forecastMethod,
          })}
          unitLabel={unitLabel}
        />
      )}
    </section>
  );
}

export function ModelEvaluation({
  evaluation,
  procurement = false,
}: {
  procurement?: boolean;
  evaluation: ModelSummary["evaluation"];
  unitLabel: string;
}) {
  if (evaluation == null)
    return (
      <p className="status-note" data-testid="model-evaluation">
        Качество прогноза пока не проверено. Проверьте предложенные количества вручную.
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
    <div data-testid="model-evaluation" className={mlWorse || !comparable ? "evaluation-warning" : "status-note"}>
      <h4>Качество прогноза</h4>
      <p>
        {!comparable
          ? "Недостаточно данных для сравнения способов расчёта. Проверьте количество вручную."
          : mlWorse
            ? "Экспериментальный расчёт на проверке оказался менее точным. Предпочтите расчёт по похожим дням и проверьте количество."
            : "На прошлых данных экспериментальный расчёт прошёл проверку. Это не гарантия точности будущих продаж."}
      </p>
      {(procurement || evaluation.selection_performed) && (
        <p>Проверка использовалась для выбора способа расчёта. Она не является независимой оценкой точности.</p>
      )}
    </div>
  );
}
