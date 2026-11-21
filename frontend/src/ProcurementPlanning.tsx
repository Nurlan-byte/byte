import { useEffect, useState } from "react";
import type { PlanningSection } from "./PlanningSection";
import type { DeliverySource, ExpectedDelivery, Plan } from "./types";
import { ExpectedDeliveries } from "./ExpectedDeliveries";
import { DeliveryRegistry } from "./DeliveryRegistry";
import { api } from "./api";
import { businessDate } from "./dates";
import { dayWord } from "./dayWord";
import { stockAssumptions } from "./procurement";
import { reviewOrderItem } from "./orderReview";
import { MAX_QUANTITY, validQuantity } from "./quantities";
import { hasQuantityChanges } from "./workspace";
import { methodText, procurementNotes } from "./explanations";
import { Icon } from "./Icon";
import { SupplierOrderText } from "./SupplierOrderText";

export function ProcurementPlanning(p: Parameters<typeof PlanningSection>[0]) {
  const runReplacingPlan = p.runReplacingPlan ?? p.run;
  useEffect(() => {
    if (p.method === "B0") p.setMethod("B1");
  }, [p.method, p.setMethod]);
  const plan = p.plan?.planning_kind === "procurement" ? p.plan : null;
  const [reviewSelection, setReviewSelection] = useState<Set<string> | null>(
    null,
  );
  useEffect(() => setReviewSelection(null), [plan?.id, plan?.revision]);
  const dataset = p.dataset?.planning_kind === "procurement" ? p.dataset : null;
  const [lead, setLead] = useState(String(plan?.lead_time_days ?? ""));
  const [stock, setStock] = useState<Record<string, string>>({});
  const [alternateStock, setAlternateStock] = useState<Record<string, string>>(
    {},
  );
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
  useEffect(() => setAlternateStock({}), [dataset?.id, plan?.id]);
  const selectedDatasetDiffers =
    !!plan && !!dataset && dataset.id !== plan.dataset_id;
  const workingStock = selectedDatasetDiffers ? alternateStock : stock;
  const [deliveryEdit, setDeliveryEdit] = useState<ExpectedDelivery[] | null>(
    null,
  );
  const [sourceEdit, setSourceEdit] = useState<DeliverySource | null>(null);
  useEffect(() => {
    setDeliveryEdit(null);
    setSourceEdit(null);
  }, [dataset?.id, plan?.id, plan?.revision]);
  const replay = p.target <= businessDate();
  const savedSource = plan?.delivery_source ?? "manual";
  const source: DeliverySource = replay
    ? "manual"
    : (sourceEdit ??
      (plan && !selectedDatasetDiffers && p.target === plan.target_date
        ? savedSource
        : "registry"));
  const savedDeliveries = selectedDatasetDiffers
    ? []
    : (plan?.expected_deliveries ?? []);
  const deliveries = deliveryEdit ?? savedDeliveries;
  const deliveriesChanged =
    deliveries.length !== savedDeliveries.length ||
    deliveries.some((row, i) => {
      const saved = savedDeliveries[i];
      return (
        row.product_id !== saved.product_id ||
        row.arrival_date !== saved.arrival_date ||
        row.quantity !== saved.quantity
      );
    });
  let assumptions: Record<string, number> = {},
    inputError = "";
  try {
    assumptions = stockAssumptions(workingStock);
  } catch (e) {
    inputError = (e as Error).message;
  }
  if (
    source === "manual" &&
    deliveries.some(
      (row) =>
        !row.arrival_date ||
        !Number.isSafeInteger(row.quantity) ||
        row.quantity <= 0 ||
        row.quantity > MAX_QUANTITY,
    )
  ) {
    inputError =
      "Для каждой поставки укажите дату и целое количество от 1 до 1 000 000 000 шт.";
  }
  const leadValid = validQuantity(lead, 1, 365) && Number(lead) > 0;
  const sameStock =
    JSON.stringify(Object.entries(assumptions).sort()) ===
    JSON.stringify(Object.entries(plan?.usable_stock_assumption ?? {}).sort());
  const stale =
    !!plan &&
    (p.target !== plan.target_date ||
      p.cutoff !== plan.cutoff ||
      p.method !== plan.requested_method ||
      Number(lead) !== plan.lead_time_days ||
      (!selectedDatasetDiffers &&
        (!sameStock ||
          (sourceEdit !== null && source !== savedSource) ||
          (source === "manual" && deliveriesChanged) ||
          !!inputError)));
  const orderRows = (plan?.items ?? []).map((item) => {
    const value = p.quantities[item.product_id] ?? "";
    const stock =
      item.usable_stock_assumption === undefined
        ? plan?.usable_stock_assumption?.[item.product_id]
        : item.usable_stock_assumption;
    return { item, value, stock, ...reviewOrderItem(item, value, stock) };
  });
  const invalid = orderRows.some((row) => !row.valid);
  const reviewRows = orderRows.filter((row) => row.reasons.length > 0);
  // Keep corrected lines in the review until the operator leaves the filter.
  const visibleRows = reviewSelection
    ? orderRows.filter(
        (row) =>
          row.reasons.length > 0 || reviewSelection.has(row.item.product_id),
      )
    : orderRows;
  const dirty = hasQuantityChanges(plan, p.quantities);
  const generalNotes = plan?.items.length
    ? procurementNotes(plan.items[0]).slice(0, 2)
    : [];
  return (
    <section id="planning" aria-labelledby="planning-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">ЗАКАЗ НА ДЕНЬ</p>
          <h2 id="planning-heading">Сколько заказываем?</h2>
          <p className="section-copy">
            Закупаем готовое · прогноз продаж на целевую дату и отдельное
            решение о заказе.
          </p>
        </div>
        <span
          className={`badge ${plan?.status === "confirmed" ? "badge-success" : "badge-neutral"}`}
        >
          {plan
            ? plan.status === "confirmed"
              ? "Сохранён"
              : "Черновик"
            : "Новый заказ"}
        </span>
      </div>
      {!dataset && (
        <div className="status-note">
          <p>Выберите историю закупки для нового расчёта.</p>
          <button disabled={p.busy} onClick={p.onImport}>
            Перейти к данным
          </button>
        </div>
      )}
      {!!dataset?.catalog?.length && (
        <DeliveryRegistry
          key={dataset.id}
          datasetId={dataset.id}
          branch={dataset.bakery_id}
          catalog={dataset.catalog}
          run={p.run}
          disabled={p.busy}
        />
      )}
      <div className="controls planning-controls">
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
            max={365}
            step={1}
            value={lead}
            aria-invalid={!leadValid}
            disabled={p.busy}
            onChange={(e) => setLead(e.target.value)}
          />
          {lead !== "" && !leadValid && (
            <small className="field-error">
              Укажите срок от 1 до 365 дней.
            </small>
          )}
        </div>
        <details className="control-field">
          <summary>Настройки прогноза</summary>
          <label htmlFor="proc-method">Способ расчёта закупки</label>
          <select
            id="proc-method"
            value={p.method}
            disabled={p.busy}
            onChange={(e) => p.setMethod(e.target.value)}
          >
            <option value="B1">По похожим дням недели</option>
            <option value="ML">Экспериментальный расчёт</option>
          </select>
        </details>
      </div>
      <div className="data-guide stock-guide">
        <h3>Допущение о доступном остатке</h3>
        <p>
          Укажите, сколько единиц останется к открытию в день продаж, включая
          уже принятый товар. Ожидаемые поступления BakePlan добавит отдельно.
        </p>
        <p className="stock-rule">
          Пустое поле = остаток неизвестен: автоматический заказ отключён,
          рекомендация 0 означает ручное решение. Явный 0 означает отсутствие
          пригодного остатка.
        </p>
        <details>
          <summary>Как остаток влияет на заказ</summary>
          <p>
            Предложенный заказ учитывает прогноз продаж, указанный остаток,
            упаковку и лимит. Проверьте, что товар успеет поступить к дате
            продаж. Прогноз относится к одному дню, а не ко всему сроку
            поставки.
          </p>
          <p>
            Историческая доступность товара и дефицит могут быть неизвестны.
            Прогноз описывает наблюдавшиеся продажи и не оценивает весь
            неудовлетворённый спрос.
          </p>
        </details>
        {!!dataset?.catalog?.length && (
          <div className="stock-grid">
            {dataset.catalog.map((i) => (
              <div className="control-field" key={i.product_id}>
                <label htmlFor={`stock-${i.product_id}`}>
                  Доступный остаток: {i.name}
                </label>
                <input
                  id={`stock-${i.product_id}`}
                  type="number"
                  min={0}
                  max={MAX_QUANTITY}
                  step={1}
                  placeholder="Неизвестен"
                  value={workingStock[i.product_id] ?? ""}
                  disabled={p.busy}
                  onChange={(e) =>
                    selectedDatasetDiffers
                      ? setAlternateStock({
                          ...alternateStock,
                          [i.product_id]: e.target.value,
                        })
                      : setStock({ ...stock, [i.product_id]: e.target.value })
                  }
                />
              </div>
            ))}
          </div>
        )}
        {!!dataset?.catalog?.length && !replay && (
          <label className="control-field">
            Источник поставок
            <select
              value={source}
              disabled={p.busy}
              onChange={(e) => setSourceEdit(e.target.value as DeliverySource)}
            >
              <option value="registry">Реестр филиала</option>
              <option value="manual">Ввести для этого заказа</option>
            </select>
          </label>
        )}
        {!!dataset?.catalog?.length &&
          (source === "registry" ? (
            <p>
              В расчёт войдёт оставшееся в пути количество из реестра филиала{" "}
              {dataset.bakery_id} со сроком от даты заказа до начала дня продаж.
            </p>
          ) : (
            <>
              {replay && (
                <p>
                  Для исторического расчёта введите поставки, которые ожидались
                  на дату заказа.
                </p>
              )}
              <ExpectedDeliveries
                catalog={dataset.catalog}
                deliveries={deliveries}
                onChange={setDeliveryEdit}
                target={p.target}
                disabled={p.busy}
              />
            </>
          ))}
      </div>
      {inputError && (
        <p role="alert" className="field-error">
          {inputError}
        </p>
      )}
      {dirty && (
        <p className="status-note">
          Есть несохранённые изменения заказа. Новый расчёт заменит их.
        </p>
      )}
      <button
        className="cta"
        disabled={
          p.busy ||
          !dataset ||
          !p.target ||
          !p.cutoff ||
          !leadValid ||
          !!inputError
        }
        onClick={() =>
          void runReplacingPlan(async () =>
            p.show(
              await api<Plan>("/plans", {
                dataset_id: dataset?.id,
                target_date: p.target,
                cutoff: p.cutoff,
                method: p.method,
                mode: replay ? "replay" : "operational",
                lead_time_days: Number(lead),
                delivery_source: source,
                ...(source === "manual"
                  ? { expected_deliveries: deliveries }
                  : {}),
                usable_stock_assumption: Object.fromEntries(
                  Object.entries(assumptions).filter(([productId]) =>
                    dataset?.catalog?.some(
                      (item) => item.product_id === productId,
                    ),
                  ),
                ),
              }),
            ),
          )
        }
      >
        Рассчитать заказ
      </button>
      {plan && (
        <>
          <div className="plan-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">ПРОВЕРКА ЗАКАЗА</p>
                <h3>
                  Заказ от {plan.order_date} · продажи {plan.target_date}
                </h3>
              </div>
              <span
                className={`badge ${dirty ? "badge-warning" : plan.status === "confirmed" ? "badge-success" : "badge-neutral"}`}
              >
                {dirty
                  ? "Есть несохранённые изменения"
                  : plan.status === "confirmed"
                    ? "Подтверждён"
                    : "Ожидает проверки"}
              </span>
            </div>
            <p>
              Источник: {plan.provenance || plan.source} ·{" "}
              {plan.status === "confirmed" ? "Подтверждён" : "Черновик"}
            </p>
            <p>
              Сохранённый срок поставки: {plan.lead_time_days}{" "}
              {dayWord(plan.lead_time_days ?? 0)}. Срез: {plan.cutoff}.
            </p>
            {plan.delivery_source === "registry" && (
              <p>
                Поставки из реестра филиала {plan.bakery_id} зафиксированы на
                момент расчёта.
              </p>
            )}
          </div>
          {selectedDatasetDiffers && (
            <p
              className="status-note"
              data-testid="saved-procurement-dataset-notice"
            >
              Сохранённый заказ относится к другому набору данных. Подтверждение
              использует сохранённый снимок заказа; новый расчёт использует
              выбранный сейчас набор данных.
            </p>
          )}
          {stale && (
            <p className="stale-result" data-testid="stale-plan">
              Параметры изменены. Пересчитайте заказ перед подтверждением.
            </p>
          )}
          <details className="quantity-help">
            <summary>Как читать заказ</summary>
            <div className="quantity-guide">
              <p>
                <strong>Прогноз продаж</strong> — ожидаемые продажи за один
                день, без округления к упаковке.
              </p>
              <p>
                <strong>Предложенный заказ</strong> — прогноз за вычетом
                указанного остатка, кратно упаковке и не выше лимита.
              </p>
              <p>
                <strong>Подтверждённый заказ</strong> — ваше решение. Оно
                сохраняется только после кнопки «Подтвердить заказ».
              </p>
            </div>
            {generalNotes.map((note) => (
              <p className="muted" key={note}>
                {note}
              </p>
            ))}
          </details>
          <div
            className="order-review-filter"
            role="group"
            aria-label="Позиции заказа"
          >
            <button
              type="button"
              className="secondary"
              aria-pressed={!reviewSelection}
              onClick={() => setReviewSelection(null)}
            >
              Все позиции ({orderRows.length})
            </button>
            <button
              type="button"
              className="secondary"
              aria-pressed={!!reviewSelection}
              onClick={() =>
                setReviewSelection(
                  new Set(reviewRows.map((row) => row.item.product_id)),
                )
              }
            >
              Требуют проверки ({reviewRows.length})
            </button>
          </div>
          {reviewSelection && (
            <p className="muted" role="status">
              {reviewRows.length === 0
                ? "Все позиции проверены."
                : "Исправленные позиции останутся в списке до переключения."}
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
                  <th scope="col">Товар</th>
                  <th scope="col">Прогноз продаж, шт.</th>
                  <th scope="col">Предложенный заказ, шт.</th>
                  <th scope="col">Подтверждённый заказ, шт.</th>
                  <th scope="col">Что учесть</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => {
                  const {
                    item: i,
                    value,
                    stock,
                    valid,
                    reasons,
                    coverage,
                  } = row;
                  // The saved cap warning concerns the suggested amount. Review reasons use the current edit.
                  const itemNotes = procurementNotes({
                    ...i,
                    max_constraint_active: false,
                  }).slice(2);
                  const pack = i.pack_size ?? 1;
                  return (
                    <tr key={i.product_id}>
                      <td>
                        <strong>{i.name}</strong>
                        <small>
                          Упаковка {pack} · лимит{" "}
                          {i.max_order_qty ?? "не задан"}
                        </small>
                        <small>
                          Допущение об остатке:{" "}
                          {stock ?? "неизвестен — ручное решение"}
                        </small>
                        {!!i.incoming_units && (
                          <small>
                            В пути к дате продаж: {i.incoming_units} шт.
                          </small>
                        )}
                        {plan.registry_deliveries
                          ?.filter(
                            (delivery) => delivery.product_id === i.product_id,
                          )
                          .map((delivery) => (
                            <small
                              key={delivery.id}
                              data-delivery-id={delivery.id}
                            >
                              № {delivery.number} · {delivery.arrival_date} ·{" "}
                              {delivery.quantity} шт.
                            </small>
                          ))}
                      </td>
                      <td
                        className="numeric-cell forecast-cell"
                        data-label="Прогноз продаж, шт."
                      >
                        {i.forecast_mean == null
                          ? "Нет оценки"
                          : i.forecast_mean.toFixed(1)}
                      </td>
                      <td
                        className="numeric-cell recommendation-cell"
                        data-label="Предложенный заказ, шт."
                      >
                        {i.recommended_qty}
                        {i.recommended_packs != null && (
                          <small>{i.recommended_packs} уп.</small>
                        )}
                      </td>
                      <td
                        className="confirmation-cell"
                        data-label="Подтверждённый заказ, шт."
                      >
                        <label
                          className="sr-only"
                          htmlFor={`order-${i.product_id}`}
                        >
                          Заказ: {i.name}
                        </label>
                        <input
                          id={`order-${i.product_id}`}
                          type="number"
                          min={0}
                          step={pack}
                          max={
                            i.target_is_open === false
                              ? 0
                              : Math.min(
                                  i.max_order_qty ?? MAX_QUANTITY,
                                  MAX_QUANTITY,
                                )
                          }
                          disabled={p.busy || stale}
                          aria-invalid={!valid}
                          aria-describedby={`order-help-${i.product_id}${reasons.length ? " order-review-" + i.product_id : ""}`}
                          value={value}
                          onChange={(e) =>
                            p.setQuantities({
                              ...p.quantities,
                              [i.product_id]: e.target.value,
                            })
                          }
                        />
                        <small id={`order-help-${i.product_id}`}>
                          {coverage?.packs != null
                            ? `${coverage.packs} уп. по ${pack} шт.`
                            : "Целое количество, кратное упаковке и не выше лимита."}
                        </small>
                        <small>
                          {i.confirmed_qty == null
                            ? "Ещё не подтверждён"
                            : `Сохранено: ${i.confirmed_qty}`}
                        </small>
                        {i.confirmed_qty != null &&
                          value !== String(i.confirmed_qty) && (
                            <small className="unsaved">
                              Есть несохранённое изменение
                            </small>
                          )}
                      </td>
                      <td>
                        {reasons.length > 0 ? (
                          <ul
                            className="order-review-reasons"
                            id={`order-review-${i.product_id}`}
                          >
                            {reasons.map((reason) => (
                              <li key={reason}>{reason}</li>
                            ))}
                          </ul>
                        ) : (
                          reviewSelection && (
                            <small className="order-reviewed">Проверено</small>
                          )
                        )}
                        <strong className="actual-method">
                          Основа: {methodText(i.method)}
                        </strong>
                        {itemNotes.length > 0 && (
                          <details>
                            <summary>О расчёте</summary>
                            {itemNotes.map((w, n) => (
                              <small key={n}>{w}</small>
                            ))}
                          </details>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {invalid && (
            <p role="alert" className="field-error">
              Исправьте количества заказа: соблюдайте упаковку и лимит.
            </p>
          )}
          <div className="confirm-bar">
            <div>
              <strong>
                {stale
                  ? "Сначала обновите расчёт"
                  : invalid
                    ? "Проверьте введённые количества"
                    : dirty
                      ? "Сохраните проверенные изменения"
                      : plan.status === "confirmed"
                        ? "Заказ сохранён. Можно отправлять поставщику"
                        : "Подтвердите заказ"}
              </strong>
              <p>Проверьте остаток, упаковки и срок поставки.</p>
            </div>
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
                href={
                  p.busy
                    ? undefined
                    : `/api/plans/${encodeURIComponent(plan.id)}/export?revision=${plan.revision}`
                }
                aria-disabled={p.busy || undefined}
                download
              >
                <Icon name="download" />
                Экспорт заказа CSV
              </a>
            </div>
          </div>
          {plan.status === "confirmed" &&
            !dirty &&
            !stale &&
            !invalid &&
            !p.busy && (
              <SupplierOrderText
                key={`${plan.id}-${plan.revision}`}
                plan={plan}
              />
            )}
          <p className="muted">
            CSV содержит только сохранённые значения. Несохранённые изменения не
            попадут в экспорт.
          </p>
        </>
      )}
    </section>
  );
}
