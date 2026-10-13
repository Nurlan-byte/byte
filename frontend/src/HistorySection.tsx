import type { PlanSummary } from "./types";
export const HISTORY_PAGE_SIZE = 20;
type Props = {
  history: PlanSummary[];
  offset: number;
  busy: boolean;
  loaded: boolean;
  onPage: (offset: number) => void;
  onLoad: (id: string) => void;
};

function planDate(value: string) {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("ru-RU", {
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      });
}

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
}: Props) {
  return (
    <section id="history" aria-labelledby="history-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Сохранённые решения</p>
          <h2 id="history-heading">История планов</h2>
        </div>
        <button className="secondary" disabled={busy} onClick={() => onPage(0)}>
          Обновить историю
        </button>
      </div>
      <p className="section-copy">
        Откройте план, чтобы проверить количества или подготовить новую
        редакцию. CSV содержит сохранённые значения; несохранённые правки в файл
        не попадут.
      </p>
      {!history.length ? (
        <div className="status-note">
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
        </div>
      ) : (
        <>
          <p className="muted">
            Планы {offset + 1}–{offset + history.length} · сначала последние
            созданные
          </p>
          <ul className="history-list">
            {history.map((p) => (
              <li
                key={p.id}
                className="history-card"
                data-plan-id={p.id}
                data-target-date={p.target_date}
                data-status={p.status}
              >
                <div className="section-heading">
                  <h3>
                    <time dateTime={p.target_date}>
                      {planDate(p.target_date)}
                    </time>
                  </h3>
                  <span
                    className={`badge ${p.status === "confirmed" ? "badge-success" : "badge-warning"}`}
                  >
                    {statuses[p.status] ?? "Статус не распознан"}
                  </span>
                </div>
                <p className="history-meta">
                  {sources[p.source] ?? "Другой источник"} · редакция{" "}
                  {p.revision}
                </p>
                <p className="muted">
                  {p.status === "confirmed"
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
                        : `/api/plans/${encodeURIComponent(p.id)}/export`
                    }
                    aria-disabled={busy || undefined}
                  >
                    Экспорт CSV
                  </a>
                </div>
                <details>
                  <summary>Данные сохранённого плана</summary>
                  <p>
                    ID плана: {p.id} · набор: {p.dataset_id}
                  </p>
                  <p>
                    Дата плана: {p.target_date} · источник: {p.source} · статус:{" "}
                    {p.status} · редакция: {p.revision}
                  </p>
                  {p.created_at && <p>Создан: {p.created_at}</p>}
                </details>
              </li>
            ))}
          </ul>
        </>
      )}
      {loaded && (
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
