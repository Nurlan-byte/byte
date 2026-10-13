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
      <h2 id="history-heading">3. Сохранённая история</h2>
      <p>
        Откройте план, чтобы проверить сохранённые количества и скачать CSV. В
        истории есть и черновики, и подтверждённые редакции.
      </p>
      <button className="secondary" disabled={busy} onClick={() => onPage(0)}>
        Обновить историю
      </button>
      {!history.length ? (
        <p className="muted">
          {loaded
            ? "На этой странице планов нет. Рассчитайте первый план или вернитесь на предыдущую страницу."
            : "Нажмите «Обновить историю», чтобы загрузить сохранённые планы."}
        </p>
      ) : (
        <ul>
          {history.map((p) => (
            <li key={p.id}>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => onLoad(p.id)}
              >
                {p.target_date} · {p.source} · {p.status} · версия {p.revision}
              </button>
            </li>
          ))}
        </ul>
      )}
      {loaded && (
        <nav aria-label="Страницы истории">
          <button
            className="secondary"
            disabled={busy || offset === 0}
            onClick={() => onPage(Math.max(0, offset - HISTORY_PAGE_SIZE))}
          >
            Предыдущая страница
          </button>{" "}
          <span>Страница {Math.floor(offset / HISTORY_PAGE_SIZE) + 1}</span>{" "}
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
