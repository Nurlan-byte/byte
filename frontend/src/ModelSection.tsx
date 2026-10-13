import { useRef, useState } from "react";
import { api } from "./api";
import type { Dataset, Forecast, ModelSummary } from "./types";
import type { SectionProps } from "./sectionProps";
import { ForecastResult, type ForecastSnapshot } from "./ForecastResult";
import { forecastInputsChanged } from "./snapshots";
import { reasonText } from "./explanations";

type Props = Pick<
  SectionProps,
  "busy" | "cutoff" | "setCutoff" | "target" | "setTarget" | "run" | "setNotice"
> & { dataset: Dataset };
export function ModelSection({
  dataset,
  busy,
  cutoff,
  setCutoff,
  target,
  setTarget,
  run,
  setNotice,
}: Props) {
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [forecast, setForecast] = useState<ForecastSnapshot | null>(null);
  const [forecastMethod, setForecastMethod] = useState("ML");
  const training = useRef<{ signature: string; key: string } | null>(null);
  const salesOnly = dataset.target === "item_line_count";
  const unitLabel = salesOnly ? "строк продаж" : "шт.";

  return (
    <section
      id="model"
      aria-labelledby="model-heading"
      data-testid="model-panel"
    >
      <h2 id="model-heading">Модель и диагностический прогноз</h2>
      {!salesOnly && (
        <p className="optional-step">
          Необязательный шаг. Для первого плана можно сразу перейти к расчёту с
          B1 ниже.
        </p>
      )}
      <p>
        ML использует последний подходящий снимок модели для этого набора и
        среза. Если модель или признаки недоступны, применяется B1 с явной
        причиной. Обучение выполняется локально, один запрос может занять до 60
        секунд.
      </p>
      <p className="muted">
        {salesOnly
          ? "Sales-only: оцениваем строки продаж, а не количество изделий. Производственные партии, рекомендации выпуска и подтверждение недоступны."
          : "Диагностический прогноз не назначает выпуск. Производственный план ниже требует отдельной ручной проверки."}{" "}
        Синтетические примеры проверяют работу программы. Превосходство ML,
        точность на реальной пекарне и экономия не доказаны.
      </p>
      {dataset.metadata_declared === false && (
        <p className="error">
          Этот старый набор не содержит явно объявленных метаданных. Для
          обучения повторно импортируйте исходный CSV, указав пекарню, величину,
          единицу и происхождение.
        </p>
      )}
      <div className="controls">
        <label>
          Срез обучения и прогноза
          <input
            type="date"
            disabled={busy}
            value={cutoff}
            onChange={(e) => setCutoff(e.target.value)}
          />
        </label>
        <label>
          Дата прогноза
          <input
            type="date"
            disabled={busy}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </label>
        <div className="control-field">
          <label htmlFor="diagnostic-method">Метод диагностики</label>
          <select
            id="diagnostic-method"
            disabled={busy}
            value={forecastMethod}
            onChange={(e) => setForecastMethod(e.target.value)}
          >
            <option value="ML">ML · с явным переходом на B1</option>
            <option value="B1">B1 · базовый ориентир</option>
          </select>
        </div>
      </div>
      <div className="actions">
        <button
          disabled={busy || !cutoff || dataset.metadata_declared === false}
          onClick={() =>
            void run(async () => {
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
              setNotice(
                model.status === "ready"
                  ? "Модель сохранена. Это не подтверждение качества; проверьте оценку и фактический метод прогноза."
                  : "Обучение завершено: модель не готова. Причины показаны ниже; доступен B1.",
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
              setModels(
                await api<ModelSummary[]>(`/datasets/${dataset.id}/models`),
              );
              setLoaded(true);
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
      {!models.length && (
        <p className="muted">
          {loaded
            ? "Сохранённых моделей пока нет."
            : "Обучите модель или обновите список сохранённых моделей."}
        </p>
      )}
      {models.map((model) => (
        <article
          key={model.id}
          className="model-card"
          data-testid="model-summary"
        >
          <h3>
            {model.status === "ready" ? "Модель готова" : "Модель не готова"}
          </h3>
          {model.reason && <p>Причина: {reasonText(model.reason)}</p>}
          <p>
            Срез обучения: {model.training_cutoff} ·{" "}
            {model.unit === "item_line" ? "строки продаж" : "физические штуки"}.
          </p>
          <p className="muted">
            Готовность означает возможность расчёта, а не преимущество перед B1.
          </p>
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
            {model.reason && <p>Код причины: {model.reason}</p>}
          </details>
          {model.training_cutoff !== cutoff && (
            <p className="muted">
              Срез этой модели отличается от выбранного. Сервер проверит
              совместимость перед использованием.
            </p>
          )}
          <ul>
            {Object.entries(model.products ?? {}).map(([product, status]) => (
              <li key={product}>
                {product}: {status.status === "ready" ? "готова" : "не готова"}
                {status.reason ? ` · ${reasonText(status.reason)}` : ""}
              </li>
            ))}
          </ul>
          {model.evaluation != null ? (
            <details>
              <summary>Диагностика оценки модели</summary>
              <p className="muted">
                Результаты относятся только к указанным данным и временному
                протоколу. Они не доказывают превосходство ML на будущих
                реальных продажах.
              </p>
              <p>
                Обучение для оценки до:{" "}
                {model.evaluation.fit_end ?? "нет данных"}. Отложенный период:{" "}
                {model.evaluation.holdout_start ?? "нет данных"} —{" "}
                {model.evaluation.holdout_end ?? "нет данных"}.
              </p>
              <p>
                Горизонт оценки:{" "}
                {model.evaluation.horizon_days == null
                  ? "не указан"
                  : `${model.evaluation.horizon_days} дн. от среза до целевой даты`}
                .
                {model.evaluation.horizon_days === 2 &&
                  " Используется история не позже t−2; это соответствует паре «завтра / вчера»."}
              </p>
              <p>
                Сопоставимые даты:{" "}
                {model.evaluation.paired_start ?? "нет данных"} —{" "}
                {model.evaluation.paired_end ?? "нет данных"} · дат:{" "}
                {model.evaluation.paired_date_count ?? "не указано"}.
              </p>
              <p>
                Сопоставимых прогнозов: {model.evaluation.paired_n ?? 0} ·
                покрытие:{" "}
                {model.evaluation.coverage == null
                  ? "нет данных"
                  : `${(model.evaluation.coverage * 100).toFixed(1)}%`}
                . Доля рассчитана от{" "}
                {model.evaluation.coverage_denominator_n ??
                  "неуказанного числа"}{" "}
                пригодных наблюдаемых меток отложенного периода.
              </p>
              <p>
                Средняя абсолютная ошибка B1:{" "}
                {model.evaluation.b1_mae == null
                  ? "нет оценки"
                  : model.evaluation.b1_mae.toFixed(2)}{" "}
                · ML:{" "}
                {model.evaluation.ml_mae == null
                  ? "нет оценки"
                  : model.evaluation.ml_mae.toFixed(2)}{" "}
                {unitLabel}.
              </p>
              <details>
                <summary>Полный протокол оценки</summary>
                <pre>{JSON.stringify(model.evaluation, null, 2)}</pre>
              </details>
            </details>
          ) : (
            <p className="muted">Оценка качества не предоставлена.</p>
          )}
        </article>
      ))}
      {forecast && (
        <ForecastResult
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
