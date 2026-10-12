import React, { useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import { validQuantity } from "./quantities";
import "./style.css";

type Item = {
  product_id: string;
  name: string;
  batch_size: number;
  max_daily_qty: number;
  forecast_mean: number | null;
  recommended_qty: number;
  confirmed_qty: number | null;
  method: string;
  warnings: string[];
  used_dates?: string[];
  n?: number;
};
type Plan = {
  id: string;
  revision: number;
  status: string;
  items: Item[];
  dataset_id: string;
  target_date: string;
  cutoff: string;
  source: string;
  mode?: string;
};
type Dataset = {
  id: string;
  source: string;
  row_count?: number;
  diagnostics?: string[];
};
async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch("/api" + path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      r.status === 409
        ? "План изменён или ключ подтверждения уже использован. Загрузите актуальную версию."
        : JSON.stringify(data.detail ?? data),
    );
  return data;
}
function businessDate(offset = 0): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Qyzylorda",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  const d = new Date(`${get("year")}-${get("month")}-${get("day")}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
}
function App() {
  const [csv, setCsv] = useState(""),
    [source, setSource] = useState("synthetic"),
    [dataset, setDataset] = useState<Dataset | null>(null),
    [plan, setPlan] = useState<Plan | null>(null),
    [history, setHistory] = useState<Plan[]>([]),
    [quantities, setQuantities] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [target, setTarget] = useState(() => businessDate(1)),
    [cutoff, setCutoff] = useState(businessDate(-1)),
    [method, setMethod] = useState("B1");
  const [targetOpen, setTargetOpen] = useState(true);
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function show(p: Plan) {
    setPlan(p);
    setQuantities(
      Object.fromEntries(
        p.items.map((i) => [
          i.product_id,
          String(i.confirmed_qty ?? i.recommended_qty),
        ]),
      ),
    );
  }
  const confirmation = useRef<{ signature: string; key: string } | null>(null);
  const invalid = plan?.items.some(
    (i) =>
      !validQuantity(
        quantities[i.product_id] ?? "",
        i.batch_size,
        i.max_daily_qty,
      ),
  );
  return (
    <main>
      <header>
        <span className="brand">BakePlan</span>
        <span className="tag">Локальный прототип · одна пекарня</span>
        <h1>План выпечки на завтра</h1>
        <p>
          История продаж → базовый расчёт → ручная проверка → сохранённый план.
        </p>
      </header>
      <aside>
        Прогноз — ориентир, а не гарантия спроса. Синтетические данные служат
        только для демонстрации. Общую загрузку печи проверьте вручную.
      </aside>
      {error && (
        <div role="alert" className="error">
          <strong>Не удалось выполнить действие</strong>
          <pre>{error}</pre>
        </div>
      )}
      {notice && !busy && (
        <div role="status" className="success">
          {notice}
        </div>
      )}
      {busy && <p role="status">Выполняется…</p>}
      <section>
        <h2>1. Импорт истории</h2>
        <p>
          CSV проверяется целиком: целые неотрицательные количества, баланс и
          уникальность даты/товара. Пропуски не становятся нулями.
        </p>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const r = await fetch("/demo.csv");
              if (!r.ok) throw new Error("Демо-файл недоступен");
              setCsv(await r.text());
              setSource("synthetic");
              setNotice(
                "Загружены синтетические данные. Выберите дату плана и импортируйте CSV.",
              );
            })
          }
        >
          Загрузить демо CSV
        </button>
        <div className="controls">
          <label>
            Источник
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="synthetic">
                Синтетические / демонстрационные
              </option>
              <option value="local">Локальные данные пекарни</option>
              <option value="external">Внешние данные</option>
            </select>
          </label>
          <label>
            CSV-файл
            <input
              type="file"
              accept=".csv,text/csv"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) {
                  if (f.size > 2_000_000) {
                    setError("Файл превышает 2 МБ");
                    return;
                  }
                  void f.text().then(setCsv);
                }
              }}
            />
          </label>
        </div>
        <label>
          Содержимое CSV
          <textarea
            aria-label="Содержимое CSV"
            rows={5}
            value={csv}
            onChange={(e) => setCsv(e.target.value)}
            placeholder="date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status"
          />
        </label>
        <button
          disabled={busy || !csv.trim()}
          onClick={() =>
            void run(async () => {
              setDataset(
                await api<Dataset>("/datasets", { csv_text: csv, source }),
              );
              setPlan(null);
              setNotice("Данные проверены и сохранены. Можно рассчитать план.");
            })
          }
        >
          Проверить и импортировать
        </button>
        {dataset && (
          <p className="success">
            Набор {dataset.id} · источник: {dataset.source}{" "}
            {dataset.row_count !== undefined && `· строк: ${dataset.row_count}`}
          </p>
        )}
        {dataset?.diagnostics?.map((d, n) => (
          <p className="muted" key={n}>
            {d}
          </p>
        ))}
      </section>
      <section>
        <h2>2. Расчёт и ручная проверка</h2>
        <p>Срез включает только завершённые дни строго раньше сегодня. При задержке учёта сдвиньте его назад: время фактической готовности данных не проверяется.</p>
        <div className="controls">
          <label>
            Целевой день открыт
            <input type="checkbox" checked={targetOpen} onChange={(e) => setTargetOpen(e.target.checked)} />
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
                  setHistory(await api<Plan[]>("/plans"));
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
      <section>
        <h2>3. Сохранённая история</h2>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => setHistory(await api<Plan[]>("/plans")))
          }
        >
          Обновить историю
        </button>
        {!history.length ? (
          <p className="muted">История ещё не загружена или пуста.</p>
        ) : (
          <ul>
            {history.map((p) => (
              <li key={p.id}>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () =>
                      show(await api<Plan>(`/plans/${p.id}`)),
                    )
                  }
                >
                  {p.target_date} · {p.source} · {p.status} · версия{" "}
                  {p.revision}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <footer>
        Без подтверждённой реальной истории точность и экономия не заявляются.
        Часовой пояс пекарни: Asia/Qyzylorda.
      </footer>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
