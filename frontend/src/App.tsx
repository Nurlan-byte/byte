import { useState, useRef, useEffect } from "react";
import { OrderAdjustmentReport } from "./OrderAdjustmentReport";
import {
  savedDataset,
  forPlanningKind,
  workspaceReference,
  hasQuantityChanges,
  initialQuantity,
  productActiveOnTarget,
} from "./workspace";
import { Icon } from "./Icon";
import {
  DatasetContext,
  PlanHeader,
  PlanProgress,
  WorkspaceNavigation,
  type View,
} from "./WorkspaceChrome";
import { api, ApiError } from "./api";
import type { Dataset, DatasetTarget, Plan, PlanMethod, PlanSummary } from "./types";
import type { ForecastSnapshot } from "./ForecastResult";
import type { AdoptDataset } from "./DemandSources";
import type {
  DailyForecast,
  PosConnectionSummary,
  StoredPosResult,
} from "./DailyPos";
import { businessDate } from "./dates";
import { validQuantity } from "./quantities";
import { ImportSection } from "./ImportSection";
import { ModelSection } from "./ModelSection";
import { PlanningSection } from "./PlanningSection";
import { HistorySection, HISTORY_PAGE_SIZE } from "./HistorySection";

export function App() {
  const [planningKind, setPlanningKind] = useState<
    "production" | "procurement"
  >("production");
  const [view, setView] = useState<View>("planning");
  useEffect(() => {
    const titles = {
      planning:
        planningKind === "procurement" ? "План закупки" : "План выпечки",
      import: "Данные",
      history: "История планов",
      model: "Качество прогноза",
    };
    document.title = titles[view] + " · BakePlan";
  }, [view, planningKind]);
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
    [method, setMethod] = useState<PlanMethod>("B1");
  const [targetOpen, setTargetOpen] = useState(true);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [restored, setRestored] = useState(false);
  const [firstForecast, setFirstForecast] = useState<{
    snapshot: ForecastSnapshot;
    connection?: PosConnectionSummary;
    sequence: number;
  } | null>(null);
  function showForecast(
    first: ForecastSnapshot,
    connection?: PosConnectionSummary,
  ) {
    setCutoff(first.cutoff);
    setTarget(first.targetDate);
    if (first.method !== "AUTO") setMethod(first.method);
    setFirstForecast((previous) => ({
      snapshot: first,
      connection,
      sequence: (previous?.sequence ?? 0) + 1,
    }));
  }
  const errorBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) errorBox.current?.focus();
  }, [error]);
  useEffect(() => {
    let active = true;
    const datasetId = workspaceReference("dataset");
    const planId = workspaceReference("plan");
    const [followedDataset, connectionId] = (
      workspaceReference("pos-connection") ?? ""
    ).split("/");
    const follow =
      !planId && datasetId && datasetId === followedDataset && connectionId;
    void Promise.allSettled([
      datasetId
        ? api<Dataset>(`/datasets/${encodeURIComponent(datasetId)}`)
        : Promise.resolve(null),
      planId
        ? api<Plan>(`/plans/${encodeURIComponent(planId)}`)
        : Promise.resolve(null),
      api<PlanSummary[]>(`/plans?limit=${HISTORY_PAGE_SIZE}&offset=0`),
      follow
        ? api<StoredPosResult>(
            `/pos/connections/${encodeURIComponent(connectionId)}/latest`,
          )
        : Promise.resolve(null),
    ]).then(([data, previousPlan, plans, latest]) => {
      if (!active) return;
      const fresh = latest.status === "fulfilled" ? latest.value : null;
      const restoredDataset =
        fresh?.dataset ?? (data.status === "fulfilled" ? data.value : null);
      if (restoredDataset) {
        const stored = savedDataset(restoredDataset);
        setDataset(stored);
        setPlanningKind(
          stored.forecast_only &&
            workspaceReference("pos-mode") === "procurement"
            ? "procurement"
            : stored.planning_kind === "procurement"
              ? "procurement"
              : "production",
        );
        setSource(stored.source);
        setBakeryId(stored.bakery_id);
        setDataTarget(stored.target);
        setProvenance(stored.provenance);
      }
      if (fresh) {
        showForecast(fresh.forecast, fresh.connection);
        workspaceReference(
          "pos-connection",
          `${fresh.dataset.id}/${connectionId}`,
        );
        setView("model");
        if (fresh.connection.status === "ready")
          setNotice("Открыт последний сохранённый прогноз кассы.");
      } else if (follow && latest.status === "rejected") {
        if (latest.reason instanceof ApiError && latest.reason.status === 404) {
          workspaceReference("pos-connection", null);
          setNotice(
            "Подключение отключено. Ранее загруженные продажи доступны.",
          );
        } else {
          setNotice(
            "Не удалось получить последний прогноз кассы. Статус подключения доступен в разделе «Данные».",
          );
        }
      }
      if (previousPlan.status === "fulfilled" && previousPlan.value)
        show(previousPlan.value);
      if (plans.status === "fulfilled") {
        setHistory(plans.value);
        setHistoryLoaded(true);
      }
      if (
        (!fresh && data.status === "rejected") ||
        previousPlan.status === "rejected"
      ) {
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
    const leavingPanel = document.activeElement?.closest("[id^='panel-']");
    setView(next);
    window.scrollTo({ top: 0 });
    if (leavingPanel && view !== next) {
      requestAnimationFrame(() => {
        if (
          leavingPanel.hasAttribute("hidden") &&
          (document.activeElement === document.body ||
            leavingPanel.contains(document.activeElement))
        ) {
          document.getElementById("workspace")?.focus({ preventScroll: true });
        }
      });
    }
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
  async function runReplacingPlan(fn: () => Promise<void>) {
    if (
      unsaved &&
      !window.confirm(
        "Есть несохранённые количества. Продолжить и сбросить эти изменения после успешной операции?",
      )
    )
      return;
    await run(fn);
  }
  function show(p: Plan) {
    setPlan(p);
    setPlanningKind(
      p.planning_kind === "procurement" ? "procurement" : "production",
    );
    setTarget(p.target_date);
    setCutoff(p.cutoff);
    if (p.planning_kind !== "procurement" && p.requested_method)
      setMethod(p.requested_method);
    const calendar = p.items.find((item) => item.target_is_open != null);
    if (calendar) setTargetOpen(calendar.target_is_open!);
    setQuantities(
      Object.fromEntries(
        p.items.map((i) => [i.product_id, initialQuantity(p, i)]),
      ),
    );
  }
  const confirmation = useRef<{ signature: string; key: string } | null>(null);
  const invalid = plan?.items.some(
    (i) =>
      (i.target_is_open === false && quantities[i.product_id] !== "0") ||
      (!productActiveOnTarget(i, plan.target_date) &&
        quantities[i.product_id] !== "0") ||
      i.batch_size == null ||
      i.max_daily_qty == null ||
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
      if (value && typeof value !== "function" && !value.forecast_only) {
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
    runReplacingPlan,
    show,
    refreshHistory: () => loadHistory(0),
  };
  async function prepareForecast(datasetId: string) {
    const first = await api<DailyForecast>(
      `/datasets/${datasetId}/first-forecast`,
      {},
    );
    showForecast(first);
    return first;
  }
  const adopt: AdoptDataset = async (
    result,
    contextWarnings,
    fromPos,
    connection,
  ) => {
    const saved = savedDataset(result);
    setDataset(saved);
    setPlan(null);
    workspaceReference("plan", null);
    if (saved.forecast_only) workspaceReference("pos-mode", planningKind);
    else setPlanningKind(saved.planning_kind ?? "production");
    setSource(saved.source);
    setDataTarget(saved.target);
    setProvenance(saved.provenance);
    setBakeryId(saved.bakery_id);
    if (fromPos) setCsv("");
    const first = connection?.forecast ?? (await prepareForecast(saved.id));
    if (connection) showForecast(first, connection.summary);
    workspaceReference(
      "pos-connection",
      connection ? `${saved.id}/${connection.summary.id}` : null,
    );
    changeView("model");
    setNotice(
      [
        connection && connection.summary.status !== "ready"
          ? ""
          : "Продажи сохранены. Прогноз готов.",
        ...contextWarnings,
        ...first.warnings,
      ]
        .filter(Boolean)
        .join(" "),
    );
  };
  const salesOnly =
    selectedDataset?.forecast_only ||
    selectedDataset?.target === "item_line_count" ||
    selectedDataset?.unit === "item_line";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">
        Перейти к содержимому
      </a>
      <WorkspaceNavigation
        view={view}
        kind={planningKind}
        busy={busy}
        onView={changeView}
        onKind={(kind) => {
          setPlanningKind(kind);
          changeView("import");
        }}
      />
      <main id="workspace" tabIndex={-1}>
        {view === "planning" && (
          <PlanHeader kind={planningKind} target={target} />
        )}
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
                    changeView("planning");
                  }}
                >
                  Вернуться к несохранённому плану
                </button>
              </>
            )}
          </aside>
        )}
        {(selectedDataset || selectedPlan) && (
          <DatasetContext
            dataset={selectedDataset}
            onImport={
              view === "import" ? undefined : () => changeView("import")
            }
          />
        )}
        {error && (
          <div role="alert" className="error" tabIndex={-1} ref={errorBox}>
            <strong className="notice-title">
              <Icon name="alert" />
              Не удалось выполнить действие
            </strong>
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
          <div role="status" className="success notice-banner">
            <Icon name="check" />
            <span>{notice}</span>
          </div>
        )}
        {busy && (
          <p role="status" className="pending">
            <span className="spinner" />
            <span>Выполняется запрос… Поля временно заблокированы.</span>
          </p>
        )}
        <div id="panel-planning" hidden={view !== "planning"}>
          <PlanningSection
            {...sectionProps}
            onImport={() => changeView("import")}
            onForecast={() => changeView("model")}
            onHistory={() => changeView("history")}
          />
        </div>
        <div id="panel-import" hidden={view !== "import"}>
          <ImportSection
            {...sectionProps}
            active={view === "import"}
            adopt={adopt}
            onContinue={() => changeView(salesOnly ? "model" : "planning")}
          />
        </div>
        <div id="panel-model" hidden={view !== "model"}>
          {firstForecast?.connection &&
            firstForecast.connection.status !== "ready" &&
            firstForecast.snapshot.result.dataset_id ===
              selectedDataset?.id && (
              <p role="status" className="status-note">
                {firstForecast.connection.status === "error"
                  ? `Касса не обновилась. ${firstForecast.connection.error ?? ""}`
                  : "Идёт обновление кассы."}{" "}
                Показан последний успешный прогноз от{" "}
                {firstForecast.connection.last_success_day}.
              </p>
            )}
          {selectedDataset ? (
            <ModelSection
              key={
                selectedDataset.id +
                "/" +
                (firstForecast?.snapshot.result.dataset_id ===
                selectedDataset.id
                  ? firstForecast.sequence
                  : 0)
              }
              onOrderDataset={async (value) => {
                setPlan(null);
                workspaceReference("plan", null);
                setDataset(value);
                setPlanningKind("procurement");
                changeView("planning");
                setNotice(
                  "Упаковки сохранены. Проверьте остатки и срок поставки.",
                );
              }}
              initial={
                firstForecast?.snapshot.result.dataset_id === selectedDataset.id
                  ? firstForecast.snapshot
                  : undefined
              }
              {...sectionProps}
              dataset={selectedDataset}
              active={view === "model"}
            />
          ) : (
            <section className="quality-empty empty-state">
              <Icon name="model" />
              <h1>Для проверки прогноза нужны данные</h1>
              <p>
                {planningKind === "procurement"
                  ? "Загрузите историю продаж, чтобы рассчитать заказ поставщику."
                  : "Загрузите историю продаж. Подготовка экспериментального расчёта необязательна для первого плана."}
              </p>
              <button onClick={() => changeView("import")}>
                Перейти к данным
              </button>
            </section>
          )}
        </div>
        <div id="panel-history" hidden={view !== "history"}>
          {planningKind === "procurement" && (selectedDataset?.bakery_id || selectedPlan?.bakery_id) && (
            <OrderAdjustmentReport
              key={`${selectedDataset?.id}:${selectedPlan?.id}:${selectedPlan?.revision}`}
              branch={(selectedDataset?.bakery_id ?? selectedPlan?.bakery_id)!}
              source={(selectedDataset?.source ?? selectedPlan?.source)!}
              datasetId={selectedDataset?.target === "sold_units" && selectedDataset?.unit === "physical_unit" ? selectedDataset.id : undefined}
            />
          )}
          {
            <HistorySection
              onPlan={() => changeView("planning")}
              history={history}
              offset={historyOffset}
              loaded={historyLoaded}
              busy={busy || hiddenUnsaved}
              onPage={(offset) => void run(() => loadHistory(offset))}
              onLoad={(id) =>
                void runReplacingPlan(async () => {
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
                  changeView("planning");
                })
              }
            />
          }
        </div>
        {view === "planning" && (
          <PlanProgress
            dataset={selectedDataset}
            plan={selectedPlan}
            unsaved={unsaved}
          />
        )}
        <footer className="workspace-footer">
          <p>
            <Icon name="info" />
            {planningKind === "procurement"
              ? "Проверьте прогноз, остаток и условия поставки перед заказом."
              : "Проверьте прогноз и загрузку печи перед подтверждением плана."}
          </p>
          <span>Локальный прототип · Asia/Qyzylorda</span>
        </footer>
      </main>
    </div>
  );
}
