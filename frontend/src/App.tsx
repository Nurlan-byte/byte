import { useState, useRef } from "react";
import { api } from "./api";
import type { Dataset, DatasetTarget, Plan, PlanSummary } from "./types";
import { businessDate } from "./dates";
import { validQuantity } from "./quantities";
import { ImportSection } from "./ImportSection";
import { ModelSection } from "./ModelSection";
import { PlanningSection } from "./PlanningSection";
import { HistorySection, HISTORY_PAGE_SIZE } from "./HistorySection";
export function App() {
  const [csv, setCsv] = useState(""),
    [source, setSource] = useState("synthetic"),
    [dataset, setDataset] = useState<Dataset | null>(null),
    [plan, setPlan] = useState<Plan | null>(null),
    [history, setHistory] = useState<PlanSummary[]>([]),
    [quantities, setQuantities] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [bakeryId, setBakeryId] = useState("demo");
  const [dataTarget, setDataTarget] = useState<DatasetTarget>("sold_units");
  const [provenance, setProvenance] = useState(
    "Синтетическая демонстрация BakePlan",
  );
  const running = useRef(false);
  const [target, setTarget] = useState(() => businessDate(1)),
    [cutoff, setCutoff] = useState(businessDate(-1)),
    [method, setMethod] = useState("B1");
  const [targetOpen, setTargetOpen] = useState(true);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  async function loadHistory(offset: number) {
    const rows = await api<PlanSummary[]>(
      `/plans?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`,
    );
    setHistory(rows);
    setHistoryOffset(offset);
    setHistoryLoaded(true);
  }
  async function run(fn: () => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  function show(p: Plan) {
    setPlan(p);
    setTarget(p.target_date);
    setCutoff(p.cutoff);
    if (p.requested_method) setMethod(p.requested_method);
    const calendar = p.items.find((item) => item.target_is_open != null);
    if (calendar) setTargetOpen(calendar.target_is_open!);
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
      (i.target_is_open === false && quantities[i.product_id] !== "0") ||
      !validQuantity(
        quantities[i.product_id] ?? "",
        i.batch_size,
        i.max_daily_qty,
      ),
  );
  const sectionProps = {
    csv,
    setCsv,
    source,
    setSource,
    dataset,
    setDataset,
    bakeryId,
    setBakeryId,
    dataTarget,
    setDataTarget,
    provenance,
    setProvenance,
    plan,
    setPlan,
    busy,
    setError,
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
    refreshHistory: () => loadHistory(0),
  };
  const salesOnly =
    dataset?.target === "item_line_count" || dataset?.unit === "item_line";
  return (
    <main>
      <header>
        <span className="brand">BakePlan</span>
        <span className="tag">Локальный прототип · одна пекарня</span>
        <h1>План выпечки на завтра</h1>
        <p>
          Импорт → модель по желанию → расчёт → ручная проверка → подтверждение
          → история и экспорт.
        </p>
      </header>
      <ol className="workflow" aria-label="Порядок работы">
        <li>
          <a href="#import">1. Импорт</a>
        </li>
        {salesOnly ? (
          <li>
            <a href="#model">2. Диагностика строк продаж</a>
          </li>
        ) : (
          <>
            <li>
              <a href="#planning">2. План и проверка</a>
            </li>
            <li>
              <a href="#history">3. История и CSV</a>
            </li>
          </>
        )}
      </ol>
      <aside>
        Прогноз — ориентир, а не гарантия спроса. Синтетические данные служат
        только для демонстрации. Общую загрузку печи проверьте вручную.
      </aside>
      {error && (
        <div role="alert" className="error">
          <strong>Не удалось выполнить действие</strong>
          <pre>{error}</pre>
          <p>
            Проверьте сообщение и повторите действие. Для конфликта версии
            откройте план заново из истории.
          </p>
        </div>
      )}
      {notice && !busy && (
        <div role="status" className="success">
          {notice}
        </div>
      )}
      {busy && (
        <p role="status" className="pending">
          Выполняется запрос… Поля временно заблокированы. Обучение может занять
          до 60 секунд.
        </p>
      )}
      <ImportSection {...sectionProps} />
      {dataset && (
        <ModelSection key={dataset.id} {...sectionProps} dataset={dataset} />
      )}
      <PlanningSection {...sectionProps} />
      {dataset?.target !== "item_line_count" &&
        dataset?.unit !== "item_line" && (
          <HistorySection
            history={history}
            offset={historyOffset}
            loaded={historyLoaded}
            busy={busy}
            onPage={(offset) => void run(() => loadHistory(offset))}
            onLoad={(id) =>
              void run(async () => show(await api<Plan>(`/plans/${id}`)))
            }
          />
        )}
      <footer>
        Без подтверждённой реальной истории точность и экономия не заявляются.
        Часовой пояс пекарни: Asia/Qyzylorda.
      </footer>
    </main>
  );
}
