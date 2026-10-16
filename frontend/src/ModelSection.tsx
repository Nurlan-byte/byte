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
              ? "Прогноз продаж для закупки"
              : "Модель и диагностический прогноз"}
          </h2>
        </div>
        <span className="badge">Необязательный шаг</span>
      </div>
      <p className="section-copy">
        Сравните ошибку экспериментальной модели (ML) и расчёта по дням недели
        (B1) на прошлых данных, прежде чем выбирать метод. Готовая модель ещё не
        означает более точный прогноз.
      </p>
      {!salesOnly && (
        <p className="status-note">
          Для первого плана обучение не нужно. По умолчанию план использует B1;
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
        Синтетические примеры проверяют работу программы. Превосходство ML,
        точность на реальных данных и экономия не доказаны.
      </p>
      <details>
        <summary>Как работает прогноз</summary>
        <p>
          ML использует последний подходящий снимок модели для этого набора и
          среза. Если модель или признаки недоступны, применяется B1 с явной
          причиной. Без подходящей истории оценка не подменяется нулём. Обучение
          выполняется локально, один запрос может занять до 60 секунд.
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
          <label htmlFor="diagnostic-method">Метод диагностики</label>
          <select
            id="diagnostic-method"
            disabled={busy}
            value={forecastMethod}
            onChange={(e) => setForecastMethod(e.target.value)}
          >
            <option value="ML">Экспериментальная модель (ML)</option>
            <option value="B1">По похожим дням недели (B1)</option>
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
                  : "Обучение завершено: модель не готова. Причины показаны ниже; при подходящей истории доступен B1.",
              );
            })
          }
        >
          Обучить модель
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
          Обновить модели
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
                "Диагностический прогноз готов. Фактический метод и причины перехода на B1 показаны по каждому товару.",
              );
            })
          }
        >
          Получить прогноз
        </button>
      </div>
      {loadError && (
        <p role="alert" className="error">
          {loadError}
        </p>
      )}
      {!models.length && (
        <div className="status-note">
          <strong>
            {loaded
              ? "Сохранённых моделей пока нет"
              : "Модели ещё не загружены"}
          </strong>
          <p>
            Обновите список или обучите модель для этого набора. Прогноз B1
            можно получить без обучения, если достаточно подходящей истории.
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
              {model.status === "ready" ? "Модель готова" : "Модель не готова"}
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
            Готовность означает возможность расчёта, а не преимущество перед B1.
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
          <details>
            <summary>Технические данные модели</summary>
            <p>
              Снимок модели: {model.id} · протокол: {model.protocol_version}
            </p>
            <p>
              Набор: {model.dataset_id} · версия данных: {model.dataset_version}
            </p>
            <p>
              Пекарня: {model.bakery_id} · {model.target} / {model.unit}
            </p>
            {"snapshot_hash" in model &&
              typeof model.snapshot_hash === "string" && (
                <p>Хеш снимка данных: {model.snapshot_hash}</p>
              )}
            {model.reason && <p>Код причины: {model.reason}</p>}
          </details>
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
  unitLabel,
  procurement = false,
}: {
  procurement?: boolean;
  evaluation: ModelSummary["evaluation"];
  unitLabel: string;
}) {
  if (evaluation == null)
    return (
      <p className="status-note">
        Оценка качества не предоставлена. Пока нельзя сравнить точность ML и B1
        для этой модели.
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
  const comparison = !comparable
    ? "Недостаточно сопоставимых данных для вывода о точности ML и B1."
    : mlWorse
      ? "На этой проверке ML хуже B1: его средняя ошибка выше."
      : ml < b1
        ? "На этой проверке средняя ошибка ML ниже, чем у B1."
        : "На этой проверке средняя ошибка ML и B1 одинакова.";
  const formatError = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value)
      ? "Нет оценки"
      : value.toLocaleString("ru-RU", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 6,
        });

  return (
    <div data-testid="model-evaluation">
      <h4>
        {procurement || evaluation.selection_performed
          ? "Внутренняя валидация выбора ML/B1"
          : "Проверка на отложенных данных"}
      </h4>
      {(procurement || evaluation.selection_performed) && (
        <p className="status-note">
          Эти данные использованы для выбора между ML и B1. Метрики описывают
          внутреннюю валидацию и не являются независимой оценкой точности.
        </p>
      )}
      <p className="muted">
        MAE — средняя абсолютная ошибка прогноза. Чем ниже, тем лучше. Оба
        метода сравниваются на одних и тех же наблюдениях.
      </p>
      <div className="metric-grid">
        <div className="metric-card">
          <span>Ошибка базового расчёта</span>
          <strong>{formatError(b1)}</strong>
          <small>MAE B1 · {unitLabel}</small>
        </div>
        <div className="metric-card">
          <span>Ошибка модели</span>
          <strong>{formatError(ml)}</strong>
          <small>MAE ML · {unitLabel}</small>
        </div>
        <div className="metric-card">
          <span>Общие наблюдения</span>
          <strong>{evaluation.paired_n ?? "Нет данных"}</strong>
          <small>Парное N · сопоставимых прогнозов</small>
        </div>
        <div className="metric-card">
          <span>Покрытие</span>
          <strong>
            {evaluation.paired_n != null &&
            evaluation.coverage_denominator_n != null
              ? `${evaluation.paired_n}/${evaluation.coverage_denominator_n}`
              : "Нет данных"}
          </strong>
          <small>
            {evaluation.coverage == null
              ? "Доля не указана"
              : `${(evaluation.coverage * 100).toFixed(1)}% пригодных меток`}
          </small>
        </div>
      </div>
      <p
        className={
          mlWorse || !comparable ? "evaluation-warning" : "status-note"
        }
      >
        <strong>{comparison}</strong>{" "}
        {mlWorse && "Оснований предпочесть ML по этой оценке нет. "}
        Это результат только для указанных данных и периода, а не доказательство
        точности будущих реальных продаж или экономии.
      </p>
      <details>
        <summary>Диагностика оценки модели</summary>
        <p>
          Обучение для оценки до: {evaluation.fit_end ?? "нет данных"}.
          Отложенный период: {evaluation.holdout_start ?? "нет данных"} —{" "}
          {evaluation.holdout_end ?? "нет данных"}.
        </p>
        <p>
          Горизонт оценки:{" "}
          {evaluation.horizon_days == null
            ? "не указан"
            : `${evaluation.horizon_days} дн. от среза до целевой даты`}
          .
          {evaluation.horizon_days === 2 &&
            " Используется история не позже t−2; это соответствует паре «завтра / вчера»."}
        </p>
        <p>
          Сопоставимые даты: {evaluation.paired_start ?? "нет данных"} —{" "}
          {evaluation.paired_end ?? "нет данных"} · дат:{" "}
          {evaluation.paired_date_count ?? "не указано"}.
        </p>
        <p>
          Покрытие показывает долю сопоставимых прогнозов от{" "}
          {evaluation.coverage_denominator_n ?? "неуказанного числа"} пригодных
          наблюдаемых меток отложенного периода. Пропуски не считаются нулевыми
          продажами.
        </p>
        <details>
          <summary>Полный протокол оценки</summary>
          <pre>{JSON.stringify(evaluation, null, 2)}</pre>
        </details>
      </details>
    </div>
  );
}
