import { useRef, useState } from "react";
import { api } from "./api";
import type { Dataset, Forecast, ModelSummary } from "./types";
import type { SectionProps } from "./sectionProps";

type Props = Pick<
  SectionProps,
  "busy" | "cutoff" | "setCutoff" | "target" | "setTarget" | "run" | "setNotice"
> & { dataset: Dataset };
type ForecastSnapshot = {
  result: Forecast;
  cutoff: string;
  targetDate: string;
};

function reasonText(reason: string) {
  const reasons: Record<string, string> = {
    insufficient_history_dates:
      "Недостаточно дат истории для временной проверки",
    insufficient_training_feature_rows:
      "Недостаточно пригодных строк с признаками",
    no_holdout_pairs: "Нет сопоставимых наблюдений на отложенном периоде",
    no_ready_products: "Нет товаров с готовой моделью",
    estimator_failure: "Не удалось обучить модель",
  };
  return reasons[reason] ? `${reasons[reason]} (${reason})` : reason;
}

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
    <section aria-labelledby="model-heading" data-testid="model-panel">
      <h2 id="model-heading">Модель и диагностический прогноз</h2>
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
        <label>
          Метод диагностики
          <select
            disabled={busy}
            value={forecastMethod}
            onChange={(e) => setForecastMethod(e.target.value)}
          >
            <option value="ML">ML · с явным переходом на B1</option>
            <option value="B1">B1 · базовый ориентир</option>
          </select>
        </label>
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
              setForecast({ result, cutoff, targetDate: target });
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
            Снимок модели: {model.id} · протокол: {model.protocol_version}
          </p>
          <p>
            Набор: {model.dataset_id} · версия данных: {model.dataset_version}
          </p>
          <p>
            Пекарня: {model.bakery_id} · срез обучения: {model.training_cutoff}{" "}
            · {model.target} / {model.unit}
          </p>
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
                Сопоставимых прогнозов: {model.evaluation.paired_n ?? 0} ·
                покрытие:{" "}
                {model.evaluation.coverage == null
                  ? "нет данных"
                  : `${(model.evaluation.coverage * 100).toFixed(1)}%`}
                .
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
              <pre>{JSON.stringify(model.evaluation, null, 2)}</pre>
            </details>
          ) : (
            <p className="muted">Оценка качества не предоставлена.</p>
          )}
        </article>
      ))}
      {forecast && (
        <div data-testid="forecast-result">
          <h3>Диагностический прогноз на {forecast.targetDate}</h3>
          <p>
            Срез: {forecast.cutoff} · набор: {forecast.result.dataset_id} ·
            пекарня: {forecast.result.bakery_id}
          </p>
          <p>
            Цель: {forecast.result.target} · единица: {forecast.result.unit} ·
            снимок модели: {forecast.result.model_id ?? "не использован"}
          </p>
          <div className="table">
            <table data-testid="forecast-table">
              <thead>
                <tr>
                  <th>Товар</th>
                  <th>Прогноз, {unitLabel}</th>
                  <th>B1, {unitLabel}</th>
                  <th>Фактический метод и ограничения</th>
                </tr>
              </thead>
              <tbody>
                {forecast.result.items.map((item) => (
                  <tr key={item.product_id}>
                    <td>{item.product_id}</td>
                    <td>
                      {item.forecast_mean == null
                        ? "Нет оценки"
                        : item.forecast_mean.toFixed(1)}
                    </td>
                    <td>
                      {item.b1_mean == null
                        ? "Нет оценки"
                        : item.b1_mean.toFixed(1)}
                    </td>
                    <td>
                      {item.method}
                      {item.fallback_reason && (
                        <small>Причина перехода: {item.fallback_reason}</small>
                      )}
                      <small>
                        Подходящих наблюдений:{" "}
                        {item.n ?? item.used_dates?.length ?? 0}
                      </small>
                      {!!item.used_dates?.length && (
                        <small>Даты: {item.used_dates.join(", ")}</small>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
