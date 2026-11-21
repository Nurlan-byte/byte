import { formatPlanDate } from "./dates";
import { Icon } from "./Icon";
import type { PlanSummary } from "./types";
export const HISTORY_PAGE_SIZE = 20;
type Props = {
  history: PlanSummary[];
  offset: number;
  busy: boolean;
  loaded: boolean;
  onPage: (offset: number) => void;
  onLoad: (id: string) => void;
  onPlan?: () => void;
};

const sources: Record<string, string> = {
  synthetic: "Синтетические данные",
  local: "Данные пекарни",
  external: "Внешний источник",
};
const statuses: Record<string, string> = {
  draft: "Черновик",
  confirmed: "Подтверждён",
};

export function HistorySection({
  history,
  offset,
  busy,
  loaded,
  onPage,
  onLoad,
  onPlan,
}: Props) {
  return (
    <section
      className="archive-workspace"
      id="history"
      aria-labelledby="history-heading"
    >
      <div className="section-heading">
        <div>
          <h1 id="history-heading">История планов</h1>
          <p className="section-copy">
            Откройте сохранённую редакцию или скачайте её в CSV.
          </p>
        </div>
        <button className="secondary" disabled={busy} onClick={() => onPage(0)}>
          Обновить историю
        </button>
      </div>

      {!history.length ? (
        <div className="empty-note archive-empty">
          <Icon name="history" />
          <strong>
            {!loaded
              ? "Загрузите сохранённые планы"
              : offset === 0
                ? "Сохранённых планов пока нет"
                : "На этой странице планов нет"}
          </strong>
          <p>
            {!loaded
              ? "Нажмите «Обновить историю», чтобы увидеть черновики и подтверждённые редакции."
              : offset === 0
                ? "Рассчитайте первый план в разделе «План». Он появится здесь как черновик."
                : "Вернитесь на предыдущую страницу или обновите историю."}
          </p>
          {loaded && offset === 0 && onPlan && (
            <button type="button" onClick={onPlan} disabled={busy}>
              Перейти к плану <Icon name="arrow" />
            </button>
          )}
        </div>
      ) : (
        <>
          <p className="history-range">
            Планы {offset + 1}–{offset + history.length} · сначала последние
            созданные
          </p>
          <ul className="history-list">
            {history.map((p) => (
              <li
                key={p.id}
                className="history-card history-entry"
                data-plan-id={p.id}
                data-target-date={p.target_date}
                data-status={p.status}
              >
                <div className="history-identity">
                  <Icon
                    name={p.planning_kind === "procurement" ? "data" : "bread"}
                  />
                  <div>
                    <h2>
                      <time dateTime={p.target_date}>
                        {formatPlanDate(p.target_date)}
                      </time>
                    </h2>
                    <p>
                      {p.planning_kind === "procurement"
                        ? "Закупаем готовое"
                        : "Производим сами"}
                    </p>
                  </div>
                </div>
                <div className="history-detail">
                  <span
                    className={`badge ${p.status === "confirmed" ? "badge-success" : "badge-warning"}`}
                  >
                    {statuses[p.status] ?? "Статус не распознан"}
                  </span>
                  <p className="history-meta">
                    {sources[p.source] ?? "Другой источник"} · редакция{" "}
                    {p.revision}
                  </p>
                  {p.created_at && (
                    <p className="history-created">
                      Создан:{" "}
                      <time dateTime={p.created_at}>
                        {new Date(p.created_at).toLocaleString("ru-RU")}
                      </time>
                    </p>
                  )}
                </div>
                <div className="history-tools">
                  <p className="muted">
                    {p.planning_kind === "procurement"
                      ? p.status === "confirmed"
                        ? "Заказ подтверждён и сохранён."
                        : "Черновик заказа: проверьте остаток и количества."
                      : p.status === "confirmed"
                        ? "Количество выпуска подтверждено и сохранено."
                        : p.status === "draft"
                          ? "Количества рассчитаны. Выпуск ещё не подтверждён."
                          : "Проверьте состояние плана перед использованием."}
                  </p>
                  <div className="actions">
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => onLoad(p.id)}
                    >
                      Открыть план
                    </button>
                    <a
                      className="button-link secondary"
                      href={
                        busy
                          ? undefined
                          : `/api/plans/${encodeURIComponent(p.id)}/export?revision=${p.revision}`
                      }
                      aria-disabled={busy || undefined}
                    >
                      <Icon name="download" />
                      Экспорт CSV
                    </a>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {loaded && (history.length > 0 || offset > 0) && (
        <nav className="actions" aria-label="Страницы истории">
          <button
            className="secondary"
            disabled={busy || offset === 0}
            onClick={() => onPage(Math.max(0, offset - HISTORY_PAGE_SIZE))}
          >
            Предыдущая страница
          </button>
          <span className="muted">
            Страница {Math.floor(offset / HISTORY_PAGE_SIZE) + 1}
          </span>
          <button
            className="secondary"
            disabled={busy || history.length < HISTORY_PAGE_SIZE}
            onClick={() => onPage(offset + HISTORY_PAGE_SIZE)}
          >
            Следующая страница
          </button>
        </nav>
      )}
    </section>
  );
}
