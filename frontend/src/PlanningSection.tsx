import { Icon } from "./Icon";
import { api } from "./api";
import type { Plan } from "./types";
import type { SectionProps } from "./sectionProps";
import { validQuantity } from "./quantities";
import { businessDate } from "./dates";
import { planInputsChanged } from "./snapshots";
import { methodText, reasonText } from "./explanations";
import { EvaluationNotice } from "./EvaluationNotice";
import { hasQuantityChanges, productActiveOnTarget } from "./workspace";
import { formatDatasetPeriod } from "./datasetSummary";
export function PlanningSection({
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
> & { onImport?: () => void; onHistory?: () => void }) {
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
  const dirty = hasQuantityChanges(plan, quantities);
  const replay = target <= businessDate();
  if (!dataset && !plan)
    return (
      <section
        id="planning"
        aria-labelledby="planning-heading"
        className="welcome"
      >
        <span className="empty-icon">
          <Icon name="bread" />
        </span>
        <p className="eyebrow">ВАШ ПЕРВЫЙ ПЛАН</p>
        <h2 id="planning-heading">Подготовим выпечку на день</h2>
        <p>
          Добавьте историю продаж. BakePlan предложит количество для каждого
          изделия, а вы проверите и сохраните свой выпуск.
        </p>
        <button disabled={busy} onClick={onImport}>
          Перейти к данным <Icon name="arrow" />
        </button>
        <p className="muted">
          Нет файла под рукой? В «Данных» можно попробовать синтетический
          пример.
        </p>
        <div className="welcome-next">
          <strong>Что дальше</strong>
          <p>
            Выберите дату → получите план → поправьте количества → подтвердите.
            Сохранённый план можно открыть в истории и скачать в CSV.
          </p>
        </div>
        <button className="text-button" disabled={busy} onClick={onHistory}>
          Открыть сохранённые планы
        </button>
      </section>
    );
  return (
    <section id="planning" aria-labelledby="planning-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">ПЛАН НА ДЕНЬ</p>
          <h2 id="planning-heading">Сколько выпекаем?</h2>
          <p className="section-copy">
            Рассчитайте ориентир, проверьте партии и подтвердите свой выпуск.
          </p>
        </div>
        <span className="badge badge-neutral">
          {plan
            ? plan.status === "confirmed"
              ? "Сохранён"
              : "Черновик"
            : "Новый план"}
        </span>
      </div>
      {replay && !plan && (
        <aside className="replay-notice">
          <strong>Историческое воспроизведение</strong>
          <span>
            Выбрана прошедшая или текущая дата. Расчёт и подтверждение будут
            сохранены с меткой replay.
          </span>
        </aside>
      )}
      <div className="controls planning-controls">
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
        <div className="control-field">
          <label htmlFor="planning-method">Способ расчёта</label>
          <select
            id="planning-method"
            disabled={busy}
            value={method}
            onChange={(e) => setMethod(e.target.value)}
          >
            <option value="B1">По похожим дням недели (B1)</option>
            <option value="ML">Экспериментальная модель (ML)</option>
            <option value="B0">По прошлой неделе (B0)</option>
          </select>
        </div>
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
      </div>
      <p className="control-hint">
        Обычно планируем завтра по данным до вчера включительно: разрыв — два
        дня (t−2). Базовый расчёт по дням недели работает без обучения. При
        задержке учёта выберите более раннюю дату данных.
      </p>
      {dataset && (
        <p className="control-hint">
          Период выбранной истории: {formatDatasetPeriod(dataset)}. Новые дни
          после этой истории не добавляются автоматически.
        </p>
      )}
      <details>
        <summary>Подробнее о способах расчёта</summary>
        <p>
          B1 — среднее до четырёх таких же дней недели за 56 дней. B0 — тот же
          день неделю назад. ML — экспериментальная поправка к B1; если модель
          недоступна, используется B1 с явной причиной. Отсутствие подходящей
          истории означает отсутствие оценки, а не нулевой спрос.
        </p>
      </details>
      {dirty && (
        <p className="status-note">
          Новый расчёт заменит несохранённые поля ниже. Сначала подтвердите
          текущие правки, если хотите их сохранить.
        </p>
      )}
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
                <p className="eyebrow">ПРОВЕРКА ВЫПУСКА</p>
                <h3>Ваш план на {plan.target_date}</h3>
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
              План на <strong>{plan.target_date}</strong> ·{" "}
              {plan.source === "synthetic"
                ? "Синтетические данные"
                : plan.source === "local"
                  ? "Данные пекарни"
                  : "Внешние данные"}{" "}
              ·{" "}
              {plan.mode === "replay"
                ? "Историческое воспроизведение"
                : "Операционный план"}{" "}
              · {plan.status === "confirmed" ? "подтверждён" : "черновик"} ·
              версия {plan.revision}
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
                  <th scope="col">Метод и ограничения</th>
                </tr>
              </thead>
              <tbody>
                {plan.items.map((i) => (
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
                        <small className="error">
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
                        <small>
                          Код изделия: {i.product_id} · метод: {i.method}
                        </small>
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
                        ? "План сохранён. Можно передать в работу"
                        : "Подтвердите выпуск"}
              </strong>
              <p>Проверьте партии, ограничения и общую загрузку печи.</p>
            </div>
            <div className="actions">
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
              <a
                className="button-link secondary"
                href={busy ? undefined : `/api/plans/${plan.id}/export`}
                aria-disabled={busy || undefined}
                download
              >
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
