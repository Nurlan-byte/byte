import { useState, useRef, useEffect } from "react";
import {
  savedDataset,
  forPlanningKind,
  workspaceReference,
  hasQuantityChanges,
  productActiveOnTarget,
} from "./workspace";
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
  const [planningKind, setPlanningKind] = useState<
    "production" | "procurement"
  >("production");
  const [view, setView] = useState<View>("planning");
  const [csv, setCsv] = useState(""),
    [source, setSource] = useState("synthetic"),
    [dataset, setDataset] = useState<Dataset | null>(null),
    [plan, setPlan] = useState<Plan | null>(null),
    [history, setHistory] = useState<PlanSummary[]>([]),
    [quantities, setQuantities] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(true),
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
  const [restored, setRestored] = useState(false);
  const errorBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) errorBox.current?.focus();
  }, [error]);
  useEffect(() => {
    let active = true;
    const datasetId = workspaceReference("dataset");
    const planId = workspaceReference("plan");
    void Promise.allSettled([
      datasetId
        ? api<Dataset>(`/datasets/${encodeURIComponent(datasetId)}`)
        : Promise.resolve(null),
      planId
        ? api<Plan>(`/plans/${encodeURIComponent(planId)}`)
        : Promise.resolve(null),
      api<PlanSummary[]>(`/plans?limit=${HISTORY_PAGE_SIZE}&offset=0`),
    ]).then(([data, previousPlan, plans]) => {
      if (!active) return;
      if (data.status === "fulfilled" && data.value) {
        const stored = savedDataset(data.value);
        setDataset(stored);
        setPlanningKind(
          stored.planning_kind === "procurement" ? "procurement" : "production",
        );
        setSource(stored.source);
        setBakeryId(stored.bakery_id);
        setDataTarget(stored.target);
        setProvenance(stored.provenance);
      }
      if (previousPlan.status === "fulfilled" && previousPlan.value)
        show(previousPlan.value);
      if (plans.status === "fulfilled") {
        setHistory(plans.value);
        setHistoryLoaded(true);
      }
      if (data.status === "rejected" || previousPlan.status === "rejected") {
        setError(
          "Не удалось открыть прошлый набор или план. Откройте сохранённый набор в «Данных» или план в «Истории». Если сервер временно недоступен, обновите страницу.",
        );
      } else if (plans.status === "rejected") {
        setError(
          "Не удалось загрузить историю. Проверьте соединение с сервером и нажмите «Обновить историю».",
        );
      }
      setBusy(false);
      setRestored(true);
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (restored && dataset) workspaceReference("dataset", dataset.id);
  }, [restored, dataset]);
  useEffect(() => {
    if (restored && plan) workspaceReference("plan", plan.id);
  }, [restored, plan]);
  const unsaved = hasQuantityChanges(plan, quantities);
  useEffect(() => {
    if (!unsaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);
  function changeView(next: View) {
    setView(next);
    window.scrollTo({ top: 0 });
    if (next === "history" && view !== "history" && !busy)
      void run(() => loadHistory(historyOffset));
  }
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
    setPlanningKind(
      p.planning_kind === "procurement" ? "procurement" : "production",
    );
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
      (!productActiveOnTarget(i, plan.target_date) &&
        quantities[i.product_id] !== "0") ||
      !validQuantity(
        quantities[i.product_id] ?? "",
        i.batch_size,
        i.max_daily_qty,
      ),
  );
  const selectedDataset = forPlanningKind(dataset, planningKind);
  const selectedPlan = forPlanningKind(plan, planningKind);
  const hiddenUnsaved = unsaved && !selectedPlan;
  const sectionProps = {
    planningKind,
    csv,
    setCsv,
    source,
    setSource,
    dataset: selectedDataset,
    setDataset: (
      value: Dataset | null | ((previous: Dataset | null) => Dataset | null),
    ) => {
      if (value && typeof value !== "function") {
        setPlanningKind(
          value.planning_kind === "procurement" ? "procurement" : "production",
        );
      }
      setDataset(value);
    },
    bakeryId,
    setBakeryId,
    dataTarget,
    setDataTarget,
    provenance,
    setProvenance,
    plan: selectedPlan,
    setPlan: (
      value: Plan | null | ((previous: Plan | null) => Plan | null),
    ) => {
      if (value === null) workspaceReference("plan", null);
      setPlan(value);
    },
    busy: busy || hiddenUnsaved,
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
    selectedDataset?.target === "item_line_count" ||
    selectedDataset?.unit === "item_line";
  const replay = target <= businessDate();
  const period = selectedDataset
    ? formatDatasetPeriod(selectedDataset)
    : "Период не указан";
  const sourceLabel = selectedDataset
    ? datasetSourceLabel(selectedDataset.source)
    : "";
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
          onClick={() => changeView("planning")}
          aria-label="BakePlan"
        >
          <span className="brand-mark">
            <Icon name="bread" />
          </span>
          <span>
            BakePlan
            <small>
              {planningKind === "procurement"
                ? "Закупки под контролем"
                : "Пекарня под контролем"}
            </small>
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
              onClick={() => changeView(item.id)}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.id === "model" && (
                <span className="nav-hint" aria-hidden="true">
                  Опыт
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="status-dot" />
          Локальный прототип
          <small>
            {planningKind === "procurement" ? "Торговая точка" : "Одна пекарня"}{" "}
            · Asia/Qyzylorda
          </small>
        </div>
      </aside>
      <main id="workspace" tabIndex={-1}>
        <header className="workspace-header">
          <div>
            <p className="eyebrow">ЕЖЕДНЕВНОЕ ПЛАНИРОВАНИЕ</p>
            <h1>
              {planningKind === "procurement" ? "План закупки" : "План выпечки"}
            </h1>
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
        <div className="control-field mode-switch">
          <label htmlFor="planning-kind">Как пополняем товары</label>
          <select
            id="planning-kind"
            value={planningKind}
            disabled={busy}
            onChange={(event) => {
              setPlanningKind(
                event.target.value as "production" | "procurement",
              );
              setView("import");
            }}
          >
            <option value="production">Производим сами</option>
            <option value="procurement">Закупаем готовое</option>
          </select>
        </div>
        {((dataset && !selectedDataset) || (plan && !selectedPlan)) && (
          <aside className="status-note" data-testid="mode-selection-notice">
            <p>
              Для выбранного режима загрузите или выберите подходящий набор
              данных. Данные и план другого режима сохранены в текущей сессии.
            </p>
            {hiddenUnsaved && (
              <>
                <p>
                  В другом режиме есть несохранённые изменения. Вернитесь и
                  сохраните их перед загрузкой другого набора.
                </p>
                <button
                  onClick={() => {
                    setPlanningKind(
                      plan?.planning_kind === "procurement"
                        ? "procurement"
                        : "production",
                    );
                    setView("planning");
                  }}
                >
                  Вернуться к несохранённому плану
                </button>
              </>
            )}
          </aside>
        )}
        <ol className="journey" aria-label="Шаги подготовки плана">
          <li className={!selectedDataset ? "current" : "done"}>1. Данные</li>
          <li
            className={
              selectedDataset && !selectedPlan
                ? "current"
                : selectedPlan
                  ? "done"
                  : ""
            }
          >
            2. Расчёт
          </li>
          <li
            className={
              selectedPlan && (selectedPlan.status !== "confirmed" || unsaved)
                ? "current"
                : ""
            }
          >
            3. Проверка и сохранение
          </li>
        </ol>
        {(selectedDataset || selectedPlan) && (
          <div className="provenance-strip" data-testid="workspace-source">
            <span className={`source-icon ${selectedDataset ? "loaded" : ""}`}>
              <Icon name="data" />
            </span>
            <div>
              {selectedDataset ? (
                <>
                  <strong>{sourceLabel} · для нового расчёта</strong>
                  <span>
                    {selectedDataset.row_count ?? "—"} строк · {period}
                  </span>
                  <small>{selectedDataset.provenance}</small>
                </>
              ) : (
                <>
                  <strong>
                    {selectedPlan
                      ? "Набор для нового расчёта не выбран"
                      : "История продаж ещё не загружена"}
                  </strong>
                  <span>
                    {selectedPlan
                      ? "Открытый план использует свой сохранённый снимок данных."
                      : "Начните с CSV или безопасных демонстрационных данных."}
                  </span>
                </>
              )}
            </div>
            <button
              className="text-button"
              onClick={() => changeView("import")}
            >
              {selectedDataset ? "Открыть данные" : "Загрузить данные"}
              <Icon name="arrow" />
            </button>
          </div>
        )}
        <p className="safety-note">
          Прогноз — ориентир, а не гарантия спроса. Синтетические данные только
          для демонстрации.{" "}
          {planningKind === "procurement"
            ? "Проверьте остаток и условия поставки вручную."
            : "Загрузку печи проверьте вручную."}
        </p>
        {error && (
          <div role="alert" className="error" tabIndex={-1} ref={errorBox}>
            <strong>Не удалось выполнить действие</strong>
            <p className="error-message">{error.split("\n")[0]}</p>
            {error.includes("\n") && (
              <details>
                <summary>Подробности ошибки</summary>
                <pre>{error.slice(error.indexOf("\n") + 1)}</pre>
              </details>
            )}
            <p>
              Исправьте указанную причину и повторите действие. При конфликте
              версии откройте актуальный план из истории.
            </p>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => changeView("history")}
            >
              Открыть историю
            </button>
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
            onImport={() => changeView("import")}
            onHistory={() => changeView("history")}
          />
        </div>
        <div id="panel-import" hidden={view !== "import"}>
          <ImportSection
            {...sectionProps}
            active={view === "import"}
            onContinue={() => changeView(salesOnly ? "model" : "planning")}
          />
        </div>
        <div id="panel-model" hidden={view !== "model"}>
          {selectedDataset ? (
            <ModelSection
              key={selectedDataset.id}
              {...sectionProps}
              dataset={selectedDataset}
              active={view === "model"}
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
          {
            <HistorySection
              history={history}
              offset={historyOffset}
              loaded={historyLoaded}
              busy={busy || hiddenUnsaved}
              onPage={(offset) => void run(() => loadHistory(offset))}
              onLoad={(id) =>
                void run(async () => {
                  const storedPlan = await api<Plan>(`/plans/${id}`);
                  // A history plan may belong to another saved dataset. Keep
                  // the current import selected; the plan holds its own
                  // immutable catalog/assumptions for confirmation.
                  if (!dataset) {
                    try {
                      setDataset(
                        savedDataset(
                          await api<Dataset>(
                            `/datasets/${storedPlan.dataset_id}`,
                          ),
                        ),
                      );
                    } catch {
                      setDataset(null);
                      setNotice(
                        "План открыт из сохранённого снимка. Набор для нового расчёта недоступен; откройте его в разделе «Данные».",
                      );
                    }
                  }
                  show(storedPlan);
                  setView("planning");
                })
              }
            />
          }
        </div>
        <footer>
          Без реальной истории точность и экономия не заявляются.
          <span>BakePlan · локальный прототип</span>
        </footer>
      </main>
    </div>
  );
}
