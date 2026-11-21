import { useMemo, useRef } from "react";
import { BrandMark, Icon } from "./Icon";
import { datasetSourceLabel, formatDatasetPeriod } from "./datasetSummary";
import { businessDate, formatPlanDate } from "./dates";
import type { Dataset, Plan, PlanningKind } from "./types";

const destinations = [
  { id: "planning", label: "План", icon: "plan" },
  { id: "import", label: "Данные", icon: "data" },
  { id: "history", label: "История", icon: "history" },
] as const;
export type View = (typeof destinations)[number]["id"] | "model";

export function WorkspaceNavigation({
  view,
  kind,
  busy,
  onView,
  onKind,
}: {
  view: View;
  kind: PlanningKind;
  busy: boolean;
  onView: (view: View) => void;
  onKind: (kind: PlanningKind) => void;
}) {
  const more = useRef<HTMLDetailsElement>(null);
  return (
    <header className="masthead">
      <div className="masthead-inner">
        <a
          className="wordmark"
          href="#workspace"
          aria-label="BakePlan"
          onClick={() => onView("planning")}
        >
          <BrandMark /> BakePlan
        </a>
        <nav className="workspace-nav" aria-label="Разделы BakePlan">
          {destinations.map(({ id, label, icon }) => (
            <button
              key={id}
              type="button"
              aria-current={view === id ? "page" : undefined}
              aria-controls={`panel-${id}`}
              onClick={() => onView(id)}
            >
              <Icon name={icon} />
              {label}
            </button>
          ))}
          <details
            ref={more}
            className="more-nav"
            data-current={view === "model" || undefined}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget))
                event.currentTarget.open = false;
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.currentTarget.open = false;
                event.currentTarget.querySelector("summary")?.focus();
              }
            }}
          >
            <summary>Дополнительно</summary>
            <button
              type="button"
              aria-current={view === "model" ? "page" : undefined}
              aria-controls="panel-model"
              onClick={() => {
                onView("model");
                if (
                  more.current &&
                  window.matchMedia("(max-width: 820px)").matches
                ) {
                  more.current.open = false;
                  more.current.querySelector("summary")?.focus();
                }
              }}
            >
              <Icon name="model" />
              Качество прогноза
            </button>
          </details>
        </nav>
        <div className="mode-picker">
          <label className="sr-only" htmlFor="planning-kind">
            Как пополняем товары
          </label>
          <select
            id="planning-kind"
            value={kind}
            disabled={busy}
            onChange={(event) => onKind(event.target.value as PlanningKind)}
          >
            <option value="production">Производим сами</option>
            <option value="procurement">Закупаем готовое</option>
          </select>
        </div>
      </div>
    </header>
  );
}

export function PlanHeader({
  kind,
  target,
}: {
  kind: PlanningKind;
  target: string;
}) {
  const replay = Boolean(target && target <= businessDate());
  return (
    <header className="workspace-header">
      <h1>{kind === "procurement" ? "План закупки" : "План выпечки"}</h1>
      <div className="plan-date-summary">
        <Icon name="plan" />
        <time dateTime={target || undefined}>
          {target ? formatPlanDate(target) : "Выберите дату"}
        </time>
        {target && (
          <span className={replay ? "date-flag historical" : "date-flag"}>
            {replay ? "Историческое воспроизведение" : "Рабочий план"}
          </span>
        )}
      </div>
    </header>
  );
}

export function DatasetContext({
  dataset,
  onImport,
}: {
  dataset: Dataset | null;
  onImport?: () => void;
}) {
  const period = useMemo(() => formatDatasetPeriod(dataset), [dataset]);
  return (
    <div className="dataset-context" data-testid="workspace-source">
      <Icon name="data" />
      <div>
        <strong>
          {dataset
            ? `${datasetSourceLabel(dataset.source)} · для нового расчёта`
            : "Набор для нового расчёта не выбран"}
        </strong>
        {dataset ? (
          <>
            <span>
              {dataset.row_count ?? "—"} строк · {period}
            </span>
            <small>{dataset.provenance}</small>
          </>
        ) : (
          <span>Открытый план использует свой сохранённый снимок данных.</span>
        )}
      </div>
      {onImport && (
        <button className="text-button" onClick={onImport}>
          {dataset ? "Открыть данные" : "Загрузить данные"}
          <Icon name="arrow" />
        </button>
      )}
    </div>
  );
}

export function PlanProgress({
  dataset,
  plan,
  unsaved,
}: {
  dataset: Dataset | null;
  plan: Plan | null;
  unsaved: boolean;
}) {
  const current = plan
    ? plan.status === "confirmed" && !unsaved
      ? 3
      : 2
    : dataset
      ? 1
      : 0;
  return (
    <ol className="plan-progress" aria-label="Шаги подготовки плана">
      {["Данные", "Расчёт", "Проверка и сохранение"].map((label, index) => (
        <li
          key={label}
          className={index < current ? "complete" : undefined}
          aria-current={index === current ? "step" : undefined}
        >
          <span className="step-symbol" aria-hidden="true">
            {index < current ? <Icon name="check" /> : index + 1}
          </span>
          <span>
            {label}
            {index < current && <span className="sr-only"> · готово</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
