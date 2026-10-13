import { api } from "./api";
import type { Plan } from "./types";
import type { SectionProps } from "./sectionProps";
import { validQuantity } from "./quantities";
import { businessDate } from "./dates";
import { planInputsChanged } from "./snapshots";
import { methodText, reasonText } from "./explanations";
import { EvaluationNotice } from "./EvaluationNotice";
export function PlanningSection({
  dataset,
  plan,
  busy,
  setNotice,
  target,
  setTarget,
  cutoff,
  setCutoff,
  method,
  setMethod,
  targetOpen,
  setTargetOpen,
  quantities,
  setQuantities,
  confirmation,
  invalid,
  run,
  show,
  refreshHistory,
}: Pick<
  SectionProps,
  | "dataset"
  | "plan"
  | "busy"
  | "setNotice"
  | "target"
  | "setTarget"
  | "cutoff"
  | "setCutoff"
  | "method"
  | "setMethod"
  | "targetOpen"
  | "setTargetOpen"
  | "quantities"
  | "setQuantities"
  | "confirmation"
  | "invalid"
  | "run"
  | "show"
  | "refreshHistory"
>) {
  if (dataset?.target === "item_line_count" || dataset?.unit === "item_line") {
    return (
      <aside className="sales-only" data-testid="sales-only-notice">
        Строки продаж не преобразуются в физические штуки. Для производственного
        плана импортируйте отдельную историю sold_units / physical_unit с
        балансом.
      </aside>
    );
  }
  const stale =
    plan != null &&
    planInputsChanged(plan, { target, cutoff, method, targetOpen });
  const closed =
    plan?.items.length &&
    plan.items.every((item) => item.target_is_open === false);
  const calendarKnown = plan?.items.some((item) => item.target_is_open != null);
  return (
    <section id="planning" aria-labelledby="planning-heading">
      <h2 id="planning-heading">2. Расчёт и ручная проверка</h2>
      <p>
        B1 работает без обучения. ML — необязательный эксперимент; фактический
        метод и переход на B1 будут показаны для каждого товара.
      </p>
      <p>
        Срез включает только завершённые дни строго раньше сегодня. При задержке
        учёта сдвиньте его назад: время фактической готовности данных не
        проверяется.
      </p>
      <div className="controls">
        <label>
          Целевой день открыт
          <input
            disabled={busy}
            type="checkbox"
            checked={targetOpen}
            onChange={(e) => setTargetOpen(e.target.checked)}
          />
        </label>
        <label>
          Дата плана
          <input
            disabled={busy}
            type="date"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </label>
        <label>
          Данные доступны до
          <input
            disabled={busy}
            type="date"
            value={cutoff}
            onChange={(e) => setCutoff(e.target.value)}
          />
        </label>
        <label>
          Базовый метод
          <select
            disabled={busy}
            value={method}
            onChange={(e) => setMethod(e.target.value)}
          >
            <option value="B1">B1 · до 4 прошлых таких дней недели</option>
            <option value="ML">
              ML · последняя подходящая модель, иначе B1
            </option>
            <option value="B0">B0 · тот же день неделю назад</option>
          </select>
        </label>
      </div>
      <button
        disabled={busy || !dataset || !target || !cutoff}
        onClick={() =>
          void run(async () =>
            show(
              await api<Plan>("/plans", {
                dataset_id: dataset?.id,
                target_date: target,
                cutoff,
                method,
                mode: target <= businessDate() ? "replay" : "operational",
                target_is_open: targetOpen,
              }),
            ),
          )
        }
      >
        Рассчитать план
      </button>
      {!dataset && <p className="muted">Сначала импортируйте CSV.</p>}
      {plan && (
        <>
          <div className="plan-heading">
            <h3>Проверьте выпуск перед подтверждением</h3>
            <p>
              План на <strong>{plan.target_date}</strong> · {plan.source} ·{" "}
              {plan.mode === "replay"
                ? "Историческое воспроизведение"
                : "Операционный план"}{" "}
              · {plan.status} · версия {plan.revision}
            </p>
            <p>
              Срез: {plan.cutoff} · выбранный метод:{" "}
              {plan.requested_method ?? "не сохранён в старом плане"} · день{" "}
              {calendarKnown
                ? closed
                  ? "закрыт"
                  : "открыт"
                : "не указан в старом плане"}
              .
            </p>
          </div>
          {stale && (
            <aside data-testid="stale-plan" className="stale-result">
              Параметры изменены. Ниже показан прежний план на{" "}
              {plan.target_date}, срез {plan.cutoff}. Нажмите «Рассчитать план»
              заново или верните прежние параметры. Подтверждение пока
              недоступно.
            </aside>
          )}
          {dataset && dataset.id !== plan.dataset_id && (
            <p className="muted">
              Открыт план другого сохранённого набора. Новый расчёт использует
              текущий набор из раздела импорта.
            </p>
          )}
          <div className="quantity-guide">
            <p>
              <strong>Прогноз</strong> — оценка продаж, без округления к партии.
            </p>
            <p>
              <strong>Рекомендация</strong> — допустимая партия. При ручном
              режиме 0 — заготовка, не прогноз нулевого спроса.
            </p>
            <p>
              <strong>Подтверждение</strong> — ваш выпуск. Изменение поля
              сохраняется только после кнопки «Подтвердить и сохранить».
            </p>
          </div>
          <details>
            <summary>Технические данные плана</summary>
            <p>
              План: {plan.id} · набор плана: {plan.dataset_id}
            </p>
            <p>
              Версия данных: {plan.dataset_version ?? "не указана"} ·{" "}
              {plan.target ?? "sold_units"} / {plan.unit ?? "physical_unit"}
            </p>
            {plan.model_id && (
              <p>
                Снимок модели: {plan.model_id} · протокол:{" "}
                {plan.protocol_version ?? "не указан"}
              </p>
            )}
          </details>
          <div className="table">
            <table data-testid="plan-table">
              <thead>
                <tr>
                  <th>Товар</th>
                  <th>Прогноз, шт.</th>
                  <th>Рекомендация</th>
                  <th>Подтверждение, шт.</th>
                  <th>Метод и ограничения</th>
                </tr>
              </thead>
              <tbody>
                {plan.items.map((i) => (
                  <tr key={i.product_id}>
                    <td>
                      <strong>{i.name}</strong>
                      <small>
                        {i.product_id} · партия {i.batch_size} · максимум{" "}
                        {Math.floor(i.max_daily_qty / i.batch_size) *
                          i.batch_size}
                      </small>
                    </td>
                    <td>
                      {i.forecast_mean == null
                        ? "Нет оценки"
                        : i.forecast_mean.toFixed(1)}
                    </td>
                    <td>
                      {i.recommended_qty}
                      {i.automatic_eligible === false && (
                        <small>
                          {i.target_is_open === false
                            ? "Закрытый день"
                            : "Ручная заготовка"}
                        </small>
                      )}
                    </td>
                    <td>
                      <input
                        aria-label={`Количество ${i.name}`}
                        type="number"
                        disabled={busy || stale || i.target_is_open === false}
                        min="0"
                        max={
                          Math.floor(i.max_daily_qty / i.batch_size) *
                          i.batch_size
                        }
                        step={i.batch_size}
                        value={quantities[i.product_id] ?? ""}
                        onChange={(e) =>
                          setQuantities({
                            ...quantities,
                            [i.product_id]: e.target.value,
                          })
                        }
                      />
                      <small>
                        {i.confirmed_qty == null
                          ? "Ещё не сохранено"
                          : `Сохранено: ${i.confirmed_qty} шт.`}
                      </small>
                      {i.confirmed_qty != null &&
                        quantities[i.product_id] !==
                          String(i.confirmed_qty) && (
                          <small className="unsaved">
                            Есть несохранённое изменение
                          </small>
                        )}
                      {!validQuantity(
                        quantities[i.product_id] ?? "",
                        i.batch_size,
                        i.max_daily_qty,
                      ) && (
                        <small className="error">
                          Введите целое число, кратное партии, в пределах
                          максимума.
                        </small>
                      )}
                    </td>
                    <td>
                      <strong>{methodText(i.method)}</strong>
                      {i.diagnostic_method &&
                        i.diagnostic_method !== i.method && (
                          <small>
                            Метод диагностического прогноза:{" "}
                            {i.diagnostic_method}. Автоматический выпуск
                            отключён.
                          </small>
                        )}
                      {i.fallback_reason && (
                        <small>
                          Причина перехода: {reasonText(i.fallback_reason)}
                        </small>
                      )}
                      {i.warnings?.map((w, n) => (
                        <small key={n}>{w}</small>
                      ))}
                      <details className="item-details">
                        <summary>Источники и модель</summary>
                        {i.model_id && (
                          <small>Снимок модели: {i.model_id}</small>
                        )}
                        <small>
                          Подходящих наблюдений:{" "}
                          {i.n ?? i.used_dates?.length ?? 0}
                        </small>
                        {!!i.used_dates?.length && (
                          <small>Даты: {i.used_dates.join(", ")}</small>
                        )}
                        {i.fallback_reason && (
                          <small>
                            Код или исходная причина: {i.fallback_reason}
                          </small>
                        )}
                        {i.evaluation_applicability && (
                          <EvaluationNotice
                            value={i.evaluation_applicability}
                          />
                        )}
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            disabled={busy || invalid || stale}
            onClick={() =>
              void run(async () => {
                show(
                  await api<Plan>(`/plans/${plan.id}/confirm`, {
                    revision: plan.revision,
                    idempotency_key: (() => {
                      const signature = JSON.stringify({
                        id: plan.id,
                        revision: plan.revision,
                        quantities,
                      });
                      if (confirmation.current?.signature !== signature)
                        confirmation.current = {
                          signature,
                          key: crypto.randomUUID(),
                        };
                      return confirmation.current!.key;
                    })(),
                    quantities: Object.fromEntries(
                      Object.entries(quantities).map(([k, v]) => [
                        k,
                        Number(v),
                      ]),
                    ),
                  }),
                );
                setNotice("Подтверждённый план сохранён в базе.");
                try {
                  await refreshHistory();
                } catch {
                  setNotice(
                    "Подтверждённый план сохранён в базе. Не удалось обновить список истории; нажмите «Обновить историю» позже.",
                  );
                }
              })
            }
          >
            Подтвердить и сохранить
          </button>{" "}
          <a href={`/api/plans/${plan.id}/export`} download>
            Экспорт CSV
          </a>
          <p className="muted">
            CSV содержит сохранённую редакцию этого плана.{" "}
            {plan.status === "confirmed"
              ? "Несохранённые правки в файл не попадут."
              : "Пока это черновик: подтверждённые количества ещё не записаны."}
          </p>
        </>
      )}
    </section>
  );
}
