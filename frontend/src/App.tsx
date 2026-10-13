import { useState, useRef } from "react";
import { Icon } from "./Icon";
import { formatDatasetPeriod, datasetSourceLabel } from "./datasetSummary";
import { api } from "./api";
import type { Dataset, DatasetTarget, Plan, PlanSummary } from "./types";
import { businessDate } from "./dates";
import { validQuantity } from "./quantities";
import { ImportSection } from "./ImportSection";
import { ModelSection } from "./ModelSection";
import { PlanningSection } from "./PlanningSection";
import { HistorySection, HISTORY_PAGE_SIZE } from "./HistorySection";
type View = "planning" | "import" | "model" | "history";

export function App() {
  const [view, setView] = useState<View>("planning");
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
  const replay = target <= businessDate();
  const period = dataset ? formatDatasetPeriod(dataset) : "Период не указан";
  const sourceLabel = dataset ? datasetSourceLabel(dataset.source) : "";
  const nav = [
    { id: "planning", label: "План", icon: "plan" },
    { id: "import", label: "Данные", icon: "data" },
    { id: "model", label: "Модель", icon: "model" },
    { id: "history", label: "История", icon: "history" },
  ] as const;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">
        Перейти к содержимому
      </a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#workspace"
          onClick={() => setView("planning")}
          aria-label="BakePlan — план выпечки"
        >
          <span className="brand-mark">
            <Icon name="bread" />
          </span>
          <span>
            BakePlan<small>Пекарня под контролем</small>
          </span>
        </a>
        <div className="workspace-label">РАБОЧЕЕ МЕСТО</div>
        <nav className="section-nav" aria-label="Разделы BakePlan">
          {nav.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={view === item.id ? "page" : undefined}
              aria-controls={`panel-${item.id}`}
              onClick={() => setView(item.id)}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.id === "model" && (
                <span className="nav-hint" aria-hidden="true">
                  ML
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="status-dot" />
          Локальный прототип<small>Одна пекарня · Asia/Qyzylorda</small>
        </div>
      </aside>
      <main id="workspace" tabIndex={-1}>
        <header className="workspace-header">
          <div>
            <p className="eyebrow">ЕЖЕДНЕВНОЕ ПЛАНИРОВАНИЕ</p>
            <h1>План выпечки</h1>
            <p className="header-subtitle">
              Данные → расчёт → проверка → подтверждение
            </p>
          </div>
          <div className="date-summary">
            <span className="muted">Выбранная дата плана</span>
            <strong>{target || "Выберите дату"}</strong>
            <span
              className={`badge ${replay ? "badge-warning" : "badge-neutral"}`}
            >
              {replay ? "Историческое воспроизведение" : "Рабочий план"}
            </span>
          </div>
        </header>
        <div className="provenance-strip" data-testid="workspace-source">
          <span className={`source-icon ${dataset ? "loaded" : ""}`}>
            <Icon name="data" />
          </span>
          <div>
            {dataset ? (
              <>
                <strong>{sourceLabel} · для нового расчёта</strong>
                <span>
                  {dataset.row_count ?? "—"} строк · {period}
                </span>
                <small>{dataset.provenance}</small>
              </>
            ) : (
              <>
                <strong>
                  {plan
                    ? "Набор для нового расчёта не выбран"
                    : "История продаж ещё не загружена"}
                </strong>
                <span>
                  {plan
                    ? "Открытый план использует свой сохранённый снимок данных."
                    : "Начните с CSV или безопасных демонстрационных данных."}
                </span>
              </>
            )}
          </div>
          <button className="text-button" onClick={() => setView("import")}>
            {dataset ? "Открыть данные" : "Загрузить данные"}
            <Icon name="arrow" />
          </button>
        </div>
        <p className="safety-note">
          Прогноз — ориентир, а не гарантия спроса. Синтетические данные только
          для демонстрации. Загрузку печи проверьте вручную.
        </p>
        {error && (
          <div role="alert" className="error">
            <strong>Не удалось выполнить действие</strong>
            <pre>{error}</pre>
            <p>
              Проверьте сообщение и повторите действие. При конфликте версии
              откройте актуальный план из истории.
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
            <span className="spinner" /> Выполняется запрос… Поля временно
            заблокированы.
            {view === "model" && " Обучение может занять до 60 секунд."}
          </p>
        )}
        <div id="panel-planning" hidden={view !== "planning"}>
          <PlanningSection
            {...sectionProps}
            onImport={() => setView("import")}
          />
        </div>
        <div id="panel-import" hidden={view !== "import"}>
          <ImportSection
            {...sectionProps}
            onContinue={() => setView(salesOnly ? "model" : "planning")}
          />
        </div>
        <div id="panel-model" hidden={view !== "model"}>
          {dataset ? (
            <ModelSection
              key={dataset.id}
              {...sectionProps}
              dataset={dataset}
            />
          ) : (
            <section className="empty-state">
              <Icon name="model" />
              <h2>Модель начинается с данных</h2>
              <p>
                Загрузите историю, чтобы обучить экспериментальную ML-модель и
                сравнить её с B1. Для плана обучение необязательно.
              </p>
              <button onClick={() => setView("import")}>
                Перейти к данным
              </button>
            </section>
          )}
        </div>
        <div id="panel-history" hidden={view !== "history"}>
          {!salesOnly ? (
            <HistorySection
              history={history}
              offset={historyOffset}
              loaded={historyLoaded}
              busy={busy}
              onPage={(offset) => void run(() => loadHistory(offset))}
              onLoad={(id) =>
                void run(async () => {
                  show(await api<Plan>(`/plans/${id}`));
                  setView("planning");
                })
              }
            />
          ) : (
            <section className="empty-state">
              <h2>История производственных планов</h2>
              <p>
                Строки продаж доступны только для диагностики. Откройте набор в
                физических штуках, чтобы работать с планами.
              </p>
              <button onClick={() => setView("import")}>
                Перейти к данным
              </button>
            </section>
          )}
        </div>
        <footer>
          Без реальной истории точность и экономия не заявляются.
          <span>BakePlan · локальный прототип</span>
        </footer>
      </main>
    </div>
  );
}
