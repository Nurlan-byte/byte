import { api } from "./api";
import type { Plan } from "./types";
import type { SectionProps } from "./sectionProps";
import { validQuantity } from "./quantities";
import { businessDate } from "./dates";
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
  return (
    <section>
      <h2>2. Расчёт и ручная проверка</h2>
      <p>
        Срез включает только завершённые дни строго раньше сегодня. При задержке
        учёта сдвиньте его назад: время фактической готовности данных не
        проверяется.
      </p>
      <div className="controls">
        <label>
          Целевой день открыт
          <input
            type="checkbox"
            checked={targetOpen}
            onChange={(e) => setTargetOpen(e.target.checked)}
          />
        </label>
        <label>
          Дата плана
          <input
            type="date"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </label>
        <label>
          Данные доступны до
          <input
            type="date"
            value={cutoff}
            onChange={(e) => setCutoff(e.target.value)}
          />
        </label>
        <label>
          Базовый метод
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            <option value="B1">B1 · до 4 прошлых таких дней недели</option>
            <option value="B0">B0 · тот же день неделю назад</option>
          </select>
        </label>
      </div>
      <button
        disabled={busy || !dataset}
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
          <p>
            План на <strong>{plan.target_date}</strong> · {plan.source} ·{" "}
            {plan.mode === "replay"
              ? "Историческое воспроизведение"
              : "Операционный план"}{" "}
            · {plan.status} · версия {plan.revision}
          </p>
          <div className="table">
            <table>
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
                    <td>{i.recommended_qty}</td>
                    <td>
                      <input
                        aria-label={`Количество ${i.name}`}
                        type="number"
                        disabled={busy}
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
                      {i.method}
                      <small>
                        Подходящих наблюдений:{" "}
                        {i.n ?? i.used_dates?.length ?? 0}
                      </small>
                      {i.used_dates?.length ? (
                        <small>Даты: {i.used_dates.join(", ")}</small>
                      ) : null}
                      {i.warnings?.map((w, n) => (
                        <small key={n}>{w}</small>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            disabled={busy || invalid}
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
                await refreshHistory();
              })
            }
          >
            Подтвердить и сохранить
          </button>{" "}
          <a href={`/api/plans/${plan.id}/export`} download>
            Экспорт CSV
          </a>
        </>
      )}
    </section>
  );
}
