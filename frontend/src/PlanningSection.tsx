import { useEffect, useRef } from "react";
import { ProcurementPlanning } from "./ProcurementPlanning";
import { PlanStart } from "./PlanStart";
import { Icon } from "./Icon";
import { createPlan, confirmPlan } from "./api";
import type { Plan } from "./types";
import type { SectionProps } from "./sectionProps";
import { validQuantity } from "./quantities";
import { businessDate } from "./dates";
import { planInputsChanged } from "./snapshots";
import { methodText, reasonText } from "./explanations";
import { EvaluationNotice } from "./EvaluationNotice";
import {
  forPlanningKind,
  hasQuantityChanges,
  productActiveOnTarget,
} from "./workspace";
import { formatDatasetPeriod } from "./datasetSummary";
type PlanningProps = Pick<
  SectionProps,
  | "planningKind"
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
  | "runReplacingPlan"
  | "show"
  | "refreshHistory"
> & { onImport?: () => void; onHistory?: () => void; onForecast?: () => void };

export type PlanWorkflowProps = PlanningProps & {
  showCalculated: (plan: Plan) => void;
};

export function PlanningSection(props: PlanningProps) {
  const dataset = forPlanningKind(props.dataset, props.planningKind);
  const plan = forPlanningKind(props.plan, props.planningKind);
  const focusResult = useRef<string | null>(null);
  useEffect(() => {
    if (plan && focusResult.current === plan.id) {
      focusResult.current = null;
      document.getElementById("plan-review-heading")?.focus();
    }
  }, [plan]);

  if (!dataset && !plan) {
    return (
      <PlanStart
        procurement={props.planningKind === "procurement"}
        busy={props.busy}
        onImport={props.onImport}
        onHistory={props.onHistory}
      />
    );
  }
  if (dataset?.forecast_only && !plan) {
    return (
      <section className="sales-only" data-testid="sales-only-notice">
        <h2>Прогноз по данным кассы</h2>
        <p>
          {dataset.unit === "physical_unit"
            ? "Продажи в штуках уже загружены. Из прогноза можно перейти к заказу и указать упаковки."
            : "Доступен прогноз строк чеков. Для заказа нужны количества в штуках."}
        </p>
        <button onClick={props.onForecast} disabled={props.busy}>
          Открыть прогноз продаж
        </button>
      </section>
    );
  }
  function showCalculated(result: Plan) {
    focusResult.current = result.id;
    props.show(result);
  }
  const selected = {
    ...props,
    dataset:
      props.planningKind === "procurement" && dataset?.forecast_only
        ? null
        : dataset,
    plan,
    showCalculated,
  };
  return props.planningKind === "procurement" ? (
    <ProcurementPlanning key={dataset?.id ?? ""} {...selected} />
  ) : (
    <ProductionPlanning {...selected} />
  );
}

function ProductionPlanning({
  onImport,
  onHistory,
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
  runReplacingPlan = run,
  show,
  showCalculated,
  refreshHistory,
}: PlanWorkflowProps) {
  const salesOnly =
    dataset?.forecast_only ||
    dataset?.target === "item_line_count" ||
    dataset?.unit === "item_line";
  if (salesOnly && (!plan || dataset.id === plan.dataset_id)) {
    return (
      <aside className="sales-only" data-testid="sales-only-notice">
        <strong>
          {dataset?.forecast_only
            ? "Для выпуска нужны остатки готовых изделий"
            : "Для выпуска нужна история продаж в штуках"}
        </strong>
        <p>
          {dataset?.forecast_only && dataset.unit === "physical_unit"
            ? "Прогноз продаж доступен в разделе качества. Для расчёта выпуска добавьте остатки и размеры партий."
            : "Строки продаж не преобразуются в физические штуки. Добавьте историю с остатками и количеством проданных изделий."}
        </p>
        <button className="secondary" disabled={busy} onClick={onImport}>
          Перейти к данным
        </button>
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
  const dirty = hasQuantityChanges(plan, quantities);
  const replay = target <= businessDate();
  return (
    <section
      id="planning"
      className="plan-workbench"
      aria-labelledby="planning-heading"
    >
      <div className="section-heading">
        <div>
          <h2 id="planning-heading">Сколько выпекаем?</h2>
          <p className="section-copy">
            Укажите дату выпуска и последний день, по которому есть продажи.
          </p>
        </div>
        {plan && (
          <span
            className={`badge ${plan.status === "confirmed" ? "badge-success" : "badge-neutral"}`}
          >
            {plan.status === "confirmed" ? "Сохранён" : "Черновик"}
          </span>
        )}
      </div>
      {dirty && (
        <p className="status-note">
          Новый расчёт заменит несохранённые поля ниже. Сначала подтвердите
          текущие правки, если хотите их сохранить.
        </p>
      )}
      <div className="controls planning-controls production-controls">
        <div className="control-field">
          <label htmlFor="plan-date">Дата плана</label>
          <input
            id="plan-date"
            disabled={busy}
            type="date"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </div>
        <div className="control-field">
          <label htmlFor="plan-cutoff">Данные доступны до</label>
          <input
            id="plan-cutoff"
            disabled={busy}
            type="date"
            value={cutoff}
            onChange={(e) => setCutoff(e.target.value)}
          />
        </div>
        <button
          className="cta"
          disabled={busy || !dataset || salesOnly || !target || !cutoff}
          onClick={() => {
            if (!dataset) return;
            void runReplacingPlan(async () =>
              showCalculated(
                await createPlan({
                  dataset_id: dataset.id,
                  target_date: target,
                  cutoff,
                  method,
                  mode: target <= businessDate() ? "replay" : "operational",
                  target_is_open: targetOpen,
                }),
              ),
            );
          }}
        >
          Рассчитать план
        </button>
        <div className="control-field calendar-field">
          <label htmlFor="plan-open">Целевой день открыт</label>
          <div className="checkbox-line">
            <input
              id="plan-open"
              disabled={busy}
              type="checkbox"
              checked={targetOpen}
              onChange={(e) => setTargetOpen(e.target.checked)}
            />
            <span>{targetOpen ? "Выпекаем" : "Выпуск 0"}</span>
          </div>
        </div>
        <details className="control-field">
          <summary>Настройки прогноза</summary>
          <label htmlFor="planning-method">Способ расчёта</label>
          <select
            id="planning-method"
            disabled={busy}
            value={method}
            onChange={(e) => {
              const value = e.target.value;
              if (value === "B1" || value === "B0" || value === "ML") setMethod(value);
            }}
          >
            <option value="B1">По похожим дням недели</option>
            <option value="ML">Экспериментальный расчёт</option>
            <option value="B0">По прошлой неделе</option>
          </select>
        </details>
      </div>
      {replay && !plan && (
        <aside className="replay-notice">
          <strong>Историческое воспроизведение</strong>
          <span>
            План для прошедшей или текущей даты сохранится с этой меткой.
          </span>
        </aside>
      )}
      {salesOnly && (
        <aside className="sales-only" data-testid="sales-only-notice">
          Открыт сохранённый план выпуска. Для нового расчёта выберите историю
          продаж в штуках с балансом остатков.
        </aside>
      )}
      <details className="plan-source-help">
        <summary>Какие продажи учитываются?</summary>
        {dataset && (
          <p className="control-hint">
            Период выбранной истории: {formatDatasetPeriod(dataset)}. Новые дни
            после этой истории не добавляются автоматически.
          </p>
        )}
        <p className="control-hint">
          История показывает продажи, но не показывает, хватало ли товара всем
          покупателям. Отсутствие прогноза не означает нулевые продажи.
        </p>
      </details>
      {!dataset && plan && (
        <p className="muted">
          Открыт сохранённый план. Для нового расчёта сначала загрузите набор
          данных.
        </p>
      )}
      {plan && (
        <>
          <div className="plan-heading">
            <div className="section-heading">
              <div>
                <h3 id="plan-review-heading" tabIndex={-1}>
                  Ваш план на {plan.target_date}
                </h3>
              </div>
              <div className="review-state">
                <span
                  className={`badge ${dirty ? "badge-warning" : plan.status === "confirmed" ? "badge-success" : "badge-neutral"}`}
                >
                  {dirty
                    ? "Есть несохранённые изменения"
                    : plan.status === "confirmed"
                      ? "Подтверждён"
                      : "Ожидает проверки"}
                </span>
                <a href="#plan-confirm">К подтверждению</a>
              </div>
            </div>
            <p>
              {plan.source === "synthetic"
                ? "Синтетические данные"
                : plan.source === "local"
                  ? "Данные пекарни"
                  : "Внешние данные"}{" "}
              ·{" "}
              {plan.mode === "replay"
                ? "Историческое воспроизведение"
                : "Операционный план"}{" "}
              · версия {plan.revision}
            </p>
            <p>
              Срез: {plan.cutoff} · выбранный метод:{" "}
              {plan.requested_method
                ? methodText(plan.requested_method)
                : "не сохранён в старом плане"}{" "}
              · день{" "}
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
            <p className="status-note">
              Открыт план другого сохранённого набора. Новый расчёт использует
              текущий набор из раздела импорта.
            </p>
          )}
          <details className="quantity-help">
            <summary>Как читать план</summary>
            <div className="quantity-guide">
              <p>
                <strong>Прогноз</strong> — оценка продаж, без округления к
                партии.
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
          </details>
          <p className="table-hint">
            Прогноз — ожидаемые продажи. Рекомендация учитывает партии. Ваш
            выпуск сохраняется только после подтверждения.
          </p>
          <div
            id="plan-items"
            className="table plan-table-scroll"
            tabIndex={0}
            role="region"
            aria-label="План по изделиям: прогноз, рекомендация и ваш выпуск"
          >
            <table data-testid="plan-table">
              <thead>
                <tr>
                  <th scope="col">Изделие</th>
                  <th scope="col">Прогноз, шт.</th>
                  <th scope="col">Рекомендация, шт.</th>
                  <th scope="col">Ваш выпуск, шт.</th>
                  <th scope="col">Что учесть</th>
                </tr>
              </thead>
              <tbody>
                {plan.items.map((i) => i.batch_size == null || i.max_daily_qty == null ? (
                  <tr key={i.product_id}>
                    <td>{i.name}</td>
                    <td data-label="Прогноз, шт.">{i.forecast_mean?.toFixed(1) ?? "Нет оценки"}</td>
                    <td data-label="Рекомендация, шт.">{i.recommended_qty}</td>
                    <td data-label="Ваш выпуск, шт.">{i.confirmed_qty ?? "Ещё не сохранено"}</td>
                    <td>Для выпуска нужны размер партии и дневной лимит.</td>
                  </tr>
                ) : (
                  <tr key={i.product_id}>
                    <td>
                      <strong>{i.name}</strong>
                      <small>
                        Партия {i.batch_size} шт. · максимум{" "}
                        {Math.floor(i.max_daily_qty / i.batch_size) *
                          i.batch_size}
                      </small>
                    </td>
                    <td
                      className="numeric-cell forecast-cell"
                      data-label="Прогноз, шт."
                    >
                      {i.forecast_mean == null
                        ? "Нет оценки"
                        : i.forecast_mean.toFixed(1)}
                    </td>
                    <td
                      className="numeric-cell recommendation-cell"
                      data-label="Рекомендация, шт."
                    >
                      {i.recommended_qty}
                      {i.automatic_eligible === false && (
                        <small>
                          {!productActiveOnTarget(i, plan.target_date)
                            ? "Выпуск запрещён"
                            : i.target_is_open === false
                              ? "Закрытый день"
                              : "Ручная заготовка"}
                        </small>
                      )}
                    </td>
                    <td
                      className="confirmation-cell"
                      data-label="Ваш выпуск, шт."
                    >
                      <label
                        className="sr-only"
                        htmlFor={`quantity-${i.product_id}`}
                      >
                        Количество {i.name}
                      </label>
                      <input
                        id={`quantity-${i.product_id}`}
                        type="number"
                        disabled={
                          busy ||
                          stale ||
                          i.target_is_open === false ||
                          !productActiveOnTarget(i, plan.target_date)
                        }
                        min="0"
                        max={
                          Math.floor(i.max_daily_qty / i.batch_size) *
                          i.batch_size
                        }
                        step={i.batch_size}
                        aria-invalid={
                          !validQuantity(
                            quantities[i.product_id] ?? "",
                            i.batch_size,
                            i.max_daily_qty,
                          )
                        }
                        aria-describedby={`quantity-help-${i.product_id}`}
                        value={quantities[i.product_id] ?? ""}
                        onChange={(e) =>
                          setQuantities({
                            ...quantities,
                            [i.product_id]: e.target.value,
                          })
                        }
                      />
                      {(i.target_is_open === false ||
                        !productActiveOnTarget(i, plan.target_date)) &&
                        quantities[i.product_id] !== "0" && (
                          <div>
                            <small>
                              Товар или день закрыт для этой даты. Для
                              подтверждения старого плана установите 0.
                            </small>
                            <button
                              type="button"
                              disabled={busy || stale}
                              onClick={() =>
                                setQuantities({
                                  ...quantities,
                                  [i.product_id]: "0",
                                })
                              }
                            >
                              Установить 0 для {i.name}
                            </button>
                          </div>
                        )}
                      <small id={`quantity-help-${i.product_id}`}>
                        Шаг {i.batch_size}, до{" "}
                        {Math.floor(i.max_daily_qty / i.batch_size) *
                          i.batch_size}{" "}
                        шт. (предел {i.max_daily_qty})
                      </small>
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
                      {i.confirmed_qty == null &&
                        quantities[i.product_id] !==
                          String(i.recommended_qty) && (
                          <small className="unsaved">
                            Изменено вручную · не сохранено
                          </small>
                        )}
                      {!validQuantity(
                        quantities[i.product_id] ?? "",
                        i.batch_size,
                        i.max_daily_qty,
                      ) && (
                        <small className="field-error">
                          Введите 0 или кратное {i.batch_size}, не больше{" "}
                          {Math.floor(i.max_daily_qty / i.batch_size) *
                            i.batch_size}{" "}
                          шт.
                        </small>
                      )}
                    </td>
                    <td>
                      <strong className="actual-method">
                        {methodText(i.method)}
                      </strong>
                      {i.diagnostic_method &&
                        i.diagnostic_method !== i.method && (
                          <small>
                            Автоматический выпуск отключён. Проверьте количество
                            вручную.
                          </small>
                        )}
                      {i.fallback_reason && (
                        <small>
                          Использован упрощённый прогноз.{" "}
                          {reasonText(i.fallback_reason)}
                        </small>
                      )}
                      {i.warnings?.map((w, n) => (
                        <small key={n}>{w}</small>
                      ))}
                      {i.evaluation_applicability &&
                        !i.evaluation_applicability.applicable && (
                          <EvaluationNotice
                            value={i.evaluation_applicability}
                          />
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="confirm-bar" id="plan-confirm" tabIndex={-1}>
            <div>
              <strong>
                {stale
                  ? "Сначала обновите расчёт"
                  : invalid
                    ? "Проверьте введённые количества"
                    : dirty
                      ? "Сохраните проверенные изменения"
                      : plan.status === "confirmed"
                        ? "План сохранён. Можно передать в работу"
                        : "Подтвердите выпуск"}
              </strong>
              <p>Проверьте партии, ограничения и общую загрузку печи.</p>
              <a href="#plan-items">К товарам</a>
            </div>
            <div className="actions">
              <button
                disabled={busy || invalid || stale}
                onClick={() =>
                  void run(async () => {
                    show(
                      await confirmPlan(plan.id, {
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
              <a
                className="button-link secondary"
                href={
                  busy
                    ? undefined
                    : `/api/plans/${encodeURIComponent(plan.id)}/export?revision=${plan.revision}`
                }
                aria-disabled={busy || undefined}
                download
              >
                <Icon name="download" />
                Экспорт CSV
              </a>
              {plan.status === "confirmed" && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={onHistory}
                >
                  Открыть историю
                </button>
              )}
            </div>
          </div>
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
