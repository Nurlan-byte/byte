import { useEffect, useState } from "react";
import type { PlanningSection } from "./PlanningSection";
import type { Plan } from "./types";
import { api } from "./api";
import { businessDate } from "./dates";
import { stockAssumptions } from "./procurement";
import { validQuantity } from "./quantities";
import { hasQuantityChanges } from "./workspace";

export function ProcurementPlanning(p: Parameters<typeof PlanningSection>[0]) {
  useEffect(() => {
    if (p.method === "B0") p.setMethod("B1");
  }, [p.method, p.setMethod]);
  const plan = p.plan?.planning_kind === "procurement" ? p.plan : null;
  const dataset = p.dataset?.planning_kind === "procurement" ? p.dataset : null;
  const [lead, setLead] = useState(String(plan?.lead_time_days ?? ""));
  const [stock, setStock] = useState<Record<string, string>>({});
  useEffect(() => {
    setLead(String(plan?.lead_time_days ?? ""));
    setStock(
      Object.fromEntries(
        Object.entries(plan?.usable_stock_assumption ?? {}).map(([k, v]) => [
          k,
          String(v),
        ]),
      ),
    );
  }, [plan?.id, plan?.lead_time_days, plan?.usable_stock_assumption]);
  let assumptions: Record<string, number> = {},
    inputError = "";
  try {
    assumptions = stockAssumptions(stock);
  } catch (e) {
    inputError = (e as Error).message;
  }
  const leadValid =
    /^\d+$/.test(lead) &&
    Number.isSafeInteger(Number(lead)) &&
    Number(lead) > 0;
  const sameStock =
    JSON.stringify(Object.entries(assumptions).sort()) ===
    JSON.stringify(Object.entries(plan?.usable_stock_assumption ?? {}).sort());
  const stale =
    !!plan &&
    (p.target !== plan.target_date ||
      p.cutoff !== plan.cutoff ||
      p.method !== plan.requested_method ||
      Number(lead) !== plan.lead_time_days ||
      !sameStock ||
      !!inputError ||
      (!!dataset && dataset.id !== plan.dataset_id));
  const invalid = plan?.items.some(
    (i) =>
      !validQuantity(
        p.quantities[i.product_id] ?? "",
        i.pack_size ?? 1,
        i.max_order_qty ?? Number.MAX_SAFE_INTEGER,
      ),
  );
  const dirty = hasQuantityChanges(plan, p.quantities);
  return (
    <section id="planning" aria-labelledby="planning-heading">
      <h2 id="planning-heading">Сколько заказываем?</h2>
      <p>
        Закупаем готовое · прогноз продаж на целевую дату и отдельное решение о
        заказе.
      </p>
      {!dataset && (
        <div className="status-note">
          <p>Выберите историю закупки для нового расчёта.</p>
          <button disabled={p.busy} onClick={p.onImport}>
            Перейти к данным
          </button>
        </div>
      )}
      <div className="controls">
        <div className="control-field">
          <label htmlFor="proc-date">Дата продаж</label>
          <input
            id="proc-date"
            type="date"
            value={p.target}
            disabled={p.busy}
            onChange={(e) => p.setTarget(e.target.value)}
          />
        </div>
        <div className="control-field">
          <label htmlFor="proc-cutoff">Данные доступны до</label>
          <input
            id="proc-cutoff"
            type="date"
            value={p.cutoff}
            disabled={p.busy}
            onChange={(e) => p.setCutoff(e.target.value)}
          />
        </div>
        <div className="control-field">
          <label htmlFor="proc-lead">Срок поставки, дней</label>
          <input
            id="proc-lead"
            type="number"
            min={1}
            step={1}
            value={lead}
            aria-invalid={!leadValid}
            disabled={p.busy}
            onChange={(e) => setLead(e.target.value)}
          />
        </div>
        <div className="control-field">
          <label htmlFor="proc-method">Способ расчёта закупки</label>
          <select
            id="proc-method"
            value={p.method}
            disabled={p.busy}
            onChange={(e) => p.setMethod(e.target.value)}
          >
            <option value="B1">По дням недели (B1)</option>
            <option value="ML">Экспериментальная модель (ML)</option>
          </select>
        </div>
      </div>
      <div className="data-guide">
        <h3>Допущение о доступном остатке</h3>
        <p>
          Укажите пригодный к продаже остаток на целевую дату, учитывая
          ожидаемые поступления и выбытия. Исторический остаток не переносится
          автоматически.
        </p>
        <p>
          Пустое поле = остаток неизвестен: автоматический заказ отключён,
          рекомендация 0 означает ручное решение. Явный 0 означает отсутствие
          пригодного остатка.
        </p>
        <p>
          Заказ = max(0, прогноз продаж − доступный остаток), округление вверх
          до упаковки с учётом лимита заказа. Дата заказа — сегодня; для
          исторического воспроизведения — следующий день после среза данных.
          Дата продаж должна учитывать срок поставки. Прогноз не суммируется за
          срок поставки.
        </p>
        <p>
          Историческая доступность товара и дефицит могут быть неизвестны.
          Прогноз описывает наблюдавшиеся продажи и не оценивает весь
          неудовлетворённый спрос.
        </p>
        {(dataset?.catalog ?? []).map((i) => (
          <div className="control-field" key={i.product_id}>
            <label htmlFor={`stock-${i.product_id}`}>
              Доступный остаток: {i.name}
            </label>
            <input
              id={`stock-${i.product_id}`}
              type="number"
              min={0}
              step={1}
              placeholder="Неизвестен"
              value={stock[i.product_id] ?? ""}
              disabled={p.busy}
              onChange={(e) =>
                setStock({ ...stock, [i.product_id]: e.target.value })
              }
            />
          </div>
        ))}
      </div>
      {inputError && <p role="alert">{inputError}</p>}
      {dirty && (
        <p className="status-note">
          Есть несохранённые изменения заказа. Новый расчёт заменит их.
        </p>
      )}
      <button
        disabled={
          p.busy ||
          !dataset ||
          !p.target ||
          !p.cutoff ||
          !leadValid ||
          !!inputError
        }
        onClick={() =>
          void p.run(async () =>
            p.show(
              await api<Plan>("/plans", {
                dataset_id: dataset?.id,
                target_date: p.target,
                cutoff: p.cutoff,
                method: p.method,
                mode: p.target <= businessDate() ? "replay" : "operational",
                lead_time_days: Number(lead),
                usable_stock_assumption: assumptions,
              }),
            ),
          )
        }
      >
        Рассчитать заказ
      </button>
      {plan && (
        <>
          <h3>
            Заказ от {plan.order_date} · продажи {plan.target_date}
          </h3>
          <p>
            Источник: {plan.source} · набор: {plan.dataset_id} · редакция{" "}
            {plan.revision} ·{" "}
            {plan.status === "confirmed" ? "Подтверждён" : "Черновик"}
          </p>
          {plan.provenance && (
            <p>Происхождение сохранённого плана: {plan.provenance}</p>
          )}
          <p>
            Сохранённый срок поставки: {plan.lead_time_days} дней. Срез:{" "}
            {plan.cutoff}.
          </p>
          {stale && (
            <p className="stale-result" data-testid="stale-plan">
              Параметры изменены. Пересчитайте заказ перед подтверждением.
            </p>
          )}
          <div
            className="table plan-table-scroll"
            role="region"
            aria-label="Прогноз продаж и заказ"
            tabIndex={0}
          >
            <table data-testid="procurement-table">
              <thead>
                <tr>
                  <th>Товар</th>
                  <th>Прогноз продаж, шт.</th>
                  <th>Предложенный заказ, шт.</th>
                  <th>Подтверждённый заказ, шт.</th>
                </tr>
              </thead>
              <tbody>
                {plan.items.map((i) => (
                  <tr key={i.product_id}>
                    <td>
                      <strong>{i.name}</strong>
                      <small>
                        Упаковка {i.pack_size ?? 1} · лимит{" "}
                        {i.max_order_qty ?? "не задан"}
                      </small>
                      <small>
                        Допущение об остатке:{" "}
                        {plan.usable_stock_assumption?.[i.product_id] ??
                          "неизвестен — ручное решение"}
                      </small>
                    </td>
                    <td data-label="Прогноз продаж, шт.">
                      {i.forecast_mean == null
                        ? "Нет оценки"
                        : i.forecast_mean.toFixed(1)}
                      <small>Метод: {i.method}</small>
                    </td>
                    <td data-label="Предложенный заказ, шт.">
                      {i.recommended_qty}
                      {i.warnings.map((w, n) => (
                        <small key={n}>{w}</small>
                      ))}
                    </td>
                    <td data-label="Подтверждённый заказ, шт.">
                      <label htmlFor={`order-${i.product_id}`}>
                        Заказ: {i.name}
                      </label>
                      <input
                        id={`order-${i.product_id}`}
                        type="number"
                        min={0}
                        step={i.pack_size ?? 1}
                        max={i.max_order_qty ?? undefined}
                        disabled={p.busy || stale}
                        aria-invalid={
                          !validQuantity(
                            p.quantities[i.product_id] ?? "",
                            i.pack_size ?? 1,
                            i.max_order_qty ?? Number.MAX_SAFE_INTEGER,
                          )
                        }
                        value={p.quantities[i.product_id] ?? ""}
                        onChange={(e) =>
                          p.setQuantities({
                            ...p.quantities,
                            [i.product_id]: e.target.value,
                          })
                        }
                      />
                      <small>
                        {i.confirmed_qty == null
                          ? "Ещё не подтверждён"
                          : `Сохранено: ${i.confirmed_qty}`}
                      </small>
                      <small>
                        Целое количество, кратное упаковке и не выше лимита.
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {invalid && (
            <p role="alert">
              Исправьте количества заказа: соблюдайте упаковку и лимит.
            </p>
          )}
          <div className="actions">
            <button
              disabled={p.busy || stale || invalid}
              onClick={() =>
                void p.run(async () => {
                  const signature = JSON.stringify([
                    plan.id,
                    plan.revision,
                    p.quantities,
                  ]);
                  if (p.confirmation.current?.signature !== signature)
                    p.confirmation.current = {
                      signature,
                      key: crypto.randomUUID(),
                    };
                  p.show(
                    await api<Plan>(`/plans/${plan.id}/confirm`, {
                      revision: plan.revision,
                      idempotency_key: p.confirmation.current.key,
                      quantities: Object.fromEntries(
                        Object.entries(p.quantities).map(([k, v]) => [
                          k,
                          Number(v),
                        ]),
                      ),
                    }),
                  );
                  p.setNotice("Заказ подтверждён и сохранён.");
                  try {
                    await p.refreshHistory();
                  } catch {
                    p.setNotice(
                      "Заказ сохранён. Обновите историю, чтобы увидеть его в списке.",
                    );
                  }
                })
              }
            >
              Подтвердить заказ
            </button>
            <a
              className="button-link secondary"
              href={p.busy ? undefined : `/api/plans/${plan.id}/export`}
              download
            >
              Экспорт заказа CSV
            </a>
          </div>
          <p>
            CSV содержит только сохранённые значения. Несохранённые изменения не
            попадут в экспорт.
          </p>
        </>
      )}
    </section>
  );
}
