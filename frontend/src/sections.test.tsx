import { expect, test, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PlanningSection } from "./PlanningSection";
import { ModelSection } from "./ModelSection";
import { ImportSection, SavedDatasetSelect } from "./ImportSection";
import { ForecastResult } from "./ForecastResult";
import { EvaluationNotice } from "./EvaluationNotice";
import type { Dataset, Plan } from "./types";

const units: Dataset = {
  id: "units-data",
  source: "synthetic",
  bakery_id: "test",
  target: "sold_units",
  unit: "physical_unit",
  schema_version: "history-1",
  provenance: "Synthetic test",
  metadata_declared: true,
};
const plan: Plan = {
  id: "plan-1",
  dataset_id: units.id,
  target_date: "2026-10-12",
  cutoff: "2026-10-11",
  source: "synthetic",
  requested_method: "ML",
  status: "draft",
  revision: 0,
  model_id: "model-1",
  protocol_version: "experimental-test",
  items: [
    {
      product_id: "p1",
      name: "Хлеб",
      batch_size: 12,
      max_daily_qty: 100,
      forecast_mean: 50,
      recommended_qty: 48,
      confirmed_qty: null,
      method: "B1",
      fallback_reason: "Недостаточно данных модели",
      model_id: "model-1",
      warnings: [],
      target_is_open: true,
      automatic_eligible: true,
    },
  ],
};

function planningProps(
  dataset: Dataset,
): Parameters<typeof PlanningSection>[0] {
  return {
    dataset,
    plan,
    busy: false,
    setNotice: vi.fn(),
    target: "2026-10-12",
    setTarget: vi.fn(),
    cutoff: "2026-10-11",
    setCutoff: vi.fn(),
    method: "ML",
    setMethod: vi.fn(),
    targetOpen: true,
    setTargetOpen: vi.fn(),
    quantities: { p1: "48" },
    setQuantities: vi.fn(),
    confirmation: { current: null },
    invalid: false,
    run: async (operation) => operation(),
    show: vi.fn(),
    refreshHistory: async () => {},
  };
}

test("sales-only target or item-line unit hides production even with a previous units plan", () => {
  for (const dataset of [
    {
      ...units,
      target: "item_line_count" as const,
      unit: "item_line" as const,
    },
    { ...units, unit: "item_line" as const },
  ]) {
    const html = renderToStaticMarkup(
      <PlanningSection {...planningProps(dataset)} />,
    );
    expect(html).toContain("не преобразуются в физические штуки");
    expect(html).not.toContain("Рассчитать план");
    expect(html).not.toContain("Подтвердить и сохранить");
    expect(html).not.toContain('type="number"');
  }
});

test("units plan keeps batch constraints and explains fallback without model IDs", () => {
  const html = renderToStaticMarkup(
    <PlanningSection {...planningProps(units)} />,
  );
  expect(html).toContain('value="ML"');
  expect(html).toContain('step="12"');
  expect(html).toContain('max="96"');
  expect(html).not.toContain("model-1");
  expect(html).toContain("Использован упрощённый прогноз");
  expect(html).toContain("Что учесть");
  expect(html).toContain("Подтвердить и сохранить");
});

test("inactive product disables the quantity field and explains the guard", () => {
  const props = planningProps(units);
  props.plan = {
    ...plan,
    items: [
      {
        ...plan.items[0],
        active_to: "2026-10-11",
        automatic_eligible: false,
        forecast_mean: null,
        recommended_qty: 0,
        method: "manual",
      },
    ],
  };
  props.quantities = { p1: "0" };
  const html = renderToStaticMarkup(<PlanningSection {...props} />);
  expect(html).toContain("Выпуск запрещён");
  expect(html).toMatch(/id="quantity-p1"[^>]*disabled=""/);
});

test("legacy inactive recommendation exposes zero repair but stale snapshot stays locked", () => {
  const props = planningProps(units);
  props.plan = {
    ...plan,
    items: [{ ...plan.items[0], active_to: "2026-10-11", automatic_eligible: false }],
  };
  const current = renderToStaticMarkup(<PlanningSection {...props} />);
  expect(current).toMatch(/id="quantity-p1"[^>]*disabled=""[^>]*value="48"/);
  expect(current).toContain("Для подтверждения старого плана установите 0.");
  expect(current).toContain("<button type=\"button\">Установить 0 для");
  const stale = renderToStaticMarkup(
    <PlanningSection {...props} target="2026-10-13" />,
  );
  expect(stale).toContain('data-testid="stale-plan"');
  expect(stale).toContain('<button type="button" disabled="">Установить 0 для');
});

test("legacy metadata visibly disables training until explicit reimport", () => {
  const props = planningProps({ ...units, metadata_declared: false });
  const html = renderToStaticMarkup(
    <ModelSection {...props} dataset={props.dataset!} />,
  );
  expect(html).toContain("повторно импортируйте исходный CSV");
  expect(html).toMatch(/<button disabled="">Подготовить экспериментальный расчёт<\/button>/);
  expect(html).toContain("Проверить прогноз");
  expect(html).not.toContain('type="number"');
});

test("saved dataset control has an exact stable label independent of its options", () => {
  const html = renderToStaticMarkup(
    <SavedDatasetSelect
      datasets={[
        { id: "first-dataset", source: "synthetic" },
        { id: "second-dataset", source: "local" },
      ]}
      value="second-dataset"
      onChange={vi.fn()}
      disabled={false}
    />,
  );
  expect(html).toContain(
    '<label for="saved-dataset">Сохранённый набор</label>',
  );
  expect(html).toContain('<select id="saved-dataset">');
  expect(html).toContain(
    '<option value="second-dataset" selected="">2. Данные пекарни · — строк · дата не указана</option>',
  );
  expect(html).not.toMatch(/<label[^>]*>[^<]*<select/);
});

test("changing planning inputs blocks confirmation of the previous snapshot", () => {
  for (const changed of [
    { target: "2026-10-13" },
    { cutoff: "2026-10-10" },
    { method: "B0" },
    { targetOpen: false },
  ]) {
    const html = renderToStaticMarkup(
      <PlanningSection {...planningProps(units)} {...changed} />,
    );
    expect(html).toContain('data-testid="stale-plan"');
    expect(html).toMatch(
      /<button disabled="">Подтвердить и сохранить<\/button>/,
    );
    expect(html).toContain("прежний план на 2026-10-12");
    expect(html).toContain('value="48"');
  }
});

test("closed-day plan locks manual increases without disabling valid zero confirmation", () => {
  const closedPlan = {
    ...plan,
    items: plan.items.map((item) => ({
      ...item,
      target_is_open: false,
      automatic_eligible: false,
      forecast_mean: null,
      recommended_qty: 0,
      method: "manual",
    })),
  };
  const html = renderToStaticMarkup(
    <PlanningSection
      {...planningProps(units)}
      plan={closedPlan}
      targetOpen={false}
      quantities={{ p1: "0" }}
    />,
  );
  expect(html).toMatch(/type="number" disabled=""/);
  expect(html).toContain("Закрытый день");
  expect(html).toContain("Нет оценки");
  expect(html).toContain("<button>Подтвердить и сохранить</button>");
});

test("saved quantities stay distinguishable from unsaved manual edits", () => {
  const confirmed = {
    ...plan,
    status: "confirmed",
    revision: 1,
    items: plan.items.map((item) => ({ ...item, confirmed_qty: 48 })),
  };
  const html = renderToStaticMarkup(
    <PlanningSection
      {...planningProps(units)}
      plan={confirmed}
      quantities={{ p1: "60" }}
    />,
  );
  expect(html).toContain("Сохранено: 48 шт.");
  expect(html).toContain("Есть несохранённое изменение");
  expect(html).toContain("Несохранённые правки в файл не попадут");
  expect(html).toContain('href="/api/plans/plan-1/export"');
});

test("stale diagnostic snapshot retains its own dates and method with an actionable warning", () => {
  const html = renderToStaticMarkup(
    <ForecastResult
      stale
      unitLabel="шт."
      snapshot={{
        cutoff: "2026-10-10",
        targetDate: "2026-10-12",
        method: "ML",
        result: {
          dataset_id: units.id,
          bakery_id: "test",
          target: "sold_units",
          unit: "physical_unit",
          model_id: "model-1",
          items: [
            {
              product_id: "p1",
              forecast_mean: null,
              b1_mean: null,
              method: "manual",
              fallback_reason: "no_b1_history",
            },
          ],
        },
      }}
    />,
  );
  expect(html).toContain('data-testid="stale-forecast"');
  expect(html).toContain("Пересчитайте прогноз перед использованием");
  expect(html).toContain("Продажи учтены по 2026-10-10");
  expect(html).toContain("Недостаточно истории продаж");
  expect(html).toContain("Нет оценки");
  expect(html).not.toContain("model-1");
  expect(html).not.toContain('type="number"');
});

test("horizon compatibility is explained without claiming forecast accuracy", () => {
  const matched = renderToStaticMarkup(
    <EvaluationNotice
      value={{
        applicable: true,
        reason: null,
        requested_horizon_days: 2,
        evaluated_horizon_days: 2,
      }}
    />,
  );
  expect(matched).toContain("такому же сроку прогноза");
  expect(matched).toContain("не гарантирует точность");
  const mismatch = renderToStaticMarkup(
    <EvaluationNotice
      value={{
        applicable: false,
        reason: "forecast_horizon_mismatch",
        requested_horizon_days: 1,
        evaluated_horizon_days: 2,
      }}
    />,
  );
  expect(mismatch).toContain("относится к другому сроку");
  expect(mismatch).toContain("Проверьте предложенное количество вручную");
  const legacy = renderToStaticMarkup(<EvaluationNotice />);
  expect(legacy).toContain("Качество прогноза для этих данных не проверено");
});

test("method and import selects have exact option-independent labels and retain values and busy locks", () => {
  for (const busy of [false, true]) {
    const props = { ...planningProps(units), busy };
    const planning = renderToStaticMarkup(<PlanningSection {...props} />);
    const model = renderToStaticMarkup(
      <ModelSection {...props} dataset={units} />,
    );
    const imported = renderToStaticMarkup(
      <ImportSection
        {...props}
        csv=""
        setCsv={vi.fn()}
        source="external"
        setSource={vi.fn()}
        bakeryId="test"
        setBakeryId={vi.fn()}
        dataTarget="item_line_count"
        setDataTarget={vi.fn()}
        provenance="Synthetic test"
        setProvenance={vi.fn()}
        setDataset={vi.fn()}
        setPlan={vi.fn()}
        setError={vi.fn()}
      />,
    );
    for (const [html, id, label, selected, options] of [
      [planning, "planning-method", "Способ расчёта", "ML", ["B1", "ML", "B0"]],
      [model, "diagnostic-method", "Способ прогноза", "ML", ["ML", "B1"]],
      [
        imported,
        "import-source",
        "Источник",
        "external",
        ["synthetic", "local", "external"],
      ],
      [
        imported,
        "import-target",
        "Измеряемая величина",
        "item_line_count",
        ["sold_units", "item_line_count"],
      ],
    ] as const) {
      expect(html).toContain(`<label for="${id}">${label}</label>`);
      expect(html).toContain(
        `<select id="${id}"${busy ? ' disabled=""' : ""}>`,
      );
      const select = html.match(
        new RegExp(`<select id="${id}"[^>]*>(.*?)</select>`),
      )![1];
      expect(select).toContain(`<option value="${selected}" selected="">`);
      for (const option of options)
        expect(select).toContain(`<option value="${option}"`);
      // No label can contain select/option text, independent of its selection.
      expect(
        html
          .match(/<label\b[^>]*>[\s\S]*?<\/label>/g)
          ?.some((labelMarkup) => labelMarkup.includes("<select")),
      ).toBe(false);
    }
  }
});
