import { useState, useRef } from "react";
import { api } from "./api";
import type { Dataset, Plan, PlanSummary } from "./types";
import { businessDate } from "./dates";
import { validQuantity } from "./quantities";
import { ImportSection } from "./ImportSection";
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
  const sectionProps = {
    csv,
    setCsv,
    source,
    setSource,
    dataset,
    setDataset,
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
      <ImportSection {...sectionProps} />
      <PlanningSection {...sectionProps} />
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
      <footer>
        Без подтверждённой реальной истории точность и экономия не заявляются.
        Часовой пояс пекарни: Asia/Qyzylorda.
      </footer>
    </main>
  );
}
